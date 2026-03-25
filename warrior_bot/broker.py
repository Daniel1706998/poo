"""
Broker Module - Alpaca Order Execution
Handles: buy, sell, partial exits, position queries, hot-key style fast orders.
"""

import logging
from typing import Optional
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderStatus, QueryOrderStatus

from config import ALPACA_API_KEY, ALPACA_SECRET_KEY

logger = logging.getLogger(__name__)


class Broker:
    """
    Wraps Alpaca's Trading API.
    All orders go to the Paper Trading endpoint.
    """

    def __init__(self):
        self.client = TradingClient(
            api_key=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET_KEY,
            paper=True,  # PAPER TRADING — no real money
        )
        logger.info("Broker initialized (Paper Trading mode)")

    # ─────────────────────────────────────────────
    # ACCOUNT INFO
    # ─────────────────────────────────────────────

    def get_account(self) -> dict:
        """Get account details: equity, buying power, etc."""
        account = self.client.get_account()
        return {
            "equity": float(account.equity),
            "cash": float(account.cash),
            "buying_power": float(account.buying_power),
            "portfolio_value": float(account.portfolio_value),
            "pattern_day_trader": account.pattern_day_trader,
            "trading_blocked": account.trading_blocked,
        }

    def print_account(self):
        acc = self.get_account()
        print(f"\n  Account | Equity: ${acc['equity']:,.2f} | "
              f"Cash: ${acc['cash']:,.2f} | "
              f"Buying Power: ${acc['buying_power']:,.2f}")

    # ─────────────────────────────────────────────
    # POSITIONS
    # ─────────────────────────────────────────────

    def get_position(self, symbol: str) -> Optional[dict]:
        """Get current position for a symbol. Returns None if no position."""
        try:
            pos = self.client.get_open_position(symbol)
            return {
                "symbol": pos.symbol,
                "qty": int(pos.qty),
                "avg_entry_price": float(pos.avg_entry_price),
                "current_price": float(pos.current_price),
                "unrealized_pl": float(pos.unrealized_pl),
                "unrealized_plpc": float(pos.unrealized_plpc),
                "market_value": float(pos.market_value),
            }
        except Exception:
            return None

    def get_all_positions(self) -> list[dict]:
        """Get all open positions."""
        positions = self.client.get_all_positions()
        return [
            {
                "symbol": p.symbol,
                "qty": int(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "unrealized_pl": float(p.unrealized_pl),
            }
            for p in positions
        ]

    # ─────────────────────────────────────────────
    # ORDER EXECUTION
    # ─────────────────────────────────────────────

    def buy_market(self, symbol: str, shares: int) -> Optional[str]:
        """
        Market buy order (immediate fill at current ask).
        Ross uses market orders for momentum trades where speed matters.
        Returns order ID or None on failure.
        """
        if shares <= 0:
            logger.warning(f"Cannot buy {shares} shares of {symbol}")
            return None
        try:
            order = self.client.submit_order(
                MarketOrderRequest(
                    symbol=symbol,
                    qty=shares,
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY,
                )
            )
            logger.info(f"BUY {shares} {symbol} @ MARKET | Order ID: {order.id}")
            return str(order.id)
        except Exception as e:
            logger.error(f"Buy order failed for {symbol}: {e}")
            return None

    def buy_limit(self, symbol: str, shares: int, limit_price: float) -> Optional[str]:
        """
        Limit buy order — safer for volatile small-caps.
        Ross often uses limit orders to avoid slippage.
        """
        if shares <= 0:
            return None
        try:
            order = self.client.submit_order(
                LimitOrderRequest(
                    symbol=symbol,
                    qty=shares,
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY,
                    limit_price=round(limit_price, 2),
                )
            )
            logger.info(f"BUY LIMIT {shares} {symbol} @ ${limit_price:.2f} | Order ID: {order.id}")
            return str(order.id)
        except Exception as e:
            logger.error(f"Limit buy failed for {symbol}: {e}")
            return None

    def sell_market(self, symbol: str, shares: Optional[int] = None) -> Optional[str]:
        """
        Market sell order — used for stop loss execution.
        If shares is None, sells the full position.
        Ross's rule: when hitting stop, sell immediately at market.
        """
        try:
            if shares is None:
                pos = self.get_position(symbol)
                if not pos:
                    return None
                shares = pos["qty"]

            if shares <= 0:
                return None

            order = self.client.submit_order(
                MarketOrderRequest(
                    symbol=symbol,
                    qty=shares,
                    side=OrderSide.SELL,
                    time_in_force=TimeInForce.DAY,
                )
            )
            logger.info(f"SELL {shares} {symbol} @ MARKET | Order ID: {order.id}")
            return str(order.id)
        except Exception as e:
            logger.error(f"Sell order failed for {symbol}: {e}")
            return None

    def sell_limit(self, symbol: str, shares: int, limit_price: float) -> Optional[str]:
        """
        Limit sell order — used for profit targets.
        Ross sells partial positions at the 2:1 target with a limit order.
        """
        if shares <= 0:
            return None
        try:
            order = self.client.submit_order(
                LimitOrderRequest(
                    symbol=symbol,
                    qty=shares,
                    side=OrderSide.SELL,
                    time_in_force=TimeInForce.DAY,
                    limit_price=round(limit_price, 2),
                )
            )
            logger.info(f"SELL LIMIT {shares} {symbol} @ ${limit_price:.2f} | Order ID: {order.id}")
            return str(order.id)
        except Exception as e:
            logger.error(f"Limit sell failed for {symbol}: {e}")
            return None

    def close_position(self, symbol: str) -> Optional[str]:
        """Close the full position for a symbol immediately (market order)."""
        try:
            order = self.client.close_position(symbol)
            logger.info(f"CLOSED full position in {symbol}")
            return str(order.id)
        except Exception as e:
            logger.error(f"Failed to close position in {symbol}: {e}")
            return None

    def close_all_positions(self):
        """End-of-day force close. Ross never holds day trades overnight."""
        logger.info("Closing ALL positions (end of day)")
        self.client.close_all_positions(cancel_orders=True)

    # ─────────────────────────────────────────────
    # ORDER MANAGEMENT
    # ─────────────────────────────────────────────

    def cancel_all_orders(self):
        """Cancel all open orders."""
        self.client.cancel_orders()
        logger.info("All open orders cancelled.")

    def cancel_order(self, order_id: str):
        """Cancel a specific order by ID."""
        try:
            self.client.cancel_order_by_id(order_id)
            logger.info(f"Order {order_id} cancelled.")
        except Exception as e:
            logger.warning(f"Could not cancel order {order_id}: {e}")

    def get_open_orders(self) -> list[dict]:
        """Get all open orders."""
        request = GetOrdersRequest(status=QueryOrderStatus.OPEN)
        orders = self.client.get_orders(request)
        return [
            {
                "id": str(o.id),
                "symbol": o.symbol,
                "side": o.side.value,
                "qty": float(o.qty),
                "type": o.type.value,
                "limit_price": float(o.limit_price) if o.limit_price else None,
                "status": o.status.value,
            }
            for o in orders
        ]

    # ─────────────────────────────────────────────
    # TRADING HOURS CHECK
    # ─────────────────────────────────────────────

    def is_market_open(self) -> bool:
        """Check if the US market is currently open."""
        clock = self.client.get_clock()
        return clock.is_open

    def get_market_hours(self) -> dict:
        """Get today's market open/close times."""
        clock = self.client.get_clock()
        return {
            "is_open": clock.is_open,
            "next_open": clock.next_open,
            "next_close": clock.next_close,
        }
