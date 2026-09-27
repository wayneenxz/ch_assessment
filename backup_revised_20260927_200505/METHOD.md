# Financial Data Reconciliation — Method & Trustworthiness

## 1. What “reconciled” means

I define a reconciled row as **one complete, structurally valid daily candle per asset that can be traced back to a source and is independently corroborated**. I do not assume that any one input is always correct. The three venues and the external reference are used for different purposes:

- **Kraken** is the primary venue for trusted OHLC and volume because, in this sample, it has complete daily coverage, reported volume on the same scale as base units, no duplicate dates after sorting, and only one structural OHLC error per asset.
- **Coinbase** is the first fallback for prices because its valid closes closely agree with Kraken and the reference, but it has a seven-day gap and one zero close per asset. Its volume appears to be USD quote volume, so `volume / close` is only an approximate base-unit conversion.
- **Binance** is a corroborating source rather than a primary source. It contains exact duplicates, a repeated full candle, a gross price outlier, and an invalid high. It also uses timezone-aware `+08:00` timestamps, while the other daily feeds are date-only. Because the brief does not define a common market-day boundary, I preserve Binance’s reported calendar date rather than silently shifting it to UTC; material differences are flagged as possible session-boundary or stale-data breaks.
- **Reference feed** is an independent close-price check, not an authority. On 2026-04-11, all three venue closes cluster together while the reference is about 1.5% away for both assets, so blindly trusting the reference would create a bad reconciliation decision.

The reconciliation therefore prefers a **real venue candle** instead of creating synthetic field-by-field medians. This preserves candle coherence and makes provenance straightforward.

## 2. Normalisation and validation

Each source is mapped to a common schema and a normalized business date. Exact duplicate raw rows are removed idempotently, rows are sorted by date, and the following checks run before a row can be used as a price source:

- all OHLC values are finite and strictly positive;
- `high >= low`;
- `high >= max(open, close)`;
- `low <= min(open, close)`;
- volume is finite and positive;
- duplicate/conflicting dates and out-of-order inputs are surfaced rather than ignored.

The completeness calendar is the **union of all observed source dates**, rather than trusting one feed to define which dates should exist. Missing dates are then reported per source.

For Coinbase volume, the scale is orders of magnitude larger than Binance/Kraken, while `volume / close` is on roughly the same scale as the base-volume venues. I therefore treat Coinbase volume as **inferred USD quote volume** and convert it using `volume / close` only for comparison or last-resort fallback. This is deliberately not treated as exact because a true quote-to-base conversion should use source metadata or VWAP.

### 2.1 Centralized profiling query ("CHECK")

Rather than maintaining a different profiling query for every venue, I use one **centralized check query**. The only source-specific part is the first `source_input` CTE, where the raw table and its date / close column names are mapped into a common schema. To profile a different source, I uncomment its adapter and comment the current one.

This keeps the validation logic identical across sources and makes it easy to re-run the same controls on a new file. The SQL below is illustrative SQL; it assumes each unioned source table contains `asset` and `ingest_seq`, where `ingest_seq` preserves raw file order.

```sql
WITH
/* ================================================================
   STEP 1 — CHOOSE ONE SOURCE
   Uncomment ONE adapter only.
   All adapters map the raw file to the same profiling schema.
   ================================================================ */
source_input AS (

    /* ---------- BINANCE ---------- */
    SELECT
        'binance' AS source,
        asset,
        ingest_seq,
        timestamp AS raw_timestamp,
        CAST(SUBSTR(timestamp, 1, 10) AS DATE) AS date_norm,
        open,
        high,
        low,
        close,
        volume,
        CAST(NULL AS DOUBLE) AS reference_close_usd
    FROM binance_all

    /* ---------- KRAKEN ---------- */
    -- SELECT
    --     'kraken' AS source,
    --     asset,
    --     ingest_seq,
    --     CAST(date AS VARCHAR) AS raw_timestamp,
    --     CAST(date AS DATE) AS date_norm,
    --     open,
    --     high,
    --     low,
    --     close,
    --     volume,
    --     CAST(NULL AS DOUBLE) AS reference_close_usd
    -- FROM kraken_all

    /* ---------- COINBASE ---------- */
    -- SELECT
    --     'coinbase' AS source,
    --     asset,
    --     ingest_seq,
    --     CAST(time AS VARCHAR) AS raw_timestamp,
    --     CAST(time AS DATE) AS date_norm,
    --     open,
    --     high,
    --     low,
    --     close,
    --     volume,
    --     CAST(NULL AS DOUBLE) AS reference_close_usd
    -- FROM coinbase_all

    /* ---------- EXTERNAL REFERENCE ---------- */
    -- SELECT
    --     'reference' AS source,
    --     asset,
    --     ingest_seq,
    --     CAST(date AS VARCHAR) AS raw_timestamp,
    --     CAST(date AS DATE) AS date_norm,
    --     CAST(NULL AS DOUBLE) AS open,
    --     CAST(NULL AS DOUBLE) AS high,
    --     CAST(NULL AS DOUBLE) AS low,
    --     CAST(NULL AS DOUBLE) AS close,
    --     CAST(NULL AS DOUBLE) AS volume,
    --     reference_close_usd
    -- FROM reference_all
),

/* ================================================================
   STEP 2 — SHARED EXPECTED CALENDAR
   Built from the union of dates seen anywhere, so no one source is
   assumed to define completeness.
   ================================================================ */
expected_calendar AS (
    SELECT asset, CAST(SUBSTR(timestamp, 1, 10) AS DATE) AS date_norm
    FROM binance_all

    UNION

    SELECT asset, CAST(date AS DATE)
    FROM kraken_all

    UNION

    SELECT asset, CAST(time AS DATE)
    FROM coinbase_all

    UNION

    SELECT asset, CAST(date AS DATE)
    FROM reference_all
),

/* ================================================================
   STEP 3 — CROSS-SOURCE CLOSE CONSENSUS
   Used only for materiality / outlier checks, not to manufacture the
   trusted OHLC candle.
   ================================================================ */
all_positive_closes AS (
    SELECT asset,
           CAST(SUBSTR(timestamp, 1, 10) AS DATE) AS date_norm,
           close AS close_value
    FROM binance_all
    WHERE close > 0

    UNION ALL

    SELECT asset, CAST(date AS DATE), close
    FROM kraken_all
    WHERE close > 0

    UNION ALL

    SELECT asset, CAST(time AS DATE), close
    FROM coinbase_all
    WHERE close > 0

    UNION ALL

    SELECT asset, CAST(date AS DATE), reference_close_usd
    FROM reference_all
    WHERE reference_close_usd > 0
),
consensus AS (
    SELECT
        asset,
        date_norm,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY close_value)
            AS consensus_close
    FROM all_positive_closes
    GROUP BY asset, date_norm
),

/* ================================================================
   STEP 4 — HELPER CTEs USED BY MULTIPLE CHECKS
   ================================================================ */
with_previous AS (
    SELECT
        s.*,
        LAG(date_norm) OVER (
            PARTITION BY asset ORDER BY ingest_seq
        ) AS prev_raw_date,
        LAG(date_norm) OVER (
            PARTITION BY asset ORDER BY date_norm
        ) AS prev_calendar_date,
        LAG(open) OVER (
            PARTITION BY asset ORDER BY date_norm
        ) AS prev_open,
        LAG(high) OVER (
            PARTITION BY asset ORDER BY date_norm
        ) AS prev_high,
        LAG(low) OVER (
            PARTITION BY asset ORDER BY date_norm
        ) AS prev_low,
        LAG(close) OVER (
            PARTITION BY asset ORDER BY date_norm
        ) AS prev_close,
        LAG(volume) OVER (
            PARTITION BY asset ORDER BY date_norm
        ) AS prev_volume
    FROM source_input s
),

duplicate_dates AS (
    SELECT asset, date_norm, COUNT(*) AS row_count
    FROM source_input
    GROUP BY asset, date_norm
    HAVING COUNT(*) > 1
),

exact_duplicates AS (
    SELECT
        asset,
        date_norm,
        open,
        high,
        low,
        close,
        volume,
        reference_close_usd,
        COUNT(*) AS row_count
    FROM source_input
    GROUP BY
        asset, date_norm, open, high, low, close, volume,
        reference_close_usd
    HAVING COUNT(*) > 1
),

missing_dates AS (
    SELECT e.asset, e.date_norm
    FROM expected_calendar e
    LEFT JOIN (
        SELECT DISTINCT asset, date_norm
        FROM source_input
    ) s
      ON s.asset = e.asset
     AND s.date_norm = e.date_norm
    WHERE s.date_norm IS NULL
),

selected_close AS (
    SELECT
        s.*,
        COALESCE(s.close, s.reference_close_usd) AS close_for_comparison
    FROM source_input s
),

/* ================================================================
   STEP 5 — CENTRAL CHECK OUTPUT
   All controls return the same columns into one `checks` CTE.
   To investigate one issue, uncomment one filter at the very end.
   ================================================================ */
checks AS (

/* CHECK 1 — NULL / UNPARSABLE REQUIRED VALUES */
SELECT
    'null_required_value' AS check_name,
    source,
    asset,
    date_norm,
    'Required date / numeric field is null' AS detail
FROM source_input
WHERE date_norm IS NULL
   OR (
        source <> 'reference'
        AND (
             open IS NULL OR high IS NULL OR low IS NULL
             OR close IS NULL OR volume IS NULL
        )
      )
   OR (
        source = 'reference'
        AND reference_close_usd IS NULL
      )

UNION ALL

/* CHECK 2 — NON-POSITIVE PRICE / VOLUME */
SELECT
    'non_positive_value',
    source,
    asset,
    date_norm,
    'Price or volume is zero / negative'
FROM source_input
WHERE (
        source <> 'reference'
        AND (
             open <= 0 OR high <= 0 OR low <= 0
             OR close <= 0 OR volume <= 0
        )
      )
   OR (
        source = 'reference'
        AND reference_close_usd <= 0
      )

UNION ALL

/* CHECK 3 — STRUCTURALLY INVALID OHLC */
SELECT
    'invalid_ohlc',
    source,
    asset,
    date_norm,
    'high < low, high < open/close, or low > open/close'
FROM source_input
WHERE source <> 'reference'
  AND (
       high < low
       OR high < GREATEST(open, close)
       OR low > LEAST(open, close)
      )

UNION ALL

/* CHECK 4 — DUPLICATE BUSINESS DATE */
SELECT
    'duplicate_date',
    s.source,
    d.asset,
    d.date_norm,
    'More than one row exists for the same asset/date'
FROM duplicate_dates d
CROSS JOIN (SELECT DISTINCT source FROM source_input) s

UNION ALL

/* CHECK 5 — EXACT DUPLICATE ROW */
SELECT
    'exact_duplicate',
    s.source,
    d.asset,
    d.date_norm,
    'All normalized values are duplicated exactly'
FROM exact_duplicates d
CROSS JOIN (SELECT DISTINCT source FROM source_input) s

UNION ALL

/* CHECK 6 — RAW FILE OUT OF CHRONOLOGICAL ORDER */
SELECT
    'out_of_order',
    source,
    asset,
    date_norm,
    'Current raw row date is earlier than the prior raw row date'
FROM with_previous
WHERE prev_raw_date IS NOT NULL
  AND date_norm < prev_raw_date

UNION ALL

/* CHECK 7 — MISSING DATE */
SELECT
    'missing_date',
    s.source,
    m.asset,
    m.date_norm,
    'Expected asset/date is absent from this source'
FROM missing_dates m
CROSS JOIN (SELECT DISTINCT source FROM source_input) s

UNION ALL

/* CHECK 8 — EXACT REPEAT OF THE PRIOR DAILY CANDLE */
SELECT
    'repeated_prior_candle',
    source,
    asset,
    date_norm,
    'OHLC and volume exactly repeat the immediately prior calendar day'
FROM with_previous
WHERE source <> 'reference'
  AND date_norm = prev_calendar_date + INTERVAL '1' DAY
  AND open = prev_open
  AND high = prev_high
  AND low = prev_low
  AND close = prev_close
  AND volume = prev_volume

UNION ALL

/* CHECK 9 — MATERIAL CLOSE DIFFERENCE (> 0.50%) */
SELECT
    'material_close_difference',
    s.source,
    s.asset,
    s.date_norm,
    'Close differs from cross-source median by more than 0.50%'
FROM selected_close s
JOIN consensus c
  ON c.asset = s.asset
 AND c.date_norm = s.date_norm
WHERE s.close_for_comparison > 0
  AND ABS(s.close_for_comparison / c.consensus_close - 1) * 100 > 0.50

UNION ALL

/* CHECK 10 — GROSS CLOSE OUTLIER (> 20%) */
SELECT
    'gross_close_outlier',
    s.source,
    s.asset,
    s.date_norm,
    'Close differs from cross-source median by more than 20%'
FROM selected_close s
JOIN consensus c
  ON c.asset = s.asset
 AND c.date_norm = s.date_norm
WHERE s.close_for_comparison > 0
  AND ABS(s.close_for_comparison / c.consensus_close - 1) * 100 > 20.0
)

SELECT *
FROM checks
WHERE 1 = 1

-- Uncomment ONE of these when I only want a specific diagnostic:
-- AND check_name = 'null_required_value'
-- AND check_name = 'non_positive_value'
-- AND check_name = 'invalid_ohlc'
-- AND check_name = 'duplicate_date'
-- AND check_name = 'exact_duplicate'
-- AND check_name = 'out_of_order'
-- AND check_name = 'missing_date'
-- AND check_name = 'repeated_prior_candle'
-- AND check_name = 'material_close_difference'
-- AND check_name = 'gross_close_outlier'

-- Optional narrowing:
-- AND asset = 'BTCUSD'
-- AND date_norm = DATE '2026-02-25'

ORDER BY asset, date_norm, check_name;
```

The main advantage is that **the control logic is written once**. When profiling a new source, I only need to map its raw columns into `source_input`; the null, duplicate, ordering, completeness, structural and outlier checks remain unchanged.

For quick investigation, I uncomment the relevant filter at the bottom rather than rewriting the SQL. For example, uncommenting `AND check_name = 'missing_date'` returns only missing dates, while `AND check_name = 'invalid_ohlc'` returns only structurally impossible OHLC records.

**Observed results from the supplied files**

| Source | What the centralized checks surfaced |
|---|---|
| Binance | 3 exact duplicate rows per asset (19 Jan, 16 Feb, 23 Mar); repeated prior-day candle on 2 Jan; gross close outlier on 30 Jan; structurally invalid OHLC on 10 Mar; no missing dates or nulls. |
| Kraken | No duplicates, missing dates or nulls; raw rows are not fully chronological; structurally invalid OHLC on 25 Feb for both assets. |
| Coinbase | No duplicates or nulls; 7 missing dates per asset (4–10 Mar); `close = 0` on 8 Apr; volume magnitude indicates a likely quote-USD rather than base-unit field. |
| Reference | No duplicates, missing dates, nulls or non-positive closes; material cross-source break on 11 Apr for both assets while the three venues themselves agree closely. |

**Volume-unit diagnostic.** The centralized validity query can establish whether volume is positive, but it cannot prove the business unit from the column name alone. I therefore profile venue volume separately by scale. Coinbase volume is orders of magnitude larger than Kraken/Binance, while `Coinbase volume / Coinbase close` falls onto approximately the same scale as reported base-unit volume elsewhere. I treat this as evidence that Coinbase volume is likely quote-USD volume, while explicitly documenting that this is an inference rather than confirmed metadata.


## 3. Conflict resolution and materiality

For each asset/date, I calculate a robust close benchmark as the median of all **positive** venue closes plus the positive reference close. The median is used only to detect breaks and gross outliers; it is not used to manufacture the trusted candle.

I use a **0.50% materiality threshold** for close disagreements. This is intentionally wider than the observed normal variation: on clean observations, Kraken/Coinbase/reference differences are typically around a few basis points, with 95th-percentile differences well below 0.50%. A 0.50% threshold therefore avoids flagging ordinary small venue differences while still catching meaningful breaks such as the 2026-04-11 reference anomaly.

I separately treat a deviation above **20%** as a gross outlier for reporting. In addition, a candidate fallback price source must be within **5%** of the robust consensus to be trusted. This keeps the reporting threshold and the fail-safe selection threshold separate: crypto can move materially within a day, but a fallback that is far from every other observation should not silently enter Finance/Risk data.

Price-source priority is deterministic:

1. valid Kraken candle;
2. otherwise valid Coinbase candle;
3. otherwise valid Binance candle, only if it passes the gross-deviation guard;
4. otherwise fail closed with `confidence = excluded`.

Volume is reconciled at field level. A valid reported base-unit volume is preferred even if another source supplies the OHLC. For example, on 2026-02-25 Kraken’s OHLC is structurally impossible, so Coinbase supplies the OHLC while Kraken’s still-positive base volume is retained. The `source` field makes this explicit as `coinbase_ohlc+kraken_volume`.

## 4. Important breaks found

The main issues surfaced by the controls are:

- **Binance:** three exact duplicate rows per asset (2026-01-19, 2026-02-16, 2026-03-23); an exact carry-forward candle on 2026-01-02; a gross price outlier on 2026-01-30; invalid `high` on 2026-03-10; and many material close differences consistent with a possible daily-session/timestamp-boundary mismatch.
- **Kraken:** raw rows are out of chronological order around February; OHLC is structurally invalid on 2026-02-25 for both assets (`high < low`).
- **Coinbase:** seven missing days per asset (2026-03-04 through 2026-03-10); zero close on 2026-04-08; volume unit appears to be quote USD rather than base units.
- **Reference:** 2026-04-11 is a likely reference-feed break because all three venues agree closely with one another while the reference differs by about 1.5% for both BTCUSD and ETHUSD.

The final output contains **240 rows** (120 days × 2 assets), with **238 rows using Kraken directly** and **2 rows using Coinbase OHLC + Kraken volume**. Four rows are marked `adjusted`: the two 2026-02-25 fallback rows and the two 2026-04-11 rows where the reference break is resolved by venue consensus. No row is left `review` or `excluded` in this sample.

## 5. Why Risk and Finance can trust the output

**Completeness.** The union date calendar is compared with every source. The final control requires exactly one row per asset/date and produces 240/240 unique rows.

**Validity.** Impossible candles, non-positive prices, duplicate/conflicting dates, non-positive volume and malformed ordering are detected before source selection. Final assertions re-check trusted OHLC and volume.

**Materiality.** Small venue differences are tolerated; deviations above 0.50% are reported. Large fallback deviations are blocked instead of being quietly accepted.

**Break classification.** Every detected issue is recorded with source, asset/date, severity, action and likely cause. A separate summary aggregates recurring patterns so Finance/Risk does not have to scan every detail row.

**Control totals.** Raw row counts, duplicate removals, missing dates, invalid rows, trusted row counts, confidence counts and final validity tests are written to QA/control files. No input issue disappears silently.

**Reproducibility.** The pipeline is deterministic: exact duplicates are dropped consistently, source priority is fixed, outputs are sorted, and re-running the same files produces the same results without double-counting.

**Provenance.** Each trusted row states the source of its OHLC and, when different, its volume source. The detailed breaks report shows why a source was rejected or downgraded.

**Fail-safe behavior.** A structurally invalid price row cannot be selected. If no valid candidate passes the corroboration guard, the pipeline emits an `excluded` row instead of inventing or silently forwarding a value. In a production pipeline I would make that condition alerting/blocking for downstream Finance/Risk consumers.
