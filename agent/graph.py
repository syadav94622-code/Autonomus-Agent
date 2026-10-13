"""LangGraph: load_context -> plan -> act -> observe -> (act | replan | ask_human | verify) -> report."""
import json

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agent import state as S
from agent.actor import run_step
from agent.context_loader import build_index, load_docs
from agent.planner import make_plan, select_context
from agent.report import write_report
from agent.schemas import Plan, StepResult
from agent.log import clip, get_logger
from agent.state import MAX_REPLANS, AgentState
from agent.verify import run_checks

ABORT_WORDS = {"abort", "cancel", "stop", "quit", "no", "n"}
log = get_logger("graph")


# ---------------- nodes ----------------
async def load_context(state: AgentState):
    log.info("=== NODE load_context | goal: %s", state["goal"])
    index = build_index()
    sel = await select_context(state["goal"], index)
    docs = load_docs(sel.docs)
    log.debug("loaded context docs (%d chars)", len(docs))
    S.RUN.audit("load_context", selected=sel.docs, reason=sel.reason)
    return {"context_docs": docs}


def _plan_update(plan: Plan, extra: dict):
    S.RUN.audit("plan", objective=plan.objective, steps=[s.description for s in plan.steps],
                assumptions=plan.assumptions, open_questions=plan.open_questions)
    log.info("PLAN objective: %s", plan.objective)
    for st in plan.steps:
        log.info("  step %s: %s | tools: %s | success: %s%s", st.id, clip(st.description, 600), st.tool_hint,
                 st.success_criteria, " | NEEDS APPROVAL" if st.needs_approval else "")
    if plan.assumptions:
        log.info("  assumptions: %s", plan.assumptions)
    if plan.open_questions:
        log.info("  open questions: %s", plan.open_questions)
    upd = {"plan": plan.model_dump(), "current_step": 0, "last_result": {}, **extra}
    if plan.steps:
        upd["route"] = "act"
    else:
        q = "; ".join(plan.open_questions) or "I could not build a plan for this request. Can you clarify it?"
        log.warning("plan has no steps -> asking the human: %s", q)
        upd.update(route="ask_human", pending={"kind": "clarification", "question": q, "meta": {"needs_plan": True}})
    return upd


async def plan_node(state: AgentState):
    log.info("=== NODE plan")
    plan = await make_plan(state["goal"], state["context_docs"], state.get("facts", {}),
                           human_answers=state.get("human_answers", []))
    return _plan_update(plan, {})


async def replan_node(state: AgentState):
    n = state.get("replans", 0)
    failure = state.get("failure", "unknown failure")
    log.warning("=== NODE replan #%d | reason: %s", n + 1, clip(failure, 500))
    if n >= MAX_REPLANS:
        S.RUN.audit("escalate", replans=n, failure=failure)
        log.error("replan cap (%d) reached -> escalating to the human", n)
        return {"route": "ask_human", "pending": {
            "kind": "escalation", "meta": {},
            "question": (f"I could not complete this after {n} replans. Last problem: {failure}\n"
                         "Give me guidance to try again, or type 'abort'.")}}
    plan = await make_plan(state["goal"], state["context_docs"], state.get("facts", {}),
                           failure=failure, step_log=state.get("step_log", []),
                           human_answers=state.get("human_answers", []))
    return _plan_update(plan, {"replans": n + 1})


async def act_node(state: AgentState):
    plan = Plan(**state["plan"])
    step = plan.steps[state["current_step"]]
    S.RUN.facts.update(state.get("facts", {}))
    S.RUN.audit("act_start", step=step.id, description=step.description)
    log.info("=== NODE act | step %s/%s", step.id, len(plan.steps))
    step_trace, result, note = [], None, ""
    for attempt in range(2):  # one direct retry with the error fed back
        result, trace = await run_step(step, plan.objective, dict(S.RUN.facts), load_docs([]),   # company+policies only
                                       state.get("human_answers", []), note)
        step_trace += trace
        if result.status != "failed" or S.RUN.pending:
            break
        note = f"Your previous attempt failed: {result.note}. Retry once, correcting the problem."
        S.RUN.audit("act_retry", step=step.id, error=result.note)
        log.warning("step %s failed (%s) -> one direct retry", step.id, clip(result.note, 300))
    S.RUN.audit("act_end", step=step.id, status=result.status, note=result.note)
    log.info("step %s finished: %s %s", step.id, result.status.upper(), clip(result.note, 300))
    return {"last_result": result.model_dump(), "step_log": step_trace}


async def observe_node(state: AgentState):
    res = StepResult(**state["last_result"])
    facts = {**state.get("facts", {}), **res.facts, **S.RUN.facts}   # tool-recorded facts win
    S.RUN.facts = dict(facts)
    plan = Plan(**state["plan"])
    upd = {"facts": facts}
    if S.RUN.pending:                       # policy gate or ambiguity detected in code: trust code over the LLM
        upd.update(route="ask_human", pending=S.RUN.pending)
    elif res.status == "needs_human":
        upd.update(route="ask_human", pending={"kind": "clarification", "meta": {},
                                               "question": res.question or res.note or "Need your input."})
    elif res.status == "failed":
        upd.update(route="replan", failure=f"Step {state['current_step'] + 1} failed: {res.note}")
    else:
        nxt = state["current_step"] + 1
        upd["current_step"] = nxt
        upd["route"] = "act" if nxt < len(plan.steps) else "verify"
    log.info("=== NODE observe | status=%s -> route=%s | facts=%s", res.status, upd["route"], clip(facts, 400))
    return upd


async def ask_human_node(state: AgentState):
    p = state["pending"]
    log.debug("=== NODE ask_human [%s] %s (node re-runs once on resume)", p["kind"], p["question"])
    answer = interrupt({"kind": p["kind"], "question": p["question"]})   # pauses; resumes with the answer
    ans = str(answer).strip()
    low = ans.lower()
    S.RUN.pending = None
    entry = {"kind": p["kind"], "question": p["question"], "answer": ans}
    S.RUN.human_answers.append(entry)
    S.RUN.audit("human_answer", **entry)
    log.info("human answered: %r", ans)
    upd = {"pending": None, "human_answers": [entry]}
    if low in ABORT_WORDS and p["kind"] != "approval":
        return {**upd, "aborted": True, "route": "report"}
    if p["kind"] == "approval":
        if low.startswith(("y", "approve", "ok")):
            import time
            appr = {"vendor": p["meta"]["vendor"], "amount": p["meta"]["amount"], "ts": time.time(), "by": "human"}
            S.RUN.approvals.append(appr)
            S.RUN.audit("approval", **appr)
            return {**upd, "approvals": [appr], "route": "act"}
        return {**upd, "aborted": True, "route": "report"}
    if p["kind"] == "escalation":
        return {**upd, "replans": 0, "failure": f"Human guidance: {ans}", "route": "replan"}
    if p.get("meta", {}).get("needs_plan"):      # the plan had no steps: plan again with the human's answer
        return {**upd, "route": "plan"}
    return {**upd, "route": "act"}


async def verify_node(state: AgentState):
    log.info("=== NODE verify")
    S.RUN.audit("verify_start")
    checks = await run_checks(dict(S.RUN.facts) | {k: v for k, v in state.get("facts", {}).items() if k not in S.RUN.facts})
    S.RUN.audit("verify_end", checks=checks)
    for k, v in checks.items():
        log.log(20 if v["passed"] else 40, "verify %-17s %s: %s", k, "PASS" if v["passed"] else "FAIL", clip(v["detail"], 300))
    if all(c["passed"] for c in checks.values()):
        return {"verification": checks, "route": "report"}
    bad = "; ".join(f"{k}: {v['detail']}" for k, v in checks.items() if not v["passed"])
    return {"verification": checks, "route": "replan", "failure": f"Final verification failed: {bad}"}


async def report_node(state: AgentState):
    log.info("=== NODE report")
    state = {**state, "facts": dict(S.RUN.facts) | state.get("facts", {})}
    return {"report": write_report(state)}


# ---------------- graph ----------------
def _route(state: AgentState) -> str:
    return state["route"]


def build_graph():
    g = StateGraph(AgentState)
    for name, fn in [("load_context", load_context), ("plan", plan_node), ("act", act_node),
                     ("observe", observe_node), ("replan", replan_node), ("ask_human", ask_human_node),
                     ("verify", verify_node), ("report", report_node)]:
        g.add_node(name, fn)
    g.add_edge(START, "load_context")
    g.add_edge("load_context", "plan")
    g.add_conditional_edges("plan", _route, {"act": "act", "ask_human": "ask_human"})
    g.add_edge("act", "observe")
    g.add_conditional_edges("observe", _route, {"act": "act", "replan": "replan",
                                                "ask_human": "ask_human", "verify": "verify"})
    g.add_conditional_edges("replan", _route, {"act": "act", "ask_human": "ask_human"})
    g.add_conditional_edges("ask_human", _route, {"act": "act", "plan": "plan", "replan": "replan", "report": "report"})
    g.add_conditional_edges("verify", _route, {"report": "report", "replan": "replan"})
    g.add_edge("report", END)
    return g.compile(checkpointer=MemorySaver())
