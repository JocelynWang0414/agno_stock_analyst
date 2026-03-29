# Investment Advisory Team

An AI-powered investment research system built with the [Agno](https://github.com/agno-agi/agno) multi-agent framework. Describe any investment theme in plain language — the system maps it to relevant sectors, screens 10 large-cap companies, runs parallel fundamental and technical analysis on all of them, and produces a structured investment recommendation memo for the top 3.

---

## How It Works

```
Topic Mapper        ← LLM maps your theme to 1–3 market sectors
Company Discovery   ← selects 10 large-cap companies across those sectors
Coordinator         ← builds the research brief
    │
    ├── [Parallel]
    │     ├── Fundamental Analyst   ← revenue, EPS, P/E, ROE, FCF, analyst consensus
    │     └── Technical Analyst     ← trend, MAs, RSI, momentum, support/resistance
    │
Ranking             ← scores all 10, picks top 3 by composite score
Portfolio Strategist ← scorecard + ranked memo + position sizing
```

| Agent | Role | Tools |
|-------|------|-------|
| **Topic Mapper** | Maps free-text theme to 1–3 GICS sectors | LLM |
| **Company Discovery** | Selects 10 large-cap companies for those sectors | Curated sector index |
| **Coordinator** | Builds research brief for all 10 tickers | Custom executor |
| **Fundamental Analyst** | Scores each company 1–5 on fundamentals | YFinance + custom FCF tool |
| **Technical Analyst** | Scores each company 1–5 on technicals | pandas-computed signals + YFinance |
| **Ranking** | Extracts scores, ranks all 10, selects top 3 | Regex + LLM fallback |
| **Portfolio Strategist** | Synthesises top-3 reports into investment memo | LLM synthesis |

The Fundamental and Technical Analysts run **in parallel**. The Ranking step picks the best 3 from all 10 before the final memo is written.

---

## Output

A markdown memo saved as `memo_<topic>_<T1>_<T2>_<T3>.md`, containing:

- **Executive Summary** — investment theme outlook and top takeaway
- **Company Scorecard** — fundamental, technical, and composite scores with Buy/Hold/Sell ratings
- **Ranked Recommendations** — top 3 picks with bullet-point rationale
- **Key Risks** — sector-level and stock-specific
- **Suggested Position Sizing** — overweight / market-weight / underweight
- **Disclaimer**

A full JSON observability trace is also saved as `trace_<topic>.json`.

---

## Quick Start

**1. Clone and install**
```bash
git clone <repo-url>
cd agno_stock_analyst
pip install -r requirements.txt
```

**2. Configure credentials**

Create a `.env` file in the project root:
```
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=google/gemini-2.5-flash-lite
```

Get a free API key at [openrouter.ai](https://openrouter.ai). Any OpenAI-compatible model works — swap `OPENROUTER_MODEL` for any slug OpenRouter supports (e.g. `openai/gpt-4o`, `anthropic/claude-3.5-sonnet`).

**3. Run**
```bash
python investment_advisory.py --topic "AI and robotics"
python investment_advisory.py --topic "clean energy transition"
python investment_advisory.py --topic "US regional banking"
python investment_advisory.py --topic "luxury consumer brands"
python investment_advisory.py --topic "semiconductor supply chain"
```

No `--topic` flag defaults to `"large-cap technology companies"`.

---

## Supported Sectors

The system maps themes to any combination of these 11 sectors:

| Sector | Example companies screened |
|--------|---------------------------|
| Technology | AAPL, MSFT, NVDA, AVGO, ORCL… |
| Healthcare | LLY, UNH, JNJ, ABBV, MRK… |
| Energy | XOM, CVX, COP, EOG, SLB… |
| Financials | JPM, BAC, WFC, GS, MS… |
| Consumer Cyclical | AMZN, TSLA, HD, MCD, NKE… |
| Consumer Defensive | PG, KO, PEP, WMT, COST… |
| Industrials | HON, CAT, GE, RTX, LMT… |
| Basic Materials | LIN, APD, ECL, SHW, FCX… |
| Real Estate | PLD, AMT, EQIX, CCI, SPG… |
| Utilities | NEE, DUK, SO, D, SRE… |
| Communication Services | GOOGL, META, NFLX, DIS, CMCSA… |

The LLM selects the 1–3 most relevant sectors for your theme and 10 companies are drawn proportionally from them.

---

## Project Structure

```
agno_stock_analyst/
├── investment_advisory.py   # Full workflow — all agents, steps, tools, and CLI
├── requirements.txt
├── .env                     # API credentials (not committed)
├── CLAUDE.md                # Dev notes, Agno API learnings, bug log
├── memo_*.md                # Generated output memos
└── trace_*.json             # Observability traces
```

---

## Stack

- **[Agno](https://github.com/agno-agi/agno)** — multi-agent workflow orchestration
- **[YFinance](https://github.com/ranaroussi/yfinance)** — real-time and historical market data
- **[pandas](https://pandas.pydata.org)** — technical indicator computation (RSI, MAs, momentum)
- **[OpenRouter](https://openrouter.ai)** — unified API gateway for LLMs
- **python-dotenv** — environment variable management

---

## Disclaimer

This tool is for educational and research purposes only. It does not constitute financial advice. Always consult a qualified financial advisor before making investment decisions.
