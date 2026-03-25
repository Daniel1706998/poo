# Warrior Trading Bot 🤖
**Strategy:** Ross Cameron's Gap & Go Momentum Trading
**Mode:** Paper Trading (Alpaca)

---

## מה הבוט עושה

מיישם את האסטרטגיה של רוס קמרון:

| שלב | שעה (ET) | פעולה |
|-----|----------|--------|
| Pre-Market Scan | 07:00 | סורק מניות עם 5 Pillars |
| Market Open | 09:30 | מחפש כניסות (Gap & Go, Bull Flag) |
| Trade Monitoring | כל 30 שנ' | מנהל stop/target |
| Stop New Trades | 11:30 | מפסיק כניסות חדשות |
| End of Day | 15:55 | סוגר הכל - לא מחזיק לילה |

---

## התקנה

```bash
cd warrior_bot
pip install -r requirements.txt
```

## הגדרת API Keys

1. לך ל: https://app.alpaca.markets/paper/dashboard/overview
2. צור חשבון Paper Trading חינמי
3. העתק את ה-API Key וה-Secret Key
4. הדבק ב-`config.py`:

```python
ALPACA_API_KEY = "PKxxxxx"
ALPACA_SECRET_KEY = "xxxxxxxx"
```

## הרצה

```bash
python bot.py
```

---

## ה-5 Pillars של רוס קמרון

| # | קריטריון | ערך |
|---|----------|-----|
| 1 | Relative Volume | ≥ 5x |
| 2 | % Change today | ≥ +10% |
| 3 | News catalyst | בדיקה ידנית |
| 4 | Price range | $1 - $20 |
| 5 | Float | < 100M (ideal <20M) |

## ניהול סיכונים

- **Max daily loss:** $500 (circuit breaker)
- **Max risk/trade:** $100
- **Reward:Risk:** מינימום 2:1
- **Exit partial:** מוכר 50% ב-2:1, מזיז stop ל-breakeven
- **לא מחזיק לילה:** סוגר הכל ב-15:55

---

## קבצים

| קובץ | תפקיד |
|------|--------|
| `config.py` | הגדרות ופרמטרים |
| `data_feed.py` | נתוני שוק מ-Alpaca |
| `scanner.py` | סורק 5 Pillars |
| `patterns.py` | Gap&Go, Bull Flag, ORB |
| `risk_manager.py` | position sizing, circuit breaker |
| `broker.py` | ביצוע פקודות Alpaca |
| `strategy.py` | לוגיקת הכניסה והיציאה |
| `bot.py` | הרץ הראשי |

---

> ⚠️ **אזהרה:** בוט מסחר זה נועד ללמידה ו-Paper Trading.
> מסחר בכסף אמיתי כרוך בסיכון הפסד. השתמש בשיקול דעת.
