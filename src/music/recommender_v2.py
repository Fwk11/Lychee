#!/usr/bin/env python3
"""多维推荐引擎 v2 —— 基于你这 701 首歌单本身的喜好画像。

四维加权打分（每首歌都给出因子分解与理由）：
  f_singer  歌手亲和   → 来自听歌记录播放量
  f_style   风格亲和   → 歌的风格 vs 你的风格分布
  f_pop     热度       → 播放量归一（评论量代理）
  f_melody  旋律相似   → 歌的旋律画像 vs 你的平均旋律画像（真实谱子优先）

产出一套综合推荐歌单（为你精选），每首带四维因子与理由。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
from src.music.melody import MelodyProfile, similarity, load_sheets
from src.music.user_ctx import user_path

HERE = os.path.join(ROOT, "data", "music")


def _paths(user_id: str | None = None):
    return {
        "enriched": user_path(user_id, "enriched_songs.json"),
        "profile": user_path(user_id, "taste_profile.json"),
        "history": user_path(user_id, "listening_history.json"),
    }

# 打分权重（可在 API 层覆盖）
DEFAULT_WEIGHTS = {"singer": 0.30, "style": 0.35, "pop": 0.15, "melody": 0.20}


def _mp_from_dict(d: dict) -> MelodyProfile:
    return MelodyProfile(
        pitch_range=d.get("pitch_range", 0.5), mean_pitch=d.get("mean_pitch", 0.5),
        contour=d.get("contour", 0.0), mode=d.get("mode", 0.0),
        rhythmic=d.get("rhythmic", 0.4), tempo=d.get("tempo", 100.0),
        interval=d.get("interval", 0.4), source=d.get("source", "proxy"),
    )


def _norm(x: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.5
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


@dataclass
class Scored:
    song: dict
    f_singer: float
    f_style: float
    f_pop: float
    f_melody: float
    score: float
    reason: str


def load_inputs(user_id: str | None = None) -> tuple[list[dict], dict, dict]:
    p = _paths(user_id)
    enriched = json.load(open(p["enriched"], encoding="utf-8")) if os.path.exists(p["enriched"]) else []
    profile = json.load(open(p["profile"], encoding="utf-8")) if os.path.exists(p["profile"]) else {}
    history = json.load(open(p["history"], encoding="utf-8")) if os.path.exists(p["history"]) else []
    return enriched, profile, history


def build_singer_weights(history: list[dict]) -> dict:
    w = {}
    maxp = 1.0
    for row in history:
        a = (row.get("artist") or "").strip()
        if not a:
            continue
        p = float(row.get("plays") or 0)
        w[a] = max(w.get(a, 0.0), p)
        maxp = max(maxp, p)
    return {k: v / maxp for k, v in w.items()}


def score_all(enriched, profile, history, weights=None) -> list[Scored]:
    weights = weights or DEFAULT_WEIGHTS
    singer_w = build_singer_weights(history)
    style_dist = profile.get("style_dist", {})
    mood_dist = profile.get("mood_dist", {})
    avg_mel = _mp_from_dict(profile.get("avg_melody", {}))
    sheets = load_sheets()  # songmid -> {profile}
    s_total = sum(style_dist.values()) or 1.0
    m_total = sum(mood_dist.values()) or 1.0

    out = []
    for s in enriched:
        arts = s.get("artists", [])
        # 歌手亲和
        if arts:
            f_singer = max((singer_w.get(a, 0.0) for a in arts), default=0.0)
        else:
            f_singer = 0.0
        # 风格亲和（加权命中）
        sd = sum(style_dist.get(st, 0.0) for st in s.get("styles", []))
        f_style = _norm(sd, 0, s_total) if s_total else 0.0
        md = sum(mood_dist.get(mo, 0.0) for mo in s.get("moods", []))
        f_mood = _norm(md, 0, m_total) if m_total else 0.0
        f_style = max(f_style, f_mood * 0.8)  # 情绪也贡献内容亲和
        # 热度
        f_pop = _norm(s.get("popularity", 50.0), 0, 100.0)
        # 旋律相似（真实谱子优先）
        mel_prof = None
        if s.get("songmid") in sheets:
            mel_prof = _mp_from_dict(sheets[s["songmid"]]["profile"])
        else:
            mel_prof = _mp_from_dict(s.get("melody", {}))
        f_melody = similarity(mel_prof, avg_mel) if avg_mel.source != "none" else 0.5

        score = (weights["singer"] * f_singer + weights["style"] * f_style +
                 weights["pop"] * f_pop + weights["melody"] * f_melody)
        # 理由文本
        bits = []
        if f_singer > 0.5:
            bits.append(f"常听歌手({f_singer:.2f})")
        if f_style > 0.4:
            top_style = next((st for st in s.get("styles", []) if style_dist.get(st, 0) > 0), None)
            bits.append(f"风格合({top_style or '匹配'})")
        if f_melody > 0.6:
            bits.append(f"旋律像({f_melody:.2f})")
        if f_pop > 0.7:
            bits.append(f"高热度({f_pop:.2f})")
        reason = " · ".join(bits) if bits else "综合均衡"
        out.append(Scored(song=s, f_singer=round(f_singer, 3), f_style=round(f_style, 3),
                          f_pop=round(f_pop, 3), f_melody=round(f_melody, 3),
                          score=round(score, 4), reason=reason))
    return out


def _song_view(sc: Scored) -> dict:
    s = sc.song
    return {
        "songmid": s.get("songmid"), "title": s.get("title"),
        "artists": s.get("artists", []), "styles": s.get("styles", []),
        "moods": s.get("moods", []), "popularity": s.get("popularity"),
        "factors": {"singer": sc.f_singer, "style": sc.f_style,
                    "pop": sc.f_pop, "melody": sc.f_melody},
        "score": sc.score, "reason": sc.reason,
    }


def _build_playlists(scored: list[Scored], profile: dict, top_n: int) -> dict:
    """由打分结果生成一套综合推荐歌单（为你精选）。

    只出一套，避免多套主题歌单造成信息过载；每首仍带四维因子分解与理由。
    """
    ranked = sorted(scored, key=lambda x: -x.score)
    best = ranked[:top_n]
    return {
        "featured": {"label": "为你精选", "songs": [_song_view(s) for s in best]},
    }


def recommend_all(top_n: int = 18, weights=None, user_id: str | None = None) -> dict:
    enriched, profile, history = load_inputs(user_id=user_id)
    if not enriched:
        return {"error": "enriched_songs.json 尚未生成，请先运行 enrichment",
                "profile": profile, "playlists": {}}
    scored = score_all(enriched, profile, history, weights)
    playlists = _build_playlists(scored, profile, top_n)
    # 画像摘要
    prof_view = {
        "library_size": profile.get("library_size"),
        "listened_artists": profile.get("listened_artists"),
        "top_artists": profile.get("top_artists", [])[:10],
        "style_dist": profile.get("style_dist", {}),
        "mood_dist": profile.get("mood_dist", {}),
        "avg_melody": profile.get("avg_melody", {}),
        "popularity_pref": profile.get("popularity_pref"),
    }
    sheets = load_sheets()
    sheet_count = len(sheets)
    melody_source = f"qqmusic ({sheet_count} 首)" if sheet_count else "proxy (运行 sheet_fetcher 后自动优先)"

    return {"profile": prof_view, "playlists": playlists,
            "weights": weights or DEFAULT_WEIGHTS,
            "melody_source": melody_source,
            "user_id": user_id or "default"}


# --------------------------------------------------------------------------
# 为他人推荐：从分享卡片解析歌单 → 临时画像 → 同一套引擎打分
# --------------------------------------------------------------------------

def _norm_title(t: str) -> str:
    import re
    return re.sub(r"[\s\-_()（）【】\[\]、，。,.!！?？:：]+", "", (t or "").lower()).strip()


def parse_share_card(text: str) -> list[dict]:
    """从 QQ音乐/网易云分享卡片文本或 JSON 解析出歌单条目。

    支持：
      * 粘贴「我喜欢的音乐」分享页复制出的逐行文本（含「1. 歌名 - 歌手」）
      * 任意「歌名 - 歌手」/「歌名 歌手」纯文本
      * 直接给 JSON 数组 [{title, artist}, ...]
    返回 [{title, artist}]，解析不出时返回空列表。
    """
    if not text or not text.strip():
        return []
    s = text.strip()
    # JSON 直接给
    if s[0] in "[{":
        try:
            data = json.loads(s)
            out = []
            if isinstance(data, list):
                for x in data:
                    if isinstance(x, dict):
                        out.append({"title": str(x.get("title") or x.get("name") or ""),
                                    "artist": str(x.get("artist") or x.get("artists") or "")})
                    elif isinstance(x, str):
                        out.append({"title": x, "artist": ""})
            elif isinstance(data, dict):
                songs = data.get("songs") or data.get("list") or []
                for x in songs:
                    if isinstance(x, dict):
                        out.append({"title": str(x.get("title") or ""),
                                    "artist": str(x.get("artist") or "")})
            return [o for o in out if o["title"]]
        except Exception:
            pass
    # 逐行解析
    SKIP = {"我喜欢的音乐", "我创建的歌单", "歌单", "播放列表", "playlist", "创建者",
            "qq音乐", "网易云音乐", "分享", "二维码", "长按", "识别", "收藏", "评论"}
    out = []
    for raw in s.splitlines():
        line = raw.strip()
        line = re.sub(r"^\d+[\.、)）]\s*", "", line)        # 去序号
        line = line.strip("〉>•·*· ")
        if not line:
            continue
        if line in SKIP or len(line) < 2:
            continue
        # 切分：优先「 - 」，其次「 / 」「、」「——」
        parts = re.split(r"\s+[-–—–/、]\s+|\s+[-–—–/、]\s*|\s*[-–—–/、]\s*", line)
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) >= 2:
            title, artist = parts[0], " ".join(parts[1:])
        else:
            title, artist = parts[0], ""
        if title:
            out.append({"title": title, "artist": artist})
    return out


def _build_temp_profile(matched: list[dict]) -> tuple[dict, list[dict]]:
    """由匹配到的 enriched 歌曲聚合出临时口味画像 + 听歌记录。"""
    style_counter: dict[str, int] = {}
    mood_counter: dict[str, int] = {}
    melodies = []
    artist_freq: dict[str, int] = {}
    for s in matched:
        for st in s.get("styles", []):
            style_counter[st] = style_counter.get(st, 0) + 1
        for mo in s.get("moods", []):
            mood_counter[mo] = mood_counter.get(mo, 0) + 1
        melodies.append(_mp_from_dict(s.get("melody", {})))
        for a in s.get("artists", []):
            artist_freq[a] = artist_freq.get(a, 0) + 1

    # 平均旋律画像
    if melodies:
        avg = MelodyProfile(
            pitch_range=sum(m.pitch_range for m in melodies) / len(melodies),
            mean_pitch=sum(m.mean_pitch for m in melodies) / len(melodies),
            contour=sum(m.contour for m in melodies) / len(melodies),
            mode=sum(m.mode for m in melodies) / len(melodies),
            rhythmic=sum(m.rhythmic for m in melodies) / len(melodies),
            tempo=sum(m.tempo for m in melodies) / len(melodies),
            interval=sum(m.interval for m in melodies) / len(melodies),
            source="proxy",
        )
    else:
        avg = _mp_from_dict({})
    avg_dict = {"pitch_range": avg.pitch_range, "mean_pitch": avg.mean_pitch,
                "contour": avg.contour, "mode": avg.mode, "rhythmic": avg.rhythmic,
                "tempo": avg.tempo, "interval": avg.interval, "source": avg.source}

    pop = sum(s.get("popularity", 50.0) for s in matched) / max(len(matched), 1)
    pop_pref = "高" if pop > 65 else "中" if pop > 40 else "低"

    top_artists = sorted(artist_freq.items(), key=lambda x: -x[1])[:10]
    profile = {
        "style_dist": style_counter,
        "mood_dist": mood_counter,
        "avg_melody": avg_dict,
        "library_size": len(matched),
        "listened_artists": len(artist_freq),
        "top_artists": [{"name": n, "plays": c} for n, c in top_artists],
        "popularity_pref": pop_pref,
    }
    history = [{"artist": a, "plays": c} for a, c in artist_freq.items()]
    return profile, history


def recommend_for_songs(entries: list[dict], top_n: int = 18,
                        user_id: str | None = None) -> dict:
    """给「他人」做推荐：entries=[{title,artist}] → 匹配本曲库 → 临时画像 → 复用引擎打分。

    不污染用户自己的 taste_profile / listening_history。
    返回的 playlists 结构与 recommend_all 一致，前端可直接渲染。
    """
    enriched, _, _ = load_inputs(user_id=user_id)
    if not enriched:
        return {"error": "enriched_songs.json 尚未生成，请先运行 enrichment",
                "playlists": {}, "matched": 0, "unmatched": []}
    # 建标题索引
    index = {}
    for s in enriched:
        index.setdefault(_norm_title(s.get("title", "")), []).append(s)

    matched = []
    unmatched = []
    for e in entries:
        title = (e.get("title") or "").strip()
        artist = (e.get("artist") or "").strip()
        if not title:
            continue
        norm = _norm_title(title)
        cands = index.get(norm, [])
        if not cands and artist:
            # 退而用歌手名粗匹配
            cands = [s for s in enriched
                     if artist.lower() in " ".join(s.get("artists", [])).lower()]
        if not cands:
            cands = [s for s in enriched if norm and norm in _norm_title(s.get("title", ""))]
        if cands:
            matched.append(cands[0])
        else:
            unmatched.append(title)

    if not matched:
        return {"error": "没有匹配到任何已知歌曲，请检查卡片文本",
                "playlists": {}, "matched": 0, "unmatched": unmatched[:20]}

    profile, history = _build_temp_profile(matched)
    scored = score_all(enriched, profile, history)
    playlists = _build_playlists(scored, profile, top_n)
    prof_view = {
        "library_size": profile["library_size"],
        "listened_artists": profile["listened_artists"],
        "top_artists": profile["top_artists"],
        "style_dist": profile["style_dist"],
        "mood_dist": profile["mood_dist"],
        "avg_melody": profile["avg_melody"],
        "popularity_pref": profile["popularity_pref"],
    }
    return {"profile": prof_view, "playlists": playlists,
            "matched": len(matched), "unmatched": unmatched[:20],
            "weights": DEFAULT_WEIGHTS,
            "melody_source": f"qqmusic ({len(load_sheets())} 首)" if load_sheets() else "proxy"}


def recommend_for_card(text: str, top_n: int = 18, user_id: str | None = None) -> dict:
    """直接吃一整段分享卡片文本，端到端为他人推荐（基于指定用户曲库）。"""
    entries = parse_share_card(text)
    if not entries:
        return {"error": "解析不出歌单，请粘贴「我喜欢的音乐」分享文本或歌名列表",
                "playlists": {}, "matched": 0, "unmatched": []}
    return recommend_for_songs(entries, top_n=top_n, user_id=user_id)


if __name__ == "__main__":
    import pprint
    r = recommend_all()
    pprint.pprint({k: (v["label"], len(v["songs"])) for k, v in r["playlists"].items()})
    print("--- 为你精选 Top5 ---")
    for s in r["playlists"]["featured"]["songs"][:5]:
        print(f"  {s['title']} — {','.join(s['artists'])} | {s['reason']}")