"""LLM factory (official provider SDKs via LangChain) + forced-tool structured calls.

Why forced tools: the Planner/selector must ALWAYS answer by calling a tool. We bind the schema as a
tool and set tool_choice to that tool's name, so the provider must emit a tool call. If a model still
replies in plain text (or the provider rejects the call), we log it loudly and retry with a nudge.
"""
import os

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, ToolMessage
from pydantic import ValidationError

from agent.log import LLMTraceHandler, clip, current_label, get_logger, message_text

log = get_logger("llm")

# provider -> (init_chat_model provider id, API-key env var, default model or None)
PROVIDERS = {
    "groq": ("groq", "GROQ_API_KEY", "openai/gpt-oss-120b"),
    "anthropic": ("anthropic", "ANTHROPIC_API_KEY", "claude-sonnet-5-5"),
    "openai": ("openai", "OPENAI_API_KEY", None),
    "google": ("google_genai", "GOOGLE_API_KEY", None),
    "xai": ("xai", "XAI_API_KEY", None),
}
REASONING_PROVIDERS = {"groq", "openai"}     # accept a reasoning_effort parameter

_TRACE = LLMTraceHandler()   # one handler => one global call counter


def provider() -> str:
    return os.getenv("LLM_PROVIDER", "anthropic").lower()


def check_credentials() -> str | None:
    """Return an error message if the configured provider is unusable, else None."""
    p = provider()
    if p not in PROVIDERS:
        return f"Unknown LLM_PROVIDER={p!r}. Choose one of: {', '.join(PROVIDERS)}"
    _, key_env, default_model = PROVIDERS[p]
    if not os.getenv(key_env):
        return f"Set {key_env} in .env (LLM_PROVIDER={p})."
    if not (os.getenv("OPERATOR_MODEL") or default_model):
        return f"Set OPERATOR_MODEL in .env (no default model for LLM_PROVIDER={p})."
    return None


def make_llm(temperature: float | None = None):
    """Chat model with the trace handler attached (every request/response is logged)."""
    p = provider()
    if p not in PROVIDERS:
        raise RuntimeError(f"Unknown LLM_PROVIDER={p!r}")
    model_provider, _, default_model = PROVIDERS[p]
    model = os.getenv("OPERATOR_MODEL") or default_model
    if not model:
        raise RuntimeError(f"Set OPERATOR_MODEL for LLM_PROVIDER={p}")
    kwargs = {"callbacks": [_TRACE]}
    # Temperature is OPT-IN: newer models (e.g. claude-sonnet-5-5) reject non-default values client-side.
    temp = os.getenv("OPERATOR_TEMPERATURE", "") if temperature is None else str(temperature)
    if temp.strip().lower() not in ("", "none"):
        kwargs["temperature"] = float(temp)
    # Reasoning models (gpt-oss on Groq, OpenAI o-series/gpt-5): low effort = much lower latency per tool turn.
    effort = os.getenv("OPERATOR_REASONING_EFFORT", "").strip().lower()
    if effort and p in REASONING_PROVIDERS:
        kwargs["reasoning_effort"] = effort
    return init_chat_model(model, model_provider=model_provider, **kwargs)


def _plain_text(ai) -> str:
    return message_text(ai).strip()


def is_tool_failure(e: Exception) -> bool:
    """Provider-side rejection of a bad/absent tool call (Groq: tool_use_failed). Retryable, not fatal."""
    s = str(e).lower()
    return ("tool_use_failed" in s or "did not call a tool" in s or "tool choice is required" in s
            or "not in request.tools" in s)


async def structured_call(schema, messages, label: str = "structured", retries: int = 2):
    """Call the model and get back a validated `schema` instance. The model is FORCED to call the
    schema's tool (tool_choice=<schema name>); plain-text answers are rejected and retried."""
    name = schema.__name__
    llm = make_llm().bind_tools([schema], tool_choice=name)
    msgs, last = list(messages), "unknown"
    token = current_label.set(label)
    try:
        for attempt in range(1, retries + 2):
            try:
                ai = await llm.ainvoke(msgs)
            except Exception as e:  # noqa: BLE001
                if not is_tool_failure(e):
                    raise                    # auth / model-not-found / rate limit: fail fast (already logged)
                last = f"provider rejected the tool call: {clip(e, 300)}"
                log.warning("[%s] attempt %d/%d: %s", label, attempt, retries + 1, last)
                msgs.append(HumanMessage(content=f"You MUST answer by calling ONLY the `{name}` tool. Call it now."))
                continue
            calls = [tc for tc in ai.tool_calls if tc["name"] == name]
            if not calls:
                last = f"model did not call `{name}` (said: {clip(_plain_text(ai), 200)!r})"
                log.warning("[%s] attempt %d/%d: %s -> retrying with a nudge", label, attempt, retries + 1, last)
                if _plain_text(ai) or ai.tool_calls:
                    msgs.append(ai)
                    for tc in ai.tool_calls:   # every tool_call needs a matching ToolMessage
                        msgs.append(ToolMessage(content=f"Wrong tool. Use `{name}`.", tool_call_id=tc["id"], name=tc["name"]))
                msgs.append(HumanMessage(content=f"Plain-text answers are not accepted. Respond ONLY by calling the `{name}` tool."))
                continue
            try:
                out = schema.model_validate(calls[0]["args"])
            except ValidationError as e:
                last = f"invalid arguments for `{name}`: {clip(e, 300)}"
                log.warning("[%s] attempt %d/%d: %s -> retrying", label, attempt, retries + 1, last)
                msgs.append(ai)
                for tc in ai.tool_calls:
                    msgs.append(ToolMessage(content=f"Invalid arguments: {e}. Call `{name}` again with corrected arguments.",
                                            tool_call_id=tc["id"], name=tc["name"]))
                continue
            log.info("[%s] structured result OK (%s)", label, name)
            return out
        raise RuntimeError(f"structured output '{name}' failed after {retries + 1} attempts: {last}")
    finally:
        current_label.reset(token)
