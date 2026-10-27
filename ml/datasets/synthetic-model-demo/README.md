# Separate synthetic ML demonstration

Generated data only, not real business records.

- purchase/purchase-lines.csv: complete simulated purchases
- sales/sales-lines.csv: complete simulated invoice lines
- sales/complete-daily-sales.csv: all generated product/day observations
- Each direction has ten sample invoice PDFs, with source-style table headers.
- results/evaluation.json: actual measured scores, baseline comparisons, provenance and limitations
- results/test-predictions.csv: actual versus predicted held-out rows
- results/next-month-product-plan.csv: synthetic October 2026 quantity, price, revenue and stock plan
- results/*-model.json: portable fitted models and candidate comparisons
- results/stock-ledger.csv: isolated simulation stock movements

Reproduce with the project backend virtual-environment Python and code/run_synthetic_demo.py. The script imports the existing project trainer from /path/to/autonomous-business-os but never calls its main() or updates real artifacts. Only the synthetic demonstration is trained. Synthetic metrics cannot be claimed as real-data accuracy. Metrics are errors, not generic accuracy percentages. This run does not add a live website mode.
