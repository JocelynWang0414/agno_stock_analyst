# Investment Advisory Team

AI-powered investment research built with [Agno](https://github.com/agno-agi/agno). Describe any investment theme → maps to sectors, screens 10 large-caps, runs macro + parallel fundamental/technical analysis, outputs a ranked memo for the top 3.

## Pipeline

```
Topic Mapper      → 1–3 GICS sectors
Company Discovery → 10 large-cap tickers (+ LLM alignment check)
Coordinator       → research brief
Macro Analysis    → FRED data → Macro Regime Score 1–10
Parallel Analysis → Fundamental Analyst (FCF, EPS, P/E, ROE)
                    Technical Analyst   (MAs, RSI, momentum)
Ranking           → top 3 by composite score
Investment Memo   → final memo with macro backdrop
```

## Quick Start

```bash
pip install -r requirements.txt
```

`.env`:
```
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=google/gemini-2.5-flash-lite
FRED_API_KEY=...        # free at fred.stlouisfed.org/docs/api/api_key.html
```

```bash
python main.py --topic "AI and robotics"
python main.py --topic "clean energy transition"
python main.py --topic "US regional banking"
```

Outputs: `memo_<topic>_<T1>_<T2>_<T3>.md` + `trace_<topic>.json`

## Supported Sectors

`Technology` · `Healthcare` · `Energy` · `Financials` · `Consumer Cyclical` · `Consumer Defensive` · `Industrials` · `Basic Materials` · `Real Estate` · `Utilities` · `Communication Services`

## Stack

- [Agno](https://github.com/agno-agi/agno) — multi-agent workflow orchestration
- [YFinance](https://github.com/ranaroussi/yfinance) — market data & technical indicators
- [OpenRouter](https://openrouter.ai) — LLM gateway
- [FRED API](https://fred.stlouisfed.org) — macroeconomic data

---
*For educational/research purposes only. Not financial advice.*
