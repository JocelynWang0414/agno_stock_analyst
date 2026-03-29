"""
Agno Workflow definition — assembles all steps into the Investment Advisory pipeline.
"""

from agno.workflow import Parallel, Step, Workflow

from core.tracer import WorkflowTracer
from workflow.agents import (
    make_fundamental_analyst,
    make_portfolio_strategist,
    make_technical_analyst,
)
from workflow.steps import (
    make_company_discovery_step,
    make_coordinator_step,
    make_ranking_step,
    make_synthesis_step,
    make_topic_mapper_step,
)


def build_workflow(tracer: WorkflowTracer) -> Workflow:
    fundamental_analyst  = make_fundamental_analyst(tracer)
    technical_analyst    = make_technical_analyst(tracer)
    portfolio_strategist = make_portfolio_strategist(tracer)

    return Workflow(
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
                executor=make_synthesis_step(portfolio_strategist, tracer),
                description="Synthesise top-3 analysis into an investment recommendation memo.",
            ),
        ],
    )
