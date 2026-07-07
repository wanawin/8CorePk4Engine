#!/usr/bin/env python3
"""
C120_RULE_DAILY_PORTFOLIO_AUDIT_v1_6_SEED_ALIGNMENT_CERTIFIED

Daily portfolio replay audit for the user's 120-core Pick-4 AABC stable rule library.

Inputs:
  - history CSV/TXT
  - core_rule_library_stable_only_filtered.csv
  - optional ALL_OUT.zip for attempted score-baseline comparison

Outputs:
  - per-stream capture summaries
  - daily portfolio capture/wins-per-day summaries
  - selected rows / decisions
  - rule hit ledger sample
  - trait value audit

Proof level:
  This is a frozen-rule replay audit. If the supplied rule library was mined from the same
  period being replayed, this is NOT a strict blind retrain audit. It is still useful as a
  fast go/no-go test of whether the rule library contains actionable signal.
"""
from __future__ import annotations

import argparse
import csv
import itertools
import math
import os
import re
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

try:
    import numpy as np
    import pandas as pd
    try:
        pd.set_option("future.infer_string", False)
    except Exception:
        pass
except Exception as e:  # pragma: no cover
    print("ERROR: This tool requires pandas and numpy. Try: pip install -r requirements.txt")
    raise

try:
    import polars as pl  # used for large visible-matrix / movement outputs when available
except Exception:  # pragma: no cover
    pl = None

VERSION = "C120_RULE_DAILY_PORTFOLIO_AUDIT_v1_6_SEED_ALIGNMENT_CERTIFIED"
TOP_NS = [1, 2, 3, 5, 10, 12, 24, 40, 80, 120]
DAILY_BUDGETS = [20, 30, 50, 80, 100, 120, 150, 200]
CORE120 = ["".join(c) for c in itertools.combinations("0123456789", 3)]
MIRROR_PAIRS = [("0", "5"), ("1", "6"), ("2", "7"), ("3", "8"), ("4", "9")]


def now_s() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class StatusWriter:
    out_dir: Path
    started: float

    def __post_init__(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / "RUN_STARTED.txt").write_text(f"{VERSION}\nstarted_at={now_s()}\n", encoding="utf-8")
        self.write("started")

    def write(self, msg: str) -> None:
        elapsed = time.time() - self.started
        txt = f"{VERSION}\nstatus={msg}\nelapsed_seconds={elapsed:.1f}\nupdated_at={now_s()}\n"
        (self.out_dir / "00_LIVE_STATUS.txt").write_text(txt, encoding="utf-8")
        print(f"[{elapsed:8.1f}s] {msg}", flush=True)


def clean_core(x) -> str:
    if pd.isna(x):
        return ""
    s = re.sub(r"\D", "", str(x))
    if not s:
        return ""
    return s.zfill(3)[-3:]


def extract_base4(x) -> str:
    if pd.isna(x):
        return ""
    s = str(x)
    # If there is a comma for Fireball/Wild Ball, keep only before comma for base result.
    if "," in s:
        s = s.split(",", 1)[0]
    digits = re.findall(r"\d", s)
    if len(digits) < 4:
        return ""
    return "".join(digits[:4]).zfill(4)


def aabc_core(result4: str) -> str:
    s = extract_base4(result4)
    if len(s) != 4:
        return ""
    counts = sorted([s.count(d) for d in set(s)], reverse=True)
    if counts == [2, 1, 1]:
        return "".join(sorted(set(s)))
    return ""


def seed_structure(seed4: str) -> str:
    counts = sorted([seed4.count(d) for d in set(seed4)], reverse=True)
    if counts == [4]:
        return "AAAA"
    if counts == [3, 1]:
        return "AAAB"
    if counts == [2, 2]:
        return "AABB"
    if counts == [2, 1, 1]:
        return "AABC"
    if counts == [1, 1, 1, 1]:
        return "ABCD"
    return "UNKNOWN"


def mirror_signature_from_digits(digits: Iterable[str]) -> str:
    ds = set(digits)
    found = [a + b for a, b in MIRROR_PAIRS if a in ds and b in ds]
    if not found:
        return "mirror_none"
    # Existing rules appear to use one pair, e.g. mirror_27.
    return "mirror_" + "_".join(found)


def seed_traits(seed4: str) -> Dict[str, str]:
    s = extract_base4(seed4)
    if len(s) != 4:
        s = "0000"
    digs = [int(d) for d in s]
    digstr = [str(d) for d in digs]
    unique_sorted = "".join(sorted(set(digstr)))
    missing = "".join(d for d in "0123456789" if d not in set(digstr))
    total = sum(digs)
    high_count = sum(1 for d in digs if d >= 5)
    low_count = 4 - high_count
    spread = max(digs) - min(digs)
    if spread <= 2:
        spread_bucket = "spread_0_2"
    elif spread <= 4:
        spread_bucket = "spread_3_4"
    elif spread <= 6:
        spread_bucket = "spread_5_6"
    else:
        spread_bucket = "spread_7_plus"
    pairs = "pairs_" + "_".join("".join(p) for p in itertools.combinations(unique_sorted, 2)) if len(unique_sorted) >= 2 else "pairs_none"
    mirror = mirror_signature_from_digits(unique_sorted)
    return {
        "seed_sum_mod3": str(total % 3),
        "seed_sum_mod5": str(total % 5),
        "seed_first_last_sum": str(digs[0] + digs[-1]),
        "seed_parity_pattern": "".join("E" if d % 2 == 0 else "O" for d in digs),
        "seed_highlow_pattern": "".join("H" if d >= 5 else "L" for d in digs),
        "seed_structure": seed_structure(s),
        "seed_spread_bucket": spread_bucket,
        "seed_highlow_bucket": f"h{high_count}_l{low_count}",
        "seed_pos1": str(digs[0]),
        "seed_pair_signature": pairs,
        "seed_mirror_signature": mirror,
        "group_digitset": f"present={unique_sorted}|missing={missing}|{mirror}",
    }


def find_first_file(in_dir: Path, patterns: List[str]) -> Optional[Path]:
    files = []
    for pat in patterns:
        files += list(in_dir.glob(pat))
    files = [p for p in files if p.is_file()]
    if not files:
        return None
    # Prefer files with obvious names but not outputs.
    files.sort(key=lambda p: ("output" in p.name.lower(), len(p.name), p.name.lower()))
    return files[0]


def read_history_file(path: Path) -> pd.DataFrame:
    # Try CSV first, then TSV/TXT.
    suffix = path.suffix.lower()
    if suffix == ".csv":
        df = pd.read_csv(path, dtype=object, low_memory=False)
    else:
        # Try tab-delimited common lottery text.
        try:
            df = pd.read_csv(path, sep="\t", header=None, dtype=object, engine="python")
            if df.shape[1] >= 4:
                df = df.iloc[:, :4]
                df.columns = ["date", "state", "game", "result"]
            else:
                raise ValueError("not enough tab columns")
        except Exception:
            df = pd.read_csv(path, sep=None, engine="python", dtype=object)
    return df


def pick_col(cols: List[str], candidates: List[str]) -> Optional[str]:
    lower = {c.lower().strip(): c for c in cols}
    for cand in candidates:
        if cand.lower() in lower:
            return lower[cand.lower()]
    # fuzzy contains
    for c in cols:
        lc = c.lower().strip()
        for cand in candidates:
            if cand.lower() in lc:
                return c
    return None


def adapt_history(raw: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    cols = list(raw.columns)
    date_col = pick_col(cols, ["date", "draw_date", "play_date", "day"])
    result_col = pick_col(cols, ["result", "winning_number", "winning_numbers", "number", "base4", "draw"])
    stream_col = pick_col(cols, ["stream", "streamname", "stream_name"])
    state_col = pick_col(cols, ["state", "jurisdiction", "province", "region"])
    game_col = pick_col(cols, ["game", "game_name", "lottery", "draw_name"])

    audit = []
    audit.append({"field": "date_col", "mapped_to": date_col or ""})
    audit.append({"field": "result_col", "mapped_to": result_col or ""})
    audit.append({"field": "stream_col", "mapped_to": stream_col or ""})
    audit.append({"field": "state_col", "mapped_to": state_col or ""})
    audit.append({"field": "game_col", "mapped_to": game_col or ""})

    if date_col is None or result_col is None:
        # If headerless 4 columns came in as 0,1,2,3.
        if len(cols) >= 4:
            date_col, state_col, game_col, result_col = cols[0], cols[1], cols[2], cols[3]
        else:
            raise ValueError("Could not identify required date/result columns in history file.")

    df = raw.copy()
    out = pd.DataFrame()
    out["draw_date"] = pd.to_datetime(df[date_col], errors="coerce").dt.date.astype(str)
    if stream_col:
        out["stream"] = df[stream_col].astype(str).str.strip()
    elif state_col and game_col:
        out["stream"] = df[state_col].astype(str).str.strip() + " | " + df[game_col].astype(str).str.strip()
    elif state_col:
        out["stream"] = df[state_col].astype(str).str.strip()
    else:
        out["stream"] = "STREAM_UNKNOWN"
    out["raw_result"] = df[result_col].astype(str)
    out["base4"] = out["raw_result"].map(extract_base4)
    out = out[(out["draw_date"] != "NaT") & (out["base4"].str.len() == 4) & (out["stream"].str.len() > 0)].copy()
    out = out.drop_duplicates(subset=["draw_date", "stream", "base4"], keep="last")
    out = out.sort_values(["stream", "draw_date", "base4"]).reset_index(drop=True)

    # CRITICAL ALIGNMENT LOCK:
    # seed = prior same-stream base4 result
    # actual_core = current row/result AABC core
    # Never derive actual_core from seed. The audit below proves this row by row.
    out["prior_draw_date"] = out.groupby("stream")["draw_date"].shift(1)
    out["prior_result_used_as_seed"] = out.groupby("stream")["base4"].shift(1)
    out["seed"] = out["prior_result_used_as_seed"]
    out["seed_core"] = out["seed"].map(aabc_core).fillna("")
    out["actual_core"] = out["base4"].map(aabc_core)
    out["is_aabc_winner"] = out["actual_core"].str.len().eq(3)
    out["seed_equals_current_result"] = out["seed"].fillna("").astype(str).eq(out["base4"].astype(str))
    out["seed_core_equals_actual_core"] = out["seed_core"].fillna("").astype(str).eq(out["actual_core"].fillna("").astype(str)) & out["actual_core"].fillna("").astype(str).str.len().eq(3)
    out["event_id"] = np.arange(len(out), dtype=np.int64)

    audit_df = pd.DataFrame(audit)
    return out, audit_df



def build_seed_alignment_audit(hist: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Certify that every replay row uses prior same-stream result as seed and current result as winner.

    This is intentionally redundant and human-readable because a prior project mistake measured
    seeds against themselves. A row is VALID when either it has no seed yet (first draw for stream)
    or prior_draw_date is strictly earlier than draw_date and seed equals prior_result_used_as_seed.
    A rare exact repeat can make seed_equals_current_result=True; that is a warning field, not an
    automatic failure, because the same 4-digit result can legitimately repeat on consecutive draws.
    """
    cols = [
        "event_id", "draw_date", "stream", "prior_draw_date", "prior_result_used_as_seed",
        "seed", "base4", "raw_result", "seed_core", "actual_core", "is_aabc_winner",
        "seed_equals_current_result", "seed_core_equals_actual_core",
    ]
    available = [c for c in cols if c in hist.columns]
    aud = hist[available].copy()
    aud = aud.rename(columns={"base4": "current_result", "raw_result": "current_raw_result"})
    aud["seed_present"] = aud["seed"].fillna("").astype(str).str.len().eq(4)
    aud["seed_source_is_prior_result"] = aud["seed"].fillna("").astype(str).eq(aud["prior_result_used_as_seed"].fillna("").astype(str))
    aud["prior_date_dt"] = pd.to_datetime(aud["prior_draw_date"], errors="coerce")
    aud["draw_date_dt"] = pd.to_datetime(aud["draw_date"], errors="coerce")
    aud["prior_date_before_draw_date"] = aud["prior_date_dt"].lt(aud["draw_date_dt"])
    aud["alignment_status"] = np.where(
        ~aud["seed_present"],
        "NO_PRIOR_SEED_FIRST_ROW_FOR_STREAM",
        np.where(
            aud["seed_source_is_prior_result"] & aud["prior_date_before_draw_date"],
            "VALID_PRIOR_STREAM_RESULT_TO_CURRENT_WINNER",
            "BAD_ALIGNMENT_CHECK_REQUIRED",
        ),
    )
    aud = aud.drop(columns=["prior_date_dt", "draw_date_dt"])

    seeded = aud[aud["seed_present"]].copy()
    summary = pd.DataFrame([{
        "total_history_rows": len(aud),
        "seeded_rows": len(seeded),
        "aabc_winner_rows": int(aud.get("is_aabc_winner", pd.Series(dtype=bool)).fillna(False).sum()),
        "valid_seeded_rows": int((seeded["alignment_status"] == "VALID_PRIOR_STREAM_RESULT_TO_CURRENT_WINNER").sum()) if not seeded.empty else 0,
        "bad_alignment_rows": int((seeded["alignment_status"] == "BAD_ALIGNMENT_CHECK_REQUIRED").sum()) if not seeded.empty else 0,
        "seed_equals_current_result_rows_warning_only": int(seeded.get("seed_equals_current_result", pd.Series(dtype=bool)).fillna(False).sum()) if not seeded.empty else 0,
        "seed_core_equals_actual_core_rows_warning_only": int(seeded.get("seed_core_equals_actual_core", pd.Series(dtype=bool)).fillna(False).sum()) if not seeded.empty else 0,
        "certification": "PASS" if seeded.empty or int((seeded["alignment_status"] == "BAD_ALIGNMENT_CHECK_REQUIRED").sum()) == 0 else "FAIL",
        "meaning": "seed is prior same-stream result; actual_core is current result core; exact seed/current repeats are warnings only",
    }])
    return aud, summary


def assert_seed_alignment_ok(seed_alignment_summary: pd.DataFrame) -> None:
    if seed_alignment_summary is None or seed_alignment_summary.empty:
        raise ValueError("Seed alignment summary was not created.")
    row = seed_alignment_summary.iloc[0]
    if str(row.get("certification", "FAIL")) != "PASS":
        raise ValueError(
            "Seed alignment certification failed: seeded rows exist where prior_draw_date is not before draw_date "
            "or seed does not equal prior same-stream result. See SEED_ALIGNMENT_AUDIT.csv."
        )

def normalize_rules(raw: pd.DataFrame) -> pd.DataFrame:
    needed = ["target_core", "trait_1", "value_1", "support", "target_hits", "precision", "lift_vs_competitor"]
    missing = [c for c in needed if c not in raw.columns]
    if missing:
        raise ValueError(f"Rule file missing required columns: {missing}")
    df = raw.copy()
    if "enabled" in df.columns:
        df = df[df["enabled"].fillna(1).astype(str).isin(["1", "true", "True", "YES", "yes"])].copy()
    df["target_core"] = df["target_core"].map(clean_core)
    if "vs_competitor" in df.columns:
        df["vs_competitor"] = df["vs_competitor"].map(clean_core)
    else:
        df["vs_competitor"] = ""
    for c in ["trait_1", "trait_2", "value_1", "value_2"]:
        if c not in df.columns:
            df[c] = ""
        df[c] = df[c].fillna("").astype(str).str.strip()
    for c in ["support", "target_hits", "precision", "lift_vs_competitor", "base_pair_rate"]:
        if c not in df.columns:
            df[c] = 0
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    if "combined_rule_id" not in df.columns:
        df["combined_rule_id"] = [f"RULE_{i+1:06d}" for i in range(len(df))]
    df["rule_strength_balanced"] = np.log1p(df["support"].clip(lower=0)) * df["precision"].clip(lower=0) * df["lift_vs_competitor"].clip(lower=0)
    df["rule_strength_hits"] = df["target_hits"].clip(lower=0)
    df["rule_strength_precision_hits"] = df["target_hits"].clip(lower=0) * df["precision"].clip(lower=0)

    # Normalize trait order so same pair order is used when joining.
    rows = []
    for idx, r in df.iterrows():
        t1, v1 = r["trait_1"], str(r["value_1"])
        t2, v2 = r["trait_2"], str(r["value_2"])
        if t2 and t2.lower() != "nan":
            if t2 < t1:
                t1, v1, t2, v2 = t2, v2, t1, v1
        else:
            t2, v2 = "", ""
        rows.append((t1, v1, t2, v2))
    norm = pd.DataFrame(rows, columns=["trait_a", "value_a", "trait_b", "value_b"], index=df.index)
    df = pd.concat([df, norm], axis=1)
    df = df[df["target_core"].isin(CORE120)].copy()
    return df.reset_index(drop=True)


def apply_last_days(events: pd.DataFrame, last_days: Optional[int], start_date: Optional[str], end_date: Optional[str]) -> pd.DataFrame:
    df = events.copy()
    if start_date:
        df = df[df["draw_date"] >= start_date]
    if end_date:
        df = df[df["draw_date"] <= end_date]
    if last_days:
        dates = sorted(df["draw_date"].dropna().unique().tolist())
        keep_dates = set(dates[-int(last_days):])
        df = df[df["draw_date"].isin(keep_dates)]
    return df.reset_index(drop=True)


def attach_seed_traits(events: pd.DataFrame) -> pd.DataFrame:
    trait_rows = [seed_traits(s) for s in events["seed"].fillna("").astype(str).tolist()]
    traits = pd.DataFrame(trait_rows)
    out = pd.concat([events.reset_index(drop=True), traits], axis=1)
    return out


def match_rules(events: pd.DataFrame, rules: pd.DataFrame, status: StatusWriter) -> pd.DataFrame:
    trait_cols = sorted(set(rules["trait_a"].dropna()).union(set(rules["trait_b"].dropna())))
    trait_cols = [t for t in trait_cols if t and t in events.columns]
    all_matches = []
    pair_groups = list(rules.groupby(["trait_a", "trait_b"], dropna=False))
    total = len(pair_groups)
    for i, ((ta, tb), sub) in enumerate(pair_groups, 1):
        if i == 1 or i == total or i % 10 == 0:
            status.write(f"matching rule trait groups {i}/{total}: {ta}+{tb or 'SINGLE'}")
        if ta not in events.columns:
            continue
        sub = sub.copy()
        if not tb or tb == "nan" or tb not in events.columns:
            base_cols = ["event_id", "draw_date", "stream", "seed", "actual_core"]
            for extra_col in ["prior_draw_date", "prior_result_used_as_seed", "base4", "seed_core"]:
                if extra_col in events.columns and extra_col not in base_cols:
                    base_cols.append(extra_col)
            left = events[base_cols + [ta]].copy()
            left[ta] = left[ta].astype(str)
            sub["value_a"] = sub["value_a"].astype(str)
            m = left.merge(sub, left_on=ta, right_on="value_a", how="inner")
        else:
            base_cols = ["event_id", "draw_date", "stream", "seed", "actual_core"]
            for extra_col in ["prior_draw_date", "prior_result_used_as_seed", "base4", "seed_core"]:
                if extra_col in events.columns and extra_col not in base_cols:
                    base_cols.append(extra_col)
            left = events[base_cols + [ta, tb]].copy()
            left[ta] = left[ta].astype(str)
            left[tb] = left[tb].astype(str)
            sub["value_a"] = sub["value_a"].astype(str)
            sub["value_b"] = sub["value_b"].astype(str)
            m = left.merge(sub, left_on=[ta, tb], right_on=["value_a", "value_b"], how="inner")
        if not m.empty:
            all_matches.append(m)
    if not all_matches:
        return pd.DataFrame()
    matches = pd.concat(all_matches, ignore_index=True)
    matches["is_winner_rule_target"] = matches["target_core"].eq(matches["actual_core"])
    return matches


def aggregate_scores(matches: pd.DataFrame, config_name: str, rule_filter, score_col: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
    if matches.empty:
        return pd.DataFrame(), pd.DataFrame()
    m = matches[rule_filter(matches)].copy()
    if m.empty:
        return pd.DataFrame(), pd.DataFrame()
    group_cols = ["event_id", "draw_date", "stream", "seed", "actual_core", "target_core"]
    for extra_col in ["prior_draw_date", "prior_result_used_as_seed", "base4", "seed_core"]:
        if extra_col in m.columns and extra_col not in group_cols:
            group_cols.insert(-1, extra_col)
    grouped = m.groupby(group_cols, as_index=False).agg(
        evidence_score=(score_col, "sum"),
        rule_count=("combined_rule_id", "count"),
        max_precision=("precision", "max"),
        max_lift=("lift_vs_competitor", "max"),
        total_support=("support", "sum"),
        total_target_hits=("target_hits", "sum"),
    )
    grouped["config"] = config_name
    grouped = grouped.sort_values(["event_id", "evidence_score", "rule_count", "max_lift", "target_core"], ascending=[True, False, False, False, True])
    grouped["rule_replay_rank"] = grouped.groupby("event_id").cumcount() + 1
    # Decision table: winner rank if selected evidence exists.
    winners = grouped[grouped["target_core"].eq(grouped["actual_core"])].copy()
    decisions = grouped.groupby("event_id", as_index=False).agg(
        evidence_core_count=("target_core", "nunique"),
        top_core=("target_core", "first"),
        top_score=("evidence_score", "first"),
        top_rule_count=("rule_count", "first"),
    )
    if not winners.empty:
        winners = winners[["event_id", "rule_replay_rank", "evidence_score", "rule_count", "max_precision", "max_lift", "total_support", "total_target_hits"]].rename(columns={
            "rule_replay_rank": "winner_rule_replay_rank",
            "evidence_score": "winner_evidence_score",
            "rule_count": "winner_rule_count",
            "max_precision": "winner_max_precision",
            "max_lift": "winner_max_lift",
            "total_support": "winner_total_support",
            "total_target_hits": "winner_total_target_hits",
        })
        decisions = decisions.merge(winners, on="event_id", how="left")
    else:
        decisions["winner_rule_replay_rank"] = np.nan
    decisions["config"] = config_name
    return grouped, decisions


def summarize_capture(decisions: pd.DataFrame, event_base: pd.DataFrame, config_name: str) -> pd.DataFrame:
    # event_base contains all AABC eligible events; decisions only events with at least one rule match.
    base_cols = ["event_id", "draw_date", "stream", "seed", "actual_core"]
    for extra_col in ["prior_draw_date", "prior_result_used_as_seed", "base4", "seed_core"]:
        if extra_col in event_base.columns and extra_col not in base_cols:
            base_cols.append(extra_col)
    base = event_base[base_cols].copy()
    if decisions is None or decisions.empty or "event_id" not in decisions.columns:
        d = base.copy()
        d["winner_rule_replay_rank"] = np.nan
        d["evidence_core_count"] = 0
    else:
        d = base.merge(decisions, on="event_id", how="left")
    total = len(d)
    rows = []
    for n in TOP_NS:
        hit = d["winner_rule_replay_rank"].le(n).fillna(False)
        selected = d["evidence_core_count"].fillna(0).clip(upper=n)
        rows.append({
            "config": config_name,
            "top_n": n,
            "eligible_aabc_winner_rows": total,
            "captured_winners": int(hit.sum()),
            "capture_rate": float(hit.mean()) if total else 0.0,
            "avg_plays_per_stream_date": float(selected.mean()) if total else 0.0,
            "events_with_any_rule_evidence": int(d["evidence_core_count"].fillna(0).gt(0).sum()),
            "any_rule_evidence_rate": float(d["evidence_core_count"].fillna(0).gt(0).mean()) if total else 0.0,
        })
    return pd.DataFrame(rows)




def summarize_daily_portfolio(grouped: pd.DataFrame, eval_events: pd.DataFrame, config_name: str) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Rank stream/core opportunities across each day and count daily AABC wins at fixed budgets."""
    base_days = sorted(eval_events["draw_date"].dropna().unique().tolist())
    total_winner_rows = len(eval_events)
    winners_by_day = eval_events.groupby("draw_date").size().reindex(base_days, fill_value=0)
    if grouped is None or grouped.empty:
        rows = []
        daily_rows = []
        for b in DAILY_BUDGETS:
            rows.append({
                "config": config_name, "daily_budget": b, "test_days": len(base_days),
                "eligible_aabc_winner_rows": total_winner_rows, "captured_winner_rows": 0,
                "capture_rate": 0.0, "avg_selected_plays_per_day": 0.0,
                "avg_aabc_winners_per_day": float(winners_by_day.mean()) if len(winners_by_day) else 0.0,
                "avg_captured_wins_per_day": 0.0, "median_captured_wins_per_day": 0.0,
                "max_captured_wins_in_day": 0, "days_with_1plus_wins": 0,
                "days_with_2plus_wins": 0, "days_with_3plus_wins": 0,
                "rate_days_1plus": 0.0, "rate_days_2plus": 0.0, "rate_days_3plus": 0.0,
            })
        return pd.DataFrame(rows), pd.DataFrame(daily_rows), pd.DataFrame()

    g = grouped.copy()
    g["is_aabc_actual"] = g["actual_core"].fillna("").astype(str).str.len().eq(3)
    g["is_portfolio_hit"] = g["is_aabc_actual"] & g["target_core"].eq(g["actual_core"])
    g = g.sort_values(
        ["draw_date", "evidence_score", "rule_count", "max_lift", "max_precision", "stream", "target_core"],
        ascending=[True, False, False, False, False, True, True],
    ).reset_index(drop=True)
    g["daily_opportunity_rank"] = g.groupby("draw_date").cumcount() + 1

    summary_rows = []
    daily_rows = []
    selected_parts = []
    for b in DAILY_BUDGETS:
        sel = g[g["daily_opportunity_rank"] <= b].copy()
        if b in {20, 30, 50, 80, 120}:
            keep = sel.copy()
            keep["daily_budget"] = b
            selected_parts.append(keep)
        captured_ids = set(sel.loc[sel["is_portfolio_hit"], "event_id"].tolist())
        day_hit = sel.groupby("draw_date")["is_portfolio_hit"].sum().reindex(base_days, fill_value=0).astype(int)
        day_plays = sel.groupby("draw_date").size().reindex(base_days, fill_value=0).astype(int)
        for d in base_days:
            daily_rows.append({
                "config": config_name,
                "daily_budget": b,
                "draw_date": d,
                "selected_plays": int(day_plays.loc[d]),
                "aabc_winners_available": int(winners_by_day.loc[d]),
                "captured_wins": int(day_hit.loc[d]),
                "hit_day_1plus": int(day_hit.loc[d] >= 1),
                "hit_day_2plus": int(day_hit.loc[d] >= 2),
                "hit_day_3plus": int(day_hit.loc[d] >= 3),
            })
        summary_rows.append({
            "config": config_name,
            "daily_budget": b,
            "test_days": len(base_days),
            "eligible_aabc_winner_rows": total_winner_rows,
            "captured_winner_rows": len(captured_ids),
            "capture_rate": (len(captured_ids) / total_winner_rows) if total_winner_rows else 0.0,
            "avg_selected_plays_per_day": float(day_plays.mean()) if len(day_plays) else 0.0,
            "avg_aabc_winners_per_day": float(winners_by_day.mean()) if len(winners_by_day) else 0.0,
            "avg_captured_wins_per_day": float(day_hit.mean()) if len(day_hit) else 0.0,
            "median_captured_wins_per_day": float(day_hit.median()) if len(day_hit) else 0.0,
            "max_captured_wins_in_day": int(day_hit.max()) if len(day_hit) else 0,
            "days_with_1plus_wins": int((day_hit >= 1).sum()),
            "days_with_2plus_wins": int((day_hit >= 2).sum()),
            "days_with_3plus_wins": int((day_hit >= 3).sum()),
            "rate_days_1plus": float((day_hit >= 1).mean()) if len(day_hit) else 0.0,
            "rate_days_2plus": float((day_hit >= 2).mean()) if len(day_hit) else 0.0,
            "rate_days_3plus": float((day_hit >= 3).mean()) if len(day_hit) else 0.0,
        })
    selected = pd.concat(selected_parts, ignore_index=True) if selected_parts else pd.DataFrame()
    return pd.DataFrame(summary_rows), pd.DataFrame(daily_rows), selected


def portfolio_verdict(portfolio_summary: pd.DataFrame) -> str:
    lines = [VERSION, f"created_at={now_s()}"]
    if portfolio_summary is None or portfolio_summary.empty:
        lines += ["FINAL_VERDICT=STOP_RULE_DAILY_PORTFOLIO_PATH", "reason=No portfolio summary was produced."]
        return "\n".join(lines) + "\n"
    # Choose strongest config at budget 80, with budget 50 and 120 as context.
    def best_at(b):
        x = portfolio_summary[portfolio_summary["daily_budget"] == b].copy()
        if x.empty:
            return None
        return x.sort_values(["avg_captured_wins_per_day", "rate_days_1plus", "capture_rate"], ascending=[False, False, False]).iloc[0]
    for b in [30, 50, 80, 120]:
        r = best_at(b)
        if r is not None:
            lines.append(f"best_budget_{b}_config={r['config']}")
            lines.append(f"best_budget_{b}_avg_wins_per_day={r['avg_captured_wins_per_day']:.4f}")
            lines.append(f"best_budget_{b}_days_1plus={r['days_with_1plus_wins']}/{r['test_days']} = {r['rate_days_1plus']:.2%}")
            lines.append(f"best_budget_{b}_days_2plus={r['days_with_2plus_wins']}/{r['test_days']} = {r['rate_days_2plus']:.2%}")
            lines.append(f"best_budget_{b}_capture={r['captured_winner_rows']}/{r['eligible_aabc_winner_rows']} = {r['capture_rate']:.2%}")
    r80 = best_at(80)
    r120 = best_at(120)
    decision = "STOP_RULE_DAILY_PORTFOLIO_PATH"
    reason = "Daily portfolio did not show enough wins/day or hit-day consistency."
    if r80 is not None:
        if float(r80["avg_captured_wins_per_day"]) >= 1.5 and float(r80["rate_days_1plus"]) >= 0.60:
            decision = "BUILD_RULE_DAILY_PORTFOLIO_PLAYER_CANDIDATE"
            reason = "Budget 80 produced a meaningful daily win portfolio signal."
        elif float(r80["avg_captured_wins_per_day"]) >= 0.9 or float(r80["rate_days_1plus"]) >= 0.45:
            decision = "BORDERLINE_RUN_180DAY_OR_COMPARE_WITH_ALL_OUT"
            reason = "Budget 80 showed some daily signal; confirm on larger/blind window and baseline."
    if decision.startswith("STOP") and r120 is not None:
        if float(r120["avg_captured_wins_per_day"]) >= 1.5 and float(r120["rate_days_1plus"]) >= 0.55:
            decision = "BORDERLINE_PORTFOLIO_TOO_MANY_PLAYS"
            reason = "Budget 120 shows signal, but play count may be too high."
    lines.append(f"FINAL_VERDICT={decision}")
    lines.append(f"reason={reason}")
    lines.append("proof_note=Portfolio audit ranks all stream/core opportunities across each day; capture is evaluated only on AABC winner rows, but non-AABC streams can consume plays.")
    return "\n".join(lines) + "\n"


def core_digit_overlap(core_a: str, core_b: str) -> int:
    a = set(clean_core(core_a)) if core_a else set()
    b = set(clean_core(core_b)) if core_b else set()
    return len(a & b)


def rank_bucket_120(x) -> str:
    try:
        x = int(float(x))
    except Exception:
        return "NO_EVIDENCE"
    if x <= 0:
        return "NO_EVIDENCE"
    if x == 1: return "R001"
    if x <= 3: return "R002_003"
    if x <= 6: return "R004_006"
    if x <= 12: return "R007_012"
    if x <= 24: return "R013_024"
    if x <= 40: return "R025_040"
    if x <= 80: return "R041_080"
    return "R081_120"


def daily_rank_bucket(x) -> str:
    try:
        x = int(float(x))
    except Exception:
        return "NO_EVIDENCE"
    if x <= 0:
        return "NO_EVIDENCE"
    if x <= 20: return "D001_020"
    if x <= 30: return "D021_030"
    if x <= 50: return "D031_050"
    if x <= 80: return "D051_080"
    if x <= 100: return "D081_100"
    if x <= 120: return "D101_120"
    if x <= 150: return "D121_150"
    if x <= 200: return "D151_200"
    return "D201_PLUS"


def movement_bucket(delta) -> str:
    # Negative delta = rank improved / moved toward top. Positive = moved deeper.
    if pd.isna(delta):
        return "FIRST_OBSERVED"
    try:
        d = float(delta)
    except Exception:
        return "UNKNOWN"
    if d <= -80: return "UP_80_PLUS"
    if d <= -40: return "UP_40_79"
    if d <= -20: return "UP_20_39"
    if d <= -10: return "UP_10_19"
    if d <= -4: return "UP_04_09"
    if d <= -1: return "UP_01_03"
    if d == 0: return "SAME"
    if d <= 3: return "DOWN_01_03"
    if d <= 9: return "DOWN_04_09"
    if d <= 19: return "DOWN_10_19"
    if d <= 39: return "DOWN_20_39"
    if d <= 79: return "DOWN_40_79"
    return "DOWN_80_PLUS"


def write_rank_movement_signal_outputs(out_dir: Path, grouped: pd.DataFrame, winner_rows: pd.DataFrame, status: StatusWriter) -> None:
    """Low-cost signal capture: evaluate rank axis, movement axis, and rank x movement together.

    Movement is computed for each candidate core within the same stream across consecutive
    seeded dates. This is pre-result information for that row, so it is a usable daily
    trait candidate. Winner-only ledgers are also summarized separately for diagnosis.
    """
    if grouped is None or grouped.empty:
        return
    status.write("building rank x movement signal summaries")
    gm = grouped.sort_values(["stream", "target_core", "draw_date"]).copy()
    gm["prev_matrix_rank_same_stream_core"] = gm.groupby(["stream", "target_core"])["matrix_rank_in_stream"].shift(1)
    gm["prev_daily_opportunity_rank_same_stream_core"] = gm.groupby(["stream", "target_core"])["daily_opportunity_rank"].shift(1)
    gm["matrix_rank_delta_same_stream_core"] = gm["matrix_rank_in_stream"] - gm["prev_matrix_rank_same_stream_core"]
    gm["daily_rank_delta_same_stream_core"] = gm["daily_opportunity_rank"] - gm["prev_daily_opportunity_rank_same_stream_core"]
    gm["matrix_rank_bucket"] = gm["matrix_rank_in_stream"].map(rank_bucket_120)
    gm["daily_rank_bucket"] = gm["daily_opportunity_rank"].map(daily_rank_bucket)
    gm["matrix_movement_bucket"] = gm["matrix_rank_delta_same_stream_core"].map(movement_bucket)
    gm["daily_movement_bucket"] = gm["daily_rank_delta_same_stream_core"].map(movement_bucket)
    gm["is_hit"] = gm["target_core"].eq(gm["actual_core"]) & gm["actual_core"].fillna("").astype(str).str.len().eq(3)

    # Save compact visible candidate rows carrying both axes; top200 + winners only to stay small.
    compact = gm[(gm["daily_opportunity_rank"] <= 200) | gm["is_hit"]].copy()
    compact_cols = [
        "config", "draw_date", "stream", "seed", "actual_core", "target_core",
        "matrix_rank_in_stream", "matrix_rank_bucket", "prev_matrix_rank_same_stream_core",
        "matrix_rank_delta_same_stream_core", "matrix_movement_bucket",
        "daily_opportunity_rank", "daily_rank_bucket", "prev_daily_opportunity_rank_same_stream_core",
        "daily_rank_delta_same_stream_core", "daily_movement_bucket",
        "evidence_score", "rule_count", "max_precision", "max_lift", "total_support",
        "total_target_hits", "is_hit"
    ] + [f"selected_budget_{b}" for b in DAILY_BUDGETS if f"selected_budget_{b}" in gm.columns]
    compact_cols = [c for c in compact_cols if c in compact.columns]
    compact[compact_cols].to_csv(out_dir / "VISIBLE_RANK_MOVEMENT_MATRIX_TOP200_PLUS_WINNERS.csv", index=False)

    def summarize(group_cols: List[str], axis_name: str) -> pd.DataFrame:
        rows = gm.groupby(group_cols, dropna=False).agg(
            candidate_rows=("target_core", "size"),
            winner_hits=("is_hit", "sum"),
            avg_evidence_score=("evidence_score", "mean"),
            avg_rule_count=("rule_count", "mean"),
            max_lift_seen=("max_lift", "max"),
        ).reset_index()
        rows["hit_rate_per_candidate_row"] = rows["winner_hits"] / rows["candidate_rows"].replace(0, np.nan)
        rows["axis_test"] = axis_name
        return rows.sort_values(["winner_hits", "hit_rate_per_candidate_row", "candidate_rows"], ascending=[False, False, False])

    def exact_rank_summary(rank_col: str, movement_col: str, axis_name: str) -> pd.DataFrame:
        """Exact single-row rank value audit.

        This answers the user's row question directly: do winners appear on exact
        row values like 1, 7, 65, or 80 rather than needing to play a whole
        range such as 75-80?
        """
        tmp = gm.copy()
        tmp = tmp[pd.notna(tmp[rank_col])].copy()
        if tmp.empty:
            return pd.DataFrame()
        tmp[rank_col] = pd.to_numeric(tmp[rank_col], errors="coerce")
        tmp = tmp[pd.notna(tmp[rank_col])].copy()
        if tmp.empty:
            return pd.DataFrame()
        tmp["single_row_rank_value"] = tmp[rank_col].astype(int)
        rows = tmp.groupby("single_row_rank_value", dropna=False).agg(
            candidate_rows=("target_core", "size"),
            winner_hits=("is_hit", "sum"),
            days_present=("draw_date", "nunique"),
            hit_days=("draw_date", lambda s: tmp.loc[s.index, "is_hit"].groupby(tmp.loc[s.index, "draw_date"]).max().sum()),
            avg_evidence_score=("evidence_score", "mean"),
            avg_rule_count=("rule_count", "mean"),
            max_lift_seen=("max_lift", "max"),
        ).reset_index()
        total_hits = int(tmp["is_hit"].sum())
        total_days = max(1, int(gm["draw_date"].nunique()))
        rows["axis_test"] = axis_name
        rows["rank_col"] = rank_col
        rows["movement_col"] = movement_col
        rows["winner_share_of_axis_hits"] = rows["winner_hits"] / total_hits if total_hits else 0.0
        rows["hit_rate_per_candidate_row"] = rows["winner_hits"] / rows["candidate_rows"].replace(0, np.nan)
        rows["estimated_plays_per_day_if_playing_this_exact_row"] = rows["candidate_rows"] / total_days
        rows["hit_days_rate"] = rows["hit_days"] / total_days
        if movement_col in tmp.columns:
            # Most common movement bucket observed on this exact row, useful for quick filtering.
            mode_map = tmp.groupby("single_row_rank_value")[movement_col].agg(lambda x: x.value_counts(dropna=False).index[0] if len(x) else "")
            rows["most_common_movement_bucket_on_row"] = rows["single_row_rank_value"].map(mode_map).fillna("")
        return rows.sort_values(["winner_hits", "winner_share_of_axis_hits", "single_row_rank_value"], ascending=[False, False, True])

    def greedy_exact_row_set(exact_df: pd.DataFrame, axis_name: str) -> pd.DataFrame:
        if exact_df is None or exact_df.empty:
            return pd.DataFrame()
        x = exact_df.sort_values(["winner_hits", "hit_rate_per_candidate_row", "single_row_rank_value"], ascending=[False, False, True]).copy()
        total_hits = max(1, int(x["winner_hits"].sum()))
        test_days = max(1, int(gm["draw_date"].nunique()))
        out_rows = []
        chosen = []
        cum_hits = 0
        cum_candidates = 0
        checkpoints = set([1,2,3,4,5,6,8,10,12,15,20,25,30,40,50,60,80,100,120,150,200])
        for i, r in enumerate(x.itertuples(index=False), start=1):
            chosen.append(str(int(getattr(r, "single_row_rank_value"))))
            cum_hits += int(getattr(r, "winner_hits"))
            cum_candidates += int(getattr(r, "candidate_rows"))
            if i in checkpoints or i == len(x):
                out_rows.append({
                    "axis_test": axis_name,
                    "exact_row_values_selected_count": i,
                    "exact_row_values_selected": ",".join(chosen),
                    "cumulative_winner_hits": cum_hits,
                    "winner_share_of_axis_hits": cum_hits / total_hits,
                    "candidate_rows_total": cum_candidates,
                    "estimated_plays_per_day_from_exact_rows": cum_candidates / test_days,
                })
        return pd.DataFrame(out_rows)

    def exact_movement_summary(delta_col: str, axis_name: str) -> pd.DataFrame:
        """Exact movement-value audit.

        This avoids hiding signal inside UP_20_39 / DOWN_20_39 buckets.
        Negative values mean the row moved toward the top; positive values mean it moved deeper.
        """
        if delta_col not in gm.columns:
            return pd.DataFrame()
        tmp = gm.copy()
        tmp["exact_movement_value"] = pd.to_numeric(tmp[delta_col], errors="coerce")
        tmp["exact_movement_value_label"] = tmp["exact_movement_value"].map(lambda x: "FIRST_OBSERVED" if pd.isna(x) else str(int(x)))
        rows = tmp.groupby("exact_movement_value_label", dropna=False).agg(
            candidate_rows=("target_core", "size"),
            winner_hits=("is_hit", "sum"),
            days_present=("draw_date", "nunique"),
            hit_days=("draw_date", lambda s: tmp.loc[s.index, "is_hit"].groupby(tmp.loc[s.index, "draw_date"]).max().sum()),
            avg_evidence_score=("evidence_score", "mean"),
            avg_rule_count=("rule_count", "mean"),
            max_lift_seen=("max_lift", "max"),
        ).reset_index()
        total_hits = int(tmp["is_hit"].sum())
        total_days = max(1, int(gm["draw_date"].nunique()))
        rows["axis_test"] = axis_name
        rows["movement_delta_col"] = delta_col
        rows["winner_share_of_axis_hits"] = rows["winner_hits"] / total_hits if total_hits else 0.0
        rows["hit_rate_per_candidate_row"] = rows["winner_hits"] / rows["candidate_rows"].replace(0, np.nan)
        rows["estimated_plays_per_day_if_playing_this_exact_movement"] = rows["candidate_rows"] / total_days
        rows["hit_days_rate"] = rows["hit_days"] / total_days
        # Sort numeric movement values in signal order while keeping FIRST_OBSERVED last unless it is high-hit.
        def sort_key(v):
            if v == "FIRST_OBSERVED":
                return 999999
            try:
                return int(v)
            except Exception:
                return 999998
        rows["exact_movement_sort"] = rows["exact_movement_value_label"].map(sort_key)
        return rows.sort_values(["winner_hits", "hit_rate_per_candidate_row", "exact_movement_sort"], ascending=[False, False, True])

    def exact_rank_x_exact_movement_summary(rank_col: str, delta_col: str, axis_name: str) -> pd.DataFrame:
        """Exact 2-axis audit: exact rank value x exact movement value.

        This is the non-bucketed version of the heatmap. It answers whether row 7 + movement -4
        has different value than row 7 + movement -20, and whether scattered rows like 1, 7, 65,
        and 80 are useful without paying for full ranges.
        """
        if rank_col not in gm.columns or delta_col not in gm.columns:
            return pd.DataFrame()
        tmp = gm.copy()
        tmp["exact_rank_value"] = pd.to_numeric(tmp[rank_col], errors="coerce")
        tmp = tmp[pd.notna(tmp["exact_rank_value"])].copy()
        if tmp.empty:
            return pd.DataFrame()
        tmp["exact_rank_value"] = tmp["exact_rank_value"].astype(int)
        tmp["exact_movement_value"] = pd.to_numeric(tmp[delta_col], errors="coerce")
        tmp["exact_movement_value_label"] = tmp["exact_movement_value"].map(lambda x: "FIRST_OBSERVED" if pd.isna(x) else str(int(x)))
        rows = tmp.groupby(["exact_rank_value", "exact_movement_value_label"], dropna=False).agg(
            candidate_rows=("target_core", "size"),
            winner_hits=("is_hit", "sum"),
            days_present=("draw_date", "nunique"),
            hit_days=("draw_date", lambda s: tmp.loc[s.index, "is_hit"].groupby(tmp.loc[s.index, "draw_date"]).max().sum()),
            avg_evidence_score=("evidence_score", "mean"),
            avg_rule_count=("rule_count", "mean"),
            max_lift_seen=("max_lift", "max"),
        ).reset_index()
        total_hits = int(tmp["is_hit"].sum())
        total_days = max(1, int(gm["draw_date"].nunique()))
        rows["axis_test"] = axis_name
        rows["rank_col"] = rank_col
        rows["movement_delta_col"] = delta_col
        rows["winner_share_of_axis_hits"] = rows["winner_hits"] / total_hits if total_hits else 0.0
        rows["hit_rate_per_candidate_row"] = rows["winner_hits"] / rows["candidate_rows"].replace(0, np.nan)
        rows["estimated_plays_per_day_if_playing_exact_rank_movement"] = rows["candidate_rows"] / total_days
        rows["hit_days_rate"] = rows["hit_days"] / total_days
        def sort_key(v):
            if v == "FIRST_OBSERVED":
                return 999999
            try:
                return int(v)
            except Exception:
                return 999998
        rows["exact_movement_sort"] = rows["exact_movement_value_label"].map(sort_key)
        return rows.sort_values(["winner_hits", "hit_rate_per_candidate_row", "exact_rank_value", "exact_movement_sort"], ascending=[False, False, True, True])

    def greedy_exact_rank_movement_set(combo_df: pd.DataFrame, axis_name: str) -> pd.DataFrame:
        if combo_df is None or combo_df.empty:
            return pd.DataFrame()
        x = combo_df.sort_values(["winner_hits", "hit_rate_per_candidate_row", "exact_rank_value"], ascending=[False, False, True]).copy()
        total_hits = max(1, int(x["winner_hits"].sum()))
        test_days = max(1, int(gm["draw_date"].nunique()))
        out_rows, chosen = [], []
        cum_hits = 0
        cum_candidates = 0
        checkpoints = set([1,2,3,4,5,6,8,10,12,15,20,25,30,40,50,60,80,100,120,150,200])
        for i, r in enumerate(x.itertuples(index=False), start=1):
            chosen.append(f"{int(getattr(r, 'exact_rank_value'))}@{getattr(r, 'exact_movement_value_label')}")
            cum_hits += int(getattr(r, "winner_hits"))
            cum_candidates += int(getattr(r, "candidate_rows"))
            if i in checkpoints or i == len(x):
                out_rows.append({
                    "axis_test": axis_name,
                    "exact_rank_movement_pairs_selected_count": i,
                    "exact_rank_movement_pairs_selected": ",".join(chosen),
                    "cumulative_winner_hits": cum_hits,
                    "winner_share_of_axis_hits": cum_hits / total_hits,
                    "candidate_rows_total": cum_candidates,
                    "estimated_plays_per_day_from_exact_rank_movement_pairs": cum_candidates / test_days,
                })
        return pd.DataFrame(out_rows)

    summaries = []
    summaries.append(summarize(["matrix_rank_bucket"], "MATRIX_RANK_ONLY_120_SCALE"))
    summaries.append(summarize(["matrix_movement_bucket"], "MATRIX_MOVEMENT_ONLY_120_SCALE"))
    summaries.append(summarize(["matrix_rank_bucket", "matrix_movement_bucket"], "MATRIX_RANK_X_MOVEMENT_120_SCALE"))
    summaries.append(summarize(["daily_rank_bucket"], "DAILY_PORTFOLIO_RANK_ONLY"))
    summaries.append(summarize(["daily_movement_bucket"], "DAILY_PORTFOLIO_MOVEMENT_ONLY"))
    summaries.append(summarize(["daily_rank_bucket", "daily_movement_bucket"], "DAILY_RANK_X_MOVEMENT"))
    signal = pd.concat(summaries, ignore_index=True)
    signal.to_csv(out_dir / "RANK_MOVEMENT_SIGNAL_SUMMARY.csv", index=False)

    # Heatmap-style separate files for quick spreadsheet pivoting.
    matrix_heat = summarize(["matrix_rank_bucket", "matrix_movement_bucket"], "MATRIX_RANK_X_MOVEMENT_120_SCALE")
    matrix_heat.to_csv(out_dir / "MATRIX_RANK_X_MOVEMENT_HEATMAP.csv", index=False)
    daily_heat = summarize(["daily_rank_bucket", "daily_movement_bucket"], "DAILY_RANK_X_MOVEMENT")
    daily_heat.to_csv(out_dir / "DAILY_RANK_X_MOVEMENT_HEATMAP.csv", index=False)

    # Exact single-row rank value summaries. These are intentionally separate from buckets.
    matrix_exact = exact_rank_summary("matrix_rank_in_stream", "matrix_movement_bucket", "MATRIX_SINGLE_ROW_RANK_VALUE_120_SCALE")
    daily_exact = exact_rank_summary("daily_opportunity_rank", "daily_movement_bucket", "DAILY_SINGLE_ROW_RANK_VALUE_PORTFOLIO_SCALE")
    if not matrix_exact.empty:
        matrix_exact.to_csv(out_dir / "MATRIX_SINGLE_ROW_RANK_VALUE_SUMMARY.csv", index=False)
    if not daily_exact.empty:
        daily_exact.to_csv(out_dir / "DAILY_SINGLE_ROW_RANK_VALUE_SUMMARY.csv", index=False)
    combined_exact = pd.concat([x for x in [matrix_exact, daily_exact] if x is not None and not x.empty], ignore_index=True) if (not matrix_exact.empty or not daily_exact.empty) else pd.DataFrame()
    if not combined_exact.empty:
        combined_exact.to_csv(out_dir / "SINGLE_ROW_RANK_VALUE_SUMMARY.csv", index=False)
    greedy_parts = []
    if not matrix_exact.empty:
        greedy_parts.append(greedy_exact_row_set(matrix_exact, "MATRIX_SINGLE_ROW_RANK_VALUE_120_SCALE"))
    if not daily_exact.empty:
        greedy_parts.append(greedy_exact_row_set(daily_exact, "DAILY_SINGLE_ROW_RANK_VALUE_PORTFOLIO_SCALE"))
    greedy_parts = [g for g in greedy_parts if g is not None and not g.empty]
    if greedy_parts:
        pd.concat(greedy_parts, ignore_index=True).to_csv(out_dir / "SINGLE_ROW_GREEDY_ROW_SET_SUGGESTIONS.csv", index=False)

    # Exact axis-value outputs: rank, movement, and exact rank x exact movement.
    # Bucketed heatmaps are still written, but these preserve the non-ranged signal.
    matrix_move_exact = exact_movement_summary("matrix_rank_delta_same_stream_core", "MATRIX_EXACT_MOVEMENT_VALUE_120_SCALE")
    daily_move_exact = exact_movement_summary("daily_rank_delta_same_stream_core", "DAILY_EXACT_MOVEMENT_VALUE_PORTFOLIO_SCALE")
    matrix_combo_exact = exact_rank_x_exact_movement_summary("matrix_rank_in_stream", "matrix_rank_delta_same_stream_core", "MATRIX_EXACT_RANK_X_EXACT_MOVEMENT_120_SCALE")
    daily_combo_exact = exact_rank_x_exact_movement_summary("daily_opportunity_rank", "daily_rank_delta_same_stream_core", "DAILY_EXACT_RANK_X_EXACT_MOVEMENT_PORTFOLIO_SCALE")
    if not matrix_move_exact.empty:
        matrix_move_exact.to_csv(out_dir / "MATRIX_EXACT_MOVEMENT_VALUE_SUMMARY.csv", index=False)
    if not daily_move_exact.empty:
        daily_move_exact.to_csv(out_dir / "DAILY_EXACT_MOVEMENT_VALUE_SUMMARY.csv", index=False)
    if not matrix_combo_exact.empty:
        matrix_combo_exact.to_csv(out_dir / "MATRIX_EXACT_RANK_X_EXACT_MOVEMENT_SUMMARY.csv", index=False)
    if not daily_combo_exact.empty:
        daily_combo_exact.to_csv(out_dir / "DAILY_EXACT_RANK_X_EXACT_MOVEMENT_SUMMARY.csv", index=False)
    exact_axis_parts = [x for x in [matrix_exact, daily_exact, matrix_move_exact, daily_move_exact, matrix_combo_exact, daily_combo_exact] if x is not None and not x.empty]
    if exact_axis_parts:
        # A combined lookup file; columns differ by axis, so blanks are expected.
        pd.concat(exact_axis_parts, ignore_index=True, sort=False).to_csv(out_dir / "EXACT_AXIS_VALUE_SIGNAL_SUMMARY.csv", index=False)
    combo_greedy_parts = []
    if not matrix_combo_exact.empty:
        combo_greedy_parts.append(greedy_exact_rank_movement_set(matrix_combo_exact, "MATRIX_EXACT_RANK_X_EXACT_MOVEMENT_120_SCALE"))
    if not daily_combo_exact.empty:
        combo_greedy_parts.append(greedy_exact_rank_movement_set(daily_combo_exact, "DAILY_EXACT_RANK_X_EXACT_MOVEMENT_PORTFOLIO_SCALE"))
    combo_greedy_parts = [g for g in combo_greedy_parts if g is not None and not g.empty]
    if combo_greedy_parts:
        pd.concat(combo_greedy_parts, ignore_index=True).to_csv(out_dir / "EXACT_AXIS_GREEDY_RANK_MOVEMENT_SUGGESTIONS.csv", index=False)

    # Winner-only axis map: this is diagnostic, not a selector by itself.
    if winner_rows is not None and not winner_rows.empty:
        wr = gm[gm["is_hit"]].copy()
        wcols = [
            "config", "draw_date", "stream", "prior_draw_date", "prior_result_used_as_seed", "seed", "base4", "seed_core", "actual_core", "target_core",
            "matrix_rank_in_stream", "matrix_rank_bucket", "matrix_rank_delta_same_stream_core",
            "matrix_movement_bucket", "daily_opportunity_rank", "daily_rank_bucket",
            "daily_rank_delta_same_stream_core", "daily_movement_bucket", "evidence_score",
            "rule_count", "max_precision", "max_lift"
        ]
        wcols = [c for c in wcols if c in wr.columns]
        wr[wcols].sort_values(["draw_date", "daily_opportunity_rank", "stream"]).to_csv(
            out_dir / "WINNER_RANK_MOVEMENT_AXIS_LEDGER.csv", index=False
        )
        wr_summary = []
        for cols, name in [
            (["matrix_rank_bucket", "matrix_movement_bucket"], "WINNER_MATRIX_RANK_X_MOVEMENT"),
            (["daily_rank_bucket", "daily_movement_bucket"], "WINNER_DAILY_RANK_X_MOVEMENT"),
            (["matrix_rank_bucket"], "WINNER_MATRIX_RANK_ONLY"),
            (["daily_rank_bucket"], "WINNER_DAILY_RANK_ONLY"),
            (["matrix_rank_in_stream"], "WINNER_MATRIX_SINGLE_ROW_RANK_VALUE"),
            (["daily_opportunity_rank"], "WINNER_DAILY_SINGLE_ROW_RANK_VALUE"),
            (["matrix_rank_delta_same_stream_core"], "WINNER_MATRIX_EXACT_MOVEMENT_VALUE"),
            (["daily_rank_delta_same_stream_core"], "WINNER_DAILY_EXACT_MOVEMENT_VALUE"),
            (["matrix_rank_in_stream", "matrix_rank_delta_same_stream_core"], "WINNER_MATRIX_EXACT_RANK_X_EXACT_MOVEMENT"),
            (["daily_opportunity_rank", "daily_rank_delta_same_stream_core"], "WINNER_DAILY_EXACT_RANK_X_EXACT_MOVEMENT"),
        ]:
            t = wr.groupby(cols, dropna=False).size().reset_index(name="winner_count")
            t["axis_test"] = name
            wr_summary.append(t)
        pd.concat(wr_summary, ignore_index=True).to_csv(out_dir / "WINNER_RANK_MOVEMENT_AXIS_SUMMARY.csv", index=False)


def _pick_best_portfolio_config(portfolio_summary: pd.DataFrame, budget: int = 80) -> str:
    if portfolio_summary is None or portfolio_summary.empty:
        return "ALL_BALANCED"
    x = portfolio_summary[portfolio_summary["daily_budget"] == budget].copy()
    if x.empty:
        x = portfolio_summary.copy()
    x = x.sort_values(["avg_captured_wins_per_day", "rate_days_1plus", "capture_rate"], ascending=[False, False, False])
    return str(x.iloc[0]["config"])


def write_visible_matrix_and_movement_outputs(
    out_dir: Path,
    grouped_by_config: Dict[str, pd.DataFrame],
    candidate_events: pd.DataFrame,
    eval_events: pd.DataFrame,
    portfolio_summary: pd.DataFrame,
    status: StatusWriter,
    write_full_matrix: bool = False,
    visible_top_n: int = 200,
) -> None:
    """Create visible matrix and winner movement audit outputs for the best daily portfolio config.

    This is the part intended to answer: where did the winners sit in the daily matrix,
    and did winner rows move day-to-day by rank/core/stream?
    """
    status.write("building visible matrix + winner movement outputs")
    best_config = _pick_best_portfolio_config(portfolio_summary, budget=80)
    grouped = grouped_by_config.get(best_config, pd.DataFrame()).copy()
    if grouped.empty:
        (out_dir / "VISIBLE_MATRIX_NOTE.txt").write_text(
            f"No grouped evidence rows were available for best_config={best_config}\n", encoding="utf-8"
        )
        return

    # Rank within stream/date and across the whole day.
    grouped = grouped.sort_values(
        ["draw_date", "stream", "evidence_score", "rule_count", "max_lift", "max_precision", "target_core"],
        ascending=[True, True, False, False, False, False, True],
    ).reset_index(drop=True)
    grouped["matrix_rank_in_stream"] = grouped.groupby(["draw_date", "stream"]).cumcount() + 1
    grouped = grouped.sort_values(
        ["draw_date", "evidence_score", "rule_count", "max_lift", "max_precision", "stream", "target_core"],
        ascending=[True, False, False, False, False, True, True],
    ).reset_index(drop=True)
    grouped["daily_opportunity_rank"] = grouped.groupby("draw_date").cumcount() + 1
    grouped["is_actual_aabc_winner_core"] = grouped["target_core"].eq(grouped["actual_core"])
    for b in DAILY_BUDGETS:
        grouped[f"selected_budget_{b}"] = grouped["daily_opportunity_rank"].le(b)

    # Key visible rows: top N per day plus all actual winner rows, even if deep.
    visible = grouped[(grouped["daily_opportunity_rank"] <= visible_top_n) | (grouped["is_actual_aabc_winner_core"])].copy()
    visible_cols = [
        "config", "draw_date", "stream", "prior_draw_date", "prior_result_used_as_seed", "seed", "base4", "seed_core", "actual_core", "target_core",
        "matrix_rank_in_stream", "daily_opportunity_rank", "evidence_score", "rule_count",
        "max_precision", "max_lift", "total_support", "total_target_hits",
        "is_actual_aabc_winner_core"
    ] + [f"selected_budget_{b}" for b in DAILY_BUDGETS]
    visible_cols = [c for c in visible_cols if c in visible.columns]
    visible[visible_cols].to_csv(out_dir / "VISIBLE_DAILY_MATRIX_TOP200_PLUS_WINNERS.csv", index=False)

    # Winner row map: exactly where each AABC winner sat.
    winner_rows = grouped[grouped["is_actual_aabc_winner_core"]].copy()
    if not winner_rows.empty:
        wcols = [
            "config", "draw_date", "stream", "seed", "actual_core", "target_core",
            "matrix_rank_in_stream", "daily_opportunity_rank", "evidence_score", "rule_count",
            "max_precision", "max_lift", "total_support", "total_target_hits",
        ] + [f"selected_budget_{b}" for b in DAILY_BUDGETS]
        wcols = [c for c in wcols if c in winner_rows.columns]
        winner_rows[wcols].sort_values(["draw_date", "daily_opportunity_rank", "stream"]).to_csv(
            out_dir / "WINNER_ROW_MAP_BY_DATE_STREAM.csv", index=False
        )

        # Movement ledger: rank/core movement for winners within the same stream from prior AABC winner.
        wm = winner_rows[wcols].sort_values(["stream", "draw_date"]).copy()
        wm["prev_aabc_draw_date_same_stream"] = wm.groupby("stream")["draw_date"].shift(1)
        wm["prev_actual_core_same_stream"] = wm.groupby("stream")["actual_core"].shift(1)
        wm["prev_matrix_rank_same_stream"] = wm.groupby("stream")["matrix_rank_in_stream"].shift(1)
        wm["prev_daily_opportunity_rank_same_stream"] = wm.groupby("stream")["daily_opportunity_rank"].shift(1)
        wm["matrix_rank_delta_from_prev_aabc_same_stream"] = wm["matrix_rank_in_stream"] - wm["prev_matrix_rank_same_stream"]
        wm["daily_rank_delta_from_prev_aabc_same_stream"] = wm["daily_opportunity_rank"] - wm["prev_daily_opportunity_rank_same_stream"]
        wm["core_same_as_prev_aabc_same_stream"] = wm["actual_core"].eq(wm["prev_actual_core_same_stream"])
        wm["core_digit_overlap_prev_aabc_same_stream"] = [core_digit_overlap(a, b) for a, b in zip(wm["actual_core"], wm["prev_actual_core_same_stream"].fillna(""))]
        wm["draw_date_dt"] = pd.to_datetime(wm["draw_date"], errors="coerce")
        wm["prev_date_dt"] = pd.to_datetime(wm["prev_aabc_draw_date_same_stream"], errors="coerce")
        wm["days_since_prev_aabc_same_stream"] = (wm["draw_date_dt"] - wm["prev_date_dt"]).dt.days
        wm = wm.drop(columns=["draw_date_dt", "prev_date_dt"])
        wm.to_csv(out_dir / "WINNER_MOVEMENT_LEDGER_BY_STREAM.csv", index=False)

        # Daily movement summary: how many winners were in each budget and rank band each day.
        def rb(x):
            try:
                x = int(x)
            except Exception:
                return "NO_EVIDENCE"
            if x == 1: return "R001"
            if x <= 3: return "R002_003"
            if x <= 6: return "R004_006"
            if x <= 12: return "R007_012"
            if x <= 24: return "R013_024"
            if x <= 40: return "R025_040"
            if x <= 80: return "R041_080"
            return "R081_PLUS"
        tmp = winner_rows.copy()
        tmp["winner_daily_rank_bucket"] = tmp["daily_opportunity_rank"].map(rb)
        daily_map = tmp.groupby(["draw_date", "winner_daily_rank_bucket"], as_index=False).size().rename(columns={"size": "winner_count"})
        daily_map.to_csv(out_dir / "WINNER_DAILY_RANK_BUCKET_MAP.csv", index=False)

    # Low-cost added signal: rank-only, movement-only, and rank x movement together.
    write_rank_movement_signal_outputs(out_dir, grouped, winner_rows, status)

    # Optional full all-core matrix for small/medium runs. This is large on full history, so it is opt-in.
    if write_full_matrix:
        status.write("building optional full all-core visible matrix parquet")
        base = candidate_events[["event_id", "draw_date", "stream", "seed", "actual_core"]].copy()
        base["key"] = 1
        cores = pd.DataFrame({"target_core": CORE120, "key": 1})
        full = base.merge(cores, on="key", how="inner").drop(columns=["key"])
        score_cols = [
            "event_id", "target_core", "evidence_score", "rule_count", "max_precision", "max_lift",
            "total_support", "total_target_hits", "matrix_rank_in_stream", "daily_opportunity_rank"
        ] + [f"selected_budget_{b}" for b in DAILY_BUDGETS]
        score_cols = [c for c in score_cols if c in grouped.columns]
        full = full.merge(grouped[score_cols], on=["event_id", "target_core"], how="left")
        for c in ["evidence_score", "rule_count", "max_precision", "max_lift", "total_support", "total_target_hits"]:
            if c in full.columns:
                full[c] = full[c].fillna(0)
        full["has_rule_evidence"] = full["rule_count"].fillna(0).gt(0) if "rule_count" in full.columns else False
        full["is_actual_aabc_winner_core"] = full["target_core"].eq(full["actual_core"])
        if pl is not None:
            pl.from_pandas(full).write_parquet(out_dir / "VISIBLE_DAILY_MATRIX_ALL_120_CORES_BEST_CONFIG.parquet")
        else:
            # CSV fallback; still explicit but may be large.
            full.to_csv(out_dir / "VISIBLE_DAILY_MATRIX_ALL_120_CORES_BEST_CONFIG.csv", index=False)

    # Small phase file explaining what matrix/movement outputs mean.
    (out_dir / "VISIBLE_MATRIX_README.txt").write_text(
        f"best_config_for_matrix={best_config}\n"
        f"VISIBLE_DAILY_MATRIX_TOP200_PLUS_WINNERS.csv = top {visible_top_n} opportunities per day plus every actual AABC winner row.\n"
        "WINNER_ROW_MAP_BY_DATE_STREAM.csv = exact rank/row where each AABC winner landed.\n"
        "WINNER_MOVEMENT_LEDGER_BY_STREAM.csv = day-to-day movement of winner rows within each stream.\n"
        "WINNER_DAILY_RANK_BUCKET_MAP.csv = daily count of winners by portfolio rank bucket.\n"
        "RANK_MOVEMENT_SIGNAL_SUMMARY.csv = rank-only, movement-only, and rank x movement signal tests for both 120-core stream-rank axis and daily portfolio axis.\n"
        "MATRIX_RANK_X_MOVEMENT_HEATMAP.csv / DAILY_RANK_X_MOVEMENT_HEATMAP.csv = bucketed pivot-ready 2-axis summaries.\n"
        "MATRIX_EXACT_MOVEMENT_VALUE_SUMMARY.csv / DAILY_EXACT_MOVEMENT_VALUE_SUMMARY.csv = exact movement delta values, no ranges.\n"
        "MATRIX_EXACT_RANK_X_EXACT_MOVEMENT_SUMMARY.csv / DAILY_EXACT_RANK_X_EXACT_MOVEMENT_SUMMARY.csv = exact row value x exact movement value, no ranges.\n"
        "EXACT_AXIS_VALUE_SIGNAL_SUMMARY.csv = combined exact axis-value lookup.\n"
        "WINNER_RANK_MOVEMENT_AXIS_LEDGER.csv = exact winner rows with both rank and movement axes.\n"
        "Optional full matrix writes all 120 cores per seeded stream/date when --write-full-matrix is enabled.\n",
        encoding="utf-8"
    )


def load_allout_candidate_baseline(allout: Optional[Path], event_base: pd.DataFrame) -> pd.DataFrame:
    # Best-effort only. Different ALL_OUT versions may use different schemas.
    if not allout or not allout.exists():
        return pd.DataFrame()
    try:
        with zipfile.ZipFile(allout, "r") as zf:
            names = zf.namelist()
            cand_names = [n for n in names if n.lower().endswith("candidate_rows_wf.csv") or "candidate_rows" in n.lower() and n.lower().endswith(".csv")]
            if not cand_names:
                return pd.DataFrame()
            name = cand_names[0]
            with zf.open(name) as f:
                cand = pd.read_csv(f, dtype=object, low_memory=False)
    except Exception:
        return pd.DataFrame()
    cols = list(cand.columns)
    date_col = pick_col(cols, ["play_date", "date", "draw_date"])
    stream_col = pick_col(cols, ["stream", "stream_name", "streamname"])
    core_col = pick_col(cols, ["core", "target_core", "candidate_core"])
    score_col = pick_col(cols, ["score", "final_score", "hybrid_score", "HYBRID_SEED_RULE_CAD", "evidence_score"])
    if not all([date_col, stream_col, core_col, score_col]):
        return pd.DataFrame()
    c = pd.DataFrame({
        "draw_date": pd.to_datetime(cand[date_col], errors="coerce").dt.date.astype(str),
        "stream": cand[stream_col].astype(str).str.strip(),
        "target_core": cand[core_col].map(clean_core),
        "baseline_score": pd.to_numeric(cand[score_col], errors="coerce").fillna(0),
    })
    c = c[c["target_core"].isin(CORE120)].copy()
    c = c.sort_values(["draw_date", "stream", "baseline_score", "target_core"], ascending=[True, True, False, True])
    c["baseline_rank"] = c.groupby(["draw_date", "stream"]).cumcount() + 1
    base = event_base[["draw_date", "stream", "actual_core"]].copy()
    w = base.merge(c, left_on=["draw_date", "stream", "actual_core"], right_on=["draw_date", "stream", "target_core"], how="left")
    total = len(base)
    rows = []
    for n in TOP_NS:
        hit = w["baseline_rank"].le(n).fillna(False)
        rows.append({
            "baseline_name": "ALL_OUT_CANDIDATE_ROWS_SCORE_BEST_EFFORT",
            "top_n": n,
            "eligible_aabc_winner_rows": total,
            "captured_winners": int(hit.sum()),
            "capture_rate": float(hit.mean()) if total else 0.0,
        })
    return pd.DataFrame(rows)


def verdict(summary: pd.DataFrame, baseline: pd.DataFrame) -> str:
    # Focus on balanced default if present; otherwise best top12.
    if summary.empty:
        return "STOP_RULE_LIBRARY_PATH\nNo rule evidence summary was produced."
    # select best capture at top12 with avg plays <=12
    s12 = summary[(summary["top_n"] == 12) & (summary["avg_plays_per_stream_date"] <= 12.0001)].copy()
    s5 = summary[(summary["top_n"] == 5) & (summary["avg_plays_per_stream_date"] <= 5.0001)].copy()
    best12 = s12.sort_values("capture_rate", ascending=False).head(1)
    best5 = s5.sort_values("capture_rate", ascending=False).head(1)
    lines = [VERSION, f"created_at={now_s()}"]
    if not best12.empty:
        r = best12.iloc[0]
        lines.append(f"best_top12_config={r['config']}")
        lines.append(f"best_top12_capture={r['captured_winners']}/{r['eligible_aabc_winner_rows']} = {r['capture_rate']:.4%}")
        lines.append(f"best_top12_avg_plays={r['avg_plays_per_stream_date']:.2f}")
    if not best5.empty:
        r = best5.iloc[0]
        lines.append(f"best_top5_config={r['config']}")
        lines.append(f"best_top5_capture={r['captured_winners']}/{r['eligible_aabc_winner_rows']} = {r['capture_rate']:.4%}")
        lines.append(f"best_top5_avg_plays={r['avg_plays_per_stream_date']:.2f}")

    decision = "STOP_RULE_LIBRARY_PATH"
    reason = "Rule replay did not meet minimum go threshold."
    if not best12.empty:
        cap12 = float(best12.iloc[0]["capture_rate"])
        # random top12 of 120 is 10%. Need material lift.
        if cap12 >= 0.20:
            decision = "BUILD_RULE_LIBRARY_DAILY_PLAYER_CANDIDATE"
            reason = "Top12 capture has material lift over random and is worth daily-player prototype."
        elif cap12 >= 0.15:
            decision = "BORDERLINE_RUN_180DAY_OR_FULL_CONFIRMATION"
            reason = "Top12 capture has some lift; confirm on a larger window before building daily player."
    if not baseline.empty and not best12.empty:
        b12 = baseline[baseline["top_n"] == 12]
        if not b12.empty:
            bcap = float(b12.iloc[0]["capture_rate"])
            lines.append(f"baseline_top12_capture={b12.iloc[0]['captured_winners']}/{b12.iloc[0]['eligible_aabc_winner_rows']} = {bcap:.4%}")
            if float(best12.iloc[0]["capture_rate"]) > bcap + 0.02:
                decision = "BUILD_RULE_LIBRARY_DAILY_PLAYER_CANDIDATE"
                reason = "Rule replay beat available ALL_OUT baseline by more than 2 percentage points at Top12."
            elif float(best12.iloc[0]["capture_rate"]) <= bcap:
                decision = "STOP_RULE_LIBRARY_PATH"
                reason = "Rule replay did not beat available ALL_OUT baseline at Top12."
    lines.append(f"FINAL_VERDICT={decision}")
    lines.append(f"reason={reason}")
    lines.append("proof_note=Frozen-rule replay audit only; if rules were mined from the replay period, confirm truly blind before trusting for play.")
    return "\n".join(lines) + "\n"


def write_zip(out_dir: Path, zip_name: str = "RULE_LIBRARY_REPLAY_OUTPUTS.zip") -> None:
    zpath = out_dir / zip_name
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in out_dir.glob("*"):
            if p.is_file() and p.name != zip_name:
                zf.write(p, arcname=p.name)


def create_selftest_files(tmp: Path) -> Tuple[Path, Path]:
    tmp.mkdir(parents=True, exist_ok=True)
    hist = tmp / "selftest_history.csv"
    rules = tmp / "selftest_core_rule_library_stable_only_filtered.csv"
    # Construct simple stream histories with AABC winners and seeds that match rules.
    rows = []
    streams = [("Georgia", "Cash 4 Evening"), ("New York", "Win 4 Midday"), ("Texas", "Daily 4 Day")]
    dates = pd.date_range("2026-06-01", periods=20, freq="D")
    seed_cycle = ["1129", "7942", "3605", "9841", "6364"]
    win_cycle = ["3899", "0669", "3997", "2250", "1030"]
    for st, gm in streams:
        for i, d in enumerate(dates):
            if i == 0:
                res = seed_cycle[i % len(seed_cycle)]
            else:
                res = win_cycle[(i + len(st)) % len(win_cycle)]
            rows.append({"date": d.strftime("%Y-%m-%d"), "state": st, "game": gm, "result": "-".join(res)})
    pd.DataFrame(rows).to_csv(hist, index=False)
    # Rules target cores from seed traits likely present.
    rrows = []
    examples = [
        ("389", "seed_sum_mod5", "3", "seed_structure", "AABC"),
        ("069", "seed_parity_pattern", "OOEE", "seed_sum_mod5", "2"),
        ("379", "seed_first_last_sum", "5", "seed_structure", "AABC"),
        ("025", "seed_highlow_pattern", "HHEL", "", ""),
        ("013", "seed_spread_bucket", "spread_3_4", "", ""),
    ]
    for i, (core, t1, v1, t2, v2) in enumerate(examples, 1):
        rrows.append({
            "combined_rule_id": f"SELF_RULE_{i:03d}",
            "rule_id": f"SELF_{i:03d}",
            "target_core": core,
            "vs_competitor": "000",
            "rule_type": "stacked_2way" if t2 else "single",
            "condition": f"{t1} == '{v1}'" + (f" AND {t2} == '{v2}'" if t2 else ""),
            "trait_1": t1,
            "value_1": v1,
            "trait_2": t2,
            "value_2": v2,
            "support": 12,
            "target_hits": 10,
            "precision": 0.83,
            "lift_vs_competitor": 1.7,
            "tier": "stable",
            "base_pair_rate": 0.5,
            "enabled": 1,
            "build": "SELFTEST",
            "mined_at": now_s(),
            "source_core_rank_range": "SELF",
            "source_file": "selftest",
        })
    pd.DataFrame(rrows).to_csv(rules, index=False)
    return hist, rules


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-dir", default="IN")
    ap.add_argument("--out-dir", default="OUT/RULE_REPLAY_RUN")
    ap.add_argument("--history", default=None)
    ap.add_argument("--rules", default=None)
    ap.add_argument("--allout", default=None)
    ap.add_argument("--last-days", type=int, default=None)
    ap.add_argument("--start-date", default=None)
    ap.add_argument("--end-date", default=None)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--write-full-matrix", action="store_true", help="Write all 120 cores per seeded stream/date for the best config. Large on full history.")
    ap.add_argument("--visible-top-n", type=int, default=200, help="Top daily opportunities to include in visible matrix CSV, plus all actual winner rows.")
    args = ap.parse_args()

    t0 = time.time()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    status = StatusWriter(out_dir, t0)

    try:
        if args.selftest:
            status.write("creating synthetic selftest inputs")
            hist_path, rules_path = create_selftest_files(out_dir / "_SELFTEST_INPUTS")
            allout_path = None
        else:
            in_dir = Path(args.in_dir)
            # STRICT INPUT PICKING: do not fall back to any random CSV for history.
            # v1.5 could accidentally load core_rule_library_*.csv as history when no history file was present.
            hist_path = Path(args.history) if args.history else find_first_file(
                in_dir,
                ["history.csv", "history.txt", "*history*.csv", "*history*.txt", "*draw*.csv", "*draw*.txt", "*result*.csv", "*result*.txt"]
            )
            rules_path = Path(args.rules) if args.rules else find_first_file(in_dir, ["core_rule_library_stable_only_filtered*.csv", "*core_rule_library*.csv", "*rule_library*.csv", "*rule*.csv"])
            allout_path = Path(args.allout) if args.allout else find_first_file(in_dir, ["ALL_OUT*.zip", "*allout*.zip"])
            if hist_path is None:
                raise FileNotFoundError(
                    "No history CSV/TXT found in IN folder. Put your draw history file in IN and name it history.csv/history.txt, "
                    "or run with --history path/to/your_history.csv. The app will not guess from unrelated CSVs."
                )
            if rules_path is None:
                raise FileNotFoundError("No core rule library CSV found in IN folder. Expected core_rule_library_stable_only_filtered.csv.")
            if hist_path.name.lower().startswith("core_rule") or "rule_library" in hist_path.name.lower():
                raise ValueError(
                    f"History input was resolved to {hist_path.name}, which looks like the rule library, not draw history. "
                    "Rename the real draw history to history.csv or pass --history explicitly."
                )
            if rules_path.resolve() == hist_path.resolve():
                raise ValueError("History file and rule file resolved to the same path. Put both IN/history.csv and IN/core_rule_library_stable_only_filtered.csv in the IN folder.")

        status.write(f"loading history: {hist_path.name}")
        raw_hist = read_history_file(hist_path)
        hist, schema_audit = adapt_history(raw_hist)
        schema_audit.to_csv(out_dir / "SCHEMA_ADAPTER_AUDIT.csv", index=False)
        seed_alignment_audit, seed_alignment_summary = build_seed_alignment_audit(hist)
        seed_alignment_audit.to_csv(out_dir / "SEED_ALIGNMENT_AUDIT.csv", index=False)
        seed_alignment_summary.to_csv(out_dir / "SEED_ALIGNMENT_SUMMARY.csv", index=False)
        (out_dir / "SEED_ALIGNMENT_CERTIFICATION.txt").write_text(
            seed_alignment_summary.to_string(index=False) + "\n", encoding="utf-8"
        )
        assert_seed_alignment_ok(seed_alignment_summary)

        status.write(f"loading rules: {rules_path.name}")
        raw_rules = pd.read_csv(rules_path, dtype=object, low_memory=False)
        rules = normalize_rules(raw_rules)
        rules.to_csv(out_dir / "NORMALIZED_RULES_USED.csv", index=False)

        status.write("building replay event table")
        # IMPORTANT FOR DAILY PORTFOLIO TEST:
        # Rank opportunities from ALL seeded stream/date events, not only streams that later had AABC winners.
        # Capture is evaluated only against AABC winners, but non-AABC streams can still consume plays.
        candidate_events_all = hist[hist["seed"].notna()].copy()
        candidate_events = apply_last_days(candidate_events_all, args.last_days, args.start_date, args.end_date)
        candidate_events = attach_seed_traits(candidate_events)
        candidate_events.to_csv(out_dir / "REPLAY_EVENTS_ALL_SEEDED.csv", index=False)

        eval_events = candidate_events[candidate_events["is_aabc_winner"]].copy()
        eval_events.to_csv(out_dir / "REPLAY_EVENTS_AABC_ONLY.csv", index=False)

        if candidate_events.empty:
            raise ValueError("No seeded stream/date rows were found for the requested window.")
        if eval_events.empty:
            raise ValueError("No AABC winner rows with prior seed were found for the requested window.")

        status.write("matching stable rules to all seeded replay events")
        matches = match_rules(candidate_events, rules, status)
        if matches.empty:
            status.write("no rules matched replay events")
        else:
            # Keep ledger sample and full compact matched ledger if not too huge.
            ledger_cols = [
                "event_id", "draw_date", "stream", "prior_draw_date", "prior_result_used_as_seed", "seed", "base4", "seed_core", "actual_core", "target_core", "combined_rule_id",
                "rule_type", "trait_1", "value_1", "trait_2", "value_2", "support", "target_hits",
                "precision", "lift_vs_competitor", "rule_strength_balanced", "is_winner_rule_target"
            ]
            available = [c for c in ledger_cols if c in matches.columns]
            matches[available].head(100000).to_csv(out_dir / "RULE_HIT_LEDGER_SAMPLE_100000.csv", index=False)

        configs = [
            ("ALL_BALANCED", lambda m: pd.Series(True, index=m.index), "rule_strength_balanced"),
            ("ALL_RULE_COUNT", lambda m: pd.Series(True, index=m.index), "rule_strength_hits"),
            ("PRECISION_090_BALANCED", lambda m: m["precision"] >= 0.90, "rule_strength_balanced"),
            ("PRECISION_100_BALANCED", lambda m: m["precision"] >= 0.999999, "rule_strength_balanced"),
            ("LIFT_125_BALANCED", lambda m: m["lift_vs_competitor"] >= 1.25, "rule_strength_balanced"),
            ("LIFT_150_BALANCED", lambda m: m["lift_vs_competitor"] >= 1.50, "rule_strength_balanced"),
            ("SUPPORT_15_BALANCED", lambda m: m["support"] >= 15, "rule_strength_balanced"),
            ("SUPPORT_20_BALANCED", lambda m: m["support"] >= 20, "rule_strength_balanced"),
            ("HIGH_CONF_090_L125", lambda m: (m["precision"] >= 0.90) & (m["lift_vs_competitor"] >= 1.25), "rule_strength_balanced"),
        ]

        all_summaries = []
        all_decisions = []
        selected_top12_parts = []
        portfolio_summaries = []
        portfolio_daily_parts = []
        portfolio_selected_parts = []
        config_diag = []
        grouped_by_config = {}
        for cname, filt, scol in configs:
            status.write(f"aggregating/ranking config {cname}")
            grouped, decisions = aggregate_scores(matches, cname, filt, scol) if not matches.empty else (pd.DataFrame(), pd.DataFrame())
            summary = summarize_capture(decisions, eval_events, cname)
            all_summaries.append(summary)
            if not decisions.empty:
                all_decisions.append(decisions)
            if not grouped.empty:
                grouped_by_config[cname] = grouped.copy()
                # Selected rows for top12 only, enough for per-stream audit without huge outputs.
                sel = grouped[grouped["rule_replay_rank"] <= 12].copy()
                selected_top12_parts.append(sel)
                # Daily portfolio audit: rank all stream/core rows across each day at fixed daily budgets.
                p_sum, p_daily, p_sel = summarize_daily_portfolio(grouped, eval_events, cname)
                portfolio_summaries.append(p_sum)
                portfolio_daily_parts.append(p_daily)
                if not p_sel.empty:
                    portfolio_selected_parts.append(p_sel)
                config_diag.append({
                    "config": cname,
                    "matched_rule_rows": int(filt(matches).sum()) if not matches.empty else 0,
                    "selected_rows_top12": int(len(sel)),
                    "portfolio_selected_rows_saved": int(len(p_sel)),
                    "unique_events_with_evidence": int(grouped["event_id"].nunique()),
                    "unique_cores_with_evidence": int(grouped["target_core"].nunique()),
                })
            else:
                config_diag.append({
                    "config": cname,
                    "matched_rule_rows": 0,
                    "selected_rows_top12": 0,
                    "unique_events_with_evidence": 0,
                    "unique_cores_with_evidence": 0,
                })

        cap_summary = pd.concat(all_summaries, ignore_index=True) if all_summaries else pd.DataFrame()
        cap_summary.to_csv(out_dir / "RULE_REPLAY_CAPTURE_SUMMARY.csv", index=False)
        pd.DataFrame(config_diag).to_csv(out_dir / "RULE_CONFIG_DIAGNOSTIC.csv", index=False)
        if all_decisions:
            pd.concat(all_decisions, ignore_index=True).to_csv(out_dir / "RULE_REPLAY_STREAM_DATE_DECISIONS.csv", index=False)
        if selected_top12_parts:
            pd.concat(selected_top12_parts, ignore_index=True).to_csv(out_dir / "RULE_REPLAY_SELECTED_ROWS_BEST_TOP12.csv", index=False)
        if portfolio_summaries:
            pd.concat(portfolio_summaries, ignore_index=True).to_csv(out_dir / "DAILY_PORTFOLIO_CAPTURE_SUMMARY.csv", index=False)
        if portfolio_daily_parts:
            pd.concat(portfolio_daily_parts, ignore_index=True).to_csv(out_dir / "DAILY_PORTFOLIO_BY_DATE.csv", index=False)
        if portfolio_selected_parts:
            pd.concat(portfolio_selected_parts, ignore_index=True).to_csv(out_dir / "DAILY_PORTFOLIO_SELECTED_ROWS_KEY_BUDGETS.csv", index=False)

        portfolio_summary_df_for_matrix = pd.concat(portfolio_summaries, ignore_index=True) if portfolio_summaries else pd.DataFrame()
        write_visible_matrix_and_movement_outputs(
            out_dir=out_dir,
            grouped_by_config=grouped_by_config,
            candidate_events=candidate_events,
            eval_events=eval_events,
            portfolio_summary=portfolio_summary_df_for_matrix,
            status=status,
            write_full_matrix=bool(args.write_full_matrix),
            visible_top_n=int(args.visible_top_n),
        )

        status.write("building trait value audit")
        if not matches.empty:
            trait_rows = []
            for field in ["trait_1", "trait_2"]:
                if field in matches.columns:
                    tmp = matches[matches[field].fillna("").astype(str).str.len() > 0].copy()
                    if not tmp.empty:
                        valfield = "value_1" if field == "trait_1" else "value_2"
                        g = tmp.groupby([field, valfield], as_index=False).agg(
                            matched_rule_rows=("combined_rule_id", "count"),
                            winner_target_rule_rows=("is_winner_rule_target", "sum"),
                            avg_precision=("precision", "mean"),
                            avg_lift=("lift_vs_competitor", "mean"),
                            unique_events=("event_id", "nunique"),
                            unique_target_cores=("target_core", "nunique"),
                        ).rename(columns={field: "trait", valfield: "value"})
                        trait_rows.append(g)
            if trait_rows:
                trait_val = pd.concat(trait_rows, ignore_index=True)
                trait_val["winner_rule_row_rate"] = trait_val["winner_target_rule_rows"] / trait_val["matched_rule_rows"].replace(0, np.nan)
                trait_val = trait_val.sort_values(["winner_rule_row_rate", "matched_rule_rows"], ascending=[False, False])
                trait_val.to_csv(out_dir / "RULE_VALUE_BY_TRAIT.csv", index=False)

        status.write("attempting optional ALL_OUT baseline comparison")
        baseline = load_allout_candidate_baseline(allout_path, eval_events) if allout_path else pd.DataFrame()
        if not baseline.empty:
            baseline.to_csv(out_dir / "OPTIONAL_ALL_OUT_BASELINE_CAPTURE_SUMMARY.csv", index=False)

        status.write("writing final verdict and run report")
        verdict_txt = verdict(cap_summary, baseline)
        (out_dir / "FINAL_RULE_REPLAY_VERDICT.txt").write_text(verdict_txt, encoding="utf-8")
        portfolio_summary_df = pd.concat(portfolio_summaries, ignore_index=True) if portfolio_summaries else pd.DataFrame()
        portfolio_verdict_txt = portfolio_verdict(portfolio_summary_df)
        (out_dir / "FINAL_DAILY_PORTFOLIO_VERDICT.txt").write_text(portfolio_verdict_txt, encoding="utf-8")

        run_report = pd.DataFrame([{
            "version": VERSION,
            "history_file": str(hist_path),
            "rules_file": str(rules_path),
            "allout_file": str(allout_path) if allout_path else "",
            "history_rows_loaded": len(hist),
            "history_date_min": hist["draw_date"].min(),
            "history_date_max": hist["draw_date"].max(),
            "unique_streams": hist["stream"].nunique(),
            "rules_loaded_enabled_normalized": len(rules),
            "replay_events_all_seeded": len(candidate_events),
            "replay_events_aabc_with_seed": len(eval_events),
            "seed_alignment_certification": str(seed_alignment_summary.iloc[0].get("certification", "")) if "seed_alignment_summary" in locals() and not seed_alignment_summary.empty else "MISSING",
            "bad_alignment_rows": int(seed_alignment_summary.iloc[0].get("bad_alignment_rows", -1)) if "seed_alignment_summary" in locals() and not seed_alignment_summary.empty else -1,
            "seed_equals_current_result_rows_warning_only": int(seed_alignment_summary.iloc[0].get("seed_equals_current_result_rows_warning_only", -1)) if "seed_alignment_summary" in locals() and not seed_alignment_summary.empty else -1,
            "matches_rows": len(matches) if not matches.empty else 0,
            "last_days": args.last_days or "FULL",
            "start_date": args.start_date or "",
            "end_date": args.end_date or "",
            "elapsed_seconds": round(time.time() - t0, 2),
            "write_full_matrix": bool(args.write_full_matrix),
            "visible_top_n": int(args.visible_top_n),
            "polars_available": bool(pl is not None),
            "proof_level": "FROZEN_RULE_REPLAY_NOT_STRICT_BLIND_IF_RULES_MINED_ON_REPLAY_PERIOD",
        }])
        run_report.to_csv(out_dir / "00_RUN_REPORT.csv", index=False)

        write_zip(out_dir)
        status.write("complete")
        return 0
    except Exception as e:
        status.write(f"ERROR: {type(e).__name__}: {e}")
        (out_dir / "ERROR.txt").write_text(f"{type(e).__name__}: {e}\n", encoding="utf-8")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
