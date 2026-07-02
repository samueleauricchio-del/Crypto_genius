import os
import requests
from flask import Flask, jsonify, request
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

watchlist = {}


def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[WARN] Telegram not configured. Would have sent: {text}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Telegram send failed: {e}")


def check_prices():
    if not watchlist:
        return

    for symbol, rules in watchlist.items():
        try:
            resp = requests.get(
                "https://data-api.binance.vision/api/v3/ticker/price",
                params={"symbol": symbol},
                timeout=10
            )
            resp.raise_for_status()
            current = float(resp.json()["price"])
        except (requests.exceptions.RequestException, KeyError, ValueError) as e:
            print(f"[ERROR] Binance fetch failed for {symbol}: {e}")
            continue

        if rules.get("above") and current >= rules["above"] and "above" not in rules["triggered"]:
            send_telegram_message(f"🚀 {symbol} ha superato ${rules['above']}: ora è a ${current}")
            rules["triggered"].add("above")

        if rules.get("below") and current <= rules["below"] and "below" not in rules["triggered"]:
            send_telegram_message(f"📉 {symbol} è sceso sotto ${rules['below']}: ora è a ${current}")
            rules["triggered"].add("below")


scheduler = BackgroundScheduler()
scheduler.add_job(check_prices, "interval", minutes=5)
scheduler.start()


@app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "service": "Crypto Price Watchdog",
        "watchlist": {k: {"above": v.get("above"), "below": v.get("below")} for k, v in watchlist.items()},
        "endpoints": {
            "POST /watch": "body: {symbol, above?, below?} — set/update a price alert",
            "DELETE /watch/<symbol>": "remove an alert",
            "GET /check-now": "force an immediate price check (bypasses the 5-min schedule)",
            "GET /prices": "current prices, optional ?symbols=BTCUSDT,ETHUSDT"
        }
    })


@app.route("/watch", methods=["POST"])
def add_watch():
    """
    Body: {"symbol": "BTCUSDT", "above": 70000, "below": 50000}
    'symbol' must be a Binance ticker symbol (e.g. BTCUSDT, ETHUSDT, SOLUSDT).
    """
    data = request.get_json(force=True)
    symbol = (data.get("symbol") or "").strip().upper()
    above = data.get("above")
    below = data.get("below")

    if not symbol or (above is None and below is None):
        return jsonify({"error": "need symbol (e.g. BTCUSDT) and at least one of 'above'/'below'"}), 400

    watchlist[symbol] = {"above": above, "below": below, "triggered": set()}
    return jsonify({"status": "watching", "symbol": symbol, "above": above, "below": below})


@app.route("/watch/<symbol>", methods=["DELETE"])
def remove_watch(symbol):
    watchlist.pop(symbol.upper(), None)
    return jsonify({"status": "removed", "symbol": symbol.upper()})


@app.route("/check-now")
def check_now():
    check_prices()
    return jsonify({"status": "checked"})


@app.route("/prices")
def prices():
    """
    Current prices for major coins via Binance's public market-data-only API
    (data-api.binance.vision) — no API key required, built for this exact use case.
    Query param 'symbols' overrides the default list (Binance ticker symbols, e.g. BTCUSDT).
    """
    symbols_param = request.args.get("symbols", "BTCUSDT,ETHUSDT,SOLUSDT")
    symbols = [s.strip().upper() for s in symbols_param.split(",")]

    results = {}
    errors = {}
    for symbol in symbols:
        try:
            resp = requests.get(
                "https://data-api.binance.vision/api/v3/ticker/price",
                params={"symbol": symbol},
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            results[symbol] = {"usd": float(data["price"])}
        except requests.exceptions.RequestException as e:
            errors[symbol] = str(e)

    return jsonify({"prices": results, "errors": errors or None})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
