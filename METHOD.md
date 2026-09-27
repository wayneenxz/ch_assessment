# Reconciliation method

## Objective

The aim is to produce one daily BTCUSD and ETHUSD record that Finance/Risk can trace to a real source, reproduce from the supplied files, and challenge when the inputs disagree. I do not treat any input—including the reference feed—as inherently correct. The final output keeps a coherent venue OHLC candle rather than publishing a field-by-field median, while the median is used as validation evidence.

## Source roles and assumptions

Kraken is the preferred OHLC and base-volume source because it has the strongest observed coverage and quality in the sample. Coinbase is the first price fallback when Kraken’s candle is invalid. Binance is a corroborating and last-resort source: it has timezone-aware `+08:00` timestamps while the other inputs are date-labelled, so the source’s stated calendar date is retained rather than silently shifted to UTC. The external reference is an independent close-price check, not an override.

The supplied Coinbase volume appears to be on a quote-currency scale. The pipeline therefore derives an approximate base volume as `volume / close` only for comparison or a last-resort fallback, and documents that inference in the breaks report. In production I would require source documentation or VWAP/transaction data before treating this conversion as exact.

## Normalization and quality controls

Each source is mapped to a normalized business date and common OHLCV fields. The process is deterministic:

1. Parse dates and retain Binance’s reported business-date label.
2. Detect out-of-order raw files, then sort by the normalized date.
3. Remove exact duplicate rows idempotently; block conflicting non-identical rows for the same business date.
4. Validate every candle before it can provide a price: all OHLC values must be finite and positive; `high >= low`; `high >= max(open, close)`; and `low <= min(open, close)`.
5. Validate that volume is finite and positive, and identify repeated full candles as possible stale/carry-forward data.
6. Build a calendar from the union of observed source dates and record source-specific missing dates.

`CHECKS.sql` expresses the duplicate, completeness, invalid OHLC and invalid-volume controls in one reusable SQL template. The first CTE is the only source adapter: it maps a source table into a common schema. This keeps the check definitions consistent when profiling a new source. The SQL is a supplementary validation artefact; `reconcile.py` is the executable pipeline.

## Reconciliation and materiality

For each asset/day, the script calculates a robust median close across available positive source closes. It uses the median as a benchmark, not as a published price, because a median is less distorted by one extreme bad observation than a mean while a venue candle preserves provenance.

A difference above **0.50%** is recorded as a material close disagreement. A difference above **20%** is a gross price outlier. These thresholds are reporting and safety controls: a source remains preferred only if its candle is structurally valid and its close is within the script’s 5% gross-deviation guard. The price priority is Kraken, then Coinbase, then Binance. If no candidate passes structural and deviation checks, the output fails closed as `excluded` rather than inventing a value.

Volume is selected separately from OHLC. A positive known base-unit volume is preferred, so a valid Kraken volume can be retained even when an invalid Kraken candle forces a Coinbase OHLC fallback. The `source` field makes such cases explicit, for example `coinbase_ohlc+kraken_volume`.

The reference feed is treated as evidence rather than authority. If it differs materially while the venue prices agree, the discrepancy is reported and the venue candle remains the trusted record. Conversely, a source with a bad candle or a large deviation cannot become trusted merely because it is high priority.

## Output, controls and limitations

The pipeline writes the trusted dataset alongside a detailed breaks report, a break summary, source-level QA, and final control totals. Before output is written it asserts unique `asset/date` rows, expected row count, positive non-excluded prices and volume, and valid OHLC relationships. These checks make a silent bad publish fail visibly.

The main limitations are the absence of formal source metadata for daily-session boundaries and volume units. The submission preserves those uncertainties in the method and breaks report rather than resolving them through unsupported assumptions. In production, I would obtain the venue session definitions, volume semantics and official instrument metadata, then add scheduled ingestion, data lineage, monitoring and escalation for excluded or material-break records.
