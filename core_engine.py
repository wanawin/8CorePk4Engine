#!/usr/bin/env python3
"""
C120_v2_4_TRAP_BROWSER_UI

Purpose:
    Preserve the daily candidate matrix for tracking, calculate core/member features,
    apply only SAFE stream-skip scenarios, calculate member-level sum/spread filters,
    and replace deleted plays with the next most efficient candidates until the requested
    play cutoff is reached.

Important:
    This build can accept either a candidate matrix/ledger OR a raw history file plus rule library.
    In full mode, full_pipeline.py builds the rule/profile matrix from history first, then this engine
    preserves matrix rows/axes, expands members, applies safe stream skips and replacement logic.

Locked definitions:
    core = sorted 3 unique digits from an AABC/single-double result.
    member = sorted 4-digit boxed AABC value, leading zeros preserved.
    permutation/straight = exact 4-digit order; not selected in this build.
"""
from __future__ import annotations

import io, json, math, re, zipfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
try:
    # Pandas 3 / future string dtype can reject numeric assignments into imported text columns.
    # Keep imported tables as object dtype, then explicitly convert numeric columns where needed.
    pd.set_option("future.infer_string", False)
except Exception:
    pass

def force_object_df(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with extension string dtypes converted to plain object dtype.
    This prevents pandas StringDtype/str columns from failing when the engine later writes
    numeric scores, ranks, booleans, or replacement flags into columns that came from CSV/TXT.
    """
    if df is None:
        return df
    out = df.copy()
    for c in out.columns:
        try:
            dt = str(out[c].dtype).lower()
            if dt in {"string", "str"} or "string" in dt:
                out[c] = out[c].astype("object")
        except Exception:
            pass
    return out

BUILD = "C120_v2_4_TRAP_BROWSER_UI"
EIGHT_CORES = {"457", "067", "389", "027", "679", "138", "145", "567"}

SAFE_SKIP_SCENARIOS = {
    "none": "No stream skip.",
    "tier1_eooo": "Skip stream if seed_parity_pattern = EOOO.",
    "tier2_eooo_or_triple": "Skip stream if seed_parity_pattern = EOOO OR prior seed was AAAB/triple. Tested safest default.",
    "tier5_eooo_or_triple_or_last2_8core": "Tier2 plus last two same-stream AABC cores both from the 8-core group. Optional; adds little but tested safe/light.",
}
FORMULA_SCENARIOS = {
    "app_only": "App/profile order only.",
    "core_spread": "App/profile + core spread support. Current tested default at 120 core-play zone.",
    "core_spread_member_soft": "App/profile + core spread + soft member sum/spread support.",
}
MEMBER_DELETE_PRESETS = {
    "none": "No hard member deletion. Member rules affect ranking only.",
    "close_sum_le1": "Delete member if abs(member_sum - seed_sum) <= 1.",
    "close_sum_le2": "Delete member if abs(member_sum - seed_sum) <= 2. Aggressive; use only for testing.",
    "same_spread_and_close_sum_le2": "Delete member if same spread AND abs sum delta <= 2.",
    "low_member_score_lt_minus1": "Delete member if member feasibility score < -1.0.",
}

# ---------------- basic normalization ----------------
def _only_digits(x: object) -> str:
    if pd.isna(x):
        return ""
    return re.sub(r"\D", "", str(x).replace(".0", ""))

def norm4(x: object) -> str:
    s = _only_digits(x)
    return s.zfill(4)[-4:] if s else ""

def norm4_series(s: pd.Series) -> pd.Series:
    return s.fillna("").astype(str).str.replace(r"\.0$", "", regex=True).str.replace(r"\D", "", regex=True).str.zfill(4).str[-4:]

def norm_core(x: object) -> str:
    s = _only_digits(x)
    if not s:
        return ""
    return "".join(sorted(set(s))).zfill(3)[-3:]

def normcore_series(s: pd.Series) -> pd.Series:
    return s.map(norm_core)

def core_members(core: object) -> List[str]:
    c = norm_core(core)
    if len(c) != 3:
        return []
    ds = sorted(list(c))
    return ["".join(sorted(ds + [d])) for d in ds]

def spread_str(s: object) -> float:
    digs = [int(c) for c in _only_digits(s)]
    return float(max(digs)-min(digs)) if digs else np.nan

def structure4_str(s: object) -> str:
    v = norm4(s)
    if not v:
        return ""
    counts = tuple(sorted([v.count(d) for d in set(v)], reverse=True))
    return {(4,):"AAAA", (3,1):"AAAB", (2,2):"AABB", (2,1,1):"AABC", (1,1,1,1):"ABCD"}.get(counts,"OTHER")

def digroot(v: object) -> float:
    try:
        if pd.isna(v): return np.nan
        n = int(v)
        return 0 if n == 0 else 1 + ((n-1)%9)
    except Exception:
        return np.nan

def seed_parity_pattern(seed4: str) -> str:
    return "".join("E" if int(c)%2==0 else "O" for c in seed4) if seed4 else ""

def coerce_date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce").dt.strftime("%Y-%m-%d")

def find_col(columns: Iterable[str], aliases: Iterable[str]) -> Optional[str]:
    lower={c.lower().strip():c for c in columns}
    for a in aliases:
        if a.lower().strip() in lower:
            return lower[a.lower().strip()]
    return None

# ---------------- file readers ----------------
def read_any_table(path_or_bytes, filename: Optional[str]=None) -> pd.DataFrame:
    def read_one(name, data):
        low=name.lower()
        if low.endswith(".parquet"):
            return force_object_df(pd.read_parquet(io.BytesIO(data)))
        if low.endswith(".csv"):
            return force_object_df(pd.read_csv(io.BytesIO(data), dtype=object))
        if low.endswith(".tsv"):
            return force_object_df(pd.read_csv(io.BytesIO(data), dtype=object, sep="\t"))
        if low.endswith(".txt"):
            try:
                return force_object_df(pd.read_csv(io.BytesIO(data), dtype=object))
            except Exception:
                return force_object_df(pd.read_csv(io.BytesIO(data), dtype=object, sep="\t"))
        raise ValueError(f"Unsupported file type: {name}")
    if isinstance(path_or_bytes,(str,Path)):
        p=Path(path_or_bytes); name=filename or p.name
        if p.suffix.lower()==".zip":
            with zipfile.ZipFile(p,"r") as z: return read_candidate_from_zip(z)
        return read_one(name,p.read_bytes())
    data=path_or_bytes.read() if hasattr(path_or_bytes,"read") else path_or_bytes
    name=filename or "uploaded.csv"
    if name.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data),"r") as z: return read_candidate_from_zip(z)
    return read_one(name,data)

def read_candidate_from_zip(z: zipfile.ZipFile) -> pd.DataFrame:
    candidates=[]
    preferred_names=["matrix","candidate","ledger","CORE_CANDIDATES","VISIBLE_DAILY_MATRIX","APP_TOP3","TOP4"]
    for name in z.namelist():
        low=name.lower()
        if name.endswith("/") or not (low.endswith(".csv") or low.endswith(".txt") or low.endswith(".tsv") or low.endswith(".parquet")):
            continue
        try:
            df=read_any_table(io.BytesIO(z.read(name)), filename=name)
            cols={c.lower() for c in df.columns}
            key_like=any(c in cols for c in ["target_core","candidate_core","core"])
            stream_like=any(c in cols for c in ["stream","streamname","stream_name"])
            date_like=any(c in cols for c in ["draw_date","date","play_date"])
            score_like=any(c in cols for c in ["final_stream_core_score","profile_weight_sum","evidence_score","stream_core_rank_by_deep_score","score"])
            pref=sum(1 for p in preferred_names if p.lower() in low)
            if key_like and stream_like and date_like:
                candidates.append((10*int(score_like)+pref, len(df), name, df))
        except Exception:
            pass
    if not candidates:
        raise ValueError("No candidate/matrix-like table found in zip. Need date, stream, target_core and preferably score/rank.")
    candidates.sort(key=lambda t:(t[0],t[1]), reverse=True)
    return candidates[0][3]

# ---------------- input normalization ----------------
def normalize_candidate_input(df: pd.DataFrame) -> Tuple[pd.DataFrame,pd.DataFrame]:
    df=force_object_df(df)
    original_cols=list(df.columns)
    ren={}
    mappings={
        "draw_date":["draw_date","date","play_date","DRAW_DATE","Draw Date"],
        "stream":["stream","Stream","stream_name","StreamName","game_stream","name"],
        "seed":["seed","Seed","seed4","prev_result","previous_result","prev_seed_same_stream"],
        "target_core":["target_core","candidate_core","core","Core","CORE"],
        "final_stream_core_score":["final_stream_core_score","profile_weight_sum","evidence_score","score","FitScore","fit_score"],
        "stream_core_rank_by_deep_score":["stream_core_rank_by_deep_score","app_core_rank","rank","StreamRank"],
    }
    for out, aliases in mappings.items():
        col=find_col(df.columns,aliases)
        if col and col!=out: ren[col]=out
    df=force_object_df(df.rename(columns=ren))
    audit=[]
    required=["draw_date","stream","seed","target_core"]
    for col in required:
        audit.append({"field":col,"status":"FOUND" if col in df.columns else "MISSING","note":"required"})
    if "final_stream_core_score" not in df.columns and "stream_core_rank_by_deep_score" not in df.columns:
        audit.append({"field":"final_stream_core_score or stream_core_rank_by_deep_score","status":"MISSING","note":"need score/rank"})
    missing=[r["field"] for r in audit if r["status"]=="MISSING"]
    if missing:
        raise ValueError("Missing required fields: "+", ".join(missing))
    df["draw_date"]=coerce_date(df["draw_date"])
    df=df[df["draw_date"].notna()].copy()
    df["stream"]=df["stream"].astype(str).str.strip()
    df["seed"]=norm4_series(df["seed"])
    df["target_core"]=normcore_series(df["target_core"])
    df=df[df["stream"].ne("") & df["target_core"].ne("")].copy()
    if "final_stream_core_score" in df.columns:
        df["final_stream_core_score"]=pd.to_numeric(df["final_stream_core_score"], errors="coerce").fillna(0.0)
    else:
        df["stream_core_rank_by_deep_score"]=pd.to_numeric(df["stream_core_rank_by_deep_score"], errors="coerce")
        df["final_stream_core_score"]=-df["stream_core_rank_by_deep_score"].fillna(999999)
    for c in ["actual_core","truth_actual_core"]:
        if c in df.columns: df[c+"_norm"]=normcore_series(df[c])
    for c in ["actual_member","truth_actual_member","base4_norm"]:
        if c in df.columns: df[c+"_norm"]=norm4_series(df[c])
    audit.extend([
        {"field":"BUILD","status":"INFO","note":BUILD},
        {"field":"original_columns","status":"INFO","note":json.dumps(original_cols[:120])},
        {"field":"rows_after_normalization","status":"INFO","note":str(len(df))},
        {"field":"date_range","status":"INFO","note":f"{df['draw_date'].min()} through {df['draw_date'].max()}"},
    ])
    return df,pd.DataFrame(audit)

def normalize_history_input(hist: pd.DataFrame) -> pd.DataFrame:
    hist=force_object_df(hist)
    ren={}
    for out, aliases in {"draw_date":["draw_date","date","Draw Date","DATE"],"stream":["stream","Stream","stream_name","StreamName","game_stream","name"],"result":["result","base4","base4_norm","winning_number","winner","digits","number"]}.items():
        col=find_col(hist.columns,aliases)
        if col and col!=out: ren[col]=out
    hist=force_object_df(hist.rename(columns=ren))
    if not {"draw_date","stream","result"}.issubset(hist.columns):
        raise ValueError("History file needs date/stream/result columns.")
    hist["draw_date"]=coerce_date(hist["draw_date"])
    hist["stream"]=hist["stream"].astype(str).str.strip()
    hist["result4"]=norm4_series(hist["result"])
    hist=hist[hist["draw_date"].notna() & hist["result4"].str.len().eq(4)].copy()
    hist["result_structure"]=hist["result4"].map(structure4_str)
    hist["aabc_core"]=hist["result4"].map(lambda x: norm_core(x) if structure4_str(x)=="AABC" else "")
    return hist.sort_values(["stream","draw_date"]).reset_index(drop=True)

def fill_prior_aabc_from_history(cand: pd.DataFrame, hist: pd.DataFrame) -> pd.DataFrame:
    cand=cand.copy()
    for col in ["prev_seed_same_stream","last1_aabc_core","last2_aabc_core","last3_aabc_core"]:
        if col not in cand.columns: cand[col]=""
    hist_by_stream={s:g.sort_values("draw_date") for s,g in hist.groupby("stream")}
    fill=[]
    for idx,r in cand.iterrows():
        g=hist_by_stream.get(r["stream"])
        if g is None:
            fill.append((idx,"","","","")); continue
        prior=g[g["draw_date"] < r["draw_date"]]
        prev_seed=prior["result4"].iloc[-1] if len(prior) else ""
        prior_aabc=prior[prior["aabc_core"].ne("")]
        last=prior_aabc["aabc_core"].tail(3).tolist()[::-1] + [""]*3
        fill.append((idx,prev_seed,last[0],last[1],last[2]))
    f=pd.DataFrame(fill, columns=["_idx","_prev","_l1","_l2","_l3"]).set_index("_idx")
    for target,src in [("prev_seed_same_stream","_prev"),("last1_aabc_core","_l1"),("last2_aabc_core","_l2"),("last3_aabc_core","_l3")]:
        cand[target]=cand[target].astype("object")
        existing=cand[target].fillna("").astype(str).str.replace(r"\.0$","",regex=True)
        cand.loc[existing.eq("") | existing.eq("nan"), target]=f[src].astype("object")
    return cand

@dataclass
class EngineSettings:
    play_date_mode: str = "latest"          # latest or all
    play_date: Optional[str] = None
    cutoff_per_day: int = 80                 # member plays/day target by default
    top_n_cores_per_stream: int = 4
    candidate_scope: str = "8cores"
    skip_scenario: str = "tier2_eooo_or_triple"
    formula: str = "core_spread_member_soft"
    member_delete_preset: str = "none"
    replacement_enabled: bool = True

# ---------------- feature/scoring ----------------
def add_core_features(df: pd.DataFrame, settings: EngineSettings) -> pd.DataFrame:
    d=force_object_df(df)
    d["target_core"]=normcore_series(d["target_core"])
    if settings.candidate_scope == "8cores":
        d=d[d["target_core"].isin(EIGHT_CORES)].copy()
    d=d.sort_values(["draw_date","stream","final_stream_core_score","target_core"], ascending=[True,True,False,True]).copy()
    d["app_core_rank"]=d.groupby(["draw_date","stream"]).cumcount()+1
    d=d[d["app_core_rank"].le(int(settings.top_n_cores_per_stream))].copy()
    d["app_topN_row"] = d.groupby("draw_date").cumcount()+1
    # Preserve matrix axes when present; otherwise synthesize honest placeholders.
    if "matrix_rank_in_stream" not in d.columns:
        d["matrix_rank_in_stream"] = d["app_core_rank"]
    if "daily_opportunity_rank" not in d.columns:
        d["daily_opportunity_rank"] = d["app_topN_row"]
    d["seed4"]=norm4_series(d["seed"])
    d["seed_sum"]=d["seed4"].map(lambda s: sum(map(int,s)) if s else np.nan)
    d["seed_spread"]=d["seed4"].map(spread_str)
    d["seed_structure"]=d["seed4"].map(structure4_str)
    d["seed_parity_pattern"]=d["seed4"].map(seed_parity_pattern)
    for c in ["last1_aabc_core","last2_aabc_core","last3_aabc_core"]:
        if c not in d.columns: d[c]=""
        d[c+"_norm"]=normcore_series(d[c])
    d["skip_seed_parity_EOOO"]=d["seed_parity_pattern"].eq("EOOO")
    d["skip_seed_AAAB_triple"]=d["seed_structure"].eq("AAAB")
    d["skip_last2_same_stream_8core"]=d["last1_aabc_core_norm"].isin(EIGHT_CORES) & d["last2_aabc_core_norm"].isin(EIGHT_CORES)
    d["SKIP_NONE"]=False
    d["SKIP_TIER1_EOOO"]=d["skip_seed_parity_EOOO"]
    d["SKIP_TIER2_EOOO_OR_TRIPLE"]=d["skip_seed_parity_EOOO"] | d["skip_seed_AAAB_triple"]
    d["SKIP_TIER5_EOOO_OR_TRIPLE_OR_LAST2_SAME_STREAM_8CORE"]=d["SKIP_TIER2_EOOO_OR_TRIPLE"] | d["skip_last2_same_stream_8core"]
    d["core_spread"]=d["target_core"].map(spread_str)
    d["spread_delta"]=d["core_spread"]-d["seed_spread"]
    d["abs_spread_delta"]=d["spread_delta"].abs()
    d["same_spread"]=d["abs_spread_delta"].eq(0)
    d["abs_spread_delta_le_2"]=d["abs_spread_delta"].le(2)
    d["abs_spread_delta_le_3"]=d["abs_spread_delta"].le(3)
    d["seed_spread_high_ge7"]=d["seed_spread"].ge(7)
    d["seed_spread_low_le3"]=d["seed_spread"].le(3)
    d["core_spread_high_ge7"]=d["core_spread"].ge(7)
    d["core_spread_low_le3"]=d["core_spread"].le(3)
    d["manual_spread_score"]=(
        -0.65*d["same_spread"].astype(float)
        +0.25*d["abs_spread_delta_le_2"].astype(float)
        +0.20*d["abs_spread_delta_le_3"].astype(float)
        -0.20*(d["seed_spread_high_ge7"] & d["core_spread_low_le3"]).astype(float)
        -0.20*(d["seed_spread_low_le3"] & d["core_spread_high_ge7"]).astype(float)
    )
    return d

def apply_stream_skip(core: pd.DataFrame, settings: EngineSettings) -> pd.DataFrame:
    d=force_object_df(core)
    col={
        "none":"SKIP_NONE",
        "tier1_eooo":"SKIP_TIER1_EOOO",
        "tier2_eooo_or_triple":"SKIP_TIER2_EOOO_OR_TRIPLE",
        "tier5_eooo_or_triple_or_last2_8core":"SKIP_TIER5_EOOO_OR_TRIPLE_OR_LAST2_SAME_STREAM_8CORE",
    }.get(settings.skip_scenario)
    if col is None: raise ValueError(f"Unknown skip_scenario: {settings.skip_scenario}")
    d["stream_skip_flag"]=d[col].astype(bool)
    reasons=[]
    for _,r in d.iterrows():
        p=[]
        if r.get("skip_seed_parity_EOOO",False): p.append("EOOO seed parity")
        if r.get("skip_seed_AAAB_triple",False): p.append("prior seed AAAB/triple")
        if r.get("skip_last2_same_stream_8core",False): p.append("last2 same-stream AABC cores both in 8-core group")
        reasons.append("; ".join(p))
    d["stream_skip_reason"]=reasons
    return d

def expand_members(core: pd.DataFrame) -> pd.DataFrame:
    rows=[]
    for _,r in core.iterrows():
        for m in core_members(r["target_core"]):
            rr=r.to_dict(); rr["candidate_member"]=m; rows.append(rr)
    mem=pd.DataFrame(rows)
    if mem.empty: return mem
    mem["member_sum"]=mem["candidate_member"].map(lambda s: sum(map(int,s)))
    mem["member_spread"]=mem["candidate_member"].map(spread_str)
    mem["sum_delta"]=mem["member_sum"]-mem["seed_sum"]
    mem["abs_sum_delta"]=mem["sum_delta"].abs()
    mem["member_sum_dr"]=mem["member_sum"].map(digroot)
    mem["seed_sum_dr"]=mem["seed_sum"].map(digroot)
    mem["same_digital_root_sum"]=mem["member_sum_dr"].eq(mem["seed_sum_dr"])
    mem["member_sum_last_digit"]=(mem["member_sum"].astype(int)%10).astype(str)
    mem["seed_sum_last_digit"]=(mem["seed_sum"].fillna(-1).astype(int)%10).astype(str)
    member_digit_sets=[set(norm4(x)) for x in mem["candidate_member"]]
    seed_digit_sets=[set(norm4(x)) for x in mem["seed4"]]
    mem["sum_delta_plus9"]=mem["sum_delta"].eq(9)
    mem["sum_delta_minus9"]=mem["sum_delta"].eq(-9)
    mem["sum_delta_plus10"]=mem["sum_delta"].eq(10)
    mem["sum_delta_minus10"]=mem["sum_delta"].eq(-10)
    mem["sum_delta_plus18"]=mem["sum_delta"].eq(18)
    mem["sum_delta_minus18"]=mem["sum_delta"].eq(-18)
    mem["abs_sum_delta_le_1"]=mem["abs_sum_delta"].le(1)
    mem["abs_sum_delta_le_2"]=mem["abs_sum_delta"].le(2)
    mem["abs_sum_delta_le_3"]=mem["abs_sum_delta"].le(3)
    mem["seed_sum_ld_in_member"]=[d in digs for d,digs in zip(mem["seed_sum_last_digit"], member_digit_sets)]
    mem["mirror_seed_sum_ld_in_member"]=[str((int(d)+5)%10) in digs if d.isdigit() else False for d,digs in zip(mem["seed_sum_last_digit"], member_digit_sets)]
    mem["member_sum_ld_in_seed"]=[d in digs for d,digs in zip(mem["member_sum_last_digit"], seed_digit_sets)]
    mem["mirror_member_sum_ld_in_seed"]=[str((int(d)+5)%10) in digs if d.isdigit() else False for d,digs in zip(mem["member_sum_last_digit"], seed_digit_sets)]
    mem["manual_sum_score"]=(
        -0.90*mem["abs_sum_delta_le_1"].astype(float)
        -0.60*mem["abs_sum_delta_le_2"].astype(float)
        -0.35*mem["abs_sum_delta_le_3"].astype(float)
        +0.55*(mem["sum_delta_plus9"]|mem["sum_delta_minus9"]).astype(float)
        +0.30*(mem["sum_delta_plus10"]|mem["sum_delta_minus10"]).astype(float)
        +0.25*(mem["sum_delta_plus18"]|mem["sum_delta_minus18"]).astype(float)
        +0.20*mem["seed_sum_ld_in_member"].astype(float)
        +0.15*mem["mirror_seed_sum_ld_in_member"].astype(float)
        +0.10*mem["same_digital_root_sum"].astype(float)
    )
    mem["member_feasibility_score"]=mem["manual_sum_score"] + 0.25*mem["manual_spread_score"]
    mem["member_filter_reason"]=mem.apply(member_reason, axis=1)
    mem=mem.sort_values(["draw_date","stream","target_core","member_feasibility_score","candidate_member"], ascending=[True,True,True,False,True]).copy()
    mem["member_rank_within_core"]=mem.groupby(["draw_date","stream","target_core"]).cumcount()+1
    return mem

def member_reason(r: pd.Series) -> str:
    parts=[]
    if r.get("abs_sum_delta_le_1",False): parts.append("abs_sum_delta<=1 strong penalty")
    elif r.get("abs_sum_delta_le_2",False): parts.append("abs_sum_delta<=2 penalty")
    elif r.get("abs_sum_delta_le_3",False): parts.append("abs_sum_delta<=3 penalty")
    if r.get("sum_delta_plus9",False) or r.get("sum_delta_minus9",False): parts.append("±9 sum support")
    if r.get("sum_delta_plus10",False) or r.get("sum_delta_minus10",False): parts.append("±10 sum support")
    if r.get("sum_delta_plus18",False) or r.get("sum_delta_minus18",False): parts.append("±18 sum support")
    if r.get("seed_sum_ld_in_member",False): parts.append("seed-sum last digit in member")
    if r.get("mirror_seed_sum_ld_in_member",False): parts.append("mirror seed-sum last digit in member")
    if r.get("same_spread",False): parts.append("same-spread core penalty")
    return "; ".join(parts) if parts else "neutral member sum/spread"

def apply_member_deletion(mem: pd.DataFrame, preset: str) -> pd.DataFrame:
    d=force_object_df(mem)
    if preset not in MEMBER_DELETE_PRESETS: raise ValueError(f"Unknown member_delete_preset: {preset}")
    d["member_delete_flag"]=False
    d["member_delete_reason"]=""
    if preset=="close_sum_le1":
        d["member_delete_flag"]=d["abs_sum_delta_le_1"]
        d.loc[d["member_delete_flag"],"member_delete_reason"]="abs_sum_delta<=1"
    elif preset=="close_sum_le2":
        d["member_delete_flag"]=d["abs_sum_delta_le_2"]
        d.loc[d["member_delete_flag"],"member_delete_reason"]="abs_sum_delta<=2"
    elif preset=="same_spread_and_close_sum_le2":
        d["member_delete_flag"]=d["same_spread"] & d["abs_sum_delta_le_2"]
        d.loc[d["member_delete_flag"],"member_delete_reason"]="same_spread AND abs_sum_delta<=2"
    elif preset=="low_member_score_lt_minus1":
        d["member_delete_flag"]=d["member_feasibility_score"].lt(-1.0)
        d.loc[d["member_delete_flag"],"member_delete_reason"]="member_feasibility_score<-1.0"
    return d

def score_member_matrix(mem: pd.DataFrame, settings: EngineSettings) -> pd.DataFrame:
    d=force_object_df(mem)
    # app rank dominates, but member and spread move plays within the candidate universe.
    d["score_app_core_rank"]=(settings.top_n_cores_per_stream + 1 - d["app_core_rank"].astype(float))*10.0
    d["score_final_stream_component"] = pd.to_numeric(d["final_stream_core_score"], errors="coerce").rank(pct=True) if len(d) else 0.0
    d["core_formula_app_only"]=d["score_app_core_rank"]
    d["core_formula_core_spread"]=d["score_app_core_rank"] + d["manual_spread_score"]
    d["core_formula_core_spread_member_soft"]=d["score_app_core_rank"] + d["manual_spread_score"] + 0.50*d["member_feasibility_score"].fillna(0)
    score_col={"app_only":"core_formula_app_only","core_spread":"core_formula_core_spread","core_spread_member_soft":"core_formula_core_spread_member_soft"}.get(settings.formula)
    if score_col is None: raise ValueError(f"Unknown formula: {settings.formula}")
    d["final_member_score"]=d[score_col]
    d["play_key"]=d["draw_date"].astype(str)+"|"+d["stream"].astype(str)+"|"+d["target_core"].astype(str)+"|"+d["candidate_member"].astype(str)
    d=d.sort_values(["draw_date","final_member_score","final_stream_core_score","stream","target_core","candidate_member"], ascending=[True,False,False,True,True,True]).copy()
    d["daily_member_matrix_rank"] = d.groupby("draw_date").cumcount()+1
    return d

def add_hit_flags(df: pd.DataFrame) -> pd.DataFrame:
    d=force_object_df(df)
    actual_core_col=None
    for c in ["truth_actual_core_norm","actual_core_norm","truth_actual_core","actual_core"]:
        if c in d.columns:
            if not c.endswith("_norm"):
                d[c+"_norm"]=normcore_series(d[c]); c=c+"_norm"
            actual_core_col=c; break
    actual_member_col=None
    for c in ["truth_actual_member_norm","actual_member_norm","base4_norm_norm","actual_member","base4_norm"]:
        if c in d.columns:
            if not c.endswith("_norm"):
                d[c+"_norm"]=norm4_series(d[c]); c=c+"_norm"
            actual_member_col=c; break
    d["core_hit_audit"]=d["target_core"].eq(d[actual_core_col]) if actual_core_col else False
    d["member_hit_audit"]=d["candidate_member"].eq(d[actual_member_col]) if actual_member_col else False
    return d

# ---------------- selection/replacement ----------------
def select_daily_playlist(candidates: pd.DataFrame, history: Optional[pd.DataFrame]=None, settings: Optional[EngineSettings]=None) -> Dict[str,pd.DataFrame]:
    settings=settings or EngineSettings()
    cand,schema_audit=normalize_candidate_input(candidates)
    history_through="set by upstream candidate matrix"
    if history is not None:
        hist=normalize_history_input(history)
        cand=fill_prior_aabc_from_history(cand,hist)
        history_through=str(hist["draw_date"].max()) if len(hist) else "uploaded history empty"
        schema_audit=pd.concat([schema_audit,pd.DataFrame([{"field":"history_prior_fields","status":"INFO","note":"Filled blank prior fields from history."}])], ignore_index=True)
    core=add_core_features(cand,settings)
    all_dates=sorted(core["draw_date"].dropna().unique())
    if settings.play_date_mode=="all": selected_dates=all_dates
    else:
        if settings.play_date:
            dt=pd.to_datetime(settings.play_date, errors="coerce")
            if pd.isna(dt): raise ValueError(f"Bad play date: {settings.play_date}")
            selected_dates=[dt.strftime("%Y-%m-%d")]
        else:
            selected_dates=[all_dates[-1]] if all_dates else []
    core=core[core["draw_date"].isin(selected_dates)].copy()
    if core.empty: raise ValueError("No candidate rows left for selected date(s).")
    core=apply_stream_skip(core,settings)
    member=expand_members(core)
    member=apply_member_deletion(member, settings.member_delete_preset)
    member=score_member_matrix(member, settings)
    member=add_hit_flags(member)
    # Baseline topN before hard member deletion, but after stream skip. This creates replacement audit.
    baseline_pool=member[~member["stream_skip_flag"]].copy()
    baseline_top=baseline_pool.sort_values(["draw_date","final_member_score","final_stream_core_score","stream","target_core","candidate_member"], ascending=[True,False,False,True,True,True]).groupby("draw_date").head(settings.cutoff_per_day).copy()
    baseline_top["baseline_topN_rank"] = baseline_top.groupby("draw_date").cumcount()+1
    playable=member[(~member["stream_skip_flag"]) & (~member["member_delete_flag"])].copy()
    playable=playable.sort_values(["draw_date","final_member_score","final_stream_core_score","stream","target_core","candidate_member"], ascending=[True,False,False,True,True,True]).copy()
    playable["final_play_rank"] = playable.groupby("draw_date").cumcount()+1
    final=playable[playable["final_play_rank"].le(settings.cutoff_per_day)].copy()
    final["played_flag"]=True
    # annotate member matrix with final status
    final_keys=set(final["play_key"])
    baseline_keys=set(baseline_top["play_key"])
    member["baseline_topN_flag"]=member["play_key"].isin(baseline_keys)
    member["played_flag"]=member["play_key"].isin(final_keys)
    member["final_play_rank"]=""
    member["final_play_rank"]=member["final_play_rank"].astype("object")
    rank_map=final.set_index("play_key")["final_play_rank"].to_dict()
    member.loc[member["played_flag"],"final_play_rank"]=member.loc[member["played_flag"],"play_key"].map(rank_map)
    # core matrix summary from member-level statuses
    core_matrix=core.copy()
    core_matrix["core_status"] = np.where(core_matrix["stream_skip_flag"],"STREAM_SKIPPED","CORE_CANDIDATE_TRACKED")
    core_matrix["app_matrix_definition"]="topN app/profile candidate core matrix; exact rows preserved for tracking"
    # replacement report
    deleted_baseline=baseline_top[baseline_top["member_delete_flag"]].copy()
    replacements=final[~final["play_key"].isin(baseline_keys)].copy()
    repl_rows=[]
    max_len=max(len(deleted_baseline),len(replacements))
    db=deleted_baseline.reset_index(drop=True); rp=replacements.reset_index(drop=True)
    for i in range(max_len):
        row={"replacement_pair_index":i+1}
        if i < len(db):
            for c in ["draw_date","baseline_topN_rank","stream","seed4","target_core","candidate_member","member_delete_reason","final_member_score"]:
                row["deleted_"+c]=db.loc[i,c] if c in db.columns else ""
        if i < len(rp):
            for c in ["draw_date","final_play_rank","stream","seed4","target_core","candidate_member","member_filter_reason","final_member_score"]:
                row["replacement_"+c]=rp.loc[i,c] if c in rp.columns else ""
        repl_rows.append(row)
    replacement_df=pd.DataFrame(repl_rows)
    # stream skip audit one row per stream/date
    stream_skip_audit=core.drop_duplicates(["draw_date","stream"])[["draw_date","stream","seed4","seed_structure","seed_parity_pattern","last1_aabc_core_norm","last2_aabc_core_norm","stream_skip_flag","stream_skip_reason"]].copy()
    summary=summarize(core,member,baseline_top,final,stream_skip_audit,settings)
    run_report=pd.DataFrame([
        {"field":"BUILD","value":BUILD},
        {"field":"HISTORY_THROUGH","value":history_through},
        {"field":"PLAY_DATE","value":";".join(selected_dates)},
        {"field":"CANDIDATE_SCOPE","value":settings.candidate_scope},
        {"field":"TOP_N_CORES_PER_STREAM","value":settings.top_n_cores_per_stream},
        {"field":"PLAY_CUTOFF_PER_DAY","value":settings.cutoff_per_day},
        {"field":"SKIP_SCENARIO","value":settings.skip_scenario},
        {"field":"FORMULA","value":settings.formula},
        {"field":"MEMBER_DELETE_PRESET","value":settings.member_delete_preset},
        {"field":"MATRIX_NOTE","value":"DAILY_CORE_MATRIX_ALL_CANDIDATES and DAILY_MEMBER_MATRIX_ALL_CANDIDATES preserve exact matrix/rank fields for tracking, even when not selected."},
        {"field":"REPLACEMENT_NOTE","value":"Final playlist is filled from next most efficient non-deleted, non-skipped member candidates until cutoff, if enough candidates remain."},
        {"field":"FULL_APP_NOTE","value":"When run through full_pipeline.py/app_streamlit.py with history+rule library, this is full history-to-matrix-to-member-playlist mode. Cached matrix mode is also supported."},
    ])
    matrix_defs=pd.DataFrame([
        {"field":"app_core_rank","definition":"rank of candidate core inside stream after app/profile score; lower is better"},
        {"field":"app_topN_row","definition":"exact row in the daily TopN-core-per-stream candidate matrix"},
        {"field":"matrix_rank_in_stream","definition":"upstream matrix rank if provided; otherwise app_core_rank placeholder"},
        {"field":"daily_opportunity_rank","definition":"upstream daily/matrix row if provided; otherwise app_topN_row placeholder"},
        {"field":"daily_member_matrix_rank","definition":"exact ranked row after core/member sum-spread scoring before final cutoff"},
        {"field":"final_play_rank","definition":"final selected member play rank after skips, member deletion, and replacement"},
    ])
    return {
        "RUN_REPORT.csv":run_report,
        "RUN_SUMMARY.csv":pd.DataFrame([summary]),
        "INPUT_SCHEMA_AUDIT.csv":schema_audit,
        "MATRIX_ROW_DEFINITIONS.csv":matrix_defs,
        "STREAM_SKIP_AUDIT.csv":stream_skip_audit,
        "DAILY_CORE_MATRIX_ALL_CANDIDATES.csv":core_matrix,
        "DAILY_MEMBER_MATRIX_ALL_CANDIDATES.csv":member,
        "DAILY_MEMBER_PLAYLIST_TOPN.csv":final,
        "DAILY_MEMBER_DELETIONS_AND_REPLACEMENTS.csv":replacement_df,
    }

def summarize(core: pd.DataFrame, member: pd.DataFrame, baseline_top: pd.DataFrame, final: pd.DataFrame, stream_audit: pd.DataFrame, settings: EngineSettings) -> Dict[str,object]:
    days=sorted(core["draw_date"].unique())
    streams_total=core.drop_duplicates(["draw_date","stream"]).shape[0]
    streams_skipped=stream_audit[stream_audit["stream_skip_flag"]].drop_duplicates(["draw_date","stream"]).shape[0]
    deleted_total=int(member["member_delete_flag"].sum()) if "member_delete_flag" in member else 0
    baseline_deleted=int(baseline_top["member_delete_flag"].sum()) if len(baseline_top) else 0
    out={
        "BUILD":BUILD,"dates":";".join(days),"date_count":len(days),"cutoff_per_day":settings.cutoff_per_day,
        "top_n_cores_per_stream":settings.top_n_cores_per_stream,"skip_scenario":settings.skip_scenario,"formula":settings.formula,
        "member_delete_preset":settings.member_delete_preset,"streams_total":streams_total,"streams_skipped":streams_skipped,
        "streams_skipped_per_day":round(streams_skipped/max(len(days),1),3),"core_matrix_rows":len(core),"member_matrix_rows":len(member),
        "members_deleted_total":deleted_total,"baseline_topN_deleted_and_replaced":baseline_deleted,
        "final_plays_total":len(final),"final_plays_per_day":round(len(final)/max(len(days),1),3),
        "met_cutoff_each_day": bool((final.groupby("draw_date").size().reindex(days,fill_value=0) >= settings.cutoff_per_day).all()) if days else False,
    }
    if "core_hit_audit" in final.columns:
        daily_core=final.groupby("draw_date")["core_hit_audit"].sum().reindex(days, fill_value=0)
        daily_mem=final.groupby("draw_date")["member_hit_audit"].sum().reindex(days, fill_value=0)
        out.update({
            "audit_core_hits":int(final["core_hit_audit"].sum()),"audit_core_zero_days":int((daily_core==0).sum()),
            "audit_core_days_1plus":int((daily_core>=1).sum()),"audit_core_days_2plus":int((daily_core>=2).sum()),
            "audit_member_hits":int(final["member_hit_audit"].sum()),"audit_member_zero_days":int((daily_mem==0).sum()),
        })
    return out

# ---------------- output helpers ----------------
def make_printable_txt(final: pd.DataFrame, settings: EngineSettings) -> str:
    lines=[BUILD, f"PLAY CUTOFF: {settings.cutoff_per_day} member plays/day", f"SKIP: {settings.skip_scenario} | FORMULA: {settings.formula} | MEMBER DELETE: {settings.member_delete_preset}", "Definitions: core=sorted 3 unique digits; member=sorted boxed AABC 4-digit value; straight/permutation not selected here.", ""]
    if final.empty:
        lines.append("No final plays selected."); return "\n".join(lines)
    for date,g in final.groupby("draw_date"):
        lines.append(f"PLAY_DATE: {date}")
        lines.append("# | Stream | Seed | Core | Member | Score | Reason")
        lines.append("-"*120)
        for _,r in g.sort_values("final_play_rank").iterrows():
            lines.append(f"{int(r['final_play_rank']):03d} | {r['stream']} | {r['seed4']} | {r['target_core']} | {r['candidate_member']} | {float(r['final_member_score']):.3f} | {r.get('member_filter_reason','')}")
        lines.append("")
    return "\n".join(lines)

def outputs_to_zip_bytes(outputs: Dict[str,pd.DataFrame], settings: EngineSettings) -> bytes:
    bio=io.BytesIO()
    with zipfile.ZipFile(bio,"w",zipfile.ZIP_DEFLATED) as z:
        for name,df in outputs.items():
            z.writestr(name, df.to_csv(index=False))
        z.writestr("PRINTABLE_DAILY_MEMBER_PLAYLIST.txt", make_printable_txt(outputs["DAILY_MEMBER_PLAYLIST_TOPN.csv"],settings))
        z.writestr("ENGINE_SETTINGS.json", json.dumps(asdict(settings),indent=2))
    return bio.getvalue()

def write_outputs(outputs: Dict[str,pd.DataFrame], out_dir: str|Path, settings: EngineSettings) -> Path:
    out=Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    for name,df in outputs.items(): df.to_csv(out/name,index=False)
    (out/"PRINTABLE_DAILY_MEMBER_PLAYLIST.txt").write_text(make_printable_txt(outputs["DAILY_MEMBER_PLAYLIST_TOPN.csv"],settings), encoding="utf-8")
    (out/"ENGINE_SETTINGS.json").write_text(json.dumps(asdict(settings),indent=2), encoding="utf-8")
    zp=out/"DAILY_MATRIX_MEMBER_REPLACEMENT_RESULTS.zip"
    with zipfile.ZipFile(zp,"w",zipfile.ZIP_DEFLATED) as z:
        for p in sorted(out.glob("*")):
            if p.name!=zp.name: z.write(p,p.name)
    return zp
