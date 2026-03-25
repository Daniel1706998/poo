"""
Data Feed Module - Fetches market data from Alpaca
Handles: price bars, snapshots, pre-market data, float info
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
import pandas as pd
import yfinance as yf
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import (
    StockBarsRequest,
    StockSnapshotRequest,
    StockLatestQuoteRequest,
)
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from config import ALPACA_API_KEY, ALPACA_SECRET_KEY

logger = logging.getLogger(__name__)

# Eastern Time zone
ET = timezone(timedelta(hours=-5))  # EST (switches to -4 during EDT)


class DataFeed:
    """Fetches and caches market data from Alpaca and Yahoo Finance."""

    def __init__(self):
        self.client = StockHistoricalDataClient(
            api_key=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET_KEY,
        )
        self._float_cache: dict[str, Optional[int]] = {}

    # ─────────────────────────────────────────────
    # PRICE BARS
    # ─────────────────────────────────────────────

    def get_bars(
        self,
        symbol: str,
        timeframe: str = "1Min",
        limit: int = 100,
        start: Optional[datetime] = None,
    ) -> pd.DataFrame:
        """
        Get OHLCV bars for a symbol.
        timeframe: '1Min', '5Min', '15Min', '1Day'
        Returns DataFrame with columns: open, high, low, close, volume
        """
        tf_map = {
            "1Min": TimeFrame(1, TimeFrameUnit.Minute),
            "5Min": TimeFrame(5, TimeFrameUnit.Minute),
            "15Min": TimeFrame(15, TimeFrameUnit.Minute),
            "1Day": TimeFrame(1, TimeFrameUnit.Day),
        }
        tf = tf_map.get(timeframe, TimeFrame(1, TimeFrameUnit.Minute))

        if start is None:
            start = datetime.now(tz=timezone.utc) - timedelta(hours=6)

        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=start,
            limit=limit,
            feed="iex",  # IEX feed (free tier)
        )
        try:
            bars = self.client.get_stock_bars(request)
            df = bars.df
            if df.empty:
                return pd.DataFrame()
            # Flatten multi-index if multiple symbols
            if isinstance(df.index, pd.MultiIndex):
                df = df.xs(symbol, level="symbol")
            df.index = pd.to_datetime(df.index)
            return df[["open", "high", "low", "close", "volume"]]
        except Exception as e:
            logger.warning(f"Failed to get bars for {symbol}: {e}")
            return pd.DataFrame()

    def get_daily_bars(self, symbol: str, days: int = 30) -> pd.DataFrame:
        """Get daily bars for the past N days (used for RVOL calculation)."""
        start = datetime.now(tz=timezone.utc) - timedelta(days=days + 5)
        return self.get_bars(symbol, timeframe="1Day", start=start)

    # ─────────────────────────────────────────────
    # SNAPSHOTS (Current state of a stock)
    # ─────────────────────────────────────────────

    def get_snapshot(self, symbol: str) -> Optional[dict]:
        """
        Get a real-time snapshot: current price, today's change %, volume, VWAP.
        Returns dict with keys: price, change_pct, volume, vwap, prev_close
        """
        try:
            request = StockSnapshotRequest(symbol_or_symbols=[symbol], feed="iex")
            snapshots = self.client.get_stock_snapshot(request)
            snap = snapshots.get(symbol)
            if snap is None:
                return None

            prev_close = snap.previous_daily_bar.close if snap.previous_daily_bar else None
            current_price = snap.latest_trade.price if snap.latest_trade else None
            today_volume = snap.daily_bar.volume if snap.daily_bar else 0
            today_vwap = snap.daily_bar.vwap if snap.daily_bar else None

            change_pct = None
            if prev_close and current_price:
                change_pct = ((current_price - prev_close) / prev_close) * 100

            return {
                "symbol": symbol,
                "price": current_price,
                "prev_close": prev_close,
                "change_pct": change_pct,
                "volume": today_volume,
                "vwap": today_vwap,
            }
        except Exception as e:
            logger.warning(f"Failed to get snapshot for {symbol}: {e}")
            return None

    def get_snapshots_bulk(self, symbols: list[str]) -> dict[str, dict]:
        """Get snapshots for multiple symbols at once (more efficient)."""
        if not symbols:
            return {}
        try:
            request = StockSnapshotRequest(symbol_or_symbols=symbols, feed="iex")
            snapshots = self.client.get_stock_snapshot(request)
            result = {}
            for sym, snap in snapshots.items():
                prev_close = snap.previous_daily_bar.close if snap.previous_daily_bar else None
                current_price = snap.latest_trade.price if snap.latest_trade else None
                today_volume = snap.daily_bar.volume if snap.daily_bar else 0
                today_vwap = snap.daily_bar.vwap if snap.daily_bar else None

                change_pct = None
                if prev_close and current_price:
                    change_pct = ((current_price - prev_close) / prev_close) * 100

                result[sym] = {
                    "symbol": sym,
                    "price": current_price,
                    "prev_close": prev_close,
                    "change_pct": change_pct,
                    "volume": today_volume,
                    "vwap": today_vwap,
                }
            return result
        except Exception as e:
            logger.warning(f"Bulk snapshot failed: {e}")
            return {}

    # ─────────────────────────────────────────────
    # RELATIVE VOLUME (RVOL)
    # ─────────────────────────────────────────────

    def get_relative_volume(self, symbol: str, current_volume: int) -> float:
        """
        Calculate RVOL = today's volume / 30-day average daily volume.
        Ross requires RVOL >= 5x.
        """
        daily_bars = self.get_daily_bars(symbol, days=30)
        if daily_bars.empty or len(daily_bars) < 5:
            return 0.0
        avg_volume = daily_bars["volume"].tail(30).mean()
        if avg_volume == 0:
            return 0.0
        return current_volume / avg_volume

    # ─────────────────────────────────────────────
    # FLOAT (shares available for trading)
    # ─────────────────────────────────────────────

    def get_float(self, symbol: str) -> Optional[int]:
        """
        Get float shares from Yahoo Finance.
        Uses cache to avoid repeated API calls.
        """
        if symbol in self._float_cache:
            return self._float_cache[symbol]

        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info
            float_shares = info.get("floatShares") or info.get("sharesOutstanding")
            self._float_cache[symbol] = float_shares
            return float_shares
        except Exception as e:
            logger.warning(f"Could not get float for {symbol}: {e}")
            self._float_cache[symbol] = None
            return None

    # ─────────────────────────────────────────────
    # PRE-MARKET DATA
    # ─────────────────────────────────────────────

    def get_premarket_change(self, symbol: str) -> Optional[float]:
        """
        Get pre-market % change vs prior close.
        Uses latest quote vs previous daily close.
        """
        try:
            request = StockLatestQuoteRequest(symbol_or_symbols=[symbol], feed="iex")
            quotes = self.client.get_stock_latest_quote(request)
            quote = quotes.get(symbol)
            if not quote:
                return None

            daily_bars = self.get_daily_bars(symbol, days=2)
            if daily_bars.empty:
                return None

            prev_close = daily_bars["close"].iloc[-1]
            ask_price = quote.ask_price
            if prev_close and ask_price:
                return ((ask_price - prev_close) / prev_close) * 100
        except Exception as e:
            logger.warning(f"Pre-market data failed for {symbol}: {e}")
        return None
