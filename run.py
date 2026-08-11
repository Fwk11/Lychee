#!/usr/bin/env python3
"""CLI entry: run video aesthetics pipeline on a directory of videos.

Writes results incrementally (one video at a time) so a crash / OOM in the
middle of a large batch does not lose already-computed results.

Usage:
    python run.py                      # default: data/raw/videos -> output/aesthetics_report.json
    python run.py --input DIR --output FILE
    python run.py agent "帮我推荐歌单"   # natural-language ReAct agent
    python run.py agent -i             # interactive agent mode
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from src.video.aesthetics_pipeline import run_pipeline


def _load_existing(out_path: str) -> list:
    if os.path.exists(out_path):
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return []


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "agent":
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        from src.agent.agent import main as agent_main
        agent_main()
        return

    if len(sys.argv) > 1 and sys.argv[1] == "music":
        from src.music.sheet_fetcher import backfill_sheets
        backfill_sheets()
        from src.music.omr_pipeline import run as omr_run
        omr_run()
        return

    if len(sys.argv) > 1 and sys.argv[1] == "omr":
        from src.music.omr_pipeline import run as omr_run
        omr_run()
        return

    p = argparse.ArgumentParser(description="video aesthetics pipeline")
    p.add_argument("--input", "-i", default="data/raw/videos", help="video directory")
    p.add_argument("--output", "-o", default="output/aesthetics_report.json", help="output JSON path")
    p.add_argument("--reset", action="store_true", help="ignore previous partial results")
    args = p.parse_args()

    video_dir = args.input if os.path.isabs(args.input) else os.path.join(ROOT, args.input)
    out_path = args.output if os.path.isabs(args.output) else os.path.join(ROOT, args.output)

    if not os.path.isdir(video_dir):
        print(f"video dir not found: {video_dir}")
        sys.exit(1)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    done = {} if args.reset else {r.get("video_id"): r for r in _load_existing(out_path)}
    results = list(done.values())

    files = sorted(
        f for f in os.listdir(video_dir)
        if f.lower().endswith((".mp4", ".mov", ".avi", ".mkv"))
    )

    print(f"== video aesthetics pipeline ==")
    print(f"input : {video_dir}")
    print(f"output: {out_path}")
    print(f"resume: {len(results)} already done, {len(files) - len(results)} to process")
    print()

    t0 = time.time()
    for f in files:
        if f in done:
            continue
        path = os.path.join(video_dir, f)
        print(f"  analysing {f} ...")
        try:
            rec = run_pipeline(path)
            results.append(rec)
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(results, fh, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"    ERROR: {e}")

    elapsed = time.time() - t0
    total_shots = sum(r["shot_count"] for r in results)
    print()
    print(f"== summary ==")
    print(f"videos analysed : {len(results)}")
    print(f"total shots     : {total_shots}")
    print(f"time            : {elapsed:.1f}s")
    print(f"report          : {out_path}")


if __name__ == "__main__":
    main()