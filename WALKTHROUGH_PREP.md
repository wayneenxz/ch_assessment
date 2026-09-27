# Walkthrough / Defense Prep

## 60-second overview

“My approach was to avoid declaring any feed universally correct. I first normalized and validated every source, then chose a deterministic source hierarchy based on the quality actually present in the sample. Kraken became the primary venue because it is complete and already has base-unit volume. Coinbase is my first price fallback, while Binance is mainly corroborating because of duplicates, a large outlier, an invalid candle and a different timestamp convention. The external reference is used as an independent close check rather than an authority. I use a robust median to detect material breaks, but I preserve an actual venue candle in the trusted dataset for coherence and provenance. Anything structurally invalid is blocked, and if no candidate can be corroborated the pipeline fails closed.”

## The strongest findings to mention

1. **Reference feed is not always right.** On 2026-04-11, BTC and ETH show the same pattern: Binance, Kraken and Coinbase cluster together, while the reference is ~1.5% away. That is the clearest evidence for consensus instead of “reference = truth.”
2. **Kraken has a repairable structural error.** On 2026-02-25, `high < low` for both assets. I block that candle, take Coinbase OHLC, and retain Kraken volume because the volume field itself remains valid and is already in base units.
3. **Coinbase has a clear ingestion gap.** 2026-03-04 through 2026-03-10 is absent for both assets, plus 2026-04-08 has a zero close.
4. **Binance has multiple quality/semantics concerns.** Three exact duplicates per asset, a full carry-forward candle, a ~9.5x price error on 2026-01-30, an impossible high on 2026-03-10, and many larger close differences. Its `+08:00` timestamp also means daily-session semantics should be confirmed before treating it as directly comparable with date-only feeds.
5. **Volume units are not uniform.** Coinbase volume is orders of magnitude larger; dividing by close produces values similar to base-volume venues, so I infer quote USD. I do not pretend that conversion is exact without VWAP/source metadata.

## Why Kraken is primary

Do not say “Kraken is correct.” Say:

- It is **the best primary source in this sample** after quality profiling.
- It has 120/120 dates per asset.
- It has no duplicate dates after sorting.
- Its volume is already on the base-unit scale.
- Its valid closes closely track Coinbase and the reference.
- Its one structural OHLC issue is isolated and caught automatically.

In production, source hierarchy should also incorporate contractual definitions, venue SLAs, official market-day cutoffs and source ownership—not only observed sample quality.

## Why not take the median OHLC?

A field-by-field median can create a candle that never existed at any venue and may mix incompatible session boundaries. I use the median only as a robust **reconciliation benchmark**, then select one real venue candle. That gives Finance/Risk clearer provenance and preserves internally coherent OHLC.

## Why 0.50% materiality?

The clean Kraken/Coinbase/reference observations differ by only a few basis points. Their 95th-percentile clean differences are comfortably below 0.50%, so 0.50% is a conservative buffer above normal noise. It also catches the ~1.5% reference anomaly. I would not claim 0.50% is universally correct: in production it should be calibrated by asset volatility, report use case and financial materiality.

## Why a separate 5% fallback guard and 20% gross-outlier label?

They serve different purposes:

- **0.50%** = investigate/report the break.
- **5%** = do not automatically trust a fallback that far from consensus.
- **20%** = classify an observation as an obvious gross outlier in this exercise.

This avoids treating every volatile crypto move as “corrupt,” while still preventing extreme values from flowing downstream.

## Why keep Binance’s +08 date instead of converting to UTC?

The source gives timezone-aware midnight timestamps, but the brief does not state whether each row represents a Singapore-time candle, a UTC candle labeled in local time, or something else. Converting `00:00+08` to UTC changes the calendar date and silently assumes semantics that are not provided. I preserve the source’s reported business date, surface the larger disagreements, and would confirm candle-boundary metadata with the data owner in production.

If the interviewer says the intended contract is UTC, the correct response is: “Then I would update the normalization rule and re-run; the rest of the controls and source-selection framework stays the same.”

## Likely interviewer questions

### “Why not just trust the external reference?”
Because 2026-04-11 demonstrates that it can be the odd source out. A reference should be an independent check, not an unquestioned authority.

### “Why did you use sample quality to choose Kraken?”
The brief gives no contractual source hierarchy, so I needed a transparent rule. I profiled completeness, structural validity, duplicates, units and cross-source agreement. Kraken had the strongest overall profile. I would replace that empirical hierarchy with business-approved source priority in production.

### “Is Coinbase volume / close really base volume?”
Only approximately. The scale strongly suggests quote USD volume, and dividing by close makes it comparable to base-volume venues. But exact conversion should use VWAP or source metadata. That is why I avoid Coinbase as primary volume when Kraken/Binance base volume is available.

### “What happens if tomorrow every venue disagrees?”
The pipeline does not force a number. Structurally invalid sources are rejected, and a fallback too far from robust consensus is blocked. If no candidate passes, the row is emitted with `confidence=excluded` and should trigger an alert/block downstream.

### “What happens if the same file is loaded twice?”
Exact duplicates are removed deterministically and the final key is one row per `asset,date`. Re-running the same raw inputs produces the same output instead of double-counting.

### “Would you use the same threshold for every asset?”
Not necessarily. For the take-home I use one transparent threshold because there are only BTC and ETH and their clean source differences are small. In production I would calibrate by asset volatility, liquidity, reporting materiality and downstream use.

### “What would you add with more time?”
- explicit schema/data-contract tests per source;
- source SLAs and historical reliability scores;
- VWAP-based Coinbase volume conversion or official unit metadata;
- alerting and quarantine tables for failed rows;
- partition/file hashes and run IDs for stronger audit lineage;
- unit/integration tests with deliberately malformed fixture files;
- a small dashboard summarizing break counts, unresolved items and stale sources.

## What not to overclaim

- Do not say Binance is “wrong” on every >0.5% difference; daily-session boundaries may differ.
- Do not say Coinbase volume is definitively quote volume; call it an inference from scale.
- Do not say Kraken is objectively authoritative; call it the chosen primary for this dataset.
- Do not say the reference is bad generally; identify the specific 2026-04-11 break.
- Do not claim a 0.50% threshold is a universal finance standard; it is calibrated to this sample and should be business-approved in production.
