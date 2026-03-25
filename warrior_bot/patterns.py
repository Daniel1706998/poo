"""
Technical Pattern Detection
Implements Ross Cameron's core setups:
  - VWAP (Volume Weighted Average Price)
  - EMA (9, 20, 200)
  - Gap and Go
  - Bull Flag
  - ABCD Pattern
  - Opening Range Breakout (ORB)
  - Red-to-Green Move
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional
import numpy as np
import pandas as pd

from config import (
    EMA_FAST, EMA_SLOW,
    BULL_FLAG_MAX_RETRACE,
    BULL_FLAG_CANDLES,
    GAP_MIN_PCT,
)

logger = logging.getLogger(__name__)


class SetupType(Enum):
    GAP_AND_GO = "Gap and Go"
    BULL_FLAG = "Bull Flag"
    ABCD = "ABCD Pattern"
    ORB = "Opening Range Breakout"
    RED_TO_GREEN = "Red-to-Green"
    VWAP_RECLAIM = "VWAP Reclaim"
    NONE = "No Setup"


@dataclass
class SetupSignal:
    """Describes a detected trading setup."""
    setup_type: SetupType
    entry_price: float           # Suggested entry (limit order price)
    stop_price: float            # Hard stop loss price
    target_price: float          # First profit target (2:1 R:R)
    confidence: float            # 0.0 - 1.0 (how clean the setup is)
    notes: str = ""

    @property
    def risk_per_share(self) -> float:
        return abs(self.entry_price - self.stop_price)

    @property
    def reward_per_share(self) -> float:
        return abs(self.target_price - self.entry_price)

    @property
    def reward_risk_ratio(self) -> float:
        if self.risk_per_share == 0:
            return 0
        return self.reward_per_share / self.risk_per_share

    def __str__(self):
        return (
            f"{self.setup_type.value} | "
            f"Entry=${self.entry_price:.2f} "
            f"Stop=${self.stop_price:.2f} "
            f"Target=${self.target_price:.2f} "
            f"R:R={self.reward_risk_ratio:.1f} "
            f"Conf={self.confidence:.0%}"
        )


# ─────────────────────────────────────────────
# INDICATOR CALCULATIONS
# ─────────────────────────────────────────────

def calculate_vwap(bars: pd.DataFrame) -> pd.Series:
    """
    VWAP = sum(price * volume) / sum(volume)
    Uses typical price = (high + low + close) / 3
    Resets each day (uses today's bars only).
    """
    typical_price = (bars["high"] + bars["low"] + bars["close"]) / 3
    cumulative_tp_vol = (typical_price * bars["volume"]).cumsum()
    cumulative_vol = bars["volume"].cumsum()
    vwap = cumulative_tp_vol / cumulative_vol
    vwap.name = "vwap"
    return vwap


def calculate_ema(bars: pd.DataFrame, period: int) -> pd.Series:
    """Exponential Moving Average."""
    ema = bars["close"].ewm(span=period, adjust=False).mean()
    ema.name = f"ema_{period}"
    return ema


def calculate_indicators(bars: pd.DataFrame) -> pd.DataFrame:
    """
    Add all indicators to a bar DataFrame.
    Returns bars + vwap, ema_9, ema_20 columns.
    """
    df = bars.copy()
    df["vwap"] = calculate_vwap(df)
    df[f"ema_{EMA_FAST}"] = calculate_ema(df, EMA_FAST)
    df[f"ema_{EMA_SLOW}"] = calculate_ema(df, EMA_SLOW)
    return df


def is_above_vwap(bars: pd.DataFrame) -> bool:
    """Price is above VWAP → bullish bias."""
    if "vwap" not in bars.columns:
        bars = calculate_indicators(bars)
    last = bars.iloc[-1]
    return last["close"] > last["vwap"]


# ─────────────────────────────────────────────
# GAP AND GO
# ─────────────────────────────────────────────

def detect_gap_and_go(
    bars_1min: pd.DataFrame,
    prev_close: float,
    premarket_high: Optional[float] = None,
) -> Optional[SetupSignal]:
    """
    Gap and Go: stock opened above prior close by >= 10%, and is now
    breaking above the pre-market high (or the opening candle high).

    Entry: just above pre-market high (or opening candle high)
    Stop:  below the opening candle low (or pre-market high)
    Target: 2x the risk above entry
    """
    if bars_1min.empty or len(bars_1min) < 2:
        return None

    first_candle = bars_1min.iloc[0]
    last_candle = bars_1min.iloc[-1]
    current_price = last_candle["close"]

    # Check gap %
    gap_pct = (first_candle["open"] - prev_close) / prev_close
    if gap_pct < GAP_MIN_PCT:
        return None

    # Determine breakout level
    breakout_level = premarket_high if premarket_high else first_candle["high"]
    stop_level = first_candle["low"]  # Below opening candle low

    # Price must be at or just below the breakout level for a "fresh" trigger
    near_breakout = (current_price >= breakout_level * 0.99)
    breaking_out = (current_price >= breakout_level)

    if not near_breakout:
        return None

    # Confirm VWAP is below price (bullish)
    bars_with_ind = calculate_indicators(bars_1min)
    above_vwap = last_candle["close"] > bars_with_ind["vwap"].iloc[-1]

    entry = breakout_level + 0.05  # Trigger 5 cents above breakout
    stop = stop_level - 0.05       # Stop 5 cents below opening low
    risk = entry - stop
    if risk <= 0:
        return None
    target = entry + (risk * 2.0)  # 2:1 R:R

    confidence = 0.6
    if breaking_out:
        confidence += 0.2
    if above_vwap:
        confidence += 0.1
    if gap_pct >= 0.20:           # 20%+ gap = very high conviction
        confidence += 0.1
    confidence = min(confidence, 1.0)

    return SetupSignal(
        setup_type=SetupType.GAP_AND_GO,
        entry_price=round(entry, 2),
        stop_price=round(stop, 2),
        target_price=round(target, 2),
        confidence=confidence,
        notes=f"Gap={gap_pct:.1%} BreakoutLevel=${breakout_level:.2f}",
    )


# ─────────────────────────────────────────────
# BULL FLAG
# ─────────────────────────────────────────────

def detect_bull_flag(bars_1min: pd.DataFrame) -> Optional[SetupSignal]:
    """
    Bull Flag:
    - Flagpole: sharp move up (at least 3 consecutive green candles with volume)
    - Flag: 2-5 red/sideways candles, retracing < 50% of flagpole
    - Entry trigger: first green candle breaking above the flag high
    - Stop: below the flag low

    Ross's rule: the flag must NOT retrace more than 50% of the flagpole.
    """
    if len(bars_1min) < 6:
        return None

    df = bars_1min.copy()

    # Find flagpole: look for a sharp run-up in recent bars
    # We define a flagpole as 3+ bars where each closes higher than open
    flagpole_end = None
    flagpole_start = None

    for i in range(len(df) - BULL_FLAG_CANDLES - 3, 0, -1):
        # Check if bars[i:i+3] form a flagpole (3+ consecutive green bars)
        segment = df.iloc[i:i + 4]
        all_green = all(segment["close"] > segment["open"])
        if all_green:
            flagpole_start = i
            flagpole_end = i + 3
            break

    if flagpole_start is None:
        return None

    pole_low = df.iloc[flagpole_start]["low"]
    pole_high = df.iloc[flagpole_end - 1]["high"]
    pole_height = pole_high - pole_low

    # Flag: the bars after the flagpole
    flag_bars = df.iloc[flagpole_end:]
    if len(flag_bars) < 2 or len(flag_bars) > BULL_FLAG_CANDLES + 2:
        return None

    flag_high = flag_bars["high"].max()
    flag_low = flag_bars["low"].min()

    # Retrace check: flag low must not go below 50% retracement of pole
    max_retrace = pole_high - (pole_height * BULL_FLAG_MAX_RETRACE)
    if flag_low < max_retrace:
        return None

    # Most flag candles should be red or doji (consolidation)
    red_candles = sum(1 for _, row in flag_bars.iterrows() if row["close"] < row["open"])
    if red_candles < len(flag_bars) * 0.4:
        return None

    # Current price: we want to enter just above flag high (breakout)
    current_price = df.iloc[-1]["close"]
    near_breakout = current_price >= flag_high * 0.98

    if not near_breakout:
        return None

    entry = flag_high + 0.05
    stop = flag_low - 0.05
    risk = entry - stop
    if risk <= 0:
        return None
    target = entry + (risk * 2.0)

    # Confidence based on flag quality
    tight_flag = (flag_high - flag_low) / pole_height < 0.30  # tight = high conf
    confidence = 0.65
    if tight_flag:
        confidence += 0.15
    if len(flag_bars) <= 3:         # Short flag = cleaner setup
        confidence += 0.10
    if red_candles == len(flag_bars):  # All red = perfect flag
        confidence += 0.10
    confidence = min(confidence, 1.0)

    return SetupSignal(
        setup_type=SetupType.BULL_FLAG,
        entry_price=round(entry, 2),
        stop_price=round(stop, 2),
        target_price=round(target, 2),
        confidence=confidence,
        notes=f"Pole={pole_height:.2f} FlagHigh=${flag_high:.2f} FlagLow=${flag_low:.2f}",
    )


# ─────────────────────────────────────────────
# OPENING RANGE BREAKOUT (ORB)
# ─────────────────────────────────────────────

def detect_orb(bars_1min: pd.DataFrame, orb_minutes: int = 5) -> Optional[SetupSignal]:
    """
    Opening Range Breakout:
    - Opening range = first N minutes of trading (default: 5 minutes)
    - Entry: break above the opening range high
    - Stop: below the opening range low
    """
    if len(bars_1min) < orb_minutes + 1:
        return None

    opening_range = bars_1min.iloc[:orb_minutes]
    orb_high = opening_range["high"].max()
    orb_low = opening_range["low"].min()

    current_price = bars_1min.iloc[-1]["close"]
    current_volume = bars_1min["volume"].iloc[-1]
    avg_volume = bars_1min["volume"].mean()

    # Price must be at or above ORB high
    breaking_out = current_price >= orb_high
    volume_confirmation = current_volume > avg_volume * 1.5

    if not (breaking_out and volume_confirmation):
        return None

    entry = orb_high + 0.05
    stop = orb_low - 0.05
    risk = entry - stop
    if risk <= 0:
        return None
    target = entry + (risk * 2.0)

    confidence = 0.60
    if volume_confirmation:
        confidence += 0.15
    if current_price > orb_high * 1.02:  # Already extended past ORB
        confidence -= 0.10  # Don't chase extended moves

    return SetupSignal(
        setup_type=SetupType.ORB,
        entry_price=round(entry, 2),
        stop_price=round(stop, 2),
        target_price=round(target, 2),
        confidence=confidence,
        notes=f"ORB High=${orb_high:.2f} Low=${orb_low:.2f} ({orb_minutes}min range)",
    )


# ─────────────────────────────────────────────
# RED-TO-GREEN MOVE
# ─────────────────────────────────────────────

def detect_red_to_green(
    bars_1min: pd.DataFrame,
    prev_close: float,
) -> Optional[SetupSignal]:
    """
    Red-to-Green: stock was below prev close, now crossing back above it.
    Entry: at the green cross with volume confirmation.
    Stop: below VWAP or prev_close.
    """
    if bars_1min.empty:
        return None

    df = calculate_indicators(bars_1min)
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else None

    current_price = last["close"]
    vwap = last["vwap"]

    # Was below prev_close, now above it
    was_red = prev is not None and prev["close"] < prev_close
    is_green = current_price > prev_close

    if not (was_red and is_green):
        return None

    # Volume spike at the cross
    current_volume = last["volume"]
    avg_volume = df["volume"].mean()
    volume_spike = current_volume > avg_volume * 2.0

    if not volume_spike:
        return None

    entry = prev_close + 0.05
    stop = min(vwap, prev_close) - 0.10
    risk = entry - stop
    if risk <= 0:
        return None
    target = entry + (risk * 2.0)

    confidence = 0.55
    if volume_spike:
        confidence += 0.20
    if current_price > vwap:
        confidence += 0.10

    return SetupSignal(
        setup_type=SetupType.RED_TO_GREEN,
        entry_price=round(entry, 2),
        stop_price=round(stop, 2),
        target_price=round(target, 2),
        confidence=confidence,
        notes=f"PrevClose=${prev_close:.2f} VWAP=${vwap:.2f}",
    )


# ─────────────────────────────────────────────
# MASTER DETECTOR
# ─────────────────────────────────────────────

def find_best_setup(
    bars_1min: pd.DataFrame,
    prev_close: float,
    premarket_high: Optional[float] = None,
    min_confidence: float = 0.65,
) -> Optional[SetupSignal]:
    """
    Run all pattern detectors and return the highest-confidence setup.
    Returns None if no setup meets the minimum confidence threshold.
    """
    candidates: list[SetupSignal] = []

    for detector, kwargs in [
        (detect_gap_and_go, {"prev_close": prev_close, "premarket_high": premarket_high}),
        (detect_bull_flag, {}),
        (detect_orb, {}),
        (detect_red_to_green, {"prev_close": prev_close}),
    ]:
        try:
            signal = detector(bars_1min, **kwargs)
            if signal and signal.confidence >= min_confidence:
                candidates.append(signal)
        except Exception as e:
            logger.debug(f"Pattern detector failed: {e}")

    if not candidates:
        return None

    # Return the highest-confidence setup
    best = max(candidates, key=lambda s: s.confidence)
    logger.info(f"Best setup found: {best}")
    return best
