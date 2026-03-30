## CLAUDE.md — Investment Advisory Team

## Project Structure
* **`main.py`**: CLI entry point (`python main.py --topic "AI"`).
* **`config.py`**: `llm()` factory — OpenRouter/Gemini 2.5 Flash Lite, `max_tokens=8000`. Call lazily inside functions (not at module level) so `.env` is loaded first.
* **`data/sectors.py`**: `_SECTOR_TICKERS` — 12 S&P 500 tickers per sector. Hardcoded; do not replace with FMP API (returns 403).
* **`tools/`**: `fundamental.py` (`get_free_cash_flow`), `technical.py` (`compute_technical_signals`), `macro.py` (`get_fred_data` — FRED REST API, retries on 5xx).
* **`workflow/`**: `agents.py` (agent factories), `steps.py` (all executor functions), `pipeline.py` (`build_workflow`).

## Pipeline Order
```
Topic Mapper → Company Discovery → Coordinator → Macro Analysis
    → Parallel(Fundamental Analyst, Technical Analyst) → Ranking → Investment Memo
```
Order matters: Macro Analysis must run before Parallel Analysis so analysts can reference the macro report.

## Agno API Rules

| Rule | Detail |
| :--- | :--- |
| **Workflow init** | `Workflow(steps=[...])` plain list. No `Steps()` wrapper. |
| **Parallelism** | `Parallel(Step(...), Step(...), name="...")` — positional args only. |
| **`agent=` input** | `Step(agent=...)` always sends `previous_step_outputs[-1]` as the user message. Use `executor=` whenever a step needs content from non-adjacent steps. |
| **`executor=` preferred** | All analyst steps use `executor=`. Executor receives full `StepInput`; call `step_input.get_step_content("Step Name")` to read any prior step. |
| **Data access** | `step_input.get_step_content("Step Name")` — works across nested steps recursively. |
| **Runtime data** | Pass `additional_data` in `workflow.run()`, not in constructor. |
| **Env vars** | `load_dotenv()` before all imports. No module-level `os.getenv` for LLM config. |

## Model Constraints (gemini-2.5-flash-lite via OpenRouter)
* **No tools on Macro Analyst** — model returns 0-char responses when given tools. Pre-fetch FRED data in the executor and pass as text.
* **One ticker per analyst call** — model refuses multi-ticker tool-calling requests. Both analyst executors loop one ticker at a time and concatenate results.
* **Guard `response.content`** — can be `None` even on a non-None response object. Always use `(response.content if response and response.content else None) or fallback`.

## FRED Data
Series fetched: `FEDFUNDS` (12), `T10Y2Y` (12), `CPIAUCSL` (13), `GDPC1` (8). `get_fred_data` retries up to 3× with exponential backoff (2s, 4s) on 5xx errors; returns error string on 4xx without retrying.

## Analysis Specs
* **RSI**: 14-period Wilder EWM. **FCF**: OCF − CapEx, last 4 fiscal years via `yfinance`.
* Final Memo calls `_filter_content_to_tickers(content, top3)` to trim context to top-3 only.
