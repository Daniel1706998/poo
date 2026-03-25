"""
Warrior Bot Dashboard — Flask Server
Run from warrior_bot/ directory:
    python3 dashboard/app.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO
from datetime import datetime, date, timezone, timedelta
import csv

from config import BROKER_TYPE
if BROKER_TYPE == "IBKR":
    from broker_ibkr import Broker
else:
    from broker import Broker
from data_feed import DataFeed
from config import TRADE_JOURNAL_FILE, MAX_DAILY_LOSS

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

IST = timezone(timedelta(hours=2))

# Lazy-init
_broker = None
_data = None


def broker() -> Broker:
    global _broker
    if _broker is None:
        _broker = Broker()
    return _broker


def data_feed() -> DataFeed:
    global _data
    if _data is None:
        _data = DataFeed()
    return _data


def read_journal() -> list:
    rows = []
    try:
        with open(TRADE_JOURNAL_FILE, newline="") as f:
            for row in csv.DictReader(f):
                rows.append(row)
    except FileNotFoundError:
        pass
    return rows


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/live")
def live():
    try:
        balance = broker().get_account()
    except Exception:
        balance = {"equity": 0, "cash": 0, "buying_power": 0}

    positions = []
    try:
        for pos in broker().get_all_positions():
            try:
                ticker = data_feed().get_snapshot(pos["symbol"]) or {}
                current = ticker.get("price") or pos.get("current_price")
            except Exception:
                current = pos.get("current_price")

            qty = pos.get("qty", 0)
            avg = pos.get("avg_entry_price") or pos.get("avg_price") or 0

            pnl = (current - avg) * qty if current and avg else 0

            positions.append({
                **pos,
                "current_price": round(current or 0, 4),
                "live_pnl": round(pnl, 2),
            })
    except Exception:
        pass

    # Day stats from journal
    journal = read_journal()
    today_str = date.today().isoformat()
    today = [t for t in journal if t.get("datetime", "").startswith(today_str)]
    exits = [t for t in today if t.get("action", "").startswith("EXIT") or t.get("action")=="PARTIAL EXIT"]
    partials = [t for t in today if t.get("action", "") == "PARTIAL EXIT"]
    wins = [t for t in exits if float(t.get("pnl", 0)) > 0]
    losses = [t for t in exits if float(t.get("pnl", 0)) <= 0]
    day_pnl = float(today[-1].get("day_pnl", 0)) if today else 0

    return jsonify({
        "balance": balance,
        "positions": positions,
        "day_stats": {
            "pnl": round(day_pnl, 2),
            "closed": len(exits),
            "partials": len(partials),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(exits) * 100, 1) if exits else 0,
            "max_loss": MAX_DAILY_LOSS,
            "remaining": round(MAX_DAILY_LOSS + day_pnl, 2),
        },
        "timestamp": datetime.now(tz=IST).strftime("%H:%M:%S IST"),
    })


@app.route("/api/trades")
def trades():
    return jsonify(read_journal())


@app.route("/api/equity")
def equity():
    journal = read_journal()
    curve = []
    running = 0.0
    for t in journal:
        if t.get("action", "").startswith("EXIT") or t.get("action") == "PARTIAL EXIT":
            pnl = float(t.get("pnl", 0))
            running += pnl
            curve.append({
                "datetime": t.get("datetime", "")[:16],
                "symbol": t.get("symbol"),
                "action": t.get("action"),
                "trade_pnl": round(pnl, 2),
                "pnl": round(running, 2),
            })
    return jsonify(curve)


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("  WARRIOR BOT DASHBOARD")
    print("  http://localhost:5002")
    print("=" * 50 + "\n")
    # Use socketio.run so WebSocket works
    socketio.run(app, host="0.0.0.0", port=5002, debug=False)
