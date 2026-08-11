#!/usr/bin/env python3
"""Pipeline orchestrator: shot segmentation + per-shot colour analysis.

Produces a JSON record matching the annotation guideline schema (v0.2):
  * D3 colour / B5 lighting / B2 camera move  -> OpenCV + optical flow
  * A1 content caption                        -> local VLM (qwen2.5vl via Ollama)
  * 17-dim RLHF reward features (A/B/C/V)      -> aesthetic_scorer (auto + human)
  * G-block compliance gate                    -> compliance (local rule engine)
"""
from __future__ import annotations

import os
import json

from .color_analysis import analyze_color
from .shot_segmentation import segment_shots
from .camera_motion import estimate_camera_move
from .data_value import tech_quality, phash
from .vlm_client import caption_video as _vlm_caption
from .vlm_client import director_tags_video as _vlm_director
from .aesthetic_scorer import score_report
from .video_utils import video_meta


def _lighting(color: dict) -> dict:
    """B5 光线质感 derived from colour metrics."""
    bri = color.get("brightness_mean", 0.0)
    contrast = color.get("color_contrast", 0.0)
    if bri < 0.15:
        exposure = "欠曝"
    elif bri > 0.85:
        exposure = "过曝"
    else:
        exposure = "正常"
    return {"exposure": exposure, "dynamic_range": contrast}


def run_pipeline(video_path: str, with_motion: bool = True,
                 with_caption: bool = True, vlm_backend: str = "ollama") -> dict:
    """Analyse one video -> per-shot record (colour + motion + content).

    `content_caption` (D6 / A1) is filled by a local VLM (default Ollama
    qwen2.5vl:3b) reading each shot's MIDDLE frame -- this is the
    "读取视频画面与内容" capability. Compliance (G) still needs a moderation
    model and stays a placeholder.
    """
    meta = video_meta(video_path)
    fps = meta["fps"]
    frame_count = meta["frame_count"]
    duration = meta["duration_sec"]

    shots = segment_shots(video_path)

    shot_records = []
    for s in shots:
        start_frame = int(s["start_sec"] * fps)
        end_frame = int(s["end_sec"] * fps)
        mid_sec = (s["start_sec"] + s["end_sec"]) / 2.0
        color = analyze_color(video_path, start_frame=start_frame,
                              end_frame=end_frame)
        motion = (estimate_camera_move(video_path, s["start_sec"], s["end_sec"])
                  if with_motion else {"camera_move": None})
        # VLM director tags (content caption + 景别/构图/情绪/美学) on the
        # shot's middle frame. One call returns everything; retries on the
        # pipeline side keep the caption from silently going empty.
        caption = None
        director = {}
        if with_caption:
            try:
                director = _vlm_director(video_path, mid_sec, backend=vlm_backend)
                caption = director.get("content_caption")
            except Exception as e:  # VLM totally unavailable -> keep null
                caption = None
                director = {}
        shot_records.append({
            "shot_id": s["shot_id"],
            "start_sec": s["start_sec"],
            "end_sec": s["end_sec"],
            "frame_count": s["frame_count"],
            # computable (no model)
            "color": color,
            "lighting": _lighting(color),
            "camera_move": motion.get("camera_move"),
            "motion": motion,
            # VLM-generated content understanding + director tags
            "content_caption": caption if caption else None,
            "shot_scale": director.get("shot_scale"),
            "composition": director.get("composition"),
            "mood": director.get("mood"),
            "aesthetic_score": director.get("aesthetic_score"),
            "compliance": None,
        })

    report = {
        "video_id": os.path.splitext(os.path.basename(video_path))[0],
        "source": os.path.basename(video_path),
        "duration_sec": duration,
        "fps": round(fps, 2),
        "frame_count": frame_count,
        "shot_count": len(shots),
        # data value (video level)
        "data_value": {
            "tech": tech_quality(video_path),
            "phash": phash(video_path),
        },
        "shots": shot_records,
    }

    # RLHF scoring (A/B/C/V blocks) + compliance gate (G block)
    report = score_report(report, video_path=video_path)
    return report


def run_batch(video_dir: str) -> list[dict]:
    """Run pipeline on every .mp4/.mov/.avi in a directory."""
    results = []
    for f in sorted(os.listdir(video_dir)):
        if f.lower().endswith((".mp4", ".mov", ".avi", ".mkv")):
            path = os.path.join(video_dir, f)
            print(f"  analysing {f} ...")
            try:
                results.append(run_pipeline(path))
            except Exception as e:
                print(f"    ERROR: {e}")
    return results


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python -m src.video.aesthetics_pipeline <video_or_dir>")
        sys.exit(1)
    target = sys.argv[1]
    if os.path.isdir(target):
        out = run_batch(target)
    else:
        out = run_pipeline(target)
    print(json.dumps(out, ensure_ascii=False, indent=2))
