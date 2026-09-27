# Reconciliation method

## Objective

The output is one daily BTCUSD/ETHUSD record that Finance and Risk can trace to a real source, reproduce from the supplied files, and challenge when inputs disagree. I do not assume that any source—including the reference feed—is always correct. A published record retains one coherent venue OHLC candle; a robust median is validation evidence, not a synthetic output price.

## Source roles and assumptions

Kraken is the preferred price and reported base-volume source. Coinbase is the price fallback when a valid candle is independently corroborated. Binance remains subject to duplicate, stale-record, structural and gross-outlier checks, and is a fallback for reported base volume. However, it is excluded from automatic same-day price consensus: its timestamps use `+08:00` while the other supplied feeds are date-labelled, and no common daily-session contract is provided.

The reference feed is an independent close-price check, not an override. It is validated for invalid dates, duplicate dates and non-positive closes before it can corroborate a venue.

Coinbase volume is diagnostic-only. The supplied unit is not documented, so dividing a daily amount by close would be only an approximation; it can differ from actual base volume without VWAP, transaction data, or a source contract. The pipeline therefore never publishes Coinbase volume as `volume_base`.

## Normalization and controls

Each source is mapped to a normalized business date and common fields. Binance’s reported date label is preserved rather than silently shifted to UTC. Price and volume fields are converted with invalid values coerced to missing values, ensuring malformed future data is visible.

The pipeline then records and handles:

1. invalid/unparseable dates;
2. out-of-order source files (sorted after recording the condition);
3. exact duplicate rows (later copies removed idempotently);
4. conflicting duplicate business dates (the source/date is blocked);
5. invalid candles: non-finite/non-positive OHLC, `high < low`, high below open/close, or low above open/close;
6. invalid/non-positive volume; and
7. exact carry-forward OHLCV records on consecutive dates.

For each asset, the expected calendar is every daily date from the earliest to latest usable observation. This is stronger than an observed-date union: a date omitted by every feed is still visible. Missing dates are written per source to the breaks report.

`CHECKS.sql` is a reusable centralized profiling template. Its first CTE maps a raw/normalized source to a shared schema; the remaining query performs identical duplicate, completeness, invalid-OHLC and invalid-volume checks for every source. It is supplementary QA evidence; `reconcile.py` is the executable reconciliation process.

## Price reconciliation and materiality

For each asset/day, the comparable diagnostic benchmark is the median of valid Kraken, Coinbase and reference closes. A venue close joins that benchmark only when its complete OHLC candle passes structural checks. Binance does not join because same-session comparability has not been established.

The 0.50% threshold means independent agreement. Kraken is selected only if valid Coinbase or valid reference agrees within 0.50%. Otherwise Coinbase is selected only if Kraken or reference agrees within 0.50%. If neither candidate has such corroboration, the price is excluded rather than accepted using a looser tolerance. Source priority chooses among valid candidates; it does not make a value trustworthy by itself.

Comparable differences above 0.50% are reported as material. Binance is automatically reported for an unmistakable gross outlier only (>20% versus the comparable median), because smaller differences may reflect the unconfirmed session boundary. A reference disagreement is recorded but does not replace two agreeing venue prices.

## Volume, provenance and outputs

Volume is reconciled separately from OHLC. The pipeline uses positive reported base-unit volume in order: Kraken, then Binance. A valid Kraken volume may be retained even if an invalid Kraken candle forces a Coinbase price fallback, because candle validity and volume validity are separate field-level controls. If no reported base-unit volume is available, the record fails closed as excluded. Provenance makes this explicit, e.g. `coinbase_ohlc+kraken_volume`.

Before writing output, the pipeline asserts expected row count, unique asset/date keys, positive non-excluded OHLC and volume, and valid high/low relationships. It writes the trusted dataset plus a detailed breaks report, break summary, source QA summary, and control totals.

The principal limitations are missing formal definitions for daily-session alignment and volume semantics. Rather than hiding those gaps through conversion or timestamp assumptions, the submission records them explicitly. In production I would obtain venue session definitions, volume metadata and official identifiers, then add lineage, monitoring and escalation for material breaks or exclusions.
