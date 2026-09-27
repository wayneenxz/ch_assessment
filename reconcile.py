#!/usr/bin/env python3
"""Deterministic daily OHLCV reconciliation for the Coinhako take-home.

Run with no arguments from this folder:
    python reconcile.py

Optional paths are available for repeatable runs elsewhere.  The process fails closed:
only a structurally valid, independently corroborated price and a reported base-unit
volume are published.  Coinbase volume is deliberately diagnostic-only because its
unit is not documented in the supplied files.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

ASSETS = ["BTCUSD", "ETHUSD"]
VENUES = ["binance", "kraken", "coinbase"]
PRICE_FIELDS = ["open", "high", "low", "close"]
MATERIALITY_PCT = 0.50
GROSS_OUTLIER_PCT = 20.00


def pct_diff(value: float, benchmark: float) -> float:
    """Absolute percentage difference, or NaN when comparison is impossible."""
    if pd.isna(value) or pd.isna(benchmark) or benchmark == 0:
        return np.nan
    return abs(value / benchmark - 1.0) * 100.0


def normalize_date(df: pd.DataFrame, source: str) -> pd.Series:
    """Keep the business-date label reported by each input source.

    Binance timestamps include +08:00 while the other inputs are date-labelled.  The
    supplied contract does not establish a common candle-session boundary, so shifting
    Binance to UTC would introduce an unsupported assumption.  Its stated calendar
    date is preserved, but it is not used for the same-session price consensus.
    """
    column = {"binance": "timestamp", "kraken": "date", "coinbase": "time", "reference": "date"}[source]
    values = df[column].astype(str).str[:10] if source == "binance" else df[column]
    return pd.to_datetime(values, errors="coerce")


def row_ohlc_valid(row: pd.Series) -> Tuple[bool, str]:
    values = {field: row.get(field, np.nan) for field in PRICE_FIELDS}
    if any(pd.isna(value) or not np.isfinite(value) for value in values.values()):
        return False, "missing_or_nonfinite_price"
    if any(value <= 0 for value in values.values()):
        return False, "nonpositive_price"
    if values["high"] < values["low"]:
        return False, "high_below_low"
    if values["high"] < max(values["open"], values["close"]):
        return False, "high_below_open_or_close"
    if values["low"] > min(values["open"], values["close"]):
        return False, "low_above_open_or_close"
    return True, "ok"


def add_break(breaks: List[dict], **kwargs: object) -> None:
    record = {
        "date": "", "asset": "", "source": "", "break_type": "", "severity": "",
        "field": "", "observed_value": "", "benchmark_value": "", "abs_diff_pct": "",
        "action": "", "likely_cause": "", "blocks_source_value": False, "details": "",
    }
    record.update(kwargs)
    breaks.append(record)


def date_text(value: object) -> str:
    return value.date().isoformat() if pd.notna(value) else ""


def normalize_reference(input_dir: Path, asset: str, breaks: List[dict]) -> Tuple[pd.DataFrame, dict]:
    raw = pd.read_csv(input_dir / f"reference_{asset}.csv")
    raw_rows = len(raw)
    raw["date_norm"] = normalize_date(raw, "reference")
    raw["reference_close_usd"] = pd.to_numeric(raw["reference_close_usd"], errors="coerce")
    raw["source"] = "reference"
    raw["asset"] = asset

    invalid_dates = raw["date_norm"].isna()
    for _, row in raw.loc[invalid_dates].iterrows():
        add_break(breaks, asset=asset, source="reference", break_type="invalid_date", severity="critical",
                  action="excluded_from_reference_checks", likely_cause="unparseable_date",
                  blocks_source_value=True, details="Reference row has no usable business date.")
    raw = raw.loc[~invalid_dates].copy()

    duplicate_dates = raw["date_norm"].duplicated(keep=False)
    if duplicate_dates.any():
        for day, group in raw.loc[duplicate_dates].groupby("date_norm"):
            add_break(breaks, date=date_text(day), asset=asset, source="reference",
                      break_type="duplicate_date", severity="critical",
                      action="reference_date_blocked_pending_resolution",
                      likely_cause="multiple_reference_observations_for_business_date", blocks_source_value=True,
                      details=f"{len(group)} reference rows share the normalized date.")
        raw = raw.loc[~duplicate_dates].copy()

    raw["reference_valid"] = np.isfinite(raw["reference_close_usd"]) & (raw["reference_close_usd"] > 0)
    for _, row in raw.loc[~raw["reference_valid"]].iterrows():
        add_break(breaks, date=date_text(row["date_norm"]), asset=asset, source="reference",
                  break_type="invalid_reference_close", severity="critical", field="reference_close_usd",
                  observed_value=str(row["reference_close_usd"]), action="excluded_from_price_consensus",
                  likely_cause="missing_nonfinite_or_nonpositive_close", blocks_source_value=True,
                  details="Reference close is not a positive finite number.")
    return raw.sort_values("date_norm").reset_index(drop=True), {
        "asset": asset, "source": "reference", "raw_rows": raw_rows,
        "rows_after_exact_dedup": len(raw), "exact_duplicates_dropped": 0,
        "unique_dates": raw["date_norm"].nunique(), "ohlc_invalid_rows": pd.NA,
        "invalid_reference_rows": int((~raw["reference_valid"]).sum()), "out_of_order_raw": False,
    }


def normalize_venue(input_dir: Path, asset: str, source: str, breaks: List[dict]) -> Tuple[pd.DataFrame, dict]:
    raw = pd.read_csv(input_dir / f"{source}_{asset}.csv")
    raw_rows = len(raw)
    raw["date_norm"] = normalize_date(raw, source)
    for column in PRICE_FIELDS + ["volume"]:
        raw[column] = pd.to_numeric(raw[column], errors="coerce")

    out_of_order = not raw["date_norm"].is_monotonic_increasing
    if out_of_order:
        add_break(breaks, asset=asset, source=source, break_type="out_of_order_dates", severity="low",
                  action="sorted_by_normalized_date", likely_cause="source_file_ordering_quirk",
                  blocks_source_value=False, details="Raw date sequence is not monotonic; normalized rows are sorted.")

    invalid_dates = raw["date_norm"].isna()
    for _, row in raw.loc[invalid_dates].iterrows():
        add_break(breaks, asset=asset, source=source, break_type="invalid_date", severity="critical",
                  action="excluded_from_reconciliation", likely_cause="unparseable_date", blocks_source_value=True,
                  details="Venue row has no usable business date.")
    raw = raw.loc[~invalid_dates].copy()

    duplicate_rows = raw.duplicated(keep="first")
    for _, row in raw.loc[duplicate_rows].iterrows():
        add_break(breaks, date=date_text(row["date_norm"]), asset=asset, source=source,
                  break_type="exact_duplicate_row", severity="medium", action="dropped_exact_duplicate_keep_first",
                  likely_cause="duplicate_ingestion_or_file_append", blocks_source_value=False,
                  details="Exact duplicate raw row removed idempotently.")
    df = raw.loc[~duplicate_rows].sort_values("date_norm").reset_index(drop=True)

    duplicate_dates = df["date_norm"].duplicated(keep=False)
    if duplicate_dates.any():
        for day, group in df.loc[duplicate_dates].groupby("date_norm"):
            add_break(breaks, date=date_text(day), asset=asset, source=source,
                      break_type="conflicting_duplicate_date", severity="critical",
                      action="source_date_blocked_pending_resolution",
                      likely_cause="multiple_nonidentical_rows_for_same_business_date", blocks_source_value=True,
                      details=f"{len(group)} non-identical rows share the normalized date.")
        df = df.loc[~duplicate_dates].copy()

    validity = df.apply(row_ohlc_valid, axis=1, result_type="expand")
    df[["ohlc_valid", "ohlc_invalid_reason"]] = validity
    for _, row in df.loc[~df["ohlc_valid"]].iterrows():
        add_break(breaks, date=date_text(row["date_norm"]), asset=asset, source=source,
                  break_type="invalid_ohlc", severity="critical", field="ohlc",
                  observed_value=f"O={row['open']};H={row['high']};L={row['low']};C={row['close']}",
                  action="blocked_as_price_source_for_date", likely_cause=row["ohlc_invalid_reason"],
                  blocks_source_value=True, details="Candle fails automatic price-validity rules.")

    df["volume_valid"] = np.isfinite(df["volume"]) & (df["volume"] > 0)
    if source == "binance":
        add_break(breaks, asset=asset, source=source, break_type="session_definition_unconfirmed",
                  severity="medium", field="timestamp",
                  action="excluded_from_automatic_same_day_price_consensus",
                  likely_cause="daily_candle_boundary_differs_or_is_undocumented",
                  blocks_source_value=False,
                  details="Binance timestamps carry +08:00 while other supplied feeds are date-labelled; the source contract does not establish a common daily session. Binance remains available for reported base-volume fallback.")
    if source == "coinbase":
        # Do not derive a base amount from a daily quote amount and one close.  That
        # transformation needs VWAP/transaction data or a documented source contract.
        df["volume_base"] = np.nan
        df["volume_base_method"] = "not_trusted_unknown_unit"
        add_break(breaks, asset=asset, source=source, break_type="volume_unit_unconfirmed",
                  severity="medium", field="volume", action="diagnostic_only_not_used_for_volume_base",
                  likely_cause="source_volume_unit_not_documented", blocks_source_value=True,
                  details="Coinbase volume is not converted using close; daily quote volume divided by close is not a defensible base-volume measure without VWAP or source metadata.")
    else:
        df["volume_base"] = np.where(df["volume_valid"], df["volume"], np.nan)
        df["volume_base_method"] = "reported_base_units"

    previous = df[PRICE_FIELDS + ["volume"]].shift(1)
    repeated = (df[PRICE_FIELDS + ["volume"]] == previous).all(axis=1) & (df["date_norm"].diff().dt.days == 1)
    for _, row in df.loc[repeated].iterrows():
        add_break(breaks, date=date_text(row["date_norm"]), asset=asset, source=source,
                  break_type="repeated_full_candle", severity="medium",
                  action="retained_for_diagnostic_and_volume_fallback", likely_cause="possible_stale_or_carried_forward_record",
                  blocks_source_value=False, details="All OHLC and volume fields exactly repeat the preceding calendar day.")

    return df, {
        "asset": asset, "source": source, "raw_rows": raw_rows,
        "rows_after_exact_dedup": len(df), "exact_duplicates_dropped": int(duplicate_rows.sum()),
        "unique_dates": df["date_norm"].nunique(), "ohlc_invalid_rows": int((~df["ohlc_valid"]).sum()),
        "invalid_reference_rows": pd.NA, "out_of_order_raw": out_of_order,
    }


def load_and_normalize(input_dir: Path, breaks: List[dict]):
    normalized: Dict[Tuple[str, str], pd.DataFrame] = {}
    refs: Dict[str, pd.DataFrame] = {}
    qa_rows: List[dict] = []
    for asset in ASSETS:
        refs[asset], reference_qa = normalize_reference(input_dir, asset, breaks)
        qa_rows.append(reference_qa)
        for source in VENUES:
            normalized[(asset, source)], venue_qa = normalize_venue(input_dir, asset, source, breaks)
            qa_rows.append(venue_qa)
    return normalized, refs, pd.DataFrame(qa_rows)


def select_price(venue_idx: Dict[str, pd.DataFrame], ref_idx: pd.DataFrame, day: pd.Timestamp):
    """Return a price source only when Kraken/Coinbase has independent agreement.

    Binance is deliberately omitted: daily-session alignment is not established by the
    supplied timestamps.  It remains subject to structural and gross-outlier controls,
    and remains a volume fallback because its reported volume is base-unit scale.
    """
    candidates = []
    for source in ["kraken", "coinbase"]:
        if day in venue_idx[source].index:
            row = venue_idx[source].loc[day]
            if bool(row["ohlc_valid"]):
                candidates.append((source, row, float(row["close"])))
    if day in ref_idx.index:
        reference = ref_idx.loc[day]
        if bool(reference["reference_valid"]):
            candidates.append(("reference", reference, float(reference["reference_close_usd"])))

    comparable_median = float(np.median([value for _, _, value in candidates])) if candidates else np.nan
    for source, row, close in candidates:
        if source not in {"kraken", "coinbase"}:
            continue
        corroborators = [other for other, _, other_close in candidates
                          if other != source and pct_diff(close, other_close) <= MATERIALITY_PCT]
        if corroborators:
            return source, row, close, candidates, comparable_median, corroborators
    return None, None, np.nan, candidates, comparable_median, []


def reconcile(input_dir: Path, output_dir: Path) -> None:
    if not input_dir.is_dir():
        raise FileNotFoundError(f"Input directory does not exist: {input_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    breaks: List[dict] = []
    normalized, refs, qa = load_and_normalize(input_dir, breaks)
    trusted_rows: List[dict] = []
    calendar_control: List[dict] = []

    for asset in ASSETS:
        ref_idx = refs[asset].set_index("date_norm")
        venue_idx = {source: normalized[(asset, source)].set_index("date_norm") for source in VENUES}
        all_dates = list(ref_idx.index)
        all_dates.extend(day for source in VENUES for day in venue_idx[source].index)
        start, end = min(all_dates), max(all_dates)
        calendar = pd.date_range(start, end, freq="D")

        # Calendar days are expected even if every input accidentally omits the same day.
        for source, index in {**venue_idx, "reference": ref_idx}.items():
            missing = [day for day in calendar if day not in index.index]
            for day in missing:
                add_break(breaks, date=date_text(day), asset=asset, source=source, break_type="missing_date",
                          severity="high", action="source_unavailable_for_date; rely_on_other_valid_sources",
                          likely_cause="source_gap_or_failed_ingestion", blocks_source_value=True,
                          details="Date is absent from the continuous expected calendar.")

        for day in calendar:
            price_source, selected, selected_close, comparable, median_close, corroborators = select_price(venue_idx, ref_idx, day)

            # Comparable close checks: valid Kraken, Coinbase and reference only.
            for source, _, close in comparable:
                difference = pct_diff(close, median_close)
                if difference > MATERIALITY_PCT:
                    add_break(breaks, date=date_text(day), asset=asset, source=source,
                              break_type="material_close_disagreement", severity="medium", field="close",
                              observed_value=f"{close:.8f}", benchmark_value=f"{median_close:.8f}",
                              abs_diff_pct=f"{difference:.4f}", action="not_used_without_independent_corroboration",
                              likely_cause="reference_feed_outlier_likely_if_venues_agree" if source == "reference" else "source_specific_price_break",
                              blocks_source_value=False,
                              details=f"Difference from comparable-source median exceeds {MATERIALITY_PCT:.2f}%.")

            # Binance can be assessed for an unmistakable gross error, but not routine
            # 0.5% disagreement, because the daily session is not proven comparable.
            if day in venue_idx["binance"].index and np.isfinite(median_close):
                binance = venue_idx["binance"].loc[day]
                if bool(binance["ohlc_valid"]):
                    difference = pct_diff(float(binance["close"]), median_close)
                    if difference > GROSS_OUTLIER_PCT:
                        add_break(breaks, date=date_text(day), asset=asset, source="binance",
                                  break_type="gross_price_outlier", severity="critical", field="close",
                                  observed_value=f"{float(binance['close']):.8f}", benchmark_value=f"{median_close:.8f}",
                                  abs_diff_pct=f"{difference:.4f}", action="not_used_as_price_source",
                                  likely_cause="gross_error_or_stale_value", blocks_source_value=True,
                                  details=f"Difference from comparable-source median exceeds {GROSS_OUTLIER_PCT:.2f}%.")

            if price_source is None:
                trusted_rows.append({"date": date_text(day), "asset": asset, **{field: np.nan for field in PRICE_FIELDS},
                                     "volume_base": np.nan, "source": "none", "confidence": "excluded"})
                add_break(breaks, date=date_text(day), asset=asset, source="reconciliation",
                          break_type="no_trusted_price_source", severity="critical",
                          action="excluded_from_trusted_values", likely_cause="no_valid_kraken_or_coinbase_price_with_independent_agreement",
                          blocks_source_value=True, details="Fail-closed: no price candidate had independent corroboration within 0.50%.")
                continue

            volume_source, volume_base = None, np.nan
            for source in ["kraken", "binance"]:
                if day not in venue_idx[source].index:
                    continue
                volume = venue_idx[source].loc[day]["volume_base"]
                if pd.notna(volume) and float(volume) > 0:
                    volume_source, volume_base = source, float(volume)
                    break
            if volume_source is None:
                trusted_rows.append({"date": date_text(day), "asset": asset, **{field: np.nan for field in PRICE_FIELDS},
                                     "volume_base": np.nan, "source": "none", "confidence": "excluded"})
                add_break(breaks, date=date_text(day), asset=asset, source="reconciliation",
                          break_type="no_trusted_base_volume", severity="critical", action="excluded_from_trusted_values",
                          likely_cause="no_reported_base_unit_volume_available", blocks_source_value=True,
                          details="Coinbase volume is intentionally excluded because its base/quote unit is unconfirmed.")
                continue

            valid_venue_prices = [(source, close) for source, _, close in comparable if source in {"kraken", "coinbase"}]
            reference_close = next((close for source, _, close in comparable if source == "reference"), np.nan)
            reference_break_resolved = (
                len(valid_venue_prices) >= 2
                and np.isfinite(reference_close)
                and pct_diff(valid_venue_prices[0][1], valid_venue_prices[1][1]) <= MATERIALITY_PCT
                and pct_diff(reference_close, float(np.median([close for _, close in valid_venue_prices]))) > MATERIALITY_PCT
            )
            confidence = "adjusted" if price_source != "kraken" or reference_break_resolved else "ok"
            source_label = price_source if price_source == volume_source else f"{price_source}_ohlc+{volume_source}_volume"
            trusted_rows.append({"date": date_text(day), "asset": asset,
                                 **{field: float(selected[field]) for field in PRICE_FIELDS},
                                 "volume_base": volume_base, "source": source_label, "confidence": confidence})

        calendar_control.append({"asset": asset, "expected_calendar_start": date_text(start),
                                 "expected_calendar_end": date_text(end), "expected_calendar_days": len(calendar),
                                 "trusted_rows_expected": len(calendar)})

    trusted = pd.DataFrame(trusted_rows).sort_values(["asset", "date"]).reset_index(drop=True)
    breaks_df = pd.DataFrame(breaks)
    if breaks_df.empty:
        breaks_df = pd.DataFrame(columns=["break_id", "date", "asset", "source", "break_type", "severity", "field", "observed_value", "benchmark_value", "abs_diff_pct", "action", "likely_cause", "blocks_source_value", "details"])
    else:
        breaks_df = breaks_df.sort_values(["asset", "date", "source", "break_type"], na_position="last").reset_index(drop=True)
        breaks_df.insert(0, "break_id", [f"BRK-{number:04d}" for number in range(1, len(breaks_df) + 1)])

    expected_rows = sum(row["trusted_rows_expected"] for row in calendar_control)
    assert len(trusted) == expected_rows, "Unexpected trusted row count"
    assert not trusted.duplicated(["asset", "date"]).any(), "Duplicate asset/date in trusted output"
    published = trusted[trusted["confidence"] != "excluded"]
    assert (published[PRICE_FIELDS] > 0).all().all(), "Non-positive trusted OHLC"
    assert (published["volume_base"] > 0).all(), "Non-positive trusted base volume"
    assert (published["high"] >= published[["open", "close"]].max(axis=1)).all(), "Invalid trusted high"
    assert (published["low"] <= published[["open", "close"]].min(axis=1)).all(), "Invalid trusted low"

    break_summary = (breaks_df.groupby(["source", "break_type", "severity"], dropna=False).size()
                     .reset_index(name="break_count").sort_values(["severity", "source", "break_type"]))
    missing_counts = []
    for asset in ASSETS:
        expected = pd.date_range(min(list(refs[asset]["date_norm"]) + [day for source in VENUES for day in normalized[(asset, source)]["date_norm"]]),
                                 max(list(refs[asset]["date_norm"]) + [day for source in VENUES for day in normalized[(asset, source)]["date_norm"]]), freq="D")
        for source, frame in [("reference", refs[asset]), *[(source, normalized[(asset, source)]) for source in VENUES]]:
            missing_counts.append({"asset": asset, "source": source, "missing_vs_expected_calendar": len(set(expected) - set(frame["date_norm"]))})
    qa = qa.merge(pd.DataFrame(missing_counts), on=["asset", "source"], how="left")
    controls = pd.DataFrame([
        {"control": "trusted_row_count", "value": len(trusted), "status": "PASS"},
        {"control": "trusted_unique_asset_date", "value": trusted[["asset", "date"]].drop_duplicates().shape[0], "status": "PASS"},
        {"control": "excluded_rows", "value": int((trusted["confidence"] == "excluded").sum()), "status": "PASS" if not (trusted["confidence"] == "excluded").any() else "REVIEW"},
        {"control": "adjusted_rows", "value": int((trusted["confidence"] == "adjusted").sum()), "status": "INFO"},
        {"control": "trusted_ohlc_valid", "value": len(published), "status": "PASS"},
        {"control": "trusted_volume_positive", "value": int((published["volume_base"] > 0).sum()), "status": "PASS"},
    ])
    trusted.to_csv(output_dir / "trusted_dataset.csv", index=False, float_format="%.8f")
    breaks_df.to_csv(output_dir / "breaks_report.csv", index=False)
    break_summary.to_csv(output_dir / "break_summary.csv", index=False)
    qa.to_csv(output_dir / "qa_summary.csv", index=False)
    controls.to_csv(output_dir / "control_totals.csv", index=False)
    print(f"Wrote {len(trusted)} trusted rows to {output_dir / 'trusted_dataset.csv'}")
    print(f"Wrote {len(breaks_df)} breaks to {output_dir / 'breaks_report.csv'}")
    print("Confidence counts:", trusted["confidence"].value_counts().to_dict())
    print("Source counts:", trusted["source"].value_counts().to_dict())


def main() -> None:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=root / "reconciliation_data")
    parser.add_argument("--output-dir", type=Path, default=root / "output")
    args = parser.parse_args()
    reconcile(args.input_dir, args.output_dir)


if __name__ == "__main__":
    main()
