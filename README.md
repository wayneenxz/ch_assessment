# Coinhako Financial Data Reconciliation

## Run

```bash
python reconcile.py --input-dir ./reconciliation_data --output-dir ./output
```

Requires Python 3.10+ with `pandas` and `numpy`.

## Deliverables

- `METHOD.md` — written method and trustworthiness rationale.
- `reconcile.py` — deterministic reconciliation pipeline.
- `output/trusted_dataset.csv` — required trusted dataset schema: `date, asset, open, high, low, close, volume_base, source, confidence`.
- `output/breaks_report.csv` — detailed discrepancy log with severity, action and likely cause.
- `output/break_summary.csv` — aggregated break counts for stakeholder scanning.
- `output/qa_summary.csv` — source-level row, duplicate, coverage and validity controls.
- `output/control_totals.csv` — final trusted-output control checks.
- `WALKTHROUGH_PREP.md` — interview defense notes and likely follow-up questions.

## Notes

The raw data is intentionally not copied into this folder. Point `--input-dir` at the provided `reconciliation_data` directory.
