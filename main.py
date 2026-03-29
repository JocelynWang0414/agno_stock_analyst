"""
Investment Advisory Team — CLI entry point.

Run:
  python main.py --topic "AI and robotics"
  python main.py --topic "clean energy transition"
  python main.py --topic "US regional banking"
"""

import argparse
import json
import re
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from core.tracer import WorkflowTracer
from workflow.pipeline import build_workflow


def main():
    parser = argparse.ArgumentParser(
        description="Investment Advisory Multi-Agent System (Agno)"
    )
    parser.add_argument(
        "--topic", default="large-cap technology companies",
        help='Free-text investment theme, e.g. "AI and robotics", "clean energy transition"',
    )
    args = parser.parse_args()

    topic  = args.topic.strip()
    header = f"  INVESTMENT ADVISORY TEAM  |  Topic: {topic}  "
    print(f"\n{'='*len(header)}")
    print(header)
    print(f"{'='*len(header)}\n")

    tracer = WorkflowTracer()
    tracer.start_workflow(topic)

    workflow = build_workflow(tracer)

    result = workflow.run(
        input=f"Run investment research on the theme: {topic}",
        additional_data={"topic": topic},
    )

    top3: list[str] = []
    memo_content    = ""

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
    topic_slug  = re.sub(r"[^a-z0-9]+", "_", topic.lower()).strip("_")[:25]
    ticker_tag  = "_".join(top3) if top3 else "unknown"
    output_path = f"memo_{topic_slug}_{ticker_tag}.md"
    with open(output_path, "w") as f:
        f.write("# Investment Recommendation Memo\n")
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
