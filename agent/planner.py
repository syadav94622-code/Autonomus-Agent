"""Planner and context-selection prompts. Both answer ONLY through forced tool calls (see agent/llm.py)."""
import json

from langchain_core.messages import HumanMessage, SystemMessage

from agent import state as S
from agent.context_loader import index_text
from agent.llm import structured_call
from agent.log import get_logger
from agent.schemas import ContextSelection, Plan
from agent.tools import TOOL_GUIDE

log = get_logger("planner")

SELECT_SYSTEM = """You select which company context documents are needed to complete a task.
Return only file names that appear in the index. If the message is a greeting or not an actionable task,
return an empty list. You must respond by calling the ContextSelection tool."""


async def select_context(goal: str, index: list[dict]) -> ContextSelection:
    msgs = [SystemMessage(content=SELECT_SYSTEM),
            HumanMessage(content=f"Task: {goal}\n\nIndex:\n{index_text(index)}")]
    sel = await structured_call(ContextSelection, msgs, label="select_context")
    log.info("context docs selected: %s | reason: %s", sel.docs, sel.reason)
    return sel


PLANNER_SYSTEM = """You are the Planner of an AI operator that completes company tasks with real tools.
Turn the goal into an ordered list of small steps. Rules:
- You must respond by calling the Plan tool. Never answer in plain text.
- Follow the relevant procedure document in the context. Do not invent systems or URLs.
- Each step must be doable in at most {max_calls} tool calls by an executor that sees only that step. The browser is
  already authenticated. NEVER plan a login step. Never repeat navigation that an earlier step already did.
- The executor does NOT see the procedure document. Make every step description SELF-CONTAINED: name the exact
  tools, arguments and URLs to use and any procedure rule that matters (e.g. "use submit_record, never click submit").
- Prefer few, meaningful steps (typically 3-4), each 2-{max_calls} tool calls, e.g. one step "identify the document"
  (lookup_vendor + find_latest_document + read_file) instead of one step per tool call.
- Do NOT include a final verification step; the system verifies deterministically afterwards.
- Never include a step that asks a human unless information is truly missing; ambiguity is handled by tools.
- If the goal is a greeting, unclear, or not something these tools can do: return steps=[] and put ONE
  clarifying question in open_questions.
- Mark needs_approval=true for steps that submit something above policy thresholds.
- submit_record already saves a screenshot automatically; do not make a separate screenshot a success criterion.
- When re-planning, plan only the REMAINING work, using the known facts, and avoid repeating the failed approach.
  A one-time ERP HTTP 500 is retryable with identical values; it is not a data problem.
Available tools:
{tools}

Company context:
{context}"""


async def make_plan(goal: str, context: str, facts: dict, failure: str | None = None,
                    step_log: list | None = None, human_answers: list | None = None) -> Plan:
    parts = [f"Goal: {goal}", f"Known facts: {json.dumps(facts)}"]
    if human_answers:
        parts.append("Human answers so far: " + json.dumps(human_answers))
    if failure:
        recent = (step_log or [])[-6:]
        parts.append("RE-PLAN REQUIRED. What went wrong:\n" + failure +
                     "\nRecent actions:\n" + json.dumps(recent, default=str)[:2500])
    msgs = [SystemMessage(content=PLANNER_SYSTEM.format(tools=TOOL_GUIDE, context=context,
                                                          max_calls=S.MAX_TOOL_CALLS_PER_STEP)),
            HumanMessage(content="\n\n".join(parts))]
    return await structured_call(Plan, msgs, label="replan" if failure else "plan")
