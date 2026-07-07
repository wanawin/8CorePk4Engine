# C120_Trap_v2_4_BROWSER_UI
# Winner-location trap layer for C120 daily matrix outputs.
# BUILD: C120_Trap_v2_4_BROWSER_UI_2026-07-04

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import traceback
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

BUILD_MARKER = "C120_Trap_v2_4_BROWSER_UI_2026-07-04"
DEFAULT_WATCHED8 = ["027", "067", "138", "145", "389", "457", "567", "679"]

REQUIRED_MATRIX_COLS = ["stream", "target_core"]


def safe_mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def write_error(out_dir: Path, msg: str, exc: Optional[BaseException] = None) -> None:
    safe_mkdir(out_dir)
    with open(out_dir / "ERROR.txt", "w", encoding="utf-8") as f:
        f.write(msg + "\n")
        if exc is not None:
            f.write("\n")
            f.write("".join(traceback.format_exception(exc)))


def first_existing(in_dir: Path, candidates: List[str]) -> Optional[Path]:
    for name in candidates:
        p = in_dir / name
        if p.exists():
            return p
    return None


def find_file(in_dir: Path, contains: Iterable[str], suffixes=(".csv", ".txt")) -> Optional[Path]:
    words = [w.lower() for w in contains]
    for p in sorted(in_dir.iterdir() if in_dir.exists() else []):
        if p.is_file() and p.suffix.lower() in suffixes:
            low = p.name.lower()
            if all(w in low for w in words):
                return p
    return None


def read_csv_any(path: Path) -> pd.DataFrame:
    # All text initially. Preserve leading zeros and avoid dtype warnings.
    if path.suffix.lower() == ".txt":
        try:
            return pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False)
        except Exception:
            return pd.read_csv(path, dtype=str, keep_default_na=False)
    return pd.read_csv(path, dtype=str, keep_default_na=False, low_memory=False)


def to_num(s, default=0.0):
    try:
        if pd.isna(s) or str(s).strip() == "":
            return default
        return float(s)
    except Exception:
        return default


def norm_digits(s: object, n: Optional[int] = None) -> str:
    digs = re.findall(r"\d", "" if s is None else str(s))
    if n is not None:
        digs = digs[:n]
    out = "".join(digs)
    if n is not None:
        out = out.zfill(n)[-n:]
    return out


def norm_core(s: object) -> str:
    digs = re.findall(r"\d", "" if s is None else str(s))
    if not digs:
        return ""
    # If a full 4 digit AABC/member slips in, collapse to unique sorted 3 digits.
    if len(digs) >= 4:
        u = sorted(set(digs[:4]))
        if len(u) == 3:
            return "".join(u)
    return "".join(digs[:3]).zfill(3)[-3:]


def boxed4(s: object) -> str:
    d = norm_digits(s, 4)
    if len(d) != 4:
        return ""
    return "".join(sorted(d))


def aabc_core_from_result(s: object) -> str:
    b = boxed4(s)
    if len(b) != 4:
        return ""
    u = sorted(set(b))
    if len(u) == 3:
        counts = {x: b.count(x) for x in u}
        if sorted(counts.values()) == [1, 1, 2]:
            return "".join(u)
    return ""


def is_aabc(s: object) -> bool:
    return bool(aabc_core_from_result(s))


def generate_aabc_members(core: str) -> List[str]:
    c = norm_core(core)
    if len(c) != 3 or len(set(c)) != 3:
        return []
    out = []
    for rep in c:
        out.append("".join(sorted(c + rep)))
    return sorted(set(out))


def digital_root_sum(n: int) -> int:
    if n == 0:
        return 0
    return 1 + ((n - 1) % 9)


def mirror_digit(d: str) -> Optional[str]:
    if not d.isdigit():
        return None
    return str((int(d) + 5) % 10)


def calc_member_soft_score(seed: str, member: str) -> Tuple[float, str]:
    seed = norm_digits(seed, 4)
    member = boxed4(member)
    if len(seed) != 4 or len(member) != 4:
        return 0.0, "no seed/member"
    sd = [int(x) for x in seed]
    md = [int(x) for x in member]
    seed_sum = sum(sd)
    member_sum = sum(md)
    delta = member_sum - seed_sum
    abs_delta = abs(delta)
    score = 0.0
    reasons = []
    if digital_root_sum(seed_sum) == digital_root_sum(member_sum):
        score += 0.40; reasons.append("same digital root")
    if delta in (9, -9):
        score += 0.70; reasons.append("±9 sum support")
    if delta in (10, -10):
        score += 0.55; reasons.append("±10 sum support")
    if delta in (18, -18):
        score += 0.40; reasons.append("±18 sum support")
    if abs_delta <= 1:
        score += 0.25; reasons.append("sum delta <=1")
    elif abs_delta <= 2:
        score += 0.18; reasons.append("sum delta <=2")
    elif abs_delta <= 3:
        score += 0.10; reasons.append("sum delta <=3")
    sld = str(seed_sum % 10)
    if sld in member:
        score += 0.35; reasons.append("seed-sum last digit in member")
    m = mirror_digit(sld)
    if m and m in member:
        score += 0.20; reasons.append("mirror seed-sum last digit in member")
    overlap = len(set(seed) & set(member))
    score += min(overlap, 4) * 0.07
    if overlap:
        reasons.append(f"digit overlap {overlap}")
    return score, "; ".join(reasons) if reasons else "weak soft fit"


def parse_date_series(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce")


def normalize_history(hist: pd.DataFrame) -> pd.DataFrame:
    h = hist.copy()
    # Flexible column support.
    if "Date" not in h.columns:
        for c in ["draw_date", "date", "DrawDate"]:
            if c in h.columns:
                h["Date"] = h[c]
                break
    if "Result4" not in h.columns:
        for c in ["Result", "result", "base4", "winner", "Winner"]:
            if c in h.columns:
                h["Result4"] = h[c].map(lambda x: norm_digits(x, 4))
                break
    if "StreamKey" not in h.columns:
        if "stream" in h.columns:
            h["StreamKey"] = h["stream"]
        elif "State" in h.columns and "Game" in h.columns:
            h["StreamKey"] = h["State"].astype(str).str.strip() + " | " + h["Game"].astype(str).str.strip()
    if "Date" not in h.columns or "Result4" not in h.columns or "StreamKey" not in h.columns:
        raise ValueError("History must contain Date/Result4/StreamKey or equivalent State+Game+Result columns.")
    h["Date"] = parse_date_series(h["Date"]).dt.strftime("%Y-%m-%d")
    h["Result4"] = h["Result4"].map(lambda x: norm_digits(x, 4))
    h["StreamKey"] = h["StreamKey"].astype(str).str.strip()
    h = h[(h["Date"].notna()) & (h["Result4"].str.len() == 4) & (h["StreamKey"] != "")].copy()
    h["winner_member"] = h["Result4"].map(boxed4)
    h["winner_core"] = h["Result4"].map(aabc_core_from_result)
    h["is_aabc"] = h["winner_core"] != ""
    h = h.sort_values(["StreamKey", "Date"]).reset_index(drop=True)
    h["prev_result4"] = h.groupby("StreamKey")["Result4"].shift(1).fillna("")
    h["prev_core"] = h["prev_result4"].map(aabc_core_from_result)
    return h


def parse_winner_text(path: Path) -> pd.DataFrame:
    rows = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        date_raw, state, game, result_raw = parts[0], parts[1], parts[2], parts[3]
        result4 = norm_digits(result_raw, 4)
        dt = pd.to_datetime(date_raw, errors="coerce")
        if pd.isna(dt) or len(result4) != 4:
            continue
        rows.append({
            "Date": dt.strftime("%Y-%m-%d"),
            "State": state.strip(),
            "Game": game.strip(),
            "stream": f"{state.strip()} | {game.strip()}",
            "Result4": result4,
            "winner_member": boxed4(result4),
            "winner_core": aabc_core_from_result(result4),
            "is_aabc": aabc_core_from_result(result4) != "",
        })
    return pd.DataFrame(rows)


def load_watched_cores(cfg_dir: Path) -> List[str]:
    p = cfg_dir / "watched_cores.txt"
    if not p.exists():
        return DEFAULT_WATCHED8[:]
    vals = []
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        c = norm_core(line)
        if len(c) == 3:
            vals.append(c)
    return sorted(set(vals)) or DEFAULT_WATCHED8[:]


def prepare_matrix(matrix: pd.DataFrame) -> pd.DataFrame:
    m = matrix.copy()
    missing = [c for c in REQUIRED_MATRIX_COLS if c not in m.columns]
    if missing:
        raise ValueError(f"Full matrix missing required columns: {missing}")
    if "PLAY_DATE" not in m.columns:
        if "draw_date" in m.columns:
            m["PLAY_DATE"] = m["draw_date"]
        else:
            m["PLAY_DATE"] = ""
    if "HISTORY_THROUGH" not in m.columns:
        m["HISTORY_THROUGH"] = ""
    m["stream"] = m["stream"].astype(str).str.strip()
    m["target_core"] = m["target_core"].map(norm_core)
    score_col = "final_stream_core_score" if "final_stream_core_score" in m.columns else "evidence_score"
    if score_col not in m.columns:
        m[score_col] = "0"
    m["core_score"] = m[score_col].map(to_num)
    if "matrix_rank_in_stream" in m.columns:
        m["full_core_rank"] = m["matrix_rank_in_stream"].map(lambda x: int(to_num(x, 999999)))
    else:
        m["full_core_rank"] = m.groupby(["PLAY_DATE", "stream"])["core_score"].rank(method="first", ascending=False).astype(int)
    if "daily_opportunity_rank" in m.columns:
        m["daily_opportunity_rank_num"] = m["daily_opportunity_rank"].map(lambda x: int(to_num(x, 999999)))
    else:
        m["daily_opportunity_rank_num"] = m["core_score"].rank(method="first", ascending=False).astype(int)
    # Normalize dates to string.
    for c in ["PLAY_DATE", "HISTORY_THROUGH", "draw_date", "prior_draw_date"]:
        if c in m.columns:
            dt = pd.to_datetime(m[c], errors="coerce")
            m[c] = dt.dt.strftime("%Y-%m-%d").fillna(m[c].astype(str))
    # Seed column.
    if "seed" not in m.columns:
        if "prior_result_used_as_seed" in m.columns:
            m["seed"] = m["prior_result_used_as_seed"]
        else:
            m["seed"] = ""
    m["seed"] = m["seed"].map(lambda x: norm_digits(x, 4))
    return m


def build_training_counts(hist: pd.DataFrame, through_date: str) -> Tuple[pd.DataFrame, Dict[Tuple[str, str], int], Dict[Tuple[str, str, str], int], Dict[Tuple[str, str], int]]:
    h = hist.copy()
    if through_date:
        h = h[pd.to_datetime(h["Date"], errors="coerce") <= pd.to_datetime(through_date, errors="coerce")].copy()
    # Only target core wins are AABC.
    a = h[h["is_aabc"]].copy()
    global_trans = a[(a["prev_core"] != "") & (a["winner_core"] != "")].groupby(["prev_core", "winner_core"]).size().to_dict()
    stream_trans = a[(a["prev_core"] != "") & (a["winner_core"] != "")].groupby(["StreamKey", "prev_core", "winner_core"]).size().to_dict()
    stream_core = a.groupby(["StreamKey", "winner_core"]).size().to_dict()
    return h, global_trans, stream_trans, stream_core


def add_trap_lanes_for_playdate(m_day: pd.DataFrame, hist: pd.DataFrame, watched8: List[str]) -> pd.DataFrame:
    d = m_day.copy()
    play_date = str(d["PLAY_DATE"].iloc[0]) if "PLAY_DATE" in d.columns and len(d) else ""
    through = str(d["HISTORY_THROUGH"].iloc[0]) if "HISTORY_THROUGH" in d.columns and len(d) else ""
    train, global_trans, stream_trans, stream_core = build_training_counts(hist, through)

    watched_set = set(watched8)
    d["is_watched8_core"] = d["target_core"].isin(watched_set)
    # watched8 rank inside each stream by full rank then score.
    w = d[d["is_watched8_core"]].copy()
    if not w.empty:
        w = w.sort_values(["stream", "full_core_rank", "core_score"], ascending=[True, True, False])
        w["watched8_rank"] = w.groupby("stream").cumcount() + 1
        d = d.merge(w[["stream", "target_core", "watched8_rank"]], on=["stream", "target_core"], how="left")
    else:
        d["watched8_rank"] = pd.NA
    d["watched8_rank"] = d["watched8_rank"].fillna(999999).astype(int)

    # Prior core from matrix seed, or seed_core if present.
    if "seed_core" in d.columns:
        d["prior_seed_core"] = d["seed_core"].map(norm_core)
    else:
        d["prior_seed_core"] = ""
    # If seed_core blank because seed is ABCD, use last1_aabc_core as transition anchor if present.
    if "last1_aabc_core" in d.columns:
        d["last1_norm"] = d["last1_aabc_core"].map(norm_core)
        d["prior_seed_core_for_transition"] = d["prior_seed_core"]
    else:
        d["prior_seed_core_for_transition"] = d["prior_seed_core"]

    def gtc(row):
        return int(global_trans.get((row["prior_seed_core_for_transition"], row["target_core"]), 0))
    def stc(row):
        return int(stream_trans.get((row["stream"], row["prior_seed_core_for_transition"], row["target_core"]), 0))
    def scc(row):
        return int(stream_core.get((row["stream"], row["target_core"]), 0))

    d["global_prevcore_to_core_count"] = d.apply(gtc, axis=1)
    d["stream_prevcore_to_core_count"] = d.apply(stc, axis=1)
    d["stream_core_hit_count"] = d.apply(scc, axis=1)

    d["lane_top4_normal"] = d["is_watched8_core"] & (d["watched8_rank"] <= 4)
    d["lane_watched8_transition"] = d["is_watched8_core"] & (d["global_prevcore_to_core_count"] >= 3) & (d["full_core_rank"] <= 25) & (d["watched8_rank"] <= 4)
    d["lane_watched8_buried"] = d["is_watched8_core"] & (d["full_core_rank"].between(90, 100)) & (d["watched8_rank"] == 6) & (d["core_score"] > 0)
    d["lane_stream_affinity"] = d["is_watched8_core"] & (d["stream_core_hit_count"] >= 4)
    d["trap_keep"] = d[["lane_top4_normal", "lane_watched8_transition", "lane_watched8_buried"]].any(axis=1)

    def lane_name(row):
        # Priority label; audit columns preserve all lanes.
        if row["lane_watched8_buried"]:
            return "WATCHED8_BURIED"
        if row["lane_watched8_transition"]:
            return "WATCHED8_TRANSITION"
        if row["lane_stream_affinity"]:
            return "STREAM_AFFINITY"
        if row["lane_top4_normal"]:
            return "TOP4_NORMAL"
        return "NO_TRAP"
    d["trap_lane"] = d.apply(lane_name, axis=1)

    # Lower sort is better.
    lane_weight = {
        "WATCHED8_BURIED": 1,
        "WATCHED8_TRANSITION": 2,
        "TOP4_NORMAL": 3,
        "STREAM_AFFINITY": 4,
        "NO_TRAP": 99,
    }
    d["trap_lane_weight"] = d["trap_lane"].map(lane_weight).fillna(99).astype(int)
    lane_base = d["trap_lane"].map({
        "WATCHED8_BURIED": 1000.0,
        "WATCHED8_TRANSITION": 900.0,
        "TOP4_NORMAL": 600.0,
        "STREAM_AFFINITY": 500.0,
        "NO_TRAP": 0.0,
    }).fillna(0.0)
    d["trap_priority_score"] = (
        lane_base
        + d["core_score"].clip(lower=0, upper=100) * 0.35
        + d["global_prevcore_to_core_count"].clip(upper=20) * 2.0
        + d["stream_core_hit_count"].clip(upper=20) * 0.50
        - d["full_core_rank"].clip(upper=120) * 0.05
    )
    return d


def add_trap_lanes(matrix: pd.DataFrame, hist: pd.DataFrame, watched8: List[str]) -> pd.DataFrame:
    out = []
    for play_date, g in matrix.groupby("PLAY_DATE", dropna=False):
        out.append(add_trap_lanes_for_playdate(g, hist, watched8))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def expand_members_for_core_locations(core_locs: pd.DataFrame, existing_member_matrix: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    rows = []
    existing_lookup = pd.DataFrame()
    if existing_member_matrix is not None and not existing_member_matrix.empty:
        em = existing_member_matrix.copy()
        if "target_core" in em.columns:
            em["target_core"] = em["target_core"].map(norm_core)
        if "candidate_member" in em.columns:
            em["candidate_member"] = em["candidate_member"].map(boxed4)
        for c in ["final_member_score", "member_rank_within_core", "daily_member_matrix_rank", "played_flag", "final_play_rank"]:
            if c not in em.columns:
                em[c] = ""
        existing_lookup = em

    for _, r in core_locs.iterrows():
        core = r["target_core"]
        members = generate_aabc_members(core)
        # If current member matrix has rows for this stream/core, reuse those members and scores.
        reuse = pd.DataFrame()
        if not existing_lookup.empty:
            mask = (existing_lookup["stream"] == r["stream"]) & (existing_lookup["target_core"] == core)
            if "PLAY_DATE" in existing_lookup.columns and "PLAY_DATE" in r.index:
                mask = mask & (existing_lookup["PLAY_DATE"] == r["PLAY_DATE"])
            reuse = existing_lookup[mask].copy()
        used = set()
        if not reuse.empty:
            for _, mr in reuse.iterrows():
                mbr = boxed4(mr.get("candidate_member", ""))
                if not mbr:
                    continue
                used.add(mbr)
                soft, why = calc_member_soft_score(r.get("seed", ""), mbr)
                row = r.to_dict()
                row.update({
                    "candidate_member": mbr,
                    "member_source": "existing_member_matrix",
                    "member_soft_score": soft,
                    "member_soft_reason": why,
                    "existing_final_member_score": to_num(mr.get("final_member_score", 0), 0),
                    "existing_member_rank_within_core": mr.get("member_rank_within_core", ""),
                    "existing_daily_member_matrix_rank": mr.get("daily_member_matrix_rank", ""),
                    "existing_played_flag": mr.get("played_flag", ""),
                    "existing_final_play_rank": mr.get("final_play_rank", ""),
                })
                rows.append(row)
        for mbr in members:
            if mbr in used:
                continue
            soft, why = calc_member_soft_score(r.get("seed", ""), mbr)
            row = r.to_dict()
            row.update({
                "candidate_member": mbr,
                "member_source": "generated_from_trap_core",
                "member_soft_score": soft,
                "member_soft_reason": why,
                "existing_final_member_score": 0.0,
                "existing_member_rank_within_core": "",
                "existing_daily_member_matrix_rank": "",
                "existing_played_flag": "False",
                "existing_final_play_rank": "",
            })
            rows.append(row)
    mem = pd.DataFrame(rows)
    if mem.empty:
        return mem
    mem["existing_final_member_score_num"] = mem["existing_final_member_score"].map(to_num)
    # Lane member depth. Buried and transition are allowed multiple members because these are rescue lanes.
    depth_by_lane = {
        "WATCHED8_BURIED": 3,
        "WATCHED8_TRANSITION": 3,
        "STREAM_AFFINITY": 1,
        "TOP4_NORMAL": 1,
        "NO_TRAP": 0,
    }
    mem["lane_member_depth"] = mem["trap_lane"].map(depth_by_lane).fillna(1).astype(int)
    mem["member_trap_score"] = (
        mem["trap_priority_score"].map(to_num)
        + mem["member_soft_score"].map(to_num) * 12
        + mem["existing_final_member_score_num"].clip(upper=100) * 0.15
    )
    # Rank within stream/core/lane and keep allowed depth.
    mem = mem.sort_values(["PLAY_DATE", "stream", "target_core", "member_trap_score"], ascending=[True, True, True, False])
    mem["member_trap_rank_within_core"] = mem.groupby(["PLAY_DATE", "stream", "target_core"]).cumcount() + 1
    mem["member_depth_keep"] = mem["member_trap_rank_within_core"] <= mem["lane_member_depth"]
    return mem


def build_playlists(member_candidates: pd.DataFrame, budget40=40, budget80=80) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if member_candidates.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    m = member_candidates[member_candidates["member_depth_keep"]].copy()
    # Budget mode: trap-priority global ranking.
    # This intentionally allows more than one member in a stream when a high-value trap lane says depth is needed.
    # The old global list failed because buried/transition trap lanes were absent, not because global ranking is always wrong.
    m = m.sort_values(["PLAY_DATE", "member_trap_score", "core_score", "full_core_rank"], ascending=[True, False, False, True]).reset_index(drop=True)
    m["rank_within_stream_trap"] = m.groupby(["PLAY_DATE", "stream"]).cumcount() + 1
    m["trap_final_rank"] = m.groupby("PLAY_DATE").cumcount() + 1
    must40 = m[m["trap_final_rank"] <= budget40].copy()
    exp80 = m[(m["trap_final_rank"] > budget40) & (m["trap_final_rank"] <= budget80)].copy()
    top80 = m[m["trap_final_rank"] <= budget80].copy()
    return must40, exp80, top80


def locate_actual_winners(winners: pd.DataFrame, core_locs: pd.DataFrame, member_cands: pd.DataFrame, top40: pd.DataFrame, top80: pd.DataFrame) -> pd.DataFrame:
    if winners is None or winners.empty:
        return pd.DataFrame()
    w = winners[winners.get("is_aabc", False) == True].copy() if "is_aabc" in winners.columns else winners.copy()
    if w.empty:
        return pd.DataFrame()
    rows = []
    core_key = core_locs.set_index(["PLAY_DATE", "stream", "target_core"], drop=False) if not core_locs.empty else None
    mem_key = member_cands.set_index(["PLAY_DATE", "stream", "target_core", "candidate_member"], drop=False) if not member_cands.empty else None
    p40_keys = set(zip(top40.get("PLAY_DATE", []), top40.get("stream", []), top40.get("target_core", []), top40.get("candidate_member", [])))
    p80_keys = set(zip(top80.get("PLAY_DATE", []), top80.get("stream", []), top80.get("target_core", []), top80.get("candidate_member", [])))
    for _, r in w.iterrows():
        pdte = str(r.get("Date", r.get("PLAY_DATE", "")))[:10]
        stream = str(r.get("stream", r.get("StreamKey", ""))).strip()
        core = norm_core(r.get("winner_core", ""))
        mbr = boxed4(r.get("winner_member", r.get("Result4", "")))
        base = {
            "PLAY_DATE": pdte,
            "stream": stream,
            "winner_result": r.get("Result4", ""),
            "winner_core": core,
            "winner_member": mbr,
        }
        loc = {}
        if core_key is not None and (pdte, stream, core) in core_key.index:
            cr = core_key.loc[(pdte, stream, core)]
            if isinstance(cr, pd.DataFrame):
                cr = cr.iloc[0]
            loc.update({
                "full_core_rank": cr.get("full_core_rank", ""),
                "watched8_rank": cr.get("watched8_rank", ""),
                "core_score": cr.get("core_score", ""),
                "trap_keep_core": cr.get("trap_keep", ""),
                "trap_lane": cr.get("trap_lane", ""),
                "global_prevcore_to_core_count": cr.get("global_prevcore_to_core_count", ""),
                "stream_core_hit_count": cr.get("stream_core_hit_count", ""),
            })
        else:
            loc.update({"full_core_rank": "not in core matrix", "trap_keep_core": False, "trap_lane": "CORE_NOT_FOUND"})
        if mem_key is not None and (pdte, stream, core, mbr) in mem_key.index:
            mr = mem_key.loc[(pdte, stream, core, mbr)]
            if isinstance(mr, pd.DataFrame):
                mr = mr.iloc[0]
            loc.update({
                "member_candidate_present": True,
                "member_trap_score": mr.get("member_trap_score", ""),
                "member_trap_rank_within_core": mr.get("member_trap_rank_within_core", ""),
                "member_depth_keep": mr.get("member_depth_keep", ""),
                "member_source": mr.get("member_source", ""),
            })
        else:
            loc.update({"member_candidate_present": False, "member_depth_keep": False})
        loc["played_in_must40"] = (pdte, stream, core, mbr) in p40_keys
        loc["played_in_top80"] = (pdte, stream, core, mbr) in p80_keys
        if loc["played_in_must40"]:
            miss = "PLAYED_MUST40"
        elif loc["played_in_top80"]:
            miss = "PLAYED_EXPANSION80"
        elif not bool(loc.get("trap_keep_core", False)):
            miss = "CORE_NOT_TRAPPED"
        elif not bool(loc.get("member_candidate_present", False)):
            miss = "MEMBER_NOT_GENERATED"
        elif not bool(loc.get("member_depth_keep", False)):
            miss = "MEMBER_DEPTH_CUT"
        else:
            miss = "BUDGET_CUT"
        loc["miss_stage"] = miss
        rows.append({**base, **loc})
    return pd.DataFrame(rows)


def leakage_audit(matrix: pd.DataFrame) -> pd.DataFrame:
    m = matrix.copy()
    if "PLAY_DATE" not in m.columns or "HISTORY_THROUGH" not in m.columns:
        return pd.DataFrame([{"audit": "date columns missing", "rows": len(m), "leakage_rows": "UNKNOWN"}])
    pdte = pd.to_datetime(m["PLAY_DATE"], errors="coerce")
    htd = pd.to_datetime(m["HISTORY_THROUGH"], errors="coerce")
    leakage = (htd >= pdte) & htd.notna() & pdte.notna()
    return pd.DataFrame([{
        "BUILD_MARKER": BUILD_MARKER,
        "rows_checked": len(m),
        "unique_play_dates": m["PLAY_DATE"].nunique(),
        "leakage_rows_history_through_ge_play_date": int(leakage.sum()),
        "status": "FAIL" if int(leakage.sum()) else "PASS",
    }])


def write_outputs(out_dir: Path, outputs: Dict[str, pd.DataFrame], summary_extra: Dict[str, object]) -> Path:
    safe_mkdir(out_dir)
    for name, df in outputs.items():
        if df is None:
            continue
        path = out_dir / name
        df.to_csv(path, index=False)
    # Always write summary.
    summary = pd.DataFrame([{**{"BUILD_MARKER": BUILD_MARKER}, **summary_extra}])
    summary.to_csv(out_dir / "TRAP_RUN_SUMMARY.csv", index=False)
    # Zip outputs.
    zip_path = out_dir / "C120_TRAP_OUTPUTS.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in sorted(out_dir.glob("*.csv")):
            z.write(p, arcname=p.name)
        if (out_dir / "ERROR.txt").exists():
            z.write(out_dir / "ERROR.txt", arcname="ERROR.txt")
    return zip_path


def run_daily(in_dir: Path, out_dir: Path, cfg_dir: Path) -> Path:
    safe_mkdir(out_dir)
    full_path = first_existing(in_dir, ["FULL_DAILY_RULE_MATRIX_ALL_120_CORES.csv"]) or find_file(in_dir, ["full", "matrix"], (".csv",))
    if not full_path:
        raise FileNotFoundError("Put FULL_DAILY_RULE_MATRIX_ALL_120_CORES.csv in IN.")
    hist_path = first_existing(in_dir, ["history_updated.csv", "history_updated_THROUGH_2026-06-18_CLEAN_FULL.csv"]) or find_file(in_dir, ["history"], (".csv",))
    if not hist_path:
        raise FileNotFoundError("Put the clean history CSV in IN.")
    mem_path = first_existing(in_dir, ["DAILY_MEMBER_MATRIX_ALL_CANDIDATES.csv"])
    winner_path = None
    # Optional actual winner file for after-the-fact validation.
    for p in sorted(in_dir.iterdir() if in_dir.exists() else []):
        if p.is_file() and p.suffix.lower() in (".txt", ".csv") and ("winner" in p.name.lower() or "update" in p.name.lower()):
            if p.name != hist_path.name:
                winner_path = p
                break

    watched8 = load_watched_cores(cfg_dir)
    matrix = prepare_matrix(read_csv_any(full_path))
    hist = normalize_history(read_csv_any(hist_path))
    existing_mem = prepare_matrix(read_csv_any(mem_path)) if mem_path and mem_path.exists() else None
    if existing_mem is not None and "candidate_member" not in existing_mem.columns:
        existing_mem = None

    core_locs_all = add_trap_lanes(matrix, hist, watched8)
    core_locs = core_locs_all[core_locs_all["trap_keep"]].copy()
    mem_cands = expand_members_for_core_locations(core_locs, existing_mem)
    must40, exp80, top80 = build_playlists(mem_cands, 40, 80)

    lane_counts = core_locs_all.groupby(["PLAY_DATE", "trap_lane"], dropna=False).size().reset_index(name="core_locations") if not core_locs_all.empty else pd.DataFrame()
    member_lane_counts = mem_cands[mem_cands.get("member_depth_keep", False) == True].groupby(["PLAY_DATE", "trap_lane"], dropna=False).size().reset_index(name="member_plays_allowed_by_depth") if not mem_cands.empty else pd.DataFrame()
    if not lane_counts.empty and not member_lane_counts.empty:
        lane_counts = lane_counts.merge(member_lane_counts, on=["PLAY_DATE", "trap_lane"], how="left")

    winners = pd.DataFrame()
    if winner_path is not None:
        if winner_path.suffix.lower() == ".txt":
            winners = parse_winner_text(winner_path)
        else:
            winners = normalize_history(read_csv_any(winner_path)).rename(columns={"StreamKey": "stream"})
            winners["Date"] = winners["Date"].astype(str)
    winner_audit = locate_actual_winners(winners, core_locs_all, mem_cands, must40, top80) if not winners.empty else pd.DataFrame()

    leak = leakage_audit(matrix)
    summary_extra = {
        "mode": "daily",
        "input_matrix": full_path.name,
        "input_history": hist_path.name,
        "input_member_matrix": mem_path.name if mem_path else "NONE",
        "input_winners": winner_path.name if winner_path else "NONE",
        "watched8_cores": " ".join(watched8),
        "matrix_rows": len(matrix),
        "trap_core_locations": len(core_locs),
        "trap_member_candidates_all": len(mem_cands),
        "must40_rows": len(must40),
        "expansion80_rows": len(exp80),
        "top80_total_rows": len(top80),
        "winner_audit_rows": len(winner_audit),
        "leakage_status": leak.iloc[0].get("status", "UNKNOWN") if not leak.empty else "UNKNOWN",
    }
    outputs = {
        "TRAP_CORE_LOCATIONS_ALL_WITH_LANES.csv": core_locs_all,
        "TRAP_CORE_LOCATIONS.csv": core_locs,
        "TRAP_MEMBER_CANDIDATES.csv": mem_cands,
        "MUST_PLAY_40.csv": must40,
        "EXPAND_TO_80.csv": exp80,
        "TOP80_COMBINED.csv": top80,
        "TRAP_LANE_COUNTS.csv": lane_counts,
        "WINNER_LOCATION_AUDIT.csv": winner_audit,
        "LEAKAGE_AUDIT.csv": leak,
    }
    return write_outputs(out_dir, outputs, summary_extra)


def run_wf(in_dir: Path, out_dir: Path, cfg_dir: Path) -> Path:
    # Same code path as daily, but accepts multi-date matrix and writes aggregate performance.
    zip_path = run_daily(in_dir, out_dir, cfg_dir)
    winner_audit_path = out_dir / "WINNER_LOCATION_AUDIT.csv"
    if winner_audit_path.exists():
        wa = pd.read_csv(winner_audit_path, dtype=str, keep_default_na=False)
        if not wa.empty:
            perf = wa.groupby(["trap_lane", "miss_stage"], dropna=False).size().reset_index(name="winner_count")
            perf.to_csv(out_dir / "WF_TRAP_LANE_PERFORMANCE.csv", index=False)
            # Re-zip including perf.
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
                for p in sorted(out_dir.glob("*.csv")):
                    z.write(p, arcname=p.name)
    return zip_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["daily", "wf"], default="daily")
    ap.add_argument("--in", dest="in_dir", default="IN")
    ap.add_argument("--out", dest="out_dir", default="OUT")
    ap.add_argument("--cfg", dest="cfg_dir", default="CFG")
    args = ap.parse_args()
    in_dir = Path(args.in_dir)
    out_dir = Path(args.out_dir)
    cfg_dir = Path(args.cfg_dir)
    try:
        if args.mode == "daily":
            zip_path = run_daily(in_dir, out_dir, cfg_dir)
        else:
            zip_path = run_wf(in_dir, out_dir, cfg_dir)
        print(f"BUILD_MARKER={BUILD_MARKER}")
        print(f"DONE: {zip_path}")
    except Exception as e:
        write_error(out_dir, f"C120 Trap run failed: {e}", e)
        print(f"RUN FAILED: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
