# CentrAlign AI Operator (prototype)

LangGraph + LangChain 1.x (`create_agent`) + Playwright + an official LLM provider (Anthropic by default). A CLI operator that takes a short company
request, completes it with real tools (files + a real browser + a mock ERP), asks for approval/clarification
when policy demands it, retries/replans on failure, and verifies the result with deterministic checks.

```
load_context -> plan -> act -> observe -> (act | replan | ask_human | verify) -> report
```

## Setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env        # add ANTHROPIC_API_KEY (and delete any old OPERATOR_MODEL=llama... line)
python sandbox/make_data.py # (sample PDFs are already included; re-run to regenerate)
```

## Run
```bash
python main.py                                   # demo task (Acme invoice); auto-starts the mock ERP
python main.py "Mark the latest PO from Globex as received."
python main.py --headed "..."                    # watch the browser
python main.py --debug "..."                     # full prompts/responses/tool outputs on the console
```
Manual sandbox: `python sandbox/app.py --reset` (http://127.0.0.1:5001, login demo / demo123).
Runs write `runs/<id>/audit.jsonl`, screenshots, `report.md` and `report.json`.

## Debugging: see what the LLM is doing
Every model request/response and every tool call is logged.
- Console (INFO): one line per LLM request, per LLM response (`TOOL CALL name(args)` or a loud
  `NO TOOL CALL` warning), per tool call and tool result, plus graph node transitions, the plan, replans and verify results.
- `--debug` (or `LOG_LEVEL=DEBUG`): the same with full prompts, full responses and full tool outputs on the console.
- `runs/<run_id>/operator.log`: **always** the full DEBUG trace of that run. `runs/sandbox.log`: the mock ERP's output.
- Labels in `[brackets]` tell you who is talking: `select_context`, `plan`, `replan`, `actor.step3`, `actor.step3.fallback`.

## How "the LLM always calls a tool" is enforced
- **Planner / context selector:** the schema is bound as a tool with `tool_choice=<that tool>` (`agent/llm.py`).
  A plain-text reply (or a provider "tool_use_failed" error) is logged and retried with a nudge.
- **Executor = `langchain.agents.create_agent`** (`agent/actor.py`): `response_format=ToolStrategy(StepResult)` makes
  create_agent send `tool_choice="any"` on every turn, so the model must call a tool each turn; the step ends only when it
  calls the `StepResult` tool. `StepGuard` middleware enforces the per-step call budget (`MAX_TOOL_CALLS_PER_STEP`, 8) and
  the loop detector in code: when the budget is spent the agent loop is STOPPED and the result comes from one forced
  `StepResult` call built from a plain-text transcript (no tool-call history to imitate). Provider rejections such as
  Groq's `tool_use_failed` are retried with a nudge and never crash the run.
- **Executor context:** the actor sees only `company.md` + `policies.md`; procedures go to the planner, which must write
  self-contained steps. The actor has no `remember` tool (tools record facts in code; `StepResult.facts` carries the rest).
- `python tests/test_offline.py` proves this against a scripted fake Anthropic API (no key, no cost).

## Evals
```bash
python evals/run_evals.py            # 6 cases: ambiguity, recovery, approval, verification (email source), tie_invoices, generalization
python evals/run_evals.py approval   # one case
```
Each case resets the sandbox, feeds scripted human answers, and asserts final app state.

## How the doc's requirements map to code
| Requirement | Where |
|---|---|
| Planner / Actor / Verifier roles | `planner.py`, `actor.py` (create_agent), `verify.py` |
| Context as files, LLM-selected | `context/*.md`, `context_loader.py`, `planner.select_context` |
| Structured outputs (forced tool calls) | `schemas.py`, `llm.structured_call` |
| Logging / tracing | `log.py` (`LLMTraceHandler`), `actor.StepGuard`, `graph.py` |
| Graph, checkpointer, `interrupt()` | `graph.py` (`MemorySaver`) |
| Max 8 tool calls/step, loop detector, 1 direct retry | `actor.py`, `graph.act_node` |
| Replan cap (3) then escalate to human | `graph.replan_node` |
| Policy in code (threshold, allow-list, gated submit) | `policy.py`, `tools.submit_record`, `browser.click` |
| 3 deterministic checks | `verify.py` |
| Sandbox with injected faults | `sandbox/app.py` |

## Design notes / deviations
- **Gated submit:** the form's submit button is tagged `data-role="submit-record"`; `click` refuses it, only
  `submit_record` can press it, after the policy gate passes. Approval-required and ambiguous-vendor
  situations are detected in code (`RUN.pending`) and override whatever the LLM reports.
- **Extra tools beyond the doc:** `fill_form` (several fields in one call, to fit the call budget),
  `lookup_vendor` (alias matching against `vendors.md`; detects the two "Acme" vendors) and
  `find_latest_document` (searches invoice PDFs *and* emails, picks the latest by the date inside the file).
  The model still reads the file and extracts amount/due date itself.
- **Code-enforced "latest" and ties:** `submit_record` re-scans the source documents; it blocks an invoice that is
  not the latest, and a tie on the latest date triggers `needs_human` (sample: two Initech invoices dated 05 Jan 2026).
  It also records `source_file` itself, so the source check never depends on the model remembering it.
- **Always-loaded docs:** `company.md` and `policies.md` are always injected; the Planner selects the rest.
- **Run context:** tools/policy/verify share a per-run `RunContext` (`agent/state.py`), synced into
  `AgentState` by `observe`. This is single-run/in-process by design.
- **Strict source check:** requires `source_file`, then confirms vendor, invoice number, amount and due date
  appear literally in that file (no fallback to "any file read").
- **Verification is generic:** invoice and PO workflows share the same three checks, driven by
  `record_type` (`invoice` | `po_receive`) set by `submit_record`.
- **Injected faults:** form hints `MM/DD/YYYY` but the server requires `YYYY-MM-DD`; first valid POST
  returns a one-time HTTP 500 (nothing saved); duplicates are rejected. Disable with `SANDBOX_FAULTS=0`.
- **Model:** `LLM_PROVIDER=anthropic` (default model `claude-sonnet-5-5`), or `openai` / `google` / `xai` with `OPERATOR_MODEL` set.
  Temperature is not set by default (newer models reject non-default values); opt in with `OPERATOR_TEMPERATURE`.
- **Windows:** all file IO is explicit UTF-8 (`vendors.md` contains an em dash that cp1252 would mangle).

## Known limitations
One task at a time; browser + files only; LLM-based context selection (small doc set); per-run memory;
text-only model (no visual UI understanding). Structured calls are retried twice on plain-text/invalid replies.
