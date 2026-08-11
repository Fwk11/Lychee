# -*- coding: utf-8 -*-
"""镜头解析与文本切分：把 LLM 吐出的（可能残缺的）JSON 收拢成干净镜头列表。"""
from __future__ import annotations

import json
import re
from typing import List

from ._common import _CAMERA_FALLBACK, _DIALOG_RE, _BEAT_KW
from .prompts import _RICH_FIELDS, _SKELETON_FIELDS


def _norm_keys(obj: dict) -> dict:
    """把 LLM 输出的字段名归一化：去掉首尾空格、冒号、冒号空格变体。

    3b 常输出 "shot_type: " 这种带尾部冒号空格的 key，导致标准字段解析为空。
    """
    if not isinstance(obj, dict):
        return obj
    out = {}
    for k, v in obj.items():
        if isinstance(k, str):
            k = k.strip().rstrip(":：").strip().replace(" ", "")
        # 同名 key 冲突时（如 "shot_type: " 与 "shot_type"）保留非空值，
        # 避免后出现的空值覆盖前面已解析到的有效内容
        if k in out:
            old = out[k]
            old_empty = old is None or (isinstance(old, str) and not old.strip())
            new_empty = v is None or (isinstance(v, str) and not v.strip())
            if old_empty and not new_empty:
                out[k] = v
            continue
        out[k] = v
    return out


def _story_density(text: str) -> dict:
    """统计剧情密度信号：字数、对白量、节拍转折、有效段落。"""
    return {
        "n_chars": len(text),
        "n_dialog": len(_DIALOG_RE.findall(text)),
        "n_beats": len(_BEAT_KW.findall(text)),
        "n_paras": len([p for p in text.split("\n") if len(p.strip()) > 20]),
    }


def _dynamic_shot_count(text: str, chapter_count: int = 1) -> int:
    """按剧情丰富度决定镜头数，不设固定值。

    平铺直叙的过渡章 → 少镜；对白密集、转折多、场景频繁切换的高潮章 → 多镜。
    """
    d = _story_density(text)
    n = d["n_chars"] / 500.0          # 叙事体量基线
    n += d["n_dialog"] * 0.28         # 对白多 → 拆正反打
    n += d["n_beats"] * 0.25         # 每个转折需要独立镜头承接
    n += d["n_paras"] * 0.06         # 段落切换的节奏感
    n = int(round(n))
    lo = max(3, chapter_count * 2)
    hi = min(40, max(18, chapter_count * 7))
    return max(lo, min(hi, n))


def _segment_text(body: str, target: int = 1000) -> List[str]:
    """把正文切成语义段，逐段生成骨架。

    3b 一次吐 20 个镜头的 JSON 必然截断；分段后每次只要 2-6 镜，
    既保证输出完整，也让长章节自然产出更多镜头。
    """
    paras = [p.strip() for p in body.split("\n") if p.strip()]
    if not paras:
        return [body] if body.strip() else []
    segs: List[str] = []
    cur: List[str] = []
    cur_len = 0
    for p in paras:
        cur.append(p)
        cur_len += len(p)
        if cur_len >= target:
            segs.append("\n".join(cur))
            cur, cur_len = [], 0
    if cur:
        tail = "\n".join(cur)
        # 尾巴太短就并进上一段，避免产出一个信息量不足的镜头组
        if segs and len(tail) < target * 0.35:
            segs[-1] += "\n" + tail
        else:
            segs.append(tail)
    return segs


def _collect_names(shots: List[dict]) -> List[str]:
    """从骨架里汇总出场角色名。"""
    seen: List[str] = []
    for s in shots:
        for raw in re.split(r"[、,，/;；]", s.get("characters") or ""):
            n = raw.strip().strip("（）() ")
            # 去除角色名后的状态括号，如“柳神（未显形）/柳神(未显形)”统一得到“柳神”
            n = re.sub(r"（[^）]*）", "", n)
            n = re.sub(r"\([^)]*\)", "", n).strip()
            if not n or len(n) > 14:
                continue
            if any(w in n for w in ("村民", "众人", "群众", "路人", "旁白", "无")):
                continue
            if n not in seen:
                seen.append(n)
    return seen


def _parse_shots(raw: str, n_expect: int) -> List[dict]:
    """解析整段生成结果（含 dialogue/提示词），用于"整部→连续剧分集"分支。"""
    # 1) 优先整体解析：Ollama JSON 模式强制顶层 object，模型会把数组包成 {"shots":[...]}
    shots = []
    try:
        obj = json.loads(raw)
        if isinstance(obj, list):
            shots = obj
        elif isinstance(obj, dict):
            obj = _norm_keys(obj)
            for key in ("shots", "list", "items", "data", "result", "scenes"):
                v = obj.get(key)
                if isinstance(v, list):
                    shots = v
                    break
            else:
                # 单个镜头被当成 object 返回（没包数组）
                if obj.get("scene") or obj.get("plot") or obj.get("shot_type"):
                    shots = [obj]
    except Exception:
        pass
    # 2) 退路：从文本里捞第一个 [...] 数组
    if not shots:
        try:
            m = re.search(r"\[.*\]", raw, re.S)
            if m:
                shots = json.loads(m.group(0))
        except Exception:
            return []
    if not isinstance(shots, list):
        return []
    fields = ("scene", "shot_type", "camera", "plot", "arrangement",
              "characters", "dialogue", "narration", "action",
              "lighting_tech", "vfx", "color_script", "audio", "emotion",
              "bridge", "video_prompt_anime")
    _EMPTY = {"..", "...", ".", "无", "无。", "无！", "none", "null",
              "（场景名）", "（景别）", "（运镜）", "（剧情功能）", "（场景布置）",
              "（人物动作）", "（技术与灯光）", "（特效）", "（出场角色）",
              "（人物台词）", "（旁白/台词）", "（视频提示词）",
              "image_prompt", "video_prompt",
              "video_prompt_anime"}
    cleaned = []
    for i, s in enumerate(shots, 1):
        if not isinstance(s, dict):
            continue
        s = _norm_keys(s)
        s["shot_id"] = i
        for f in fields:
            v = s.get(f, "")
            v = v.strip() if isinstance(v, str) else ""
            if v.lower() in _EMPTY:
                v = ""
            if f == "scene":
                v = v.strip("（）() ")
            if f == "camera" and len(v) <= 1 and v in _CAMERA_FALLBACK:
                v = _CAMERA_FALLBACK[v]
            s[f] = v
        has_substance = any(s.get(k) for k in ("plot", "arrangement", "action",
                                                 "dialogue", "narration"))
        if s.get("scene") and has_substance:
            cleaned.append(s)
    return cleaned[: max(n_expect + 4, 6)]


def _parse_skeleton(raw: str, n_expect: int) -> List[dict]:
    """解析阶段1生成的分镜骨架（不含台词/提示词）。"""
    shots = []
    try:
        obj = json.loads(raw)
        if isinstance(obj, list):
            shots = obj
        elif isinstance(obj, dict):
            for key in ("shots", "list", "items", "data", "result", "scenes"):
                v = obj.get(key)
                if isinstance(v, list):
                    shots = v
                    break
            else:
                if obj.get("scene") or obj.get("plot") or obj.get("shot_type"):
                    shots = [obj]
    except Exception:
        pass
    if not shots:
        try:
            m = re.search(r"\[.*\]", raw, re.S)
            if m:
                shots = json.loads(m.group(0))
        except Exception:
            return []
    if not isinstance(shots, list):
        return []
    cleaned = []
    for i, s in enumerate(shots, 1):
        if not isinstance(s, dict):
            continue
        s = _norm_keys(s)
        s["shot_id"] = i
        for f in _SKELETON_FIELDS:
            v = s.get(f, "")
            v = v.strip() if isinstance(v, str) else ""
            if f == "scene":
                v = v.strip("（）() ")
            if f == "camera" and len(v) <= 1 and v in _CAMERA_FALLBACK:
                v = _CAMERA_FALLBACK[v]
            s[f] = v
        # 富字段先置空，稍后逐镜补全
        for f in _RICH_FIELDS:
            s.setdefault(f, "")
        if s.get("scene") and (s.get("plot") or s.get("arrangement") or s.get("narration")):
            cleaned.append(s)
    return cleaned[: max(n_expect + 4, 6)]


def _parse_rich(raw: str) -> dict:
    """解析单镜补全结果（dialogue/action/灯光/特效/两套提示词）。"""
    out = {f: "" for f in _RICH_FIELDS}
    try:
        obj = json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return out
        try:
            obj = json.loads(m.group(0))
        except Exception:
            return out
    if not isinstance(obj, dict):
        return out
    obj = _norm_keys(obj)
    for f in _RICH_FIELDS:
        v = obj.get(f, "")
        v = v.strip() if isinstance(v, str) else ""
        if f == "dialogue":
            v = _clean_dialogue(v)
        out[f] = v
    return out


def _clean_dialogue(d: str) -> str:
    """清洗 3b 偶发的台词噪声：字面转义序列、角括号、首尾杂字符。"""
    if not d:
        return ""
    d = re.sub(r"\\u[0-9a-fA-F]{4}", "", d)          # 去掉字面 \u300c 之类
    d = d.replace("「", "").replace("」", "").replace("『", "").replace("』", "")
    d = d.replace("\u300c", "").replace("\u300d", "").replace("\u300e", "").replace("\u300f", "")
    d = d.replace("\u201c", "").replace("\u201d", "").replace("\u2018", "").replace("\u2019", "")
    d = d.replace('"', "").replace("'", "")
    # 删掉混进台词的旁白/配音说明，如"（慈祥男声）""（画外音）"
    d = re.sub(r"[（(][^）)]*(?:男声|女声|画外|旁白|配音|解说|os)[^）)]*[）)]", "", d, flags=re.I)
    d = d.replace("\t", " ").replace("\r", "")
    d = d.replace("{", "").replace("}", "")  # 清掉 3b 偶发残留的花括号（含行内孤立 "}"）
    d = re.sub(r"[ \t]{2,}", " ", d)
    d = d.strip().lstrip("\"' \t。. ").rstrip("\"' \t")
    # 先删除“缺角色名”的冒号片段（如“：太阳初升…”“。：你们明白吗？”）
    d = re.sub(r"(?<![一-鿿A-Za-z0-9·•）\)])：\S*?(?=[。！？，、\s]|$)", "", d)
    d = re.sub(r"\s{2,}", " ", d).strip()
    # 按“空格+角色：”或“句末标点+空格”切分，逐段保留合法“角色（情绪）：台词”，丢弃无冒号残片
    _ROLE = r"[一-鿿A-Za-z0-9·•]{1,10}(?:（[^）]{1,10}）)?"
    parts = re.split(rf"(?<=[。！？])\s+|(?=\s+{_ROLE}：)", d)
    kept = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        m = re.match(rf"^({_ROLE})：(.*)$", p)
        if not m:
            continue
        role, line = m.group(1), m.group(2).strip().strip("。. ")
        if not line:
            continue
        kept.append(f"{role}：{line}")
    if kept:
        return " ".join(kept)
    return ""


def _parse_dialogue(raw: str) -> str:
    """抽取 dialogue 字符串值，避免整段 JSON 被杂散花括号干扰（3b 偶发前置 '}'）。"""
    m = re.search(r'"dialogue"\s*:\s*"((?:[^"\\]|\\.)*)"', raw, re.S)
    if m:
        d = m.group(1)
        d = d.replace('\\"', '"').replace("\\n", " ").replace("\\/", "/")
        d = d.strip().lstrip("{}[],\"' \t").rstrip("{}[],\"' \t")
        return _clean_dialogue(d)
    # 兜底：尝试整体 JSON
    try:
        obj = json.loads(raw)
    except Exception:
        m2 = re.search(r"\{.*\}", raw, re.S)
        if not m2:
            return ""
        try:
            obj = json.loads(m2.group(0))
        except Exception:
            return ""
    if not isinstance(obj, dict):
        return ""
    d = obj.get("dialogue", "")
    d = d.strip() if isinstance(d, str) else ""
    d = d.lstrip("{}[],\"' \t").rstrip("{}[],\"' \t")
    return _clean_dialogue(d)


def _parse_characters(raw: str) -> dict:
    """从骨架输出里解析 characters 数组，转成 {name:{zh,en}}（与 bible 结构一致，省独立建库调用）。"""
    bible: dict = {}
    try:
        obj = json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return bible
        try:
            obj = json.loads(m.group(0))
        except Exception:
            return bible
    if not isinstance(obj, dict):
        return bible
    obj = _norm_keys(obj)
    items = obj.get("characters")
    if not isinstance(items, list):
        return bible
    for it in items:
        if not isinstance(it, dict):
            continue
        name = (it.get("name") or "").strip().strip("（）() ")
        # 丢弃含分隔符/空/过长的名字：模型有时把"小不点, 石云峰"这种多角色名拼成一个 name，
        # 真正的单名会在 build_character_bible 用 _collect_names 正确拆分后单独建库
        if not name or len(name) > 14 or ("," in name) or ("，" in name) or ("、" in name):
            continue
        if any(w in name for w in ("村民", "众人", "群众", "路人", "旁白", "无")):
            continue
        zh = (it.get("zh") or "").strip()
        en = (it.get("en") or "").strip()
        if not zh:
            continue
        bible[name] = {"zh": zh, "en": en}
    return bible


def _parse_batch_rich(raw: str, n: int) -> list:
    """解析批量补全结果：一个长度与输入相同的数组，每个元素含 9 个富字段。"""
    out: list = []
    try:
        obj = json.loads(raw)
    except Exception:
        m = re.search(r"\[.*\]", raw, re.S)
        if not m:
            m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return []
        try:
            obj = json.loads(m.group(0))
        except Exception:
            return []
    if isinstance(obj, dict):
        for key in ("shots", "list", "items", "data", "result"):
            if isinstance(obj.get(key), list):
                obj = obj[key]
                break
        else:
            obj = []
    if not isinstance(obj, list):
        return []
    for s in obj[:n]:
        if not isinstance(s, dict):
            out.append({f: "" for f in _RICH_FIELDS})
            continue
        s = _norm_keys(s)
        d = {}
        for f in _RICH_FIELDS:
            v = s.get(f, "")
            v = v.strip() if isinstance(v, str) else ""
            if f == "dialogue":
                v = _clean_dialogue(v)
            d[f] = v
        out.append(d)
    return out


def _parse_scene(raw: str, want: int):
    """解析场景级生成结果：返回 (scene_meta:dict, shots:list)。

    scene_meta: {heading, time, location, characters, event}
    shots: 骨架镜头列表（与 _parse_skeleton 同款清洗）。
    """
    meta = {"heading": "", "time": "", "location": "", "characters": "", "event": ""}
    shots: List[dict] = []
    try:
        obj = json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return meta, shots
        try:
            obj = json.loads(m.group(0))
        except Exception:
            return meta, shots
    if not isinstance(obj, dict):
        return meta, shots
    obj = _norm_keys(obj)
    sm = obj.get("scene_meta") or obj.get("scene") or {}
    if isinstance(sm, dict):
        for k in ("heading", "time", "location", "characters", "event"):
            v = sm.get(k, "")
            if isinstance(v, str):
                meta[k] = v.strip()
    arr = obj.get("shots") or obj.get("list") or []
    if isinstance(arr, list):
        for s in arr:
            if not isinstance(s, dict):
                continue
            s = _norm_keys(s)
            for f in _SKELETON_FIELDS:
                v = s.get(f, "")
                v = v.strip() if isinstance(v, str) else ""
                if f == "scene":
                    v = v.strip("（）() ")
                if f == "camera" and len(v) <= 1 and v in _CAMERA_FALLBACK:
                    v = _CAMERA_FALLBACK[v]
                s[f] = v
            for f in _RICH_FIELDS:
                s.setdefault(f, "")
            if s.get("scene") and (s.get("plot") or s.get("arrangement") or s.get("narration")):
                shots.append(s)
    return meta, shots[: max(want + 1, 3)]
