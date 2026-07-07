#!/usr/bin/env python3
"""
C120 v2.4 trap-integrated runner.

This keeps the original v2.2/v2.3 matrix/member engine intact, then adds a
separate winner-location trap layer from the full 120 matrix. That makes the
change additive, auditable, and reversible.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
import pandas as pd

from core_engine import EngineSettings, SAFE_SKIP_SCENARIOS, FORMULA_SCENARIOS, MEMBER_DELETE_PRESETS
from full_pipeline import BUILD, run_full_daily

TRAP_BUILD = "C120_v2_4_TRAP_BROWSER_UI"


def copy_if_exists(src: Path, dst: Path) -> bool:
    if src and src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return True
    return False


def run_trap_from_files(full_matrix: Path, history: Path, out_dir: Path, member_matrix: Path | None = None, winners: Path | None = None, mode: str = "daily") -> Path:
    trap_in = out_dir / "TRAP_IN"
    trap_out = out_dir / "TRAP_OUT"
    cfg = out_dir / "TRAP_CFG"
    trap_in.mkdir(parents=True, exist_ok=True)
    trap_out.mkdir(parents=True, exist_ok=True)
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "watched_cores.txt").write_text("027\n067\n138\n145\n389\n457\n567\n679\n", encoding="utf-8")
    copy_if_exists(full_matrix, trap_in / "FULL_DAILY_RULE_MATRIX_ALL_120_CORES.csv")
    copy_if_exists(history, trap_in / "history_updated.csv")
    if member_matrix:
        copy_if_exists(member_matrix, trap_in / "DAILY_MEMBER_MATRIX_ALL_CANDIDATES.csv")
    if winners:
        # Keep its original name so the trap engine can detect winner/update text/csv.
        copy_if_exists(winners, trap_in / winners.name)
    cmd = [sys.executable, str(Path(__file__).with_name("c120_trap_engine_v23.py")), "--mode", mode, "--in", str(trap_in), "--out", str(trap_out), "--cfg", str(cfg)]
    subprocess.run(cmd, check=True)
    return trap_out / "C120_TRAP_OUTPUTS.zip"


def main() -> None:
    ap = argparse.ArgumentParser(description=f"{TRAP_BUILD} runner")
    ap.add_argument("--mode", choices=["daily_full_trap", "trap_existing", "wf_trap_existing"], default="trap_existing")
    ap.add_argument("--history", default=None, help="Clean history CSV. In daily_full_trap this must be history through at least HISTORY_THROUGH; in trap modes it is used for blind prior counts.")
    ap.add_argument("--rules", default=None, help="Rule library CSV for daily_full_trap mode.")
    ap.add_argument("--full-matrix", default=None, help="Existing FULL_DAILY_RULE_MATRIX_ALL_120_CORES.csv for trap_existing/wf_trap_existing.")
    ap.add_argument("--member-matrix", default=None, help="Existing DAILY_MEMBER_MATRIX_ALL_CANDIDATES.csv for trap_existing/wf_trap_existing.")
    ap.add_argument("--winners", default=None, help="Optional actual winner text/csv for after-the-fact audit only. Never used to build plays.")
    ap.add_argument("--out", default="OUT_C120_v24_TRAP_BROWSER_UI")
    ap.add_argument("--history-through", default=None)
    ap.add_argument("--play-date", default=None)
    ap.add_argument("--config-name", default="ALL_BALANCED", choices=["ALL_BALANCED","ALL_RULE_COUNT","PRECISION_090_BALANCED","LIFT_125_BALANCED","HIGH_CONF_090_L125"])
    ap.add_argument("--cutoff", type=int, default=80)
    ap.add_argument("--top-n-cores", type=int, default=4)
    ap.add_argument("--skip", choices=list(SAFE_SKIP_SCENARIOS), default="tier2_eooo_or_triple")
    ap.add_argument("--formula", choices=list(FORMULA_SCENARIOS), default="core_spread_member_soft")
    ap.add_argument("--member-delete-preset", choices=list(MEMBER_DELETE_PRESETS), default="none")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    if args.mode == "daily_full_trap":
        if not args.history or not args.rules:
            raise SystemExit("daily_full_trap requires --history and --rules")
        settings = EngineSettings(play_date_mode="latest", play_date=args.play_date, cutoff_per_day=args.cutoff, top_n_cores_per_stream=args.top_n_cores, skip_scenario=args.skip, formula=args.formula, member_delete_preset=args.member_delete_preset)
        base_out = out / "BASE_ENGINE_OUT"
        outputs, base_zip = run_full_daily(history_obj=args.history, rules_obj=args.rules, out_dir=base_out, history_filename=Path(args.history).name, rules_filename=Path(args.rules).name, play_date=args.play_date, history_through=args.history_through, config_name=args.config_name, settings=settings)
        full_matrix = base_out / "FULL_DAILY_RULE_MATRIX_ALL_120_CORES.csv"
        member_matrix = base_out / "DAILY_MEMBER_MATRIX_ALL_CANDIDATES.csv"
        trap_zip = run_trap_from_files(full_matrix, Path(args.history), out, member_matrix=member_matrix, winners=Path(args.winners) if args.winners else None, mode="daily")
        print(f"{TRAP_BUILD} COMPLETE")
        print(f"Base engine ZIP: {base_zip.resolve()}")
        print(f"Trap ZIP: {trap_zip.resolve()}")
        # Show compact summaries if present.
        for p in [base_out / "RUN_SUMMARY.csv", out / "TRAP_OUT" / "TRAP_RUN_SUMMARY.csv", out / "TRAP_OUT" / "LEAKAGE_AUDIT.csv"]:
            if p.exists():
                print("\n" + p.name)
                print(pd.read_csv(p, dtype=str).to_string(index=False))
        return

    if args.mode in {"trap_existing", "wf_trap_existing"}:
        if not args.full_matrix or not args.history:
            raise SystemExit("trap_existing/wf_trap_existing requires --full-matrix and --history")
        trap_zip = run_trap_from_files(Path(args.full_matrix), Path(args.history), out, member_matrix=Path(args.member_matrix) if args.member_matrix else None, winners=Path(args.winners) if args.winners else None, mode="wf" if args.mode == "wf_trap_existing" else "daily")
        print(f"{TRAP_BUILD} COMPLETE")
        print(f"Trap ZIP: {trap_zip.resolve()}")
        for p in [out / "TRAP_OUT" / "TRAP_RUN_SUMMARY.csv", out / "TRAP_OUT" / "LEAKAGE_AUDIT.csv", out / "TRAP_OUT" / "WINNER_LOCATION_AUDIT.csv", out / "TRAP_OUT" / "WF_TRAP_LANE_PERFORMANCE.csv"]:
            if p.exists():
                print("\n" + p.name)
                df = pd.read_csv(p, dtype=str)
                print(df.head(30).to_string(index=False))
        return

if __name__ == "__main__":
    main()
