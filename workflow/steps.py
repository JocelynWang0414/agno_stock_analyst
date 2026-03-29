"""
Step factory functions — topic mapper, company discovery, coordinator, ranking, synthesis.
"""

import json
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
        content  = response.content if response else ""

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

        tickers = fetch_companies_for_sectors(sectors)

        print(f"  [Company Discovery] {len(tickers)} companies for {sectors}: {', '.join(tickers)}")

        sector_label = " / ".join(sectors)
        result = {
            "tickers":      tickers,
            "sectors":      sectors,
            "sector_label": sector_label,
            "topic":        topic,
        }
        tracer.data_flow("Company Discovery", "Coordinator", f"{len(tickers)} tickers: {tickers}")
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

        tracer.step_start("Investment Memo", input_data={
            "topic":             topic,
            "top3":              top3,
            "fundamental_chars": len(fund_content),
            "technical_chars":   len(tech_content),
        })

        response = portfolio_strategist.run(synthesis_prompt)
        memo     = response.content if response else "Memo generation failed."

        tracer.step_end("Investment Memo", output_data=f"{len(memo)} chars", success=bool(memo))
        return StepOutput(step_name="Investment Memo", content=memo, success=True)

    return synthesis_step
