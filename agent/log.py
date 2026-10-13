"""Logging: everything the LLM sees, says and calls is visible here.

Two sinks:
  * console  -> INFO by default (one line per LLM call / tool call / decision); DEBUG with --debug
  * runs/<id>/operator.log -> ALWAYS DEBUG (full prompts, full responses, full tool outputs)

Set the level with `--log-level DEBUG` / `--debug` on the CLI or LOG_LEVEL=DEBUG in .env.
"""
import contextvars
import itertools
import logging
import sys
import time
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

ROOT_LOGGER = "operator"
current_label: contextvars.ContextVar[str] = contextvars.ContextVar("llm_label", default="llm")

_FMT = "%(asctime)s %(levelname)-7s %(name)-16s| %(message)s"
_DATEFMT = "%H:%M:%S"


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{ROOT_LOGGER}.{name}")


def clip(text: Any, n: int = 400, oneline: bool = True) -> str:
    s = str(text).replace("\r", "")
    if oneline:
        s = s.replace("\n", " // ")      # keep console output to one line per event
    return s if len(s) <= n else s[:n] + f"...[+{len(s) - n} chars]"


def setup_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    """Idempotent: safe to call once per run (re-attaches the file handler for the new run dir)."""
    try:  # Windows consoles default to cp1252; avoid UnicodeEncodeError when logging model output
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    root = logging.getLogger(ROOT_LOGGER)
    root.setLevel(logging.DEBUG)          # handlers decide what is shown
    root.propagate = False
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    console.setFormatter(logging.Formatter(_FMT, _DATEFMT))
    root.addHandler(console)
    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(_FMT, _DATEFMT))
        root.addHandler(fh)
    # third-party chatter (httpx prints every request at INFO)
    noisy = logging.INFO if level.upper() == "DEBUG" else logging.WARNING
    for name in ("httpx", "httpcore", "anthropic", "openai", "langchain", "langgraph", "urllib3"):
        logging.getLogger(name).setLevel(noisy)


# ---------------------------------------------------------------------------------------------
def message_text(msg) -> str:
    """Plain text of a message (tool_use blocks are NOT text; they are logged separately as tool calls)."""
    t = getattr(msg, "text", None)
    if callable(t) and not isinstance(t, str):   # old langchain-core: .text() is a method
        t = t()
    if isinstance(t, str):                       # new langchain-core: .text is a str-like property
        return str(t)
    c = getattr(msg, "content", "")
    return c if isinstance(c, str) else ""


def _fmt_calls(tool_calls) -> str:
    return "; ".join(f"{tc['name']}({clip(tc.get('args'), 300)})" for tc in tool_calls)


class LLMTraceHandler(BaseCallbackHandler):
    """Attached to the chat model itself, so EVERY call (selector, planner, actor, retries) is traced."""
    run_inline = True       # keep log lines in order

    def __init__(self):
        self.log = get_logger("llm")
        self._count = itertools.count(1)
        self._start: dict = {}

    # ---- request ----
    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        n, label = next(self._count), current_label.get()
        self._start[run_id] = (n, label, time.time())
        msgs = messages[0]
        self.log.info("[%s #%d] -> request: %d messages | last=%s: %s", label, n, len(msgs),
                      msgs[-1].type, clip(message_text(msgs[-1]), 300))
        if self.log.isEnabledFor(logging.DEBUG):
            for m in msgs:
                self.log.debug("[%s #%d]    %s: %s", label, n, m.type.upper(), clip(message_text(m), 6000, oneline=False))
                for tc in getattr(m, "tool_calls", None) or []:
                    self.log.debug("[%s #%d]       tool_call: %s", label, n, _fmt_calls([tc]))

    # ---- response ----
    def on_llm_end(self, response, *, run_id, **kwargs):
        n, label, t0 = self._start.pop(run_id, (0, current_label.get(), time.time()))
        gen = response.generations[0][0]
        msg = getattr(gen, "message", None)
        calls = list(getattr(msg, "tool_calls", None) or [])
        usage = getattr(msg, "usage_metadata", None) or {}
        meta = f"{time.time() - t0:.1f}s in={usage.get('input_tokens', '?')} out={usage.get('output_tokens', '?')}"
        text = message_text(msg) if msg is not None else gen.text
        if calls:
            self.log.info("[%s #%d] <- TOOL CALL %s | %s", label, n, _fmt_calls(calls), meta)
            if text.strip():
                self.log.info("[%s #%d]    reasoning text: %s", label, n, clip(text, 400))
        else:
            self.log.warning("[%s #%d] <- NO TOOL CALL (model answered in plain text) | %s | text=%s",
                             label, n, meta, clip(text, 500))
        if self.log.isEnabledFor(logging.DEBUG):
            self.log.debug("[%s #%d]    full response: %s | tool_calls=%s", label, n, text, calls)

    def on_llm_error(self, error, *, run_id, **kwargs):
        n, label, t0 = self._start.pop(run_id, (0, current_label.get(), time.time()))
        self.log.error("[%s #%d] <- LLM ERROR after %.1fs: %s: %s", label, n, time.time() - t0,
                       type(error).__name__, clip(error, 1500))
