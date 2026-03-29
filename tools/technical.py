"""
Technical analysis tool — pandas-computed signals for the Technical Analyst agent.
"""

from textwrap import dedent

import pandas as pd
import yfinance as yf


def compute_technical_signals(symbol: str) -> str:
    """
    Download up to 1 year of daily OHLCV data for *symbol* and compute:
      - 50-day and 200-day simple moving averages
      - Golden / death cross (MA50 vs MA200 crossover in last 20 sessions)
      - RSI (14-period, Wilder smoothing via EWM)
      - 6-month trend classification: Uptrend / Downtrend / Sideways
      - 20-day rate-of-change (momentum)
      - 52-week and 20-day support / resistance levels
      - A composite technical score 1–5

    Returns a plain-text summary ready for the Technical Analyst agent.
    """
    ticker = symbol
    # Use Ticker.history() instead of yf.download() to avoid yfinance's request-batching
    # behaviour, which merges concurrent downloads across threads and can omit the requested
    # ticker entirely from the returned DataFrame.
    raw = yf.Ticker(ticker).history(period="1y", auto_adjust=True)

    if raw.empty or len(raw) < 21:
        return f"[{ticker}] Insufficient price history to compute signals."

    close = raw["Close"].astype(float)
    high  = raw["High"].astype(float)
    low   = raw["Low"].astype(float)

    current_price = float(close.iloc[-1])

    # ── Moving averages ───────────────────────────────────────────────────────
    ma50  = close.rolling(50).mean()
    ma200 = close.rolling(200).mean()

    cur_ma50  = float(ma50.iloc[-1])  if not pd.isna(ma50.iloc[-1])  else None
    cur_ma200 = float(ma200.iloc[-1]) if not pd.isna(ma200.iloc[-1]) else None

    def pct_vs(val, ref):
        return f"{(val - ref) / ref * 100:+.1f}%" if ref else "N/A"

    ma50_str  = f"${cur_ma50:.2f} ({pct_vs(current_price, cur_ma50)} vs price)"   if cur_ma50  else "N/A"
    ma200_str = f"${cur_ma200:.2f} ({pct_vs(current_price, cur_ma200)} vs price)" if cur_ma200 else "N/A"

    # ── Golden / death cross ─────────────────────────────────────────────────
    if cur_ma50 is not None and cur_ma200 is not None and len(ma50.dropna()) >= 20:
        prev_ma50  = float(ma50.iloc[-20])
        prev_ma200 = float(ma200.iloc[-20]) if not pd.isna(ma200.iloc[-20]) else cur_ma200
        if prev_ma50 <= prev_ma200 and cur_ma50 > cur_ma200:
            cross_signal = "Golden Cross within last 20 sessions (bullish)"
        elif prev_ma50 >= prev_ma200 and cur_ma50 < cur_ma200:
            cross_signal = "Death Cross within last 20 sessions (bearish)"
        elif cur_ma50 > cur_ma200:
            cross_signal = "MA50 above MA200 — bullish long-term alignment"
        else:
            cross_signal = "MA50 below MA200 — bearish long-term alignment"
    else:
        cross_signal = "Insufficient data for cross detection"

    # ── RSI (14-period, Wilder EWM) ──────────────────────────────────────────
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(com=13, min_periods=14).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=13, min_periods=14).mean()
    rsi_series = 100 - (100 / (1 + gain / loss))
    cur_rsi    = float(rsi_series.iloc[-1])

    if cur_rsi >= 70:
        rsi_label = f"Overbought ({cur_rsi:.1f})"
    elif cur_rsi <= 30:
        rsi_label = f"Oversold ({cur_rsi:.1f})"
    else:
        rsi_label = f"Neutral ({cur_rsi:.1f})"

    # ── Trend (6-month window, slope of 20-day MA) ───────────────────────────
    six_mo  = close.iloc[-126:]
    ma20_6m = six_mo.rolling(20).mean().dropna()
    if len(ma20_6m) >= 10:
        slope_pct = (float(ma20_6m.iloc[-1]) - float(ma20_6m.iloc[-10])) / float(ma20_6m.iloc[-10]) * 100
        if slope_pct > 2:
            trend = "Uptrend"
        elif slope_pct < -2:
            trend = "Downtrend"
        else:
            trend = "Sideways"
    else:
        trend = "Insufficient data"

    # ── Momentum (20-day rate of change) ────────────────────────────────────
    if len(close) >= 21:
        roc_20       = (current_price - float(close.iloc[-21])) / float(close.iloc[-21]) * 100
        momentum_str = f"{roc_20:+.2f}% (20-day ROC)"
    else:
        roc_20       = 0.0
        momentum_str = "N/A"

    # ── Support / resistance ─────────────────────────────────────────────────
    high_52w    = float(high.max())
    low_52w     = float(low.min())
    recent_high = float(high.iloc[-20:].max())
    recent_low  = float(low.iloc[-20:].min())

    # ── Composite technical score (1–5) ──────────────────────────────────────
    pts = 0

    # Price vs MA200: +2 well above, +1 slightly above, -1 slightly below, -2 well below
    if cur_ma200:
        d    = (current_price - cur_ma200) / cur_ma200 * 100
        pts += 2 if d > 5 else 1 if d > 0 else -1 if d > -5 else -2

    # Trend
    pts += 2 if trend == "Uptrend" else -2 if trend == "Downtrend" else 0

    # RSI: healthy range = bullish, extremes = caution
    pts += 1 if 40 <= cur_rsi <= 65 else -1 if cur_rsi < 30 else 0

    # MA alignment
    pts += 1 if "bullish" in cross_signal.lower() else -1

    # Momentum
    pts += 1 if roc_20 > 5 else -1 if roc_20 < -5 else 0

    # Map raw points (-7 → +7) to 1–5
    tech_score = (
        5 if pts >= 5
        else 4 if pts >= 2
        else 3 if pts >= -1
        else 2 if pts >= -4
        else 1
    )

    return dedent(f"""\
        COMPUTED TECHNICAL SIGNALS — {ticker}
        ─────────────────────────────────────────
        Current Price    : ${current_price:.2f}
        6-Month Trend    : {trend}
        Momentum         : {momentum_str}
        RSI (14)         : {rsi_label}
        50-Day MA        : {ma50_str}
        200-Day MA       : {ma200_str}
        MA Cross Signal  : {cross_signal}
        52-Week Range    : ${low_52w:.2f} – ${high_52w:.2f}
        20-Day Range     : ${recent_low:.2f} – ${recent_high:.2f}  ← near-term support/resistance
        ─────────────────────────────────────────
        Suggested Technical Score: {tech_score}/5
    """)
