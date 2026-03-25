"""
Main Strategy Module - Ties Everything Together
Implements Ross Cameron's Gap and Go momentum strategy:

Per-trade lifecycle:
  1. Scanner finds a candidate (5 pillars)
  2. Pattern detector finds a setup (Gap&Go, Bull Flag, etc.)
  3. Risk manager validates the trade (R:R, daily loss budget)
  4. Broker executes the entry
  5. Strategy monitors the trade:
     - Hit first target (2:1) → sell 50%, move stop to breakeven
     - Hit stop → exit full position immediately
     - Trade stalls → exit (Ross: "if it doesn't work quickly, get out")
  6. Record results
"""

import logging
import csv
import time
from datetime import datetime, timezone
from typing import Optional
from enum import Enum

from config import (
    MAX_POSITIONS,
    FIRST_PARTIAL_PCT,
    LOG_FILE,
    TRADE_JOURNAL_FILE,
)
from data_feed import DataFeed
from patterns import find_best_setup, calculate_indicators, SetupSignal
from risk_manager import RiskManager, TradeRecord
# Optional real-time publisher (dashboard)
try:
    from socket_publisher import publish_trade, publish_live
except Exception:
    def publish_trade(*a, **k):
        return None
    def publish_live(*a, **k):
        return None
from config import BROKER_TYPE
if BROKER_TYPE == "IBKR":
    from broker_ibkr import Broker
else:
    from broker import Broker
from scanner import StockCandidate
import telegram_notify

logger = logging.getLogger(__name__)


class TradeState(Enum):
    WAITING = "waiting"           # No position
    ENTERED = "entered"           # Full position open
    FIRST_TARGET_HIT = "partial"  # Sold 50%, trailing stop on remainder
    CLOSED = "closed"             # Trade fully closed


class ActiveTrade:
    """Tracks the state of an open trade."""

    def __init__(
        self,
        symbol: str,
        signal: SetupSignal,
        shares: int,
        record: TradeRecord,
    ):
        self.symbol = symbol
        self.signal = signal
        self.total_shares = shares
        self.remaining_shares = shares
        self.record = record
        self.state = TradeState.ENTERED
        self.entry_time = datetime.now(tz=timezone.utc)
        self.trailing_stop = signal.stop_price  # Updated as trade progresses

    @property
    def is_open(self) -> bool:
        return self.state in (TradeState.ENTERED, TradeState.FIRST_TARGET_HIT)


class Strategy:
    """
    Orchestrates the full Ross Cameron momentum strategy.
    Call run_premarket() before open, then run_market_session() during trading hours.
    """

    def __init__(self):
        self.data = DataFeed()
        self.broker = Broker()
        self.risk = RiskManager()
        self.active_trade: Optional[ActiveTrade] = None

    # ─────────────────────────────────────────────
    # PRE-MARKET ROUTINE
    # ─────────────────────────────────────────────

    def run_premarket(self, scanner) -> list[StockCandidate]:
        """
        Run at 7:00 AM ET. Build the watchlist.
        Returns the watchlist for display.
        """
        logger.info("=== PRE-MARKET ROUTINE START ===")
        self.risk.new_day()
        watchlist = scanner.run_premarket_scan()
        scanner.print_watchlist()
        return watchlist

    # ─────────────────────────────────────────────
    # TRADE ENTRY
    # ─────────────────────────────────────────────

    def try_enter_trade(self, candidate: StockCandidate) -> bool:
        """
        Attempt to enter a trade on a candidate.
        Steps:
          1. Get latest 1-minute bars
          2. Find best setup
          3. Validate with risk manager
          4. Execute entry
        Returns True if a position was opened.
        """
        if self.active_trade and self.active_trade.is_open:
            logger.debug(f"Already in a trade ({self.active_trade.symbol}). Skipping {candidate.symbol}.")
            return False

        if not self.risk.is_trading_allowed:
            logger.warning("Daily max loss hit. No new trades.")
            return False

        symbol = candidate.symbol
        logger.info(f"Evaluating setup for {symbol}...")

        # Get intraday bars
        bars = self.data.get_bars(symbol, timeframe="1Min", limit=60)
        if bars.empty:
            logger.warning(f"No bar data for {symbol}.")
            return False

        # Find best setup
        signal = find_best_setup(
            bars_1min=bars,
            prev_close=candidate.prev_close,
            premarket_high=candidate.premarket_high,
        )
        if not signal:
            logger.info(f"No qualifying setup found for {symbol}.")
            return False

        logger.info(f"Setup found: {signal}")

        # Validate risk
        valid, reason = self.risk.validate_trade(
            entry_price=signal.entry_price,
            stop_price=signal.stop_price,
            target_price=signal.target_price,
        )
        if not valid:
            logger.warning(f"Trade rejected: {reason}")
            return False

        # Calculate shares
        shares = self.risk.calculate_shares(signal.entry_price, signal.stop_price)
        if shares <= 0:
            logger.warning("Position size is 0. Skipping.")
            return False

        logger.info(f"Entering trade: {symbol} | {signal} | {shares} shares")

        # Execute order (limit order, 5 cents above entry to ensure fill)
        order_id = self.broker.buy_limit(symbol, shares, signal.entry_price + 0.05)
        if not order_id:
            logger.error(f"Order execution failed for {symbol}.")
            return False

        # Wait briefly for fill (in live trading, use websocket events instead)
        time.sleep(2)
        position = self.broker.get_position(symbol)
        if not position or position["qty"] == 0:
            logger.warning(f"Order may not have filled for {symbol}. Check manually.")
            self.broker.cancel_all_orders()
            return False

        # Record the trade
        actual_shares = position["qty"]
        record = self.risk.record_trade_open(
            symbol=symbol,
            entry_price=signal.entry_price,
            stop_price=signal.stop_price,
            target_price=signal.target_price,
            shares=actual_shares,
        )

        self.active_trade = ActiveTrade(
            symbol=symbol,
            signal=signal,
            shares=actual_shares,
            record=record,
        )

        logger.info(
            f"✓ ENTERED: {symbol} | {actual_shares} shares @ ${signal.entry_price:.2f} | "
            f"Stop=${signal.stop_price:.2f} Target=${signal.target_price:.2f}"
        )

        self._log_trade_to_journal(
            symbol=symbol,
            action="ENTRY",
            price=signal.entry_price,
            shares=actual_shares,
            setup=signal.setup_type.value,
            pnl=0,
        )

        telegram_notify.notify_entry(
            symbol=symbol,
            setup=signal.setup_type.value,
            shares=actual_shares,
            entry_price=signal.entry_price,
            stop_price=signal.stop_price,
            target_price=signal.target_price,
        )

        return True

    # ─────────────────────────────────────────────
    # TRADE MONITORING
    # ─────────────────────────────────────────────

    def monitor_active_trade(self):
        """
        Called every 30-60 seconds during market hours.
        Checks price vs stop/target and manages exits.
        """
        if not self.active_trade or not self.active_trade.is_open:
            return

        trade = self.active_trade
        symbol = trade.symbol
        snap = self.data.get_snapshot(symbol)
        if not snap:
            return

        current_price = snap["price"]
        if not current_price:
            return

        signal = trade.signal
        logger.debug(
            f"Monitoring {symbol}: Price=${current_price:.2f} | "
            f"Stop=${trade.trailing_stop:.2f} | "
            f"Target=${signal.target_price:.2f} | "
            f"Shares={trade.remaining_shares}"
        )

        # ── STOP LOSS HIT ──
        if current_price <= trade.trailing_stop:
            logger.warning(
                f"🛑 STOP HIT: {symbol} at ${current_price:.2f} "
                f"(stop was ${trade.trailing_stop:.2f})"
            )
            self._exit_trade(trade, current_price, trade.remaining_shares, reason="STOP")
            return

        # ── FIRST TARGET HIT ──
        if (
            trade.state == TradeState.ENTERED
            and current_price >= signal.target_price
        ):
            first_exit_shares = int(trade.remaining_shares * FIRST_PARTIAL_PCT)
            logger.info(
                f"✓ FIRST TARGET HIT: {symbol} at ${current_price:.2f}. "
                f"Selling {first_exit_shares} shares."
            )
            order_id = self.broker.sell_limit(symbol, first_exit_shares, current_price)
            if order_id:
                pnl = (current_price - signal.entry_price) * first_exit_shares
                self.risk.record_trade_close(trade.record, current_price, first_exit_shares)
                trade.remaining_shares -= first_exit_shares
                trade.state = TradeState.FIRST_TARGET_HIT

                # Move stop to breakeven on the runner
                trade.trailing_stop = signal.entry_price
                logger.info(
                    f"Stop moved to breakeven: ${signal.entry_price:.2f}. "
                    f"Holding {trade.remaining_shares} runner shares."
                )
                self._log_trade_to_journal(
                    symbol=symbol,
                    action="PARTIAL EXIT",
                    price=current_price,
                    shares=first_exit_shares,
                    setup=signal.setup_type.value,
                    pnl=pnl,
                )
                telegram_notify.notify_partial_exit(
                    symbol=symbol,
                    shares_sold=first_exit_shares,
                    price=current_price,
                    pnl=pnl,
                    remaining_shares=trade.remaining_shares,
                )

        # ── TRAIL STOP ON RUNNER ──
        elif trade.state == TradeState.FIRST_TARGET_HIT:
            # Trail the stop: move it up if the stock has moved higher
            bars = self.data.get_bars(symbol, timeframe="1Min", limit=10)
            if not bars.empty:
                # Trail stop to the low of the last 3 candles
                recent_low = bars["low"].tail(3).min()
                new_stop = recent_low - 0.05
                if new_stop > trade.trailing_stop:
                    trade.trailing_stop = new_stop
                    logger.debug(f"Trail stop updated to ${trade.trailing_stop:.2f} for {symbol}")

        # ── STALLING CHECK ──
        # Ross exits if the trade doesn't work quickly.
        # If we're 5+ minutes in and still below entry, exit.
        minutes_in_trade = (
            datetime.now(tz=timezone.utc) - trade.entry_time
        ).total_seconds() / 60

        if (
            trade.state == TradeState.ENTERED
            and minutes_in_trade > 5
            and current_price < signal.entry_price
        ):
            logger.warning(
                f"⚠ TRADE STALLING: {symbol} not working after {minutes_in_trade:.0f} min. "
                f"Exiting per Ross's rule."
            )
            self._exit_trade(trade, current_price, trade.remaining_shares, reason="STALL")

        # Publish a live update for dashboard (best-effort)
        try:
            snap_small = {
                'symbol': symbol,
                'price': current_price,
                'stop': trade.trailing_stop,
                'shares': trade.remaining_shares,
            }
            publish_live({'snapshot': snap_small})
        except Exception:
            pass

    # ─────────────────────────────────────────────
    # TRADE EXIT
    # ─────────────────────────────────────────────

    def _exit_trade(self, trade: ActiveTrade, price: float, shares: int, reason: str):
        """Execute a full exit of a trade."""
        symbol = trade.symbol
        order_id = self.broker.sell_market(symbol, shares)
        if order_id:
            pnl = (price - trade.signal.entry_price) * shares
            self.risk.record_trade_close(trade.record, price, shares)
            trade.remaining_shares = 0
            trade.state = TradeState.CLOSED
            logger.info(
                f"EXIT ({reason}): {symbol} | {shares} shares @ ${price:.2f} | "
                f"P&L: ${pnl:+.2f}"
            )
            self._log_trade_to_journal(
                symbol=symbol,
                action=f"EXIT ({reason})",
                price=price,
                shares=shares,
                setup=trade.signal.setup_type.value,
                pnl=pnl,
            )
            telegram_notify.notify_exit(
                symbol=symbol,
                reason=reason,
                shares=shares,
                entry_price=trade.signal.entry_price,
                exit_price=price,
                pnl=pnl,
            )
            self.risk.print_status()
            self.active_trade = None

    def close_all_end_of_day(self):
        """
        Called at market close (or 11:30 AM if we stop early).
        Ross NEVER holds overnight.
        """
        if self.active_trade and self.active_trade.is_open:
            logger.info("End of day: force-closing all positions.")
            self._exit_trade(
                self.active_trade,
                price=self.data.get_snapshot(self.active_trade.symbol)["price"],
                shares=self.active_trade.remaining_shares,
                reason="EOD",
            )
        self.broker.close_all_positions()
        self.broker.cancel_all_orders()

    # ─────────────────────────────────────────────
    # TRADE JOURNAL
    # ─────────────────────────────────────────────

    def _log_trade_to_journal(
        self,
        symbol: str,
        action: str,
        price: float,
        shares: int,
        setup: str,
        pnl: float,
    ):
        """Append trade to CSV journal for post-market review."""
        row = {
            "datetime": datetime.now().isoformat(),
            "symbol": symbol,
            "action": action,
            "price": price,
            "shares": shares,
            "setup": setup,
            "pnl": round(pnl, 2),
            "day_pnl": round(self.risk.day_pnl, 2),
        }
        write_header = False
        try:
            with open(TRADE_JOURNAL_FILE, "r") as f:
                pass
        except FileNotFoundError:
            write_header = True

        with open(TRADE_JOURNAL_FILE, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=row.keys())
            if write_header:
                writer.writeheader()
            writer.writerow(row)
        # Publish trade to dashboard (best-effort)
        try:
            publish_trade(row)
        except Exception:
            pass
