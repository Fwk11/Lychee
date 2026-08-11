#!/usr/bin/env python3
"""B2 运镜估计：基于稠密光流（Farneback），纯计算无 VLM。

运镜是时序信号，单帧 VLM 看不到，所以用光流场在镜头内采样若干帧对、
计算聚合位移后分类：
  - 近零均值幅值              → 固定（静止）
  - 强径向发散/收缩            → 推拉/变焦
  - 主导水平/垂直漂移          → 摇（pan/tilt）
  - 连贯大幅位移              → 移动/跟随
  - 高频非相干抖动            → 手持

阈值为启发式，意在「可调」——调参过程本身就是标注标准工作的一部分。
"""
from __future__ import annotations

import cv2
import numpy as np


def _gray_frames(video_path: str, start_sec: float, end_sec: float | None,
                 n: int = 9, size: tuple[int, int] = (160, 90)) -> list[np.ndarray]:
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    s = max(0, int(start_sec * fps))
    e = int(end_sec * fps) if end_sec else total
    e = max(s + 2, e)
    idxs = np.linspace(s, e - 1, n).astype(int)
    out = []
    for i in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, fr = cap.read()
        if ok:
            out.append(cv2.cvtColor(cv2.resize(fr, size), cv2.COLOR_BGR2GRAY))
    cap.release()
    return out


def estimate_camera_move(video_path: str, start_sec: float = 0.0,
                         end_sec: float | None = None) -> dict:
    frames = _gray_frames(video_path, start_sec, end_sec)
    if len(frames) < 2:
        return {"camera_move": "固定", "mean_flow": 0.0, "divergence": 0.0,
                "dominant_dx": 0.0, "dominant_dy": 0.0, "confidence": 0.2}

    h, w = frames[0].shape
    cy, cx = h / 2.0, w / 2.0
    yy, xx = np.mgrid[0:h, 0:w]
    rx, ry = (xx - cx) / cx, (yy - cy) / cy
    rad = np.sqrt(rx ** 2 + ry ** 2) + 1e-6

    mags, dxs, dys, divs, jitters = [], [], [], [], []
    for a, b in zip(frames[:-1], frames[1:]):
        flow = cv2.calcOpticalFlowFarneback(a, b, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        fx, fy = flow[..., 0], flow[..., 1]
        mag, _ = cv2.cartToPolar(fx, fy)
        mags.append(float(mag.mean()))
        dxs.append(float(fx.mean()))
        dys.append(float(fy.mean()))
        divs.append(float(((fx * rx + fy * ry) / rad).mean()))
        # jitter = spatial incoherence of flow direction (handheld shakes all over)
        jitters.append(float(np.std(mag)))

    mean_mag = float(np.mean(mags))
    mean_dx = float(np.mean(dxs))
    mean_dy = float(np.mean(dys))
    mean_div = float(np.mean(divs))
    jitter = float(np.mean(jitters))

    # ---- classify (heuristic, tune on your data) ----
    if mean_mag < 0.4:
        label, conf = "固定", 0.9
    elif abs(mean_div) > 0.5 and abs(mean_div) > abs(mean_dx) and abs(mean_div) > abs(mean_dy):
        label, conf = ("推/变焦(放大)" if mean_div > 0 else "拉/变焦(缩小)"), 0.7
    elif abs(mean_dx) > 0.8 and abs(mean_dx) > abs(mean_dy):
        label, conf = "摇(水平)", 0.7
    elif abs(mean_dy) > 0.8:
        label, conf = "摇(垂直)", 0.7
    elif jitter > 1.2:
        label, conf = "手持", 0.6
    elif mean_mag > 1.5:
        label, conf = "移动/跟随", 0.6
    else:
        label, conf = "轻微运动", 0.5

    return {
        "camera_move": label,
        "mean_flow": round(mean_mag, 3),
        "divergence": round(mean_div, 3),
        "dominant_dx": round(mean_dx, 3),
        "dominant_dy": round(mean_dy, 3),
        "jitter": round(jitter, 3),
        "confidence": conf,
    }


if __name__ == "__main__":
    import sys, json
    if len(sys.argv) < 2:
        print("usage: python -m src.video.camera_motion <video> [start_sec] [end_sec]")
        sys.exit(1)
    v = sys.argv[1]
    s = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
    e = float(sys.argv[3]) if len(sys.argv) > 3 else None
    print(json.dumps(estimate_camera_move(v, s, e), ensure_ascii=False, indent=2))
