import requests


def get_btc_price():
    url = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
    r = requests.get(url)
    return float(r.json()["price"])


def get_klines(symbol="BTCUSDT", interval="1h", limit=100):
    url = "https://api.binance.com/api/v3/klines"
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }

    r = requests.get(url, params=params)
    data = r.json()

    candles = []
    for k in data:
        candles.append({
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4])
        })

    return candles


def get_multi_tf_klines(symbol="BTCUSDT"):
    intervals = ["1h", "4h", "1d", "1w", "1M"]
    data = {}

    for interval in intervals:
        data[interval] = get_klines(symbol=symbol, interval=interval)

    return data