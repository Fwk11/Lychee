#!/usr/bin/env python3
"""本地合规闸门（评测体系 G 块）。

无需外部审核模型——用：
  * G1/G2/G3 → 中英关键词表扫描 VLM 描述 + 暗红像素占比启发式（G2 暴力/血腥）
  * G4       → 边角区域 logo/水印启发式（轻量）
  * G5       → OpenCV 自带 Haar 级联人脸检测（无需下载）

判定：任一维度 fail → blocked；任一 warn → review；否则 compliant。
等级：pass / warn / fail（对齐评测标准）。
"""
from __future__ import annotations

import cv2
import numpy as np

# ---- keyword tables (defensive, not exhaustive) ---------------------------
G1_NSFW = ["裸露", "色情", "情色", "裸体", "性暗示", "成人内容",
           "nude", "nudity", "porn", "sexual", "erotic", "nsfw"]
G2_VIOLENCE = ["暴力", "血腥", "鲜血", "受伤", "武器", "打架", "血迹",
               "violence", "bloody", "blood", "weapon", "fight", "gore", "wound"]
G3_ILLEGAL = ["毒品", "枪支", "赌博", "管制刀具", "走私", "违法",
              "drug", "firearm", "illegal", "gambling"]
# G4 soft signals that often accompany watermark / ad slates
G4_WATERMARK = ["水印", "广告", "关注", "订阅", "logo", "贴片",
                "watermark", "advertisement", "subscribe", "follow us"]

# severity -> level
SEV_FAIL = 3
SEV_WARN = 2


def _hit(text: str, table: list[str]) -> list[str]:
    if not text:
        return []
    t = text.lower()
    return [w for w in table if w.lower() in t]


def _red_dominance(frame_bgr: np.ndarray) -> float:
    """Fraction of DARK-red pixels (blood/violence heuristic).

    Bright orange / sunset (common in landscape clips) is excluded by the
    low total-brightness gate, so we don't flag scenic footage as gore.
    """
    if frame_bgr is None:
        return 0.0
    b, g, r = cv2.split(frame_bgr)
    ri = r.astype(int)
    mask = (ri > 120) & (ri - g.astype(int) > 80) & (ri - b.astype(int) > 80) \
           & ((ri + g.astype(int) + b.astype(int)) < 360)
    return float(mask.mean())


def _border_logo(bgr: np.ndarray) -> bool:
    """Heuristic: large flat-corner logo/watermark -> high edge density in
    corner bands with low colour variance (a static overlay)."""
    if bgr is None:
        return False
    h, w = bgr.shape[:2]
    band = max(8, int(min(h, w) * 0.08))
    corners = [bgr[0:band, 0:band], bgr[0:band, w - band:w],
               bgr[h - band:h, 0:band], bgr[h - band:h, w - band:w]]
    scores = []
    for c in corners:
        gray = cv2.cvtColor(c, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        scores.append(edges.mean() / 255.0)
    # A static watermark/logo usually lights up MOST corners consistently;
    # natural scene edges rarely do, so require >=3 busy corners to flag.
    return sum(1 for s in scores if s > 0.15) >= 3


def _faces(frame_bgr: np.ndarray):
    if frame_bgr is None:
        return 0
    try:
        cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray, 1.3, 5)
        return int(len(faces))
    except Exception:
        return 0


def assess(shot: dict, mid_frame: np.ndarray | None = None) -> dict:
    """Assess one shot record.

    `shot` must contain `content_caption` and `color`. `mid_frame` is the
    shot's middle frame (BGR) used for G2 red check, G4 logo, G5 faces.
    """
    caption = (shot.get("content_caption") or "") or ""
    reasons = []

    # G1 色情/裸露
    g1 = "pass"
    hits = _hit(caption, G1_NSFW)
    if hits:
        g1 = "fail"
        reasons.append(f"G1 疑似裸露/色情关键词: {hits}")

    # G2 暴力/血腥
    g2 = "pass"
    vhits = _hit(caption, G2_VIOLENCE)
    red = _red_dominance(mid_frame) if mid_frame is not None else 0.0
    if vhits:
        g2 = "fail"
        reasons.append(f"G2 疑似暴力关键词: {vhits}")
    elif red > 0.18:
        g2 = "warn"
        reasons.append(f"G2 画面红色占比偏高({red:.2f})，疑似血迹/警示，需人工复核")

    # G3 违法/敏感
    g3 = "pass"
    ihits = _hit(caption, G3_ILLEGAL)
    if ihits:
        g3 = "fail"
        reasons.append(f"G3 违法/敏感关键词: {ihits}")

    # G4 水印/贴片/广告
    g4 = "pass"
    whits = _hit(caption, G4_WATERMARK)
    logo = _border_logo(mid_frame) if mid_frame is not None else False
    if whits:
        g4 = "warn"
        reasons.append(f"G4 疑似水印/广告信号: {whits}")
    elif logo:
        g4 = "warn"
        reasons.append("G4 边角检测到疑似静态logo/水印，需人工复核(启发式，可能误报)")

    # G5 可辨识人脸（肖像权风险）
    g5 = "pass"
    nf = _faces(mid_frame) if mid_frame is not None else 0
    if nf > 0:
        g5 = "warn"
        reasons.append(f"G5 检测到 {nf} 张人脸，存在肖像权风险，建议脱敏/授权")

    levels = {"G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5}
    if any(v == "fail" for v in levels.values()):
        verdict = "blocked"
    elif any(v == "warn" for v in levels.values()):
        verdict = "review"
    else:
        verdict = "compliant"

    return {
        "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5,
        "verdict": verdict,
        "reasons": reasons,
        "faces_detected": nf,
    }
