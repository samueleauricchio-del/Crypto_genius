import os
import math
import requests
from flask import Flask, jsonify, request
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

watchlist = {}

COINGECKO_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"


def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[WARN] Telegram not configured. Would have sent: {text}")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Telegram send failed: {e}")


def fetch_coin_price_usd(symbol):
    symbol = symbol.upper().replace("USDT", "")

    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": 250,
        "page": 1,
        "sparkline": "false",
        "price_change_percentage": "1h,24h,7d,30d",
    }

    for page in range(1, 5):
        params["page"] = page
        resp = requests.get(COINGECKO_MARKETS_URL, params=params, timeout=15)
        resp.raise_for_status()

        for coin in resp.json():
            if coin.get("symbol", "").upper() == symbol:
                return float(coin["current_price"])

    raise ValueError(f"Coin not found in CoinGecko top 1000: {symbol}")


def check_prices():
    if not watchlist:
        return

    for symbol, rules in watchlist.items():
        try:
            current = fetch_coin_price_usd(symbol)
        except (requests.exceptions.RequestException, KeyError, ValueError) as e:
            print(f"[ERROR] Price fetch failed for {symbol}: {e}")
            continue

        if rules.get("above") and current >= rules["above"] and "above" not in rules["triggered"]:
            send_telegram_message(f"🚀 {symbol} ha superato ${rules['above']}: ora è a ${current}")
            rules["triggered"].add("above")

        if rules.get("below") and current <= rules["below"] and "below" not in rules["triggered"]:
            send_telegram_message(f"📉 {symbol} è sceso sotto ${rules['below']}: ora è a ${current}")
            rules["triggered"].add("below")


def get_coingecko_market_data(symbols=None, top_n=1000):
    top_n = max(1, min(int(top_n), 1000))
    per_page = 250
    pages = math.ceil(top_n / per_page)

    wanted = None
    if symbols:
        wanted = {s.strip().upper().replace("USDT", "") for s in symbols if s.strip()}

    results = {}

    for page in range(1, pages + 1):
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": per_page,
            "page": page,
            "sparkline": "false",
            "price_change_percentage": "1h,24h,7d,30d",
        }

        resp = requests.get(COINGECKO_MARKETS_URL, params=params, timeout=15)
        resp.raise_for_status()
        coins = resp.json()

        for coin in coins:
            symbol = coin.get("symbol", "").upper()

            if wanted and symbol not in wanted:
                continue

            market_cap = coin.get("market_cap")
            volume_24h = coin.get("total_volume")

            volume_market_cap_ratio = None
            if market_cap and volume_24h:
                volume_market_cap_ratio = volume_24h / market_cap

            results[symbol] = {
                "id": coin.get("id"),
                "name": coin.get("name"),
                "symbol": symbol,
                "image": coin.get("image"),

                "price_usd": coin.get("current_price"),
                "high_24h": coin.get("high_24h"),
                "low_24h": coin.get("low_24h"),

                "change_1h_pct": coin.get("price_change_percentage_1h_in_currency"),
                "change_24h_pct": coin.get("price_change_percentage_24h"),
                "change_7d_pct": coin.get("price_change_percentage_7d_in_currency"),
                "change_30d_pct": coin.get("price_change_percentage_30d_in_currency"),

                "price_change_24h": coin.get("price_change_24h"),

                "market_cap": market_cap,
                "market_cap_rank": coin.get("market_cap_rank"),
                "market_cap_change_24h": coin.get("market_cap_change_24h"),
                "market_cap_change_24h_pct": coin.get("market_cap_change_percentage_24h"),

                "fully_diluted_valuation": coin.get("fully_diluted_valuation"),
                "volume_24h": volume_24h,
                "volume_market_cap_ratio": volume_market_cap_ratio,

                "circulating_supply": coin.get("circulating_supply"),
                "total_supply": coin.get("total_supply"),
                "max_supply": coin.get("max_supply"),

                "ath": coin.get("ath"),
                "ath_change_pct": coin.get("ath_change_percentage"),
                "ath_date": coin.get("ath_date"),

                "atl": coin.get("atl"),
                "atl_change_pct": coin.get("atl_change_percentage"),
                "atl_date": coin.get("atl_date"),

                "last_updated": coin.get("last_updated"),
            }

    return results


scheduler = BackgroundScheduler()
scheduler.add_job(check_prices, "interval", minutes=5)
scheduler.start()


@app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "service": "Crypto Price Watchdog",
        "source": "CoinGecko",
        "watchlist": {
            k: {
                "above": v.get("above"),
                "below": v.get("below")
            }
            for k, v in watchlist.items()
        },
        "endpoints": {
            "POST /watch": "body: {symbol, above?, below?}",
            "DELETE /watch/<symbol>": "remove an alert",
            "GET /check-now": "force immediate alert check",
            "GET /prices": "top crypto market data, default top=1000",
            "GET /prices?top=500": "top 500 crypto",
            "GET /prices?symbols=BTC,ETH,SOL": "specific crypto data"
        }
    })


@app.route("/watch", methods=["POST"])
def add_watch():
    data = request.get_json(force=True)

    symbol = (data.get("symbol") or "").strip().upper().replace("USDT", "")
    above = data.get("above")
    below = data.get("below")

    if not symbol or (above is None and below is None):
        return jsonify({
            "error": "need symbol, example BTC, and at least one of above/below"
        }), 400

    try:
        above = float(above) if above is not None else None
        below = float(below) if below is not None else None
    except ValueError:
        return jsonify({"error": "above and below must be numbers"}), 400

    watchlist[symbol] = {
        "above": above,
        "below": below,
        "triggered": set()
    }

    return jsonify({
        "status": "watching",
        "symbol": symbol,
        "above": above,
        "below": below
    })


@app.route("/watch/<symbol>", methods=["DELETE"])
def remove_watch(symbol):
    clean_symbol = symbol.upper().replace("USDT", "")
    watchlist.pop(clean_symbol, None)

    return jsonify({
        "status": "removed",
        "symbol": clean_symbol
    })


@app.route("/check-now")
def check_now():
    check_prices()
    return jsonify({"status": "checked"})


@app.route("/prices")
def prices():
    explicit = request.args.get("symbols")
    top_n = request.args.get("top", 1000)

    symbols = None
    if explicit:
        symbols = [s.strip().upper() for s in explicit.split(",") if s.strip()]

    try:
        results = get_coingecko_market_data(symbols=symbols, top_n=top_n)
    except (requests.exceptions.RequestException, ValueError) as e:
        return jsonify({"error": str(e)}), 502

    return jsonify({
        "count": len(results),
        "source": "coingecko",
        "prices": results
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
