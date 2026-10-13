---
description: Approval thresholds and what the AI may and may not do
approval_threshold: 5000
allowed_urls: http://127.0.0.1:5001
allowed_paths: sandbox/data
---
# Policies
- Invoices with an amount above the approval threshold (USD 5,000) need recorded human approval before they are submitted. The system blocks the submit until approval is recorded.
- If the vendor name is ambiguous (matches more than one vendor), ask the human which one. Never guess.
- If several invoices could be "the latest" (same date), ask the human.
- The AI may only open URLs on the allow-list and read files under the allowed paths.
- The AI must never submit the same invoice twice. If the ERP reports a duplicate, stop and report it.
- The AI must not change or delete existing ERP records.
