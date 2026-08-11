# -*- coding: utf-8 -*-
"""一体化提示词编织与技术参数推导（不额外消耗 LLM，确定性组装）。"""
from __future__ import annotations

import difflib
import re
from typing import List, Optional

from ._common import (
    _EXAMPLE_SHOT,
    _FAST_CAM,
    _SHOT_COMPOSITION,
    _SHOT_DURATION,
    _SHOT_TYPE_EN,
    _SLOW_CAM,
    _STYLE_EN,
    _STYLE_ZH,
)
from .bible import _shot_character_design


def _ngrams(s: str, n: int = 2) -> set:
    s = re.sub(r"[，。；、,.;：:\s]", "", s or "")
    return {s[i:i + n] for i in range(max(0, len(s) - n + 1))}


def _too_similar(a: str, b: str, thr: float = 0.45) -> bool:
    """3b 常把 action 和 arrangement 写成同一件事，用 2-gram 重合率判重。"""
    A, B = _ngrams(a), _ngrams(b)
    if not A or not B:
        return False
    return len(A & B) / min(len(A), len(B)) >= thr


def _uniq_segments(*vals: str) -> List[str]:
    """去重：完全包含或语义高度重合的只保留信息量更大的那条。"""
    out: List[str] = []
    for v in vals:
        v = (v or "").strip().strip("。；;，, ")
        if not v:
            continue
        dup = -1
        for i, o in enumerate(out):
            if v == o or v in o or o in v or _too_similar(v, o):
                dup = i
                break
        if dup >= 0:
            if len(v) > len(out[dup]):
                out[dup] = v
            continue
        out.append(v)
    return out


def _is_copied_example(text: str, shot: dict) -> bool:
    """3b 常直接照抄示范镜头的英文提示词，检测并丢弃。

    仅当与示范的 video_prompt_anime 高度相似才判定为照抄，
    避免误杀正文里合法出现的 willow/石村 等词。
    """
    ex = _EXAMPLE_SHOT.get("video_prompt_anime", "")
    t = (text or "").strip()
    if not t or not ex:
        return False
    tl, el = t.lower(), ex.lower()
    if tl == el:
        return True
    return difflib.SequenceMatcher(None, tl, el).ratio() > 0.6


def _derive_spec(shot: dict, prev: Optional[dict] = None) -> dict:
    """由景别/运镜/场景变化推导时长、构图、转场，稳定且零成本。"""
    st = (shot.get("shot_type") or "").strip()
    cam = shot.get("camera") or ""
    dur = _SHOT_DURATION.get(st, 3.5)
    if _FAST_CAM.search(cam):
        dur = max(1.5, dur - 1.0)
    elif _SLOW_CAM.search(cam):
        dur += 1.0
    if prev is None:
        trans = "淡入起幅"
    elif (prev.get("scene") or "") == (shot.get("scene") or ""):
        trans = "硬切（同场景内部切换，保持轴线一致）"
    else:
        trans = "叠化转场（跨场景，0.5 秒交叠）"
    return {
        "duration": f"{dur:g}秒",
        "composition": _SHOT_COMPOSITION.get(st, "35mm，f/4，三分法构图"),
        "transition": trans,
        "aspect": "16:9",
        "fps": "24fps（作画基准），输出 25/30fps",
    }


def compose_prompt(shot: dict, bible: Optional[dict] = None) -> str:
    """把该镜全部制作信息编成一份可直接投喂视频大模型的详细提示词（中文）。"""
    spec = shot.get("spec") or _derive_spec(shot)
    st = (shot.get("shot_type") or "中景").strip()
    lines: List[str] = [
        f"【{st} · {spec['duration']} · {spec['aspect']} · 国漫】"
    ]

    def add(label: str, *vals: str):
        segs = _uniq_segments(*vals)
        if segs:
            lines.append(f"{label}：" + "；".join(segs) + "。")

    design = _shot_character_design(shot, bible or {}, "zh")
    if design:
        lines.append(f"角色形象（每镜必须保持一致）：{design}。")
    elif shot.get("characters"):
        lines.append(f"出场角色：{shot['characters']}。")

    add("画面内容", shot.get("arrangement"))
    add("角色表演", shot.get("action"))
    add("情绪节奏", shot.get("emotion"))
    add("镜头衔接", shot.get("bridge"))
    cam = (shot.get("camera") or "").strip().strip("。；;，, ")
    if cam:
        lines.append(f"镜头运动：{cam}。构图：{spec['composition']}。")
    else:
        lines.append(f"构图：{spec['composition']}。")
    add("光影", shot.get("lighting_tech"))
    add("色彩", shot.get("color_script"))
    add("特效", shot.get("vfx"))
    add("声音", shot.get("audio"))
    if shot.get("dialogue"):
        lines.append(f"口型台词：{shot['dialogue']}")
    lines.append(f"转场：{spec['transition']}。")
    lines.append(f"画面风格：{_STYLE_ZH}。")
    lines.append(f"帧率：{spec['fps']}。")
    return "\n".join(lines)


def compose_prompt_en(shot: dict, bible: Optional[dict] = None) -> str:
    """英文版一体化提示词；模型抄示范或未产出时返回空。"""
    core = (shot.get("video_prompt_anime") or "").strip()
    if not core or _is_copied_example(core, shot):
        return ""
    # 国漫风格下清掉真人摄影词汇
    core = re.sub(r"\b(photo)?realistic\b,?\s*", "", core, flags=re.I)
    core = re.sub(r"\b(film still|live action|35mm film look|real actors?)\b,?\s*",
                  "", core, flags=re.I)
    # 剥掉模型自带的风格尾巴，统一换成一套，避免 4k / smooth motion 重复堆叠
    core = re.sub(
        r"[,\s]*\b(4k|8k|smooth motion|cinematic composition|cel shading|"
        r"volumetric lighting|high(ly)? (quality|detailed)|"
        r"chinese donghua( animation)? style|donghua style|anime style)\b",
        "", core, flags=re.I)
    core = re.sub(r"\s*,\s*(?=,)", "", core)
    core = re.sub(r"\s{2,}", " ", core).strip(" ,.")
    st_en = _SHOT_TYPE_EN.get((shot.get("shot_type") or "").strip(), "")
    if st_en and st_en not in core.lower():
        core = f"{st_en}, {core}"
    design_en = _shot_character_design(shot, bible or {}, "en")
    if design_en:
        core = f"{core}. Character design (keep consistent across shots): {design_en}"
    return f"{core}, {_STYLE_EN}."


def attach_prompts(shot: dict, bible: Optional[dict] = None,
                   prev: Optional[dict] = None) -> dict:
    """给单个镜头挂上技术参数与 prompt / prompt_en。"""
    shot["spec"] = _derive_spec(shot, prev)
    shot["prompt"] = compose_prompt(shot, bible)
    shot["prompt_en"] = compose_prompt_en(shot, bible)
    return shot


def attach_all(shots: List[dict], bible: Optional[dict] = None) -> List[dict]:
    """整组镜头挂 prompt，转场需要参考上一镜的场景。"""
    prev = None
    for s in shots:
        attach_prompts(s, bible, prev)
        prev = s
    return shots
