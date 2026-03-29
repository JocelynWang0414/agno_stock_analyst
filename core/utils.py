"""
Shared utility functions: regex extractors, text formatters, LLM ranking fallback.
"""

import json
import re

from agno.agent import Agent

from config import llm
from data.sectors import FMP_SECTORS


def _parse_sectors_from_llm(text: str) -> list[str]:
    """Extract and validate sectors list from LLM JSON response."""
    match = re.search(r'\{[^{}]*"sectors"\s*:\s*\[[^\]]*\][^{}]*\}', text, re.DOTALL)
    if match:
        try:
            data    = json.loads(match.group())
            sectors = data.get("sectors", [])
            valid   = [s for s in sectors if s in FMP_SECTORS]
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
        content  = response.content if response else ""
        match    = re.search(r'\{[^{}]*"ranked"\s*:\s*\[[^\]]*\][^{}]*\}', content, re.DOTALL)
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
        match   = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
        sections.append(match.group(1).strip() if match else f"## {ticker}\n[Analysis not available]")
    return "\n\n".join(sections)
