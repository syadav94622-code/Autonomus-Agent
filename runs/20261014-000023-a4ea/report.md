# Operator report: DONE

**Goal:** Enter the latest invoice from Globex Inc into the internal system.
**Run:** 20261014-000023-a4ea

## Facts
- vendor: Globex Inc
- amount: 1125.00
- due_date: 2026-04-19
- invoice_date: 2026-03-20
- invoice_number: INV-5602
- source_file: sandbox/data/emails/globex-inc_INV-5602.txt
- amount_due: 1125.00
- record_type: invoice
- record_id: 1
- record_url: http://127.0.0.1:5001/invoices/1

## Verification
- PASS record_check: ERP record matches facts ({'vendor': 'Globex Inc', 'invoice_number': 'INV-5602', 'amount': '1125.00', 'due_date': '2026-04-19'}). Evidence: D:\centralign-operator\runs\20261014-000023-a4ea\verify_record.png
- PASS source_check: Extracted values appear literally in sandbox/data/emails/globex-inc_INV-5602.txt
- PASS side_effect_check: Exactly one new record; approval ordering OK

## Approvals / human input
- none

Replans used: 0

## Evidence
- D:\centralign-operator\runs\20261014-000023-a4ea\submit_1.png
- D:\centralign-operator\runs\20261014-000023-a4ea\submit_2.png
- D:\centralign-operator\runs\20261014-000023-a4ea\invoice_globex_inc.png
- D:\centralign-operator\runs\20261014-000023-a4ea\verify_record.png
- audit log: D:\centralign-operator\runs\20261014-000023-a4ea\audit.jsonl