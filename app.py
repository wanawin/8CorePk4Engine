# P4 Mirror Ladder App v3.5 C120 DYNAMIC TEST READY — PROFILE BUNDLE DEPLOYMENT FIX
# Build date: 2026-07-06
# v3.5 keeps dynamic C120 integration and adds a compressed scoring-profile bundle fallback so Streamlit/GitHub deployments do not fail when profile CSV folders or large CSVs are missed.

from __future__ import annotations

import io
import math
import re
import zipfile
import itertools
import json
import time
import shutil
import sys
from pathlib import Path
from collections import Counter
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd
import streamlit as st

APP_VERSION = "P4 Mirror Ladder App v3.5 C120 DYNAMIC TEST READY — PROFILE BUNDLE DEPLOYMENT FIX"
BUILD_MARKER = "BUILD_2026_07_06_V3_5_PROFILE_BUNDLE_DEPLOYMENT_FIX"
WATCHED8 = {"027", "067", "138", "145", "389", "457", "567", "679"}
DEFAULT_EXCLUDED_STATES = ["Maryland", "Arizona", "AZ"]
SAVE_AUTO_DEFAULT = 150
SAVE_SOLO_DEFAULT = 200
SAVE_COMBINED_DEFAULT = 300
NON_AGGRESSIVE_LADDER = [80, 60, 50, 40, 30, 24, 20, 15]
STEP4_NON_AGGRESSIVE_CUT = 10
STEP4_AGGRESSIVE_CUT = 50
REQUIRED_PROFILE_FILES = [
    "V6_8CORE_USABLE_STREAM_CORE_SIGNALS.csv",
    "V6_8CORE_USABLE_SEED_TRAIT_SIGNALS.csv",
    "V6_8CORE_USABLE_STREAM_SEED_TRAIT_SIGNALS.csv",
    "V6_8CORE_CADENCE_SIGNALS.csv",
    "V6_8CORE_MEMBER_ROLE_PROFILES.csv",
    "V6_8CORE_PROFILE_SUMMARY.csv",
    "V6_8CORE_EXACT_STREAM_CORE_MEMBER_PROFILES.csv",
    "V6_8CORE_STREAMRANK_EVIDENCE_COMPACT.csv",
]
OPTIONAL_PROFILE_FILES = [
    "V6_8CORE_AFFINITY_RULE_CANDIDATES_TOP.csv",
    "V6_8CORE_SIGNAL_COUNT_SUMMARY.csv",
    "V6_8CORE_TOP_STREAM_CORE_SIGNALS.csv",
    "V6_8CORE_TOP_SEED_TRAIT_SIGNALS.csv",
    "V6_8CORE_TOP_STREAM_SEED_TRAIT_SIGNALS.csv",
]
C120_PROFILE_FILES = [
    "C120_TRAP_MEMBER_CANDIDATES.csv",
    "C120_TOP80_COMBINED.csv",
    "C120_MUST_PLAY_40.csv",
    "C120_EXPAND_TO_80.csv",
    "C120_TRAP_CORE_LOCATIONS.csv",
    "C120_TRAP_RUN_SUMMARY.csv",
    "C120_LEAKAGE_AUDIT.csv",
    "C120_WINNER_LOCATION_AUDIT.csv",
]

BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / "profiles"
DEFAULT_HISTORY = BASE_DIR / "data" / "default_history_THROUGH_2026-06-18.csv"

def resolve_c120_rule_library() -> Path:
    """Find the C120 stable rule library in every supported deployment location.

    v3.0 packaged the file under rules/, but Streamlit/GitHub deployments often
    fail when a user uploads/replaces only app.py or misses a nested folder. This
    resolver accepts either location and lets the file-status table show the
    exact path being used instead of silently failing.
    """
    candidates = [
        BASE_DIR / "rules" / "core_rule_library_stable_only_filtered.csv",
        BASE_DIR / "core_rule_library_stable_only_filtered.csv",
        BASE_DIR / "data" / "core_rule_library_stable_only_filtered.csv",
        BASE_DIR / "profiles" / "core_rule_library_stable_only_filtered.csv",
        BASE_DIR / "IN" / "core_rule_library_stable_only_filtered.csv",
        BASE_DIR / "rules" / "core_rule_library_stable_only_filtered(10).csv",
        BASE_DIR / "core_rule_library_stable_only_filtered(10).csv",
        BASE_DIR / "data" / "core_rule_library_stable_only_filtered(10).csv",
        BASE_DIR / "profiles" / "core_rule_library_stable_only_filtered(10).csv",
        BASE_DIR / "IN" / "core_rule_library_stable_only_filtered(10).csv",
    ]
    for path in candidates:
        if path.exists() and path.stat().st_size > 0:
            return path
    return candidates[0]

C120_RULE_LIBRARY = resolve_c120_rule_library()
IN_DIR = BASE_DIR / "IN"
OUTPUT_DIR = BASE_DIR / "outputs"

PROFILE_SEARCH_DIRS = [
    PROFILE_DIR,
    BASE_DIR,
    BASE_DIR / "data",
    BASE_DIR / "IN",
]
PROFILE_BUNDLE_FILENAMES = [
    "profiles_required_bundle.zip",
    "profiles_bundle.zip",
    "V6_REQUIRED_PROFILE_BUNDLE.zip",
]

def _profile_bundle_candidates() -> List[Path]:
    out = []
    for d in [BASE_DIR, PROFILE_DIR, BASE_DIR / "data", BASE_DIR / "IN"]:
        for nm in PROFILE_BUNDLE_FILENAMES:
            out.append(d / nm)
    return out

def _extract_profile_from_bundle(name: str) -> Optional[Path]:
    """Extract one required/optional profile CSV from a bundled zip fallback.

    This prevents false missing-profile failures on Streamlit/GitHub deployments
    where large CSVs or nested profile folders do not upload cleanly. The app
    still uses the original CSV contents; it just reads them from a compressed
    package if the loose CSV is not present.
    """
    for zpath in _profile_bundle_candidates():
        if not (zpath.exists() and zpath.is_file() and zpath.stat().st_size > 0):
            continue
        try:
            with zipfile.ZipFile(zpath, "r") as zf:
                members = zf.namelist()
                match = None
                for m in members:
                    if Path(m).name == name:
                        match = m
                        break
                if not match:
                    continue
                cache_dir = OUTPUT_DIR / "_profile_bundle_cache"
                cache_dir.mkdir(parents=True, exist_ok=True)
                out = cache_dir / name
                if (not out.exists()) or out.stat().st_size == 0 or out.stat().st_mtime < zpath.stat().st_mtime:
                    with zf.open(match) as src, open(out, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                return out
        except Exception:
            continue
    return None

def resolve_profile_file(name: str, profile_dir: str = str(PROFILE_DIR)) -> Path:
    """Find a scoring profile in deployment-safe locations.

    Some GitHub/Streamlit uploads preserve the profiles/ folder, while others
    flatten files into the repo root. v3.5 accepts both and also searches data/
    and IN/. A final recursive search is used only as a safety net so the app
    reports the exact found path instead of falsely declaring files missing.
    """
    candidates = []
    if profile_dir:
        candidates.append(Path(profile_dir) / name)
    for d in PROFILE_SEARCH_DIRS:
        candidates.append(d / name)
    seen = set()
    for path in candidates:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.exists() and path.is_file() and path.stat().st_size > 0:
            return path
    try:
        for path in BASE_DIR.rglob(name):
            # Do not treat files inside the temporary extraction cache as the original source unless they already exist from a bundle extraction.
            if path.exists() and path.is_file() and path.stat().st_size > 0:
                return path
    except Exception:
        pass
    bundled = _extract_profile_from_bundle(name)
    if bundled is not None and bundled.exists() and bundled.stat().st_size > 0:
        return bundled
    return Path(profile_dir) / name
C120_DYNAMIC_OUTPUT_DIR = OUTPUT_DIR / "c120_dynamic"

st.set_page_config(page_title="P4 Mirror Ladder", layout="wide")


def safe_st_dataframe(df, *args, **kwargs):
    """Display a dataframe without Streamlit/PyArrow duplicate-column crashes.

    Pandas can tolerate duplicate column labels, but Streamlit's Arrow renderer
    cannot. Step 4 previews can accidentally request the chosen score column
    twice, and some profile merges may also carry duplicate display labels.
    This helper keeps the first occurrence for display only; it does not change
    the working dataframe or any reduction logic.
    """
    if isinstance(df, pd.DataFrame):
        display_df = df.copy()
        display_df = display_df.loc[:, ~pd.Index(display_df.columns).duplicated()].copy()
        return st.dataframe(display_df, *args, **kwargs)
    return st.dataframe(df, *args, **kwargs)


def unique_existing_cols(df: pd.DataFrame, cols: List[str]) -> List[str]:
    """Return requested columns that exist, preserving order and dropping repeats."""
    out = []
    seen = set()
    for c in cols:
        if c in df.columns and c not in seen:
            out.append(c)
            seen.add(c)
    return out

# ----------------------------- app/file helpers -----------------------------

def safe_slug(x: str) -> str:
    s = str(x).strip() or "UNKNOWN"
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", s)
    return s.strip("_") or "UNKNOWN"


def date_label(x: str) -> str:
    dt = pd.to_datetime(str(x), errors="coerce")
    if pd.isna(dt):
        return safe_slug(x)
    return dt.strftime("%Y-%m-%d")


def next_date_label(x: str) -> str:
    dt = pd.to_datetime(str(x), errors="coerce")
    if pd.isna(dt):
        return ""
    return (dt + pd.Timedelta(days=1)).strftime("%Y-%m-%d")


def find_history_in_in_folder() -> Optional[Path]:
    IN_DIR.mkdir(exist_ok=True)
    candidates = []
    for pattern in ("*.csv", "*.txt", "*.tsv"):
        candidates.extend(IN_DIR.glob(pattern))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def date_options_from_history(history_df: pd.DataFrame) -> List[str]:
    if history_df is None or history_df.empty or "Date" not in history_df.columns:
        return []
    d = pd.to_datetime(history_df["Date"], errors="coerce")
    opts = sorted({x.strftime("%Y-%m-%d") for x in d.dropna()})
    return opts


def seed_page_from_history(history_df: pd.DataFrame, seed_date: str) -> pd.DataFrame:
    if history_df is None or history_df.empty:
        return pd.DataFrame(columns=["Date", "State", "Game", "Result", "Result4", "StreamKey"])
    h = history_df.copy()
    h["_date"] = pd.to_datetime(h["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    target = date_label(seed_date)
    h = h[h["_date"].eq(target)].copy()
    if h.empty:
        return pd.DataFrame(columns=["Date", "State", "Game", "Result", "Result4", "StreamKey"])
    # History can have only StreamKey; split it for display.
    state_game = h["StreamKey"].astype(str).str.split("|", n=1, expand=True, regex=False)
    state = state_game[0].str.strip() if 0 in state_game.columns else "Unknown"
    game = state_game[1].str.strip() if 1 in state_game.columns else h["StreamKey"].astype(str)
    return pd.DataFrame({
        "Date": h["Date"].astype(str).values,
        "State": state.values if hasattr(state, "values") else ["Unknown"] * len(h),
        "Game": game.values if hasattr(game, "values") else h["StreamKey"].astype(str).values,
        "Result": h["Result4"].astype(str).str.zfill(4).values,
        "Result4": h["Result4"].astype(str).map(norm4).values,
        "StreamKey": h["StreamKey"].astype(str).values,
    }).drop_duplicates(["StreamKey"], keep="last").reset_index(drop=True)


def load_history_source(history_upload=None) -> Tuple[pd.DataFrame, str, str]:
    """Return history, source label, and user-facing note. Runs even if IN folder is empty."""
    if history_upload is not None:
        h = read_history_file(history_upload)
        return h, getattr(history_upload, "name", "uploaded history"), "Using uploaded history file."
    in_file = find_history_in_in_folder()
    if in_file is not None:
        h = read_history_file(in_file)
        return h, str(in_file.name), f"Using history from IN folder: {in_file.name}."
    if DEFAULT_HISTORY.exists():
        h = load_packaged_history()
        return h, DEFAULT_HISTORY.name, "IN folder has no history file; using packaged default history."
    return pd.DataFrame(columns=["Date", "StreamKey", "Result4"]), "NO_HISTORY", "No history found. App can still parse seeds, but rolling mirror/profile context will be weak."


def base_output_folder(context: Dict) -> Path:
    play = date_label(context.get("play_date", "PLAY_DATE_UNKNOWN"))
    seed = date_label(context.get("seed_date", "SEED_DATE_UNKNOWN"))
    folder = OUTPUT_DIR / play / f"HISTORY_THROUGH_{seed}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def current_metadata(context: Dict, stage_label: str = "") -> Dict[str, str]:
    return {
        "APP_VERSION": APP_VERSION,
        "BUILD_MARKER": BUILD_MARKER,
        "PLAY_DATE": str(context.get("play_date", "")),
        "SEED_DATE_HISTORY_THROUGH": str(context.get("seed_date", "")),
        "HISTORY_SOURCE": str(context.get("history_source", "")),
        "EXCLUDED_STATES": ", ".join(context.get("excluded_states", [])),
        "CURRENT_STAGE": stage_label,
        "RANKING_NOTE": "Playlist is sorted by profile_final_member_score descending, strongest model score to weakest; not a guarantee.",
        "STRAIGHT_LAYER_STATUS": "Not active yet; boxed member/core fields are preserved so straight layer can be added later.",
    }


def playlist_text(df: pd.DataFrame, context: Dict, stage_label: str = "") -> str:
    meta = current_metadata(context, stage_label)
    pl = printable_playlist(df)
    lines = []
    lines.append("P4 MIRROR LADDER PLAYLIST")
    for k, v in meta.items():
        lines.append(f"{k}: {v}")
    lines.append(f"ROWS: {len(pl)}")
    lines.append("")
    if pl.empty:
        lines.append("No rows selected.")
    else:
        lines.append("Rank | Stream | Seed | Core | Member | Score | Branch")
        lines.append("-" * 100)
        for r in pl.itertuples(index=False):
            lines.append(f"{r.Rank:>4} | {r.Stream} | {r.Seed} | {r.Core} | {r.Member} | {r.Score} | {r.Branch}")
    return "\n".join(lines) + "\n"


def stage_summary_text(stages: List[Dict], context: Dict) -> str:
    lines = ["P4 MIRROR LADDER STAGE SUMMARY"]
    for k, v in current_metadata(context, stages[-1]["label"] if stages else "").items():
        lines.append(f"{k}: {v}")
    lines.append("")
    for i, s in enumerate(stages):
        m = stage_metrics(s["rows"])
        lines.append(f"{i}. {s['label']} | Rows={m['Rows']} Streams={m['Streams']} Cores={m['Cores']} Members={m['Members']} | {s.get('note','')}")
    return "\n".join(lines) + "\n"


def dated_filename(prefix: str, context: Dict, ext: str) -> str:
    play = date_label(context.get("play_date", "PLAY_DATE"))
    seed = date_label(context.get("seed_date", "SEED_DATE"))
    return f"{prefix}_{play}_HISTORY_THROUGH_{seed}.{ext}"


def save_session_to_daily_folder(stages: List[Dict], context: Dict, winner_audit: Optional[pd.DataFrame] = None, stage_summary: Optional[pd.DataFrame] = None, removed_log: Optional[pd.DataFrame] = None) -> Optional[Path]:
    if not stages:
        return None
    folder = base_output_folder(context)
    for s in stages:
        s["rows"].to_csv(folder / f"{safe_slug(s['label'])}.csv", index=False)
    final = printable_playlist(stages[-1]["rows"])
    final.to_csv(folder / dated_filename("PRINTABLE_CURRENT_PLAYLIST", context, "csv"), index=False)
    (folder / dated_filename("PRINTABLE_CURRENT_PLAYLIST", context, "txt")).write_text(playlist_text(stages[-1]["rows"], context, stages[-1]["label"]), encoding="utf-8")
    (folder / dated_filename("STAGE_SUMMARY", context, "txt")).write_text(stage_summary_text(stages, context), encoding="utf-8")
    if winner_audit is not None and not winner_audit.empty:
        winner_audit.to_csv(folder / dated_filename("WINNER_AUDIT_BY_STAGE", context, "csv"), index=False)
    if stage_summary is not None and not stage_summary.empty:
        stage_summary.to_csv(folder / dated_filename("STAGE_WINNER_SUMMARY", context, "csv"), index=False)
    if removed_log is not None and not removed_log.empty:
        removed_log.to_csv(folder / dated_filename("STEP4_REMOVED_ROWS_AUDIT", context, "csv"), index=False)
    if stages:
        latest = stages[-1]["rows"]
        fam = score_family_firing_audit(latest, context) if isinstance(latest, pd.DataFrame) and "score_files_fired" in latest.columns else pd.DataFrame()
        if not fam.empty:
            fam.to_csv(folder / dated_filename("PROFILE_FAMILY_FIRING_AUDIT", context, "csv"), index=False)
        core_audit = context.get("core_audit", pd.DataFrame()) if isinstance(context, dict) else pd.DataFrame()
        if isinstance(core_audit, pd.DataFrame) and not core_audit.empty:
            core_audit.to_csv(folder / dated_filename("STEP3_HISTORICAL_CORE_AUDIT", context, "csv"), index=False)
        dyn = context.get("c120_dynamic_bundle", {}) if isinstance(context, dict) else {}
        if isinstance(dyn, dict):
            man = dyn.get("c120_manifest", pd.DataFrame())
            if isinstance(man, pd.DataFrame) and not man.empty:
                man.to_csv(folder / dated_filename("C120_DYNAMIC_PREFLIGHT_MANIFEST", context, "csv"), index=False)
            sc = dyn.get("c120_source_counts", pd.DataFrame())
            if isinstance(sc, pd.DataFrame) and not sc.empty:
                sc.to_csv(folder / dated_filename("C120_DYNAMIC_SOURCE_COUNTS", context, "csv"), index=False)
    return folder


# ----------------------------- basic normalization -----------------------------

def norm4(x) -> str:
    s = "".join(ch for ch in str(x) if ch.isdigit())
    return s.zfill(4)[-4:] if s else ""


def boxed_s(s) -> str:
    n = norm4(s)
    return "".join(sorted(n)) if n else ""


def is_aabc_member(m: str) -> bool:
    return len(m) == 4 and sorted(Counter(m).values()) == [1, 1, 2]


def core_from_member(m: str) -> str:
    return "".join(sorted(set(m))) if len(m) == 4 and is_aabc_member(m) else ""


def core_from_result(s: str) -> str:
    return core_from_member(boxed_s(s))


def norm_core(s) -> str:
    digs = "".join(ch for ch in str(s) if ch.isdigit())
    if len(digs) >= 4:
        return core_from_result(digs[:4])
    return digs.zfill(3)[-3:] if digs else ""


def seed_sum(s) -> int:
    n = norm4(s)
    return sum(int(c) for c in n) if n else 0


def seed_spread(s) -> int:
    ds = [int(c) for c in norm4(s)]
    return max(ds) - min(ds) if ds else 0


def vset_s(s) -> str:
    return "".join(map(str, sorted(set((int(c) % 5) + 1 for c in norm4(s)))))


def digits_from_str(s) -> List[int]:
    return [int(c) for c in norm4(s)]


def to_num(x, default=0.0) -> float:
    try:
        if x is None:
            return default
        stx = str(x).strip()
        if stx == "" or stx.lower() in {"nan", "none", "not_ready", "not_tested"}:
            return default
        return float(stx)
    except Exception:
        return default


def result4_from_text(text: str) -> str:
    """Extract base 4 digits from unnormalized result text such as 6-0-3-7, Fireball: 7."""
    s = str(text)
    # Prefer hyphen/space separated four digits at the front of a result field.
    m = re.search(r"(?<!\d)(\d)\s*[- ]\s*(\d)\s*[- ]\s*(\d)\s*[- ]\s*(\d)(?!\d)", s)
    if m:
        return "".join(m.groups())
    # Fallback: first 4 digits in the text. This intentionally ignores Fireball/Wild Ball after the base result.
    digs = re.findall(r"\d", s)
    return "".join(digs[:4]) if len(digs) >= 4 else ""


def clean_stream(state: str, game: str) -> str:
    return f"{str(state).strip()} | {str(game).strip()}"


def state_from_stream(stream: str) -> str:
    return str(stream).split("|")[0].strip()


def is_nonplayable_stream(stream: str, excluded_states: List[str]) -> bool:
    state = state_from_stream(stream).lower()
    excluded = {x.strip().lower() for x in excluded_states if str(x).strip()}
    return state in excluded or ("az" in excluded and state == "arizona")

# ----------------------------- parsing uploads -----------------------------

def parse_uploaded_table(uploaded_file, default_date: str = "") -> pd.DataFrame:
    if uploaded_file is None:
        return pd.DataFrame(columns=["Date", "State", "Game", "Result", "Result4", "StreamKey"])
    raw = uploaded_file.getvalue()
    name = getattr(uploaded_file, "name", "upload")
    text = raw.decode("utf-8", errors="ignore") if isinstance(raw, (bytes, bytearray)) else str(raw)
    return parse_text_or_csv(text, name=name, default_date=default_date)


def parse_text_or_csv(text: str, name: str = "upload", default_date: str = "") -> pd.DataFrame:
    rows: List[Dict] = []
    stripped = text.strip()
    if not stripped:
        return pd.DataFrame(columns=["Date", "State", "Game", "Result", "Result4", "StreamKey"])

    # Try CSV/TSV first when there is a header.
    lowered_first = stripped.splitlines()[0].lower()
    if any(key in lowered_first for key in ["state", "game", "result", "stream", "date"]):
        for sep in [None, "\t", ",", "|"]:
            try:
                df = pd.read_csv(io.StringIO(text), sep=sep, engine="python", dtype=str, keep_default_na=False)
                if len(df.columns) >= 2:
                    parsed = parse_dataframe_rows(df, default_date=default_date)
                    if not parsed.empty:
                        return parsed
            except Exception:
                pass

    # TXT/tab-line parser.
    for line in stripped.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("\t")]
        if len(parts) >= 4:
            date_s, state, game = parts[0], parts[1], parts[2]
            result = parts[3]
            result4 = result4_from_text(result)
            if result4:
                rows.append({"Date": date_s or default_date, "State": state, "Game": game, "Result": result, "Result4": result4, "StreamKey": clean_stream(state, game)})
                continue
        # More permissive fallback: split by 2+ spaces around result; otherwise store Unknown.
        result4 = result4_from_text(line)
        if result4:
            rows.append({"Date": default_date, "State": "Unknown", "Game": line[:80], "Result": line, "Result4": result4, "StreamKey": clean_stream("Unknown", line[:80])})
    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=["Date", "State", "Game", "Result", "Result4", "StreamKey"])
    out["Result4"] = out["Result4"].map(norm4)
    out = out.drop_duplicates(["StreamKey"], keep="last").reset_index(drop=True)
    return out


def parse_dataframe_rows(df: pd.DataFrame, default_date: str = "") -> pd.DataFrame:
    colmap = {c.lower().strip(): c for c in df.columns}
    rows: List[Dict] = []
    for _, r in df.iterrows():
        # Known columns.
        date_s = str(r.get(colmap.get("date", ""), default_date)) if "date" in colmap else default_date
        state = ""
        game = ""
        stream = ""
        result = ""
        for cand in ["streamkey", "stream", "stream_name", "streamname"]:
            if cand in colmap:
                stream = str(r.get(colmap[cand], "")).strip()
                break
        for cand in ["state", "jurisdiction"]:
            if cand in colmap:
                state = str(r.get(colmap[cand], "")).strip()
                break
        for cand in ["game", "draw", "draw_name"]:
            if cand in colmap:
                game = str(r.get(colmap[cand], "")).strip()
                break
        for cand in ["result4", "result", "winning numbers", "winning_numbers", "number", "seed"]:
            if cand in colmap:
                result = str(r.get(colmap[cand], "")).strip()
                break
        if not result:
            result = " ".join(map(str, r.tolist()))
        result4 = result4_from_text(result)
        if not result4:
            continue
        if stream and "|" in stream:
            state = state or state_from_stream(stream)
            game = game or stream.split("|", 1)[1].strip()
            streamkey = stream
        else:
            state = state or "Unknown"
            game = game or stream or "Unknown"
            streamkey = clean_stream(state, game)
        rows.append({"Date": date_s, "State": state, "Game": game, "Result": result, "Result4": norm4(result4), "StreamKey": streamkey})
    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=["Date", "State", "Game", "Result", "Result4", "StreamKey"])
    return out.drop_duplicates(["StreamKey"], keep="last").reset_index(drop=True)

# ----------------------------- cached universes -----------------------------

@st.cache_data(show_spinner=False)
def build_universe():
    members = []
    for digs in itertools.combinations_with_replacement("0123456789", 4):
        m = "".join(digs)
        if is_aabc_member(m):
            members.append(m)
    member_idx = {m: i for i, m in enumerate(members)}
    member_core = {m: core_from_member(m) for m in members}
    watched_members = [m for m in members if member_core[m] in WATCHED8]
    all_vsets = sorted({vset_s(f"{i:04d}") for i in range(10000)} | {vset_s(m) for m in members})
    vcode = {v: i for i, v in enumerate(all_vsets)}
    seed_vcode_arr = np.zeros(10000, dtype=np.int16)
    mirror_mat = np.zeros((10000, len(members)), dtype=np.int8)
    mem_digits_unique = [set(digits_from_str(m)) for m in members]
    for i in range(10000):
        s = f"{i:04d}"
        seed_vcode_arr[i] = vcode[vset_s(s)]
        mir = {(d + 5) % 10 for d in digits_from_str(s)}
        mirror_mat[i] = [len(ud & mir) for ud in mem_digits_unique]
    return members, member_idx, member_core, watched_members, all_vsets, vcode, seed_vcode_arr, mirror_mat

@st.cache_data(show_spinner=False)
def load_packaged_history() -> pd.DataFrame:
    return read_history_file(DEFAULT_HISTORY)


def read_history_file(path_or_upload) -> pd.DataFrame:
    if hasattr(path_or_upload, "getvalue"):
        raw = path_or_upload.getvalue()
        text = raw.decode("utf-8", errors="ignore") if isinstance(raw, (bytes, bytearray)) else str(raw)
        parsed = parse_text_or_csv(text, name=getattr(path_or_upload, "name", "history_upload"))
        if not parsed.empty:
            parsed = parsed.rename(columns={"Date": "Date", "StreamKey": "StreamKey", "Result4": "Result4"})
            return parsed[["Date", "StreamKey", "Result4"]].copy()
    else:
        p = Path(path_or_upload)
        if p.exists():
            df = pd.read_csv(p, dtype=str, keep_default_na=False)
            if {"Date", "StreamKey", "Result4"}.issubset(df.columns):
                return df[["Date", "StreamKey", "Result4"]].copy()
            parsed = parse_text_or_csv(p.read_text(encoding="utf-8", errors="ignore"), name=p.name)
            if not parsed.empty:
                return parsed[["Date", "StreamKey", "Result4"]].copy()
    return pd.DataFrame(columns=["Date", "StreamKey", "Result4"])

@st.cache_data(show_spinner=False)

def profile_file_status(profile_dir: str = str(PROFILE_DIR)) -> pd.DataFrame:
    """Deployment-safe profile file check. Does not load large files."""
    rows = []
    for name in REQUIRED_PROFILE_FILES + OPTIONAL_PROFILE_FILES + C120_PROFILE_FILES:
        f = resolve_profile_file(name, profile_dir)
        exists = f.exists() and f.is_file() and f.stat().st_size > 0
        try:
            rel = str(f.relative_to(BASE_DIR)) if f.exists() else str(f)
        except Exception:
            rel = str(f)
        rows.append({
            "file": name,
            "path": rel,
            "required": name in REQUIRED_PROFILE_FILES,
            "exists": bool(exists),
            "size_bytes": int(f.stat().st_size) if f.exists() else 0,
            "status": "READY" if exists else ("MISSING_REQUIRED" if name in REQUIRED_PROFILE_FILES else "MISSING_OPTIONAL"),
        })
    return pd.DataFrame(rows)


def profile_ready(profile_dir: str = str(PROFILE_DIR)) -> Tuple[bool, pd.DataFrame, List[str]]:
    status = profile_file_status(profile_dir)
    missing = status[(status["required"]) & (~status["exists"] | (status["size_bytes"] <= 0))]["file"].astype(str).tolist()
    return len(missing) == 0, status, missing



def c120_engine_file_status() -> pd.DataFrame:
    """Verify dynamic C120 engine/rule files before allowing C120 scoring."""
    rows = []
    for name, path, required in [
        ("full_pipeline.py", BASE_DIR / "full_pipeline.py", True),
        ("core_engine.py", BASE_DIR / "core_engine.py", True),
        ("rule_daily_portfolio_audit.py", BASE_DIR / "rule_daily_portfolio_audit.py", True),
        ("c120_trap_engine_v23.py", BASE_DIR / "c120_trap_engine_v23.py", False),
        ("run_trap_integrated.py", BASE_DIR / "run_trap_integrated.py", False),
        ("core_rule_library_stable_only_filtered.csv", resolve_c120_rule_library(), True),
    ]:
        exists = path.exists() and path.stat().st_size > 0
        rows.append({
            "file": name,
            "path": str(path.relative_to(BASE_DIR)) if path.exists() else str(path),
            "required": required,
            "exists": bool(exists),
            "size_bytes": int(path.stat().st_size) if path.exists() else 0,
            "status": "READY" if exists else ("MISSING_REQUIRED" if required else "MISSING_OPTIONAL"),
        })
    return pd.DataFrame(rows)


def c120_required_ready() -> Tuple[bool, pd.DataFrame, List[str]]:
    status = c120_engine_file_status()
    missing = status[(status["required"]) & (~status["exists"] | (status["size_bytes"] <= 0))]["file"].astype(str).tolist()
    return len(missing) == 0, status, missing


def c120_output_bundle_from_files(out_dir: Path) -> Dict[str, pd.DataFrame]:
    wanted = [
        "FULL_DAILY_RULE_MATRIX_ALL_120_CORES.csv",
        "DAILY_CORE_MATRIX_ALL_CANDIDATES.csv",
        "DAILY_MEMBER_MATRIX_ALL_CANDIDATES.csv",
        "DAILY_MEMBER_PLAYLIST_TOPN.csv",
        "DAILY_MEMBER_DELETIONS_AND_REPLACEMENTS.csv",
        "DAILY_SEED_EVENTS_FOR_PLAY_DATE.csv",
        "MATRIX_BUILD_REPORT.csv",
        "SEED_ALIGNMENT_AUDIT.csv",
        "SEED_ALIGNMENT_SUMMARY.csv",
        "NORMALIZED_RULES_USED.csv",
        "RUN_REPORT.csv",
        "RUN_SUMMARY.csv",
        "STREAM_SKIP_AUDIT.csv",
        "INPUT_SCHEMA_AUDIT.csv",
        "SCHEMA_ADAPTER_AUDIT.csv",
        "MATRIX_ROW_DEFINITIONS.csv",
        "TRAP_CORE_LOCATIONS.csv",
        "TRAP_CORE_LOCATIONS_ALL_WITH_LANES.csv",
        "TRAP_MEMBER_CANDIDATES.csv",
        "MUST_PLAY_40.csv",
        "EXPAND_TO_80.csv",
        "TOP80_COMBINED.csv",
        "TRAP_LANE_COUNTS.csv",
        "TRAP_RUN_SUMMARY.csv",
        "LEAKAGE_AUDIT.csv",
    ]
    out = {}
    for name in wanted:
        p = Path(out_dir) / name
        if p.exists() and p.stat().st_size > 0:
            try:
                out[name] = pd.read_csv(p, dtype=str, keep_default_na=False)
            except Exception:
                out[name] = pd.DataFrame()
    return out


def build_c120_dynamic_indices(outputs: Dict[str, pd.DataFrame]) -> Dict:
    """Build exact-date dynamic C120 lookup indices used by Step 3/Step 4 scoring."""
    def row_date(r, key):
        return date_label(r.get(key, ""))
    core_idx = {}
    member_idx = {}
    source_counts = []

    core_sources = [
        ("FULL_DAILY_RULE_MATRIX_ALL_120_CORES", outputs.get("FULL_DAILY_RULE_MATRIX_ALL_120_CORES.csv", pd.DataFrame())),
        ("DAILY_CORE_MATRIX_ALL_CANDIDATES", outputs.get("DAILY_CORE_MATRIX_ALL_CANDIDATES.csv", pd.DataFrame())),
        ("TRAP_CORE_LOCATIONS", outputs.get("TRAP_CORE_LOCATIONS.csv", pd.DataFrame())),
    ]
    for src_name, df in core_sources:
        cnt = 0
        if isinstance(df, pd.DataFrame) and not df.empty:
            for _, r in df.iterrows():
                k = (
                    row_date(r, "PLAY_DATE") or row_date(r, "draw_date"),
                    row_date(r, "HISTORY_THROUGH"),
                    str(r.get("stream", "")).strip(),
                    norm4(r.get("seed", r.get("prior_result_used_as_seed", ""))),
                    norm_core(r.get("target_core", r.get("core", ""))),
                )
                if not all(k):
                    continue
                rec = r.to_dict(); rec["c120_source_file"] = src_name
                old = core_idx.get(k)
                score_new = to_num(rec.get("final_stream_core_score", rec.get("trap_priority_score", rec.get("evidence_score", 0))))
                score_old = to_num(old.get("final_stream_core_score", old.get("trap_priority_score", old.get("evidence_score", 0)))) if old else -1e99
                if old is None or score_new > score_old:
                    core_idx[k] = rec
                cnt += 1
        source_counts.append({"family": src_name, "indexed_rows": cnt})

    member_sources = [
        ("DAILY_MEMBER_MATRIX_ALL_CANDIDATES", outputs.get("DAILY_MEMBER_MATRIX_ALL_CANDIDATES.csv", pd.DataFrame())),
        ("DAILY_MEMBER_PLAYLIST_TOPN", outputs.get("DAILY_MEMBER_PLAYLIST_TOPN.csv", pd.DataFrame())),
        ("TRAP_MEMBER_CANDIDATES", outputs.get("TRAP_MEMBER_CANDIDATES.csv", pd.DataFrame())),
        ("TOP80_COMBINED", outputs.get("TOP80_COMBINED.csv", pd.DataFrame())),
        ("MUST_PLAY_40", outputs.get("MUST_PLAY_40.csv", pd.DataFrame())),
        ("EXPAND_TO_80", outputs.get("EXPAND_TO_80.csv", pd.DataFrame())),
    ]
    for src_name, df in member_sources:
        cnt = 0
        if isinstance(df, pd.DataFrame) and not df.empty:
            for _, r in df.iterrows():
                k = (
                    row_date(r, "PLAY_DATE") or row_date(r, "draw_date"),
                    row_date(r, "HISTORY_THROUGH"),
                    str(r.get("stream", "")).strip(),
                    norm4(r.get("seed", r.get("prior_result_used_as_seed", ""))),
                    norm_core(r.get("target_core", r.get("core", ""))),
                    boxed_s(r.get("candidate_member", r.get("member", ""))),
                )
                if not all(k):
                    continue
                rec = r.to_dict(); rec["c120_source_file"] = src_name
                old = member_idx.get(k)
                score_new = to_num(rec.get("final_member_score", rec.get("existing_final_member_score_num", rec.get("member_trap_score", rec.get("member_soft_score", 0)))))
                score_old = to_num(old.get("final_member_score", old.get("existing_final_member_score_num", old.get("member_trap_score", old.get("member_soft_score", 0))))) if old else -1e99
                if old is None or score_new > score_old:
                    member_idx[k] = rec
                cnt += 1
        source_counts.append({"family": src_name, "indexed_rows": cnt})

    seed_sum = outputs.get("SEED_ALIGNMENT_SUMMARY.csv", pd.DataFrame())
    build_report = outputs.get("MATRIX_BUILD_REPORT.csv", pd.DataFrame())
    leakage = outputs.get("LEAKAGE_AUDIT.csv", pd.DataFrame())
    certification = ""
    bad_alignment_rows = ""
    if isinstance(seed_sum, pd.DataFrame) and not seed_sum.empty:
        certification = str(seed_sum.iloc[0].get("certification", ""))
        bad_alignment_rows = str(seed_sum.iloc[0].get("bad_alignment_rows", ""))
    manifest = pd.DataFrame([{
        "c120_dynamic_status": "READY" if len(core_idx) > 0 and len(member_idx) > 0 and str(certification).upper() == "PASS" else "NOT_READY",
        "core_rows_indexed": len(core_idx),
        "member_rows_indexed": len(member_idx),
        "seed_alignment_certification": certification,
        "bad_alignment_rows": bad_alignment_rows,
        "matrix_build_rows": len(outputs.get("FULL_DAILY_RULE_MATRIX_ALL_120_CORES.csv", pd.DataFrame())),
        "member_matrix_rows": len(outputs.get("DAILY_MEMBER_MATRIX_ALL_CANDIDATES.csv", pd.DataFrame())),
        "playlist_topn_rows": len(outputs.get("DAILY_MEMBER_PLAYLIST_TOPN.csv", pd.DataFrame())),
        "leakage_status": str(leakage.iloc[0].get("status", "")) if isinstance(leakage, pd.DataFrame) and not leakage.empty else "NOT_RUN_OR_NOT_PROVIDED",
    }])
    return {"c120_core_idx": core_idx, "c120_member_idx": member_idx, "c120_source_counts": pd.DataFrame(source_counts), "c120_manifest": manifest, "c120_outputs": outputs}


def apply_c120_dynamic_to_profiles(profiles: Dict, dynamic_bundle: Optional[Dict]) -> Dict:
    """Overlay dynamic C120 indices over packaged static C120 outputs for this session."""
    if not isinstance(dynamic_bundle, dict):
        return profiles
    bundle_manifest = dynamic_bundle.get("c120_manifest", pd.DataFrame())
    ready = False
    if isinstance(bundle_manifest, pd.DataFrame) and not bundle_manifest.empty:
        ready = str(bundle_manifest.iloc[0].get("c120_dynamic_status", "")).upper() == "READY"
    if not ready:
        return profiles
    p2 = dict(profiles)
    if isinstance(dynamic_bundle.get("c120_member_idx"), dict):
        p2["c120_member_idx"] = dynamic_bundle["c120_member_idx"]
    if isinstance(dynamic_bundle.get("c120_core_idx"), dict):
        p2["c120_core_idx"] = dynamic_bundle["c120_core_idx"]
    p2["c120_guard"] = {
        **p2.get("c120_guard", {}),
        "mode": "DYNAMIC_C120_PRELIGHT_READY",
        "member_rows_indexed": len(p2.get("c120_member_idx", {})),
        "core_rows_indexed": len(p2.get("c120_core_idx", {})),
    }
    return p2


def run_c120_dynamic_preflight(history_df: pd.DataFrame, play_date: str, seed_date: str, work_root: Optional[Path] = None) -> Dict:
    """Run the uploaded C120 stable-rule pipeline from current history before scoring."""
    global C120_RULE_LIBRARY
    C120_RULE_LIBRARY = resolve_c120_rule_library()
    ok, status, missing = c120_required_ready()
    if not ok:
        return {"ok": False, "error": "Missing C120 required files: " + ", ".join(missing), "file_status": status}
    if history_df is None or history_df.empty:
        return {"ok": False, "error": "No history dataframe available for C120 dynamic preflight.", "file_status": status}
    work_root = Path(work_root or C120_DYNAMIC_OUTPUT_DIR)
    run_id = f"PLAY_{date_label(play_date)}__HISTORY_THROUGH_{date_label(seed_date)}"
    out_dir = work_root / run_id
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    hist_path = out_dir / "C120_INPUT_HISTORY.csv"
    history_df.to_csv(hist_path, index=False)
    try:
        if str(BASE_DIR) not in sys.path:
            sys.path.insert(0, str(BASE_DIR))
        from full_pipeline import run_full_daily
        from run_trap_integrated import run_trap_from_files
        outputs, zip_path = run_full_daily(
            history_obj=hist_path,
            rules_obj=C120_RULE_LIBRARY,
            out_dir=out_dir,
            history_filename=hist_path.name,
            rules_filename=C120_RULE_LIBRARY.name,
            play_date=date_label(play_date),
            history_through=date_label(seed_date),
            config_name="ALL_BALANCED",
        )
        # Add trap outputs when possible; failure is recorded but does not contaminate the core/member matrix.
        trap_error = ""
        try:
            trap_out = out_dir / "TRAP_DYNAMIC_OUT"
            run_trap_from_files(
                out_dir / "FULL_DAILY_RULE_MATRIX_ALL_120_CORES.csv",
                hist_path,
                trap_out,
                member_matrix=out_dir / "DAILY_MEMBER_MATRIX_ALL_CANDIDATES.csv",
                winners=None,
                mode="daily",
            )
            for pth in trap_out.rglob("*.csv"):
                shutil.copy2(pth, out_dir / pth.name)
        except Exception as te:
            trap_error = str(te)
        file_outputs = c120_output_bundle_from_files(out_dir)
        bundle = build_c120_dynamic_indices(file_outputs)
        manifest = bundle.get("c120_manifest", pd.DataFrame())
        ready = isinstance(manifest, pd.DataFrame) and not manifest.empty and str(manifest.iloc[0].get("c120_dynamic_status", "")).upper() == "READY"
        if isinstance(manifest, pd.DataFrame) and not manifest.empty:
            manifest["preflight_output_folder"] = str(out_dir)
            manifest["preflight_zip"] = str(zip_path)
            manifest["trap_error"] = trap_error
        bundle["c120_manifest"] = manifest
        bundle["file_status"] = status
        bundle["ok"] = bool(ready)
        bundle["error"] = "" if ready else "C120 dynamic preflight ran but did not reach READY status. Check manifest/seed alignment/member indices."
        return bundle
    except Exception as e:
        return {"ok": False, "error": f"C120 dynamic preflight failed: {e}", "file_status": status}


@st.cache_data(show_spinner=False)
def load_profiles(profile_dir: str = str(PROFILE_DIR)):
    """Load all scoring profiles used by Step 4.

    v1.9 treats the exact stream+core+boxed-member profile and the compact
    StreamRank evidence matrix as required. If they are missing, Step 4 blocks
    instead of silently creating partial/weak scores.
    """
    p = Path(profile_dir)
    def read(name):
        f = resolve_profile_file(name, str(p))
        if f.exists() and f.is_file() and f.stat().st_size > 0:
            return pd.read_csv(f, dtype=str, keep_default_na=False)
        return pd.DataFrame()

    status = profile_file_status(profile_dir)
    missing_required = status[(status["required"]) & (~status["exists"] | (status["size_bytes"] <= 0))]["file"].astype(str).tolist()

    stream_core = read("V6_8CORE_USABLE_STREAM_CORE_SIGNALS.csv")
    seed_trait = read("V6_8CORE_USABLE_SEED_TRAIT_SIGNALS.csv")
    stream_seed_trait = read("V6_8CORE_USABLE_STREAM_SEED_TRAIT_SIGNALS.csv")
    cadence = read("V6_8CORE_CADENCE_SIGNALS.csv")
    member_role = read("V6_8CORE_MEMBER_ROLE_PROFILES.csv")
    core_profiles = read("V6_8CORE_PROFILE_SUMMARY.csv")
    top_stream_core = read("V6_8CORE_TOP_STREAM_CORE_SIGNALS.csv")
    top_seed_trait = read("V6_8CORE_TOP_SEED_TRAIT_SIGNALS.csv")
    top_stream_seed_trait = read("V6_8CORE_TOP_STREAM_SEED_TRAIT_SIGNALS.csv")
    affinity = read("V6_8CORE_AFFINITY_RULE_CANDIDATES_TOP.csv")
    signal_count_summary = read("V6_8CORE_SIGNAL_COUNT_SUMMARY.csv")
    exact_pair = read("V6_8CORE_EXACT_STREAM_CORE_MEMBER_PROFILES.csv")
    streamrank = read("V6_8CORE_STREAMRANK_EVIDENCE_COMPACT.csv")
    c120_members = read("C120_TRAP_MEMBER_CANDIDATES.csv")
    c120_top80 = read("C120_TOP80_COMBINED.csv")
    c120_must40 = read("C120_MUST_PLAY_40.csv")
    c120_expand80 = read("C120_EXPAND_TO_80.csv")
    c120_core_locations = read("C120_TRAP_CORE_LOCATIONS.csv")
    c120_run_summary = read("C120_TRAP_RUN_SUMMARY.csv")
    c120_leakage = read("C120_LEAKAGE_AUDIT.csv")

    for d in [stream_core, seed_trait, stream_seed_trait, cadence, member_role, core_profiles, top_stream_core, top_seed_trait, top_stream_seed_trait, affinity, signal_count_summary, exact_pair, streamrank, c120_members, c120_top80, c120_must40, c120_expand80, c120_core_locations]:
        if not d.empty:
            for cc in ["core_str", "core"]:
                if cc in d.columns:
                    d[cc] = d[cc].map(norm_core)

    def best_signal_index(df: pd.DataFrame, key_cols: List[str], metric: str = "confidence_score") -> Dict:
        idx = {}
        if df.empty or not set(key_cols).issubset(df.columns):
            return idx
        for _, r in df.iterrows():
            key = tuple(str(r.get(c, "")).strip() for c in key_cols)
            if not all(key):
                continue
            old = idx.get(key)
            if old is None or to_num(r.get(metric)) > to_num(old.get(metric)):
                idx[key] = r.to_dict()
        return idx

    sc_idx = best_signal_index(stream_core, ["core_str", "StreamKey"])
    st_idx = best_signal_index(seed_trait, ["core_str", "trait_name", "trait_value"])
    sst_idx = best_signal_index(stream_seed_trait, ["core_str", "StreamKey", "trait_name", "trait_value"])
    top_sc_idx = best_signal_index(top_stream_core, ["core_str", "StreamKey"])
    top_st_idx = best_signal_index(top_seed_trait, ["core_str", "trait_name", "trait_value"])
    top_sst_idx = best_signal_index(top_stream_seed_trait, ["core_str", "StreamKey", "trait_name", "trait_value"])
    cad_idx = best_signal_index(cadence, ["core_str", "SameCoreGapBucket"])

    mr_idx = {}
    if not member_role.empty and {"core_str", "member_str"}.issubset(member_role.columns):
        for _, r in member_role.iterrows():
            mr_idx[(r["core_str"], boxed_s(r["member_str"]))] = r.to_dict()
    cp_idx = {r["core_str"]: r.to_dict() for _, r in core_profiles.iterrows() if "core_str" in core_profiles.columns and r.get("core_str")}

    sig_count_idx = {}
    if not signal_count_summary.empty and "core_str" in signal_count_summary.columns:
        for _, r in signal_count_summary.iterrows():
            core = norm_core(r.get("core_str", ""))
            if core:
                sig_count_idx[core] = r.to_dict()

    exact_idx = {}
    if not exact_pair.empty and {"core_str", "StreamKey", "member_str"}.issubset(exact_pair.columns):
        for _, r in exact_pair.iterrows():
            exact_idx[(r["core_str"], str(r["StreamKey"]).strip(), boxed_s(r["member_str"]))] = r.to_dict()

    # Compact StreamRank evidence: two directions, exact trait matches only.
    # Built with to_dict("records") instead of iterrows so Streamlit loads the matrix quickly.
    sr_idx = {}
    if not streamrank.empty and {"direction", "core_str", "StreamKey", "trait_name", "trait_value"}.issubset(streamrank.columns):
        use_cols = [c for c in ["direction", "core_str", "StreamKey", "trait_name", "trait_value", "numerator", "denominator", "rate", "baseline_rate", "lift", "support_reliability", "evidence_weight", "formula"] if c in streamrank.columns]
        for rec in streamrank[use_cols].to_dict("records"):
            k = (str(rec.get("direction", "")).strip(), norm_core(rec.get("core_str", "")), str(rec.get("StreamKey", "")).strip(), str(rec.get("trait_name", "")).strip(), str(rec.get("trait_value", "")).strip())
            if all(k):
                sr_idx[k] = rec

    # Parse affinity candidate rules so those files actually fire when applicable,
    # and retain a rule-level parse audit so wins/losses can be diagnosed later.
    aff_stream_core, aff_seed_trait, aff_stream_seed_trait = {}, {}, {}
    affinity_parse_audit = []

    def _parse_affinity_rule(rule: str) -> Dict[str, str]:
        txt = str(rule or "").strip()
        out = {"parsed_stream": "", "parsed_trait_name": "", "parsed_trait_value": "", "parse_status": "UNPARSED", "parse_error": ""}
        if not txt:
            out["parse_error"] = "EMPTY_RULE"
            return out
        left = txt.split("->", 1)[0].strip()
        parts = [x.strip() for x in re.split(r"\s+AND\s+", left) if str(x).strip()]
        trait_pairs = []
        for part in parts:
            if "==" not in part:
                continue
            k, v = part.split("==", 1)
            k = k.strip()
            v = v.strip()
            if k == "StreamKey":
                out["parsed_stream"] = v
            else:
                trait_pairs.append((k, v))
        if trait_pairs:
            out["parsed_trait_name"] = trait_pairs[0][0]
            out["parsed_trait_value"] = trait_pairs[0][1]
        out["parse_status"] = "PARSED"
        return out

    if not affinity.empty and {"core_str", "rule_type", "rule"}.issubset(affinity.columns):
        for i, r in affinity.reset_index(drop=True).iterrows():
            core = norm_core(r.get("core_str", ""))
            rt = str(r.get("rule_type", "")).strip()
            rule = str(r.get("rule", ""))
            candidate_rank = str(r.get("candidate_rank", i + 1)).strip()
            rule_id = f"AFF_{core}_{candidate_rank}_{rt}" if core else f"AFF_ROW_{i+1}_{rt}"
            parsed = _parse_affinity_rule(rule)
            rec = r.to_dict()
            rec.update(parsed)
            rec["affinity_rule_id"] = rule_id
            rec["core_str"] = core
            rec["rule_type"] = rt
            parse_ok = False
            parse_error = ""
            if not core:
                parse_error = "MISSING_CORE"
            elif not rule:
                parse_error = "MISSING_RULE_TEXT"
            elif rt == "stream_core":
                if parsed.get("parsed_stream"):
                    aff_stream_core[(core, parsed["parsed_stream"])] = rec
                    parse_ok = True
                else:
                    parse_error = "STREAM_CORE_RULE_MISSING_STREAM"
            elif rt == "seed_trait_core":
                if parsed.get("parsed_trait_name") and parsed.get("parsed_trait_value"):
                    aff_seed_trait[(core, parsed["parsed_trait_name"], parsed["parsed_trait_value"])] = rec
                    parse_ok = True
                else:
                    parse_error = "SEED_TRAIT_RULE_MISSING_TRAIT"
            elif rt == "stream_seed_trait_core":
                if parsed.get("parsed_stream") and parsed.get("parsed_trait_name") and parsed.get("parsed_trait_value"):
                    aff_stream_seed_trait[(core, parsed["parsed_stream"], parsed["parsed_trait_name"], parsed["parsed_trait_value"])] = rec
                    parse_ok = True
                else:
                    parse_error = "STREAM_SEED_TRAIT_RULE_MISSING_STREAM_OR_TRAIT"
            else:
                parse_error = f"UNKNOWN_RULE_TYPE_{rt}"
            rec["parse_status"] = "PARSED_AND_INDEXED" if parse_ok else "PARSE_FAILED"
            rec["parse_error"] = "" if parse_ok else parse_error
            affinity_parse_audit.append({
                "affinity_rule_id": rule_id,
                "core_str": core,
                "candidate_rank": candidate_rank,
                "rule_type": rt,
                "rule": rule,
                "parsed_stream": parsed.get("parsed_stream", ""),
                "parsed_trait_name": parsed.get("parsed_trait_name", ""),
                "parsed_trait_value": parsed.get("parsed_trait_value", ""),
                "parse_status": rec["parse_status"],
                "parse_error": rec["parse_error"],
                "sample_size": r.get("sample_size", ""),
                "hit_count": r.get("hit_count", ""),
                "hit_rate_pct": r.get("hit_rate_pct", ""),
                "baseline_rate_pct": r.get("baseline_rate_pct", ""),
                "relative_lift_x": r.get("relative_lift_x", ""),
                "confidence_score": r.get("confidence_score", ""),
                "meets_signal_floor": r.get("meets_signal_floor", ""),
            })


    # Guarded C120 trap/member replacement integration. These files are daily/static
    # outputs, so the app uses them only on exact PLAY_DATE + HISTORY_THROUGH + stream + seed + core + member matches.
    def _c120_key(r):
        return (
            date_label(r.get("PLAY_DATE", r.get("draw_date", ""))),
            date_label(r.get("HISTORY_THROUGH", "")),
            str(r.get("stream", "")).strip(),
            norm4(r.get("seed", r.get("prior_result_used_as_seed", ""))),
            norm_core(r.get("target_core", r.get("core", ""))),
            boxed_s(r.get("candidate_member", r.get("member", ""))),
        )

    c120_member_idx = {}
    c120_source_counts = []
    for src_name, df_src in [
        ("C120_TRAP_MEMBER_CANDIDATES", c120_members),
        ("C120_TOP80_COMBINED", c120_top80),
        ("C120_MUST_PLAY_40", c120_must40),
        ("C120_EXPAND_TO_80", c120_expand80),
    ]:
        hit_count = 0
        if not df_src.empty:
            for _, r in df_src.iterrows():
                k = _c120_key(r)
                if not all(k):
                    continue
                row = r.to_dict()
                row["c120_source_file"] = src_name
                old = c120_member_idx.get(k)
                if old is None or to_num(row.get("member_trap_score", row.get("existing_final_member_score", 0))) > to_num(old.get("member_trap_score", old.get("existing_final_member_score", 0))):
                    c120_member_idx[k] = row
                hit_count += 1
        c120_source_counts.append({"family": src_name, "indexed_rows": hit_count})

    c120_core_idx = {}
    if not c120_core_locations.empty:
        for _, r in c120_core_locations.iterrows():
            k = (
                date_label(r.get("PLAY_DATE", r.get("draw_date", ""))),
                date_label(r.get("HISTORY_THROUGH", "")),
                str(r.get("stream", "")).strip(),
                norm4(r.get("seed", r.get("prior_result_used_as_seed", ""))),
                norm_core(r.get("target_core", r.get("core", ""))),
            )
            if all(k):
                old = c120_core_idx.get(k)
                row = r.to_dict()
                if old is None or to_num(row.get("trap_priority_score", row.get("final_stream_core_score", 0))) > to_num(old.get("trap_priority_score", old.get("final_stream_core_score", 0))):
                    c120_core_idx[k] = row

    c120_guard = {
        "member_rows_indexed": len(c120_member_idx),
        "core_rows_indexed": len(c120_core_idx),
        "leakage_status": str(c120_leakage.iloc[0].get("status", "UNKNOWN")) if not c120_leakage.empty else "NOT_PROVIDED",
        "run_summary_rows": int(len(c120_run_summary)) if not c120_run_summary.empty else 0,
        "source_counts": c120_source_counts,
    }
    return {
        "status": status,
        "missing_required": missing_required,
        "sc_idx": sc_idx,
        "st_idx": st_idx,
        "sst_idx": sst_idx,
        "top_sc_idx": top_sc_idx,
        "top_st_idx": top_st_idx,
        "top_sst_idx": top_sst_idx,
        "cad_idx": cad_idx,
        "mr_idx": mr_idx,
        "cp_idx": cp_idx,
        "sig_count_idx": sig_count_idx,
        "exact_idx": exact_idx,
        "sr_idx": sr_idx,
        "c120_member_idx": c120_member_idx,
        "c120_core_idx": c120_core_idx,
        "c120_guard": c120_guard,
        "aff_stream_core": aff_stream_core,
        "aff_seed_trait": aff_seed_trait,
        "aff_stream_seed_trait": aff_stream_seed_trait,
        "affinity_parse_audit": pd.DataFrame(affinity_parse_audit),
    }

def build_core_dates(history_df: pd.DataFrame) -> Dict[str, List[pd.Timestamp]]:
    h = history_df.copy()
    h["date"] = pd.to_datetime(h["Date"], errors="coerce")
    h["result4"] = h["Result4"].map(norm4)
    h["winner_core"] = h["result4"].map(core_from_result)
    h = h[(h["winner_core"] != "") & h["date"].notna()]
    return {core: sorted(g["date"].tolist()) for core, g in h.groupby("winner_core")}


def build_rolling_mirror_counters(history_df: pd.DataFrame, play_date: str, excluded_states: List[str]) -> Tuple[np.ndarray, np.ndarray, pd.DataFrame]:
    members, member_idx, member_core, watched_members, all_vsets, vcode, seed_vcode_arr, mirror_mat = build_universe()
    cnt_mirror = np.zeros((len(all_vsets), 5), dtype=np.int32)
    gmirror = np.zeros(5, dtype=np.int32)
    h = history_df.copy()
    if h.empty:
        return cnt_mirror, gmirror, pd.DataFrame()
    h["date"] = pd.to_datetime(h["Date"], errors="coerce")
    h["stream"] = h["StreamKey"].astype(str)
    h["result4"] = h["Result4"].map(norm4)
    h = h.dropna(subset=["date"]).sort_values(["stream", "date"])
    trans_rows = []
    for stream, g in h.groupby("stream", sort=False):
        prev = None
        for r in g.itertuples(index=False):
            cur = r.result4
            if prev:
                wb = boxed_s(cur)
                a = is_aabc_member(wb)
                seed_int = int(prev)
                trans_rows.append({
                    "date": r.date,
                    "stream": stream,
                    "seed": prev,
                    "result4": cur,
                    "actual_member": wb if a else "",
                    "actual_core": core_from_member(wb) if a else "",
                    "is_aabc_winner": a,
                    "seed_vcode": int(seed_vcode_arr[seed_int]),
                    "mirror_hits": int(mirror_mat[seed_int, member_idx[wb]]) if a and wb in member_idx else -1,
                    "nonplayable_stream": is_nonplayable_stream(stream, excluded_states),
                })
            prev = cur
    trans = pd.DataFrame(trans_rows).sort_values("date") if trans_rows else pd.DataFrame()
    if not trans.empty:
        cutoff = pd.Timestamp(play_date)
        for _, row in trans[trans["date"] < cutoff].iterrows():
            if bool(row["is_aabc_winner"]) and 0 <= int(row["mirror_hits"]) <= 4:
                cnt_mirror[int(row["seed_vcode"]), int(row["mirror_hits"])] += 1
                gmirror[int(row["mirror_hits"])] += 1
    return cnt_mirror, gmirror, trans


def top_indices(arr: np.ndarray, k: int, fallback: np.ndarray) -> np.ndarray:
    if k <= 0:
        return np.array([], dtype=np.int16)
    src = arr if arr.sum() > 0 else fallback
    idx = np.argsort(-src)[:k]
    return idx[src[idx] > 0]

# ----------------------------- scoring -----------------------------


def digital_root(n: int) -> int:
    return 0 if n == 0 else 1 + ((n - 1) % 9)


def seed_traits(seed: str, play_date: str) -> Dict[str, str]:
    """Seed traits aligned to both older V6 files and the V2 StreamRank matrix.

    The matrix verification confirmed seed=current previous same-stream result and winner=next result;
    these traits are all computed from that prior seed only.
    """
    s = norm4(seed)
    ds = [int(c) for c in s]
    vals: Dict[str, str] = {}
    if not s:
        return vals
    total = sum(ds)
    outer = ds[0] + ds[3]
    inner = ds[1] + ds[2]
    counts = Counter(s)
    count_values = sorted(counts.values(), reverse=True)
    if count_values == [4]:
        repeat_type = "quad"
        structure = "double_double_or_triple"
    elif count_values == [3, 1]:
        repeat_type = "triple"
        structure = "double_double_or_triple"
    elif count_values == [2, 2]:
        repeat_type = "double_double"
        structure = "double_double_or_triple"
    elif count_values == [2, 1, 1]:
        repeat_type = "double"
        structure = "double"
    else:
        repeat_type = "single"
        structure = "single"
    parity_pattern = "".join("E" if d % 2 == 0 else "O" for d in ds)
    hl_pattern = "".join("H" if d >= 5 else "L" for d in ds)
    unique_count = len(set(ds))
    spread = max(ds) - min(ds)
    mirror_pairs_count = sum(1 for d in set(ds) if ((d + 5) % 10) in ds) // 2
    consec_links = sum(1 for a, b in zip(ds, ds[1:]) if abs(a - b) == 1)
    ascending_pairs = sum(1 for a, b in zip(ds, ds[1:]) if b == a + 1)
    descending_pairs = sum(1 for a, b in zip(ds, ds[1:]) if b == a - 1)
    same_adjacent_pairs = sum(1 for a, b in zip(ds, ds[1:]) if b == a)

    # V2 matrix exact names.
    vals.update({
        "seed_norm": s,
        "seed_sum": str(total),
        "seed_root": str(digital_root(total)),
        "structure": structure,
        "repeat_type": repeat_type,
        "unique_count": str(unique_count),
        "has_repeat": "1" if repeat_type != "single" else "0",
        "spread": str(spread),
        "consec_links": str(consec_links),
        "ascending_pairs": str(ascending_pairs),
        "descending_pairs": str(descending_pairs),
        "same_adjacent_pairs": str(same_adjacent_pairs),
        "mirror_pairs_count": str(mirror_pairs_count),
        "has_mirror_pair": "1" if mirror_pairs_count > 0 else "0",
        "even_count": str(sum(d % 2 == 0 for d in ds)),
        "odd_count": str(sum(d % 2 == 1 for d in ds)),
        "parity_pattern": parity_pattern,
        "hl_pattern": hl_pattern,
        "hl_count_H": str(sum(d >= 5 for d in ds)),
        "hl_count_L": str(sum(d < 5 for d in ds)),
        "first_digit": str(ds[0]),
        "last_digit": str(ds[3]),
        "min_digit": str(min(ds)),
        "max_digit": str(max(ds)),
        "max_digit_count": str(max(counts.values())),
        "outer_sum": str(outer),
        "inner_sum": str(inner),
        "pair12": f"{ds[0]}{ds[1]}",
        "pair13": f"{ds[0]}{ds[2]}",
        "pair14": f"{ds[0]}{ds[3]}",
        "pair23": f"{ds[1]}{ds[2]}",
        "pair24": f"{ds[1]}{ds[3]}",
        "pair34": f"{ds[2]}{ds[3]}",
        "triplet123": f"{ds[0]}{ds[1]}{ds[2]}",
        "triplet124": f"{ds[0]}{ds[1]}{ds[3]}",
        "triplet134": f"{ds[0]}{ds[2]}{ds[3]}",
        "triplet234": f"{ds[1]}{ds[2]}{ds[3]}",
    })
    for i, d in enumerate(ds, start=1):
        vals[f"pos{i}"] = str(d)
        vals[f"pos{i}_parity"] = "E" if d % 2 == 0 else "O"
        vals[f"pos{i}_HL"] = "H" if d >= 5 else "L"
        vals[f"pos{i}_mod3"] = str(d % 3)
        vals[f"pos{i}_mod5"] = str(d % 5)
    for name, val in [("pos12_sum", ds[0]+ds[1]), ("pos13_sum", ds[0]+ds[2]), ("pos14_sum", ds[0]+ds[3]), ("pos23_sum", ds[1]+ds[2]), ("pos24_sum", ds[1]+ds[3]), ("pos34_sum", ds[2]+ds[3])]:
        vals[name] = str(val)
    for mod in range(2, 13):
        vals[f"sum_mod_{mod}"] = str(total % mod)
        vals[f"outer_sum_mod_{mod}"] = str(outer % mod)
        vals[f"inner_sum_mod_{mod}"] = str(inner % mod)
    for d in range(10):
        vals[f"has_{d}"] = "1" if str(d) in s else "0"
        vals[f"count_{d}"] = str(s.count(str(d)))

    # Older V6 profile names/synonyms.
    vals["seed_sorted"] = "".join(sorted(s))
    vals["seed_digit_family"] = "".join(sorted(set(s)))
    vals["seed_shape"] = "quad" if repeat_type == "quad" else "triple" if repeat_type == "triple" else "double_double" if repeat_type == "double_double" else "one_pair" if repeat_type == "double" else "all_unique"
    vals["seed_sum_mod5"] = str(total % 5)
    vals["seed_sum_end"] = str(total % 10)
    vals["seed_sum_bucket"] = "sum_00_09" if total <= 9 else "sum_10_13" if total <= 13 else "sum_14_17" if total <= 17 else "sum_18_21" if total <= 21 else "sum_22_plus"
    vals["seed_high_count"] = vals["hl_count_H"]
    vals["seed_low_count"] = vals["hl_count_L"]
    vals["seed_even_count"] = vals["even_count"]
    vals["seed_odd_count"] = vals["odd_count"]
    vals["seed_parity"] = parity_pattern
    vals["seed_highlow"] = hl_pattern
    vals["seed_spread"] = str(spread)
    vals["seed_spread_bucket"] = "spread_0_2" if spread <= 2 else "spread_3_4" if spread <= 4 else "spread_5_6" if spread <= 6 else "spread_7_plus"
    vals["seed_consec_links"] = str(consec_links)
    vals["seed_mirror_pairs"] = str(mirror_pairs_count)
    dt = pd.to_datetime(play_date, errors="coerce")
    if pd.isna(dt):
        dt = pd.Timestamp.today()
    vals["DayOfWeek"] = dt.day_name()
    vals["Month"] = f"{dt.month:02d}"
    vals["IsWeekend"] = "True" if dt.dayofweek >= 5 else "False"
    for d in range(10):
        vals[f"seed_has{d}"] = "True" if str(d) in s else "False"
    return vals


def weighted_signal(row: Dict, kind: str) -> float:
    if not row:
        return 0.0
    conf = to_num(row.get("confidence_score"))
    lift = max(0.0, to_num(row.get("relative_lift_x"), 1.0) - 1.0)
    hits = to_num(row.get("hit_count"))
    sample = to_num(row.get("sample_size"))
    support = min(1.0, math.sqrt(max(hits, 0) / 5.0)) if hits > 0 else 0.0
    sample_factor = 1.0 if sample >= 250 else 0.85 if sample >= 100 else 0.65 if sample >= 50 else 0.45
    kind_mult = {
        "stream_core": 1.0,
        "top_stream_core": 0.65,
        "seed_trait": 0.75,
        "top_seed_trait": 0.45,
        "stream_seed_trait": 0.90,
        "top_stream_seed_trait": 0.55,
        "cadence": 0.35,
        "affinity": 0.40,
    }.get(kind, 0.5)
    return (conf * 1000 + lift * 0.35 + min(hits, 50) * 0.03) * support * sample_factor * kind_mult


def weighted_streamrank_signal(row: Dict, kind: str) -> float:
    if not row:
        return 0.0
    ew = to_num(row.get("evidence_weight"), 0.0)
    lift = to_num(row.get("lift"), 1.0)
    numerator = to_num(row.get("numerator"), 0.0)
    support = to_num(row.get("support_reliability"), 0.0)
    # Use both positive and negative matrix evidence. Negative evidence can suppress a row.
    direction_mult = 0.55 if str(row.get("direction", "")).upper() == "STREAM_TO_CORE" else 0.45
    kind_mult = {"streamrank_core_to_stream": 1.0, "streamrank_stream_to_core": 1.0}.get(kind, 1.0)
    return (ew * 22.0 + (lift - 1.0) * 1.5 + min(numerator, 20) * 0.08) * max(0.25, support) * direction_mult * kind_mult

def gap_bucket(core: str, play_date: str, core_dates_all: Dict[str, List[pd.Timestamp]]) -> str:
    dt = pd.to_datetime(play_date, errors="coerce")
    if pd.isna(dt):
        return "first_seen"
    dates = core_dates_all.get(core, [])
    prior = [d for d in dates if d < dt]
    if not prior:
        return "first_seen"
    gap = int((dt - max(prior)).days)
    if gap <= 7:
        return "gap_001_007"
    if gap <= 14:
        return "gap_008_014"
    if gap <= 30:
        return "gap_015_030"
    if gap <= 60:
        return "gap_031_060"
    if gap <= 120:
        return "gap_061_120"
    return "gap_121_plus"



def score_rows(rows: pd.DataFrame, play_date: str, history_df: pd.DataFrame, c120_dynamic_bundle: Optional[Dict] = None) -> pd.DataFrame:
    """Score each remaining candidate as one overall best play.

    This is the only ranking authority for Step 4. It is not stream-only,
    core-only, or member-only. Rows are later cut from the bottom of
    profile_final_member_score only.
    """
    if rows.empty:
        return pd.DataFrame()
    profiles = apply_c120_dynamic_to_profiles(load_profiles(), c120_dynamic_bundle)
    if profiles.get("missing_required"):
        raise RuntimeError("Missing required scoring profile files: " + ", ".join(profiles["missing_required"]))
    core_dates_all = build_core_dates(history_df)
    out = []
    for _, r in rows.iterrows():
        core = norm_core(r.get("core", ""))
        stream = str(r.get("stream", "")).strip()
        seed = norm4(r.get("seed", ""))
        mem = boxed_s(r.get("boxed_member", ""))
        traits = seed_traits(seed, play_date)
        fire = []

        # Existing V6 usable + TOP signal layers.
        score_stream_core_usable = weighted_signal(profiles["sc_idx"].get((core, stream), {}), "stream_core")
        if score_stream_core_usable: fire.append("usable_stream_core")
        score_stream_core_top = weighted_signal(profiles["top_sc_idx"].get((core, stream), {}), "top_stream_core")
        if score_stream_core_top: fire.append("top_stream_core")

        seed_scores = []
        top_seed_scores = []
        stream_seed_scores = []
        top_stream_seed_scores = []
        streamrank_core_to_stream_scores = []
        streamrank_stream_to_core_scores = []
        aff_seed_scores = []
        aff_stream_seed_scores = []
        matched_traits = []
        affinity_rule_ids = []
        affinity_rule_details = []
        for tn, tv in traits.items():
            row = profiles["st_idx"].get((core, tn, str(tv)))
            s = weighted_signal(row, "seed_trait")
            if s:
                seed_scores.append(s); matched_traits.append(f"usable_seed:{tn}={tv}")
            row = profiles["top_st_idx"].get((core, tn, str(tv)))
            s = weighted_signal(row, "top_seed_trait")
            if s:
                top_seed_scores.append(s); matched_traits.append(f"top_seed:{tn}={tv}")
            row = profiles["sst_idx"].get((core, stream, tn, str(tv)))
            s = weighted_signal(row, "stream_seed_trait")
            if s:
                stream_seed_scores.append(s); matched_traits.append(f"usable_stream_seed:{tn}={tv}")
            row = profiles["top_sst_idx"].get((core, stream, tn, str(tv)))
            s = weighted_signal(row, "top_stream_seed_trait")
            if s:
                top_stream_seed_scores.append(s); matched_traits.append(f"top_stream_seed:{tn}={tv}")

            row = profiles["sr_idx"].get(("CORE_TO_STREAM", core, stream, tn, str(tv)))
            s = weighted_streamrank_signal(row, "streamrank_core_to_stream")
            if s:
                streamrank_core_to_stream_scores.append(s); matched_traits.append(f"sr_core_to_stream:{tn}={tv}")
            row = profiles["sr_idx"].get(("STREAM_TO_CORE", core, stream, tn, str(tv)))
            s = weighted_streamrank_signal(row, "streamrank_stream_to_core")
            if s:
                streamrank_stream_to_core_scores.append(s); matched_traits.append(f"sr_stream_to_core:{tn}={tv}")

            row = profiles["aff_seed_trait"].get((core, tn, str(tv)))
            s = weighted_signal(row, "affinity")
            if s:
                rid = str(row.get("affinity_rule_id", f"aff_seed:{tn}={tv}"))
                aff_seed_scores.append(s); matched_traits.append(f"aff_seed:{tn}={tv}:{rid}")
                affinity_rule_ids.append(rid); affinity_rule_details.append(f"{rid}|seed_trait|{tn}={tv}|score={round(s,4)}")
            row = profiles["aff_stream_seed_trait"].get((core, stream, tn, str(tv)))
            s = weighted_signal(row, "affinity")
            if s:
                rid = str(row.get("affinity_rule_id", f"aff_stream_seed:{tn}={tv}"))
                aff_stream_seed_scores.append(s); matched_traits.append(f"aff_stream_seed:{tn}={tv}:{rid}")
                affinity_rule_ids.append(rid); affinity_rule_details.append(f"{rid}|stream_seed_trait|{stream}|{tn}={tv}|score={round(s,4)}")

        # Cap each family so one dense trait family does not swamp the single overall ranking.
        score_seed_trait_usable = sum(sorted(seed_scores, reverse=True)[:3])
        score_seed_trait_top = sum(sorted(top_seed_scores, reverse=True)[:2])
        score_stream_seed_trait_usable = sum(sorted(stream_seed_scores, reverse=True)[:2])
        score_stream_seed_trait_top = sum(sorted(top_stream_seed_scores, reverse=True)[:2])
        score_streamrank_core_to_stream = sum(sorted(streamrank_core_to_stream_scores, reverse=True)[:6])
        score_streamrank_stream_to_core = sum(sorted(streamrank_stream_to_core_scores, reverse=True)[:6])
        score_affinity_seed = sum(sorted(aff_seed_scores, reverse=True)[:2])
        score_affinity_stream_seed = sum(sorted(aff_stream_seed_scores, reverse=True)[:2])
        if score_seed_trait_usable: fire.append("usable_seed_trait")
        if score_seed_trait_top: fire.append("top_seed_trait")
        if score_stream_seed_trait_usable: fire.append("usable_stream_seed_trait")
        if score_stream_seed_trait_top: fire.append("top_stream_seed_trait")
        if score_streamrank_core_to_stream: fire.append("streamrank_core_to_stream")
        if score_streamrank_stream_to_core: fire.append("streamrank_stream_to_core")
        if score_affinity_seed or score_affinity_stream_seed: fire.append("affinity_rules")

        gb = gap_bucket(core, play_date, core_dates_all)
        score_cadence = weighted_signal(profiles["cad_idx"].get((core, gb), {}), "cadence")
        if score_cadence: fire.append("cadence")

        aff_sc_row = profiles["aff_stream_core"].get((core, stream), {})
        aff_sc = weighted_signal(aff_sc_row, "affinity")
        if aff_sc:
            fire.append("affinity_stream_core")
            rid = str(aff_sc_row.get("affinity_rule_id", f"aff_stream_core:{stream}"))
            affinity_rule_ids.append(rid); affinity_rule_details.append(f"{rid}|stream_core|{stream}|score={round(aff_sc,4)}")

        cp = profiles["cp_idx"].get(core, {})
        core_prior = math.log1p(to_num(cp.get("core_hits"))) * 0.08 + to_num(cp.get("dominance_score")) * 0.08

        sigc = profiles.get("sig_count_idx", {}).get(core, {})
        score_signal_count_summary = 0.0
        signal_count_member_role = ""
        if sigc:
            # Material use of V6_8CORE_SIGNAL_COUNT_SUMMARY: it acts as a capped reliability/depth prior.
            signal_depth = (
                to_num(sigc.get("stream_core_primary_signals")) * 0.08 +
                to_num(sigc.get("stream_core_tiebreaker_signals")) * 0.025 +
                to_num(sigc.get("broad_seed_signals")) * 0.045 +
                to_num(sigc.get("seed_tiebreak_signals")) * 0.035 +
                to_num(sigc.get("narrow_stream_seed_signals")) * 0.04 +
                to_num(sigc.get("cadence_signals")) * 0.035
            )
            frequency_rank = max(1.0, to_num(sigc.get("frequency_rank_120"), 120))
            dominance_rank = max(1.0, to_num(sigc.get("dominance_rank_120"), 120))
            rank_quality = ((121.0 - min(frequency_rank, 120.0)) + (121.0 - min(dominance_rank, 120.0))) / 240.0
            score_signal_count_summary = min(4.0, signal_depth * 0.12 + rank_quality * 1.25)
            if mem == boxed_s(sigc.get("strongest_member", "")):
                score_signal_count_summary += 1.00; signal_count_member_role = "strongest_member"
            elif mem == boxed_s(sigc.get("middle_member", "")):
                score_signal_count_summary += 0.45; signal_count_member_role = "middle_member"
            elif mem == boxed_s(sigc.get("suppressed_member", "")):
                score_signal_count_summary -= 0.10; signal_count_member_role = "suppressed_member"
            fire.append("signal_count_summary")

        mr = profiles["mr_idx"].get((core, mem), {})
        share = to_num(mr.get("member_share_pct"), 33.333)
        mhits = to_num(mr.get("member_hits"), 0)
        score_member_role = (share - 33.333) * 0.15 + math.log1p(mhits) * 0.08
        if mr: fire.append("member_role")

        exact = profiles["exact_idx"].get((core, stream, mem), {})
        score_exact_pair = to_num(exact.get("exact_pair_score"), 0.0)
        exact_opp = to_num(exact.get("opportunity_count"), 0)
        exact_hits = to_num(exact.get("exact_member_hits", 0))
        if exact:
            fire.append("exact_stream_core_member")

        c120_key = (date_label(play_date), date_label(r.get("seed_date", "")), stream, seed, core, mem)
        c120_member = profiles.get("c120_member_idx", {}).get(c120_key, {})
        c120_core = profiles.get("c120_core_idx", {}).get(c120_key[:-1], {})
        score_c120_core_matrix = 0.0
        score_c120_member_matrix = 0.0
        score_c120_replacement_matrix = 0.0
        c120_lane = ""
        c120_source_file = ""
        if c120_core:
            score_c120_core_matrix = min(8.0, to_num(c120_core.get("final_stream_core_score", c120_core.get("core_score", 0))) * 0.035)
            score_c120_core_matrix += max(0.0, 125.0 - to_num(c120_core.get("matrix_rank_in_stream", 125))) * 0.015
            score_c120_core_matrix += to_num(c120_core.get("rule_count")) * 0.05
            c120_lane = str(c120_core.get("trap_lane", ""))
            fire.append("c120_core_rule_matrix")
        if c120_member:
            score_c120_member_matrix = min(9.0, to_num(c120_member.get("final_member_score", c120_member.get("existing_final_member_score_num", c120_member.get("existing_final_member_score", 0)))) * 0.09)
            score_c120_replacement_matrix = min(7.0, to_num(c120_member.get("member_trap_score", 0)) * 0.006 + to_num(c120_member.get("member_soft_score", 0)) * 0.9)
            c120_lane = c120_lane or str(c120_member.get("trap_lane", ""))
            c120_source_file = str(c120_member.get("c120_source_file", ""))
            fire.append("c120_member_replacement_matrix")

        # One global best-play score. Negative StreamRank evidence is included above.
        profile_signal_score_only = (
            score_stream_core_usable + score_stream_core_top +
            score_seed_trait_usable + score_seed_trait_top +
            score_stream_seed_trait_usable + score_stream_seed_trait_top +
            score_streamrank_core_to_stream + score_streamrank_stream_to_core +
            score_cadence + score_affinity_seed + score_affinity_stream_seed + aff_sc +
            score_signal_count_summary + score_c120_core_matrix
        )
        profile_core_score = profile_signal_score_only + core_prior
        # Exact pair is member-specific and now required, but low-support exact pairs are allowed to contribute small/zero evidence.
        profile_final_member_score = profile_core_score + score_member_role + score_exact_pair + score_c120_member_matrix + score_c120_replacement_matrix
        d = r.to_dict()
        d.update({
            "score_stream_core_usable": score_stream_core_usable,
            "score_stream_core_top": score_stream_core_top,
            "score_seed_trait_usable": score_seed_trait_usable,
            "score_seed_trait_top": score_seed_trait_top,
            "score_stream_seed_trait_usable": score_stream_seed_trait_usable,
            "score_stream_seed_trait_top": score_stream_seed_trait_top,
            "score_streamrank_core_to_stream": score_streamrank_core_to_stream,
            "score_streamrank_stream_to_core": score_streamrank_stream_to_core,
            "score_affinity_seed": score_affinity_seed,
            "score_affinity_stream_seed": score_affinity_stream_seed,
            "score_affinity_stream_core": aff_sc,
            "score_cadence": score_cadence,
            "score_signal_count_summary": score_signal_count_summary,
            "signal_count_member_role": signal_count_member_role,
            "score_core_prior": core_prior,
            "score_c120_core_matrix": score_c120_core_matrix,
            "score_c120_member_matrix": score_c120_member_matrix,
            "score_c120_replacement_matrix": score_c120_replacement_matrix,
            "c120_trap_lane": c120_lane,
            "c120_source_file": c120_source_file,
            "score_exact_stream_core_member": score_exact_pair,
            "exact_pair_opportunity_count": exact_opp,
            "exact_pair_hits": exact_hits,
            "exact_pair_hit_rate_pct": to_num(exact.get("exact_hit_rate_pct"), 0.0),
            "exact_pair_sample_tier": str(exact.get("sample_tier", "MISSING")) if exact else "MISSING",
            "profile_signal_score_only": profile_signal_score_only,
            "profile_core_score": profile_core_score,
            "profile_member_role_share_pct": share,
            "profile_member_role_hits": mhits,
            "profile_member_role_score": score_member_role,
            "profile_final_member_score": profile_final_member_score,
            "profile_gap_bucket": gb,
            "score_files_fired": ";".join(sorted(set(fire))) if fire else "NO_PROFILE_RULE_FIRED",
            "score_trait_hits": " | ".join(matched_traits[:40]),
            "affinity_rule_ids_fired": ";".join(sorted(set([x for x in affinity_rule_ids if x]))),
            "affinity_rule_fire_details": " | ".join(affinity_rule_details[:60]),
            "scoring_ready": True,
            "scoring_warning": "" if fire else "NO_PROFILE_RULE_FIRED_FOR_ROW",
        })
        out.append(d)
    df = pd.DataFrame(out)
    if df.empty:
        return df
    df = df.sort_values(["profile_final_member_score", "profile_signal_score_only", "stream", "core", "boxed_member"], ascending=[False, False, True, True, True], kind="mergesort").reset_index(drop=True)
    df["overall_play_rank"] = range(1, len(df) + 1)
    df["playlist_rank_profile_final"] = df["overall_play_rank"]
    for col in ["profile_final_member_score", "profile_signal_score_only", "profile_core_score", "profile_member_role_score", "score_exact_stream_core_member"]:
        if col in df.columns:
            df[f"global_rank_{col}"] = df[col].rank(method="first", ascending=False).astype(int)
            df[f"rank_within_core_{col}"] = df.groupby("core")[col].rank(method="first", ascending=False).astype(int)
            df[f"rank_within_stream_{col}"] = df.groupby("stream")[col].rank(method="first", ascending=False).astype(int)
            df[f"rank_within_stream_core_{col}"] = df.groupby(["stream", "core"])[col].rank(method="first", ascending=False).astype(int)
    return df

# ----------------------------- pipeline steps -----------------------------

def stage_record(label: str, df: pd.DataFrame, note: str = "", selection_rule: str = "") -> Dict:
    if df is None:
        df = pd.DataFrame()
    return {
        "label": label,
        "note": note,
        "selection_rule": selection_rule,
        "rows": df.copy(),
    }


def stage_metrics(df: pd.DataFrame) -> Dict[str, int]:
    if df is None or df.empty:
        return {"Rows": 0, "Streams": 0, "Cores": 0, "Members": 0}
    return {
        "Rows": int(len(df)),
        "Streams": int(df["stream"].nunique()) if "stream" in df.columns else 0,
        "Cores": int(df["core"].nunique()) if "core" in df.columns else 0,
        "Members": int(df["boxed_member"].nunique()) if "boxed_member" in df.columns else 0,
    }


def add_branch_label(df: pd.DataFrame, label: str) -> pd.DataFrame:
    out = df.copy()
    if "branch_name" in out.columns:
        out["branch_name"] = label
    else:
        out.insert(0, "branch_name", label)
    if "profile_final_member_score" in out.columns:
        out["playlist_rank_profile_final"] = out["profile_final_member_score"].rank(method="first", ascending=False).astype(int)
    return out


def run_initial_pipeline(seed_df: pd.DataFrame, play_date: str, seed_date: str, history_df: pd.DataFrame, excluded_states: List[str], step3_mode: str, progress=None) -> Tuple[List[Dict], pd.DataFrame, pd.DataFrame]:
    members, member_idx, member_core, watched_members, all_vsets, vcode, seed_vcode_arr, mirror_mat = build_universe()
    cnt_mirror, gmirror, trans = build_rolling_mirror_counters(history_df, play_date, excluded_states)
    stages: List[Dict] = []

    if progress:
        progress.progress(5, text="Step 0: parsing playable streams")
    seeds = seed_df.copy()
    seeds["stream"] = seeds["StreamKey"].astype(str)
    seeds["seed"] = seeds["Result4"].map(norm4)
    seeds["nonplayable_excluded_step0"] = seeds["stream"].map(lambda s: is_nonplayable_stream(s, excluded_states))
    playable = seeds[~seeds["nonplayable_excluded_step0"]].drop_duplicates(["stream"], keep="last").copy()
    stages.append(stage_record("STEP0_PLAYABLE_SEEDS", playable.rename(columns={"Result4": "seed"}), "Non-playable streams excluded before row generation", "Step 0"))

    if progress:
        progress.progress(20, text="Step 1: mirror-only FULL120 member qualification")
    stream_context = []
    step1_rows = []
    for _, r in playable.iterrows():
        seed = norm4(r["seed"])
        if not seed:
            continue
        seed_int = int(seed)
        sv = int(seed_vcode_arr[seed_int])
        allowed = top_indices(cnt_mirror[sv], 2, gmirror).tolist()
        if not allowed:
            allowed = list(np.argsort(-gmirror)[:2]) if gmirror.sum() > 0 else [1, 0]
        mirror_counts = mirror_mat[seed_int]
        q_count = int(np.isin(mirror_counts, allowed).sum())
        stream_context.append({"play_date": play_date, "seed_date": seed_date, "stream": r["stream"], "seed": seed, "mirror_qualified_count": q_count, "allowed_mirror_hits": "|".join(map(str, allowed))})
        # Step 1 must start with the full AABC universe:
        # all 360 boxed AABC members / all 120 cores.
        # Watched-core restriction is applied later in Step 3.
        for m in members:
            mi = member_idx[m]
            if int(mirror_counts[mi]) in allowed:
                step1_rows.append({
                    "play_date": play_date,
                    "seed_date": seed_date,
                    "stream": r["stream"],
                    "seed": seed,
                    "boxed_member": m,
                    "core": member_core[m],
                    "mirror_qualified_count": q_count,
                    "actual_mirror_hits_for_member": int(mirror_counts[mi]),
                    "allowed_mirror_hits": "|".join(map(str, allowed)),
                })
    ctx = pd.DataFrame(stream_context)
    step1 = pd.DataFrame(step1_rows)
    stages.append(stage_record("STEP1_MIRROR_ONLY", add_branch_label(step1, "STEP1_MIRROR_ONLY"), "Mirror-only FULL120; all 360 AABC members / all 120 cores; no sum/spread/VTrac hard gates", "mirror-only"))

    if progress:
        progress.progress(35, text="Step 2: mirror bucket refinement")
    step2, step2_note = build_step2_mirror_bucket(step1, ctx)
    stages.append(stage_record("STEP2_MIRROR_BUCKET_REFINEMENT", add_branch_label(step2, "STEP2_MIRROR_BUCKET_REFINEMENT"), step2_note, "MIRROR_BUCKET_REFINEMENT"))

    if progress:
        progress.progress(50, text="Step 3: historical-score-driven core handling")
    candidate_cores = sorted(c for c in WATCHED8 if c in set(step2["core"].astype(str))) if not step2.empty and "core" in step2.columns else sorted(WATCHED8)
    core_audit, step3_scored_for_audit, step3_errors = historical_step3_core_audit(step2, step1, candidate_cores, play_date, history_df)
    if step3_errors:
        raise RuntimeError("; ".join(step3_errors))
    default_remove_count = min(max(0, len(candidate_cores) - 1), len(default_dynamic_removed(build_core_audit(step2, step1, candidate_cores))[0]))
    removed, core_audit = recommend_removed_cores_historical(core_audit, default_remove_count)
    step3, core_audit = apply_step3_manual(step2, step1, candidate_cores, removed)
    hist_cols = [c for c in ["core", "historical_rows_scored", "historical_avg_score", "historical_median_score", "historical_max_score", "historical_total_score", "historical_top25_rows", "historical_no_rule_rows", "historical_strength_score", "historical_removal_rank"] if c in core_audit.columns]
    if hist_cols and not step3.empty:
        step3 = step3.merge(core_audit[hist_cols], on="core", how="left")
    stages.append(stage_record("STEP3_CORE_SELECTION", add_branch_label(step3, "STEP3_CORE_SELECTION"), "Historical-score-driven Step 3 core selection; weakest core scores removed first.", "STEP3_HISTORICAL_SCORE_DRIVEN"))

    if progress:
        progress.progress(70, text="Step 4: score/rank base")
    scored = score_rows(step3, play_date, history_df) if not step3.empty else pd.DataFrame()
    stages.append(stage_record("STEP4_SCORED_BASE", add_branch_label(scored, "STEP4_SCORED_BASE"), "Scored/ranked base; no row reduction", "score_rows"))

    # v1.9: do not run any hidden Step 5 reduction. Step 5 in the UI only exports the current Step 4 rows.
    if progress:
        progress.progress(100, text="Initial ladder built through scored Step 4 base; no hidden final reduction")
    return stages, ctx, core_audit


def apply_step3_core_choice(step2: pd.DataFrame, step1: pd.DataFrame, mode: str, selected_cores: Optional[List[str]] = None) -> Tuple[pd.DataFrame, pd.DataFrame]:
    if step2.empty:
        return pd.DataFrame(), pd.DataFrame()
    safe_counts = step2.groupby("core").agg(safe_candidate_member_rows=("boxed_member", "size"), safe_streams_touched=("stream", "nunique")).reindex(sorted(WATCHED8)).fillna(0).astype(int).reset_index()
    eliminated = step1[~step1["stream"].isin(set(step2["stream"]))].groupby("core").agg(eliminated_candidate_member_rows=("boxed_member", "size"), eliminated_streams_touched=("stream", "nunique")).reindex(sorted(WATCHED8)).fillna(0).astype(int).reset_index()
    ca = safe_counts.merge(eliminated, on="core")

    if mode == "KEEP_ALL_WATCHED8":
        keep_cores = set(sorted(WATCHED8))
        ca["dynamic_remove_before_minmax_save"] = False
        ca["saved_by_minmax_default"] = False
    elif mode == "MANUAL_CORE_SELECTION" and selected_cores is not None:
        keep_cores = set(selected_cores)
        ca["dynamic_remove_before_minmax_save"] = ~ca["core"].isin(keep_cores)
        ca["saved_by_minmax_default"] = False
    else:
        remove = set()
        initial_remove = set()
        to_save = []
        if len(ca):
            min_safe = ca["safe_candidate_member_rows"].min()
            max_safe = ca["safe_candidate_member_rows"].max()
            initial_remove.update(ca.loc[ca["safe_candidate_member_rows"].eq(min_safe), "core"].tolist())
            initial_remove.update(ca.loc[ca["safe_candidate_member_rows"].eq(max_safe), "core"].tolist())
            min_elim = ca["eliminated_candidate_member_rows"].min()
            low_elim = ca[ca["eliminated_candidate_member_rows"].eq(min_elim)].sort_values(["safe_candidate_member_rows", "core"])
            if len(low_elim):
                initial_remove.add(low_elim.iloc[0]["core"])
            remove = set(initial_remove)
            if mode == "DYNAMIC_WITH_MINMAX_SAVE":
                minmax_cores = set(ca.loc[ca["safe_candidate_member_rows"].isin([min_safe, max_safe]), "core"].tolist())
                costs = {row.core: int(row.safe_candidate_member_rows) for row in ca.itertuples(index=False)}
                save_candidates = [c for c in minmax_cores if c in remove]
                for c in save_candidates:
                    if costs.get(c, 999999) <= SAVE_AUTO_DEFAULT:
                        to_save.append(c)
                if len(to_save) >= 2 and sum(costs[c] for c in to_save) > SAVE_COMBINED_DEFAULT:
                    to_save = sorted(to_save, key=lambda c: (costs[c], c))[:1]
                if not to_save and len(save_candidates) == 1 and costs[save_candidates[0]] <= SAVE_SOLO_DEFAULT:
                    to_save = [save_candidates[0]]
                remove.difference_update(to_save)
        keep_cores = set(sorted(WATCHED8)) - remove
        ca["dynamic_remove_before_minmax_save"] = ca["core"].isin(initial_remove)
        ca["saved_by_minmax_default"] = ca["core"].isin(to_save)
    ca["STEP3_STATUS"] = ca["core"].apply(lambda c: "KEEP" if c in keep_cores else "REMOVE")
    step3 = step2[step2["core"].isin(keep_cores)].copy()
    step3 = step3.merge(ca[["core", "safe_candidate_member_rows", "safe_streams_touched", "eliminated_candidate_member_rows", "eliminated_streams_touched", "STEP3_STATUS", "saved_by_minmax_default"]], on="core", how="left")
    return step3, ca

# ----------------------------- reduction choices -----------------------------

def select_top_by_score(df: pd.DataFrame, col: str, n: int) -> pd.DataFrame:
    return df.sort_values([col, "profile_signal_score_only", "profile_final_member_score"], ascending=[False, False, False]).head(min(n, len(df))).copy()


def select_bottom_by_rank_value(df: pd.DataFrame, col: str, n: int) -> pd.DataFrame:
    # Larger rank number = lower/buried position. Used for bottom30 by within-stream-core rank.
    return df.sort_values([col, "profile_signal_score_only", "profile_final_member_score"], ascending=[False, False, False]).head(min(n, len(df))).copy()


def select_top_by_rank_value(df: pd.DataFrame, col: str, n: int) -> pd.DataFrame:
    # Smaller rank number = top. Used for optional 15 branch.
    return df.sort_values([col, "profile_signal_score_only", "profile_final_member_score"], ascending=[True, False, False]).head(min(n, len(df))).copy()


def apply_reduction(df: pd.DataFrame, mode: str, target_rows: int) -> Tuple[pd.DataFrame, str]:
    if df.empty:
        return df.copy(), "empty"
    n = max(1, min(int(target_rows), len(df)))
    if mode == "Aggressive default":
        out = select_top_by_score(df, "profile_member_role_score", n)
        rule = f"TOP{n}_BY_profile_member_role_score"
    elif mode == "Non-aggressive default":
        if "rank_within_stream_core_profile_signal_score_only" in df.columns:
            out = select_bottom_by_rank_value(df, "rank_within_stream_core_profile_signal_score_only", n)
            rule = f"BOTTOM{n}_BY_rank_within_stream_core_profile_signal_score_only"
        else:
            out = select_top_by_score(df, "profile_final_member_score", n)
            rule = f"TOP{n}_BY_profile_final_member_score"
    elif mode == "Optional tight / within-core role":
        if "rank_within_core_profile_member_role_score" in df.columns:
            out = select_top_by_rank_value(df, "rank_within_core_profile_member_role_score", n)
            rule = f"TOP{n}_BY_rank_within_core_profile_member_role_score"
        else:
            out = select_top_by_score(df, "profile_member_role_score", n)
            rule = f"TOP{n}_BY_profile_member_role_score"
    elif mode in ["Manual target by final score", "Manual target by final score (global ranked cut)"]:
        out = select_top_by_score(df, "profile_final_member_score", n)
        rule = f"MANUAL_GLOBAL_TOP{n}_BY_profile_final_member_score"
    elif mode in ["Manual target by member-role score", "Manual target by member-role score (global ranked cut)"]:
        out = select_top_by_score(df, "profile_member_role_score", n)
        rule = f"MANUAL_GLOBAL_TOP{n}_BY_profile_member_role_score"
    elif mode in ["Manual target by signal score", "Manual target by signal score (global ranked cut)"]:
        out = select_top_by_score(df, "profile_signal_score_only", n)
        rule = f"MANUAL_GLOBAL_TOP{n}_BY_profile_signal_score_only"
    else:
        out = select_top_by_score(df, "profile_final_member_score", n)
        rule = f"TOP{n}_BY_profile_final_member_score"
    out = add_branch_label(out, rule)
    return out, rule


def next_non_aggressive_target(current_n: int) -> int:
    for t in NON_AGGRESSIVE_LADDER:
        if current_n > t:
            return t
    return max(1, current_n)


def bottom_up_cut_preview(
    df: pd.DataFrame,
    score_col: str,
    rows_to_remove: int,
    protect_cores: bool = False,
    min_rows_per_core: int = 1,
    protect_streams: bool = False,
    min_rows_per_stream: int = 1,
) -> Tuple[pd.DataFrame, pd.DataFrame, str, int]:
    """Remove lowest-ranked plays first from the one global overall score list.

    v1.9 deliberately has no core/stream/member protection by default. The
    user's confirmed rule is: the only Step 4 protection is rank. That means
    rows with the weakest profile_final_member_score are removed first.

    Returns: kept_df, removed_df, rule_text, skipped_due_to_protection.
    """
    if df is None or df.empty:
        return pd.DataFrame(), pd.DataFrame(), "BOTTOM_UP_EMPTY", 0

    out = df.copy()
    if score_col not in out.columns:
        score_col = "profile_final_member_score" if "profile_final_member_score" in out.columns else out.columns[0]

    n_requested = max(0, min(int(rows_to_remove), len(out)))
    if n_requested <= 0:
        kept = out.copy()
        removed = out.iloc[0:0].copy()
        return kept, removed, f"BOTTOM_UP_REMOVE0_BY_{score_col}", 0

    tie_cols = []
    asc = []
    for c, a in [
        (score_col, True),
        ("profile_signal_score_only", True),
        ("profile_final_member_score", True),
        ("profile_member_role_score", True),
        ("stream", True),
        ("core", True),
        ("boxed_member", True),
    ]:
        if c in out.columns and c not in tie_cols:
            tie_cols.append(c)
            asc.append(a)

    ranked = out.sort_values(tie_cols, ascending=asc, kind="mergesort").copy()
    core_counts = ranked["core"].astype(str).value_counts().to_dict() if "core" in ranked.columns else {}
    stream_counts = ranked["stream"].astype(str).value_counts().to_dict() if "stream" in ranked.columns else {}

    remove_idx = []
    skipped = 0
    for idx, row in ranked.iterrows():
        if len(remove_idx) >= n_requested:
            break
        core = str(row.get("core", ""))
        stream = str(row.get("stream", ""))
        if protect_cores and core and core_counts.get(core, 0) <= max(0, int(min_rows_per_core)):
            skipped += 1
            continue
        if protect_streams and stream and stream_counts.get(stream, 0) <= max(0, int(min_rows_per_stream)):
            skipped += 1
            continue
        remove_idx.append(idx)
        if core:
            core_counts[core] = core_counts.get(core, 0) - 1
        if stream:
            stream_counts[stream] = stream_counts.get(stream, 0) - 1

    removed = ranked.loc[remove_idx].copy() if remove_idx else ranked.iloc[0:0].copy()
    kept = out.drop(index=remove_idx).copy() if remove_idx else out.copy()
    rule = (
        f"BOTTOM_UP_REMOVE{len(removed)}_REQUESTED{n_requested}_BY_{score_col}"
        f"_COREPROTECT_{int(bool(protect_cores))}"
        f"_STREAMPROTECT_{int(bool(protect_streams))}"
    )
    return kept, removed, rule, skipped


def core_playchart(before: pd.DataFrame, after: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    if before.empty or "core" not in before.columns:
        return pd.DataFrame()
    b = before.groupby("core").agg(before_rows=("boxed_member", "size"), before_streams=("stream", "nunique")).reset_index()
    if after is not None and not after.empty and "core" in after.columns:
        a = after.groupby("core").agg(after_rows=("boxed_member", "size"), after_streams=("stream", "nunique")).reset_index()
        out = b.merge(a, on="core", how="left").fillna(0)
    else:
        out = b.copy()
        out["after_rows"] = ""
        out["after_streams"] = ""
    if "after_rows" in out.columns and out["after_rows"].dtype != object:
        out["rows_removed"] = out["before_rows"] - out["after_rows"].astype(int)
    out["before_share_pct"] = (out["before_rows"] / max(1, int(out["before_rows"].sum())) * 100).round(1)
    return out.sort_values("before_rows", ascending=False).reset_index(drop=True)

# ----------------------------- audit -----------------------------

def prep_winner_df(winner_df: pd.DataFrame, excluded_states: List[str]) -> pd.DataFrame:
    if winner_df.empty:
        return pd.DataFrame()
    w = winner_df.copy()
    w["stream"] = w["StreamKey"].astype(str)
    w["result4"] = w["Result4"].map(norm4)
    w["actual_boxed_member"] = w["result4"].map(boxed_s)
    w["actual_core"] = w["actual_boxed_member"].map(core_from_member)
    w["is_aabc"] = w["actual_boxed_member"].map(is_aabc_member)
    w["is_watched8_core"] = w["actual_core"].isin(WATCHED8)
    w["nonplayable_excluded_step0"] = w["stream"].map(lambda s: is_nonplayable_stream(s, excluded_states))
    return w


def audit_against_stage(winners: pd.DataFrame, stage_df: pd.DataFrame, label: str) -> pd.DataFrame:
    if winners.empty:
        return pd.DataFrame()
    rows = []
    stage = stage_df.copy() if isinstance(stage_df, pd.DataFrame) else pd.DataFrame()
    for _, w in winners.iterrows():
        stream = w["stream"]
        mem = w["actual_boxed_member"]
        core = w["actual_core"]
        exact = False
        core_hit = False
        if not stage.empty:
            exact = bool(((stage.get("stream", "") == stream) & (stage.get("boxed_member", "") == mem)).any()) if {"stream", "boxed_member"}.issubset(stage.columns) else False
            core_hit = bool(((stage.get("stream", "") == stream) & (stage.get("core", "") == core)).any()) if {"stream", "core"}.issubset(stage.columns) else False
        rows.append({
            "stage": label,
            "stream": stream,
            "result4": w["result4"],
            "actual_boxed_member": mem,
            "actual_core": core,
            "is_aabc": bool(w["is_aabc"]),
            "is_watched8_core": bool(w["is_watched8_core"]),
            "nonplayable_excluded_step0": bool(w["nonplayable_excluded_step0"]),
            "exact_box_hit": exact,
            "stream_core_hit": core_hit,
        })
    return pd.DataFrame(rows)


def audit_all_stages(winners: pd.DataFrame, stages: List[Dict]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    audits = []
    for s in stages:
        audits.append(audit_against_stage(winners, s["rows"], s["label"]))
    all_a = pd.concat(audits, ignore_index=True) if audits else pd.DataFrame()
    if all_a.empty:
        return all_a, pd.DataFrame()
    playable_watched = all_a[(all_a["is_aabc"]) & (all_a["is_watched8_core"]) & (~all_a["nonplayable_excluded_step0"])]
    summary = playable_watched.groupby("stage").agg(
        watched8_playable_winner_events=("stream", "count"),
        exact_box_hits=("exact_box_hit", "sum"),
        stream_core_hits=("stream_core_hit", "sum"),
    ).reset_index()
    return all_a, summary


def score_family_firing_audit(df: pd.DataFrame, context: Optional[Dict] = None) -> pd.DataFrame:
    """Count how many rows fired each scoring family, including strict zero-fire visibility."""
    context = context or {}
    families = [
        "usable_stream_core", "top_stream_core", "usable_seed_trait", "top_seed_trait",
        "usable_stream_seed_trait", "top_stream_seed_trait", "streamrank_core_to_stream",
        "streamrank_stream_to_core", "cadence", "member_role", "exact_stream_core_member",
        "signal_count_summary", "affinity_stream_core", "affinity_rules",
        "c120_core_rule_matrix", "c120_member_replacement_matrix",
    ]
    if df is None or df.empty or "score_files_fired" not in df.columns:
        return pd.DataFrame([{"family": f, "rows_fired": 0, "pct_rows_fired": 0.0, "status": "NO_ROWS"} for f in families])
    total = max(1, len(df))
    rows = []
    sf = df["score_files_fired"].astype(str)
    for fam in families:
        cnt = int(sf.map(lambda x: fam in {part.strip() for part in str(x).split(";")}).sum())
        rows.append({
            "family": fam,
            "rows_fired": cnt,
            "pct_rows_fired": round(cnt / total * 100, 2),
            "status": "FIRED" if cnt > 0 else "ZERO_FIRE",
        })
    return pd.DataFrame(rows)



def affinity_rule_parse_audit(profile_dir: str = PROFILE_DIR) -> pd.DataFrame:
    """Return rule-level affinity parse/index audit from the loaded profiles."""
    try:
        profiles = load_profiles(profile_dir)
        df = profiles.get("affinity_parse_audit", pd.DataFrame())
        return df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()
    except Exception as e:
        return pd.DataFrame([{"parse_status": "AUDIT_ERROR", "parse_error": str(e)}])


def affinity_rule_fire_audit(scored_df: pd.DataFrame, profile_dir: str = PROFILE_DIR) -> pd.DataFrame:
    """Per-affinity-rule audit: loaded, parsed/indexed, fired row count, score impact, and example rows.
    This is designed for win/loss diagnosis: it tells which affinity rules were available,
    which were unusable, and which actually affected the ranked list.
    """
    parse_df = affinity_rule_parse_audit(profile_dir)
    if parse_df is None or parse_df.empty:
        return pd.DataFrame([{"affinity_rule_id": "NO_AFFINITY_RULES", "parse_status": "NO_RULE_FILE_OR_EMPTY", "rows_fired": 0}])
    fire_counts = {}
    fire_examples = {}
    if scored_df is not None and not scored_df.empty and "affinity_rule_ids_fired" in scored_df.columns:
        for _, row in scored_df.iterrows():
            ids = [x.strip() for x in str(row.get("affinity_rule_ids_fired", "")).split(";") if x.strip()]
            for rid in ids:
                fire_counts[rid] = fire_counts.get(rid, 0) + 1
                if rid not in fire_examples:
                    fire_examples[rid] = {
                        "example_overall_rank": row.get("overall_play_rank", ""),
                        "example_stream": row.get("stream", ""),
                        "example_seed": row.get("seed", ""),
                        "example_core": row.get("core", ""),
                        "example_member": row.get("boxed_member", row.get("member", "")),
                        "example_final_score": row.get("profile_final_member_score", ""),
                        "example_fire_details": row.get("affinity_rule_fire_details", ""),
                    }
    out = parse_df.copy()
    out["rows_fired"] = out["affinity_rule_id"].map(lambda x: int(fire_counts.get(str(x), 0)))
    out["fire_status"] = out.apply(lambda r: "FIRED" if int(r.get("rows_fired", 0)) > 0 else ("PARSED_ZERO_FIRE" if str(r.get("parse_status", "")) == "PARSED_AND_INDEXED" else "NOT_INDEXED"), axis=1)
    for col in ["example_overall_rank", "example_stream", "example_seed", "example_core", "example_member", "example_final_score", "example_fire_details"]:
        out[col] = out["affinity_rule_id"].map(lambda rid, c=col: fire_examples.get(str(rid), {}).get(c, ""))
    # Summary columns that help see whether apparent losses were due to no rule, low confidence, or no current match.
    out["audit_note"] = out.apply(lambda r: (
        "Rule affected current ranked list" if r.get("fire_status") == "FIRED" else
        "Rule parsed but current seed/stream/core did not match" if r.get("fire_status") == "PARSED_ZERO_FIRE" else
        f"Rule not usable: {r.get('parse_error','')}"
    ), axis=1)
    return out

def strict_scoring_failures(df: pd.DataFrame, context: Optional[Dict] = None) -> List[str]:
    """Stop the app if a required scoring family silently failed to fire for the ranked list."""
    if df is None or df.empty:
        return ["No scored rows were produced."]
    audit = score_family_firing_audit(df, context)
    counts = dict(zip(audit["family"], audit["rows_fired"])) if not audit.empty else {}
    required_any = {
        "usable_stream_core": "USABLE stream/core profile fired zero rows.",
        "usable_seed_trait": "USABLE seed-trait profile fired zero rows.",
        "usable_stream_seed_trait": "USABLE stream+seed-trait profile fired zero rows.",
        "member_role": "Member-role profile fired zero rows.",
        "exact_stream_core_member": "Exact stream+core+member profile fired zero rows.",
        "signal_count_summary": "Signal-count summary fired zero rows.",
    }
    failures = [msg for fam, msg in required_any.items() if int(counts.get(fam, 0)) <= 0]
    if int(counts.get("streamrank_core_to_stream", 0)) + int(counts.get("streamrank_stream_to_core", 0)) <= 0:
        failures.append("StreamRank evidence matrix fired zero rows in both directions.")
    if int(counts.get("affinity_stream_core", 0)) + int(counts.get("affinity_rules", 0)) <= 0:
        failures.append("Affinity rule file loaded but no affinity rules fired for the current Step 4 rows.")
    # v3.5: if dynamic C120 preflight is READY, require both core and member C120 families to fire.
    dyn = (context or {}).get("c120_dynamic_bundle") if isinstance(context, dict) else None
    dyn_ready = False
    if isinstance(dyn, dict):
        man = dyn.get("c120_manifest", pd.DataFrame())
        if isinstance(man, pd.DataFrame) and not man.empty:
            dyn_ready = str(man.iloc[0].get("c120_dynamic_status", "")).upper() == "READY"
    if dyn_ready:
        if int(counts.get("c120_core_rule_matrix", 0)) <= 0:
            failures.append("Dynamic C120 core rule matrix was READY but fired zero rows.")
        if int(counts.get("c120_member_replacement_matrix", 0)) <= 0:
            failures.append("Dynamic C120 member/replacement matrix was READY but fired zero rows.")
    else:
        # Static packaged C120 context remains optional/date-guarded only.
        play = date_label((context or {}).get("play_date", ""))
        seed = date_label((context or {}).get("seed_date", ""))
        profiles = load_profiles()
        c120_context_available = False
        for k in list(profiles.get("c120_member_idx", {}).keys())[:50000]:
            if len(k) >= 2 and k[0] == play and k[1] == seed:
                c120_context_available = True
                break
        if c120_context_available and int(counts.get("c120_member_replacement_matrix", 0)) <= 0:
            failures.append("Static C120 member replacement matrix has this play/history date but fired zero rows.")
    return failures


def historical_step3_core_audit(step2: pd.DataFrame, step1: pd.DataFrame, candidate_cores: List[str], play_date: str, history_df: pd.DataFrame, c120_dynamic_bundle: Optional[Dict] = None) -> Tuple[pd.DataFrame, pd.DataFrame, List[str]]:
    """Build Step 3 audit using historical scores, not only cost/prevalence."""
    ca = build_core_audit(step2, step1, candidate_cores)
    if ca.empty or step2 is None or step2.empty:
        return ca, pd.DataFrame(), ["No Step 2 rows/candidate cores available for Step 3 historical audit."]
    cand = step2[step2["core"].astype(str).isin(set(ca["core"].astype(str)))].copy()
    try:
        scored = score_rows(cand, play_date, history_df, c120_dynamic_bundle=c120_dynamic_bundle)
    except Exception as e:
        ca["historical_score_status"] = "FAILED"
        return ca, pd.DataFrame(), [f"Step 3 historical scoring failed: {e}"]
    if scored.empty:
        ca["historical_score_status"] = "NO_SCORED_ROWS"
        return ca, scored, ["Step 3 historical scoring produced no rows."]
    g = scored.groupby("core").agg(
        historical_rows_scored=("boxed_member", "size"),
        historical_avg_score=("profile_final_member_score", "mean"),
        historical_median_score=("profile_final_member_score", "median"),
        historical_max_score=("profile_final_member_score", "max"),
        historical_total_score=("profile_final_member_score", "sum"),
        historical_top25_rows=("overall_play_rank", lambda x: int((x.astype(float) <= 25).sum())),
        historical_no_rule_rows=("scoring_warning", lambda x: int((x.astype(str) != "").sum())),
    ).reset_index()
    ca = ca.merge(g, on="core", how="left").fillna({
        "historical_rows_scored": 0, "historical_avg_score": 0.0, "historical_median_score": 0.0,
        "historical_max_score": 0.0, "historical_total_score": 0.0, "historical_top25_rows": 0,
        "historical_no_rule_rows": 0,
    })
    # Higher is better; removal order uses lower historical strength first.
    ca["historical_strength_score"] = (
        ca["historical_avg_score"].astype(float) * 0.45 +
        ca["historical_median_score"].astype(float) * 0.20 +
        ca["historical_max_score"].astype(float) * 0.20 +
        ca["historical_top25_rows"].astype(float) * 0.10 -
        ca["historical_no_rule_rows"].astype(float) * 0.03
    )
    ca["step3_basis"] = "HISTORICAL_SCORE_DRIVEN"
    return ca, scored, []


def recommend_removed_cores_historical(ca: pd.DataFrame, remove_count: int) -> Tuple[List[str], pd.DataFrame]:
    if ca is None or ca.empty:
        return [], pd.DataFrame()
    ca = ca.copy()
    remove_count = max(0, min(int(remove_count), max(0, len(ca) - 1)))
    # Weakest historical score goes first. Cost breaks ties only after historical score.
    order_cols = ["historical_strength_score", "historical_avg_score", "historical_top25_rows", "safe_candidate_member_rows", "core"]
    for c in order_cols:
        if c not in ca.columns:
            ca[c] = 0 if c != "core" else ""
    ordered = ca.sort_values(order_cols, ascending=[True, True, True, False, True], kind="mergesort")
    removed = ordered.head(remove_count)["core"].astype(str).tolist()
    ca["STEP3_STATUS_PREVIEW"] = ca["core"].astype(str).map(lambda c: "REMOVE" if c in set(removed) else "KEEP")
    ca["historical_removal_rank"] = ca.set_index("core").index.map(lambda _: "")
    rank_map = {c: i+1 for i, c in enumerate(ordered["core"].astype(str).tolist())}
    ca["historical_removal_rank"] = ca["core"].astype(str).map(rank_map)
    return removed, ca.sort_values(["STEP3_STATUS_PREVIEW", "historical_removal_rank"], ascending=[False, True]).reset_index(drop=True)

# ----------------------------- output helpers -----------------------------


def printable_playlist(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["Rank", "PlayDate", "SeedListDate", "Stream", "Seed", "Core", "Member", "Score", "Branch"])
    use = df.copy()
    if "profile_final_member_score" in use.columns:
        use = use.sort_values(["profile_final_member_score", "profile_signal_score_only", "stream", "core", "boxed_member"], ascending=[False, False, True, True, True], kind="mergesort")
    else:
        use = use.sort_values(["stream", "core", "boxed_member"])
    out = pd.DataFrame({
        "Rank": range(1, len(use) + 1),
        "PlayDate": use.get("play_date", pd.Series([""] * len(use))).astype(str).values,
        "SeedListDate": use.get("seed_date", pd.Series([""] * len(use))).astype(str).values,
        "Stream": use["stream"].astype(str).values,
        "Seed": use["seed"].astype(str).str.zfill(4).values,
        "Core": use["core"].astype(str).str.zfill(3).values,
        "Member": use["boxed_member"].astype(str).str.zfill(4).values,
        "Score": use.get("profile_final_member_score", pd.Series([0] * len(use))).astype(float).round(6).values,
        "Branch": use.get("branch_name", pd.Series([""] * len(use))).astype(str).values,
    })
    return out


def zip_outputs(stages: List[Dict], winner_audit: Optional[pd.DataFrame], stage_summary: Optional[pd.DataFrame], context: Optional[Dict] = None, removed_log: Optional[pd.DataFrame] = None) -> bytes:
    context = context or {}
    play = date_label(context.get("play_date", "PLAY_DATE"))
    seed = date_label(context.get("seed_date", "SEED_DATE"))
    root = f"outputs/{play}/HISTORY_THROUGH_{seed}"
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as z:
        for s in stages:
            df = s["rows"]
            z.writestr(f"{root}/{safe_slug(s['label'])}.csv", df.to_csv(index=False))
        final = printable_playlist(stages[-1]["rows"]) if stages else pd.DataFrame()
        z.writestr(f"{root}/{dated_filename('PRINTABLE_CURRENT_PLAYLIST', context, 'csv')}", final.to_csv(index=False))
        z.writestr(f"{root}/{dated_filename('PRINTABLE_CURRENT_PLAYLIST', context, 'txt')}", playlist_text(stages[-1]["rows"], context, stages[-1]["label"] if stages else ""))
        z.writestr(f"{root}/{dated_filename('STAGE_SUMMARY', context, 'txt')}", stage_summary_text(stages, context))
        if winner_audit is not None and not winner_audit.empty:
            z.writestr(f"{root}/{dated_filename('WINNER_AUDIT_BY_STAGE', context, 'csv')}", winner_audit.to_csv(index=False))
        if stage_summary is not None and not stage_summary.empty:
            z.writestr(f"{root}/{dated_filename('STAGE_WINNER_SUMMARY', context, 'csv')}", stage_summary.to_csv(index=False))
        # v1.9 audit/debug files: scoring components, step decisions, and profile load status.
        try:
            prof_status = profile_file_status()
            z.writestr(f"{root}/debug_file_load_status.csv", prof_status.to_csv(index=False))
            z.writestr(f"{root}/c120_engine_file_status.csv", c120_engine_file_status().to_csv(index=False))
        except Exception as e:
            z.writestr(f"{root}/debug_file_load_status_ERROR.txt", str(e))
        # Include dynamic C120 preflight manifest and compact outputs when available.
        try:
            dyn = context.get("c120_dynamic_bundle", {}) if isinstance(context, dict) else {}
            if isinstance(dyn, dict):
                man = dyn.get("c120_manifest", pd.DataFrame())
                if isinstance(man, pd.DataFrame) and not man.empty:
                    z.writestr(f"{root}/c120_dynamic_preflight_manifest.csv", man.to_csv(index=False))
                sc = dyn.get("c120_source_counts", pd.DataFrame())
                if isinstance(sc, pd.DataFrame) and not sc.empty:
                    z.writestr(f"{root}/c120_dynamic_source_counts.csv", sc.to_csv(index=False))
                outs = dyn.get("c120_outputs", {})
                if isinstance(outs, dict):
                    for nm in ["MATRIX_BUILD_REPORT.csv", "SEED_ALIGNMENT_SUMMARY.csv", "DAILY_SEED_EVENTS_FOR_PLAY_DATE.csv", "DAILY_CORE_MATRIX_ALL_CANDIDATES.csv", "DAILY_MEMBER_MATRIX_ALL_CANDIDATES.csv", "DAILY_MEMBER_PLAYLIST_TOPN.csv", "FULL_DAILY_RULE_MATRIX_ALL_120_CORES.csv", "TRAP_RUN_SUMMARY.csv", "LEAKAGE_AUDIT.csv"]:
                        dfout = outs.get(nm, pd.DataFrame())
                        if isinstance(dfout, pd.DataFrame) and not dfout.empty:
                            z.writestr(f"{root}/c120_dynamic/{nm}", dfout.to_csv(index=False))
        except Exception as e:
            z.writestr(f"{root}/c120_dynamic_export_ERROR.txt", str(e))
        if stages:
            rows=[]
            for s in stages:
                m=stage_metrics(s["rows"])
                rows.append({"stage":s.get("label",""), "rows":m["Rows"], "streams":m["Streams"], "cores":m["Cores"], "members":m["Members"], "note":s.get("note",""), "selection_rule":s.get("selection_rule","")})
            z.writestr(f"{root}/step_reduction_audit.csv", pd.DataFrame(rows).to_csv(index=False))
            latest = stages[-1]["rows"]
            audit_cols = unique_existing_cols(latest, ["overall_play_rank", "play_date", "seed_date", "stream", "seed", "core", "boxed_member", "profile_final_member_score", "profile_signal_score_only", "profile_core_score", "profile_member_role_score", "score_exact_stream_core_member", "score_stream_core_usable", "score_stream_core_top", "score_seed_trait_usable", "score_seed_trait_top", "score_stream_seed_trait_usable", "score_stream_seed_trait_top", "score_streamrank_core_to_stream", "score_streamrank_stream_to_core", "score_cadence", "score_signal_count_summary", "score_affinity_stream_core", "score_affinity_seed", "score_affinity_stream_seed", "score_c120_core_matrix", "score_c120_member_matrix", "score_c120_replacement_matrix", "c120_trap_lane", "c120_source_file", "score_files_fired", "score_trait_hits", "affinity_rule_ids_fired", "affinity_rule_fire_details", "scoring_warning", "branch_name"])
            if audit_cols:
                z.writestr(f"{root}/score_component_audit.csv", latest[audit_cols].to_csv(index=False))
                z.writestr(f"{root}/decision_audit.csv", latest[audit_cols].to_csv(index=False))
            if isinstance(latest, pd.DataFrame) and "score_files_fired" in latest.columns:
                z.writestr(f"{root}/profile_family_firing_audit.csv", score_family_firing_audit(latest, context).to_csv(index=False))
                z.writestr(f"{root}/affinity_rule_parse_audit.csv", affinity_rule_parse_audit().to_csv(index=False))
                z.writestr(f"{root}/affinity_rule_fire_audit.csv", affinity_rule_fire_audit(latest).to_csv(index=False))
                strict = pd.DataFrame({"strict_scoring_failure": strict_scoring_failures(latest, context)})
                z.writestr(f"{root}/strict_scoring_failure_audit.csv", strict.to_csv(index=False))
            core_audit = context.get("core_audit", pd.DataFrame()) if isinstance(context, dict) else pd.DataFrame()
            if isinstance(core_audit, pd.DataFrame) and not core_audit.empty:
                z.writestr(f"{root}/step3_historical_core_audit.csv", core_audit.to_csv(index=False))
            if removed_log is not None and isinstance(removed_log, pd.DataFrame) and not removed_log.empty:
                z.writestr(f"{root}/step4_removed_rows_audit.csv", removed_log.to_csv(index=False))
        readme = f"{APP_VERSION}\n{BUILD_MARKER}\nGenerated from current app session.\nPLAY_DATE={context.get('play_date','')}\nHISTORY_THROUGH={context.get('seed_date','')}\nHISTORY_SOURCE={context.get('history_source','')}\n"
        z.writestr(f"{root}/README.txt", readme)
    return bio.getvalue()


# ----------------------------- stepwise helper functions -----------------------------

def aabc_history_core_summary(history_df: pd.DataFrame, excluded_states: Optional[List[str]] = None) -> Dict[str, int]:
    """Count true AABC core/member coverage in the loaded history, optionally excluding states."""
    if history_df is None or history_df.empty:
        return {"History rows": 0, "AABC rows": 0, "AABC cores": 0, "AABC members": 0, "Streams": 0}
    h = history_df.copy()
    h["stream"] = h["StreamKey"].astype(str) if "StreamKey" in h.columns else ""
    if excluded_states:
        h = h[~h["stream"].map(lambda s: is_nonplayable_stream(s, excluded_states))].copy()
    h["result4"] = h["Result4"].map(norm4) if "Result4" in h.columns else ""
    h["boxed_member"] = h["result4"].map(boxed_s)
    h["core"] = h["boxed_member"].map(core_from_member)
    aabc = h[h["core"].ne("")].copy()
    return {
        "History rows": int(len(h)),
        "AABC rows": int(len(aabc)),
        "AABC cores": int(aabc["core"].nunique()) if not aabc.empty else 0,
        "AABC members": int(aabc["boxed_member"].nunique()) if not aabc.empty else 0,
        "Streams": int(h["stream"].nunique()) if "stream" in h.columns else 0,
    }


def prepare_step0_seed_rows(seed_df: pd.DataFrame, excluded_states: List[str], play_date: str, seed_date: str) -> pd.DataFrame:
    if seed_df is None or seed_df.empty:
        return pd.DataFrame(columns=["play_date", "seed_date", "stream", "seed", "Date", "State", "Game", "nonplayable_excluded_step0"])
    seeds = seed_df.copy()
    seeds["stream"] = seeds["StreamKey"].astype(str)
    seeds["seed"] = seeds["Result4"].map(norm4)
    seeds["play_date"] = play_date
    seeds["seed_date"] = seed_date
    seeds["nonplayable_excluded_step0"] = seeds["stream"].map(lambda s: is_nonplayable_stream(s, excluded_states))
    playable = seeds[~seeds["nonplayable_excluded_step0"]].drop_duplicates(["stream"], keep="last").copy()
    keep_cols = [c for c in ["play_date", "seed_date", "Date", "State", "Game", "stream", "seed", "StreamKey", "nonplayable_excluded_step0"] if c in playable.columns]
    return playable[keep_cols].reset_index(drop=True)


def build_step1_full120(playable: pd.DataFrame, play_date: str, seed_date: str, history_df: pd.DataFrame, excluded_states: List[str], progress=None) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Step 1: generate mirror-qualified rows from full 360-member / 120-core AABC universe."""
    members, member_idx, member_core, watched_members, all_vsets, vcode, seed_vcode_arr, mirror_mat = build_universe()
    if progress:
        progress.progress(5, text="Preparing rolling mirror counters from history...")
    cnt_mirror, gmirror, trans = build_rolling_mirror_counters(history_df, play_date, excluded_states)
    stream_context = []
    step1_rows = []
    if progress:
        progress.progress(35, text="Generating FULL120 mirror-qualified candidates...")
    total = max(1, len(playable))
    for i, (_, r) in enumerate(playable.iterrows(), start=1):
        seed = norm4(r.get("seed", ""))
        if not seed:
            continue
        seed_int = int(seed)
        sv = int(seed_vcode_arr[seed_int])
        allowed = top_indices(cnt_mirror[sv], 2, gmirror).tolist()
        if not allowed:
            allowed = list(np.argsort(-gmirror)[:2]) if gmirror.sum() > 0 else [1, 0]
        mirror_counts = mirror_mat[seed_int]
        q_count = int(np.isin(mirror_counts, allowed).sum())
        stream_context.append({
            "play_date": play_date,
            "seed_date": seed_date,
            "stream": r["stream"],
            "seed": seed,
            "mirror_qualified_count": q_count,
            "allowed_mirror_hits": "|".join(map(str, allowed)),
        })
        for m in members:
            mi = member_idx[m]
            if int(mirror_counts[mi]) in allowed:
                step1_rows.append({
                    "play_date": play_date,
                    "seed_date": seed_date,
                    "stream": r["stream"],
                    "seed": seed,
                    "boxed_member": m,
                    "core": member_core[m],
                    "mirror_qualified_count": q_count,
                    "actual_mirror_hits_for_member": int(mirror_counts[mi]),
                    "allowed_mirror_hits": "|".join(map(str, allowed)),
                })
        if progress and (i % 10 == 0 or i == total):
            progress.progress(min(95, 35 + int(i / total * 60)), text=f"Generated candidates for {i}/{total} streams...")
    return add_branch_label(pd.DataFrame(step1_rows), "STEP1_MIRROR_ONLY_FULL120"), pd.DataFrame(stream_context)


def build_step2_mirror_bucket(step1: pd.DataFrame, stream_context: pd.DataFrame) -> Tuple[pd.DataFrame, str]:
    """
    Step 2 must visibly reduce candidate rows before Step 3, without restricting to watched cores.

    Original v1.3 used only stream-level median(q_count). On many real seed pages every
    playable stream ties at the same q_count, e.g. 240, so >= median keeps every stream
    and nothing changes. That is mathematically possible but operationally useless.

    v1.4 therefore tries the stream median first only when it actually separates streams.
    If it would keep all streams or no streams, Step 2 falls back to the stronger mirror
    bucket inside each stream: keep only the first/highest-priority bucket from
    allowed_mirror_hits. This changes row/play count while preserving FULL120 core/member
    availability until Step 3.
    """
    if step1 is None or step1.empty or stream_context is None or stream_context.empty:
        return pd.DataFrame(), "No Step 1 rows or stream context."

    med = float(stream_context["mirror_qualified_count"].median())
    keep_streams = set(stream_context[stream_context["mirror_qualified_count"] >= med]["stream"])
    all_streams = set(stream_context["stream"].astype(str))

    # Use median only if it actually changes the stream set.
    if 0 < len(keep_streams) < len(all_streams):
        step2 = step1[step1["stream"].astype(str).isin(keep_streams)].copy()
        note = f"Stream median separated the page: kept {len(keep_streams)} of {len(all_streams)} streams where mirror_qualified_count >= median ({med:g})."
        return add_branch_label(step2, "STEP2_STREAM_MEDIAN_BUCKET"), note

    # No-change median case: reduce within every stream using the primary mirror bucket.
    s2 = step1.copy()
    primary_bucket = (
        s2["allowed_mirror_hits"]
        .astype(str)
        .str.split("|")
        .str[0]
        .map(lambda x: int(x) if str(x).strip().lstrip("-").isdigit() else -999)
    )
    s2["step2_primary_mirror_bucket"] = primary_bucket
    s2 = s2[s2["actual_mirror_hits_for_member"].astype(int).eq(s2["step2_primary_mirror_bucket"].astype(int))].copy()
    before_rows = int(len(step1))
    after_rows = int(len(s2))
    before_streams = int(step1["stream"].nunique()) if "stream" in step1.columns else 0
    after_streams = int(s2["stream"].nunique()) if "stream" in s2.columns and not s2.empty else 0
    note = (
        f"Stream median did not separate this seed page (median {med:g}; it would keep {len(keep_streams)} of {len(all_streams)} streams). "
        f"Applied primary mirror-bucket refinement instead: rows {before_rows} -> {after_rows}; streams {before_streams} -> {after_streams}. "
        "FULL120 core/member universe is still allowed; watched-core restriction still waits until Step 3."
    )
    return add_branch_label(s2, "STEP2_PRIMARY_MIRROR_BUCKET"), note


# Backward-compatible alias for any older callbacks/state references.
def build_step2_median(step1: pd.DataFrame, stream_context: pd.DataFrame) -> Tuple[pd.DataFrame, str]:
    return build_step2_mirror_bucket(step1, stream_context)


def available_profile_cores() -> List[str]:
    """Return cores present in packaged V6 profile files, falling back to WATCHED8."""
    cores = set(WATCHED8)
    try:
        for f in PROFILE_DIR.glob("V6_8CORE_*.csv"):
            try:
                df = pd.read_csv(f, dtype=str, keep_default_na=False, nrows=5000)
                if "core_str" in df.columns:
                    cores.update(df["core_str"].map(norm_core).dropna().astype(str).tolist())
            except Exception:
                pass
    except Exception:
        pass
    return sorted(c for c in cores if c)


def build_core_audit(step2: pd.DataFrame, step1: pd.DataFrame, candidate_cores: List[str]) -> pd.DataFrame:
    candidate_cores = sorted({norm_core(c) for c in candidate_cores if norm_core(c)})
    if step2 is None or step2.empty or not candidate_cores:
        return pd.DataFrame(columns=["core", "safe_candidate_member_rows", "safe_streams_touched", "eliminated_candidate_member_rows", "eliminated_streams_touched"])
    safe_counts = step2.groupby("core").agg(
        safe_candidate_member_rows=("boxed_member", "size"),
        safe_streams_touched=("stream", "nunique"),
    ).reindex(candidate_cores).fillna(0).astype(int).reset_index()
    # Count rows Step 2 removed by core. v1.3 only counted streams removed by Step 2,
    # which showed zero eliminated rows when Step 2 reduced members inside still-kept streams.
    if step1 is None or step1.empty:
        eliminated_source = pd.DataFrame()
    elif step2 is None or step2.empty:
        eliminated_source = step1.copy()
    else:
        key_cols = [c for c in ["stream", "seed", "boxed_member", "core"] if c in step1.columns and c in step2.columns]
        if key_cols:
            survivor_keys = step2[key_cols].drop_duplicates()
            eliminated_source = step1.merge(survivor_keys, on=key_cols, how="left", indicator=True)
            eliminated_source = eliminated_source[eliminated_source["_merge"].eq("left_only")].drop(columns=["_merge"])
        else:
            eliminated_source = step1[~step1["stream"].isin(set(step2["stream"]))].copy() if "stream" in step1.columns and "stream" in step2.columns else pd.DataFrame()
    if eliminated_source.empty:
        eliminated = pd.DataFrame({"core": candidate_cores, "eliminated_candidate_member_rows": 0, "eliminated_streams_touched": 0})
    else:
        eliminated = eliminated_source.groupby("core").agg(
            eliminated_candidate_member_rows=("boxed_member", "size"),
            eliminated_streams_touched=("stream", "nunique"),
        ).reindex(candidate_cores).fillna(0).astype(int).reset_index()
    ca = safe_counts.merge(eliminated, on="core", how="left").fillna(0)
    ca["core"] = ca["core"].astype(str).map(norm_core)
    ca["safe_candidate_member_rows"] = ca["safe_candidate_member_rows"].astype(int)
    ca["safe_streams_touched"] = ca["safe_streams_touched"].astype(int)
    ca["eliminated_candidate_member_rows"] = ca["eliminated_candidate_member_rows"].astype(int)
    ca["eliminated_streams_touched"] = ca["eliminated_streams_touched"].astype(int)
    return ca


def default_dynamic_removed(ca: pd.DataFrame) -> Tuple[set, set, set]:
    """Original dynamic idea with min/max save protection for chunk validation."""
    if ca is None or ca.empty:
        return set(), set(), set()
    initial_remove = set()
    to_save = []
    min_safe = ca["safe_candidate_member_rows"].min()
    max_safe = ca["safe_candidate_member_rows"].max()
    initial_remove.update(ca.loc[ca["safe_candidate_member_rows"].eq(min_safe), "core"].tolist())
    initial_remove.update(ca.loc[ca["safe_candidate_member_rows"].eq(max_safe), "core"].tolist())
    min_elim = ca["eliminated_candidate_member_rows"].min()
    low_elim = ca[ca["eliminated_candidate_member_rows"].eq(min_elim)].sort_values(["safe_candidate_member_rows", "core"])
    if len(low_elim):
        initial_remove.add(low_elim.iloc[0]["core"])
    remove = set(initial_remove)
    minmax_cores = set(ca.loc[ca["safe_candidate_member_rows"].isin([min_safe, max_safe]), "core"].tolist())
    costs = {row.core: int(row.safe_candidate_member_rows) for row in ca.itertuples(index=False)}
    save_candidates = [c for c in minmax_cores if c in remove]
    for c in save_candidates:
        if costs.get(c, 999999) <= SAVE_AUTO_DEFAULT:
            to_save.append(c)
    if len(to_save) >= 2 and sum(costs[c] for c in to_save) > SAVE_COMBINED_DEFAULT:
        to_save = sorted(to_save, key=lambda c: (costs[c], c))[:1]
    if not to_save and len(save_candidates) == 1 and costs[save_candidates[0]] <= SAVE_SOLO_DEFAULT:
        to_save = [save_candidates[0]]
    remove.difference_update(to_save)
    return remove, initial_remove, set(to_save)


def recommend_removed_cores(ca: pd.DataFrame, remove_count: int, protect_minmax: bool = True) -> Tuple[List[str], pd.DataFrame]:
    if ca is None or ca.empty:
        return [], pd.DataFrame()
    ca = ca.copy()
    dyn_remove, initial_remove, saved = default_dynamic_removed(ca)
    ca["dynamic_remove_before_minmax_save"] = ca["core"].isin(initial_remove)
    ca["saved_by_minmax_default"] = ca["core"].isin(saved)
    ca["dynamic_default_remove"] = ca["core"].isin(dyn_remove)
    min_safe = ca["safe_candidate_member_rows"].min() if len(ca) else 0
    max_safe = ca["safe_candidate_member_rows"].max() if len(ca) else 0
    ca["minmax_edge_core"] = ca["safe_candidate_member_rows"].isin([min_safe, max_safe])
    # Elimination order: original dynamic removals first; then high-cost cores if the user asks for more cuts.
    # Protected min/max saved cores move lower unless the user explicitly asks for enough eliminations to reach them.
    ca["remove_priority"] = 0
    ca.loc[ca["dynamic_default_remove"], "remove_priority"] = 100
    ca.loc[ca["dynamic_remove_before_minmax_save"] & ~ca["dynamic_default_remove"], "remove_priority"] = 75
    if protect_minmax:
        ca.loc[ca["saved_by_minmax_default"], "remove_priority"] -= 50
    ca["cost_priority"] = ca["safe_candidate_member_rows"].astype(int)
    ordered = ca.sort_values(["remove_priority", "cost_priority", "eliminated_candidate_member_rows", "core"], ascending=[False, False, True, True])
    removed = ordered.head(max(0, min(int(remove_count), len(ordered))))["core"].astype(str).tolist()
    ca["STEP3_STATUS_PREVIEW"] = ca["core"].apply(lambda c: "REMOVE" if c in set(removed) else "KEEP")
    return removed, ca.sort_values(["STEP3_STATUS_PREVIEW", "safe_candidate_member_rows", "core"], ascending=[False, False, True]).reset_index(drop=True)


def apply_step3_manual(step2: pd.DataFrame, step1: pd.DataFrame, candidate_cores: List[str], remove_cores: List[str]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    candidate_cores = sorted({norm_core(c) for c in candidate_cores if norm_core(c)})
    remove_set = {norm_core(c) for c in remove_cores if norm_core(c)}
    keep_cores = sorted(set(candidate_cores) - remove_set)
    ca = build_core_audit(step2, step1, candidate_cores)
    dyn_remove, initial_remove, saved = default_dynamic_removed(ca)
    ca["dynamic_remove_before_minmax_save"] = ca["core"].isin(initial_remove)
    ca["saved_by_minmax_default"] = ca["core"].isin(saved)
    ca["STEP3_STATUS"] = ca["core"].apply(lambda c: "KEEP" if c in keep_cores else "REMOVE")
    step3 = step2[step2["core"].isin(keep_cores)].copy() if step2 is not None and not step2.empty else pd.DataFrame()
    if not step3.empty:
        merge_cols = ["core", "safe_candidate_member_rows", "safe_streams_touched", "eliminated_candidate_member_rows", "eliminated_streams_touched", "STEP3_STATUS", "saved_by_minmax_default"]
        step3 = step3.merge(ca[merge_cols], on="core", how="left")
    return add_branch_label(step3, "STEP3_CORE_SELECTION"), ca



def build_final_from_step3(step3: pd.DataFrame, play_date: str, history_df: pd.DataFrame, progress=None) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Legacy helper retained for compatibility; v1.9 never applies hidden reduction."""
    if step3 is None or step3.empty:
        return pd.DataFrame(), pd.DataFrame()
    if progress:
        progress.progress(15, text="Scoring selected Step 3 rows...")
    scored = score_rows(step3, play_date, history_df)
    scored = add_branch_label(scored, "STEP4_SCORED_BASE")
    if progress:
        progress.progress(100, text="Scored base ready. No hidden final reduction applied.")
    return scored, add_branch_label(scored.copy(), "FINAL_FROM_STEP4_NO_HIDDEN_REDUCTION")

def reset_stages_from(step_label_prefix: str):
    """Drop the named step and all downstream steps from session_state.stages."""
    keep = []
    for s in st.session_state.stages:
        if str(s.get("label", "")).startswith(step_label_prefix):
            break
        keep.append(s)
    st.session_state.stages = keep


def get_stage(label_prefix: str) -> Optional[Dict]:
    for s in st.session_state.get("stages", []):
        if str(s.get("label", "")).startswith(label_prefix):
            return s
    return None


def get_latest_stage(label_prefix: str) -> Optional[Dict]:
    for s in reversed(st.session_state.get("stages", [])):
        if str(s.get("label", "")).startswith(label_prefix):
            return s
    return None


def count_stages(label_prefix: str) -> int:
    return sum(1 for s in st.session_state.get("stages", []) if str(s.get("label", "")).startswith(label_prefix))


def drop_final_stages():
    st.session_state.stages = [s for s in st.session_state.get("stages", []) if not str(s.get("label", "")).startswith("FINAL")]


def stage_dashboard(stages: List[Dict]) -> pd.DataFrame:
    rows = []
    for i, s in enumerate(stages):
        m = stage_metrics(s["rows"])
        rows.append({
            "#": i,
            "Stage": s["label"],
            "Rows/Plays": m["Rows"],
            "Streams": m["Streams"],
            "Cores": m["Cores"],
            "Members": m["Members"],
            "Rule/Note": s.get("note", ""),
        })
    return pd.DataFrame(rows)

# ----------------------------- Streamlit UI -----------------------------

st.title("P4 Mirror Ladder App")
st.caption(f"{APP_VERSION} · {BUILD_MARKER}")

if "stages" not in st.session_state:
    st.session_state.stages = []
if "context" not in st.session_state:
    st.session_state.context = {}
if "winner_audit" not in st.session_state:
    st.session_state.winner_audit = pd.DataFrame()
if "winner_summary" not in st.session_state:
    st.session_state.winner_summary = pd.DataFrame()
if "step4_removed_log" not in st.session_state:
    st.session_state.step4_removed_log = pd.DataFrame()
if "c120_dynamic_bundle" not in st.session_state:
    st.session_state.c120_dynamic_bundle = {}

with st.expander("What changed in this repaired build", expanded=True):
    st.markdown(
        """
- **Step 1 FULL120 is enumeration only.** The app builds the full 120-core / 360-member AABC universe first, then Step 3 moves into the watched 8-core engine.
- **Step 4 has one ranking only:** the overall best-play score, not stream-only, core-only, or member-only.
- **Step 4 cuts rows only from the bottom of that one ranked list.** Non-aggressive removes 10 worst rows, aggressive removes 50 worst rows, and manual removes the number you choose.
- **No core/stream/member protection is applied in Step 4.** The only protection is rank, per your instruction.
- **Dynamic C120 preflight is now required before Step 3/4 scoring.** It rebuilds the C120 stable-rule daily matrix from the uploaded history and rule library, then verifies seed alignment before C120 can affect scores.
- **Step 5 applies no reduction.** It only builds/displays/downloads the current Step 4 list and writes audit/debug files.
        """
    )

st.sidebar.header("Inputs")
history_upload = st.sidebar.file_uploader("Upload updated history CSV/TXT", type=["csv", "txt", "tsv"], key="history_upload_stepwise")
seed_upload = st.sidebar.file_uploader("Optional: upload seed list TXT/CSV", type=["txt", "csv", "tsv"], key="seed_upload_stepwise")
winner_upload = st.sidebar.file_uploader("Optional: upload winner list for audit", type=["txt", "csv", "tsv"], key="winner_upload_stepwise")

history_df_current, history_source_label, history_note = load_history_source(history_upload)
date_opts = date_options_from_history(history_df_current)
latest_history_date = date_opts[-1] if date_opts else "2026-06-18"
st.sidebar.info(history_note)

seed_source_mode = st.sidebar.radio(
    "Seed source",
    ["Use selected date from history", "Use uploaded seed list"],
    index=0 if seed_upload is None else 1,
)

if date_opts:
    selected_seed_date = st.sidebar.selectbox("Seed/history-through date from history", date_opts, index=len(date_opts) - 1)
else:
    selected_seed_date = st.sidebar.text_input("Seed/history-through date", value=latest_history_date)

seed_date = st.sidebar.text_input("Seed/history-through date used on playlist", value=selected_seed_date)
play_date = st.sidebar.text_input("Play date", value=next_date_label(seed_date) or "2026-06-19")
exclude_az_md = st.sidebar.checkbox("Step 0: exclude Arizona/AZ and Maryland/MD before row generation", value=True)
extra_exclude_text = st.sidebar.text_area("Optional extra excluded state names, one per line", value="", height=75)
excluded_states = []
if exclude_az_md:
    excluded_states.extend(["Arizona", "AZ", "Maryland", "MD"])
excluded_states.extend([x.strip() for x in extra_exclude_text.splitlines() if x.strip()])

# Build current seed preview every run so the top image/dashboard changes with input changes.
if seed_source_mode == "Use uploaded seed list":
    seed_df_preview = parse_uploaded_table(seed_upload, default_date=seed_date) if seed_upload is not None else pd.DataFrame()
else:
    seed_df_preview = seed_page_from_history(history_df_current, seed_date)
step0_preview = prepare_step0_seed_rows(seed_df_preview, excluded_states, play_date, seed_date)
full_hist_summary = aabc_history_core_summary(history_df_current, [])
playable_hist_summary = aabc_history_core_summary(history_df_current, excluded_states)

st.subheader("Loaded history / Step 0 preview")
st.caption("These numbers update immediately when you change the history file, seed date, seed upload, or AZ/MD exclusion setting.")

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("History AABC cores", full_hist_summary["AABC cores"])
m2.metric("History AABC members", full_hist_summary["AABC members"])
m3.metric("History streams", full_hist_summary["Streams"])
m4.metric("Seed rows before Step 0", int(len(seed_df_preview)))
m5.metric("Playable seed rows after Step 0", int(len(step0_preview)))

if excluded_states:
    c1, c2, c3 = st.columns(3)
    c1.metric("Playable-history AABC cores", playable_hist_summary["AABC cores"])
    c2.metric("Excluded seed rows", max(0, int(len(seed_df_preview) - len(step0_preview))))
    c3.metric("Step 0 excluded states", ", ".join(excluded_states[:6]) + ("..." if len(excluded_states) > 6 else ""))

with st.expander("Step 0 seed rows preview", expanded=False):
    if step0_preview.empty:
        st.warning("No playable seed rows found yet. Check the selected date or upload a seed list.")
    else:
        safe_st_dataframe(step0_preview, use_container_width=True, hide_index=True, height=260)

if st.button("Apply Step 0 / reset downstream", type="primary"):
    if step0_preview.empty:
        st.error("Step 0 cannot be applied because there are no playable seed rows.")
    else:
        st.session_state.stages = [stage_record(
            "STEP0_PLAYABLE_SEEDS",
            step0_preview,
            "Applied AZ/MD/non-playable stream exclusion before row generation.",
            "STEP0_EXCLUDE_NONPLAYABLE",
        )]
        st.session_state.context = {
            "history_df": history_df_current,
            "history_source": history_source_label,
            "play_date": play_date,
            "seed_date": seed_date,
            "excluded_states": excluded_states,
            "seed_source_mode": seed_source_mode,
            "stream_context": pd.DataFrame(),
            "core_audit": pd.DataFrame(),
            "c120_dynamic_bundle": {},
        }
        st.session_state.winner_audit = pd.DataFrame()
        st.session_state.winner_summary = pd.DataFrame()
        st.session_state.step4_removed_log = pd.DataFrame()
        st.session_state.c120_dynamic_bundle = {}
        st.success("Step 0 applied. Step 1 is now available.")
        st.rerun()

st.subheader("Current stage dashboard")
if st.session_state.stages:
    safe_st_dataframe(stage_dashboard(st.session_state.stages), use_container_width=True, hide_index=True, height=220)
    current = st.session_state.stages[-1]["rows"]
    cm = stage_metrics(current)
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Current rows / plays", cm["Rows"])
    k2.metric("Current streams", cm["Streams"])
    k3.metric("Current cores", cm["Cores"])
    k4.metric("Current members", cm["Members"])
else:
    st.info("Apply Step 0 to begin the ladder.")

step0_stage = get_stage("STEP0")
step1_stage = get_stage("STEP1")
step2_stage = get_stage("STEP2")
step3_stage = get_stage("STEP3")
final_stage = get_stage("FINAL")

st.divider()
st.subheader("Step 1 — FULL120 mirror qualification")
st.write("Step 1 takes each playable seed stream from Step 0 and tests it against the full AABC boxed universe: all 360 members and all 120 cores. No watched-core restriction is allowed here.")
if step0_stage is None:
    st.info("Apply Step 0 first.")
else:
    if st.button("Apply Step 1 — build FULL120 mirror candidates"):
        prog = st.progress(0, text="Starting Step 1...")
        step1, stream_ctx = build_step1_full120(
            step0_stage["rows"],
            st.session_state.context.get("play_date", play_date),
            st.session_state.context.get("seed_date", seed_date),
            st.session_state.context.get("history_df", history_df_current),
            st.session_state.context.get("excluded_states", excluded_states),
            progress=prog,
        )
        # Keep only Step 0, then append Step 1.
        st.session_state.stages = st.session_state.stages[:1]
        st.session_state.stages.append(stage_record(
            "STEP1_MIRROR_ONLY_FULL120",
            step1,
            "Mirror-only FULL120; all 360 AABC members / all 120 cores; no watched-core restriction.",
            "MIRROR_FULL120",
        ))
        st.session_state.context["stream_context"] = stream_ctx
        st.session_state.context["core_audit"] = pd.DataFrame()
        prog.empty()
        st.success("Step 1 applied. Counts should now show up to 120 cores and 360 members.")
        st.rerun()

st.divider()
st.subheader("Step 2 — mirror bucket refinement")
st.write("Step 2 applies the next mirror filter before any watched-core restriction. If the stream median separates the page, it keeps the upper stream bucket. If the median ties all streams, it keeps only the primary mirror bucket inside each stream so the row count changes here instead of silently doing nothing.")
if step1_stage is None:
    st.info("Apply Step 1 first.")
else:
    stream_ctx = st.session_state.context.get("stream_context", pd.DataFrame())
    if isinstance(stream_ctx, pd.DataFrame) and not stream_ctx.empty:
        with st.expander("Step 1 stream mirror counts", expanded=False):
            safe_st_dataframe(stream_ctx.sort_values("mirror_qualified_count", ascending=False), use_container_width=True, hide_index=True, height=260)
    if st.button("Apply Step 2 — apply mirror bucket refinement"):
        before_rows = len(step1_stage["rows"]) if step1_stage and isinstance(step1_stage.get("rows"), pd.DataFrame) else 0
        step2, note = build_step2_mirror_bucket(step1_stage["rows"], stream_ctx)
        after_rows = len(step2) if isinstance(step2, pd.DataFrame) else 0
        st.session_state.stages = st.session_state.stages[:2]
        st.session_state.stages.append(stage_record("STEP2_MIRROR_BUCKET_REFINEMENT", step2, note, "MIRROR_BUCKET_REFINEMENT"))
        st.session_state.context["core_audit"] = pd.DataFrame()
        if after_rows == before_rows:
            st.warning("Step 2 applied, but row count did not change. Check the Step 1 stream mirror counts; this means the seed page is fully tied under the active mirror bucket rule.")
        else:
            st.success(f"Step 2 applied. Rows changed from {before_rows} to {after_rows}. Step 3 core selection is now available.")
        st.rerun()


st.divider()
st.subheader("C120 dynamic preflight — rebuild full 120-core matrix from current history")
st.write("v3.5 requires this preflight before Step 3/Step 4 scoring. It runs the packaged stable C120 rule library against the current history, verifies seed = previous same-stream winner, and builds fresh C120 core/member outputs for the selected play date. The rule-library row must show READY before preflight can run.")
ok_c120_files, c120_file_status, c120_missing = c120_required_ready()
with st.expander("C120 engine/rule file status", expanded=True):
    safe_st_dataframe(c120_file_status, use_container_width=True, hide_index=True, height=260)
    rule_status_rows = c120_file_status[c120_file_status["file"].astype(str).eq("core_rule_library_stable_only_filtered.csv")] if isinstance(c120_file_status, pd.DataFrame) and "file" in c120_file_status.columns else pd.DataFrame()
    if rule_status_rows.empty:
        st.error("C120 status table does not include the rule-library row. This means the deployed app.py is not v3.5.")
    elif bool(rule_status_rows.iloc[0].get("exists", False)):
        st.success("C120 rule library found: " + str(rule_status_rows.iloc[0].get("path", "")))
    else:
        st.error("C120 rule library NOT found. Upload core_rule_library_stable_only_filtered.csv to repo root or /rules/.")
if c120_missing:
    st.error("C120 dynamic preflight cannot run because required files are missing: " + ", ".join(c120_missing))

c120_bundle = st.session_state.context.get("c120_dynamic_bundle", {}) if isinstance(st.session_state.get("context"), dict) else {}
if isinstance(c120_bundle, dict) and c120_bundle.get("ok"):
    man = c120_bundle.get("c120_manifest", pd.DataFrame())
    st.success("C120 dynamic preflight is READY for this session.")
    if isinstance(man, pd.DataFrame) and not man.empty:
        safe_st_dataframe(man, use_container_width=True, hide_index=True)
    sc = c120_bundle.get("c120_source_counts", pd.DataFrame())
    if isinstance(sc, pd.DataFrame) and not sc.empty:
        with st.expander("C120 dynamic indexed source counts", expanded=False):
            safe_st_dataframe(sc, use_container_width=True, hide_index=True)
elif step2_stage is None:
    st.info("Apply Step 2 first, then run C120 dynamic preflight.")
elif ok_c120_files:
    if st.button("Run C120 dynamic preflight for current history/play date", type="primary"):
        prog = st.progress(0, text="Running C120 dynamic preflight. This may take a minute...")
        bundle = run_c120_dynamic_preflight(
            st.session_state.context.get("history_df", history_df_current),
            st.session_state.context.get("play_date", play_date),
            st.session_state.context.get("seed_date", seed_date),
        )
        prog.progress(100, text="C120 dynamic preflight finished.")
        prog.empty()
        st.session_state.c120_dynamic_bundle = bundle
        st.session_state.context["c120_dynamic_bundle"] = bundle
        # Reset downstream stages because the C120 scoring basis has changed.
        st.session_state.stages = [s for s in st.session_state.stages if not str(s.get("label", "")).startswith(("STEP3", "STEP4", "FINAL"))]
        if bundle.get("ok"):
            st.success("C120 dynamic preflight passed. Step 3 is now available with fresh C120 evidence.")
        else:
            st.error(bundle.get("error", "C120 dynamic preflight failed."))
        st.rerun()

c120_ready_for_scoring = isinstance(st.session_state.context.get("c120_dynamic_bundle", {}), dict) and bool(st.session_state.context.get("c120_dynamic_bundle", {}).get("ok"))

# v3.5 deployment guard: show required V6/C120 profile lookup before Step 3.
with st.expander("Scoring profile file status", expanded=False):
    _profile_status_preview = profile_file_status()
    safe_st_dataframe(_profile_status_preview, use_container_width=True, hide_index=True, height=360)
    _missing_profiles_preview = _profile_status_preview[(_profile_status_preview["required"]) & (~_profile_status_preview["exists"] | (_profile_status_preview["size_bytes"] <= 0))]["file"].astype(str).tolist()
    if _missing_profiles_preview:
        st.error("Required scoring profile files missing: " + ", ".join(_missing_profiles_preview))
    else:
        st.success("Required scoring profile files are visible to the deployed app.")

st.divider()
st.subheader("Step 3 — historical-score-driven core selection / core elimination")
st.write("Step 3 is the first watched-8 stage. It now scores the Step 2 watched-core candidates first and recommends core elimination from the weakest historical core scores, not from prevalence/cost alone.")
if step2_stage is None:
    st.info("Apply Step 2 first.")
elif not c120_ready_for_scoring:
    st.warning("Run and pass the C120 dynamic preflight before Step 3 so C120 evidence is fresh and verified.")
else:
    step2_cores = sorted(step2_stage["rows"]["core"].dropna().astype(str).unique().tolist())
    profiled = [c for c in available_profile_cores() if c in step2_cores]
    default_pool = profiled if profiled else [c for c in sorted(WATCHED8) if c in step2_cores]
    st.caption(f"Step 2 currently contains {len(step2_cores)} cores. Profile/watched default pool contains {len(default_pool)} cores.")
    candidate_pool = st.multiselect(
        "Core pool to consider in Step 3",
        options=step2_cores,
        default=default_pool,
        help="The repaired path is built for the watched 8. Other cores can be selected, but rows without profile support will trigger the scoring audit.",
    )
    if not candidate_pool:
        st.error("Step 3 needs at least one candidate core.")
    else:
        with st.spinner("Building Step 3 historical score audit from the current Step 2 rows..."):
            ca_preview_base, step3_scored_preview, step3_hist_errors = historical_step3_core_audit(
                step2_stage["rows"],
                step1_stage["rows"] if step1_stage else pd.DataFrame(),
                candidate_pool,
                st.session_state.context.get("play_date", play_date),
                st.session_state.context.get("history_df", history_df_current),
                c120_dynamic_bundle=st.session_state.context.get("c120_dynamic_bundle"),
            )
        if step3_hist_errors:
            st.error("Step 3 historical scoring is not ready. The app will not perform a score-driven core cut until this is fixed.")
            for e in step3_hist_errors:
                st.write(f"- {e}")
        else:
            # Default count uses the older dynamic count only as a count suggestion; the actual removal order is historical score.
            old_ca_for_count = build_core_audit(step2_stage["rows"], step1_stage["rows"] if step1_stage else pd.DataFrame(), candidate_pool)
            old_default_count = len(default_dynamic_removed(old_ca_for_count)[0])
            max_remove = max(0, len(candidate_pool) - 1)
            default_remove_count = min(max_remove, old_default_count)
            remove_count = st.slider("Number of cores to eliminate", min_value=0, max_value=max_remove, value=default_remove_count, step=1)
            recommended_remove, ca_preview = recommend_removed_cores_historical(ca_preview_base, remove_count=remove_count)
            st.caption("Removal order is now historical-score-driven: lowest historical_strength_score cores are recommended first. Prevalence/cost appears only as audit context/tie support.")
            cprev1, cprev2, cprev3, cprev4 = st.columns(4)
            cprev1.metric("Candidate core pool", len(candidate_pool))
            cprev2.metric("Preview removed cores", len(recommended_remove))
            cprev3.metric("Preview kept cores", max(0, len(candidate_pool) - len(recommended_remove)))
            cprev4.metric("Step 3 scored rows", len(step3_scored_preview))
            with st.expander("Step 3 historical core audit / removal preview", expanded=True):
                show_cols = unique_existing_cols(ca_preview, [
                    "core", "STEP3_STATUS_PREVIEW", "historical_removal_rank", "historical_strength_score",
                    "historical_avg_score", "historical_median_score", "historical_max_score", "historical_top25_rows",
                    "safe_candidate_member_rows", "safe_streams_touched", "eliminated_candidate_member_rows", "eliminated_streams_touched",
                    "historical_no_rule_rows", "step3_basis"
                ])
                safe_st_dataframe(ca_preview[show_cols] if show_cols else ca_preview, use_container_width=True, hide_index=True, height=360)
            manual_remove = st.multiselect(
                "Exact cores to eliminate when Step 3 is applied",
                options=sorted(candidate_pool),
                default=recommended_remove,
                key=f"manual_remove_hist_{len(candidate_pool)}_{remove_count}_{','.join(recommended_remove)}",
            )
            if st.button("Apply Step 3 — historical score-driven core selection"):
                step3, core_audit = apply_step3_manual(step2_stage["rows"], step1_stage["rows"] if step1_stage else pd.DataFrame(), candidate_pool, manual_remove)
                hist_cols = [c for c in ["core", "historical_rows_scored", "historical_avg_score", "historical_median_score", "historical_max_score", "historical_total_score", "historical_top25_rows", "historical_no_rule_rows", "historical_strength_score", "historical_removal_rank", "step3_basis"] if c in ca_preview.columns]
                if hist_cols and not step3.empty:
                    step3 = step3.merge(ca_preview[hist_cols], on="core", how="left")
                core_audit = core_audit.merge(ca_preview[[c for c in hist_cols if c != "core"] + ["core"]], on="core", how="left") if hist_cols else core_audit
                st.session_state.stages = st.session_state.stages[:3]
                st.session_state.stages.append(stage_record(
                    "STEP3_CORE_SELECTION_HISTORICAL_SCORE_DRIVEN",
                    step3,
                    f"Historical-score-driven Step 3. Kept {max(0, len(candidate_pool) - len(manual_remove))} of {len(candidate_pool)} selected core-pool cores; removed: {', '.join(manual_remove) if manual_remove else 'none'}.",
                    "STEP3_HISTORICAL_SCORE_DRIVEN",
                ))
                st.session_state.context["core_audit"] = core_audit
                st.success("Step 3 applied with historical score-driven core audit. Step 4 one-list scoring/ranking is now available.")
                st.rerun()

st.divider()
st.subheader("Step 4 — one global best-play ranking, then bottom-up row cuts")
st.write("Step 4 scores every remaining play into one overall ranked list. Cuts remove the lowest-ranked plays first. There is no stream-only, core-only, member-only, core-protection, or stream-protection behavior in this step.")
ready, prof_status, missing_required = profile_ready()
with st.expander("Required scoring file status", expanded=bool(missing_required)):
    safe_st_dataframe(prof_status, use_container_width=True, hide_index=True, height=300)
if missing_required:
    st.error("SCORING NOT READY — missing required files: " + ", ".join(missing_required))

if step3_stage is None:
    st.info("Apply Step 3 first.")
elif missing_required:
    st.stop()
else:
    latest_step4 = get_latest_stage("STEP4")
    if latest_step4 is None:
        st.info("Start Step 4 by scoring the Step 3 rows. This creates the ranked base and does not reduce rows.")
        if st.button("Start Step 4 — score/rank Step 3 rows", type="primary"):
            prog = st.progress(0, text="Scoring Step 3 rows with required profiles...")
            try:
                scored = score_rows(
                    step3_stage["rows"],
                    st.session_state.context.get("play_date", play_date),
                    st.session_state.context.get("history_df", history_df_current),
                    c120_dynamic_bundle=st.session_state.context.get("c120_dynamic_bundle"),
                )
            except Exception as e:
                prog.empty()
                st.error(f"Step 4 scoring failed before any reduction: {e}")
                st.stop()
            family_audit = score_family_firing_audit(scored, st.session_state.context)
            strict_errors = strict_scoring_failures(scored, st.session_state.context)
            if strict_errors:
                prog.empty()
                st.error("Step 4 scoring produced a partial/unsafe rule set, so the app did not create a playlist.")
                for e in strict_errors:
                    st.write(f"- {e}")
                with st.expander("Scoring family firing audit", expanded=True):
                    safe_st_dataframe(family_audit, use_container_width=True, hide_index=True)
                st.stop()
            scored = add_branch_label(scored, "STEP4_SCORED_BASE")
            st.session_state.stages = st.session_state.stages[:4]
            st.session_state.stages.append(stage_record(
                "STEP4_SCORED_BASE",
                scored,
                "One global best-play score/rank created; no row reduction yet. Strict scoring-family audit passed.",
                "SCORE_RANK_ONLY_NO_REDUCTION_STRICT_FAMILY_AUDIT_PASS",
            ))
            st.session_state.context["score_family_audit"] = family_audit
            prog.progress(100, text="Step 4 scored/ranked base ready.")
            prog.empty()
            st.success("Step 4 ranked base created. Strict scoring-family audit passed. Now choose non-aggressive, aggressive, or manual bottom-up removal.")
            st.rerun()
    else:
        source_df = latest_step4["rows"]
        current_rows = len(source_df) if isinstance(source_df, pd.DataFrame) else 0
        st.caption(f"Current Step 4 source: {latest_step4['label']} | rows available for next bottom-up cut: {current_rows}")
        m4 = stage_metrics(source_df)
        c4a, c4b, c4c, c4d = st.columns(4)
        c4a.metric("Current Step 4 rows / plays", m4["Rows"])
        c4b.metric("Current streams", m4["Streams"])
        c4c.metric("Current cores", m4["Cores"])
        c4d.metric("Current members", m4["Members"])

        if "profile_final_member_score" not in source_df.columns:
            st.error("Step 4 rows are not scored. Reset Step 4 and start it again.")
        elif current_rows <= 0:
            st.warning("Step 4 has no rows to reduce.")
        else:
            cut_mode = st.radio(
                "Step 4 reduction mode",
                ["Non-aggressive: remove 10 worst rows", "Aggressive: remove 50 worst rows", "Manual: choose worst-row count"],
                horizontal=False,
            )
            if cut_mode.startswith("Non-aggressive"):
                rows_to_remove = min(STEP4_NON_AGGRESSIVE_CUT, max(0, current_rows - 1))
            elif cut_mode.startswith("Aggressive"):
                rows_to_remove = min(STEP4_AGGRESSIVE_CUT, max(0, current_rows - 1))
            else:
                rows_to_remove = st.number_input(
                    "Manual rows / plays to remove from the bottom in this pass",
                    min_value=0,
                    max_value=max(0, current_rows - 1),
                    value=min(STEP4_NON_AGGRESSIVE_CUT, max(0, current_rows - 1)),
                    step=1,
                )
                rows_to_remove = int(rows_to_remove)

            preview_df, removed_preview, preview_rule, skipped_due_to_protection = bottom_up_cut_preview(
                source_df,
                "profile_final_member_score",
                rows_to_remove,
                protect_cores=False,
                protect_streams=False,
            )
            preview_rule = preview_rule.replace("COREPROTECT_0_STREAMPROTECT_0", "RANK_ONLY_NO_PROTECTION")
            preview_df = add_branch_label(preview_df, preview_rule)
            removed_preview = add_branch_label(removed_preview, f"REMOVED_BY_{preview_rule}") if not removed_preview.empty else removed_preview

            pmet = stage_metrics(preview_df)
            p1, p2, p3, p4c = st.columns(4)
            p1.metric("Preview rows / plays", pmet["Rows"], delta=pmet["Rows"] - m4["Rows"])
            p2.metric("Preview streams", pmet["Streams"], delta=pmet["Streams"] - m4["Streams"])
            p3.metric("Preview cores", pmet["Cores"], delta=pmet["Cores"] - m4["Cores"])
            p4c.metric("Preview members", pmet["Members"], delta=pmet["Members"] - m4["Members"])
            st.caption(f"Preview rule: {preview_rule}")
            st.caption("Verified direction: removed rows are the lowest profile_final_member_score rows; strongest/highest ranked plays stay unless you cut far enough to reach them.")

            with st.expander("Preview — exact bottom-ranked rows selected for removal", expanded=True):
                if removed_preview.empty:
                    st.caption("No rows are selected for removal with the current settings.")
                else:
                    removed_sorted = removed_preview.sort_values(["profile_final_member_score", "profile_signal_score_only", "stream", "core", "boxed_member"], ascending=[True, True, True, True, True], kind="mergesort")
                    show_cols_removed = unique_existing_cols(removed_sorted, ["overall_play_rank", "stream", "seed", "core", "boxed_member", "profile_final_member_score", "profile_signal_score_only", "score_exact_stream_core_member", "score_files_fired", "branch_name"])
                    safe_st_dataframe(removed_sorted[show_cols_removed].head(500) if show_cols_removed else removed_sorted.head(500), use_container_width=True, hide_index=True, height=340)
                    if len(removed_sorted) > 500:
                        st.caption(f"Showing first 500 of {len(removed_sorted)} removed rows.")

            with st.expander("Preview — top of list that stays after this cut", expanded=False):
                kept_sorted = preview_df.sort_values(["profile_final_member_score", "profile_signal_score_only", "stream", "core", "boxed_member"], ascending=[False, False, True, True, True], kind="mergesort")
                show_cols = unique_existing_cols(kept_sorted, ["overall_play_rank", "stream", "seed", "core", "boxed_member", "profile_final_member_score", "profile_signal_score_only", "score_exact_stream_core_member", "score_files_fired", "branch_name"])
                safe_st_dataframe(kept_sorted[show_cols].head(500) if show_cols else kept_sorted.head(500), use_container_width=True, hide_index=True, height=340)

            with st.expander("Decision audit preview for rows being removed", expanded=False):
                audit_cols = unique_existing_cols(removed_preview, [
                    "overall_play_rank", "stream", "seed", "core", "boxed_member", "profile_final_member_score",
                    "score_stream_core_usable", "score_stream_core_top", "score_seed_trait_usable", "score_seed_trait_top",
                    "score_stream_seed_trait_usable", "score_stream_seed_trait_top", "score_streamrank_core_to_stream", "score_streamrank_stream_to_core",
                    "score_cadence", "score_signal_count_summary", "score_affinity_stream_core", "score_affinity_seed", "score_affinity_stream_seed",
                    "score_c120_core_matrix", "score_c120_member_matrix", "score_c120_replacement_matrix", "c120_trap_lane", "c120_source_file",
                    "score_exact_stream_core_member", "profile_member_role_score", "score_files_fired", "scoring_warning"
                ])
                safe_st_dataframe(removed_preview[audit_cols].head(500) if audit_cols else removed_preview.head(500), use_container_width=True, hide_index=True, height=360)

            a, b, c = st.columns(3)
            with a:
                if st.button("Apply Step 4 cut from bottom of ranked list", type="primary", disabled=(len(removed_preview) == 0)):
                    drop_final_stages()
                    reduction_no = count_stages("STEP4_REDUCTION") + 1
                    label = f"STEP4_REDUCTION_{reduction_no:02d}_{safe_slug(preview_rule)}"
                    st.session_state.stages.append(stage_record(
                        label,
                        add_branch_label(preview_df, label),
                        f"{cut_mode}; removed {len(removed_preview)} weakest rows of {current_rows} by one global profile_final_member_score ranking.",
                        preview_rule,
                    ))
                    removed_log = add_branch_label(removed_preview.copy(), f"REMOVED_BY_{label}") if not removed_preview.empty else pd.DataFrame()
                    if not removed_log.empty:
                        removed_log.insert(0, "step4_cut_label", label)
                        removed_log.insert(1, "step4_cut_rule", preview_rule)
                        removed_log.insert(2, "step4_cut_mode", cut_mode)
                        removed_log.insert(3, "rows_before_cut", current_rows)
                        removed_log.insert(4, "rows_after_cut", len(preview_df))
                        if isinstance(st.session_state.get("step4_removed_log"), pd.DataFrame) and not st.session_state.step4_removed_log.empty:
                            st.session_state.step4_removed_log = pd.concat([st.session_state.step4_removed_log, removed_log], ignore_index=True, sort=False)
                        else:
                            st.session_state.step4_removed_log = removed_log
                    st.success(f"Step 4 cut applied from the bottom: removed {len(removed_preview)} rows; {current_rows} → {len(preview_df)} rows. Removed rows were appended to permanent Step 4 removal audit.")
                    st.rerun()
            with b:
                step4_reductions = [s for s in st.session_state.stages if str(s.get("label", "")).startswith("STEP4_REDUCTION")]
                if st.button("Rollback last Step 4 cut", disabled=(len(step4_reductions) == 0)):
                    drop_final_stages()
                    new_stages = list(st.session_state.stages)
                    removed_label = "STEP4_REDUCTION"
                    for i in range(len(new_stages) - 1, -1, -1):
                        if str(new_stages[i].get("label", "")).startswith("STEP4_REDUCTION"):
                            removed_label = new_stages[i].get("label", "STEP4_REDUCTION")
                            del new_stages[i]
                            break
                    st.session_state.stages = new_stages
                    if isinstance(st.session_state.get("step4_removed_log"), pd.DataFrame) and not st.session_state.step4_removed_log.empty and "step4_cut_label" in st.session_state.step4_removed_log.columns:
                        st.session_state.step4_removed_log = st.session_state.step4_removed_log[~st.session_state.step4_removed_log["step4_cut_label"].astype(str).eq(str(removed_label))].copy()
                    st.success(f"Rolled back: {removed_label}; matching removed-row audit entries were removed.")
                    st.rerun()
            with c:
                if st.button("Reset Step 4 to scored base"):
                    drop_final_stages()
                    keep = []
                    for s in st.session_state.stages:
                        keep.append(s)
                        if str(s.get("label", "")).startswith("STEP4_SCORED_BASE"):
                            break
                    st.session_state.stages = keep
                    st.session_state.step4_removed_log = pd.DataFrame()
                    st.success("Step 4 reset to the scored/ranked base; all reductions and removed-row audit log removed.")
                    st.rerun()
st.divider()
st.subheader("Step 5 — build / display / print final playlist")
st.write("This uses the current Step 4 rows exactly as your final playlist. Step 5 does not reduce, filter, bottom-1, or re-rank beyond display sorting.")
latest_step4_for_final = get_latest_stage("STEP4")
if latest_step4_for_final is None:
    st.info("Start Step 4 first.")
else:
    final_source_rows = latest_step4_for_final["rows"]
    fmet = stage_metrics(final_source_rows)
    st.caption(f"Final source: {latest_step4_for_final['label']} | rows/plays: {fmet['Rows']} | streams: {fmet['Streams']} | cores: {fmet['Cores']} | members: {fmet['Members']}")
    if fmet["Rows"] > 200:
        st.warning("This is still a large playlist. You can continue Step 4 reductions before building the final playlist.")
    if st.button("Build / display final playlist", type="primary"):
        drop_final_stages()
        final = add_branch_label(final_source_rows.copy(), "FINAL_PLAYLIST_FROM_STEP4")
        st.session_state.stages.append(stage_record("FINAL_PLAYLIST_FROM_STEP4", final, f"Final playlist built directly from {latest_step4_for_final['label']}; no hidden extra reduction.", "FINAL_FROM_STEP4"))
        folder = save_session_to_daily_folder(st.session_state.stages, st.session_state.context, st.session_state.winner_audit, st.session_state.winner_summary, st.session_state.get("step4_removed_log", pd.DataFrame()))
        st.success(f"Final playlist built. Outputs saved to: {folder}")
        st.rerun()

final_stage = get_stage("FINAL")
current_stage = st.session_state.stages[-1] if st.session_state.stages else None
if current_stage is not None:
    st.subheader("Current rows")
    cur_df = current_stage["rows"]
    if current_stage["label"].startswith("FINAL"):
        printable = printable_playlist(cur_df)
        safe_st_dataframe(printable, use_container_width=True, hide_index=True, height=520)
    else:
        show_cols = unique_existing_cols(cur_df, ["stream", "seed", "core", "boxed_member", "profile_final_member_score", "branch_name"])
        safe_st_dataframe(cur_df[show_cols] if show_cols else cur_df, use_container_width=True, hide_index=True, height=360)

    st.subheader("Downloads")
    d1, d2, d3 = st.columns(3)
    with d1:
        st.download_button(
            "Download current CSV",
            (printable_playlist(cur_df) if current_stage["label"].startswith("FINAL") else cur_df).to_csv(index=False),
            file_name=dated_filename(safe_slug(current_stage["label"]), st.session_state.context, "csv"),
            mime="text/csv",
            key=f"download_current_csv_{current_stage['label']}",
        )
    with d2:
        st.download_button(
            "Download current TXT playlist",
            playlist_text(cur_df, st.session_state.context, current_stage["label"]),
            file_name=dated_filename("PRINTABLE_CURRENT_PLAYLIST", st.session_state.context, "txt"),
            mime="text/plain",
            key=f"download_current_txt_{current_stage['label']}",
        )
    with d3:
        zbytes = zip_outputs(st.session_state.stages, st.session_state.winner_audit, st.session_state.winner_summary, st.session_state.context, st.session_state.get("step4_removed_log", pd.DataFrame()))
        st.download_button(
            "Download full session ZIP",
            data=zbytes,
            file_name=dated_filename("P4_MIRROR_LADDER_SESSION_OUTPUTS", st.session_state.context, "zip"),
            mime="application/zip",
            key=f"download_session_zip_{len(st.session_state.stages)}",
        )

st.subheader("Optional winner audit")
if winner_upload is not None and st.session_state.stages:
    winners = parse_uploaded_table(winner_upload, default_date=play_date)
    w = prep_winner_df(winners, st.session_state.context.get("excluded_states", excluded_states))
    all_a, summary = audit_all_stages(w, st.session_state.stages)
    st.session_state.winner_audit = all_a
    st.session_state.winner_summary = summary
    if summary.empty:
        st.warning("No watched/profiled AABC winner events found in the uploaded winner file, or no audit rows produced.")
    else:
        safe_st_dataframe(summary, use_container_width=True, hide_index=True)
    with st.expander("Full winner audit rows", expanded=False):
        safe_st_dataframe(all_a, use_container_width=True, hide_index=True, height=380)
else:
    st.caption("Upload a winner list after building stages to audit trapped boxed/core winners.")

st.caption("Important count check: Step 1 keeps FULL120 for enumeration only; C120 dynamic preflight rebuilds fresh daily evidence; Step 3 moves to watched 8 cores; Step 4 uses one overall best-play rank and cuts weakest rows first with rollback; Step 5 only builds/downloads the playlist.")
