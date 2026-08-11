"""Lychee 音乐 · 进阶特色功能（主流音乐媒体不具备）。

四个差异化能力：
  1. contextual  —— 「此刻」情境推荐：综合 时间×天气×活动×情绪，实时生成
                    「此刻你该听什么」并给出可解释理由（主流只有「猜你喜欢」）。
  2. personality —— 音乐人格画像：聚合你跨平台多年数据，给出人格原型 + 开放度。
  3. evolution   —— 口味演化时间线：把你历年听歌按月份聚合成风格权重轨迹。
  4. rlhf        —— 歌曲工业级 RLHF 标注：给单曲打多维美学/技术/情绪分(带锚)，
                    形成你个人的偏好 reward 信号（串联 Lychee 的 RLHF 标注体系）。
  5. deconstruct —— AI 段落解构 + 接歌/混音建议：用旋律特征做谐波混音(Camelot)配对。

所有函数都对缺失数据做优雅降级，绝不让接口 500。
"""
from __future__ import annotations

import json
import math
import os
import re
from collections import defaultdict

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA = os.path.join(ROOT, "data", "music")

from src.music.user_ctx import user_path

# ---- 数据加载（带缓存，避免重复读盘；按 user_id 隔离） ----
_CACHE: dict = {}


def _cache_key(user_id: str | None, name: str) -> str:
    return f"{user_id or 'default'}:{name}"


def _load(name: str, user_id: str | None = None):
    key = _cache_key(user_id, name)
    if key in _CACHE:
        return _CACHE[key]
    p = user_path(user_id, name)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        _CACHE[key] = json.load(f)
    return _CACHE[key]


def _enriched(user_id: str | None = None) -> list[dict]:
    d = _load("enriched_songs.json", user_id=user_id)
    if isinstance(d, dict):
        # 可能是 {songmid: song} 或 {"songs": [...]}
        if "songs" in d:
            return d["songs"]
        return list(d.values())
    return d or []


def _profile(user_id: str | None = None) -> dict:
    return _load("taste_profile_v3.json", user_id=user_id) or _load("taste_profile.json", user_id=user_id) or {}


# ---- 通用工具 ----
def _norm(x, lo, hi):
    if hi == lo:
        return 0.0
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def _song_card(s: dict) -> dict:
    mel = s.get("melody", {}) or {}
    return {
        "songmid": s.get("songmid"),
        "title": s.get("title"),
        "artists": s.get("artists", []),
        "album": s.get("album"),
        "styles": s.get("styles", []),
        "moods": s.get("moods", []),
        "popularity": s.get("popularity", 50),
        "bpm": mel.get("tempo") or mel.get("bpm"),
        "key": mel.get("key"),
        "mode": mel.get("mode"),
        "energy": mel.get("energy"),
    }


# =====================================================================
# 1. 「此刻」情境推荐
# =====================================================================
# 情境 → 期望风格 / 情绪 的映射（规则可解释，非黑盒）
_CONTEXT_RULES = {
    "工作":   {"styles": ["电子", "轻音乐", "纯音乐", "R&B", "Lo-Fi"], "moods": ["平静", "专注"], "note": "需要专注，突出器乐与低人声"},
    "学习":   {"styles": ["轻音乐", "纯音乐", "古典", "民谣"], "moods": ["平静", "专注"], "note": "弱干扰，旋律稳定"},
    "运动":   {"styles": ["电子", "摇滚", "嘻哈", "EDM"], "moods": ["亢奋", "燃"], "note": "高能量、强节奏驱动"},
    "健身":   {"styles": ["电子", "摇滚", "嘻哈", "EDM"], "moods": ["亢奋", "燃"], "note": "高 BPM 助推心率"},
    "睡眠":   {"styles": ["轻音乐", "纯音乐", "古典", "环境"], "moods": ["平静", "舒缓"], "note": "低频、慢板、无突兀变化"},
    "放松":   {"styles": ["爵士", "民谣", "轻音乐", "R&B"], "moods": ["舒缓", "慵懒"], "note": "松弛但有余韵"},
    "社交":   {"styles": ["流行", "华语流行", "舞曲", "电子"], "moods": ["欢快", "热闹"], "note": "易共鸣、能带气氛"},
    "通勤":   {"styles": ["流行", "华语流行", "摇滚"], "moods": ["轻松"], "note": "碎片时间，旋律易记"},
    "雨天":   {"styles": ["民谣", "情歌", "爵士"], "moods": ["舒缓", "忧郁"], "note": "雨声氛围感"},
    "夜晚":   {"styles": ["爵士", "R&B", "电子", "轻音乐"], "moods": ["慵懒", "舒缓"], "note": "夜间低频更耐听"},
    "清晨":   {"styles": ["轻音乐", "民谣", "流行"], "moods": ["轻松", "清新"], "note": "唤醒但不刺耳"},
}
_HOUR_DEFAULTS = {  # 时段兜底
    (0, 6): ("睡眠", "夜晚"),
    (6, 11): ("清晨", "通勤"),
    (11, 14): ("通勤", "放松"),
    (14, 18): ("工作", "学习"),
    (18, 22): ("放松", "社交"),
    (22, 24): ("夜晚", "放松"),
}


def _hour_fallback(hour: int):
    for (a, b), acts in _HOUR_DEFAULTS.items():
        if a <= hour < b:
            return acts
    return ("放松", "夜晚")


def contextual_recommend(context: dict | None = None, top_n: int = 12,
                         user_id: str | None = None) -> dict:
    """综合情境生成「此刻该听什么」+ 可解释理由。

    context: {hour, weekday, activity, weather, mood} 任一可缺，缺失部分用时段兜底。
    返回 {context_used, reason, items:[{card, score, why}]}
    """
    ctx = context or {}
    hour = ctx.get("hour")
    activity = (ctx.get("activity") or "").strip()
    weather = (ctx.get("weather") or "").strip()
    mood = (ctx.get("mood") or "").strip()

    # 解析情境
    acts = []
    if activity and activity in _CONTEXT_RULES:
        acts.append(activity)
    if weather in ("雨", "雨天", "下雨", "rain", "rainy"):
        acts.append("雨天")
    if mood:
        # 情绪作为软偏好（不直接映射规则，仅影响排序）
        pass
    if hour is not None:
        acts.extend(_hour_fallback(int(hour)))
    # 去重保序
    seen = set()
    acts = [a for a in acts if not (a in seen or seen.add(a))]

    rules = [_CONTEXT_RULES[a] for a in acts if a in _CONTEXT_RULES]
    want_styles = []
    want_moods = []
    for r in rules:
        want_styles += r["styles"]
        want_moods += r["moods"]
    # 用户真实口味（风格权重）
    prof = _profile(user_id=user_id)
    style_dist = prof.get("style_dist", {}) or {}
    s_total = sum(style_dist.values()) or 1.0

    songs = _enriched(user_id=user_id)
    scored = []
    for s in songs:
        styles = s.get("styles", []) or []
        moods = s.get("moods", []) or []
        # 情境匹配度：命中期望风格/情绪越多越高
        ctx_hit = sum(2.0 for st in styles if st in want_styles) + \
                  sum(1.0 for mo in moods if mo in want_moods)
        ctx_score = _norm(ctx_hit, 0, 6.0)
        # 口味亲和度：该曲风格在用户口味里的占比
        sd = sum(style_dist.get(st, 0.0) for st in styles)
        taste_score = _norm(sd, 0, s_total)
        # 情绪软偏好
        mood_boost = 0.0
        if mood and any(mood in (mo or "") for mo in moods):
            mood_boost = 0.1
        final = 0.55 * ctx_score + 0.4 * taste_score + mood_boost
        if ctx_score <= 0 and taste_score <= 0:
            continue
        # 理由
        why_bits = []
        if ctx_score > 0.25:
            hit = next((st for st in styles if st in want_styles), None)
            why_bits.append(f"契合「{acts[0] if acts else '此刻'}」·{hit or '氛围'}")
        if taste_score > 0.05:
            why_bits.append("你的口味常听")
        if s.get("popularity", 0) and s["popularity"] > 75:
            why_bits.append("高热度")
        scored.append({
            "card": _song_card(s),
            "score": round(final, 4),
            "why": " · ".join(why_bits) if why_bits else "综合均衡",
        })
    scored.sort(key=lambda x: x["score"], reverse=True)
    items = scored[:top_n]

    reason = "；".join(r["note"] for r in rules) or "按你的口味与当前时段综合推荐"
    return {
        "context_used": {"activities": acts, "want_styles": list(dict.fromkeys(want_styles)),
                         "want_moods": list(dict.fromkeys(want_moods))},
        "reason": reason,
        "count": len(items),
        "items": items,
    }


# =====================================================================
# 2. 音乐人格画像
# =====================================================================
def _style_entropy(style_dist: dict) -> float:
    vals = [v for v in style_dist.values() if v > 0]
    tot = sum(vals) or 1.0
    h = 0.0
    for v in vals:
        p = v / tot
        if p > 0:
            h -= p * math.log(p)
    return h


def music_personality(user_id: str | None = None) -> dict:
    prof = _profile(user_id=user_id)
    style_dist = prof.get("style_dist", {}) or {}
    if not style_dist:
        return {"ok": False, "reason": "尚未生成口味画像（先导入你的音乐库）"}
    tot = sum(style_dist.values()) or 1.0
    norm = {k: v / tot for k, v in style_dist.items()}
    top = sorted(norm.items(), key=lambda x: x[1], reverse=True)
    top_styles = [t[0] for t in top[:5]]
    entropy = _style_entropy(style_dist)
    # 开放度：熵越高 + 风格数越多 → 越开放
    openness = round(min(1.0, entropy / 2.5) * 0.7 + min(1.0, len(style_dist) / 12) * 0.3, 3)

    # 原型判定
    s = set(top_styles)
    pioneer = {"电子", "摇滚", "爵士", "独立", "EDM", "嘻哈"}
    calm = {"民谣", "轻音乐", "纯音乐", "古典", "环境"}
    love = {"华语流行", "情歌", "流行"}
    if openness > 0.55 and (s & pioneer):
        arche = "先锋探索者"
        desc = "你不被单一风格定义，电子/摇滚/爵士都在你的版图里——永远在找下一首新鲜的。"
    elif (s & love) and top[0][1] > 0.25:
        arche = "深度专情派"
        desc = "华语流行与情歌是你的情绪锚点，少数歌手占据了你绝大部分播放——为共鸣而听。"
    elif s & calm:
        arche = "安静文艺派"
        desc = "民谣与轻音乐是你的避风港，你听的是氛围与叙事，而非热闹。"
    else:
        arche = "均衡鉴赏家"
        desc = "你的口味宽而稳，什么都能欣赏，少有极端偏好——包容性极强。"

    top_artists = prof.get("top_artists", [])[:8]
    return {
        "ok": True,
        "archetype": arche,
        "archetype_desc": desc,
        "openness": openness,
        "style_entropy": round(entropy, 3),
        "top_styles": [{"name": k, "share": round(v, 4)} for k, v in top[:8]],
        "top_artists": [{"name": a.get("name"), "plays": a.get("plays")} for a in top_artists],
        "library_size": prof.get("library_size"),
    }


# =====================================================================
# 3. 口味演化时间线
# =====================================================================
def _bucket_style(history: list[dict], enriched_index: dict) -> list[dict]:
    """按月份聚合听歌的风格分布轨迹。"""
    per_month = defaultdict(lambda: defaultdict(float))
    for h in history:
        ts = h.get("time") or h.get("date") or h.get("play_time") or h.get("timestamp")
        if not ts:
            continue
        # 解析 YYYY-MM / YYYY-MM-DD / 含时间戳的字符串
        m = re.search(r"(\d{4})[-/](\d{1,2})", str(ts))
        if not m:
            continue
        ym = f"{m.group(1)}-{int(m.group(2)):02d}"
        # 关联歌曲风格
        key = h.get("songmid") or h.get("title")
        song = enriched_index.get(key)
        styles = (song or {}).get("styles", []) if song else []
        w = float(h.get("weight") or h.get("count") or h.get("plays") or 1)
        if not styles:
            # 退而用 title 匹配
            styles = (song or {}).get("styles", [])
        if styles:
            for st in styles:
                per_month[ym][st] += w
        else:
            per_month[ym]["(未知)"] += w
    series = []
    for ym in sorted(per_month.keys()):
        dist = dict(per_month[ym])
        tot = sum(dist.values()) or 1.0
        series.append({
            "month": ym,
            "total_weight": round(tot, 1),
            "style_share": {k: round(v / tot, 4) for k, v in
                            sorted(dist.items(), key=lambda x: x[1], reverse=True)[:8]},
        })
    return series


def music_evolution(user_id: str | None = None) -> dict:
    hist = _load("listening_history.json", user_id=user_id)
    if isinstance(hist, dict):
        hist = hist.get("history") or hist.get("records") or []
    if not hist:
        return {"ok": False,
                "reason": "没有带时间戳的听歌历史",
                "hint": "导出带时间戳的播放记录（如 QQ音乐年度听歌报告）即可启用演化时间线",
                "series": []}
    enriched = _enriched(user_id=user_id)
    # 建索引：songmid / title
    idx = {}
    for s in enriched:
        if s.get("songmid"):
            idx[s["songmid"]] = s
        if s.get("title"):
            idx[s["title"]] = s
    series = _bucket_style(hist, idx)
    if not series:
        return {"ok": False,
                "reason": "历史记录缺少可解析的时间或歌曲关联",
                "hint": "播放记录需包含时间字段，且能匹配曲库中的歌曲名或 songmid",
                "series": []}
    # 找演化：首尾风格对比
    first, last = series[0], series[-1]
    return {
        "ok": True,
        "span": [first["month"], last["month"]],
        "points": len(series),
        "series": series,
        "first_top": list(first["style_share"].items())[:3],
        "last_top": list(last["style_share"].items())[:3],
    }


# =====================================================================
# 4. 歌曲工业级 RLHF 标注
# =====================================================================
# 音乐标注维度（带锚，呼应视频 RLHF 体系，但针对音乐）
MUSIC_RLHF_SCHEMA = {
    "schema_version": "1.0",
    "name": "Lychee 音乐工业级标注 · RLHF",
    "scores": [
        {"name": "m_melody", "title": "旋律记忆点", "anchors": {"1": "无记忆点", "5": "顺耳", "7": "洗脑", "10": "封神旋律"}},
        {"name": "m_arrange", "title": "编曲层次", "anchors": {"1": "单薄", "5": "完整", "7": "丰富", "10": "电影级"}},
        {"name": "m_vocal", "title": "人声表现", "anchors": {"1": "平淡", "5": "合格", "7": "动人", "10": "教科书"}},
        {"name": "m_emotion", "title": "情绪传达", "anchors": {"1": "无感", "5": "正常", "7": "感染", "10": "破防"}},
        {"name": "m_production", "title": "制作质量", "anchors": {"1": "粗糙", "5": "干净", "7": "精良", "10": "母带级"}},
        {"name": "m_overall", "title": "整体惊艳度", "anchors": {"1": "平庸", "4": "好听", "7": "惊艳", "9": "神作"}},
    ],
    "rlhf": [
        {"name": "loop_desire", "title": "单曲循环欲", "values": ["不会", "偶尔", "经常性", "必须循环"]},
        {"name": "share_desire", "title": "分享欲", "values": ["不会", "会发给懂的人", "立刻发朋友圈", "强制安利"]},
        {"name": "quality_tier", "title": "质量梯队", "values": ["S", "A", "B", "C", "D"]},
        {"name": "subjective", "title": "主观感受", "values": ["惊艳", "舒适", "平淡", "不适"]},
        {"name": "confidence", "title": "标注置信度", "values": ["高", "中", "低"]},
    ],
    "compliance": [
        {"name": "copyright", "title": "版权/翻唱争议", "values": ["无", "翻唱", "采样未授权", "争议中"]},
        {"name": "lyric_sensitive", "title": "歌词敏感", "values": ["无", "成人", "政治敏感", "其他"]},
        {"name": "commercial", "title": "可否商用", "values": ["可", "不可", "需授权"]},
    ],
}


def music_rlhf_annotate(query: str | None = None, user_id: str | None = None) -> dict:
    """为单曲生成工业级 RLHF 标注模板（含按音频特征/口味预填）。

    返回 {song, schema, prefill}；prefill 是可直接编辑的分数草稿。
    """
    songs = _enriched(user_id=user_id)
    target = None
    if query:
        q = query.strip().lower()
        for s in songs:
            if q in (s.get("title") or "").lower() or q in " ".join(s.get("artists", [])).lower():
                target = s
                break
    if target is None and songs:
        target = songs[0]
    if target is None:
        return {"ok": False, "reason": "曲库为空"}
    mel = target.get("melody", {}) or {}
    # 预填：基于能量/BPM/流行度的粗略代理（人工可改）
    energy = mel.get("energy")
    pop = target.get("popularity", 50)
    prefill = {
        "m_production": 5 + int(_norm(pop, 0, 100) * 4),   # 流行度代理制作
        "m_overall": 4 + int(_norm(pop, 0, 100) * 4),
        "loop_desire": "偶尔" if (energy or 0) < 0.6 else "经常性",
        "quality_tier": "B" if pop < 60 else ("A" if pop < 85 else "S"),
        "confidence": "中",
    }
    return {
        "ok": True,
        "song": _song_card(target),
        "schema": MUSIC_RLHF_SCHEMA,
        "prefill": prefill,
        "note": "分数为按音频特征/热度生成的草稿，需人工校正后才能作为 reward 信号。",
    }


# =====================================================================
# 5. AI 段落解构 + 接歌/混音建议
# =====================================================================
# 调性 → Camelot 码（用于谐波混音配对）
_MAJOR_CAMELOT = {"C": "8B", "G": "9B", "D": "10B", "A": "11B", "E": "12B", "B": "1B",
                  "F#": "2B", "Db": "3B", "Ab": "4B", "Eb": "5B", "Bb": "6B", "F": "7B"}
_MINOR_CAMELOT = {"Am": "8A", "Em": "9A", "Bm": "10A", "F#m": "11A", "C#m": "12A", "G#m": "1A",
                  "D#m": "2A", "Bbm": "3A", "Fm": "4A", "Cm": "5A", "Gm": "6A", "Dm": "7A"}


def _camelot(key: str | None):
    if not key:
        return None
    k = key.strip()
    if k in _MAJOR_CAMELOT:
        return _MAJOR_CAMELOT[k]
    if k in _MINOR_CAMELOT:
        return _MINOR_CAMELOT[k]
    # 尝试 minor 形式（如 "a"）
    if (k[0].upper() + "m") in _MINOR_CAMELOT:
        return _MINOR_CAMELOT[k[0].upper() + "m"]
    return None


def _harmonic_compatible(c1, c2):
    """Camelot 同码或相邻（±1 且同大小调）即可平滑接歌。"""
    if not c1 or not c2:
        return False
    n1, t1 = int(c1[:-1]), c1[-1]
    n2, t2 = int(c2[:-1]), c2[-1]
    if t1 != t2:
        return False
    diff = abs(n1 - n2)
    return diff == 0 or diff == 1 or diff == 11


def deconstruct(query: str | None = None, top_n: int = 8,
                user_id: str | None = None) -> dict:
    """AI 段落解构 + 接歌/混音建议。

    用真实旋律特征（tempo=BPM、mode=调式）做兼容性配对：
      - 节奏兼容：BPM 差 ≤ max(6, bpm*8%)
      - 调式兼容：major-major / minor-minor 更易平滑过渡
    注：曲库旋律来自谱子 OMR，未做主音检测，故用「节奏+调式」代理谐波混音
    （完整 Camelot 谐波圈需音频主音，可由后续音频分析补齐）。
    """
    songs = _enriched(user_id=user_id)
    for s in songs:
        m = s.get("melody", {}) or {}
        s["_bpm"] = m.get("tempo") or m.get("bpm")
        mv = m.get("mode")
        s["_mode_cls"] = "major" if (isinstance(mv, (int, float)) and mv >= 0.5) else (
            "minor" if isinstance(mv, (int, float)) else None)
    target = None
    if query:
        q = query.strip().lower()
        for s in songs:
            if q in (s.get("title") or "").lower():
                target = s
                break
    if target is None:
        target = next((s for s in songs if s.get("_bpm")), songs[0]) if songs else None
    if target is None:
        return {"ok": False, "reason": "曲库为空"}
    t_bpm = target.get("_bpm")
    t_mode = target.get("_mode_cls")
    candidates = []
    for s in songs:
        if s is target:
            continue
        bpm_diff = None
        bpm_ok = False
        if t_bpm and s.get("_bpm"):
            bpm_diff = abs(t_bpm - s["_bpm"])
            bpm_ok = bpm_diff <= max(6, t_bpm * 0.08)
        mode_ok = (t_mode and s.get("_mode_cls") == t_mode)
        if bpm_ok:
            candidates.append({
                "card": _song_card(s),
                "bpm": s["_bpm"],
                "mode": s["_mode_cls"],
                "bpm_diff": bpm_diff,
                "match": "节奏+调式双兼容" if (bpm_ok and mode_ok) else "节奏兼容",
            })
    candidates.sort(key=lambda x: (x["bpm_diff"] if x["bpm_diff"] is not None else 999))
    return {
        "ok": True,
        "song": _song_card(target),
        "bpm": t_bpm,
        "mode": t_mode,
        "mix_candidates": candidates[:top_n],
        "note": "基于 BPM 接近度 + 调式匹配给出接歌建议（谱子 OMR 无主音，完整谐波混音待音频分析补齐）。",
    }
