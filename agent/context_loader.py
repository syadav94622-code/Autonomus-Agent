"""Context as files: builds an index (filename + one-line description) and loads selected docs."""
from pathlib import Path

from agent.state import ROOT

CONTEXT_DIR = ROOT / "context"
ALWAYS_LOAD = ["company.md", "policies.md"]   # baseline safety/context docs


def parse_front_matter(text: str) -> dict:
    meta = {}
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
    return meta


def build_index() -> list[dict]:
    index = []
    for p in sorted(CONTEXT_DIR.rglob("*.md")):
        meta = parse_front_matter(p.read_text(encoding="utf-8"))
        index.append({"file": p.relative_to(CONTEXT_DIR).as_posix(),
                      "description": meta.get("description", "")})
    return index


def index_text(index: list[dict]) -> str:
    return "\n".join(f"- {i['file']}: {i['description']}" for i in index)


def load_docs(names: list[str]) -> str:
    valid = {i["file"] for i in build_index()}
    chosen = list(dict.fromkeys(ALWAYS_LOAD + [n for n in names if n in valid]))
    chunks = []
    for n in chosen:
        if n in valid:
            body = (CONTEXT_DIR / n).read_text(encoding="utf-8")
            chunks.append(f"=== {n} ===\n{body.strip()}")
    return "\n\n".join(chunks)
