#!/usr/bin/env python3
"""镜头边界检测：基于 HSV 直方图差异的帧差法。

逐帧构建 HSV 三维直方图，当相邻帧直方图相关度低于阈值时判定为镜头切换边界，
返回若干镜头的起止时间戳。这是经典的「帧差法」——快、无需模型，对边界清晰的
镜头切分足够好；阈值 threshold 与最短镜头时长 min_len_sec 可按数据集调参。

设计取舍：不用 PySceneDetect 等第三方库，保持 8GB 单机零额外依赖；Ollama VLM
只读「镜头中间帧」做内容理解，镜头边界必须由确定性的 CV 算法给出，否则 VLM 会
把同一镜头的多帧重复描述。
"""
from __future__ import annotations

import cv2
import numpy as np


def _hist(frame: np.ndarray) -> np.ndarray:
    """Normalised HSV histogram (3D) for one frame."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1, 2], None, [8, 8, 8],
                        [0, 180, 0, 256, 0, 256])
    cv2.normalize(hist, hist)
    return hist.flatten()


def segment_shots(video_path: str, threshold: float = 0.5,
                  min_len_sec: float = 0.6) -> list[dict]:
    """Return list of shots: [{shot_id, start_sec, end_sec, frame_count}]."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0

    boundaries = [0]  # frame indices where a shot starts
    prev = None
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        h = _hist(frame)
        if prev is not None:
            # cv2.HISTCMP_CORREL: 1.0 = identical, -1 = opposite
            sim = cv2.compareHist(prev, h, cv2.HISTCMP_CORREL)
            if sim < threshold:
                boundaries.append(idx)
        prev = h
        idx += 1
    cap.release()

    boundaries.append(idx)  # end sentinel

    min_frames = int(max(1, min_len_sec * fps))
    # Keep a boundary only if the shot ending at it is long enough.
    # This also drops a spurious 1-frame shot at the very start
    # (e.g. a black fade-in frame) by merging it into the next shot.
    merged = [boundaries[0]]
    for b in boundaries[1:-1]:
        if b - merged[-1] >= min_frames:
            merged.append(b)
    merged.append(idx)

    shots = []
    for i, (s, e) in enumerate(zip(merged[:-1], merged[1:])):
        if e - s < 1:
            continue
        shots.append({
            "shot_id": f"s{i+1}",
            "start_sec": round(s / fps, 2),
            "end_sec": round(e / fps, 2),
            "frame_count": e - s,
        })
    if not shots:
        shots.append({
            "shot_id": "s1",
            "start_sec": 0.0,
            "end_sec": round(idx / fps, 2),
            "frame_count": idx,
        })
    return shots


if __name__ == "__main__":
    import sys
    import json
    if len(sys.argv) < 2:
        print("usage: python shot_segmentation.py <video>")
        sys.exit(1)
    print(json.dumps(segment_shots(sys.argv[1]), ensure_ascii=False, indent=2))
