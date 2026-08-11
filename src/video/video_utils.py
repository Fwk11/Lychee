#!/usr/bin/env python3
"""视频 / 图像公共工具。

集中此前散落在 aesthetics_pipeline / vlm_client / aesthetic_scorer / API
抽帧端点里重复的取帧、缩放、元数据逻辑。全部仅用 OpenCV（无模型、无网络），
在 8GB 内存上安全。
"""
from __future__ import annotations

from typing import Iterator, Optional

import cv2
import numpy as np


# --------------------------------------------------------------------------
# metadata
# --------------------------------------------------------------------------

def video_meta(video_path: str) -> dict:
    """Return {fps, frame_count, duration_sec, width, height} for a video."""
    cap = cv2.VideoCapture(video_path)
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    finally:
        cap.release()
    duration = round(frame_count / fps, 2) if fps else 0.0
    return {"fps": fps, "frame_count": frame_count, "duration_sec": duration,
            "width": width, "height": height}


# --------------------------------------------------------------------------
# frame access
# --------------------------------------------------------------------------

def frame_at(video_path: str, sec: float,
             max_side: Optional[int] = None) -> Optional[np.ndarray]:
    """Grab the BGR frame at `sec`; optionally downscale so the longest
    side is <= max_side (OOM guard on 8GB machines). Returns None on failure."""
    cap = cv2.VideoCapture(video_path)
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(sec * fps)))
        ok, frame = cap.read()
    finally:
        cap.release()
    if not ok or frame is None:
        return None
    if max_side:
        frame = resize_max(frame, max_side)
    return frame


def middle_frame(video_path: str, start_sec: float, end_sec: float,
                 max_side: Optional[int] = None) -> Optional[np.ndarray]:
    """Grab the middle frame of a shot [start_sec, end_sec]."""
    return frame_at(video_path, (start_sec + end_sec) / 2.0, max_side=max_side)


def iter_frames(video_path: str, start_frame: int = 0,
                end_frame: Optional[int] = None,
                step: int = 1) -> Iterator[np.ndarray]:
    """Yield BGR frames in [start_frame, end_frame) with a stride."""
    cap = cv2.VideoCapture(video_path)
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, start_frame))
        idx = start_frame
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            if end_frame is not None and idx >= end_frame:
                break
            if (idx - start_frame) % step == 0:
                yield frame
            idx += 1
    finally:
        cap.release()


# --------------------------------------------------------------------------
# image ops
# --------------------------------------------------------------------------

def resize_max(frame_bgr: np.ndarray, max_side: int) -> np.ndarray:
    """Downscale so the longest side is <= max_side (never upscale)."""
    h, w = frame_bgr.shape[:2]
    side = max(h, w)
    if side <= max_side:
        return frame_bgr
    scale = max_side / side
    return cv2.resize(frame_bgr, (int(w * scale), int(h * scale)))


def to_jpeg_bytes(frame_bgr: np.ndarray, quality: int = 85) -> bytes:
    """Encode a BGR frame as JPEG bytes."""
    ok, buf = cv2.imencode(".jpg", frame_bgr,
                           [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("JPEG encode failed")
    return buf.tobytes()


def brightness_mean(frame_bgr: np.ndarray) -> float:
    """Mean brightness (0..1) of a BGR frame."""
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    return float(gray.mean() / 255.0)


if __name__ == "__main__":
    import glob, os, sys
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    vids = glob.glob(os.path.join(root, "data", "raw", "videos", "*.mp4"))
    if not vids:
        print("no test video found"); sys.exit(0)
    v = vids[0]
    meta = video_meta(v)
    print("meta:", meta)
    fr = frame_at(v, meta["duration_sec"] / 2, max_side=320)
    print("mid frame:", None if fr is None else fr.shape,
          "brightness:", None if fr is None else round(brightness_mean(fr), 3))
    print("jpeg bytes:", len(to_jpeg_bytes(fr)) if fr is not None else 0)
