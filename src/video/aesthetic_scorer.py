#!/usr/bin/env python3
"""RLHF 奖励特征打分器（评测体系 A/B/C + V 块）。

机器可计算的维度自动打 1-5 分；真正需要人工/强 VLM 判断的维度
（B1 景别、B3 构图、A3 叙事、C3 美学综合）置 None，留给标注 UI 填——
这正是评测标准里「人在回路」的设计。

输出：17 个奖励特征 + 一个 aesthetic_proxy（可计算子集的加权和），
保证报告始终有一个可用的整体分数，不至于全空。
"""
from __future__ import annotations

import os
import glob

# weight vector from rubric §7 (only the computable subset is used for proxy)
PROXY_WEIGHTS = {
    "A1": 0.08, "A2": 0.07, "A3": 0.05, "B1": 0.06, "B2": 0.08,
    "B3": 0.08, "B4": 0.10, "B5": 0.08, "B6": 0.06, "C1": 0.06,
    "C2": 0.06, "C3": 0.22,
}


def _clamp5(x: float) -> int:
    return int(max(1, min(5, round(x))))


def _caption_signal(caption: str):
    """Rough linguistic signal: length + whether it names concrete entities."""
    if not caption:
        return 0.0, 0.0  # no caption -> little signal
    length = len(caption)
    # Chinese/English concrete nouns proxy: count of characters beyond a stub
    info = min(1.0, length / 40.0)
    return info, info


def score_shot(shot: dict, known_phashes: list[str] | None = None) -> dict:
    color = shot.get("color") or {}
    motion = shot.get("motion") or {}
    cap = (shot.get("content_caption") or "") or ""
    sat = color.get("saturation_mean", 0.0)
    bri = color.get("brightness_mean", 0.0)
    contrast = color.get("color_contrast", 0.0)
    temp = color.get("color_temp", "neutral")
    doms = color.get("dominant_colors", []) or []

    scores: dict[str, int | None] = {}

    # ---- A 块 内容 ----
    info, _ = _caption_signal(cap)
    # A1 内容明确性: caption present & specific + not under/over exposed
    a1 = 3.0 + 2.0 * info
    if "vlm_error" in cap or not cap:
        a1 = 1.0
    elif bri < 0.12 or bri > 0.9:  # extreme exposure kills clarity
        a1 -= 1.5
    scores["A1"] = _clamp5(a1)

    # A2 信息丰富度: caption length + colour variance proxy
    a2 = 2.5 + 2.0 * info + 0.5 * min(1.0, len(doms) / 3.0)
    scores["A2"] = _clamp5(a2)

    # A3 叙事性: action verb heuristic (human-fillable, proxy only)
    ACTION = ["走", "跑", "飞", "跳", "舞", "唱", "吃", "喝", "追", "落", "升",
              "walk", "run", "fly", "dance", "eat", "pour", "drive", "play"]
    if any(w in cap.lower() for w in ACTION):
        scores["A3"] = 4
    elif cap:
        scores["A3"] = 3  # static but has content
    else:
        scores["A3"] = None  # human

    # ---- B 块 技艺 ----
    # B1 景别: infer 近景/特写 if faces; else human
    faces = shot.get("_faces", 0)
    scores["B1"] = 3 if faces == 0 else (4 if faces <= 2 else 5)

    # B2 运镜: from optical-flow estimate
    cm = shot.get("camera_move") or "固定"
    b2_map = {"固定": 4, "摇": 4, "推": 4, "拉": 4, "推拉": 4, "跟随": 5,
              "航拍": 5, "手持": 3, "未知": 3, None: 3, "": 3}
    scores["B2"] = b2_map.get(cm, 3)

    # B3 构图: human (needs strong VLM) -> None
    scores["B3"] = None

    # B4 色彩协调: <=3 dominant + reasonable saturation + contrast layers
    nd = max(1, len(doms))
    b4 = 5.0 if nd <= 3 else (3.0 if nd <= 5 else 1.5)
    if sat > 0.85 or sat < 0.02:      # oversaturated or colourless
        b4 -= 1.5
    if contrast < 0.05:               # flat / grey-black
        b4 -= 1.0
    scores["B4"] = _clamp5(b4)

    # B5 光线质感: exposure + dynamic range
    exp = (shot.get("lighting") or {}).get("exposure", "正常")
    b5 = {"正常": 4.5, "欠曝": 2.0, "过曝": 2.0}.get(exp, 4.0)
    b5 += min(0.5, contrast)          # some dynamic range is good
    scores["B5"] = _clamp5(b5)

    # B6 运动节奏: from mean optical flow magnitude
    mf = motion.get("mean_flow", 0.0) or 0.0
    if mf < 0.005:
        b6 = 3      # static
    elif mf > 0.08:
        b6 = 3      # too shaky / chaotic
    else:
        b6 = 4      # smooth motion
    scores["B6"] = b6

    # ---- C 块 情感 ----
    # C1 情绪基调 (proxy from colour temp + keywords)
    if temp == "cool":
        c1_mood = "宁静/清冷"
    elif temp == "warm":
        c1_mood = "温暖/活力"
    else:
        c1_mood = "中性"
    if any(w in cap for w in ["伤感", "孤独", "sad", "lonely"]):
        c1_mood = "伤感"
    elif any(w in cap for w in ["浪漫", "love", "romantic", "婚礼"]):
        c1_mood = "浪漫"
    scores["C1"] = c1_mood  # categorical, kept for the report

    # C2 情绪强度: motion + saturation
    c2 = 2.5 + 1.5 * min(1.0, mf / 0.05) + 1.0 * min(1.0, sat / 0.6)
    scores["C2"] = _clamp5(c2)

    # C3 美学综合: human double-label (rubric §8) -> None here;
    # but record a proxy so the UI always has a number.
    scores["C3"] = None

    # ---- V 块 数据价值 ----
    tech = shot.get("_tech", {}) or {}
    W = tech.get("width", 0)
    sharp = tech.get("sharpness", 0.0)
    # V1 技术质量
    v1 = 3.0
    if W >= 1920:
        v1 += 1.0
    elif W >= 1280:
        v1 += 0.5
    v1 += min(1.0, sharp / 200.0)
    scores["V1"] = _clamp5(v1)

    # V2 唯一性: pHash hamming vs known set (needs dataset)
    ph = shot.get("_phash")
    if known_phashes and ph is not None:
        from .data_value import hamming
        min_d = min((hamming(ph, k) for k in known_phashes), default=64)
        scores["V2"] = _clamp5(1 + min(1.0, min_d / 12.0) * 4)
    else:
        scores["V2"] = None  # needs dataset comparison

    # V3/V4 need dataset distribution -> None
    scores["V3"] = None
    scores["V4"] = None

    # V5 可标注性: single shot, clear boundaries
    scores["V5"] = 5 if shot.get("frame_count", 0) > 15 else 3

    # V6 许可合规: Pexels source == CC0
    src = (shot.get("_source") or "").lower()
    scores["V6"] = 5 if ("pexels" in src or "cc0" in src) else 3

    # ---- proxy overall (computable subset) ----
    num, den = 0.0, 0.0
    for k, w in PROXY_WEIGHTS.items():
        v = scores.get(k)
        if isinstance(v, (int, float)):
            num += w * v
            den += w
    aesthetic_proxy = round(num / den, 2) if den else None

    return {"scores": scores, "aesthetic_proxy": aesthetic_proxy,
            "mood_label": scores["C1"]}


def score_report(report: dict, video_path: str = "") -> dict:
    """Augment a full pipeline report with per-shot scores + compliance."""
    from .compliance import assess

    report["_path"] = video_path or report.get("_path", "")
    known = []  # phashes from sibling reports (for V2 uniqueness)
    # collect phashes from sibling reports for V2 uniqueness
    rep_dir = os.path.join(os.path.dirname(__file__), "..", "..",
                           "output", "reports")
    for f in glob.glob(os.path.join(rep_dir, "*.json")):
        try:
            d = __import__("json").load(open(f, encoding="utf-8"))
            if d.get("video_id") == report.get("video_id"):
                continue
            for s in d.get("shots", []):
                p = s.get("phash") or (s.get("_phash"))
                if p:
                    known.append(p)
        except Exception:
            pass

    tech = report.get("data_value", {}).get("tech", {})
    src = report.get("source", "")

    for s in report["shots"]:
        # mid frame for compliance
        mid_sec = (s["start_sec"] + s["end_sec"]) / 2.0
        frame = _read_mid_frame(report.get("_path", ""), mid_sec)
        # face count feeds both B1 and G5
        if frame is not None:
            from .compliance import _faces
            s["_faces"] = _faces(frame)
        s["_tech"] = tech
        s["_source"] = src
        s["_phash"] = report.get("data_value", {}).get("phash")
        sc = score_shot(s, known_phashes=known)
        s["scores"] = sc["scores"]
        s["aesthetic_proxy"] = sc["aesthetic_proxy"]
        # mood: VLM 填了就用 VLM 的（更准）；否则用颜色启发式兜底
        if s.get("mood") is None:
            s["mood"] = sc["mood_label"]
        s["compliance"] = assess(s, mid_frame=frame)
        # cleanup temp helper keys
        for k in ("_faces", "_tech", "_source", "_phash"):
            s.pop(k, None)
    return report


def _read_mid_frame(video_path: str, mid_sec: float):
    if not video_path or not os.path.exists(video_path):
        return None
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(mid_sec * fps)))
        ok, frame = cap.read()
        cap.release()
        return frame if ok else None
    except Exception:
        return None
