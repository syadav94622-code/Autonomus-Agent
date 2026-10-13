from typing import Literal, Optional

from pydantic import BaseModel, Field


class ContextSelection(BaseModel):
    """Select which company context documents are needed for the task (may be empty for non-tasks)."""
    docs: list[str] = Field(description="Relative paths of the context docs to load, from the index")
    reason: str = Field(description="One sentence on why these docs were chosen")


class Step(BaseModel):
    id: int
    description: str = Field(description="One small, concrete action (at most ~5 tool calls)")
    tool_hint: str = Field(description="Which tools are likely needed")
    success_criteria: str = Field(description="Observable condition that shows the step worked")
    needs_approval: bool = False


class Plan(BaseModel):
    """The ordered plan for the goal. Use an empty steps list plus open_questions when the goal is not an actionable task."""
    objective: str
    steps: list[Step]
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class StepResult(BaseModel):
    """FINAL tool of a step: call it ALONE, after you have seen the results of your other tool calls, to report the outcome."""
    status: Literal["done", "failed", "needs_human"]
    facts: dict[str, str] = Field(default_factory=dict, description="Values discovered in this step")
    note: str = ""
    question: Optional[str] = None
