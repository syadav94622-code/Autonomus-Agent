---
description: Company overview, systems and URLs, naming conventions
---
# CentrAlign (sandbox company)
We are a small company that processes vendor invoices and purchase orders.

## Systems
- Internal ERP (mock): http://127.0.0.1:5001
  - The browser session is already logged in. Never log in; if you land on /login the tools re-authenticate automatically.
  - Vendors: /vendors, Invoices: /invoices, New invoice: /invoices/new
  - Purchase orders: /pos, PO detail: /pos/<id>
- Source documents are local files under `sandbox/data/`:
  - `sandbox/data/invoices/`  vendor invoice PDFs, named `<vendor-slug>_<invoice-no>.pdf`
  - `sandbox/data/purchase_orders/`  PO PDFs, named `<vendor-slug>_<po-no>.pdf`
  - `sandbox/data/emails/`  vendor emails, named `<vendor-slug>_<invoice-no>.txt`; some invoices arrive only as emails
    (same fields as the PDF invoices: Invoice No, Invoice Date, Due Date, Amount Due)

## Conventions
- Amounts are entered as plain numbers with two decimals, no currency symbol or commas (e.g. 3480.00).
- Dates: the ERP requires YYYY-MM-DD even though the form label says MM/DD/YYYY.
- Always use the vendor's canonical name from the vendor list.
- "Latest" means the most recent date printed inside the document, not the file name or file time.
