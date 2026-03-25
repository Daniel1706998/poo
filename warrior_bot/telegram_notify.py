"""
Telegram Notifications for Warrior Bot
Sends alerts on trade entries, exits, and daily summaries.
"""

import logging
import urllib.request
import urllib.parse
import json

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

BOT_NAME = "Warrior Bot (Stocks)"


def _send_message(text: str):
    """Send a Telegram message. Non-blocking — failures are logged, never raised."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as e:
        logger.debug(f"Telegram send failed: {e}")


def notify_entry(symbol: str, setup: str, shares: int, entry_price: float,
                 stop_price: float, target_price: float):
    """Send notification when entering a trade."""
    risk = abs(entry_price - stop_price)
    rr = abs(target_price - entry_price) / risk if risk > 0 else 0
    total_risk = risk * shares

    text = (
        f"<b>{BOT_NAME}</b>\n"
        f"ENTRY  {symbol}\n"
        f"\n"
        f"Setup: {setup}\n"
        f"Shares: {shares}\n"
        f"Entry: ${entry_price:.2f}\n"
        f"Stop: ${stop_price:.2f}\n"
        f"Target: ${target_price:.2f}\n"
        f"R:R: {rr:.1f}x\n"
        f"Risk: ${total_risk:.2f}"
    )
    _send_message(text)


def notify_exit(symbol: str, reason: str, shares: int, entry_price: float,
                exit_price: float, pnl: float):
    """Send notification when exiting a trade."""
    pnl_emoji = "+" if pnl >= 0 else ""

    text = (
        f"<b>{BOT_NAME}</b>\n"
        f"EXIT ({reason})  {symbol}\n"
        f"\n"
        f"Shares: {shares}\n"
        f"Entry: ${entry_price:.2f}\n"
        f"Exit: ${exit_price:.2f}\n"
        f"P&L: {pnl_emoji}${pnl:.2f}"
    )
    _send_message(text)


def notify_partial_exit(symbol: str, shares_sold: int, price: float,
                        pnl: float, remaining_shares: int):
    """Send notification on partial exit (first target hit)."""
    text = (
        f"<b>{BOT_NAME}</b>\n"
        f"PARTIAL EXIT  {symbol}\n"
        f"\n"
        f"Sold: {shares_sold} shares @ ${price:.2f}\n"
        f"P&L: +${pnl:.2f}\n"
        f"Remaining: {remaining_shares} shares\n"
        f"Stop moved to breakeven"
    )
    _send_message(text)


def notify_circuit_breaker(day_pnl: float, max_loss: float):
    """Send notification when daily max loss circuit breaker triggers."""
    text = (
        f"<b>{BOT_NAME}</b>\n"
        f"CIRCUIT BREAKER\n"
        f"\n"
        f"Daily P&L: -${abs(day_pnl):.2f}\n"
        f"Limit: ${max_loss:.2f}\n"
        f"Trading stopped for today."
    )
    _send_message(text)
