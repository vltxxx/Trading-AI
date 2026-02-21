from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from backend.market_data import get_btc_price, get_klines
from backend.strategy.fvg import FVGZone, FVGSide, detect_fvg, price_in_zone
from backend.strategy.signals import Signal, TradePlan, build_signal_text

TOKEN = "8575998274:AAFdw9TXZFr-3NPc5zLLL95gFGWsgW46muA"

SYMBOL = "BTCUSDT"
INTERVAL = "1h"
TF_LABEL = "1H"

# --- Состояние (в памяти) ---
WATCHING_CHATS: set[int] = set()          # какие чаты подписаны на авто-сигналы
LAST_IN_ZONE: dict[str, bool] = {}        # был ли "в зоне" последний раз (по символу)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Бот работает 🚀\n"
        "Команды:\n"
        "/price — цена BTC\n"
        "/scan — разовый анализ FVG\n"
        "/watch — включить авто-сигналы (вход в FVG)\n"
        "/unwatch — выключить авто-сигналы\n"
        "/status — статус авто-сигналов"
    )


async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        p = get_btc_price()
        await update.message.reply_text(f"{SYMBOL}: {p}")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")


async def test_signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fvg = FVGZone(
        side=FVGSide.BULLISH,
        tf="1H",
        created_ts=0,
        low=1.1000,
        high=1.1050,
        mid=1.1025
    )

    sig = Signal(
        symbol="EURUSD",
        fvg=fvg,
        direction="LONG",
        liquidity=None,
        reason="Тест сигнал FVG",
        plan=TradePlan(entry=1.1025, sl=1.0980, tp=1.1100)
    )

    await update.message.reply_text(build_signal_text(sig))


def build_trade_plan_from_zone(zone: FVGZone, direction: str) -> TradePlan:
    """
    Простой план:
    entry = mid
    SL = край зоны (для LONG нижний, для SHORT верхний)
    TP = 2R по размеру зоны
    """
    rng = zone.high - zone.low
    entry = zone.mid

    if direction == "LONG":
        sl = zone.low
        tp = zone.high + rng * 2
    else:
        sl = zone.high
        tp = zone.low - rng * 2

    return TradePlan(entry=round(entry, 2), sl=round(sl, 2), tp=round(tp, 2))


async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Разовый скан (как сейчас), чтобы видеть, что бот видит.
    """
    try:
        candles = get_klines(symbol=SYMBOL, interval=INTERVAL, limit=180)
        fvg_list = detect_fvg(candles, tf=TF_LABEL)

        if not fvg_list:
            await update.message.reply_text(f"{SYMBOL} {TF_LABEL}: FVG не найдено")
            return

        last_fvg = fvg_list[-1]
        price_now = candles[-1]["close"]
        direction = "SHORT" if last_fvg.side == FVGSide.BEARISH else "LONG"
        in_zone = price_in_zone(price_now, last_fvg)

        reason = (
            f"Цена {'вошла' if in_zone else 'ещё не вошла'} в FVG. "
            f"Текущая цена: {round(price_now, 2)}"
        )

        plan = build_trade_plan_from_zone(last_fvg, direction)

        sig = Signal(
            symbol=SYMBOL,
            fvg=last_fvg,
            direction=direction,
            liquidity=None,
            reason=reason,
            plan=plan,
        )

        await update.message.reply_text(build_signal_text(sig))
    except Exception as e:
        await update.message.reply_text(f"Ошибка scan: {e}")


async def watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Включает авто-сигналы в этом чате.
    """
    chat_id = update.effective_chat.id
    WATCHING_CHATS.add(chat_id)

    # сбросим состояние, чтобы первое "вход в зону" отработало корректно
    LAST_IN_ZONE[SYMBOL] = False

    await update.message.reply_text(
        "✅ Авто-сигналы включены.\n"
        "Я буду присылать сигнал ТОЛЬКО когда цена ВОЙДЁТ в FVG (BTCUSDT 1H)."
    )


async def unwatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in WATCHING_CHATS:
        WATCHING_CHATS.remove(chat_id)
    await update.message.reply_text("⛔ Авто-сигналы выключены.")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    on = chat_id in WATCHING_CHATS
    await update.message.reply_text(
        f"Статус авто-сигналов: {'✅ ВКЛ' if on else '⛔ ВЫКЛ'}\n"
        f"Рынок: {SYMBOL}, TF: {TF_LABEL}\n"
        f"Проверка каждые 60 сек."
    )


async def auto_check(context: ContextTypes.DEFAULT_TYPE):
    """
    Фоновая проверка: если цена перешла из "не в зоне" -> "в зоне",
    то шлём сигнал во все подписанные чаты.
    """
    if not WATCHING_CHATS:
        return

    try:
        candles = get_klines(symbol=SYMBOL, interval=INTERVAL, limit=180)
        fvg_list = detect_fvg(candles, tf=TF_LABEL)
        if not fvg_list:
            return

        last_fvg = fvg_list[-1]
        price_now = candles[-1]["close"]
        in_zone_now = price_in_zone(price_now, last_fvg)

        in_zone_prev = LAST_IN_ZONE.get(SYMBOL, False)
        LAST_IN_ZONE[SYMBOL] = in_zone_now

        # Сигнал только на событие "входа" (false -> true)
        if (not in_zone_prev) and in_zone_now:
            direction = "SHORT" if last_fvg.side == FVGSide.BEARISH else "LONG"
            reason = f"Цена вошла в FVG ✅ Текущая цена: {round(price_now, 2)}"
            plan = build_trade_plan_from_zone(last_fvg, direction)

            sig = Signal(
                symbol=SYMBOL,
                fvg=last_fvg,
                direction=direction,
                liquidity=None,
                reason=reason,
                plan=plan,
            )

            text = build_signal_text(sig)

            for chat_id in list(WATCHING_CHATS):
                await context.bot.send_message(chat_id=chat_id, text=text)

    except Exception:
        # чтобы бот не падал от временных проблем сети/API
        return


def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("price", price))
    app.add_handler(CommandHandler("test_signal", test_signal))
    app.add_handler(CommandHandler("scan", scan))
    app.add_handler(CommandHandler("watch", watch))
    app.add_handler(CommandHandler("unwatch", unwatch))
    app.add_handler(CommandHandler("status", status))

    # Автопроверка раз в 60 секунд
    app.job_queue.run_repeating(auto_check, interval=60, first=5)

    print("Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()