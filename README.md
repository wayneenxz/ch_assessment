# Data recociliation

This submission reconciles daily BTCUSD and ETHUSD data from Binance, Kraken, Coinbase and an external reference feed. It publishes a traceable venue candle rather than a synthetic price and records source exceptions separately.

## Run

Use Python 3.10+ with `pandas` and `numpy` installed:

```powershell
python -m pip install -r requirements.txt
python reconcile.py --input-dir .\reconciliation_data --output-dir .\output
```

## Submission contents

- `reconcile.py` — deterministic normalization, validation and reconciliation pipeline.
- `METHOD.md` — concise methodology, assumptions, controls and limitations.
- `TRUSTWORTHINESS.md` — guarantees and control evidence for downstream Finance/Risk use.
- `CHECKS.sql` — reusable centralized SQL profiling controls.
- `output/trusted_dataset.csv` — trusted records in the required schema: `date, asset, open, high, low, close, volume_base, source, confidence`.
- `output/breaks_report.csv` — source-level discrepancies, severity, action and likely cause.
- `output/break_summary.csv`, `output/qa_summary.csv`, and `output/control_totals.csv` — aggregate QA and final control evidence.

The raw input files in `reconciliation_data/` are preserved. Re-running the pipeline produces the same outputs for the same inputs.
