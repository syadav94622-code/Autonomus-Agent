"""Generate sample invoice/PO PDFs and emails (no third-party deps). Run: python sandbox/make_data.py"""
from pathlib import Path

DATA = Path(__file__).parent / "data"


def _esc(s):
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(lines):
    content = "BT /F1 12 Tf 14 TL 60 760 Td\n" + "\n".join(f"({_esc(l)}) Tj T*" for l in lines) + "\nET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs)+1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


# vendor, slug, invoice no, invoice date, due date, amount, description
INVOICES = [
    ("Acme Corp", "acme-corp", "INV-1042", "18 Dec 2025", "17 Jan 2026", "1,250.00", "Quarterly maintenance"),
    ("Acme Corp", "acme-corp", "INV-1057", "22 Jan 2026", "21 Feb 2026", "980.40", "Replacement parts"),
    ("Acme Corp", "acme-corp", "INV-1071", "27 Feb 2026", "29 Mar 2026", "3,480.00", "Industrial supplies, February"),
    ("Acme Corporation Ltd", "acme-corporation-ltd", "INV-0311", "03 Feb 2026", "05 Mar 2026", "2,150.00", "Consulting hours"),
    ("Acme Corporation Ltd", "acme-corporation-ltd", "INV-0329", "10 Mar 2026", "09 Apr 2026", "7,820.50", "Equipment lease, March"),
    ("Globex Inc", "globex-inc", "INV-5510", "14 Feb 2026", "16 Mar 2026", "640.00", "Packaging"),
    ("Initech LLC", "initech-llc", "INV-0088", "05 Jan 2026", "04 Feb 2026", "410.25", "Software licences"),
    # same invoice date as INV-0088 -> "latest" is ambiguous, a human must choose
    ("Initech LLC", "initech-llc", "INV-0089", "05 Jan 2026", "11 Feb 2026", "515.75", "Support plan"),
]
# Invoices that arrive as emails (the invoice source is PDFs AND emails)
EMAIL_INVOICES = [
    ("Globex Inc", "globex-inc", "INV-5602", "20 Mar 2026", "19 Apr 2026", "1,125.00", "Gadget shipment"),
]
POS = [
    ("Globex Inc", "globex-inc", "PO-7001", "05 Jan 2026", "Widgets x100"),
    ("Globex Inc", "globex-inc", "PO-7002", "20 Feb 2026", "Widgets x250"),
    ("Globex Inc", "globex-inc", "PO-7003", "12 Mar 2026", "Gadgets x40"),
    ("Acme Corp", "acme-corp", "PO-4410", "01 Mar 2026", "Industrial supplies"),
    ("Initech LLC", "initech-llc", "PO-9100", "02 Feb 2026", "Licences x20"),
]


def main():
    for d in ("invoices", "purchase_orders", "emails"):
        (DATA / d).mkdir(parents=True, exist_ok=True)
    for v, slug, no, idate, due, amt, desc in INVOICES:
        lines = ["INVOICE", f"Vendor: {v}", f"Invoice No: {no}", f"Invoice Date: {idate}",
                 f"Due Date: {due}", f"Description: {desc}", f"Amount Due: USD {amt}",
                 "Remit to: accounts@" + slug + ".example"]
        (DATA / "invoices" / f"{slug}_{no}.pdf").write_bytes(make_pdf(lines))
    for v, slug, no, idate, due, amt, desc in EMAIL_INVOICES:
        body = (f"From: billing@{slug}.example\nTo: ap@centralign.example\nSubject: Invoice {no} from {v}\n\n"
                f"Hello, please find our invoice details below.\n\nVendor: {v}\nInvoice No: {no}\n"
                f"Invoice Date: {idate}\nDue Date: {due}\nDescription: {desc}\nAmount Due: USD {amt}\n\nThanks")
        (DATA / "emails" / f"{slug}_{no}.txt").write_text(body)
    for v, slug, no, odate, desc in POS:
        lines = ["PURCHASE ORDER", f"Vendor: {v}", f"PO Number: {no}", f"Order Date: {odate}",
                 f"Items: {desc}", "Buyer: CentrAlign Procurement"]
        (DATA / "purchase_orders" / f"{slug}_{no}.pdf").write_bytes(make_pdf(lines))
    (DATA / "emails" / "acme_followup.txt").write_text(
        "From: billing@acme.example\nSubject: Reminder - February invoice\n\n"
        "Hi, just a reminder that our latest invoice is attached to the usual folder. Thanks, Acme billing\n")
    (DATA / "emails" / "globex_shipping.txt").write_text(
        "From: ops@globex.example\nSubject: PO shipped\n\n"
        "Your most recent purchase order has shipped and should arrive this week.\n")
    print("Sample data written to", DATA)


if __name__ == "__main__":
    main()
