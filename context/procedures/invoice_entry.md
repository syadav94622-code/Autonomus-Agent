---
description: Step-by-step procedure for entering a vendor invoice into the ERP
---
# Procedure: enter a vendor invoice
1. Confirm the vendor with `lookup_vendor`. If it reports AMBIGUOUS, ask the human (needs_human) and do not continue.
2. Call `find_latest_document(kind="invoice", vendor=<canonical name>)`. It searches invoice PDFs and invoice emails and
   picks the latest by the date printed inside each file. If it reports AMBIGUOUS (several share the latest date), ask the human.
3. `read_file` the returned source_file and extract: invoice_number, amount (Amount Due, numeric only), due_date. Report them in the step result facts.
4. Open `/invoices/new`.
5. Fill vendor, invoice number, amount, due date using `fill_form`, then call `submit_record` with record_type="invoice".
6. If the ERP shows a validation error, read the message, correct the field and resubmit. On HTTP 500 / Internal Server Error the save did not happen: goto /invoices/new again (the page has no form elements) and call submit_record once more with the SAME values. Do not change formats or values.
7. Success is a flash message and the invoice detail page. Take a screenshot.
Never click the form's submit button directly; always use `submit_record`.
