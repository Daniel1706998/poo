"""
Broker Module - Interactive Brokers Order Execution via ib_insync
Drop-in replacement for broker.py (Alpaca).
Same interface: Broker class with identical method signatures.

Requires:
  - TWS or IB Gateway running on localhost
  - API connections enabled in TWS (File → Global Config → API → Settings)
  - Paper trading port: 7497 (TWS) or 4002 (IB Gateway)
"""

import logging
import time
from typing import Optional

from ib_insync import IB, Stock, MarketOrder, LimitOrder, util

from config import IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID

logger = logging.getLogger(__name__)


class Broker:
    """
    Wraps Interactive Brokers TWS API via ib_insync.
    Same interface as the Alpaca Broker class.
    """

    def __init__(self):
        self.ib = IB()
        try:
            self.ib.connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID)
            account_type = "Paper" if IBKR_PORT in (7497, 4002) else "LIVE"
            logger.info(f"IBKR Broker initialized ({account_type} Trading mode)")
        except Exception as e:
            logger.error(f"Failed to connect to IBKR TWS/Gateway: {e}")
            raise ConnectionError(
                f"Cannot connect to IBKR at {IBKR_HOST}:{IBKR_PORT}. "
                "Make sure TWS or IB Gateway is running and API is enabled."
            )

    def _make_contract(self, symbol: str) -> Stock:
        """Create an IB Stock contract for US equities."""
        return Stock(symbol, "SMART", "USD")

    def _qualify(self, contract: Stock) -> Stock:
        """Resolve contract details with IB."""
        qualified = self.ib.qualifyContracts(contract)
        if not qualified:
            raise ValueError(f"Could not qualify contract for {contract.symbol}")
        return qualified[0]

    # ─────────────────────────────────────────────
    # ACCOUNT INFO
    # ─────────────────────────────────────────────

    def get_account(self) -> dict:
        """Get account details: equity, buying power, etc."""
        self.ib.reqAccountSummary()
        time.sleep(0.5)
        summary = self.ib.accountSummary()

        values = {}
        for item in summary:
            values[item.tag] = item.value

        equity = float(values.get("NetLiquidation", 0))
        cash = float(values.get("TotalCashValue", 0))
        buying_power = float(values.get("BuyingPower", 0))

        return {
            "equity": equity,
            "cash": cash,
            "buying_power": buying_power,
            "portfolio_value": equity,
            "pattern_day_trader": False,
            "trading_blocked": False,
        }

    def print_account(self):
        try:
            acc = self.get_account()
            print(f"\n  Account | Equity: ${acc['equity']:,.2f} | "
                  f"Cash: ${acc['cash']:,.2f} | "
                  f"Buying Power: ${acc['buying_power']:,.2f}")
        except Exception as e:
            logger.error(f"Could not get account info: {e}")

    # ─────────────────────────────────────────────
    # POSITIONS
    # ─────────────────────────────────────────────

    def get_position(self, symbol: str) -> Optional[dict]:
        """Get current position for a symbol. Returns None if no position."""
        try:
            positions = self.ib.positions()
            for pos in positions:
                if pos.contract.symbol == symbol:
                    avg_cost = pos.avgCost
                    qty = int(pos.position)
                    if qty == 0:
                        return None
                    # Get current price
                    contract = self._make_contract(symbol)
                    self.ib.qualifyContracts(contract)
                    ticker = self.ib.reqMktData(contract, snapshot=True)
                    self.ib.sleep(1)
                    current_price = ticker.marketPrice()
                    if current_price != current_price:  # NaN check
                        current_price = ticker.close or avg_cost
                    self.ib.cancelMktData(contract)

                    unrealized_pl = (current_price - avg_cost) * qty
                    unrealized_plpc = (current_price - avg_cost) / avg_cost if avg_cost else 0

                    return {
                        "symbol": symbol,
                        "qty": abs(qty),
                        "avg_entry_price": avg_cost,
                        "current_price": current_price,
                        "unrealized_pl": unrealized_pl,
                        "unrealized_plpc": unrealized_plpc,
                        "market_value": current_price * abs(qty),
                    }
            return None
        except Exception as e:
            logger.warning(f"Failed to get position for {symbol}: {e}")
            return None

    def get_all_positions(self) -> list[dict]:
        """Get all open positions."""
        try:
            positions = self.ib.positions()
            result = []
            for pos in positions:
                qty = int(pos.position)
                if qty == 0:
                    continue
                result.append({
                    "symbol": pos.contract.symbol,
                    "qty": abs(qty),
                    "avg_entry_price": pos.avgCost,
                    "current_price": pos.avgCost,  # Approximate; no live price fetch for speed
                    "unrealized_pl": 0.0,
                })
            return result
        except Exception as e:
            logger.warning(f"Failed to get positions: {e}")
            return []

    # ─────────────────────────────────────────────
    # ORDER EXECUTION
    # ─────────────────────────────────────────────

    def buy_market(self, symbol: str, shares: int) -> Optional[str]:
        """Market buy order. Returns order ID or None."""
        if shares <= 0:
            logger.warning(f"Cannot buy {shares} shares of {symbol}")
            return None
        try:
            contract = self._make_contract(symbol)
            self._qualify(contract)
            order = MarketOrder("BUY", shares)
            trade = self.ib.placeOrder(contract, order)
            self.ib.sleep(1)  # Wait for submission
            order_id = str(trade.order.orderId)
            logger.info(f"BUY {shares} {symbol} @ MARKET | Order ID: {order_id}")
            return order_id
        except Exception as e:
            logger.error(f"Buy order failed for {symbol}: {e}")
            return None

    def buy_limit(self, symbol: str, shares: int, limit_price: float) -> Optional[str]:
        """Limit buy order. Returns order ID or None."""
        if shares <= 0:
            return None
        try:
            contract = self._make_contract(symbol)
            self._qualify(contract)
            order = LimitOrder("BUY", shares, round(limit_price, 2))
            trade = self.ib.placeOrder(contract, order)
            self.ib.sleep(1)
            order_id = str(trade.order.orderId)
            logger.info(f"BUY LIMIT {shares} {symbol} @ ${limit_price:.2f} | Order ID: {order_id}")
            return order_id
        except Exception as e:
            logger.error(f"Limit buy failed for {symbol}: {e}")
            return None

    def sell_market(self, symbol: str, shares: Optional[int] = None) -> Optional[str]:
        """Market sell order. If shares is None, sells full position."""
        try:
            if shares is None:
                pos = self.get_position(symbol)
                if not pos:
                    return None
                shares = pos["qty"]

            if shares <= 0:
                return None

            contract = self._make_contract(symbol)
            self._qualify(contract)
            order = MarketOrder("SELL", shares)
            trade = self.ib.placeOrder(contract, order)
            self.ib.sleep(1)
            order_id = str(trade.order.orderId)
            logger.info(f"SELL {shares} {symbol} @ MARKET | Order ID: {order_id}")
            return order_id
        except Exception as e:
            logger.error(f"Sell order failed for {symbol}: {e}")
            return None

    def sell_limit(self, symbol: str, shares: int, limit_price: float) -> Optional[str]:
        """Limit sell order. Returns order ID or None."""
        if shares <= 0:
            return None
        try:
            contract = self._make_contract(symbol)
            self._qualify(contract)
            order = LimitOrder("SELL", shares, round(limit_price, 2))
            trade = self.ib.placeOrder(contract, order)
            self.ib.sleep(1)
            order_id = str(trade.order.orderId)
            logger.info(f"SELL LIMIT {shares} {symbol} @ ${limit_price:.2f} | Order ID: {order_id}")
            return order_id
        except Exception as e:
            logger.error(f"Limit sell failed for {symbol}: {e}")
            return None

    def close_position(self, symbol: str) -> Optional[str]:
        """Close the full position for a symbol immediately (market order)."""
        return self.sell_market(symbol, shares=None)

    def close_all_positions(self):
        """End-of-day force close. Close all positions at market."""
        logger.info("Closing ALL positions (end of day)")
        positions = self.ib.positions()
        for pos in positions:
            qty = int(pos.position)
            if qty == 0:
                continue
            contract = pos.contract
            self.ib.qualifyContracts(contract)
            side = "SELL" if qty > 0 else "BUY"
            order = MarketOrder(side, abs(qty))
            self.ib.placeOrder(contract, order)
            logger.info(f"Closing {pos.contract.symbol}: {side} {abs(qty)} shares")
        self.ib.sleep(2)

    # ─────────────────────────────────────────────
    # ORDER MANAGEMENT
    # ─────────────────────────────────────────────

    def cancel_all_orders(self):
        """Cancel all open orders."""
        self.ib.reqGlobalCancel()
        logger.info("All open orders cancelled.")

    def cancel_order(self, order_id: str):
        """Cancel a specific order by ID."""
        try:
            for trade in self.ib.openTrades():
                if str(trade.order.orderId) == order_id:
                    self.ib.cancelOrder(trade.order)
                    logger.info(f"Order {order_id} cancelled.")
                    return
            logger.warning(f"Order {order_id} not found in open trades.")
        except Exception as e:
            logger.warning(f"Could not cancel order {order_id}: {e}")

    def get_open_orders(self) -> list[dict]:
        """Get all open orders."""
        trades = self.ib.openTrades()
        return [
            {
                "id": str(t.order.orderId),
                "symbol": t.contract.symbol,
                "side": t.order.action,
                "qty": float(t.order.totalQuantity),
                "type": t.order.orderType,
                "limit_price": t.order.lmtPrice if t.order.orderType == "LMT" else None,
                "status": t.orderStatus.status,
            }
            for t in trades
        ]

    # ─────────────────────────────────────────────
    # TRADING HOURS CHECK
    # ─────────────────────────────────────────────

    def is_market_open(self) -> bool:
        """Check if the US market is currently open."""
        # IB doesn't have a simple is_open check like Alpaca.
        # Use trading hours from contract details.
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone(timedelta(hours=-5)))  # ET
        hour = now.hour
        minute = now.minute
        weekday = now.weekday()
        # Market open Mon-Fri 9:30-16:00 ET
        if weekday >= 5:
            return False
        if hour < 9 or (hour == 9 and minute < 30):
            return False
        if hour >= 16:
            return False
        return True

    def get_market_hours(self) -> dict:
        """Get today's market open/close times."""
        return {
            "is_open": self.is_market_open(),
            "next_open": None,
            "next_close": None,
        }
