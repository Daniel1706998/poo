"""
Warrior Trading Bot - Configuration
Based on Ross Cameron's strategy from Warrior Trading
"""

# ─────────────────────────────────────────────
# BROKER SELECTION
# "ALPACA" = Alpaca Paper Trading (free, no desktop app needed)
# "IBKR"   = Interactive Brokers (requires TWS/Gateway running)
# ─────────────────────────────────────────────
BROKER_TYPE = "IBKR"

# ─────────────────────────────────────────────
# ALPACA API CREDENTIALS (Paper Trading)
# Get yours at: https://app.alpaca.markets/paper/dashboard/overview
# ─────────────────────────────────────────────
ALPACA_API_KEY = "PK35SOKW2SGMCL2GOQGMQBATUD"
ALPACA_SECRET_KEY = "9bCQ2HE3tqNngHAMDGVrtSXLSGSot1xeSnmPCbzoBfWB"
ALPACA_BASE_URL = "https://paper-api.alpaca.markets"  # Paper trading URL

# ─────────────────────────────────────────────
# IBKR (Interactive Brokers) CONNECTION
# TWS Paper Trading port: 7497 | Live: 7496
# IB Gateway Paper port:  4002 | Live: 4001
# ─────────────────────────────────────────────
IBKR_HOST = "127.0.0.1"
IBKR_PORT = 7497             # 7497 = TWS Paper Trading
IBKR_CLIENT_ID = 1           # Unique client ID (change if running multiple bots)

# ─────────────────────────────────────────────
# THE 5 PILLARS - Stock Selection Filters
# ─────────────────────────────────────────────
MIN_RELATIVE_VOLUME = 5.0        # Minimum 5x relative volume vs 30-day avg
MIN_PRICE_CHANGE_PCT = 10.0      # Minimum 10% gain on the day
MIN_PRICE = 1.0                  # Minimum stock price ($1)
MAX_PRICE = 20.0                 # Maximum stock price ($20)
MAX_FLOAT = 100_000_000          # Maximum float (100M shares)
IDEAL_FLOAT = 20_000_000         # Ideal float for highest conviction (20M)

# ─────────────────────────────────────────────
# RISK MANAGEMENT
# ─────────────────────────────────────────────
MAX_DAILY_LOSS = 500.0           # Daily max loss circuit breaker ($)
MAX_RISK_PER_TRADE = 100.0       # Max dollar risk per trade ($)
MIN_REWARD_RISK_RATIO = 2.0      # Minimum reward:risk ratio (2:1)
STOP_CENTS = 0.20                # Default stop distance in cents from entry

# Partial exit (scale out)
FIRST_PARTIAL_PCT = 0.50         # Sell 50% at first target
FIRST_TARGET_RATIO = 2.0         # First target = 2x the risk (2:1 R:R)

# ─────────────────────────────────────────────
# TRADING HOURS (Eastern Time)
# ─────────────────────────────────────────────
PRE_MARKET_SCAN_START = "07:00"  # Start pre-market scan
MARKET_OPEN = "09:30"            # NYSE market open
TRADING_END = "11:30"            # Stop looking for new setups after this
MARKET_CLOSE = "16:00"           # Force-close all positions

# ─────────────────────────────────────────────
# TECHNICAL INDICATORS
# ─────────────────────────────────────────────
EMA_FAST = 9                     # 9 EMA (fast, gray line)
EMA_SLOW = 20                    # 20 EMA (slow, blue line)
EMA_TREND = 200                  # 200 EMA (daily trend filter)

# ─────────────────────────────────────────────
# PATTERN DETECTION PARAMETERS
# ─────────────────────────────────────────────
BULL_FLAG_MAX_RETRACE = 0.50     # Flag can't retrace more than 50% of pole
BULL_FLAG_CANDLES = 5            # Max candles in the flag consolidation
GAP_MIN_PCT = 0.10               # Minimum gap up % from prior close

# ─────────────────────────────────────────────
# POSITION SIZING
# ─────────────────────────────────────────────
MAX_POSITIONS = 1                # Max concurrent positions (Ross trades 1 at a time)

# ─────────────────────────────────────────────
# TELEGRAM NOTIFICATIONS
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = "8179823495:AAEGcW7HNvZc9sgMFGnjr4FXmhHywpjXDtY"
TELEGRAM_CHAT_ID = "380731190"

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
LOG_FILE = "warrior_bot_trades.log"
TRADE_JOURNAL_FILE = "trade_journal.csv"
