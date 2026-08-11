#!/usr/bin/env python3
"""D3 色彩分析 (programmatic).

Reads a video, samples frames at a fixed fps, and computes:
  - dominant_colors: top-3 RGB via K-means on sampled pixels
  - saturation_mean:  mean of HSV S channel
  - brightness_mean:  mean of HSV V channel
  - color_temp:       warm / cool / neutral  (R mean vs B mean)
  - color_contrast:   brightness std, normalised

All outputs are 0-1 floats where applicable. No model involved.
"""
from __future__ import annotations

import cv2
import numpy as np
from sklearn.cluster import KMeans


def _dominant_colors(pixels: np.ndarray, k: int = 3) -> list[list[int]]:
    """K-means on a sample of pixels -> top-k colours sorted by cluster size."""
    if len(pixels) > 2000:
        idx = np.random.choice(len(pixels), 2000, replace=False)
        pixels = pixels[idx]
    km = KMeans(n_clusters=k, n_init=4, random_state=42)
    km.fit(pixels)
    counts = np.bincount(km.labels_)
    order = np.argsort(-counts)
    colors = km.cluster_centers_[order].astype(int).tolist()
    return [list(map(int, c)) for c in colors]


def analyze_color(video_path: str, sample_fps: float = 1.0,
                  start_frame: int = 0, end_frame: int | None = None) -> dict:
    """Return the D3 colour block for one video or a shot frame range."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    frame_step = max(1, int(round(fps / sample_fps)))

    sat_vals, bri_vals, r_vals, b_vals, all_pixels, bri_std_vals = [], [], [], [], [], []
    # seek to start frame
    if start_frame > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    frame_idx = 0
    read_idx = start_frame
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if end_frame is not None and read_idx >= end_frame:
            break
        if frame_idx % frame_step == 0:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            h, s, v = cv2.split(hsv)
            sat_vals.append(float(s.mean()) / 255.0)
            bri_vals.append(float(v.mean()) / 255.0)
            bri_std_vals.append(float(v.std()) / 255.0)
            r_vals.append(float(rgb[..., 0].mean()))
            b_vals.append(float(rgb[..., 2].mean()))
            # sample pixels for kmeans
            small = cv2.resize(rgb, (64, 64)).reshape(-1, 3)
            all_pixels.append(small)
        frame_idx += 1
        read_idx += 1
    cap.release()

    if not sat_vals:
        raise RuntimeError(f"no frames sampled from {video_path}")

    pixels = np.vstack(all_pixels)
    dom = _dominant_colors(pixels, k=3)

    r_mean = float(np.mean(r_vals))
    b_mean = float(np.mean(b_vals))
    diff = r_mean - b_mean
    if diff > 8:
        temp = "warm"
    elif diff < -8:
        temp = "cool"
    else:
        temp = "neutral"

    return {
        "dominant_colors": dom,
        "saturation_mean": round(float(np.mean(sat_vals)), 3),
        "brightness_mean": round(float(np.mean(bri_vals)), 3),
        "color_temp": temp,
        "color_contrast": round(float(np.mean(bri_std_vals)), 3),
    }


if __name__ == "__main__":
    import sys
    import json
    if len(sys.argv) < 2:
        print("usage: python color_analysis.py <video>")
        sys.exit(1)
    print(json.dumps(analyze_color(sys.argv[1]), ensure_ascii=False, indent=2))
