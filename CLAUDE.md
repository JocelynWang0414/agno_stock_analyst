# CLAUDE.md — Investment Advisory Team

## Project Overview
A multi-agent investment research system built with the [Agno](https://github.com/agno-agi/agno) framework.
User provides a free-text investment theme; the system maps it to 1–3 sectors, screens 10 large-cap
companies, runs parallel fundamental + technical analysis on all 10, picks the top 3, and produces a
professional investment recommendation memo.

---

## Architecture

```
Topic Mapper      (LLM)       free-text theme → 1-3 FMP sector names
Company Discovery (hardcoded) sectors → 10 large-cap tickers from _SECTOR_TICKERS
Coordinator       (executor)  builds research brief for all 10
    │
    ├── [Parallel]
    │     ├── Fundamental Analyst  — get_free_cash_flow + YFinanceTools
    │     └── Technical Analyst    — compute_technical_signals + YFinanceTools
    │
Ranking           (executor)  regex score extraction → top 3 by composite score
Portfolio Strategist (agent)  investment memo for top 3
```

Pattern: **mapper → discovery → coordinator → specialists (parallel) → ranker → reviewer**

Data flows exclusively through `get_step_content()` chaining.
`additional_data` carries only the initial `{"topic": "..."}`.

---

## Key Files

| File | Purpose |
|------|---------|
| `investment_advisory.py` | Single-file implementation of the entire workflow |
| `.env` | API credentials (not committed) |
| `requirements.txt` | Python dependencies |
| `memo_<topic_slug>_<T1>_<T2>_<T3>.md` | Generated output memos |
| `trace_<topic_slug>.json` | Full observability trace |

---

## How to Run

```bash
# Install dependencies
pip install -r requirements.txt

# Set credentials in .env
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=google/gemini-2.5-flash-lite

# Run with any free-text investment theme
python investment_advisory.py --topic "AI and robotics"
python investment_advisory.py --topic "clean energy transition"
python investment_advisory.py --topic "US regional banking"
python investment_advisory.py --topic "luxury consumer brands"
# Default (no flag): "large-cap technology companies"
```

---

## Custom Tools

### `compute_technical_signals(ticker)` — Technical Analyst
pandas + yfinance computation (no LLM inference required):
- 50-day / 200-day SMA, golden/death cross detection
- RSI (14-period, Wilder EWM smoothing)
- 6-month trend (slope of 20-day MA)
- 20-day rate-of-change momentum
- 52-week and 20-day support/resistance
- Composite technical score 1–5

### `get_free_cash_flow(ticker)` — Fundamental Analyst
yfinance cashflow statement → OCF, CapEx, FCF for last 4 fiscal years.
Fills the gap left by YFinanceTools (no cash flow statement endpoint).

---

## Company Discovery

`_SECTOR_TICKERS` — hardcoded dict of 12 large-cap S&P 500 representatives per sector.
`fetch_companies_for_sectors(sectors)` — distributes 10 slots evenly across matched sectors.

**Why hardcoded:** FMP `/v3/stock-screener` and constituent list endpoints both return 403
on the free API plan. No external API call needed; always works reliably.

### FMP Sector Names (used by LLM prompt + _SECTOR_TICKERS keys)
`Technology` · `Healthcare` · `Energy` · `Financials` · `Consumer Cyclical` ·
`Consumer Defensive` · `Industrials` · `Basic Materials` · `Real Estate` ·
`Utilities` · `Communication Services`

Note: FMP uses `Consumer Cyclical` (not GICS "Consumer Discretionary"),
`Consumer Defensive` (not "Consumer Staples"), `Basic Materials` (not "Materials").

---

## Ranking Step

1. Regex-extracts `Fundamental Score: X/5` and `Technical Score: X/5` per ticker from agent output.
2. Composite = average of available scores.
3. If >50% of composites are missing → LLM fallback ranks all tickers by name.
4. Top 3 by composite score are passed to the Investment Memo step.

`_filter_content_to_tickers(content, tickers)` — trims 10-company analysis to top-3 only
before sending to Portfolio Strategist (keeps prompt size lean).

---

## Agno Workflow API — Key Learnings

### What works
- `Workflow(steps=[...])` — pass a **plain list**, not a `Steps(...)` wrapper (`Steps` is not iterable)
- `Parallel(Step(...), Step(...), name="...")` — steps are positional `*args`, not a `steps=` kwarg
- `Step(executor=fn)` where `fn(step_input: StepInput) -> StepOutput` — custom Python logic as a step
- `step_input.get_step_content("Step Name")` — retrieves output from a named previous step
- `step_input.get_step_content("Parallel Analysis")` returns a `dict` keyed by sub-step name
- `workflow.run(input=..., additional_data={"key": "val"})` — pass runtime data here, **not** on `Workflow()`
- `StepOutput(step_name=..., content=..., success=True)` — return value from executor functions
- `Agent(model=llm(), tools=[...])` — standard agent; `show_tool_calls` and `stream` are not valid kwargs

### What does NOT work
- `Workflow(additional_data=...)` — `additional_data` is a `run()` parameter, not a constructor parameter
- `Parallel(steps=[...])` — keyword arg `steps` is invalid; use positional args
- `Steps(Step(...), Step(...))` — `Steps` object is not iterable and cannot be passed as `workflow.steps`
- `Agent(show_tool_calls=True)` — not a valid `Agent.__init__` parameter in this version
- Module-level agent creation with `model=llm()` — `llm()` reads env vars at call time, so agents must
  be created inside a function (e.g. `build_workflow()`) called after `load_dotenv()`

### Env var loading pitfall
Module-level `OPENROUTER_API_KEY = os.environ.get(...)` is evaluated at import time, before `.env` is
loaded in some execution contexts. Always read env vars **lazily** (inside functions), and call
`load_dotenv()` at the top of the file before any imports that consume those vars.

### Step content chaining
`additional_data` is read-only and identical across all steps (set at `workflow.run()` time).
To pass data derived during the workflow (e.g. discovered tickers), write JSON to `StepOutput.content`
and read it in later steps via `step_input.get_step_content("Step Name")`.

---

## Bugs Fixed

1. **Silent sector fallback** — `DEFAULT_TICKERS.get(sector, DEFAULT_TICKERS["technology"])` silently
   used AAPL/MSFT/NVDA for any unknown sector key.
   Fixed by replacing with explicit error + all hyphenated sector keys.

2. **Module-level `llm()` call** — agents were created at import time, capturing an empty API key.
   Fixed by moving agent creation inside `build_workflow()`.

3. **`Parallel(steps=[...])`** — wrong kwarg; Agno uses `*args`. Fixed to positional syntax.

4. **`Workflow(additional_data=...)`** — not a constructor param. Moved to `workflow.run(...)`.

5. **FMP 403 on free plan** — `/v3/stock-screener` and constituent list endpoints require a paid FMP
   plan. Replaced with `_SECTOR_TICKERS` hardcoded fallback (no API call required).
