# telegram_bot/bot.py
# Вариант 2+: КНОПКИ снизу (без текста) + FVG/Price/Watch + авто-обновление цены
# python-telegram-bot >= 20
# Для JobQueue (авто-сигналы и авто-обновление цены):
#   pip install "python-telegram-bot[job-queue]"

import os
from dataclasses import dataclass
from typing import Dict, Set, Tuple, List, Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# --- ТВОИ МОДУЛИ ---
from backend.market_data import get_klines
from backend.strategy.fvg import FVGSide, detect_fvg, price_in_zone
from backend.strategy.signals import Signal, TradePlan, build_signal_text
from backend.config.symbols import SYMBOLS

# =======================
# НАСТРОЙКИ
# =======================
TOKEN ="8575998274:AAFdw9TXZFr-3NPc5zLLL95gFGWsgW46muA"

TF_INTERVALS: List[Tuple[str, str]] = [
    ("1h", "1H"),
    ("4h", "4H"),
    ("1d", "1D"),
    ("1w", "1W"),
    ("1M", "1M"),
]
TF_LABEL_TO_INTERVAL = {label.upper(): interval for interval, label in TF_INTERVALS}

AUTO_TF_LABEL = "1H"
AUTO_INTERVAL = TF_LABEL_TO_INTERVAL[AUTO_TF_LABEL]
AUTO_CHECK_EVERY_SEC = 60

PRICE_AUTO_EVERY_SEC = 10  # авто-обновление цены

MAX_MSG = 3900  # лимит телеги ~4096

# =======================
# ПАМЯТЬ (RAM)
# =======================
WATCHING: Dict[int, Set[str]] = {}  # chat_id -> set(symbols)
LAST_IN_ZONE: Dict[Tuple[int, str], bool] = {}  # (chat_id, symbol) -> bool

USER_STATE: Dict[int, dict] = {}  # user_id -> dict состояния

# авто-обновление цены: (chat_id, message_id) -> job
PRICE_JOBS: Dict[Tuple[int, int], object] = {}

# =======================
# УТИЛИТЫ
# =======================

def chunk_text(text: str, limit: int = MAX_MSG) -> List[str]:
    if len(text) <= limit:
        return [text]
    parts = []
    buf = []
    size = 0
    for block in text.split("\n\n"):
        add = block + "\n\n"
        if size + len(add) > limit and buf:
            parts.append("".join(buf).rstrip())
            buf = [add]
            size = len(add)
        else:
            buf.append(add)
            size += len(add)
    if buf:
        parts.append("".join(buf).rstrip())
    return parts


def get_last_price(symbol: str) -> float:
    candles = get_klines(symbol=symbol, interval="1h", limit=2)
    return float(candles[-1]["close"])


def build_trade_plan_from_zone(zone, direction: str) -> TradePlan:
    rng = zone.high - zone.low
    entry = zone.mid
    if direction == "LONG":
        sl = zone.low
        tp = zone.high + rng * 2
    else:
        sl = zone.high
        tp = zone.low - rng * 2
    return TradePlan(entry=round(entry, 2), sl=round(sl, 2), tp=round(tp, 2))


def make_signal(symbol: str, interval: str, tf_label: str) -> Optional[str]:
    candles = get_klines(symbol=symbol, interval=interval, limit=180)
    fvg_list = detect_fvg(candles, tf=tf_label)
    if not fvg_list:
        return None

    last_fvg = fvg_list[-1]
    price_now = float(candles[-1]["close"])
    direction = "SHORT" if last_fvg.side == FVGSide.BEARISH else "LONG"
    in_zone = price_in_zone(price_now, last_fvg)

    reason = (
        f"TF: {tf_label}\n"
        f"Цена {'вошла ✅' if in_zone else 'ещё не вошла ⏳'} в FVG.\n"
        f"Текущая цена: {round(price_now, 2)}"
    )
    plan = build_trade_plan_from_zone(last_fvg, direction)

    sig = Signal(
        symbol=symbol,
        fvg=last_fvg,
        direction=direction,
        liquidity=None,
        reason=reason,
        plan=plan,
    )
    return build_signal_text(sig)


# =======================
# НИЖНИЕ КНОПКИ (ReplyKeyboard)
# =======================

BTN_PRICE = "📈 Цена"
BTN_SCAN = "🔍 FVG"
BTN_WATCH = "🔔 Авто-сигналы"
BTN_SUBS = "⭐ Подписки"
BTN_CLOSE = "❌ Закрыть"

def bottom_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [BTN_PRICE, BTN_SCAN],
            [BTN_WATCH, BTN_SUBS],
            [BTN_CLOSE],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


# =======================
# INLINE UI (внутренние экраны)
# =======================

def main_menu_kb() -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton("📈 Цена", callback_data="main:price"),
         InlineKeyboardButton("🔍 FVG", callback_data="main:scan")],
        [InlineKeyboardButton("🔔 Авто-сигналы", callback_data="main:watch")],
        [InlineKeyboardButton("⭐ Подписки", callback_data="main:mysubs"),
         InlineKeyboardButton("❌ Закрыть", callback_data="main:close")],
    ]
    return InlineKeyboardMarkup(kb)


def pairs_kb(mode: str, selected: Optional[Set[str]] = None) -> InlineKeyboardMarkup:
    selected = selected or set()
    rows = []
    for i in range(0, len(SYMBOLS), 2):
        row = []
        for sym in SYMBOLS[i:i+2]:
            if mode == "watch":
                prefix = "✅ " if sym in selected else "➕ "
                row.append(InlineKeyboardButton(prefix + sym, callback_data=f"watch:toggle:{sym}"))
            else:
                row.append(InlineKeyboardButton(sym, callback_data=f"{mode}:pair:{sym}"))
        rows.append(row)

    if mode == "watch":
        rows.append([
            InlineKeyboardButton("✅ Сохранить", callback_data="watch:apply"),
            InlineKeyboardButton("🧹 Убрать ВСЕ", callback_data="watch:clear"),
        ])
        rows.append([
            InlineKeyboardButton("⭐ Подписки", callback_data="main:mysubs"),
            InlineKeyboardButton("⬅️ Назад", callback_data="back:main"),
        ])
    else:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="back:main")])

    return InlineKeyboardMarkup(rows)


def tf_kb() -> InlineKeyboardMarkup:
    row1 = [
        InlineKeyboardButton("1H", callback_data="scan:tf:1H"),
        InlineKeyboardButton("4H", callback_data="scan:tf:4H"),
        InlineKeyboardButton("1D", callback_data="scan:tf:1D"),
    ]
    row2 = [
        InlineKeyboardButton("1W", callback_data="scan:tf:1W"),
        InlineKeyboardButton("1M", callback_data="scan:tf:1M"),
        InlineKeyboardButton("Все TF", callback_data="scan:tf:ALL"),
    ]
    row3 = [
        InlineKeyboardButton("⬅️ Пары", callback_data="back:scan_pairs"),
        InlineKeyboardButton("🏠 Меню", callback_data="back:main"),
    ]
    return InlineKeyboardMarkup([row1, row2, row3])


def price_view_kb(symbol: str, is_auto: bool) -> InlineKeyboardMarkup:
    auto_txt = "⏱ Авто: ВКЛ" if is_auto else "⏱ Авто: ВЫКЛ"
    auto_cb = f"price:auto:{symbol}:{'OFF' if is_auto else 'ON'}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить", callback_data=f"price:refresh:{symbol}"),
         InlineKeyboardButton(auto_txt, callback_data=auto_cb)],
        [InlineKeyboardButton("⬅️ Пары", callback_data="main:price"),
         InlineKeyboardButton("🏠 Меню", callback_data="back:main")],
    ])


# =======================
# START / ТЕКСТОВЫЕ КНОПКИ СНИЗУ
# =======================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 Trading AI Bot запущен.\n\n"
        "Пользуйся кнопками снизу 👇",
        reply_markup=bottom_kb(),
    )
    await update.message.reply_text("Меню:", reply_markup=main_menu_kb())


async def on_bottom_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()

    if txt == BTN_PRICE:
        await update.message.reply_text("Выбери пару для цены:", reply_markup=pairs_kb("price"))
        return

    if txt == BTN_SCAN:
        await update.message.reply_text("Выбери пару для FVG:", reply_markup=pairs_kb("scan"))
        return

    if txt == BTN_WATCH:
        chat_id = update.message.chat.id
        user_id = update.message.from_user.id
        state = USER_STATE.setdefault(user_id, {})
        state.clear()
        state["mode"] = "watch"
        state["selected"] = set(WATCHING.get(chat_id, set()))
        await update.message.reply_text(
            "Выбери пары для авто-сигналов (тапай по кнопкам):",
            reply_markup=pairs_kb("watch", selected=state["selected"]),
        )
        return

    if txt == BTN_SUBS:
        chat_id = update.message.chat.id
        subs = WATCHING.get(chat_id, set())
        if not subs:
            out = "⭐ Подписок нет.\nНажми «🔔 Авто-сигналы» и выбери пары."
        else:
            out = "⭐ Твои подписки:\n" + "\n".join(sorted(subs)) + f"\n\nTF: {AUTO_TF_LABEL}\nПроверка: {AUTO_CHECK_EVERY_SEC} сек."
        await update.message.reply_text(out, reply_markup=bottom_kb())
        return

    if txt == BTN_CLOSE:
        await update.message.reply_text("Ок ✅", reply_markup=ReplyKeyboardRemove())
        return

    # если написал что-то ещё
    await update.message.reply_text("Пользуйся кнопками снизу 👇", reply_markup=bottom_kb())


# =======================
# CALLBACKS (Inline кнопки)
# =======================

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user_id = q.from_user.id
    data = q.data or ""
    state = USER_STATE.setdefault(user_id, {})

    # MAIN
    if data == "main:close":
        await q.edit_message_text("Ок ✅", reply_markup=None)
        return

    if data == "back:main":
        state.clear()
        await q.edit_message_text("Меню:", reply_markup=main_menu_kb())
        return

    if data == "main:price":
        state.clear()
        state["mode"] = "price"
        await q.edit_message_text("Выбери пару для цены:", reply_markup=pairs_kb("price"))
        return

    if data == "main:scan":
        state.clear()
        state["mode"] = "scan"
        await q.edit_message_text("Выбери пару для FVG:", reply_markup=pairs_kb("scan"))
        return

    if data == "main:watch":
        state.clear()
        state["mode"] = "watch"
        chat_id = q.message.chat.id
        state["selected"] = set(WATCHING.get(chat_id, set()))
        await q.edit_message_text(
            "Выбери пары для авто-сигналов (тапай по кнопкам):",
            reply_markup=pairs_kb("watch", selected=state["selected"]),
        )
        return

    if data == "main:mysubs":
        chat_id = q.message.chat.id
        subs = WATCHING.get(chat_id, set())
        if not subs:
            text = "⭐ Подписок нет.\n\nОткрой «Авто-сигналы» и выбери пары."
        else:
            text = "⭐ Твои подписки:\n" + "\n".join(sorted(subs)) + f"\n\nTF авто-сигналов: {AUTO_TF_LABEL}\nПроверка: {AUTO_CHECK_EVERY_SEC} сек."
        await q.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔔 Авто-сигналы", callback_data="main:watch")],
                [InlineKeyboardButton("🏠 Меню", callback_data="back:main")],
            ])
        )
        return

    # PRICE
    if data.startswith("price:pair:"):
        sym = data.split(":")[-1]
        state["symbol"] = sym
        try:
            p = get_last_price(sym)
            chat_id = q.message.chat.id
            msg_id = q.message.message_id
            is_auto = (chat_id, msg_id) in PRICE_JOBS
            await q.edit_message_text(
                f"📈 Цена {sym}: {round(p, 4)}",
                reply_markup=price_view_kb(sym, is_auto),
            )
        except Exception as e:
            await q.edit_message_text(
                f"Ошибка цены для {sym}: {e}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="main:price")]])
            )
        return

    if data.startswith("price:refresh:"):
        sym = data.split(":")[-1]
        try:
            p = get_last_price(sym)
            chat_id = q.message.chat.id
            msg_id = q.message.message_id
            is_auto = (chat_id, msg_id) in PRICE_JOBS
            await q.edit_message_text(
                f"📈 Цена {sym}: {round(p, 4)}",
                reply_markup=price_view_kb(sym, is_auto),
            )
        except Exception as e:
            await q.edit_message_text(
                f"Ошибка цены для {sym}: {e}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="main:price")]])
            )
        return

    if data.startswith("price:auto:"):
        # price:auto:BTCUSDT:ON|OFF
        _, _, sym, flag = data.split(":")
        chat_id = q.message.chat.id
        msg_id = q.message.message_id

        if context.application.job_queue is None:
            await q.edit_message_text(
                "❌ Авто-обновление недоступно: JobQueue не включён.\n\n"
                "Установи:\n"
                "pip install \"python-telegram-bot[job-queue]\"\n"
                "и перезапусти бот.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="back:main")]])
            )
            return

        key = (chat_id, msg_id)

        # выключение
        if flag.upper() == "OFF":
            job = PRICE_JOBS.pop(key, None)
            if job:
                job.schedule_removal()
            p = get_last_price(sym)
            await q.edit_message_text(
                f"📈 Цена {sym}: {round(p, 4)}",
                reply_markup=price_view_kb(sym, is_auto=False),
            )
            return

        # включение
        if flag.upper() == "ON":
            # если уже включено — просто обновим клаву
            if key in PRICE_JOBS:
                p = get_last_price(sym)
                await q.edit_message_text(
                    f"📈 Цена {sym}: {round(p, 4)}",
                    reply_markup=price_view_kb(sym, is_auto=True),
                )
                return

            # создаём job, который будет редактировать это же сообщение
            job = context.application.job_queue.run_repeating(
                price_auto_job,
                interval=PRICE_AUTO_EVERY_SEC,
                first=0,
                data={"chat_id": chat_id, "message_id": msg_id, "symbol": sym},
                name=f"price_auto:{chat_id}:{msg_id}",
            )
            PRICE_JOBS[key] = job

            p = get_last_price(sym)
            await q.edit_message_text(
                f"📈 Цена {sym}: {round(p, 4)}",
                reply_markup=price_view_kb(sym, is_auto=True),
            )
            return

    # SCAN
    if data == "back:scan_pairs":
        await q.edit_message_text("Выбери пару для FVG:", reply_markup=pairs_kb("scan"))
        return

    if data.startswith("scan:pair:"):
        sym = data.split(":")[-1]
        state["symbol"] = sym
        await q.edit_message_text(
            f"🔍 FVG: выбери таймфрейм для {sym}:",
            reply_markup=tf_kb()
        )
        return

    if data.startswith("scan:tf:"):
        tf = data.split(":")[-1].upper()
        sym = state.get("symbol")
        if not sym:
            await q.edit_message_text("Сначала выбери пару.", reply_markup=pairs_kb("scan"))
            return

        try:
            if tf == "ALL":
                blocks = []
                for interval, label in TF_INTERVALS:
                    txt = make_signal(sym, interval, label)
                    if txt:
                        blocks.append(txt)

                if not blocks:
                    await q.edit_message_text(f"{sym}: FVG не найдено на заданных TF.", reply_markup=tf_kb())
                    return

                out = "\n\n".join(blocks)
                parts = chunk_text(out)
                await q.edit_message_text(parts[0], reply_markup=tf_kb())
                for part in parts[1:]:
                    await context.bot.send_message(chat_id=q.message.chat.id, text=part)
                return

            interval = TF_LABEL_TO_INTERVAL.get(tf)
            if not interval:
                await q.edit_message_text("Не понимаю TF. Выбери из кнопок.", reply_markup=tf_kb())
                return

            txt = make_signal(sym, interval, tf)
            if not txt:
                await q.edit_message_text(f"{sym}: FVG не найдено на {tf}.", reply_markup=tf_kb())
                return

            parts = chunk_text(txt)
            await q.edit_message_text(parts[0], reply_markup=tf_kb())
            for part in parts[1:]:
                await context.bot.send_message(chat_id=q.message.chat.id, text=part)
            return

        except Exception as e:
            await q.edit_message_text(f"Ошибка scan: {e}", reply_markup=tf_kb())
            return

    # WATCH
    if data.startswith("watch:toggle:"):
        sym = data.split(":")[-1]
        selected: Set[str] = state.setdefault("selected", set())
        if sym in selected:
            selected.remove(sym)
        else:
            selected.add(sym)

        await q.edit_message_text(
            "Выбери пары для авто-сигналов (тапай по кнопкам):",
            reply_markup=pairs_kb("watch", selected=selected),
        )
        return

    if data == "watch:clear":
        state["selected"] = set()
        await q.edit_message_text(
            "Выбери пары для авто-сигналов (тапай по кнопкам):",
            reply_markup=pairs_kb("watch", selected=state["selected"]),
        )
        return

    if data == "watch:apply":
        chat_id = q.message.chat.id

        if context.application.job_queue is None:
            await q.edit_message_text(
                "❌ Авто-сигналы недоступны: JobQueue не включён.\n\n"
                "Установи:\n"
                "pip install \"python-telegram-bot[job-queue]\"\n"
                "и перезапусти бот.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="back:main")]])
            )
            return

        selected = set(state.get("selected", set()))
        if not selected:
            WATCHING.pop(chat_id, None)
            await q.edit_message_text(
                "⭐ Подписки очищены.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔔 Авто-сигналы", callback_data="main:watch")],
                    [InlineKeyboardButton("🏠 Меню", callback_data="back:main")],
                ])
            )
            return

        WATCHING[chat_id] = selected
        for sym in selected:
            LAST_IN_ZONE[(chat_id, sym)] = False

        await q.edit_message_text(
            "✅ Авто-сигналы включены для:\n" + "\n".join(sorted(selected)) +
            f"\n\nTF: {AUTO_TF_LABEL}\nПроверка каждые {AUTO_CHECK_EVERY_SEC} сек.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⭐ Подписки", callback_data="main:mysubs")],
                [InlineKeyboardButton("🏠 Меню", callback_data="back:main")],
            ])
        )
        return

    # fallback
    await q.edit_message_text("Не понимаю действие. Нажми кнопки снизу 👇", reply_markup=main_menu_kb())


# =======================
# JOBS
# =======================

async def auto_check(context: ContextTypes.DEFAULT_TYPE):
    if context.application.job_queue is None:
        return
    if not WATCHING:
        return

    for chat_id, symbols in list(WATCHING.items()):
        if not symbols:
            continue

        for sym in list(symbols):
            try:
                candles = get_klines(symbol=sym, interval=AUTO_INTERVAL, limit=180)
                fvg_list = detect_fvg(candles, tf=AUTO_TF_LABEL)
                if not fvg_list:
                    continue

                last_fvg = fvg_list[-1]
                price_now = float(candles[-1]["close"])
                in_zone_now = price_in_zone(price_now, last_fvg)

                key = (chat_id, sym)
                in_zone_prev = LAST_IN_ZONE.get(key, False)
                LAST_IN_ZONE[key] = in_zone_now

                if (not in_zone_prev) and in_zone_now:
                    direction = "SHORT" if last_fvg.side == FVGSide.BEARISH else "LONG"
                    plan = build_trade_plan_from_zone(last_fvg, direction)

                    sig = Signal(
                        symbol=sym,
                        fvg=last_fvg,
                        direction=direction,
                        liquidity=None,
                        reason=f"{sym} вошёл в FVG ✅\nTF: {AUTO_TF_LABEL}\nЦена: {round(price_now, 2)}",
                        plan=plan,
                    )
                    text = build_signal_text(sig)
                    for part in chunk_text(text):
                        await context.bot.send_message(chat_id=chat_id, text=part)

            except Exception:
                continue


async def price_auto_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data or {}
    chat_id = data.get("chat_id")
    message_id = data.get("message_id")
    symbol = data.get("symbol")

    if not chat_id or not message_id or not symbol:
        return

    try:
        p = get_last_price(symbol)
        # редактируем то же сообщение
        is_auto = True
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"📈 Цена {symbol}: {round(p, 4)}",
            reply_markup=price_view_kb(symbol, is_auto),
        )
    except Exception:
        # если сообщение не редактируется (удалили/старое) — выключаем job
        key = (chat_id, message_id)
        job = PRICE_JOBS.pop(key, None)
        if job:
            job.schedule_removal()


# =======================
# MAIN
# =======================

def main():
    if not TOKEN:
        raise RuntimeError("TOKEN пустой. Вставь TOKEN = '...' или задай BOT_TOKEN.")

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_callback))

    # Нажатия нижних кнопок — это обычный текст, ловим тут
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_bottom_button))

    # авто-проверка FVG (подписки)
    if app.job_queue is not None:
        app.job_queue.run_repeating(auto_check, interval=AUTO_CHECK_EVERY_SEC, first=5)

    print("Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()