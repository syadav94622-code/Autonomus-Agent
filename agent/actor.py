"""Executor: a LangChain `create_agent` runs ONE plan step.

How "the LLM always calls a tool" is guaranteed:
  * `response_format=ToolStrategy(StepResult)` makes create_agent bind the real tools PLUS a `StepResult`
    tool with tool_choice="any" -> the provider must emit a tool call on EVERY turn (no plain-text turns).
  * The step ends only when the model calls `StepResult` (that is the "finish" tool).
  * StepGuard (middleware) enforces the budget in code: after MAX_TOOL_CALLS_PER_STEP calls, or after
    repeated identical calls, the agent loop is STOPPED (no more model calls) and the step result is obtained
    with ONE forced StepResult call built from a plain-text transcript of what happened.
  * Provider-side tool errors (e.g. Groq "tool_use_failed") are retried with a nudge, never fatal.
  * If a provider still returns plain text, the same forced StepResult fallback is used.
Every model turn and every tool call/observation is logged (see agent/log.py).
"""
import json
import logging

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelResponse
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from agent import state as S
from agent.llm import is_tool_failure, make_llm, structured_call
from agent.log import clip, current_label, get_logger
from agent.schemas import Step, StepResult
from agent.tools import TOOLS

log = get_logger("actor")
RESULT_TOOL = StepResult.__name__
RECURSION_LIMIT = 60

ACTOR_SYSTEM = """You are the Actor of an AI operator. You execute exactly ONE step of a plan using tools.
Rules:
- You act ONLY through tool calls. Never reply in plain text: every turn must be a tool call.
- Use at most {max_calls} tool calls. Be economical: goto/click already return a page snapshot.
- Element ids (e1, e2...) come from the latest snapshot and change after every page load.
- Do not log in; the session is already authenticated.
- Never guess values: read them from files or pages. Amounts are plain numbers (e.g. 3480.00).
- Never click a gated submit button; use submit_record. If a tool says BLOCKED or AMBIGUOUS, stop and
  report status=needs_human with a clear question.
- If a tool result is an error, adapt (re-read the page, fix the value) rather than repeating the same call.
- Do ONLY this step. As soon as its success criteria are met, call {result_tool} immediately; do not start the
  next step even if you know what it is. Other steps run later with their own budget.
- If the ERP shows a validation error, read the message, correct the field and resubmit; on a server error
  retry once.
- FINISH by calling the {result_tool} tool BY ITSELF, in its own turn, after you have seen the results of your
  other tool calls. status=done only if the success criteria are met; failed if not (explain in note);
  needs_human if a human decision is required (set question). Put values discovered in this step in facts as
  plain strings (vendor, invoice_number, amount, due_date, invoice_date, source_file, po_number as relevant).

Company context:
{context}"""

class StepStop(Exception):
    """Raised by StepGuard when the tool budget / loop limit is reached: end the agent loop."""


FALLBACK_PROMPT = ("Report the outcome of this step now by calling the StepResult tool (it is the ONLY tool; do not "
                   "try to call any other tool). status=done only if the "
                   "success criteria are met; failed if not (explain in note); needs_human if a human decision "
                   "is required (set question).")

_BAD_OBS = ("ERROR", "BLOCKED", "AMBIGUOUS", "LOOP DETECTED", "SUBMIT FAILED", "NO MATCH", "NO PO", "NO INVOICE", "Tool-call budget")


class StepGuard(AgentMiddleware):
    """Per-step budget, loop detection, premature-finish detection, and logging of every tool call."""

    def __init__(self, step_id: int, max_calls: int):
        super().__init__()
        self.step_id, self.max_calls = step_id, max_calls
        self.calls = self.loops = 0
        self.last_sig = None
        self.trace: list[dict] = []
        self.premature = False
        self.provider_rejections = 0
        self._model_turns = 0

    @property
    def must_finish(self) -> bool:
        return self.calls >= self.max_calls or self.loops >= 2

    # ---- model side: stop at the budget; retry provider-side tool-call rejections ----
    async def awrap_model_call(self, request, handler):
        self._model_turns += 1
        if self.must_finish:
            log.warning("[step %s] budget/loop limit reached (calls=%d/%d loops=%d): stopping the agent loop, "
                        "the result will come from a forced %s call", self.step_id, self.calls, self.max_calls,
                        self.loops, RESULT_TOOL)
            raise StepStop()
        for attempt in range(1, 4):
            try:
                resp = await handler(request)
                break
            except Exception as e:  # noqa: BLE001
                if not is_tool_failure(e) or attempt == 3:
                    raise
                self.provider_rejections += 1
                log.warning("[step %s] provider rejected the model's tool call (attempt %d/3): %s",
                            self.step_id, attempt, clip(e, 300))
                names = ", ".join([t.name for t in TOOLS] + [RESULT_TOOL])
                request = request.override(messages=[*request.messages, HumanMessage(content=(
                    "Your last tool call was rejected: the tool name was not valid. The tool name must be EXACTLY one of: "
                    f"{names} (there is no tool called 'json'). Call one of them with valid arguments, "
                    f"or call {RESULT_TOOL} if the step is finished."))])
        msgs = resp.result if isinstance(resp, ModelResponse) else getattr(resp, "result", [resp])
        ai = next((m for m in msgs if isinstance(m, AIMessage)), None)
        if ai is not None:
            names = [tc["name"] for tc in ai.tool_calls]
            if RESULT_TOOL in names and len(names) > 1:
                self.premature = True
                log.warning("[step %s] model called %s TOGETHER with %s: those tool calls will NOT run, "
                            "result is untrusted", self.step_id, RESULT_TOOL, [n for n in names if n != RESULT_TOOL])
        return resp

    # ---- tool side: budget, loop detector, logging ----
    async def awrap_tool_call(self, request, handler):
        tc = request.tool_call
        name, args, tid = tc["name"], tc.get("args", {}) or {}, tc["id"]
        sig = (name, json.dumps(args, sort_keys=True, default=str))
        result = None
        if self.calls >= self.max_calls:
            obs = "Tool-call budget for this step is used up. Call StepResult now."
        elif sig == self.last_sig:
            self.loops += 1
            obs = "LOOP DETECTED: identical consecutive action refused. Try something different or report failure."
        else:
            self.calls += 1
            log.info("[step %s] tool #%d -> %s(%s)", self.step_id, self.calls, name, clip(json.dumps(args, default=str), 300))
            try:
                result = await handler(request)
                obs = str(result.content) if isinstance(result, ToolMessage) else str(result)
            except Exception as e:  # noqa: BLE001 - give the model the error as an observation
                obs = f"ERROR: {type(e).__name__}: {e}"
                result = None
        self.last_sig = sig
        obs = clip(obs, 3500, oneline=False)
        lvl = logging.WARNING if obs.startswith(_BAD_OBS) else logging.INFO
        log.log(lvl, "[step %s] tool result <- %s: %s", self.step_id, name, clip(obs, 300))
        log.debug("[step %s] tool result FULL <- %s:\n%s", self.step_id, name, obs)
        self.trace.append({"step": self.step_id, "action": f"{name}({json.dumps(args, default=str)[:200]})",
                           "observation": obs[:600]})
        if result is not None and isinstance(result, ToolMessage):
            return result
        return ToolMessage(content=obs, tool_call_id=tid, name=name)


async def run_step(step: Step, objective: str, facts: dict, context: str,
                   human_answers: list, retry_note: str = "") -> tuple[StepResult, list]:
    task = (f"Objective: {objective}\nStep {step.id}: {step.description}\nTool hint: {step.tool_hint}\n"
            f"Success criteria: {step.success_criteria}\nKnown facts: {json.dumps(facts)}")
    if human_answers:
        task += "\nHuman answers: " + json.dumps(human_answers)
    if retry_note:
        task += "\n" + retry_note
    system = ACTOR_SYSTEM.format(max_calls=S.MAX_TOOL_CALLS_PER_STEP, context=context, result_tool=RESULT_TOOL)
    guard = StepGuard(step.id, S.MAX_TOOL_CALLS_PER_STEP)
    agent = create_agent(
        model=make_llm(),
        tools=TOOLS,
        system_prompt=system,
        response_format=ToolStrategy(StepResult, tool_message_content="Step result recorded."),
        middleware=[guard],
        name=f"actor_step_{step.id}",
    )
    label = f"actor.step{step.id}"
    log.info("[step %s] START: %s | success: %s", step.id, step.description, step.success_criteria)
    log.debug("[step %s] task message:\n%s", step.id, task)
    token = current_label.set(label)
    result, reason = None, ""
    try:
        try:
            out = await agent.ainvoke({"messages": [HumanMessage(content=task)]},
                                      config={"recursion_limit": RECURSION_LIMIT, "run_name": label})
            result = out.get("structured_response")
            reason = "the agent ended without a StepResult tool call"
        except GraphRecursionError:
            result = StepResult(status="failed", note="Executor hit its iteration limit without finishing.")
            log.error("[step %s] recursion limit hit", step.id)
        except StepStop:
            reason = "tool budget / loop limit reached"
            if guard.loops >= 2:           # looping: no point asking the model, the verdict is fixed below
                result = StepResult(status="failed", note="Loop detector: repeated identical actions.")
        except Exception as e:  # noqa: BLE001
            if not is_tool_failure(e):
                raise                      # auth / model-not-found / network: fail loudly
            reason = f"the provider kept rejecting the model's tool calls: {clip(e, 200)}"
            log.error("[step %s] %s", step.id, reason)
        if result is None:
            log.warning("[step %s] getting the result with a forced %s call (%s)", step.id, RESULT_TOOL, reason)
            # Plain-text transcript instead of raw tool-call history: the model cannot imitate (or call) a tool
            # that is not bound, so providers like Groq cannot reject it.
            transcript = "\n".join(f"{i}. {t['action']} -> {clip(t['observation'], 500)}"
                                   for i, t in enumerate(guard.trace, 1)) or "(no tool calls were made)"
            msgs = [SystemMessage(content=system),
                    HumanMessage(content=f"{task}\n\nTool calls executed so far for this step:\n{transcript}\n\n"
                                         f"{FALLBACK_PROMPT}")]
            try:
                result = await structured_call(StepResult, msgs, label=f"{label}.result")
            except Exception as e:  # noqa: BLE001
                result = StepResult(status="failed", note=f"Could not produce a step result: {e}")
    finally:
        current_label.reset(token)

    if guard.loops >= 2:
        result = StepResult(status="failed", note="Loop detector: repeated identical actions.", facts=result.facts)
    elif guard.premature and result.status == "done":
        result = StepResult(status="failed", facts=result.facts,
                            note="Model reported the step before its tool calls had run; retry and call StepResult alone.")
    log.info("[step %s] RESULT status=%s note=%r question=%r facts=%s | tool_calls=%d model_turns=%d",
             step.id, result.status, result.note, result.question, result.facts, guard.calls, guard._model_turns)
    return result, guard.trace
