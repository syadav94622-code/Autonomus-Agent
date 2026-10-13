"""Final summary and evidence writer."""
import json

from agent import state as S


def build_report(state: dict) -> dict:
    r = S.RUN
    checks = state.get("verification") or {}
    if state.get("aborted"):
        status = "aborted"
    elif checks and all(c["passed"] for c in checks.values()):
        status = "completed"
    else:
        status = "failed"
    return {
        "run_id": r.run_id, "goal": state["goal"], "status": status,
        "facts": state.get("facts", {}), "checks": checks,
        "replans": state.get("replans", 0),
        "approvals": state.get("approvals", []),
        "human_answers": state.get("human_answers", []),
        "interrupts": r.interrupts,
        "submissions": [{k: v for k, v in s.items() if k != "ts"} for s in r.submissions],
        "screenshots": r.screenshots,
        "audit_log": str(r.audit_path),
    }


def render_markdown(rep: dict) -> str:
    icon = {"completed": "DONE", "aborted": "ABORTED", "failed": "NOT COMPLETED"}[rep["status"]]
    lines = [f"# Operator report: {icon}", "", f"**Goal:** {rep['goal']}", f"**Run:** {rep['run_id']}", ""]
    lines += ["## Facts"] + [f"- {k}: {v}" for k, v in rep["facts"].items()] + [""]
    lines += ["## Verification"]
    for name, c in rep["checks"].items():
        lines.append(f"- {'PASS' if c['passed'] else 'FAIL'} {name}: {c['detail']}")
    if not rep["checks"]:
        lines.append("- (verification did not run)")
    lines += ["", "## Approvals / human input"]
    lines += [f"- approval: {a['vendor']} USD {a['amount']}" for a in rep["approvals"]] or ["- none"]
    lines += [f"- asked ({h['kind']}): {h['question']} -> {h['answer']}" for h in rep["human_answers"]]
    lines += ["", f"Replans used: {rep['replans']}", "", "## Evidence"]
    lines += [f"- {p}" for p in rep["screenshots"]] + [f"- audit log: {rep['audit_log']}"]
    return "\n".join(lines)


def write_report(state: dict) -> dict:
    rep = build_report(state)
    md = render_markdown(rep)
    (S.RUN.run_dir / "report.json").write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    (S.RUN.run_dir / "report.md").write_text(md, encoding="utf-8")
    rep["summary"] = md
    S.RUN.audit("report", status=rep["status"])
    return rep
