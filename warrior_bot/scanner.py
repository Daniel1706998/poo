"""
Scanner Module - Finds stocks meeting Ross Cameron's 5 Pillars criteria.

Pre-market: scans for top gappers (price, volume, float, change %)
Intraday:   HOD (High of Day) momentum scanner - alerts on new highs with volume
"""

import logging
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetAssetsRequest
from alpaca.trading.enums import AssetClass, AssetStatus

from config import (
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    MIN_RELATIVE_VOLUME,
    MIN_PRICE_CHANGE_PCT,
    MIN_PRICE,
    MAX_PRICE,
    MAX_FLOAT,
    IDEAL_FLOAT,
)
from data_feed import DataFeed

logger = logging.getLogger(__name__)


@dataclass
class StockCandidate:
    """A stock that has passed some or all of the 5 Pillars filters."""
    symbol: str
    price: float
    change_pct: float
    volume: int
    rvol: float
    float_shares: Optional[int]
    prev_close: float
    vwap: Optional[float]
    pillars_passed: int = 0
    pillar_notes: list[str] = field(default_factory=list)
    premarket_high: Optional[float] = None

    @property
    def float_millions(self) -> Optional[float]:
        if self.float_shares:
            return self.float_shares / 1_000_000
        return None

    def __str__(self):
        float_str = f"{self.float_millions:.1f}M" if self.float_millions else "N/A"
        return (
            f"{self.symbol}: ${self.price:.2f} "
            f"({self.change_pct:+.1f}%) "
            f"Vol={self.volume:,} RVOL={self.rvol:.1f}x "
            f"Float={float_str} "
            f"Pillars={self.pillars_passed}/5"
        )


class Scanner:
    """
    Implements Ross Cameron's scanner logic:
    - Pre-market: Top gappers scan (5 pillars filter)
    - Intraday:   HOD momentum scan (real-time new highs)
    """

    # A curated universe of liquid small/mid-cap stocks to scan.
    # In production, you'd pull this from a full market list.
    # Alpaca's Assets endpoint can supply this.
    SCAN_UNIVERSE_SIZE = 500  # Top N stocks by volume to scan

    def __init__(self, data_feed: DataFeed):
        self.data = data_feed
        self.trading_client = TradingClient(
            api_key=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET_KEY,
            paper=True,
        )
        self._watchlist: list[StockCandidate] = []
        self._hod_tracker: dict[str, float] = {}  # symbol -> previous high of day

    # ─────────────────────────────────────────────
    # UNIVERSE BUILDER
    # ─────────────────────────────────────────────

    def _get_tradeable_symbols(self) -> list[str]:
        """
        Get all US equity symbols that are tradeable on Alpaca.
        Filters out ETFs, OTC pennies, and non-fractionable stocks.
        """
        try:
            request = GetAssetsRequest(
                asset_class=AssetClass.US_EQUITY,
                status=AssetStatus.ACTIVE,
            )
            assets = self.trading_client.get_all_assets(request)
            symbols = [
                a.symbol for a in assets
                if a.tradable
                and a.exchange in ("NYSE", "NASDAQ", "ARCA")
                and "." not in a.symbol  # skip preferred shares (BRK.B, etc.)
                and len(a.symbol) <= 5
            ]
            logger.info(f"Universe: {len(symbols)} tradeable symbols")
            return symbols
        except Exception as e:
            logger.error(f"Failed to fetch asset list: {e}")
            return []

    # ─────────────────────────────────────────────
    # 5 PILLARS FILTER
    # ─────────────────────────────────────────────

    def _apply_five_pillars(
        self,
        symbol: str,
        snap: dict,
        float_shares: Optional[int],
        rvol: float,
    ) -> Optional[StockCandidate]:
        """
        Apply Ross Cameron's 5 Pillars to a snapshot.
        Returns a StockCandidate if it passes enough pillars, None otherwise.
        At minimum, must pass pillars 1, 2, 4, 5 (RVOL, change %, price, float).
        """
        price = snap.get("price") or 0
        change_pct = snap.get("change_pct") or 0
        volume = snap.get("volume") or 0
        prev_close = snap.get("prev_close") or 0
        vwap = snap.get("vwap")

        candidate = StockCandidate(
            symbol=symbol,
            price=price,
            change_pct=change_pct,
            volume=volume,
            rvol=rvol,
            float_shares=float_shares,
            prev_close=prev_close,
            vwap=vwap,
        )

        # Pillar 1: Relative Volume >= 5x
        if rvol >= MIN_RELATIVE_VOLUME:
            candidate.pillars_passed += 1
            candidate.pillar_notes.append(f"✓ RVOL={rvol:.1f}x")
        else:
            candidate.pillar_notes.append(f"✗ RVOL={rvol:.1f}x (need {MIN_RELATIVE_VOLUME}x)")

        # Pillar 2: Price already up >= 10% on the day
        if change_pct >= MIN_PRICE_CHANGE_PCT:
            candidate.pillars_passed += 1
            candidate.pillar_notes.append(f"✓ Change={change_pct:.1f}%")
        else:
            candidate.pillar_notes.append(f"✗ Change={change_pct:.1f}% (need {MIN_PRICE_CHANGE_PCT}%)")

        # Pillar 3: News catalyst (cannot be auto-verified here — flagged for manual check)
        candidate.pillar_notes.append("⚠ News: verify manually")

        # Pillar 4: Price in $1-$20 range
        if MIN_PRICE <= price <= MAX_PRICE:
            candidate.pillars_passed += 1
            candidate.pillar_notes.append(f"✓ Price=${price:.2f}")
        else:
            candidate.pillar_notes.append(f"✗ Price=${price:.2f} (need ${MIN_PRICE}-${MAX_PRICE})")

        # Pillar 5: Float under 100M (ideally under 20M)
        if float_shares is not None:
            if float_shares <= MAX_FLOAT:
                candidate.pillars_passed += 1
                float_m = float_shares / 1_000_000
                note = f"✓ Float={float_m:.1f}M"
                if float_shares <= IDEAL_FLOAT:
                    note += " [LOW FLOAT - HIGH CONVICTION]"
                candidate.pillar_notes.append(note)
            else:
                candidate.pillar_notes.append(
                    f"✗ Float={float_shares/1e6:.1f}M (need <{MAX_FLOAT/1e6:.0f}M)"
                )
        else:
            candidate.pillar_notes.append("? Float: unknown")

        # Must pass at minimum: RVOL + price change + price range (pillars 1, 2, 4)
        core_pillars = (
            rvol >= MIN_RELATIVE_VOLUME
            and change_pct >= MIN_PRICE_CHANGE_PCT
            and MIN_PRICE <= price <= MAX_PRICE
        )
        if not core_pillars:
            return None

        return candidate

    # ─────────────────────────────────────────────
    # PRE-MARKET GAPPER SCAN
    # ─────────────────────────────────────────────

    def run_premarket_scan(self, max_candidates: int = 10) -> list[StockCandidate]:
        """
        Runs between 7:00 AM and 9:30 AM ET.
        Finds top gappers meeting the 5 Pillars.
        Returns sorted list: highest RVOL * change_pct score first.
        """
        logger.info("Starting pre-market gapper scan...")

        symbols = self._get_tradeable_symbols()
        if not symbols:
            logger.error("No symbols in universe. Check API keys.")
            return []

        # Batch snapshot all symbols (Alpaca supports bulk requests)
        # We'll scan in batches of 100 (API limit)
        all_candidates: list[StockCandidate] = []
        batch_size = 100

        for i in range(0, min(len(symbols), self.SCAN_UNIVERSE_SIZE), batch_size):
            batch = symbols[i: i + batch_size]
            snapshots = self.data.get_snapshots_bulk(batch)

            for sym, snap in snapshots.items():
                change_pct = snap.get("change_pct") or 0
                price = snap.get("price") or 0
                volume = snap.get("volume") or 0

                # Quick pre-filter before expensive calls
                if change_pct < MIN_PRICE_CHANGE_PCT:
                    continue
                if not (MIN_PRICE <= price <= MAX_PRICE):
                    continue
                if volume < 50_000:
                    continue

                # Get RVOL (requires daily bars — slower)
                rvol = self.data.get_relative_volume(sym, volume)
                if rvol < MIN_RELATIVE_VOLUME:
                    continue

                # Get float (cached after first call)
                float_shares = self.data.get_float(sym)

                candidate = self._apply_five_pillars(sym, snap, float_shares, rvol)
                if candidate:
                    all_candidates.append(candidate)

        # Score: pillars_passed first, then RVOL * change_pct
        all_candidates.sort(
            key=lambda c: (c.pillars_passed, c.rvol * c.change_pct),
            reverse=True,
        )

        top = all_candidates[:max_candidates]
        self._watchlist = top

        logger.info(f"Pre-market scan complete. Found {len(top)} candidates.")
        for c in top:
            logger.info(str(c))

        return top

    # ─────────────────────────────────────────────
    # INTRADAY HOD MOMENTUM SCANNER
    # ─────────────────────────────────────────────

    def run_hod_scan(self, symbols: Optional[list[str]] = None) -> list[StockCandidate]:
        """
        Intraday High-of-Day scanner. Run every 30-60 seconds during market hours.
        Alerts when a stock hits a new intraday high with significant volume.

        symbols: if None, scans the current watchlist + pre-market candidates.
        """
        if symbols is None:
            symbols = [c.symbol for c in self._watchlist]
        if not symbols:
            return []

        snapshots = self.data.get_snapshots_bulk(symbols)
        new_hod_candidates: list[StockCandidate] = []

        for sym, snap in snapshots.items():
            price = snap.get("price") or 0
            volume = snap.get("volume") or 0
            change_pct = snap.get("change_pct") or 0

            # Check if this is a new intraday high
            prev_hod = self._hod_tracker.get(sym, 0)
            if price > prev_hod and price > 0:
                self._hod_tracker[sym] = price

                # Only alert if it's a meaningful new high (not just noise)
                if prev_hod > 0 and change_pct >= MIN_PRICE_CHANGE_PCT:
                    rvol = self.data.get_relative_volume(sym, volume)
                    float_shares = self.data.get_float(sym)
                    candidate = self._apply_five_pillars(snap, float_shares, rvol)
                    if candidate:
                        candidate.pillar_notes.append("🔔 NEW HIGH OF DAY")
                        new_hod_candidates.append(candidate)
                        logger.info(f"HOD Alert: {candidate}")

        return new_hod_candidates

    # ─────────────────────────────────────────────
    # WATCHLIST
    # ─────────────────────────────────────────────

    @property
    def watchlist(self) -> list[StockCandidate]:
        return self._watchlist

    def add_to_watchlist(self, candidate: StockCandidate):
        if not any(c.symbol == candidate.symbol for c in self._watchlist):
            self._watchlist.append(candidate)

    def print_watchlist(self):
        if not self._watchlist:
            print("Watchlist is empty.")
            return
        print("\n" + "=" * 60)
        print("  WARRIOR TRADING WATCHLIST")
        print("=" * 60)
        for i, c in enumerate(self._watchlist, 1):
            print(f"\n#{i} {c}")
            for note in c.pillar_notes:
                print(f"    {note}")
        print("=" * 60 + "\n")
