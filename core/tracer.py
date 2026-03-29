"""
WorkflowTracer — observability for the multi-agent investment workflow.
"""

import json
import time
from datetime import datetime

from agno.agent import Agent


# ---------------------------------------------------------------------------
# ANSI colour helpers (gracefully degrade when terminal doesn't support them)
# ---------------------------------------------------------------------------
_RESET   = "\033[0m"
_BOLD    = "\033[1m"
_DIM     = "\033[2m"
_CYAN    = "\033[36m"
_GREEN   = "\033[32m"
_YELLOW  = "\033[33m"
_BLUE    = "\033[34m"
_MAGENTA = "\033[35m"
_WHITE   = "\033[97m"

_EVENT_COLOURS = {
    "WORKFLOW_START":    _CYAN    + _BOLD,
    "WORKFLOW_END":      _CYAN    + _BOLD,
    "STEP_START":        _BLUE    + _BOLD,
    "STEP_END":          _BLUE,
    "PARALLEL_START":    _MAGENTA + _BOLD,
    "PARALLEL_END":      _MAGENTA,
    "AGENT_INPUT":       _YELLOW  + _BOLD,
    "AGENT_OUTPUT":      _GREEN   + _BOLD,
    "AGENT_TOOL_CALL":   _WHITE   + _DIM,
    "DATA_FLOW":         _DIM,
}


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
                   summary="Step starting",
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
            colour  = _EVENT_COLOURS.get(ev["type"], "")
            actor   = ev["actor"][:27]
            etype   = ev["type"][:17]
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
