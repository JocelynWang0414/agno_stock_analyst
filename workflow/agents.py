"""
Agent definitions — Fundamental Analyst, Technical Analyst, Portfolio Strategist.
"""

from textwrap import dedent

from agno.agent import Agent
from agno.tools.yfinance import YFinanceTools

from config import llm
from core.tracer import WorkflowTracer
from tools.fundamental import get_free_cash_flow
from tools.technical import compute_technical_signals


def make_fundamental_analyst(tracer: WorkflowTracer) -> Agent:
    return tracer.wrap_agent(Agent(
        name="Fundamental Analyst",
        model=llm(),
        tools=[
            get_free_cash_flow,
            YFinanceTools(
                enable_stock_fundamentals=True,
                enable_income_statements=True,
                enable_key_financial_ratios=True,
                enable_analyst_recommendations=True,
                enable_company_info=True,
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


def make_technical_analyst(tracer: WorkflowTracer) -> Agent:
    return tracer.wrap_agent(Agent(
        name="Technical Analyst",
        model=llm(),
        tools=[
            compute_technical_signals,
            YFinanceTools(enable_stock_price=True),
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


def make_portfolio_strategist(tracer: WorkflowTracer) -> Agent:
    return tracer.wrap_agent(Agent(
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
