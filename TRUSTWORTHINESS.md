# Trustworthiness controls

The reconciliation pipeline is designed to fail visibly rather than silently publish questionable data. These controls are the basis for trusting the output downstream in Finance and Risk.

## Completeness

For each asset, the pipeline creates a calendar from all observed source dates and checks each source against it. Missing source dates are written individually to `breaks_report.csv`; they are not silently omitted. Final controls verify that the trusted output has the expected number of unique `asset + date` records.

## Validity

Before a venue candle can provide trusted OHLC, the pipeline requires finite, positive values and checks:

- `high >= low`;
- `high >= max(open, close)`; and
- `low <= min(open, close)`.

It also identifies out-of-order inputs, removes exact duplicate rows deterministically, blocks conflicting duplicate dates, validates positive volume, and flags repeated full candles as possible stale or carry-forward data.

## Materiality and break classification

The script calculates a robust median close across available positive observations. A difference above **0.50%** is reported as a material discrepancy, while a difference above **20%** is classified as a gross outlier. A selected price must also be within the script's 5% consensus guard.

Each break includes its source, date, severity, observed value, benchmark where relevant, action, likely cause, and whether it blocks the value. This groups issues into actionable causes such as duplicate ingestion, source gaps, invalid OHLC, possible stale data, timestamp/session differences, and reference-feed anomalies.

## Control totals and reproducibility

Before results are written, the pipeline asserts unique `asset/date` keys, expected output row count, positive non-excluded prices and volume, and valid OHLC relationships. Exact duplicate rows are removed idempotently, so loading the same file twice does not double-count it. The same raw inputs produce the same outputs on re-run.

## Provenance

The trusted output retains a real venue candle rather than publishing a synthetic median. Its `source` field traces every record to the selected source, including field-level combinations such as `coinbase_ohlc+kraken_volume`. The breaks report preserves the evidence behind exceptions and adjustments.
