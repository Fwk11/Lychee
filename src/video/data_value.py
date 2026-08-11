#!/usr/bin/env python3
"""数据价值打分（V1-V6）：这段视频「作为训练数据有没有用」。

纯计算、无模型：
  - pHash（感知哈希）用于跨数据集近重复检测
  - 技术质量（分辨率、Laplacian 方差锐度、亮度健全性）
  - 相对数据集的唯一性 / 多样性贡献

这是「数据运营」视角的判断：一段漂亮的视频若与另外 50 段重复，训练价值低；
一段普通的视频若处在稀缺类别，价值反而高。
"""
from __future__ import annotations

import os
import cv2
import numpy as np


def _mid_frame(video_path: str) -> np.ndarray | None:
    """Grab the MIDDLE frame (first frames are often black fade-ins, which
    makes every video hash identically -> false duplicates)."""
    cap = cv2.VideoCapture(video_path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    cap.set(cv2.CAP_PROP_POS_FRAMES, total // 2)
    ok, fr = cap.read()
    cap.release()
    return fr if ok else None


def phash(video_path: str, hash_size: int = 8) -> str | None:
    """DCT perceptual hash of the first frame -> hex string."""
    frame = _mid_frame(video_path)
    if frame is None:
        return None
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, (32, 32)).astype(np.float32)
    dct = cv2.dct(gray)
    low = dct[:hash_size, :hash_size].flatten()
    med = np.median(low[1:])  # exclude DC
    bits = (low > med).astype(np.uint8)
    # pack bits to hex
    hexstr = "".join(
        f"{int(''.join(map(str, bits[i:i+4].tolist())), 2):x}"
        for i in range(0, len(bits), 4)
    )
    return hexstr


def hamming(h1: str, h2: str) -> int:
    """Hamming distance between two hex hashes."""
    n = int(h1, 16) ^ int(h2, 16)
    return bin(n).count("1")


def tech_quality(video_path: str) -> dict:
    """Resolution + sharpness + exposure sanity (uses the middle frame)."""
    cap = cv2.VideoCapture(video_path)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    fr = _mid_frame(video_path)
    if fr is None:
        return {"width": w, "height": h, "sharpness": 0.0, "brightness": 0.0}
    gray = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
    sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    bri = float(gray.mean()) / 255.0
    return {"width": w, "height": h, "sharpness": round(sharp, 1),
            "brightness": round(bri, 3), "resolution": f"{w}x{h}"}


def score_dataset(video_dir: str, dup_threshold: int = 10) -> list[dict]:
    """Score every video for uniqueness + tech quality + duplicate groups."""
    files = sorted(f for f in os.listdir(video_dir)
                   if f.lower().endswith((".mp4", ".mov", ".avi", ".mkv")))
    entries = []
    for f in files:
        p = os.path.join(video_dir, f)
        h = phash(p)
        q = tech_quality(p)
        entries.append({"file": f, "phash": h, **q})

    # pairwise dedup
    for e in entries:
        e["duplicate_of"] = None
        e["min_hamming"] = None
    hashes = [(e["file"], e["phash"]) for e in entries if e["phash"]]
    for i, e in enumerate(entries):
        if not e["phash"]:
            continue
        best, best_d = None, 999
        for f2, h2 in hashes:
            if f2 == e["file"]:
                continue
            d = hamming(e["phash"], h2)
            if d < best_d:
                best, best_d = f2, d
        e["min_hamming"] = best_d
        if best_d <= dup_threshold:
            e["duplicate_of"] = best

    # uniqueness score 1-5 (higher = more unique)
    for e in entries:
        d = e["min_hamming"]
        if d is None:
            e["uniqueness"] = 3
        elif d <= dup_threshold:
            e["uniqueness"] = 1
        elif d <= 16:
            e["uniqueness"] = 3
        else:
            e["uniqueness"] = 5
    return entries


if __name__ == "__main__":
    import sys, json
    vd = sys.argv[1] if len(sys.argv) > 1 else "data/raw/videos"
    print(json.dumps(score_dataset(vd), ensure_ascii=False, indent=2))
