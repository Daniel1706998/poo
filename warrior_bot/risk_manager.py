"""
Risk Manager - Ross Cameron's Risk Rules
- Position sizing formula: shares = max_risk / stop_distance
- Daily max loss circuit breaker
- Drawdown protocol (Trader Rehab)
- 2:1 minimum R:R enforcement
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from config import (
    MAX_DAILY_LOSS,
    MAX_RISK_PER_TRADE,
    MIN_REWARD_RISK_RATIO,
    FIRST_PARTIAL_PCT,
    FIRST_TARGET_RATIO,
)
import telegram_notify

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    symbol: str
    entry_price: float
    stop_price: float
    target_price: float
    shares: int
    pnl: float = 0.0
    closed: bool = False


@dataclass
class DayStats:
    date: date
    realized_pnl: float = 0.0
    trades_taken: int = 0
    wins: int = 0
    losses: int = 0
    max_loss_hit: bool = False
    trade_records: list[TradeRecord] = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses
        return self.wins / total if total > 0 else 0.0

    @property
    def is_trading_allowed(self) -> bool:
        return not self.max_loss_hit


class RiskManager:
    """
    Enforces Ross Cameron's risk management rules:
    1. Position sizing based on risk/stop distance
    2. Daily max loss circuit breaker
    3. 2:1 minimum reward-to-risk
    4. Drawdown protocol for losing streaks
    """

    def __init__(self):
        self._today = DayStats(date=date.today())
        self._consecutive_losses = 0
        self._drawdown_mode = False

        # In drawdown mode, reduce limits
        self._current_max_daily_loss = MAX_DAILY_LOSS
        self._current_max_risk_per_trade = MAX_RISK_PER_TRADE

    # ─────────────────────────────────────────────
    # NEW DAY RESET
    # ─────────────────────────────────────────────

    def new_day(self):
        """Call at the start of each trading day to reset daily stats."""
        self._today = DayStats(date=date.today())
        logger.info(
            f"New trading day. Max daily loss: ${self._current_max_daily_loss:.0f} | "
            f"Max risk/trade: ${self._current_max_risk_per_trade:.0f}"
        )
        if self._drawdown_mode:
            logger.warning("Still in TRADER REHAB mode. Reduced limits active.")

    # ─────────────────────────────────────────────
    # POSITION SIZING
    # ─────────────────────────────────────────────

    def calculate_shares(
        self,
        entry_price: float,
        stop_price: float,
        max_risk: Optional[float] = None,
    ) -> int:
        """
        Ross's formula: shares = max_dollar_risk / stop_distance_in_dollars
        Example: $100 risk / $0.20 stop = 500 shares

        Returns 0 if the trade doesn't meet risk criteria.
        """
        if max_risk is None:
            max_risk = self._current_max_risk_per_trade

        stop_distance = abs(entry_price - stop_price)
        if stop_distance <= 0:
            logger.warning("Stop distance is zero. Cannot size position.")
            return 0

        shares = int(max_risk / stop_distance)
        if shares < 1:
            return 0

        logger.info(
            f"Position sizing: ${max_risk:.0f} risk / ${stop_distance:.2f} stop "
            f"= {shares} shares at ${entry_price:.2f}"
        )
        return shares

    # ─────────────────────────────────────────────
    # TRADE VALIDATION
    # ─────────────────────────────────────────────

    def validate_trade(
        self,
        entry_price: float,
        stop_price: float,
        target_price: float,
    ) -> tuple[bool, str]:
        """
        Check if a trade is valid per Ross's rules.
        Returns (is_valid, reason).
        """
        # Circuit breaker: daily max loss hit
        if not self._today.is_trading_allowed:
            return False, f"Daily max loss hit (${self._today.realized_pnl:.2f}). No more trades today."

        # Reward:Risk check
        risk = abs(entry_price - stop_price)
        reward = abs(target_price - entry_price)
        if risk <= 0:
            return False, "Stop price equals entry price."

        rr = reward / risk
        if rr < MIN_REWARD_RISK_RATIO - 0.01:
            return False, f"R:R is {rr:.1f}x — minimum is {MIN_REWARD_RISK_RATIO:.0f}x. Skip this trade."

        # Max risk check
        shares = self.calculate_shares(entry_price, stop_price)
        if shares < 1:
            return False, "Position size is too small to trade."

        # Would this trade exceed the daily max loss if stopped out?
        potential_loss = shares * risk
        remaining_daily_budget = self._current_max_daily_loss + self._today.realized_pnl
        if potential_loss > remaining_daily_budget:
            return False, (
                f"Potential loss ${potential_loss:.0f} exceeds remaining daily budget "
                f"${remaining_daily_budget:.0f}."
            )

        return True, f"Valid trade. Shares={shares} R:R={rr:.1f}x Risk=${shares*risk:.0f}"

    # ─────────────────────────────────────────────
    # TRADE RECORDING
    # ─────────────────────────────────────────────

    def record_trade_open(
        self,
        symbol: str,
        entry_price: float,
        stop_price: float,
        target_price: float,
        shares: int,
    ) -> TradeRecord:
        record = TradeRecord(
            symbol=symbol,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            shares=shares,
        )
        self._today.trade_records.append(record)
        self._today.trades_taken += 1
        return record

    def record_trade_close(self, record: TradeRecord, exit_price: float, shares: int):
        """Record a closed trade (full or partial)."""
        pnl = (exit_price - record.entry_price) * shares
        record.pnl += pnl
        self._today.realized_pnl += pnl

        if not record.closed:
            if pnl > 0:
                self._today.wins += 1
                self._consecutive_losses = 0
            else:
                self._today.losses += 1
                self._consecutive_losses += 1
            record.closed = True

        logger.info(
            f"Trade closed: {record.symbol} | "
            f"P&L=${pnl:+.2f} | "
            f"Day P&L=${self._today.realized_pnl:+.2f}"
        )

        # Check circuit breaker
        self._check_circuit_breaker()

        # Check drawdown protocol
        self._check_drawdown_protocol()

    # ─────────────────────────────────────────────
    # CIRCUIT BREAKER
    # ─────────────────────────────────────────────

    def _check_circuit_breaker(self):
        """
        Ross's hard rule: when daily loss exceeds max, STOP trading.
        No exceptions, no "one more trade to recover."
        """
        if self._today.realized_pnl <= -self._current_max_daily_loss:
            self._today.max_loss_hit = True
            logger.critical(
                f"🛑 DAILY MAX LOSS HIT: ${self._today.realized_pnl:.2f} "
                f"(limit: ${self._current_max_daily_loss:.0f}). "
                f"ALL TRADING STOPPED FOR TODAY."
            )
            telegram_notify.notify_circuit_breaker(
                self._today.realized_pnl, self._current_max_daily_loss
            )

    # ─────────────────────────────────────────────
    # TRADER REHAB (DRAWDOWN PROTOCOL)
    # ─────────────────────────────────────────────

    def _check_drawdown_protocol(self):
        """
        When in a losing streak (3+ consecutive losses), enter Trader Rehab:
        - Reduce max daily loss to 50%
        - Reduce max risk per trade to 50%
        - Force back to basics
        """
        if self._consecutive_losses >= 3 and not self._drawdown_mode:
            self._drawdown_mode = True
            self._current_max_daily_loss = MAX_DAILY_LOSS * 0.50
            self._current_max_risk_per_trade = MAX_RISK_PER_TRADE * 0.50
            logger.warning(
                f"⚠ TRADER REHAB MODE ACTIVATED after {self._consecutive_losses} "
                f"consecutive losses. Limits reduced by 50%. "
                f"New daily max: ${self._current_max_daily_loss:.0f} | "
                f"New max risk/trade: ${self._current_max_risk_per_trade:.0f}"
            )
        elif self._consecutive_losses == 0 and self._drawdown_mode:
            # Exit rehab after a winning trade
            self._drawdown_mode = False
            self._current_max_daily_loss = MAX_DAILY_LOSS
            self._current_max_risk_per_trade = MAX_RISK_PER_TRADE
            logger.info("✓ Exiting Trader Rehab mode. Normal limits restored.")

    # ─────────────────────────────────────────────
    # EXIT LEVELS (SCALING OUT)
    # ─────────────────────────────────────────────

    def calculate_exit_levels(
        self,
        entry_price: float,
        stop_price: float,
        total_shares: int,
    ) -> dict:
        """
        Ross scales out in two tranches:
        - First exit: sell 50% at 2:1 R:R, move stop to breakeven
        - Second exit: trail stop on the remaining 50%

        Returns dict with first and second exit plans.
        """
        risk = abs(entry_price - stop_price)
        first_target = entry_price + (risk * FIRST_TARGET_RATIO)
        first_shares = int(total_shares * FIRST_PARTIAL_PCT)
        second_shares = total_shares - first_shares

        return {
            "first_exit": {
                "price": round(first_target, 2),
                "shares": first_shares,
                "action": f"Sell {first_shares} shares at ${first_target:.2f} (2:1 target)",
            },
            "second_exit": {
                "stop_after_first": round(entry_price, 2),  # Move to breakeven
                "shares": second_shares,
                "action": f"Trail stop on {second_shares} shares. Move stop to breakeven (${entry_price:.2f})",
            },
            "breakeven_stop": round(entry_price, 2),
        }

    # ─────────────────────────────────────────────
    # STATUS REPORT
    # ─────────────────────────────────────────────

    def print_status(self):
        stats = self._today
        print("\n" + "─" * 50)
        print(f"  RISK MANAGER STATUS — {stats.date}")
        print("─" * 50)
        print(f"  Day P&L:         ${stats.realized_pnl:+.2f}")
        print(f"  Trades today:    {stats.trades_taken}")
        print(f"  Win/Loss:        {stats.wins}W / {stats.losses}L")
        print(f"  Win rate:        {stats.win_rate:.0%}")
        print(f"  Max loss limit:  ${self._current_max_daily_loss:.0f}")
        print(f"  Remaining:       ${self._current_max_daily_loss + stats.realized_pnl:.0f}")
        print(f"  Trading OK:      {'✓ YES' if stats.is_trading_allowed else '🛑 NO - MAX LOSS HIT'}")
        if self._drawdown_mode:
            print(f"  Mode:            ⚠ TRADER REHAB")
        print("─" * 50 + "\n")

    @property
    def is_trading_allowed(self) -> bool:
        return self._today.is_trading_allowed

    @property
    def day_pnl(self) -> float:
        return self._today.realized_pnl
