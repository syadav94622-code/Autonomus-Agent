"""Policy enforced in code (values loaded from context/policies.md). The prompt is not trusted."""
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse

from agent.context_loader import CONTEXT_DIR, parse_front_matter
from agent.state import ROOT


def to_decimal(value) -> Decimal:
    s = re.sub(r"[^\d.\-]", "", str(value))
    try:
        return Decimal(s)
    except InvalidOperation:
        raise ValueError(f"not a number: {value!r}")


def parse_date(s: str):
    """Parse the date formats seen in documents and the ERP; None if unparseable."""
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


@dataclass
class Policy:
    approval_threshold: Decimal
    allowed_urls: list
    allowed_paths: list

    @classmethod
    def load(cls) -> "Policy":
        meta = parse_front_matter((CONTEXT_DIR / "policies.md").read_text(encoding="utf-8"))
        return cls(
            approval_threshold=Decimal(meta.get("approval_threshold", "5000")),
            allowed_urls=[u.strip() for u in meta.get("allowed_urls", "").split(",") if u.strip()],
            allowed_paths=[(ROOT / p.strip()).resolve() for p in meta.get("allowed_paths", "").split(",") if p.strip()],
        )

    def check_url(self, url: str):
        """Return an error string if blocked, else None."""
        u = urlparse(url)
        if not any((u.scheme, u.netloc) == (a.scheme, a.netloc) and u.path.startswith(a.path)
                   for a in map(urlparse, self.allowed_urls)):
            return f"BLOCKED by policy: URL not on allow-list ({', '.join(self.allowed_urls)})"
        return None

    def resolve_path(self, path: str) -> Path:
        p = (ROOT / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
        if not any(p == a or a in p.parents for a in self.allowed_paths):
            raise PermissionError("BLOCKED by policy: path outside allowed folders")
        return p

    def requires_approval(self, record_type: str, amount) -> bool:
        return record_type == "invoice" and to_decimal(amount) > self.approval_threshold

    @staticmethod
    def approved(approvals: list, vendor: str, amount) -> bool:
        for a in approvals:
            if a.get("vendor", "").casefold() == vendor.casefold() and to_decimal(a["amount"]) == to_decimal(amount):
                return True
        return False
