---
description: Procedure for marking a purchase order as received in the ERP
---
# Procedure: mark a purchase order received
1. Confirm the vendor with `lookup_vendor` (ask the human if AMBIGUOUS).
2. Call `find_latest_document(kind="po", vendor=<canonical name>)` to get the latest PO number and its source_file.
3. `read_file` the source_file to confirm the PO number.
4. Open `/pos`, find that PO and open its detail page (it must be open, not already received).
5. Call `submit_record` with record_type="po_receive", vendor and po_number (this presses "Mark as received").
6. Take a screenshot of the result.
