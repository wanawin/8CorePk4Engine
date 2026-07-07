#!/usr/bin/env python3
"""
C120_v2_4_TRAP_BROWSER_UI

Full daily flow:
    raw updated history CSV/TXT + frozen/stable core rule library
    -> normalize/dedupe history
    -> latest same-stream seed per stream
    -> build full 120-core rule/profile matrix for PLAY_DATE
    -> preserve full matrix for tracking
    -> 8-core/top-N selector with safe stream skips
    -> member sum/spread rules
    -> hard-delete replacement until cutoff is met

Audit/cached flow:
    existing candidate matrix can still be supplied directly.
"""
from __future__ import annotations

import io, json, time, zipfile, shutil, subprocess, sys
from dataclasses import asdict
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd
try:
    pd.set_option("future.infer_string", False)
except Exception:
    pass

import rule_daily_portfolio_audit as rpa
from core_engine import EngineSettings, select_daily_playlist, write_outputs, outputs_to_zip_bytes, read_any_table, BUILD as CORE_BUILD

BUILD = "C120_v2_4_TRAP_BROWSER_UI"

class MiniStatus:
    def __init__(self, out_dir: Path):
        self.out_dir = Path(out_dir); self.out_dir.mkdir(parents=True, exist_ok=True); self.t0=time.time()
    def write(self, msg: str):
        elapsed=time.time()-self.t0
        txt=f"{BUILD}\nstatus={msg}\nelapsed_seconds={elapsed:.1f}\nupdated_at={time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        (self.out_dir/'00_LIVE_STATUS.txt').write_text(txt, encoding='utf-8')
        print(f"[{elapsed:8.1f}s] {msg}", flush=True)

def _read_table_from_upload_or_path(obj, filename: Optional[str]=None) -> pd.DataFrame:
    return read_any_table(obj, filename=filename)

def _coerce_history_for_rpa(history_obj, filename: Optional[str]=None) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    raw = _read_table_from_upload_or_path(history_obj, filename)
    hist, schema_audit = rpa.adapt_history(raw)
    seed_audit, seed_summary = rpa.build_seed_alignment_audit(hist)
    rpa.assert_seed_alignment_ok(seed_summary)
    return hist, schema_audit, seed_audit, seed_summary

def _load_rules(rules_obj, filename: Optional[str]=None) -> Tuple[pd.DataFrame, pd.DataFrame]:
    raw = _read_table_from_upload_or_path(rules_obj, filename)
    rules = rpa.normalize_rules(raw)
    return raw, rules

def _last_aabc_cores(hist: pd.DataFrame, stream: str, through_date: str, n: int=3):
    g = hist[(hist['stream'].eq(stream)) & (hist['draw_date'].le(through_date))].sort_values('draw_date')
    a = g[g['actual_core'].fillna('').astype(str).str.len().eq(3)]['actual_core'].tail(n).tolist()[::-1]
    return (a + ['']*n)[:n]

def build_daily_rule_matrix_from_history(
    history_obj,
    rules_obj,
    out_dir: str|Path,
    play_date: Optional[str]=None,
    history_through: Optional[str]=None,
    history_filename: Optional[str]=None,
    rules_filename: Optional[str]=None,
    config_name: str='ALL_BALANCED',
    write_csv: bool=True,
) -> Tuple[pd.DataFrame, Dict[str, pd.DataFrame]]:
    """Create one future play-date candidate event per stream and score all 120 cores.

    The seed for each stream is the latest history result through HISTORY_THROUGH. actual_core is blank
    because this is a future daily playlist, not a replay row.
    """
    out=Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    status=MiniStatus(out)
    status.write('loading/normalizing history and certifying seed alignment')
    hist, schema_audit, seed_audit, seed_summary = _coerce_history_for_rpa(history_obj, history_filename)
    if history_through:
        ht = pd.to_datetime(history_through, errors='coerce')
        if pd.isna(ht):
            raise ValueError(f'Bad history_through date: {history_through}')
        history_through = ht.strftime('%Y-%m-%d')
        hist = hist[hist['draw_date'].le(history_through)].copy()
    else:
        history_through = str(hist['draw_date'].max())
    if not play_date:
        play_date=(pd.to_datetime(history_through)+pd.Timedelta(days=1)).strftime('%Y-%m-%d')
    else:
        play_date=pd.to_datetime(play_date, errors='coerce').strftime('%Y-%m-%d')

    status.write('loading stable/frozen core rule library')
    raw_rules, rules = _load_rules(rules_obj, rules_filename)

    # latest same-stream seed per stream through history_through
    status.write('building latest same-stream seed event for each stream')
    h=hist[hist['draw_date'].le(history_through)].sort_values(['stream','draw_date','base4']).copy()
    latest=h.groupby('stream', as_index=False).tail(1).copy()
    latest=latest.sort_values('stream').reset_index(drop=True)
    events=pd.DataFrame({
        'event_id': np.arange(len(latest), dtype=np.int64),
        'draw_date': play_date,
        'stream': latest['stream'].astype(str).values,
        'prior_draw_date': latest['draw_date'].astype(str).values,
        'prior_result_used_as_seed': latest['base4'].astype(str).values,
        'seed': latest['base4'].astype(str).values,
        'base4': '',
        'seed_core': latest['actual_core'].fillna('').astype(str).values,
        'actual_core': '',
        'is_aabc_winner': False,
    })
    last1=[]; last2=[]; last3=[]
    for s in events['stream'].tolist():
        l=_last_aabc_cores(hist, s, history_through, 3)
        last1.append(l[0]); last2.append(l[1]); last3.append(l[2])
    events['last1_aabc_core']=last1
    events['last2_aabc_core']=last2
    events['last3_aabc_core']=last3
    events=rpa.attach_seed_traits(events)

    status.write('matching seed traits to rule library')
    matches=rpa.match_rules(events, rules, status)
    if matches.empty:
        raise ValueError('No rule matches were found for the latest seed events. Check that history and rule library schemas match.')
    status.write(f'aggregating/ranking config {config_name}')
    configs={
        'ALL_BALANCED': (lambda m: pd.Series(True, index=m.index), 'rule_strength_balanced'),
        'ALL_RULE_COUNT': (lambda m: pd.Series(True, index=m.index), 'rule_strength_hits'),
        'PRECISION_090_BALANCED': (lambda m: m['precision'] >= 0.90, 'rule_strength_balanced'),
        'LIFT_125_BALANCED': (lambda m: m['lift_vs_competitor'] >= 1.25, 'rule_strength_balanced'),
        'HIGH_CONF_090_L125': (lambda m: (m['precision'] >= 0.90) & (m['lift_vs_competitor'] >= 1.25), 'rule_strength_balanced'),
    }
    if config_name not in configs:
        raise ValueError(f'Unknown config_name={config_name}. Choices: {list(configs)}')
    filt, score_col = configs[config_name]
    grouped, _ = rpa.aggregate_scores(matches, config_name, filt, score_col)
    if grouped.empty:
        # fall back to all balanced to avoid blank daily matrix if high-conf filter is empty.
        grouped, _ = rpa.aggregate_scores(matches, 'ALL_BALANCED_FALLBACK', lambda m: pd.Series(True, index=m.index), 'rule_strength_balanced')

    status.write('building full all-120-core visible daily matrix')
    base=events[['event_id','draw_date','stream','prior_draw_date','prior_result_used_as_seed','seed','base4','seed_core','actual_core','last1_aabc_core','last2_aabc_core','last3_aabc_core']].copy()
    base['key']=1
    cores=pd.DataFrame({'target_core':rpa.CORE120,'key':1})
    full=base.merge(cores,on='key',how='inner').drop(columns=['key'])
    score_cols=['event_id','target_core','config','evidence_score','rule_count','max_precision','max_lift','total_support','total_target_hits']
    score_cols=[c for c in score_cols if c in grouped.columns]
    full=full.merge(grouped[score_cols], on=['event_id','target_core'], how='left')
    for c in ['evidence_score','rule_count','max_precision','max_lift','total_support','total_target_hits']:
        if c in full.columns:
            full[c]=pd.to_numeric(full[c], errors='coerce').fillna(0)
    if 'config' not in full.columns:
        full['config']=config_name
    full['final_stream_core_score']=full['evidence_score']
    full['has_rule_evidence']=full['rule_count'].fillna(0).gt(0)
    full=full.sort_values(['draw_date','stream','final_stream_core_score','rule_count','max_lift','target_core'], ascending=[True,True,False,False,False,True]).reset_index(drop=True)
    full['matrix_rank_in_stream']=full.groupby(['draw_date','stream']).cumcount()+1
    full=full.sort_values(['draw_date','final_stream_core_score','rule_count','max_lift','stream','target_core'], ascending=[True,False,False,False,True,True]).reset_index(drop=True)
    full['daily_opportunity_rank']=full.groupby('draw_date').cumcount()+1
    full['stream_core_rank_by_deep_score']=full['matrix_rank_in_stream']
    full['HISTORY_THROUGH']=history_through
    full['PLAY_DATE']=play_date

    matrices={
        'SCHEMA_ADAPTER_AUDIT.csv': schema_audit,
        'SEED_ALIGNMENT_AUDIT.csv': seed_audit,
        'SEED_ALIGNMENT_SUMMARY.csv': seed_summary,
        'NORMALIZED_RULES_USED.csv': rules,
        'DAILY_SEED_EVENTS_FOR_PLAY_DATE.csv': events,
        'RULE_MATCH_LEDGER_SAMPLE_100000.csv': matches.head(100000),
        'FULL_DAILY_RULE_MATRIX_ALL_120_CORES.csv': full,
        'MATRIX_BUILD_REPORT.csv': pd.DataFrame([{
            'BUILD': BUILD,
            'history_rows_loaded': len(hist),
            'history_through': history_through,
            'play_date': play_date,
            'unique_streams': events['stream'].nunique(),
            'seed_alignment_certification': seed_summary.iloc[0].get('certification',''),
            'bad_alignment_rows': int(seed_summary.iloc[0].get('bad_alignment_rows',-1)),
            'rules_loaded': len(rules),
            'rule_matches': len(matches),
            'matrix_rows_all_120': len(full),
            'config_used': config_name,
            'proof_note': 'Daily mode builds future candidates from latest seeds; there is no current actual_core yet. Use audit mode for hit validation.'
        }])
    }
    if write_csv:
        for name, df in matrices.items():
            df.to_csv(out/name, index=False)
    status.write('daily full matrix build complete')
    return full, matrices

def run_full_daily(
    history_obj=None,
    rules_obj=None,
    candidate_obj=None,
    out_dir: str|Path='OUT_C120_FULL_DAILY',
    history_filename: Optional[str]=None,
    rules_filename: Optional[str]=None,
    candidate_filename: Optional[str]=None,
    play_date: Optional[str]=None,
    history_through: Optional[str]=None,
    config_name: str='ALL_BALANCED',
    settings: Optional[EngineSettings]=None,
) -> Tuple[Dict[str,pd.DataFrame], Path]:
    out=Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    settings=settings or EngineSettings()
    if candidate_obj is not None:
        cand=read_any_table(candidate_obj, filename=candidate_filename)
        matrices={'CACHED_INPUT_MATRIX_USED.csv': cand, 'MATRIX_BUILD_REPORT.csv': pd.DataFrame([{'BUILD':BUILD,'mode':'cached_candidate_matrix','note':'No raw-history matrix build was run.'}])}
        history_df = read_any_table(history_obj, filename=history_filename) if history_obj is not None else None
    else:
        if history_obj is None or rules_obj is None:
            raise ValueError('Full daily mode requires history + rules, or cached mode requires candidate_obj.')
        cand, matrices = build_daily_rule_matrix_from_history(history_obj, rules_obj, out, play_date=play_date, history_through=history_through, history_filename=history_filename, rules_filename=rules_filename, config_name=config_name)
        # pass the normalized history source into selector only if user supplied; not needed because full matrix already includes last AABC fields.
        history_df = None
    outputs=select_daily_playlist(cand, history_df, settings)
    # prepend full build matrix outputs to final outputs, and patch run report
    outputs={**matrices, **outputs}
    rr=outputs.get('RUN_REPORT.csv', pd.DataFrame())
    extra=pd.DataFrame([
        {'field':'FULL_BUILD','value':BUILD},
        {'field':'FULL_DAILY_MODE','value':'history+rules->full all-120 matrix->matrix/member replacement' if candidate_obj is None else 'cached candidate matrix->matrix/member replacement'},
        {'field':'MATRIX_REQUIRED_FOR_TRACKING','value':'FULL_DAILY_RULE_MATRIX_ALL_120_CORES.csv is included when full mode is used.'},
    ])
    outputs['RUN_REPORT.csv']=pd.concat([extra, rr], ignore_index=True) if not rr.empty else extra
    zp=write_full_outputs(outputs, out, settings)
    return outputs, zp

def write_full_outputs(outputs: Dict[str,pd.DataFrame], out_dir: str|Path, settings: EngineSettings) -> Path:
    out=Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    # use core_engine write to get printable and zipped base, but include every file here to one package
    for name, df in outputs.items():
        try:
            df.to_csv(out/name, index=False)
        except Exception:
            pd.DataFrame(df).to_csv(out/name, index=False)
    from core_engine import make_printable_txt
    if 'DAILY_MEMBER_PLAYLIST_TOPN.csv' in outputs:
        (out/'PRINTABLE_DAILY_MEMBER_PLAYLIST.txt').write_text(make_printable_txt(outputs['DAILY_MEMBER_PLAYLIST_TOPN.csv'], settings), encoding='utf-8')
    (out/'ENGINE_SETTINGS.json').write_text(json.dumps(asdict(settings), indent=2), encoding='utf-8')
    (out/'FULL_APP_VERSION.txt').write_text(BUILD+'\n'+CORE_BUILD+'\n', encoding='utf-8')
    zp=out/'FULL_DAILY_MATRIX_MEMBER_REPLACEMENT_RESULTS.zip'
    with zipfile.ZipFile(zp, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in sorted(out.glob('*')):
            if p.name != zp.name:
                z.write(p, p.name)
    return zp

def run_replay_audit(history_path: str|Path, rules_path: str|Path, out_dir: str|Path, last_days: Optional[int]=180, write_full_matrix: bool=True) -> Path:
    """Runs the original v1.6 replay audit for historical matrix tracking. This can be slower."""
    out=Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    cmd=[sys.executable, str(Path(__file__).with_name('rule_daily_portfolio_audit.py')), '--history', str(history_path), '--rules', str(rules_path), '--out-dir', str(out/'REPLAY_AUDIT_OUT')]
    if last_days:
        cmd += ['--last-days', str(last_days)]
    if write_full_matrix:
        cmd += ['--write-full-matrix']
    subprocess.run(cmd, check=True)
    return out/'REPLAY_AUDIT_OUT'/'RULE_LIBRARY_REPLAY_OUTPUTS.zip'
