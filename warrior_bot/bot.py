"""
Warrior Trading Bot - Main Runner
Based on Ross Cameron's Gap and Go momentum strategy.

Schedule:
  07:00 ET  → Pre-market scan starts
  09:30 ET  → Market open — look for entry signals
  09:45 ET  → Primary trading window begins (after the initial 9:30 chaos)
  11:30 ET  → Stop taking new trades
  15:55 ET  → Close all positions (5 min before close)

Usage:
  python bot.py

Make sure to set your API keys in config.py first!
"""

import logging
import time
import schedule
from datetime import datetime, timezone, timedelta

from config import (
    ALPACA_API_KEY,
    PRE_MARKET_SCAN_START,
    MARKET_OPEN,
    TRADING_END,
    MARKET_CLOSE,
    LOG_FILE,
    MAX_DAILY_LOSS,
    MAX_RISK_PER_TRADE,
)
from data_feed import DataFeed
from scanner import Scanner
from strategy import Strategy

# ─────────────────────────────────────────────
# LOGGING SETUP
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
    ],
)
logger = logging.getLogger("warrior_bot")

# Suppress noisy library logs
logging.getLogger("alpaca").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("yfinance").setLevel(logging.WARNING)


def check_api_keys():
    """Warn if API keys are still the placeholder values."""
    if "YOUR_PAPER_API_KEY" in ALPACA_API_KEY:
        print("\n" + "=" * 60)
        print("  ⚠  API KEYS NOT SET")
        print("=" * 60)
        print("  1. Go to https://app.alpaca.markets/paper/dashboard/overview")
        print("  2. Create a free account")
        print("  3. Copy your Paper Trading API Key & Secret Key")
        print("  4. Paste them in config.py")
        print("=" * 60 + "\n")
        return False
    return True


class WarriorBot:
    """
    The main bot orchestrator.
    Implements Ross Cameron's pre-market and intraday routines.
    """

    def __init__(self):
        self.data = DataFeed()
        self.scanner = Scanner(self.data)
        self.strategy = Strategy()
        self._watchlist_built = False
        self._trading_active = False

    def start(self):
        """Start the bot with scheduled jobs."""
        logger.info("=" * 60)
        logger.info("  WARRIOR TRADING BOT — STARTING")
        logger.info(f"  Strategy: Ross Cameron Gap & Go Momentum")
        logger.info(f"  Max daily loss: ${MAX_DAILY_LOSS}")
        logger.info(f"  Max risk/trade: ${MAX_RISK_PER_TRADE}")
        logger.info(f"  Mode: PAPER TRADING (no real money)")
        logger.info("=" * 60)

        # Print account info
        try:
            self.strategy.broker.print_account()
        except Exception as e:
            logger.error(f"Could not connect to broker. Check your API keys/TWS. Error: {e}")
            return

        # ─── SCHEDULE (times in IST = UTC+2) ───
        # Pre-market scan at 7:00 AM ET = 14:00 IST
        schedule.every().day.at("14:00").do(self._job_premarket_scan)    # 7:00 AM ET

        # Market open — start evaluating setups — 9:30 AM ET = 16:30 IST
        schedule.every().day.at("16:30").do(self._job_market_open)       # 9:30 AM ET

        # Check for trades every 30 seconds during market hours
        schedule.every(30).seconds.do(self._job_monitor)

        # Stop new trades at 11:30 AM ET = 18:30 IST
        schedule.every().day.at("18:30").do(self._job_stop_new_trades)   # 11:30 AM ET

        # Close all positions 5 min before market close — 3:55 PM ET = 22:55 IST
        schedule.every().day.at("22:55").do(self._job_eod_close)         # 3:55 PM ET

        logger.info("Bot scheduled. Waiting for 14:00 IST (7:00 AM ET)...")
        logger.info("(Press Ctrl+C to stop)\n")

        try:
            while True:
                schedule.run_pending()
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Bot stopped by user.")
            self._job_eod_close()

    # ─────────────────────────────────────────────
    # SCHEDULED JOBS
    # ─────────────────────────────────────────────

    def _job_premarket_scan(self):
        """7:00 AM ET — Build the watchlist."""
        logger.info("━" * 50)
        logger.info("  7:00 AM — PRE-MARKET SCAN")
        logger.info("━" * 50)
        try:
            watchlist = self.strategy.run_premarket(self.scanner)
            self._watchlist_built = True
            if not watchlist:
                logger.warning("No candidates found. Will re-scan in 30 min.")
                # Schedule a re-scan at 8:00 AM ET
                schedule.every(30).minutes.do(self._rescan_premarket).tag("rescan")
        except Exception as e:
            logger.error(f"Pre-market scan failed: {e}", exc_info=True)

    def _rescan_premarket(self):
        """Re-scan if first scan found nothing."""
        if self._watchlist_built and self.scanner.watchlist:
            schedule.clear("rescan")
            return schedule.CancelJob
        logger.info("Re-scanning pre-market...")
        watchlist = self.scanner.run_premarket_scan()
        if watchlist:
            self._watchlist_built = True
            schedule.clear("rescan")
            return schedule.CancelJob

    def _job_market_open(self):
        """9:30 AM ET — Market opens. Start looking for entries."""
        logger.info("━" * 50)
        logger.info("  9:30 AM — MARKET OPEN")
        logger.info("━" * 50)

        if not self.scanner.watchlist:
            logger.warning("Watchlist is empty. Running emergency pre-market scan...")
            self.scanner.run_premarket_scan()

        self._trading_active = True
        logger.info(
            f"Active watchlist: {[c.symbol for c in self.scanner.watchlist]}"
        )

    def _job_monitor(self):
        """
        Every 30 seconds:
        - Monitor active trade (stop/target/stall checks)
        - If no active trade, look for new entries on watchlist
        """
        if not self._trading_active:
            return

        if not self.strategy.risk.is_trading_allowed:
            return

        # Monitor existing trade
        self.strategy.monitor_active_trade()

        # If no active trade, look for a new setup
        if not (self.strategy.active_trade and self.strategy.active_trade.is_open):
            self._look_for_entry()

    def _look_for_entry(self):
        """
        Try to enter a trade from the watchlist.
        Ross focuses on the #1 candidate first.
        """
        for candidate in self.scanner.watchlist:
            entered = self.strategy.try_enter_trade(candidate)
            if entered:
                break  # Only one trade at a time

    def _job_stop_new_trades(self):
        """11:30 AM ET — Stop looking for new setups."""
        logger.info("━" * 50)
        logger.info("  11:30 AM — STOPPING NEW TRADES")
        logger.info("  (Only managing existing position)")
        logger.info("━" * 50)
        self._trading_active = False

    def _job_eod_close(self):
        """3:55 PM ET — Force close everything."""
        logger.info("━" * 50)
        logger.info("  END OF DAY — CLOSING ALL POSITIONS")
        logger.info("━" * 50)
        self._trading_active = False
        try:
            self.strategy.close_all_end_of_day()
        except Exception as e:
            logger.error(f"EOD close failed: {e}")
        finally:
            self.strategy.risk.print_status()

    # ─────────────────────────────────────────────
    # MANUAL CONTROLS (for testing)
    # ─────────────────────────────────────────────

    def run_scan_now(self):
        """Manual: run a scan right now and print results."""
        print("\nRunning manual scan...")
        watchlist = self.scanner.run_premarket_scan()
        self.scanner.print_watchlist()
        return watchlist

    def status(self):
        """Print current bot status."""
        print(f"\n  Trading active: {self._trading_active}")
        print(f"  Watchlist: {[c.symbol for c in self.scanner.watchlist]}")
        if self.strategy.active_trade:
            t = self.strategy.active_trade
            print(f"  Active trade: {t.symbol} | State={t.state.value} | Shares={t.remaining_shares}")
        else:
            print("  No active trade.")
        self.strategy.broker.print_account()
        self.strategy.risk.print_status()


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    if not check_api_keys():
        exit(1)

    bot = WarriorBot()
    bot.start()
