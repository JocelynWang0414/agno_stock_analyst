# Workflow Bug Analysis

## Bug: Fundamental & Technical Analysts Produced Empty Responses

**Symptom**
```
[Fundamental Analyst] Received prompt (3257 chars)
[Technical Analyst]   Received prompt (3257 chars)
[Fundamental Analyst] Produced response (99 chars)
[Technical Analyst]   Produced response (63 chars)
[Ranking] Score extraction failed for 10/10 tickers — using LLM fallback
All scores: {'LLY': None, 'UNH': None, ...}
```
Both analysts produced near-empty responses. Every ticker scored `None`, causing total ranking failure.

---

## Root Cause

**Agno's `Step._prepare_message()` always feeds the last preceding step's output to `agent=` Steps.**

Source (`agno/workflow/step.py`, `_prepare_message`):
```python
last_output = list(previous_step_outputs.values())[-1]
deepest_content = self._get_deepest_content_from_step_output(last_output)
return deepest_content   # this becomes the agent's user message
```

The original pipeline order was:
```
Coordinator (237 chars) → Macro Analysis (3257 chars) → Parallel Analysis
```

When `Parallel Analysis` ran, `previous_step_outputs[-1]` = Macro Analysis output (a macroeconomic narrative). Both analysts received that as their prompt — no ticker list, no instruction to analyze anything. The model returned a confused short response.

---

## Fix

**Converted analyst Steps from `agent=` to `executor=`.** Executor-based Steps receive the full `StepInput` and compose their own prompt explicitly, bypassing `_prepare_message` entirely.

Two new factories in `workflow/steps.py`:
```python
def make_fundamental_analyst_step(fundamental_analyst, tracer):
    def step(step_input):
        coordinator_brief = step_input.get_step_content("Coordinator")
        macro_report      = step_input.get_step_content("Macro Analysis")
        prompt = f"{coordinator_brief}\n\n--- MACRO CONTEXT ---\n{macro_report}"
        response = fundamental_analyst.run(prompt)
        ...
```

Pipeline order (`workflow/pipeline.py`) restored to:
```
Coordinator → Macro Analysis → Parallel(Fundamental, Technical) → Ranking → Memo
```

Each analyst now receives:
- The Coordinator's ticker brief (what to analyze)
- The Macro Analysis report (macro context to frame their analysis)

Tracer logging (`AGENT_INPUT` / `AGENT_OUTPUT`) is unaffected because `wrap_agent` patches `agent.run()` directly.

---

## Key Agno Behavior to Remember

| Step type | How user message is determined |
|---|---|
| `Step(agent=...)` | Always `previous_step_outputs[-1].content` via `_prepare_message` |
| `Step(executor=...)` | Executor function receives full `StepInput`; caller controls the prompt |

**Rule:** Use `executor=` whenever an agent needs to read from a step other than the immediately preceding one, or needs to combine content from multiple steps.
