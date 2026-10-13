"""Graph state (AgentState) and the per-run runtime context shared by tools, policy and verify."""
import json
import operator
import os
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Annotated, Any, Optional, TypedDict

ROOT = Path(__file__).resolve().parent.parent
SANDBOX_URL = os.getenv("SANDBOX_URL", "http://127.0.0.1:5001")
ERP_USERNAME = os.getenv("ERP_USERNAME", "demo")        # demo credentials; used only by Browser.ensure_logged_in
ERP_PASSWORD = os.getenv("ERP_PASSWORD", "demo123")
MAX_REPLANS = 3
MAX_TOOL_CALLS_PER_STEP = 8


class AgentState(TypedDict, total=False):
    goal: str                                   # the user's request
    context_docs: str                           # loaded document text
    plan: dict                                  # current Plan (model_dump)
    current_step: int                           # index into plan["steps"]
    facts: dict                                 # vendor, amount, due date, record id ...
    step_log: Annotated[list, operator.add]     # {step, action, observation}
    replans: int                                # capped at MAX_REPLANS
    approvals: Annotated[list, operator.add]    # recorded human approvals
    verification: dict                          # results of the three checks
    report: dict                                # final summary + evidence paths
    # control fields used for routing
    route: str
    last_result: dict
    failure: str
    pending: Optional[dict]                     # {"kind", "question", "meta"} awaiting a human
    human_answers: Annotated[list, operator.add]
    aborted: bool


class RunContext:
    """Per-run mutable context. Tools and policy write here; the graph syncs it into state."""

    def __init__(self, goal: str, run_id: Optional[str] = None):
        self.goal = goal
        self.run_id = run_id or time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        self.run_dir = ROOT / "runs" / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.audit_path = self.run_dir / "audit.jsonl"
        self.log_path = self.run_dir / "operator.log"      # full DEBUG trace of this run
        self.facts: dict = {}
        self.approvals: list = []
        self.submissions: list = []
        self.files_read: list = []
        self.human_answers: list = []
        self.interrupts: list = []
        self.pending: Optional[dict] = None   # set by policy-blocked tools
        self.baseline: dict = {}
        self.screenshots: list = []
        self.browser: Any = None
        self.policy: Any = None

    def audit(self, event: str, **data):
        rec = {"ts": time.time(), "event": event, **data}
        with open(self.audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")


RUN: Optional[RunContext] = None


def new_run(goal: str, run_id: Optional[str] = None) -> RunContext:
    global RUN
    RUN = RunContext(goal, run_id)
    return RUN


def fetch_stats() -> dict:
    with urllib.request.urlopen(SANDBOX_URL + "/api/stats", timeout=5) as r:
        return json.loads(r.read())
