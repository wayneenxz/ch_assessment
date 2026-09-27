# Reconciliation method

## Objective and scope

The deliverable is a daily BTCUSD/ETHUSD dataset that Finance and Risk can trace to a source, reproduce from the supplied inputs, and challenge when a source disagrees. A trusted row is one complete, structurally valid OHLC candle with a reported base-unit volume and independent price corroboration. I do not assume that an input called “reference” is automatically correct.

The pipeline reads the eight venue files and two reference files, normalizes them to a common asset/date schema, records source exceptions, selects a defensible record, and writes all output deterministically. It uses the observed date range as a continuous calendar (rather than an observed-date union), so a day omitted by every source would still be detected.

## Source roles and assumptions

- **Kraken** is the preferred price and volume source when its candle is valid and independently corroborated. Its supplied volume is treated as reported base units.
- **Coinbase** is the price fallback only. A valid Coinbase close can corroborate Kraken and, if Kraken’s candle is invalid, be selected when Kraken or the reference independently agrees.
- **Binance** is not used in automatic 0.50% same-day price consensus or price selection. Its timestamp includes `+08:00`; the other inputs are date-labelled and the brief does not define a common daily session. Binance remains subject to structural, duplicate, stale-record and gross-outlier checks, and is a fallback for reported base-unit volume.
- **Reference** is an independent validation input, not an override. It is checked for invalid dates, duplicate business dates and invalid/non-positive closes before it can corroborate a venue.
- **Coinbase volume is diagnostic-only.** Its unit is not documented. Dividing daily volume by close merely gives an approximation that can differ from actual base volume because the correct conversion needs VWAP, transaction data, or a source contract. It is therefore never used for `volume_base`.

These choices avoid hiding assumptions in a reconciliation result. In particular, source priority chooses among candidates that have passed validation; it does not itself make a source trustworthy.

## Normalization and quality controls

The reported calendar label is preserved. I do not silently convert Binance’s midnight `+08:00` timestamp to the preceding UTC date. Price and volume fields are coerced with `to_numeric(errors="coerce")`, making malformed future values visible rather than allowing type-dependent behavior.

For each venue the pipeline records and handles:

1. invalid/unparseable dates;
2. non-monotonic source order (then sorts normalized data);
3. exact duplicate raw rows (drops later copies idempotently);
4. conflicting duplicate business dates (blocks that source/date);
5. non-finite/non-positive OHLC, `high < low`, high below open/close, and low above open/close (blocks price use);
6. invalid/non-positive reported volume;
7. an exact carry-forward of all OHLC and volume fields on the next date.

The reference feed is also checked for invalid dates, duplicate dates, and invalid/non-positive closes. The expected calendar runs from the earliest to latest usable reported date for each asset, at daily frequency. Missing source dates are individually written to the breaks report.

## Price reconciliation and materiality

The comparable diagnostic benchmark is the median of valid **Kraken, Coinbase and reference** closes for an asset/day. A whole venue candle must pass structural validation before its close enters this benchmark; a positive close from an otherwise invalid candle is not accepted. Binance is intentionally excluded because session comparability is not established.

The 0.50% threshold has a single meaning: independent agreement. Kraken is selected only when either valid Coinbase or valid reference agrees within 0.50%. Otherwise Coinbase is selected only when Kraken or reference agrees within 0.50%. If neither condition is true, the price is excluded rather than accepted under a looser guard. The result retains the actual selected OHLC; the median is validation evidence, not a synthetic published candle.

Differences above 0.50% in the comparable feeds are reported as material. A Binance difference is recorded automatically only when it is a gross outlier (>20% versus the comparable median); smaller differences may just reflect an unconfirmed session boundary. A disagreement from the reference is flagged but does not override two agreeing comparable venues.

The 5% threshold from the earlier draft is deliberately removed: it should describe severity if needed, not give a preferred source permission to bypass independent corroboration.

## Volume, provenance and confidence

Volume is reconciled separately from the OHLC. The pipeline selects positive reported base-unit volume in this order: Kraken, then Binance. It can keep Kraken volume when Kraken’s candle is invalid for price selection because the validity of its reported volume is a separate field-level question. If no reported base-unit volume exists, the entire row is excluded; Coinbase’s unknown-unit volume is not silently substituted.

The `source` field states field-level provenance, e.g. `coinbase_ohlc+kraken_volume`. `confidence=adjusted` identifies a Coinbase price fallback or an accepted venue consensus that resolves a reference disagreement. `confidence=excluded` means an asset/day did not meet the fail-closed publication conditions.

## Output controls and limitations

Before writing output, the pipeline asserts: the expected row count, unique asset/date keys, positive OHLC and base volume for all non-excluded rows, and valid high/low relationships. It emits a detailed breaks report, grouped summary, QA summary, and control totals alongside the trusted dataset.

The core limitation is the lack of an explicit daily-session contract and volume metadata. I preserve source dates, do not make Binance an automatic same-day price source, and do not convert Coinbase volume. In production, I would obtain venue session definitions, source volume semantics, official identifiers and metadata, then use those contracts to refine comparability and add automated lineage/alerting.
