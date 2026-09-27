-- Reusable profiling template.  Load/replace source_input with normalized columns:
-- source, asset, business_date, open, high, low, close, volume.
-- This query is written for DuckDB/Postgres-style SQL; adapt date functions as needed.
WITH source_input AS (
    SELECT source, asset, CAST(business_date AS DATE) AS business_date,
           CAST(open AS DOUBLE) AS open, CAST(high AS DOUBLE) AS high,
           CAST(low AS DOUBLE) AS low, CAST(close AS DOUBLE) AS close,
           CAST(volume AS DOUBLE) AS volume
    FROM normalized_input
),
date_bounds AS (
    SELECT asset, MIN(business_date) AS first_date, MAX(business_date) AS last_date
    FROM source_input WHERE business_date IS NOT NULL GROUP BY asset
),
expected_calendar AS (
    SELECT b.asset, d::DATE AS business_date
    FROM date_bounds b, generate_series(b.first_date, b.last_date, INTERVAL 1 DAY) AS t(d)
),
duplicate_dates AS (
    SELECT source, asset, business_date, COUNT(*) AS row_count
    FROM source_input GROUP BY 1,2,3 HAVING COUNT(*) > 1
),
checks AS (
    SELECT source, asset, business_date, 'invalid_ohlc' AS check_name,
           'critical' AS severity,
           'nonfinite_or_nonpositive_or_invalid_high_low' AS detail
    FROM source_input
    WHERE open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL
       OR open <= 0 OR high <= 0 OR low <= 0 OR close <= 0
       OR high < low OR high < GREATEST(open, close) OR low > LEAST(open, close)
    UNION ALL
    SELECT source, asset, business_date, 'invalid_volume', 'medium',
           'nonfinite_or_nonpositive_volume'
    FROM source_input WHERE volume IS NULL OR volume <= 0
    UNION ALL
    SELECT source, asset, business_date, 'duplicate_business_date', 'critical',
           CONCAT(row_count, ' rows for source/asset/date')
    FROM duplicate_dates
    UNION ALL
    SELECT s.source, c.asset, c.business_date, 'missing_date', 'high',
           'absent from continuous expected calendar'
    FROM expected_calendar c
    CROSS JOIN (SELECT DISTINCT source FROM source_input) s
    LEFT JOIN source_input i ON i.source=s.source AND i.asset=c.asset AND i.business_date=c.business_date
    WHERE i.business_date IS NULL
)
SELECT * FROM checks ORDER BY asset, business_date, source, check_name;
