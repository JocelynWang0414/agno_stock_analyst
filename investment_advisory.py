"""
Investment Advisory Team — Agno Multi-Agent Workflow
=====================================================
Architecture (planner → specialists → reviewer):

  Topic Mapper    — LLM maps free-text theme → 1-3 GICS sectors
  Company Discovery — FMP screener fetches top 10 large-cap US stocks
  Coordinator     — builds research brief for all 10
      │
      ├── [Parallel]
      │     ├── Fundamental Analyst  (specialist agent 1)
      │     └── Technical Analyst    (specialist agent 2)
      │
  Ranking         — scores all 10, picks top 3
  Portfolio Strategist — investment memo for top 3

Run:
  python investment_advisory.py --topic "AI and robotics"
  python investment_advisory.py --topic "clean energy transition"
  python investment_advisory.py --topic "US regional banking"
"""

import argparse
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from textwrap import dedent

import pandas as pd
import yfinance as yf

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.tools.yfinance import YFinanceTools
from agno.workflow import Workflow, Step, Parallel, StepInput, StepOutput


# ---------------------------------------------------------------------------
# ANSI colour helpers (gracefully degrade when terminal doesn't support them)
# ---------------------------------------------------------------------------
_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_CYAN   = "\033[36m"
_GREEN  = "\033[32m"
_YELLOW = "\033[33m"
_BLUE   = "\033[34m"
_MAGENTA= "\033[35m"
_WHITE  = "\033[97m"

_EVENT_COLOURS = {
    "WORKFLOW_START":    _CYAN   + _BOLD,
    "WORKFLOW_END":      _CYAN   + _BOLD,
    "STEP_START":        _BLUE   + _BOLD,
    "STEP_END":          _BLUE,
    "PARALLEL_START":    _MAGENTA+ _BOLD,
    "PARALLEL_END":      _MAGENTA,
    "AGENT_INPUT":       _YELLOW + _BOLD,
    "AGENT_OUTPUT":      _GREEN  + _BOLD,
    "AGENT_TOOL_CALL":   _WHITE  + _DIM,
    "DATA_FLOW":         _DIM,
}


# ---------------------------------------------------------------------------
# WorkflowTracer — core observability class
# ---------------------------------------------------------------------------
class WorkflowTracer:
    """
    Records every significant event in the multi-agent workflow:
      - step starts/ends with their inputs and outputs
      - agent prompt inputs and response outputs
      - data flowing between steps
      - wall-clock timestamps and elapsed time
    """

    def __init__(self):
        self.events: list[dict] = []
        self._start_ns: int = 0
        self._seq: int = 0

    # ------------------------------------------------------------------
    # Public recording API
    # ------------------------------------------------------------------

    def start_workflow(self, topic: str):
        self._start_ns = time.perf_counter_ns()
        self._emit("WORKFLOW_START", "Workflow",
                   summary=f"Starting analysis: '{topic}'",
                   detail={"topic": topic})

    def end_workflow(self, topic: str, top3: list[str] = None):
        self._emit("WORKFLOW_END", "Workflow",
                   summary=f"Workflow complete — topic='{topic}' top3={top3 or []}",
                   detail={"topic": topic, "top3": top3 or []})

    def step_start(self, step_name: str, input_data: object = None):
        self._emit("STEP_START", step_name,
                   summary=f"Step starting",
                   detail={"input": self._truncate(input_data)})

    def step_end(self, step_name: str, output_data: object = None, success: bool = True):
        self._emit("STEP_END", step_name,
                   summary=f"Step finished (success={success})",
                   detail={"output": self._truncate(output_data), "success": success})

    def parallel_start(self, parallel_name: str, sub_steps: list[str]):
        self._emit("PARALLEL_START", parallel_name,
                   summary=f"Parallel execution starting → [{', '.join(sub_steps)}]",
                   detail={"sub_steps": sub_steps})

    def parallel_end(self, parallel_name: str, sub_steps: list[str]):
        self._emit("PARALLEL_END", parallel_name,
                   summary=f"Parallel execution complete ← [{', '.join(sub_steps)}]",
                   detail={"sub_steps": sub_steps})

    def agent_input(self, agent_name: str, prompt: str):
        self._emit("AGENT_INPUT", agent_name,
                   summary=f"Received prompt ({len(prompt)} chars)",
                   detail={"prompt": self._truncate(prompt, max_chars=2000)})

    def agent_output(self, agent_name: str, response: str):
        self._emit("AGENT_OUTPUT", agent_name,
                   summary=f"Produced response ({len(response)} chars)",
                   detail={"response": self._truncate(response, max_chars=2000)})

    def data_flow(self, from_step: str, to_step: str, data_summary: str):
        self._emit("DATA_FLOW", f"{from_step} → {to_step}",
                   summary=data_summary,
                   detail={"from": from_step, "to": to_step, "description": data_summary})

    # ------------------------------------------------------------------
    # Agent wrapper — patches agent.run() to log input/output
    # ------------------------------------------------------------------

    def wrap_agent(self, agent: Agent) -> Agent:
        """Monkey-patch agent.run so every call is transparently logged.

        Agno may call agent.run() with a positional string (direct call in
        synthesis_step), or with keyword arguments / no positional message
        (when driven internally from a Step(agent=...)). Accept both forms.
        """
        original_run = agent.run
        tracer = self

        def traced_run(*args, **kwargs):
            # Extract the message for logging without altering the call signature
            message = (
                args[0] if args
                else kwargs.get("message", kwargs.get("input", "<no message>"))
            )
            tracer.agent_input(agent.name, str(message))
            result = original_run(*args, **kwargs)
            content = result.content if (result and hasattr(result, "content")) else str(result)
            tracer.agent_output(agent.name, content or "")
            return result

        agent.run = traced_run
        return agent

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def print_timeline(self):
        """Print a compact, colour-coded chronological event log."""
        width = 80
        print(f"\n{'═'*width}")
        print(f"  OBSERVABILITY TRACE — {len(self.events)} events")
        print(f"{'═'*width}")
        print(f"  {'#':<4}  {'T+s':<8}  {'Actor':<28}  {'Event':<18}  Summary")
        print(f"  {'─'*4}  {'─'*8}  {'─'*28}  {'─'*18}  {'─'*30}")
        for ev in self.events:
            colour = _EVENT_COLOURS.get(ev["type"], "")
            actor = ev["actor"][:27]
            etype = ev["type"][:17]
            summary = ev["summary"][:55]
            elapsed = f"{ev['elapsed_s']:>7.2f}s"
            print(f"  {colour}{ev['seq']:<4}  {elapsed}  {actor:<28}  {etype:<18}  {summary}{_RESET}")
        print(f"{'═'*width}")

    def print_data_flows(self):
        """Print a summary of all inter-step data handoffs."""
        flows = [e for e in self.events if e["type"] == "DATA_FLOW"]
        if not flows:
            return
        print(f"\n{'─'*60}")
        print("  DATA FLOW SUMMARY")
        print(f"{'─'*60}")
        for ev in flows:
            d = ev["detail"]
            print(f"  [{ev['elapsed_s']:>6.2f}s]  {d['from']:>25}  →  {d['to']}")
            print(f"           {_DIM}{d['description']}{_RESET}")

    def save_trace(self, path: str):
        """Persist the full trace as a JSON file for offline inspection."""
        with open(path, "w") as f:
            json.dump(
                {
                    "generated_at": datetime.now().isoformat(),
                    "total_events": len(self.events),
                    "events": self.events,
                },
                f,
                indent=2,
                default=str,
            )
        print(f"\n  Trace saved → {path}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _emit(self, event_type: str, actor: str, summary: str, detail: dict):
        self._seq += 1
        elapsed_s = (time.perf_counter_ns() - self._start_ns) / 1e9 if self._start_ns else 0.0
        ev = {
            "seq":       self._seq,
            "timestamp": datetime.now().isoformat(timespec="milliseconds"),
            "elapsed_s": round(elapsed_s, 3),
            "type":      event_type,
            "actor":     actor,
            "summary":   summary,
            "detail":    detail,
        }
        self.events.append(ev)
        self._print_live(ev)

    def _print_live(self, ev: dict):
        colour = _EVENT_COLOURS.get(ev["type"], "")
        indent = "    " if ev["type"] in ("AGENT_INPUT", "AGENT_OUTPUT", "AGENT_TOOL_CALL", "DATA_FLOW") else ""
        print(f"{colour}{indent}[{ev['elapsed_s']:>7.2f}s] [{ev['actor']}] {ev['summary']}{_RESET}")

    @staticmethod
    def _truncate(obj: object, max_chars: int = 500) -> str:
        s = str(obj) if obj is not None else ""
        if len(s) > max_chars:
            return s[:max_chars] + f"… [truncated {len(s)-max_chars} chars]"
        return s


# ---------------------------------------------------------------------------
# Fundamental analysis tool — free cash flow from yfinance cash flow statement
# ---------------------------------------------------------------------------
def get_free_cash_flow(ticker: str) -> str:
    """
    Fetch the last 4 annual periods of Free Cash Flow (Operating Cash Flow minus
    Capital Expenditures) from yfinance cash flow statements.
    Returns a formatted string for the Fundamental Analyst agent.
    """
    tk = yf.Ticker(ticker)
    cf = tk.cashflow  # columns = fiscal year end dates, rows = line items

    if cf is None or cf.empty:
        return f"[{ticker}] Cash flow statement not available."

    # Normalise index to lowercase for robust lookup
    cf.index = cf.index.str.lower().str.replace(" ", "_")

    ocf_keys  = ["operating_cash_flow", "total_cash_from_operating_activities",
                 "cash_from_operations", "net_cash_provided_by_operating_activities"]
    capex_keys = ["capital_expenditures", "capital_expenditure",
                  "purchase_of_plant,_property_&_equipment",
                  "purchase_of_ppe", "capex"]

    def find_row(keys):
        for k in keys:
            if k in cf.index:
                return cf.loc[k]
        return None

    ocf_row   = find_row(ocf_keys)
    capex_row = find_row(capex_keys)

    if ocf_row is None:
        return f"[{ticker}] Operating cash flow line not found in cash flow statement."

    lines = [f"FREE CASH FLOW — {ticker}", "─" * 40]
    for col in cf.columns[:4]:                     # last 4 fiscal years
        year  = str(col)[:10]
        ocf   = ocf_row.get(col, None)
        capex = capex_row.get(col, None) if capex_row is not None else None

        if pd.isna(ocf):
            lines.append(f"{year}: OCF=N/A")
            continue

        ocf_val = float(ocf)
        if capex is not None and not pd.isna(capex):
            capex_val = float(capex)
            # yfinance reports capex as negative; normalise to negative spend
            if capex_val > 0:
                capex_val = -capex_val
            fcf = ocf_val + capex_val          # OCF - |capex|
            lines.append(
                f"{year}: OCF=${ocf_val/1e9:.2f}B  CapEx=${capex_val/1e9:.2f}B  "
                f"FCF=${fcf/1e9:.2f}B"
            )
        else:
            lines.append(f"{year}: OCF=${ocf_val/1e9:.2f}B  CapEx=N/A  FCF=N/A")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Technical analysis tool — pandas-computed signals for the Technical Analyst
# ---------------------------------------------------------------------------
def compute_technical_signals(ticker: str) -> str:
    """
    Download up to 1 year of daily OHLCV data for *ticker* and compute:
      - 50-day and 200-day simple moving averages
      - Golden / death cross (MA50 vs MA200 crossover in last 20 sessions)
      - RSI (14-period, Wilder smoothing via EWM)
      - 6-month trend classification: Uptrend / Downtrend / Sideways
      - 20-day rate-of-change (momentum)
      - 52-week and 20-day support / resistance levels
      - A composite technical score 1–5

    Returns a plain-text summary ready for the Technical Analyst agent.
    """
    raw = yf.download(ticker, period="1y", auto_adjust=True, progress=False)

    # yfinance can return multi-level columns when given a single ticker
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)

    if raw.empty or len(raw) < 21:
        return f"[{ticker}] Insufficient price history to compute signals."

    close  = raw["Close"].astype(float)
    high   = raw["High"].astype(float)
    low    = raw["Low"].astype(float)

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
    cur_rsi = float(rsi_series.iloc[-1])

    if cur_rsi >= 70:
        rsi_label = f"Overbought ({cur_rsi:.1f})"
    elif cur_rsi <= 30:
        rsi_label = f"Oversold ({cur_rsi:.1f})"
    else:
        rsi_label = f"Neutral ({cur_rsi:.1f})"

    # ── Trend (6-month window, slope of 20-day MA) ───────────────────────────
    six_mo = close.iloc[-126:]
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
        roc_20 = (current_price - float(close.iloc[-21])) / float(close.iloc[-21]) * 100
        momentum_str = f"{roc_20:+.2f}% (20-day ROC)"
    else:
        roc_20 = 0.0
        momentum_str = "N/A"

    # ── Support / resistance ─────────────────────────────────────────────────
    high_52w = float(high.max())
    low_52w  = float(low.min())
    recent_high = float(high.iloc[-20:].max())
    recent_low  = float(low.iloc[-20:].min())

    # ── Composite technical score (1–5) ──────────────────────────────────────
    pts = 0

    # Price vs MA200: +2 well above, +1 slightly above, -1 slightly below, -2 well below
    if cur_ma200:
        d = (current_price - cur_ma200) / cur_ma200 * 100
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


# ---------------------------------------------------------------------------
# FMP sector names (must match FMP screener API vocabulary exactly)
# ---------------------------------------------------------------------------
FMP_SECTORS = [
    "Technology",
    "Healthcare",
    "Energy",
    "Financials",
    "Consumer Cyclical",       # GICS: Consumer Discretionary
    "Consumer Defensive",      # GICS: Consumer Staples
    "Industrials",
    "Basic Materials",         # GICS: Materials
    "Real Estate",
    "Utilities",
    "Communication Services",
]


# ---------------------------------------------------------------------------
# Company discovery — curated large-cap tickers per sector (no API required)
# ---------------------------------------------------------------------------
# Top ~12 large-cap S&P 500 representatives per FMP sector, ordered roughly
# by market cap. Used as the primary source for company discovery.
_SECTOR_TICKERS: dict[str, list[str]] = {
    "Technology":             ["AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CSCO", "AMD", "INTC", "TXN", "QCOM", "IBM", "HPQ"],
    "Healthcare":             ["LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ABT", "DHR", "BMY", "AMGN", "PFE", "GILD"],
    "Energy":                 ["XOM", "CVX", "COP", "EOG", "SLB", "MPC", "VLO", "PSX", "OXY", "HES", "DVN", "BKR"],
    "Financials":             ["JPM", "BAC", "WFC", "GS", "MS", "BLK", "C", "AXP", "SCHW", "USB", "PNC", "TFC"],
    "Consumer Cyclical":      ["AMZN", "TSLA", "HD", "MCD", "NKE", "SBUX", "LOW", "TGT", "BKNG", "GM", "F", "MAR"],
    "Consumer Defensive":     ["PG", "KO", "PEP", "WMT", "COST", "PM", "MO", "CL", "KMB", "GIS", "SYY", "HSY"],
    "Industrials":            ["HON", "CAT", "GE", "RTX", "LMT", "DE", "UNP", "UPS", "BA", "EMR", "ETN", "MMM"],
    "Basic Materials":        ["LIN", "APD", "ECL", "SHW", "NUE", "FCX", "NEM", "VMC", "MLM", "CF", "MOS", "ALB"],
    "Real Estate":            ["PLD", "AMT", "EQIX", "CCI", "SPG", "WELL", "DLR", "O", "PSA", "EXR", "AVB", "VTR"],
    "Utilities":              ["NEE", "DUK", "SO", "D", "SRE", "EXC", "XEL", "WEC", "PPL", "AEP", "ES", "ETR"],
    "Communication Services": ["GOOGL", "META", "NFLX", "DIS", "CMCSA", "VZ", "T", "CHTR", "TMUS", "EA", "WBD", "OMC"],
}


def fetch_companies_for_sectors(sectors: list[str]) -> list[str]:
    """
    Return up to 10 tickers for the given sectors using the curated fallback map.
    Distributes slots evenly across sectors; ties broken by order in _SECTOR_TICKERS.
    """
    if not sectors:
        return []

    per_sector = max(1, 10 // len(sectors))
    extra      = 10 - per_sector * len(sectors)   # distribute leftover slots to first sectors
    result: list[str] = []
    seen: set[str] = set()

    for i, sector in enumerate(sectors):
        pool  = _SECTOR_TICKERS.get(sector, [])
        slots = per_sector + (1 if i < extra else 0)
        for ticker in pool:
            if ticker not in seen and len(result) < 10:
                result.append(ticker)
                seen.add(ticker)
            if len([t for t in result if t in pool]) >= slots:
                break

    return result[:10]


# ---------------------------------------------------------------------------
# Ranking helpers
# ---------------------------------------------------------------------------
def _parse_sectors_from_llm(text: str) -> list[str]:
    """Extract and validate sectors list from LLM JSON response."""
    match = re.search(r'\{[^{}]*"sectors"\s*:\s*\[[^\]]*\][^{}]*\}', text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            sectors = data.get("sectors", [])
            valid = [s for s in sectors if s in FMP_SECTORS]
            return valid[:3] if valid else []
        except (json.JSONDecodeError, TypeError):
            pass
    return []


def _extract_scores(
    tickers: list[str], fund_content: str, tech_content: str
) -> dict[str, dict]:
    """
    Regex-extract Fundamental Score and Technical Score per ticker.
    Returns {ticker: {"fund": float|None, "tech": float|None, "composite": float|None}}.
    """
    scores: dict[str, dict] = {}
    for ticker in tickers:
        fund_match = re.search(
            rf"##\s+{re.escape(ticker)}.*?Fundamental Score:\s*(\d(?:\.\d)?)\s*/\s*5",
            fund_content, re.DOTALL | re.IGNORECASE,
        )
        tech_match = re.search(
            rf"##\s+{re.escape(ticker)}.*?Technical Score:\s*(\d(?:\.\d)?)\s*/\s*5",
            tech_content, re.DOTALL | re.IGNORECASE,
        )
        fund_score = float(fund_match.group(1)) if fund_match else None
        tech_score = float(tech_match.group(1)) if tech_match else None

        if fund_score is not None and tech_score is not None:
            composite = (fund_score + tech_score) / 2
        elif fund_score is not None:
            composite = fund_score
        elif tech_score is not None:
            composite = tech_score
        else:
            composite = None

        scores[ticker] = {"fund": fund_score, "tech": tech_score, "composite": composite}
    return scores


def _llm_rank_fallback(
    tickers: list[str],
    fund_content: str,
    tech_content: str,
    partial_scores: dict,
) -> dict:
    """
    Ask the LLM to rank tickers when regex extraction fails for most.
    Fills None composite values with synthetic rank-based scores.
    """
    ranker = Agent(model=llm(), description="Stock ranker")
    prompt = (
        f"Given this fundamental and technical analysis for {', '.join(tickers)}, "
        f"rank all tickers from best to worst investment opportunity.\n\n"
        f"FUNDAMENTAL (excerpt):\n{fund_content[:3000]}\n\n"
        f"TECHNICAL (excerpt):\n{tech_content[:3000]}\n\n"
        f'Respond ONLY with JSON: {{"ranked": ["TICK1", "TICK2", ...]}}'
    )
    try:
        response = ranker.run(prompt)
        content = response.content if response else ""
        match = re.search(r'\{[^{}]*"ranked"\s*:\s*\[[^\]]*\][^{}]*\}', content, re.DOTALL)
        if match:
            ranked_list = json.loads(match.group()).get("ranked", [])
            n = len(ranked_list)
            for i, ticker in enumerate(ranked_list):
                if ticker in partial_scores and partial_scores[ticker]["composite"] is None:
                    partial_scores[ticker]["composite"] = 5.0 - (i / max(n - 1, 1)) * 4.0
    except Exception as e:
        print(f"  [Ranking fallback] LLM ranking failed — {e}")
    return partial_scores


def _filter_content_to_tickers(content: str, tickers: list[str]) -> str:
    """Extract only the ## TICKER sections for the specified tickers."""
    if not tickers or not content:
        return content
    sections = []
    for ticker in tickers:
        pattern = rf"(##\s+{re.escape(ticker)}[^\n]*\n.*?)(?=\n##\s+[A-Z]|\Z)"
        match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
        sections.append(match.group(1).strip() if match else f"## {ticker}\n[Analysis not available]")
    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Shared model factory — OpenRouter via OpenAI-compatible API
# ---------------------------------------------------------------------------
def llm() -> OpenAIChat:
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    model   = os.environ.get("OPENROUTER_MODEL", "google/gemini-2.5-flash-lite")
    return OpenAIChat(
        id=model,
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
    )


# ---------------------------------------------------------------------------
# Step 1: Topic Mapper — LLM maps free-text theme → 1-3 FMP sector names
# ---------------------------------------------------------------------------
def make_topic_mapper_step(tracer: WorkflowTracer):
    def topic_mapper_step(step_input: StepInput) -> StepOutput:
        data  = step_input.additional_data or {}
        topic = data.get("topic", "large-cap technology companies")

        tracer.step_start("Topic Mapper", input_data={"topic": topic})

        prompt = (
            f"You are a financial research assistant. Map the following investment theme "
            f"to 1-3 of the most relevant sectors from this exact list:\n"
            f"{json.dumps(FMP_SECTORS, indent=2)}\n\n"
            f'Investment theme: "{topic}"\n\n'
            f"Respond ONLY with a JSON object, no other text:\n"
            f'{{"sectors": ["Sector1", "Sector2"], "rationale": "one sentence"}}'
        )
        mapper = Agent(model=llm(), description="Sector mapper")
        response = mapper.run(prompt)
        content = response.content if response else ""

        sectors = _parse_sectors_from_llm(content)
        if not sectors:
            print(f"  [Topic Mapper] Could not parse sectors from LLM; defaulting to Technology")
            sectors = ["Technology"]

        tracer.data_flow("Workflow Input", "Topic Mapper", f"topic='{topic}'")
        tracer.data_flow("Topic Mapper", "Company Discovery", f"sectors={sectors}")
        tracer.step_end("Topic Mapper", output_data={"sectors": sectors}, success=True)
        return StepOutput(
            step_name="Topic Mapper",
            content=json.dumps({"sectors": sectors, "topic": topic}),
            success=True,
        )

    return topic_mapper_step


# ---------------------------------------------------------------------------
# Step 2: Company Discovery — FMP screener → top 10 tickers
# ---------------------------------------------------------------------------
def make_company_discovery_step(tracer: WorkflowTracer):
    def company_discovery_step(step_input: StepInput) -> StepOutput:
        mapper_raw = step_input.get_step_content("Topic Mapper") or "{}"
        try:
            mapper_data = json.loads(mapper_raw)
        except json.JSONDecodeError:
            mapper_data = {}

        sectors = mapper_data.get("sectors", ["Technology"])
        topic   = mapper_data.get("topic", "")

        tracer.step_start("Company Discovery", input_data={"sectors": sectors})

        tickers = fetch_companies_for_sectors(sectors)

        print(f"  [Company Discovery] {len(tickers)} companies for {sectors}: {', '.join(tickers)}")

        sector_label = " / ".join(sectors)
        result = {
            "tickers": tickers,
            "sectors": sectors,
            "sector_label": sector_label,
            "topic": topic,
        }
        tracer.data_flow("Company Discovery", "Coordinator", f"{len(tickers)} tickers: {tickers}")
        tracer.step_end("Company Discovery", output_data=result, success=bool(tickers))
        return StepOutput(
            step_name="Company Discovery",
            content=json.dumps(result),
            success=bool(tickers),
        )

    return company_discovery_step


# ---------------------------------------------------------------------------
# Step 3: Coordinator — builds research brief from discovered tickers
# ---------------------------------------------------------------------------
def make_coordinator_step(tracer: WorkflowTracer):
    def coordinator_step(step_input: StepInput) -> StepOutput:
        discovery_raw = step_input.get_step_content("Company Discovery") or "{}"
        try:
            discovery_data = json.loads(discovery_raw)
        except json.JSONDecodeError:
            discovery_data = {}

        tickers      = discovery_data.get("tickers", [])
        sector_label = discovery_data.get("sector_label", "Multiple Sectors")
        topic        = discovery_data.get("topic", "")
        ticker_str   = ", ".join(tickers)

        if not tickers:
            raise RuntimeError(
                "Company Discovery returned no tickers. "
                "Check FMP_API_KEY and try a different topic."
            )

        tracer.step_start("Coordinator", input_data={
            "sector_label": sector_label, "tickers": tickers,
        })

        prompt = (
            f"Investment Theme: {topic}\n"
            f"Sectors: {sector_label}\n"
            f"Companies to analyse: {ticker_str}\n\n"
            f"Please analyse each of the following tickers: {ticker_str}"
        )

        tracer.data_flow(
            from_step="Coordinator",
            to_step="Parallel Analysis",
            data_summary=f"Research brief: {len(prompt)} chars — {len(tickers)} tickers",
        )
        tracer.step_end("Coordinator", output_data=prompt, success=True)
        return StepOutput(step_name="Coordinator", content=prompt, success=True)

    return coordinator_step


# ---------------------------------------------------------------------------
# Step 5: Ranking — extract scores, pick top 3
# ---------------------------------------------------------------------------
def make_ranking_step(tracer: WorkflowTracer):
    def ranking_step(step_input: StepInput) -> StepOutput:
        tracer.step_start("Ranking", input_data="Parsing scores from parallel analysis…")

        parallel_content = step_input.get_step_content("Parallel Analysis")
        if isinstance(parallel_content, dict):
            fund_content = str(parallel_content.get("Fundamental Analyst", ""))
            tech_content = str(parallel_content.get("Technical Analyst", ""))
        else:
            fund_content = tech_content = ""

        discovery_raw = step_input.get_step_content("Company Discovery") or "{}"
        try:
            discovery_data = json.loads(discovery_raw)
        except json.JSONDecodeError:
            discovery_data = {}

        tickers      = discovery_data.get("tickers", [])
        sector_label = discovery_data.get("sector_label", "")
        topic        = discovery_data.get("topic", "")

        scores = _extract_scores(tickers, fund_content, tech_content)

        missing = sum(1 for t in tickers if scores.get(t, {}).get("composite") is None)
        if missing > len(tickers) // 2:
            print(f"  [Ranking] Score extraction failed for {missing}/{len(tickers)} tickers — using LLM fallback")
            scores = _llm_rank_fallback(tickers, fund_content, tech_content, scores)

        ranked = sorted(
            tickers,
            key=lambda t: scores.get(t, {}).get("composite") or 0,
            reverse=True,
        )
        top3 = ranked[:3]

        print(f"  [Ranking] All scores: { {t: scores[t]['composite'] for t in tickers} }")
        print(f"  [Ranking] Top 3 selected: {top3}")

        result = {
            "top3": top3,
            "all_ranked": ranked,
            "scores": scores,
            "sector_label": sector_label,
            "topic": topic,
        }
        tracer.data_flow("Parallel Analysis", "Ranking", f"Scored {len(scores)} tickers, top3={top3}")
        tracer.step_end("Ranking", output_data=f"top3={top3}", success=True)
        return StepOutput(step_name="Ranking", content=json.dumps(result), success=True)

    return ranking_step


# ---------------------------------------------------------------------------
# Build the workflow — agents created here so llm() is called at runtime
# ---------------------------------------------------------------------------
def build_workflow(tracer: WorkflowTracer) -> Workflow:

    # Specialist Agent 1 — Fundamental Analyst
    fundamental_analyst = tracer.wrap_agent(Agent(
        name="Fundamental Analyst",
        model=llm(),
        tools=[
            get_free_cash_flow,                  # FCF from cash flow statement
            YFinanceTools(
                enable_stock_fundamentals=True,   # P/E, P/B, EPS, beta, market cap
                enable_income_statements=True,    # revenue, net income, earnings trend
                enable_key_financial_ratios=True, # debt/equity, ROE, margins
                enable_analyst_recommendations=True,
                enable_company_info=True,         # revenue growth, EBITDA, sector
                enable_company_news=True,
            ),
        ],
        description="You are a buy-side fundamental analyst at a top investment bank.",
        instructions=dedent("""\
            For EACH ticker in the request:
            1. Retrieve key fundamentals: revenue growth (company_info), EPS and P/E and P/B
               (stock_fundamentals), debt/equity and ROE (key_financial_ratios), and income
               trend (income_statements).
            2. Call `get_free_cash_flow(ticker)` to retrieve operating cash flow, capex, and
               FCF for the last 4 fiscal years. Include FCF trend in your analysis.
            3. Summarise the latest earnings trend and Wall Street analyst consensus
               (analyst_recommendations).
            4. Identify the top 2-3 business risks and near-term catalysts, drawing on
               company_news and the financial data above.
            5. Assign a fundamental score 1-5 (1=very bearish, 5=very bullish) with a
               one-sentence rationale.

            Format each company as:
            ## [TICKER] — Fundamental Analysis
            **Fundamental Score: X/5**
            - [key metric bullets]
            **Free Cash Flow:** ...
            **Risks:** ...
            **Catalysts:** ...
        """),
        markdown=True,
    ))

    # Specialist Agent 2 — Technical Analyst
    technical_analyst = tracer.wrap_agent(Agent(
        name="Technical Analyst",
        model=llm(),
        tools=[
            compute_technical_signals,          # pandas-computed signals (primary)
            YFinanceTools(enable_stock_price=True),  # live quote cross-check
        ],
        description="You are a quantitative technical analyst specialising in equities.",
        instructions=dedent("""\
            For EACH ticker in the request:
            1. Call `compute_technical_signals(ticker)` to get pre-computed indicators:
               RSI, 50-day MA, 200-day MA, golden/death cross, 6-month trend,
               20-day momentum, and near-term support/resistance levels.
            2. Interpret the computed trend and momentum signals to classify the
               primary direction (Uptrend / Downtrend / Sideways).
            3. Report the key support level (20-day low) and resistance level
               (20-day high / 52-week high). Note any notable pattern implied by
               price position relative to MAs.
            4. Summarise RSI condition (overbought / oversold / neutral),
               MA alignment, and whether a golden or death cross is present.
            5. Use the suggested technical score from the tool as your baseline,
               adjust by ±1 only if other signals strongly warrant it, and provide
               a one-sentence rationale.

            Format each company as:
            ## [TICKER] — Technical Analysis
            **Technical Score: X/5**
            - [key signal bullets]
            **Support/Resistance:** ...
            **Signal:** ...
        """),
        markdown=True,
    ))

    # Output Agent — Portfolio Strategist
    portfolio_strategist = tracer.wrap_agent(Agent(
        name="Portfolio Strategist",
        model=llm(),
        description="You are a senior portfolio strategist who synthesises research into investment memos.",
        instructions=dedent("""\
            You receive fundamental and technical analysis for three companies in the same sector.
            Produce a professional **Investment Recommendation Memo** with these sections:

            1. **Executive Summary** (3-4 sentences): sector outlook and top takeaway.
            2. **Company Scorecard** — markdown table:
                | Ticker | Fund. Score | Tech. Score | Composite | Recommendation |
                (Composite = average; Recommendation: Strong Buy / Buy / Hold / Sell / Strong Sell)
            3. **Ranked Recommendations** (Rank 1 = best opportunity):
                For each rank: ticker, composite score, 3-5 bullet rationale.
            4. **Key Risks to the Thesis** — sector-level AND stock-specific.
            5. **Suggested Position Sizing**: overweight / market-weight / underweight with reasoning.
            6. **Disclaimer**: standard investment disclaimer.

            Use markdown. Be concise and data-driven.
        """),
        markdown=True,
    ))

    tracer_ref = tracer

    def make_synthesis_step():
        def synthesis_step(step_input: StepInput) -> StepOutput:
            # Read top-3 and context from Ranking step
            ranking_raw = step_input.get_step_content("Ranking") or "{}"
            try:
                ranking_data = json.loads(ranking_raw)
            except json.JSONDecodeError:
                ranking_data = {}

            top3         = ranking_data.get("top3", [])
            scores       = ranking_data.get("scores", {})
            sector_label = ranking_data.get("sector_label", "Multiple Sectors")
            topic        = ranking_data.get("topic", "")
            ticker_str   = ", ".join(top3)

            # Get parallel analysis, filter to top-3 only
            parallel_content = step_input.get_step_content("Parallel Analysis")
            if isinstance(parallel_content, dict):
                fund_all = str(parallel_content.get("Fundamental Analyst", ""))
                tech_all = str(parallel_content.get("Technical Analyst", ""))
            else:
                fund_all = tech_all = ""

            fund_content = _filter_content_to_tickers(fund_all, top3) or fund_all
            tech_content = _filter_content_to_tickers(tech_all, top3) or tech_all

            # Build composite scorecard lines for prompt context
            scorecard_lines = []
            for t in top3:
                s = scores.get(t, {})
                comp = s.get("composite")
                comp_str = f"{comp:.2f}" if isinstance(comp, float) else "N/A"
                scorecard_lines.append(
                    f"  {t}: Fund={s.get('fund','?')}/5  Tech={s.get('tech','?')}/5  Composite={comp_str}"
                )

            tracer_ref.data_flow(
                from_step="Ranking",
                to_step="Investment Memo",
                data_summary=f"top3={top3}, fund={len(fund_content)} chars, tech={len(tech_content)} chars",
            )

            synthesis_prompt = dedent(f"""\
                Investment Theme: **{topic}**
                Sectors Covered: {sector_label}
                Top 3 Companies (selected from 10 screened): {ticker_str}

                COMPOSITE SCORES:
                {chr(10).join(scorecard_lines)}

                ---
                ### FUNDAMENTAL ANALYSIS REPORTS (Top 3)
                {fund_content}

                ---
                ### TECHNICAL ANALYSIS REPORTS (Top 3)
                {tech_content}

                ---
                Please produce the full Investment Recommendation Memo as instructed.
            """)

            tracer_ref.step_start("Investment Memo", input_data={
                "topic": topic, "top3": top3,
                "fundamental_chars": len(fund_content),
                "technical_chars": len(tech_content),
            })

            response = portfolio_strategist.run(synthesis_prompt)
            memo = response.content if response else "Memo generation failed."

            tracer_ref.step_end("Investment Memo", output_data=f"{len(memo)} chars", success=bool(memo))
            return StepOutput(step_name="Investment Memo", content=memo, success=True)

        return synthesis_step

    workflow = Workflow(
        name="Investment Advisory Team",
        description=(
            "Maps a free-text investment theme to sectors, screens 10 companies, "
            "runs parallel fundamental + technical analysis, ranks all 10, "
            "and produces an investment memo for the top 3."
        ),
        steps=[
            Step(
                name="Topic Mapper",
                executor=make_topic_mapper_step(tracer),
                description="Map free-text investment theme to 1-3 GICS sectors.",
            ),
            Step(
                name="Company Discovery",
                executor=make_company_discovery_step(tracer),
                description="Fetch top 10 large-cap US companies via FMP screener.",
            ),
            Step(
                name="Coordinator",
                executor=make_coordinator_step(tracer),
                description="Build research brief for all discovered tickers.",
            ),
            Parallel(
                Step(
                    name="Fundamental Analyst",
                    agent=fundamental_analyst,
                    description="Run fundamental analysis on all tickers.",
                ),
                Step(
                    name="Technical Analyst",
                    agent=technical_analyst,
                    description="Run technical analysis on all tickers.",
                ),
                name="Parallel Analysis",
            ),
            Step(
                name="Ranking",
                executor=make_ranking_step(tracer),
                description="Score all tickers and select top 3 by composite score.",
            ),
            Step(
                name="Investment Memo",
                executor=make_synthesis_step(),
                description="Synthesise top-3 analysis into an investment recommendation memo.",
            ),
        ],
    )
    return workflow


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Investment Advisory Multi-Agent System (Agno)"
    )
    parser.add_argument(
        "--topic", default="large-cap technology companies",
        help='Free-text investment theme, e.g. "AI and robotics", "clean energy transition"',
    )
    args = parser.parse_args()

    topic = args.topic.strip()
    header = f"  INVESTMENT ADVISORY TEAM  |  Topic: {topic}  "
    print(f"\n{'='*len(header)}")
    print(header)
    print(f"{'='*len(header)}\n")

    # ---- Initialise tracer -----------------------------------------------
    tracer = WorkflowTracer()
    tracer.start_workflow(topic)

    # ---- Build & run workflow --------------------------------------------
    workflow = build_workflow(tracer)

    result = workflow.run(
        input=f"Run investment research on the theme: {topic}",
        additional_data={"topic": topic},
    )

    # ---- Extract top-3 for file naming -----------------------------------
    top3: list[str] = []
    memo_content = ""

    # ---- Print results ---------------------------------------------------
    print("\n" + "="*70)
    print("  WORKFLOW RESULTS")
    print("="*70 + "\n")

    if hasattr(result, "step_results") and result.step_results:
        for step_result in result.step_results:
            items = step_result if isinstance(step_result, list) else [step_result]
            for sr in items:
                if not sr or not sr.content:
                    continue
                step_name = sr.step_name or "Step"

                # Extract top-3 from Ranking step
                if step_name == "Ranking":
                    try:
                        top3 = json.loads(str(sr.content)).get("top3", [])
                    except (json.JSONDecodeError, TypeError):
                        pass

                print(f"\n{'─'*60}")
                print(f"  {step_name}")
                print(f"{'─'*60}")

                if sr.steps:
                    for sub in sr.steps:
                        if sub.content:
                            print(f"\n### {sub.step_name}\n")
                            print(sub.content)
                else:
                    print(sr.content)

                if step_name == "Investment Memo":
                    memo_content = str(sr.content)
    elif hasattr(result, "content") and result.content:
        print(result.content)
        memo_content = str(result.content)

    tracer.end_workflow(topic, top3)

    # ---- Save memo -------------------------------------------------------
    topic_slug = re.sub(r"[^a-z0-9]+", "_", topic.lower()).strip("_")[:25]
    ticker_tag = "_".join(top3) if top3 else "unknown"
    output_path = f"memo_{topic_slug}_{ticker_tag}.md"
    with open(output_path, "w") as f:
        f.write(f"# Investment Recommendation Memo\n")
        f.write(f"**Topic:** {topic}  |  **Top Companies:** {', '.join(top3)}\n\n")
        f.write("---\n\n")
        f.write(memo_content)

    # ---- Observability reports -------------------------------------------
    tracer.print_data_flows()
    tracer.print_timeline()

    trace_path = f"trace_{topic_slug}.json"
    tracer.save_trace(trace_path)

    print(f"\n{'='*70}")
    print(f"  Memo  saved → {output_path}")
    print(f"  Trace saved → {trace_path}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
