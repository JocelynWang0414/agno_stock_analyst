"""
Step factory functions — topic mapper, company discovery, coordinator, ranking, synthesis.
"""

import json
import re
from datetime import date
from textwrap import dedent
from agno.agent import Agent
from agno.workflow import StepInput, StepOutput

from config import llm
from core.tracer import WorkflowTracer
from core.utils import (
    _extract_scores,
    _filter_content_to_tickers,
    _llm_rank_fallback,
    _parse_sectors_from_llm,
)
from data.sectors import FMP_SECTORS, fetch_companies_for_sectors
from tools.macro import get_fred_data

_EVAL_MAX_RETRIES = 2  # number of re-attempts before accepting whatever we have


def _sector_evaluator_check(
    tickers: list[str],
    sectors: list[str],
    topic: str,
) -> tuple[bool, str, list[str]]:
    """LLM sanity-check: do these tickers plausibly represent the given sectors / topic?

    Returns:
        accepted          – True → proceed, False → retry with suggested_sectors
        reason            – one-sentence explanation
        suggested_sectors – non-empty only when rejected; sectors to try next
    """
    ticker_str = ", ".join(tickers)
    sector_str = ", ".join(sectors)
    valid_list = json.dumps(FMP_SECTORS)

    prompt = (
        f"You are a financial domain expert reviewing a stock screener result.\n\n"
        f"Investment theme : \"{topic}\"\n"
        f"Target sectors   : {sector_str}\n"
        f"Companies found  : {ticker_str}\n\n"
        f"Valid sector names (use ONLY these): {valid_list}\n\n"
        f"Task: decide whether the companies are reasonable representatives of the theme/sectors.\n"
        f"- Answer ACCEPTED if they broadly fit, even if imperfect.\n"
        f"- Answer REJECTED only on a clear mismatch (e.g. oil stocks for a fintech theme).\n"
        f"- When rejecting, suggest 1-3 better sectors from the valid list above.\n\n"
        f"Respond ONLY with a JSON object, no other text:\n"
        f'{{"verdict":"ACCEPTED","reason":"one sentence","suggested_sectors":[]}}'
    )

    evaluator = Agent(model=llm(), description="Sector alignment evaluator")
    response  = evaluator.run(prompt)
    content   = (response.content if response and response.content else None) or ""

    # Extract first JSON object from the response
    m = re.search(r'\{.*?\}', content, re.DOTALL)
    if m:
        try:
            data      = json.loads(m.group())
            verdict   = str(data.get("verdict", "ACCEPTED")).upper()
            reason    = str(data.get("reason", ""))
            suggested = [s for s in data.get("suggested_sectors", []) if s in FMP_SECTORS]
            return verdict == "ACCEPTED", reason, suggested
        except (json.JSONDecodeError, KeyError):
            pass

    # Fail-safe: if we can't parse the response, accept and move on
    return True, "Evaluation inconclusive — proceeding with original selection.", []


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
        mapper   = Agent(model=llm(), description="Sector mapper")
        response = mapper.run(prompt)
        content  = (response.content if response and response.content else None) or ""

        sectors = _parse_sectors_from_llm(content)
        if not sectors:
            print("  [Topic Mapper] Could not parse sectors from LLM; defaulting to Technology")
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

        # ------------------------------------------------------------------ #
        #  Evaluator loop — up to _EVAL_MAX_RETRIES re-attempts.             #
        #  Why an internal loop instead of Agno Loop:                        #
        #  Agno's Loop.execute() flattens all iterations into one list and   #
        #  _search_in_step_output() returns the FIRST name-match, so         #
        #  downstream steps would silently read the *rejected* iteration.    #
        # ------------------------------------------------------------------ #
        current_sectors: list[str] = sectors
        tickers:         list[str] = []
        eval_status  = "skipped"
        eval_reason  = ""

        for attempt in range(1, _EVAL_MAX_RETRIES + 2):   # 1 … MAX+1
            tickers = fetch_companies_for_sectors(current_sectors)
            print(
                f"  [Company Discovery] Attempt {attempt}/{_EVAL_MAX_RETRIES + 1}: "
                f"sectors={current_sectors} → {len(tickers)} tickers: {', '.join(tickers)}"
            )

            if not tickers:
                print("  [Sector Evaluator] No tickers found; skipping evaluation.")
                eval_status = "no_tickers"
                break

            # ---- Evaluator ---- #
            print(f"  [Sector Evaluator] Checking alignment: \"{topic}\" → {current_sectors}")
            accepted, reason, suggested = _sector_evaluator_check(tickers, current_sectors, topic)

            if accepted:
                eval_status = "accepted"
                eval_reason = reason
                print(f"  [Sector Evaluator] ACCEPTED — {reason}")
                break

            # Rejected path
            eval_reason = reason
            print(f"  [Sector Evaluator] REJECTED (attempt {attempt}) — {reason}")

            if attempt > _EVAL_MAX_RETRIES:
                # Exhausted retries; accept best available tickers
                eval_status = "accepted_fallback"
                print("  [Sector Evaluator] Max retries reached — proceeding with current tickers.")
                break

            if suggested:
                print(f"  [Sector Evaluator] Retrying with suggested sectors: {suggested}")
                current_sectors = suggested
            else:
                # Evaluator gave no actionable suggestion; accept immediately
                eval_status = "accepted_fallback"
                print("  [Sector Evaluator] No alternative sectors suggested — accepting current tickers.")
                break

        sector_label = " / ".join(current_sectors)
        result = {
            "tickers":      tickers,
            "sectors":      current_sectors,
            "sector_label": sector_label,
            "topic":        topic,
            "eval_status":  eval_status,
            "eval_reason":  eval_reason,
        }
        tracer.data_flow("Company Discovery", "Coordinator", f"{len(tickers)} tickers: {tickers} [eval={eval_status}]")
        tracer.step_end("Company Discovery", output_data=result, success=bool(tickers))
        return StepOutput(
            step_name="Company Discovery",
            content=json.dumps(result),
            success=bool(tickers),
        )

    return company_discovery_step


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


def make_macro_step(macro_analyst, tracer: WorkflowTracer):
    def macro_step(step_input: StepInput) -> StepOutput:
        discovery_raw = step_input.get_step_content("Company Discovery") or "{}"
        try:
            discovery_data = json.loads(discovery_raw)
        except json.JSONDecodeError:
            discovery_data = {}

        tickers      = discovery_data.get("tickers", [])
        sectors      = discovery_data.get("sectors", [])
        sector_label = discovery_data.get("sector_label", "Multiple Sectors")
        topic        = discovery_data.get("topic", "")

        tracer.step_start("Macro Analysis", input_data={
            "sectors": sectors, "tickers": tickers,
        })
        tracer.data_flow("Coordinator", "Macro Analysis", f"sectors={sectors}, {len(tickers)} tickers")

        # Pre-fetch FRED data in the executor — avoids unreliable tool-calling
        fred_series = [
            ("FEDFUNDS", 12),   # Fed Funds Rate — monthly
            ("T10Y2Y",   12),   # Yield Curve spread — daily (last 12 obs)
            ("CPIAUCSL", 13),   # CPI — 13 months for YoY delta
            ("GDPC1",     8),   # Real GDP — quarterly
        ]
        fred_lines = []
        for series_id, limit in fred_series:
            result = get_fred_data(series_id, limit)
            fred_lines.append(result)
            print(f"  [Macro Analysis] Fetched {series_id}: {result.splitlines()[0]}")
        fred_context = "\n\n".join(fred_lines)

        prompt = dedent(f"""\
            Investment Theme: {topic}
            Target Sectors: {sector_label}
            Tickers Under Consideration: {', '.join(tickers)}

            --- FRED ECONOMIC DATA ---
            {fred_context}
            --- END FRED DATA ---

            Using the data above, produce a full macroeconomic analysis.
            Focus your sector sensitivity on: {sector_label}.
        """)

        response = macro_analyst.run(prompt)
        content  = (response.content if response and response.content else None) or "Macro analysis unavailable."

        tracer.data_flow("Macro Analysis", "Parallel Analysis", f"{len(content)} chars macro report")
        tracer.step_end("Macro Analysis", output_data=f"{len(content)} chars", success=True)
        return StepOutput(step_name="Macro Analysis", content=content, success=True)

    return macro_step


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

        scores  = _extract_scores(tickers, fund_content, tech_content)
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
            "top3":        top3,
            "all_ranked":  ranked,
            "scores":      scores,
            "sector_label": sector_label,
            "topic":       topic,
        }
        tracer.data_flow("Parallel Analysis", "Ranking", f"Scored {len(scores)} tickers, top3={top3}")
        tracer.step_end("Ranking", output_data=f"top3={top3}", success=True)
        return StepOutput(step_name="Ranking", content=json.dumps(result), success=True)

    return ranking_step


def _get_tickers_from_input(step_input: StepInput) -> list[str]:
    """Extract tickers from the Company Discovery step output."""
    raw = step_input.get_step_content("Company Discovery") or "{}"
    try:
        return json.loads(raw).get("tickers", [])
    except json.JSONDecodeError:
        return []


def make_fundamental_analyst_step(fundamental_analyst, tracer: WorkflowTracer):
    def fundamental_analyst_step(step_input: StepInput) -> StepOutput:
        tickers      = _get_tickers_from_input(step_input)
        macro_report = step_input.get_step_content("Macro Analysis") or ""

        tracer.data_flow("Coordinator + Macro Analysis", "Fundamental Analyst",
                         f"{len(tickers)} tickers, macro={len(macro_report)} chars")

        # gemini-2.5-flash-lite refuses multi-ticker calls — loop one ticker at a time
        results: list[str] = []
        for ticker in tickers:
            prompt = dedent(f"""\
                Analyse {ticker}.

                --- MACRO CONTEXT (use this to frame your analysis) ---
                {macro_report}
                --- END MACRO CONTEXT ---
            """)
            response = fundamental_analyst.run(prompt)
            content  = (response.content if response and response.content else None) or ""
            if content:
                results.append(content)
            else:
                print(f"  [Fundamental Analyst] Empty response for {ticker}")

        combined = "\n\n".join(results)
        return StepOutput(step_name="Fundamental Analyst", content=combined, success=bool(combined))

    return fundamental_analyst_step


def make_technical_analyst_step(technical_analyst, tracer: WorkflowTracer):
    def technical_analyst_step(step_input: StepInput) -> StepOutput:
        tickers      = _get_tickers_from_input(step_input)
        macro_report = step_input.get_step_content("Macro Analysis") or ""

        tracer.data_flow("Coordinator + Macro Analysis", "Technical Analyst",
                         f"{len(tickers)} tickers, macro={len(macro_report)} chars")

        # gemini-2.5-flash-lite refuses multi-ticker calls — loop one ticker at a time
        results: list[str] = []
        for ticker in tickers:
            prompt = dedent(f"""\
                Analyse {ticker}.

                --- MACRO CONTEXT (use this to frame your analysis) ---
                {macro_report}
                --- END MACRO CONTEXT ---
            """)
            response = technical_analyst.run(prompt)
            content  = (response.content if response and response.content else None) or ""
            if content:
                results.append(content)
            else:
                print(f"  [Technical Analyst] Empty response for {ticker}")

        combined = "\n\n".join(results)
        return StepOutput(step_name="Technical Analyst", content=combined, success=bool(combined))

    return technical_analyst_step


def make_synthesis_step(portfolio_strategist, tracer: WorkflowTracer):
    def synthesis_step(step_input: StepInput) -> StepOutput:
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

        macro_content = step_input.get_step_content("Macro Analysis") or ""

        parallel_content = step_input.get_step_content("Parallel Analysis")
        if isinstance(parallel_content, dict):
            fund_all = str(parallel_content.get("Fundamental Analyst", ""))
            tech_all = str(parallel_content.get("Technical Analyst", ""))
        else:
            fund_all = tech_all = ""

        fund_content = _filter_content_to_tickers(fund_all, top3) or fund_all
        tech_content = _filter_content_to_tickers(tech_all, top3) or tech_all

        scorecard_lines = []
        for t in top3:
            s        = scores.get(t, {})
            comp     = s.get("composite")
            comp_str = f"{comp:.2f}" if isinstance(comp, float) else "N/A"
            scorecard_lines.append(
                f"  {t}: Fund={s.get('fund','?')}/5  Tech={s.get('tech','?')}/5  Composite={comp_str}"
            )

        tracer.data_flow(
            from_step="Ranking",
            to_step="Investment Memo",
            data_summary=f"top3={top3}, macro={len(macro_content)} chars, fund={len(fund_content)} chars, tech={len(tech_content)} chars",
        )

        today = date.today().strftime("%B %d, %Y")

        synthesis_prompt = dedent(f"""\
            Investment Theme: **{topic}**
            Sectors Covered: {sector_label}
            Top 3 Companies (selected from 10 screened): {ticker_str}
            Report Date: {today}

            COMPOSITE SCORES:
            {chr(10).join(scorecard_lines)}

            ---
            ### MACROECONOMIC CONTEXT
            {macro_content if macro_content else "_Macro analysis unavailable._"}

            ---
            ### FUNDAMENTAL ANALYSIS REPORTS (Top 3)
            {fund_content}

            ---
            ### TECHNICAL ANALYSIS REPORTS (Top 3)
            {tech_content}

            ---
            Please produce the full Investment Recommendation Memo as instructed.
        """)

        tracer.step_start("Investment Memo", input_data={
            "topic":             topic,
            "top3":              top3,
            "fundamental_chars": len(fund_content),
            "technical_chars":   len(tech_content),
        })

        response = portfolio_strategist.run(synthesis_prompt)
        memo     = (response.content if response and response.content else None) or "Memo generation failed."

        tracer.step_end("Investment Memo", output_data=f"{len(memo)} chars", success=bool(memo))
        return StepOutput(step_name="Investment Memo", content=memo, success=True)

    return synthesis_step
