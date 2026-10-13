"""Three deterministic checks (no LLM). All must pass before the task can complete."""
import re
from datetime import date, datetime
from decimal import Decimal

from agent import state as S
from agent.policy import parse_date, to_decimal
from agent.tools import read_text_file

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def dates_in_text(text: str) -> set:
    found = set()
    for y, m, d in re.findall(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
        found.add(_d(int(y), int(m), int(d)))
    for m, d, y in re.findall(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text):
        found.add(_d(int(y), int(m), int(d)))
    for d, mon, y in re.findall(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b", text):
        if mon[:3].lower() in MONTHS:
            found.add(_d(int(y), MONTHS[mon[:3].lower()], int(d)))
    for mon, d, y in re.findall(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})\b", text):
        if mon[:3].lower() in MONTHS:
            found.add(_d(int(y), MONTHS[mon[:3].lower()], int(d)))
    found.discard(None)
    return found


def _d(y, m, d):
    try:
        return date(y, m, d)
    except ValueError:
        return None


def amounts_in_text(text: str) -> set:
    out = set()
    for tok in re.findall(r"\d[\d,]*\.\d{2}\b|\b\d+\b", text):
        try:
            out.add(Decimal(tok.replace(",", "")))
        except Exception:  # noqa: BLE001
            pass
    return out


def _result(passed: bool, detail: str) -> dict:
    return {"passed": bool(passed), "detail": detail}


async def _read_record(url: str, fields: list[str], shot_name: str):
    """Open the saved record in a fresh tab and read the given #f-<field> cells."""
    r = S.RUN
    page = await r.browser.context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded")
        values = {}
        for f in fields:
            loc = page.locator(f"#f-{f}")
            values[f] = (await loc.first.inner_text()).strip() if await loc.count() else None
        shot = str(r.run_dir / shot_name)
        await page.screenshot(path=shot, full_page=True)
        r.screenshots.append(shot)
        return values, shot
    finally:
        await page.close()


async def check_record(facts: dict) -> dict:
    url, rtype = facts.get("record_url"), facts.get("record_type")
    if not url:
        return _result(False, "No saved record found (nothing was submitted successfully).")
    if rtype == "invoice":
        vals, shot = await _read_record(url, ["vendor", "invoice_number", "amount", "due_date"], "verify_record.png")
        problems = []
        if (vals["vendor"] or "").casefold() != facts.get("vendor", "").casefold():
            problems.append(f"vendor ERP={vals['vendor']!r} facts={facts.get('vendor')!r}")
        try:
            if to_decimal(vals["amount"]) != to_decimal(facts.get("amount")):
                problems.append(f"amount ERP={vals['amount']} facts={facts.get('amount')}")
        except ValueError:
            problems.append("amount not numeric")
        if parse_date(vals["due_date"] or "") != parse_date(facts.get("due_date", "")):
            problems.append(f"due_date ERP={vals['due_date']} facts={facts.get('due_date')}")
        if facts.get("invoice_number") and vals["invoice_number"] != facts["invoice_number"]:
            problems.append(f"invoice_number ERP={vals['invoice_number']} facts={facts['invoice_number']}")
        return _result(not problems, "; ".join(problems) or f"ERP record matches facts ({vals}). Evidence: {shot}")
    if rtype == "po_receive":
        vals, shot = await _read_record(url, ["vendor", "po_number", "status"], "verify_record.png")
        problems = []
        if (vals["vendor"] or "").casefold() != facts.get("vendor", "").casefold():
            problems.append(f"vendor ERP={vals['vendor']!r} facts={facts.get('vendor')!r}")
        if vals["po_number"] != facts.get("po_number"):
            problems.append(f"po_number ERP={vals['po_number']} facts={facts.get('po_number')}")
        if vals["status"] != "received":
            problems.append(f"status is {vals['status']!r}, expected 'received'")
        return _result(not problems, "; ".join(problems) or f"PO shows received ({vals}). Evidence: {shot}")
    return _result(False, f"Unknown record_type {rtype!r}")


def check_source(facts: dict) -> dict:
    """Re-read the source document and confirm the extracted values literally appear in it."""
    rtype, src = facts.get("record_type"), facts.get("source_file")
    if not src:
        return _result(False, "No source_file recorded; cannot confirm the extraction.")
    try:
        text = read_text_file(src)
    except Exception as e:  # noqa: BLE001
        return _result(False, f"could not re-read {src}: {e}")
    problems = []
    if (facts.get("vendor") or "").casefold() not in text.casefold():
        problems.append("vendor name not in source")
    if rtype == "invoice":
        try:
            if to_decimal(facts["amount"]) not in amounts_in_text(text):
                problems.append(f"amount {facts['amount']} not in source")
        except (KeyError, ValueError):
            problems.append("amount missing or not numeric")
        due = parse_date(facts.get("due_date", ""))
        if due is None or due not in dates_in_text(text):
            problems.append(f"due date {facts.get('due_date')} not in source")
        if facts.get("invoice_number") and facts["invoice_number"] not in text:
            problems.append(f"invoice number {facts['invoice_number']} not in source")
    elif rtype == "po_receive":
        if not facts.get("po_number") or facts["po_number"] not in text:
            problems.append(f"PO number {facts.get('po_number')} not in source")
    else:
        problems.append(f"unknown record_type {rtype!r}")
    return _result(not problems, "; ".join(problems) or f"Extracted values appear literally in {src}")


def check_side_effects(facts: dict) -> dict:
    r = S.RUN
    ok_subs = [s for s in r.submissions if s["ok"]]
    stats = S.fetch_stats()
    problems = []
    if len(ok_subs) != 1:
        problems.append(f"{len(ok_subs)} successful submissions this run (expected exactly 1)")
    rtype = facts.get("record_type")
    if rtype == "invoice":
        delta = stats["invoices"] - r.baseline.get("invoices", 0)
        if delta != 1:
            problems.append(f"ERP invoice count changed by {delta} (expected +1)")
        for s in ok_subs:
            if r.policy.requires_approval("invoice", s["amount"]):
                prior = [a for a in r.approvals if a["ts"] < s["ts"] and
                         a["vendor"].casefold() == s["vendor"].casefold() and
                         to_decimal(a["amount"]) == to_decimal(s["amount"])]
                if not prior:
                    problems.append("approval required but none logged before the submit")
    elif rtype == "po_receive":
        delta = stats["pos_received"] - r.baseline.get("pos_received", 0)
        if delta != 1:
            problems.append(f"received POs changed by {delta} (expected +1)")
    else:
        problems.append("no record type")
    return _result(not problems, "; ".join(problems) or "Exactly one new record; approval ordering OK")


async def run_checks(facts: dict) -> dict:
    return {"record_check": await check_record(facts),
            "source_check": check_source(facts),
            "side_effect_check": check_side_effects(facts)}
