import os
import requests
from flask import Flask, jsonify, request
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# In-memory watchlist: { "bitcoin": {"above": 65000, "below": 55000, "triggered": set()} }
# NOTE: in-memory = resets on redeploy/restart. Fine for a test project; swap for a
# real DB (e.g. Railway Postgres) if this needs to survive restarts.
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

    ids = ",".join(watchlist.keys())
    try:
        resp = requests.get(
            f"{COINGECKO_BASE}/simple/price",
            params={"ids": ids, "vs_currencies": "usd"},
            timeout=10
        )
        resp.raise_for_status()
        prices = resp.json()
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] CoinGecko fetch failed: {e}")
        return

    for coin_id, rules in watchlist.items():
        if coin_id not in prices:
            continue
        current = prices[coin_id]["usd"]

        if rules.get("above") and current >= rules["above"] and "above" not in rules["triggered"]:
            send_telegram_message(f"🚀 {coin_id.upper()} ha superato ${rules['above']}: ora è a ${current}")
            rules["triggered"].add("above")

        if rules.get("below") and current <= rules["below"] and "below" not in rules["triggered"]:
            send_telegram_message(f"📉 {coin_id.upper()} è sceso sotto ${rules['below']}: ora è a ${current}")
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
            "POST /watch": "body: {coin_id, above?, below?} — set/update a price alert",
            "DELETE /watch/<coin_id>": "remove an alert",
            "GET /check-now": "force an immediate price check (bypasses the 5-min schedule)"
        }
    })


@app.route("/watch", methods=["POST"])
def add_watch():
    data = request.get_json(force=True)
    coin_id = data.get("coin_id")
    above = data.get("above")
    below = data.get("below")

    if not coin_id or (above is None and below is None):
        return jsonify({"error": "need coin_id and at least one of 'above'/'below'"}), 400

    watchlist[coin_id] = {"above": above, "below": below, "triggered": set()}
    return jsonify({"status": "watching", "coin_id": coin_id, "above": above, "below": below})


@app.route("/watch/<coin_id>", methods=["DELETE"])
def remove_watch(coin_id):
    watchlist.pop(coin_id, None)
    return jsonify({"status": "removed", "coin_id": coin_id})


@app.route("/check-now")
def check_now():
    check_prices()
    return jsonify({"status": "checked"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
