#!/usr/bin/env python3
"""Coinhako take-home: deterministic financial data reconciliation.

Usage:
    python reconcile.py --input-dir ./reconciliation_data --output-dir ./output

Design choices are documented in METHOD.md. The script intentionally fails closed:
if no structurally valid, corroborated price source exists for a date/asset, the row is
emitted as confidence='excluded' with null values rather than silently passing bad data.
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
MAX_TRUST_DEVIATION_PCT = 5.00
GROSS_OUTLIER_PCT = 20.00


def pct_diff(value: float, benchmark: float) -> float:
    if pd.isna(value) or pd.isna(benchmark) or benchmark == 0:
        return np.nan
    return abs(value / benchmark - 1.0) * 100.0


def normalize_date(df: pd.DataFrame, source: str) -> pd.Series:
    """Normalize source date while preserving the reported business-day label.

    Binance supplies timezone-aware midnight timestamps (+08:00). The brief does not
    define a universal market-day timezone, so we preserve the calendar date encoded by
    the source instead of silently shifting it to the prior UTC day. In production this
    should be confirmed against the venue data contract.
    """
    if source == "binance":
        return pd.to_datetime(df["timestamp"].astype(str).str[:10], errors="coerce")
    if source == "kraken":
        return pd.to_datetime(df["date"], errors="coerce")
    if source == "coinbase":
        return pd.to_datetime(df["time"], errors="coerce")
    if source == "reference":
        return pd.to_datetime(df["date"], errors="coerce")
    raise ValueError(source)


def row_ohlc_valid(row: pd.Series) -> Tuple[bool, str]:
    values = {f: row.get(f, np.nan) for f in PRICE_FIELDS}
    if any(pd.isna(v) or not np.isfinite(v) for v in values.values()):
        return False, "missing_or_nonfinite_price"
    if any(v <= 0 for v in values.values()):
        return False, "nonpositive_price"
    if values["high"] < values["low"]:
        return False, "high_below_low"
    if values["high"] < max(values["open"], values["close"]):
        return False, "high_below_open_or_close"
    if values["low"] > min(values["open"], values["close"]):
        return False, "low_above_open_or_close"
    return True, "ok"


def add_break(breaks: List[dict], **kwargs) -> None:
    template = {
        "date": "",
        "asset": "",
        "source": "",
        "break_type": "",
        "severity": "",
        "field": "",
        "observed_value": "",
        "benchmark_value": "",
        "abs_diff_pct": "",
        "action": "",
        "likely_cause": "",
        "blocks_source_value": False,
        "details": "",
    }
    template.update(kwargs)
    breaks.append(template)


def load_and_normalize(input_dir: Path, breaks: List[dict]):
    normalized: Dict[Tuple[str, str], pd.DataFrame] = {}
    refs: Dict[str, pd.DataFrame] = {}
    qa_rows: List[dict] = []

    for asset in ASSETS:
        # Reference feed
        ref = pd.read_csv(input_dir / f"reference_{asset}.csv")
        ref["date_norm"] = normalize_date(ref, "reference")
        ref["source"] = "reference"
        ref["asset"] = asset
        ref["reference_valid"] = (
            pd.to_numeric(ref["reference_close_usd"], errors="coerce").notna()
            & (ref["reference_close_usd"] > 0)
        )
        refs[asset] = ref.sort_values("date_norm").reset_index(drop=True)

        for source in VENUES:
            raw = pd.read_csv(input_dir / f"{source}_{asset}.csv")
            raw_rows = len(raw)
            raw["date_norm"] = normalize_date(raw, source)

            # Out-of-order detection before sorting.
            out_of_order = not raw["date_norm"].is_monotonic_increasing
            if out_of_order:
                add_break(
                    breaks,
                    asset=asset,
                    source=source,
                    break_type="out_of_order_dates",
                    severity="low",
                    action="sorted_by_normalized_date",
                    likely_cause="source_file_ordering_quirk",
                    blocks_source_value=False,
                    details="Raw date sequence is not monotonic; rows are sorted during normalization.",
                )

            # Exact duplicate rows, including source timestamp/date.
            dup_mask = raw.duplicated(keep="first")
            dup_rows = int(dup_mask.sum())
            if dup_rows:
                for _, row in raw.loc[dup_mask].iterrows():
                    add_break(
                        breaks,
                        date=row["date_norm"].date().isoformat() if pd.notna(row["date_norm"]) else "",
                        asset=asset,
                        source=source,
                        break_type="exact_duplicate_row",
                        severity="medium",
                        action="dropped_exact_duplicate_keep_first",
                        likely_cause="duplicate_ingestion_or_file_append",
                        blocks_source_value=False,
                        details="Exact duplicate raw row removed idempotently.",
                    )
            df = raw.loc[~dup_mask].copy()
            df = df.sort_values("date_norm").reset_index(drop=True)

            # Date uniqueness after exact-dedup. If conflicting duplicates remain, block them.
            conflicting_dup_dates = df["date_norm"].duplicated(keep=False)
            if conflicting_dup_dates.any():
                for d, group in df.loc[conflicting_dup_dates].groupby("date_norm"):
                    add_break(
                        breaks,
                        date=d.date().isoformat(),
                        asset=asset,
                        source=source,
                        break_type="conflicting_duplicate_date",
                        severity="critical",
                        action="source_date_blocked_pending_resolution",
                        likely_cause="multiple_nonidentical_rows_for_same_business_date",
                        blocks_source_value=True,
                        details=f"{len(group)} non-identical rows share the same normalized date.",
                    )
                df = df.loc[~conflicting_dup_dates].copy()

            # Structural validation and normalized volume.
            validity, reasons = [], []
            for _, row in df.iterrows():
                ok, reason = row_ohlc_valid(row)
                validity.append(ok)
                reasons.append(reason)
                if not ok:
                    add_break(
                        breaks,
                        date=row["date_norm"].date().isoformat(),
                        asset=asset,
                        source=source,
                        break_type="invalid_ohlc",
                        severity="critical",
                        field="ohlc",
                        observed_value=f"O={row['open']};H={row['high']};L={row['low']};C={row['close']}",
                        action="blocked_as_price_source_for_date",
                        likely_cause=reason,
                        blocks_source_value=(source == "kraken"),
                        details="Candle fails automatic price validity rules.",
                    )
            df["ohlc_valid"] = validity
            df["ohlc_invalid_reason"] = reasons
            df["volume_valid"] = pd.to_numeric(df["volume"], errors="coerce").notna() & (df["volume"] > 0)

            # Volume normalization. Binance/Kraken appear to be base units. Coinbase is
            # systematically orders of magnitude larger and behaves like USD quote volume;
            # use close as an approximate conversion only for comparison/fallback.
            if source == "coinbase":
                df["volume_base"] = np.where(
                    df["ohlc_valid"] & df["volume_valid"], df["volume"] / df["close"], np.nan
                )
                df["volume_base_method"] = "quote_usd_div_close_inferred"
                add_break(
                    breaks,
                    asset=asset,
                    source=source,
                    break_type="volume_unit_inference",
                    severity="medium",
                    field="volume",
                    action="normalized_for_comparison_only; avoid_as_primary_volume_when_base_unit_source_exists",
                    likely_cause="source_volume_appears_to_be_quote_usd_not_base_units",
                    blocks_source_value=False,
                    details="Inference is based on scale: volume/close is comparable to base-volume venues. Exact base volume would require a VWAP or source metadata.",
                )
            else:
                df["volume_base"] = np.where(df["volume_valid"], df["volume"], np.nan)
                df["volume_base_method"] = "reported_base_units"

            # Suspicious exact carry-forward of all numeric fields across consecutive dates.
            numeric_cols = PRICE_FIELDS + ["volume"]
            prev = df[numeric_cols].shift(1)
            same_full = (df[numeric_cols] == prev).all(axis=1) & (df["date_norm"].diff().dt.days == 1)
            for _, row in df.loc[same_full].iterrows():
                add_break(
                    breaks,
                    date=row["date_norm"].date().isoformat(),
                    asset=asset,
                    source=source,
                    break_type="repeated_full_candle",
                    severity="medium",
                    action="retained_for_reconciliation_but_not_preferred",
                    likely_cause="possible_stale_or_carried_forward_record",
                    blocks_source_value=False,
                    details="OHLC and volume exactly repeat the prior calendar day.",
                )

            normalized[(asset, source)] = df
            qa_rows.append(
                {
                    "asset": asset,
                    "source": source,
                    "raw_rows": raw_rows,
                    "rows_after_exact_dedup": len(df),
                    "exact_duplicates_dropped": dup_rows,
                    "unique_dates": df["date_norm"].nunique(),
                    "ohlc_invalid_rows": int((~df["ohlc_valid"]).sum()),
                    "out_of_order_raw": out_of_order,
                }
            )

    return normalized, refs, pd.DataFrame(qa_rows)


def reconcile(input_dir: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    breaks: List[dict] = []
    normalized, refs, qa = load_and_normalize(input_dir, breaks)

    trusted_rows: List[dict] = []
    calendar_control: List[dict] = []

    for asset in ASSETS:
        ref = refs[asset].copy()
        ref_idx = ref.set_index("date_norm")
        venue_idx = {
            source: normalized[(asset, source)].set_index("date_norm") for source in VENUES
        }

        # Completeness calendar is the union of all observed source dates; no single feed
        # is assumed to define existence of a business day.
        calendar_dates = set(ref["date_norm"].dropna())
        for source in VENUES:
            calendar_dates.update(normalized[(asset, source)]["date_norm"].dropna())
        calendar = pd.DatetimeIndex(sorted(calendar_dates))

        # Missing-date controls against the union calendar.
        source_indexes = {**venue_idx, "reference": ref_idx}
        for source, idx in source_indexes.items():
            present = set(idx.index)
            missing_dates = [d for d in calendar if d not in present]
            for d in missing_dates:
                add_break(
                    breaks,
                    date=d.date().isoformat(),
                    asset=asset,
                    source=source,
                    break_type="missing_date",
                    severity="high",
                    action="source_unavailable_for_date; rely_on_other_valid_sources",
                    likely_cause="source_gap_or_failed_ingestion",
                    blocks_source_value=True,
                    details="Date is present in the union calendar but absent from this source.",
                )

        for d in calendar:
            # Build field-level valid close observations for robust consensus.
            close_obs = []
            for source in VENUES:
                if d in venue_idx[source].index:
                    row = venue_idx[source].loc[d]
                    close = float(row["close"])
                    if np.isfinite(close) and close > 0:
                        close_obs.append((source, close))
            ref_close = float(ref_idx.loc[d, "reference_close_usd"]) if d in ref_idx.index else np.nan
            if np.isfinite(ref_close) and ref_close > 0:
                close_obs.append(("reference", ref_close))

            consensus_close = float(np.median([v for _, v in close_obs])) if close_obs else np.nan

            # Detailed material close breaks.
            for source, close in close_obs:
                diff = pct_diff(close, consensus_close)
                if diff > MATERIALITY_PCT:
                    severity = "critical" if diff > GROSS_OUTLIER_PCT else "medium"
                    btype = "gross_price_outlier" if diff > GROSS_OUTLIER_PCT else "material_close_disagreement"
                    if source == "reference":
                        likely = "reference_feed_outlier_likely_if_venues_agree"
                    elif source == "binance":
                        likely = "session_boundary_or_stale_value_possible"
                    else:
                        likely = "source_specific_price_break"
                    add_break(
                        breaks,
                        date=d.date().isoformat(),
                        asset=asset,
                        source=source,
                        break_type=btype,
                        severity=severity,
                        field="close",
                        observed_value=f"{close:.8f}",
                        benchmark_value=f"{consensus_close:.8f}",
                        abs_diff_pct=f"{diff:.4f}",
                        action="not_used_without_independent_corroboration",
                        likely_cause=likely,
                        blocks_source_value=False,
                        details=f"Absolute deviation from robust cross-source median exceeds {MATERIALITY_PCT:.2f}%.",
                    )

            # Price source priority: preserve a coherent real venue candle rather than
            # synthesizing field-wise medians. Primary is Kraken based on observed quality.
            price_source = None
            selected = None
            for source in ["kraken", "coinbase", "binance"]:
                if d not in venue_idx[source].index:
                    continue
                row = venue_idx[source].loc[d]
                if not bool(row["ohlc_valid"]):
                    continue
                if pct_diff(float(row["close"]), consensus_close) > MAX_TRUST_DEVIATION_PCT:
                    continue
                price_source = source
                selected = row
                break

            if price_source is None:
                trusted_rows.append(
                    {
                        "date": d.date().isoformat(),
                        "asset": asset,
                        "open": np.nan,
                        "high": np.nan,
                        "low": np.nan,
                        "close": np.nan,
                        "volume_base": np.nan,
                        "source": "none",
                        "confidence": "excluded",
                    }
                )
                add_break(
                    breaks,
                    date=d.date().isoformat(),
                    asset=asset,
                    source="reconciliation",
                    break_type="no_trusted_price_source",
                    severity="critical",
                    action="excluded_from_trusted_values",
                    likely_cause="all_available_price_sources_invalid_or_uncorroborated",
                    blocks_source_value=True,
                    details="Fail-closed: no price source passed structural validity and gross-outlier controls.",
                )
                continue

            # Volume selection is field-specific. Keep known base-unit volume when possible.
            volume_source = None
            volume_base = np.nan
            for source in [price_source, "kraken", "binance", "coinbase"]:
                if source is None or source == "reference" or d not in venue_idx[source].index:
                    continue
                row = venue_idx[source].loc[d]
                if pd.notna(row["volume_base"]) and float(row["volume_base"]) > 0:
                    volume_source = source
                    volume_base = float(row["volume_base"])
                    # Prefer a reported-base value over inferred Coinbase conversion.
                    if source != "coinbase":
                        break

            # Independent corroboration of selected close at materiality threshold.
            corroborators = []
            for source, close in close_obs:
                if source == price_source:
                    continue
                if pct_diff(close, float(selected["close"])) <= MATERIALITY_PCT:
                    corroborators.append(source)

            # Detect reference break specifically: reference disagrees, while >=2 venues agree.
            venue_closes = [
                (source, close) for source, close in close_obs if source in VENUES
            ]
            reference_break_resolved = False
            if np.isfinite(ref_close) and len(venue_closes) >= 2:
                venue_median = float(np.median([v for _, v in venue_closes]))
                if pct_diff(ref_close, venue_median) > MATERIALITY_PCT:
                    agreeing_venues = sum(
                        pct_diff(v, venue_median) <= MATERIALITY_PCT for _, v in venue_closes
                    )
                    reference_break_resolved = agreeing_venues >= 2

            confidence = "ok"
            if price_source != "kraken" or volume_source != price_source or reference_break_resolved:
                confidence = "adjusted"
            if len(corroborators) == 0:
                confidence = "review"

            if volume_source == price_source:
                source_label = price_source
            else:
                source_label = f"{price_source}_ohlc+{volume_source}_volume"

            trusted_rows.append(
                {
                    "date": d.date().isoformat(),
                    "asset": asset,
                    "open": float(selected["open"]),
                    "high": float(selected["high"]),
                    "low": float(selected["low"]),
                    "close": float(selected["close"]),
                    "volume_base": volume_base,
                    "source": source_label,
                    "confidence": confidence,
                }
            )

        calendar_control.append(
            {
                "asset": asset,
                "union_calendar_days": len(calendar),
                "trusted_rows_expected": len(calendar),
            }
        )

    trusted = pd.DataFrame(trusted_rows).sort_values(["asset", "date"]).reset_index(drop=True)
    breaks_df = pd.DataFrame(breaks)
    if not breaks_df.empty:
        breaks_df = breaks_df.sort_values(
            ["asset", "date", "source", "break_type"], na_position="last"
        ).reset_index(drop=True)
        breaks_df.insert(0, "break_id", [f"BRK-{i:04d}" for i in range(1, len(breaks_df) + 1)])

    # Final trusted-output controls.
    expected_rows = sum(x["trusted_rows_expected"] for x in calendar_control)
    assert len(trusted) == expected_rows, f"Trusted row count {len(trusted)} != expected {expected_rows}"
    assert not trusted.duplicated(["asset", "date"]).any(), "Duplicate asset/date in trusted output"

    nonexcluded = trusted[trusted["confidence"] != "excluded"].copy()
    assert (nonexcluded[PRICE_FIELDS] > 0).all().all(), "Nonpositive trusted price"
    assert (nonexcluded["volume_base"] > 0).all(), "Nonpositive trusted base volume"
    assert (nonexcluded["high"] >= nonexcluded[["open", "close"]].max(axis=1)).all(), "Trusted high invalid"
    assert (nonexcluded["low"] <= nonexcluded[["open", "close"]].min(axis=1)).all(), "Trusted low invalid"

    # Break summary and QA/control totals.
    break_summary = (
        breaks_df.groupby(["source", "break_type", "severity"], dropna=False)
        .size()
        .reset_index(name="break_count")
        .sort_values(["severity", "source", "break_type"])
        if not breaks_df.empty
        else pd.DataFrame(columns=["source", "break_type", "severity", "break_count"])
    )

    controls = []
    controls.append({"control": "trusted_row_count", "value": len(trusted), "status": "PASS" if len(trusted) == expected_rows else "FAIL"})
    controls.append({"control": "trusted_unique_asset_date", "value": trusted[["asset", "date"]].drop_duplicates().shape[0], "status": "PASS" if not trusted.duplicated(["asset", "date"]).any() else "FAIL"})
    controls.append({"control": "excluded_rows", "value": int((trusted["confidence"] == "excluded").sum()), "status": "PASS" if int((trusted["confidence"] == "excluded").sum()) == 0 else "REVIEW"})
    controls.append({"control": "adjusted_rows", "value": int((trusted["confidence"] == "adjusted").sum()), "status": "INFO"})
    controls.append({"control": "review_rows", "value": int((trusted["confidence"] == "review").sum()), "status": "PASS" if int((trusted["confidence"] == "review").sum()) == 0 else "REVIEW"})
    controls.append({"control": "trusted_ohlc_valid", "value": int(len(nonexcluded)), "status": "PASS"})
    controls.append({"control": "trusted_volume_positive", "value": int((nonexcluded["volume_base"] > 0).sum()), "status": "PASS"})
    controls_df = pd.DataFrame(controls)

    # Append missing-date counts and break counts to QA table.
    missing_counts = []
    for asset in ASSETS:
        cal = set(refs[asset]["date_norm"])
        for source in VENUES:
            cal.update(normalized[(asset, source)]["date_norm"])
        for source in VENUES:
            present = set(normalized[(asset, source)]["date_norm"])
            missing_counts.append({"asset": asset, "source": source, "missing_vs_union_calendar": len(cal - present)})
    qa = qa.merge(pd.DataFrame(missing_counts), on=["asset", "source"], how="left")

    trusted.to_csv(output_dir / "trusted_dataset.csv", index=False, float_format="%.8f")
    breaks_df.to_csv(output_dir / "breaks_report.csv", index=False)
    break_summary.to_csv(output_dir / "break_summary.csv", index=False)
    qa.to_csv(output_dir / "qa_summary.csv", index=False)
    controls_df.to_csv(output_dir / "control_totals.csv", index=False)

    print(f"Wrote {len(trusted)} trusted rows to {output_dir / 'trusted_dataset.csv'}")
    print(f"Wrote {len(breaks_df)} breaks to {output_dir / 'breaks_report.csv'}")
    print("Confidence counts:", trusted["confidence"].value_counts().to_dict())
    print("Source counts:", trusted["source"].value_counts().to_dict())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    reconcile(args.input_dir, args.output_dir)


if __name__ == "__main__":
    main()
