# Coinhako data-reconciliation take-home

This submission reconciles supplied daily BTCUSD and ETHUSD data into a traceable, defensible output. It preserves source-level problems in a breaks report rather than silently overwriting them.

## Run

From this directory, with Python 3.10+ and the listed dependencies installed:

```powershell
python -m pip install -r requirements.txt
python reconcile.py
```

The default locations are `reconciliation_data/` and `output/`. Optional arguments are available for a different input or output path:

```powershell
python reconcile.py --input-dir .\reconciliation_data --output-dir .\output
```

## Deliverables

- `reconcile.py` — deterministic reconciliation and validation pipeline.
- `METHOD.md` — concise method, assumptions, controls and limitations.
- `CHECKS.sql` — reusable profiling/check-query template.
- `output/trusted_dataset.csv` — one reconciled record per asset/day.
- `output/breaks_report.csv` — actionable source and reconciliation exceptions.
- `output/break_summary.csv`, `output/qa_summary.csv`, `output/control_totals.csv` — supporting controls.

The pipeline intentionally fails closed. A published row needs a structurally valid Kraken or Coinbase candle corroborated within 0.50% by the other comparable venue or the reference feed, plus a reported base-unit volume from Kraken or Binance. Coinbase volume is not used as `volume_base`, because its unit has not been documented.
