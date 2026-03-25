"""
Lightweight Socket.IO publisher used by the bot to push live updates to the dashboard.
This module will silently fail if the dashboard isn't running.

Usage:
  from socket_publisher import publish_trade, publish_live
  publish_trade(trade_dict)
  publish_live(payload_dict)
"""

import socketio
import threading
import os
import time

# Default dashboard URL
SERVER_URL = os.environ.get('WARRIOR_DASH_URL', 'http://localhost:5002')

sio = socketio.Client(reconnection=True, logger=False, engineio_logger=False)
_connected = False


def _connect_background():
    global _connected
    try:
        sio.connect(SERVER_URL)
        _connected = True
    except Exception:
        _connected = False


# Start connection in background thread (non-blocking)
threading.Thread(target=_connect_background, daemon=True).start()


def publish_trade(trade: dict):
    """Emit a single trade object to the dashboard (event: new_trade)."""
    try:
        if sio.connected:
            sio.emit('new_trade', trade)
    except Exception:
        pass


def publish_live(payload: dict):
    """Emit a live update (event: live_update). Payload can include balance, equity_curve, recent_trades."""
    try:
        if sio.connected:
            sio.emit('live_update', payload)
    except Exception:
        pass
