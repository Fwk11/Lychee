#!/usr/bin/env python3
"""Weekly music recommender engine.

Pipeline:
  1. load catalog (data/music/catalog.json) + listening history
     (data/music/listening_history.json, or the .example fallback)
  2. build a TasteProfile from the history
  3. ask the active Provider for "new-to-you" songs
  4. write output/recommendations/weekly_YYYYMMDD.md

Provider selection:
  - default: LocalProvider (offline, uses the curated catalog)
  - if QQMUSIC_API_BASE is set: QQMusicProvider (live similar-artist pull)
"""
from __future__ import annotations

import os
import json
import datetime as dt

from .providers.base import Provider
from .providers.local import LocalProvider
from .providers.qqmusic import QQMusicProvider, SetupNeeded

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
CATALOG = os.path.join(ROOT, "data", "music", "catalog.json")
HISTORY = os.path.join(ROOT, "data", "music", "listening_history.json")
HISTORY_EXAMPLE = os.path.join(ROOT, "data", "music", "listening_history.example.json")
OUT_DIR = os.path.join(ROOT, "output", "recommendations")


def _load_json(path: str) -> dict | list | None:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_provider() -> Provider:
    if os.environ.get("QQMUSIC_API_BASE"):
        try:
            return QQMusicProvider()
        except SetupNeeded as e:
            print(f"[warn] QQ音乐未就绪: {e}; 回退到本地引擎")
    return LocalProvider(_load_json(CATALOG) or {"artists": []})


def build_history() -> tuple[list[dict], bool]:
    """Return (history_rows, used_example_flag)."""
    data = _load_json(HISTORY)
    if data:
        return data if isinstance(data, list) else data.get("history", []), False
    ex = _load_json(HISTORY_EXAMPLE)
    if ex:
        return ex if isinstance(ex, list) else ex.get("history", []), True
    return [], True


def build_recommendation(top_n: int = 15) -> dict:
    """Structured recommendation for API use (generate() renders this to md)."""
    _load_json(CATALOG)  # keep catalog read in one place
    history, used_example = build_history()
    provider = load_provider()
    profile = provider.build_profile(history)
    songs = provider.recommend(profile, top_n=top_n)
    return {
        "used_example": used_example,
        "profile": {
            "matched_artists": len(profile.played),
            "top_artists": profile.top_artists[:10],
            "genres": dict(sorted(profile.genres.items(), key=lambda x: -x[1])[:8]),
            "moods": dict(sorted(profile.moods.items(), key=lambda x: -x[1])[:8]),
            "melody": dict(sorted(profile.melody.items(), key=lambda x: -x[1])[:8]),
            "artist_types": dict(sorted(profile.artist_types.items(), key=lambda x: -x[1])),
            "lyricists": dict(sorted(profile.lyricists.items(), key=lambda x: -x[1])[:6]),
            "languages": dict(sorted(profile.languages.items(), key=lambda x: -x[1])[:5]),
            "popularity_pref": round(profile.popularity_pref, 1),
        },
        "songs": [{"artist": s.artist, "title": s.title, "year": s.year,
                   "reason": s.reason} for s in songs],
        "engine": "QQ音乐(在线)" if os.environ.get("QQMUSIC_API_BASE") else "本地知识库(离线)",
    }


def generate(weekly: bool = True) -> str:
    history, used_example = build_history()

    provider = load_provider()
    profile = provider.build_profile(history)
    songs = provider.recommend(profile, top_n=15)

    os.makedirs(OUT_DIR, exist_ok=True)
    today = dt.date.today()
    # find this week's Monday for a stable weekly filename
    monday = today - dt.timedelta(days=today.weekday())
    fname = f"weekly_{monday.isoformat()}.md" if weekly else f"run_{today.isoformat()}.md"
    out_path = os.path.join(OUT_DIR, fname)

    lines = []
    lines.append(f"# 每周新歌推荐 · {monday.isoformat()} 那一周")
    lines.append("")
    if used_example:
        lines.append("> ⚠️ 当前用的是 **示例听歌记录**。把你的真实记录写进 "
                     "`data/music/listening_history.json` 后重新运行，推荐会更贴合你。")
        lines.append("")
    lines.append("## 你的喜好画像（由导入的听歌记录生成）")
    lines.append("")
    lines.append(f"- 命中曲库歌手：**{len(profile.played)}** 位")
    lines.append(f"- Top 歌手：{', '.join(profile.top_artists[:8]) or '（空）'}")
    if profile.genres:
        top_g = sorted(profile.genres.items(), key=lambda x: -x[1])[:6]
        lines.append(f"- 主要曲风：{', '.join(f'{g}({int(w)})' for g, w in top_g)}")
    if profile.moods:
        top_m = sorted(profile.moods.items(), key=lambda x: -x[1])[:6]
        lines.append(f"- 情绪倾向：{', '.join(f'{m}({int(w)})' for m, w in top_m)}")
    if profile.melody:
        top_me = sorted(profile.melody.items(), key=lambda x: -x[1])[:6]
        lines.append(f"- 旋律偏好：{', '.join(f'{m}({int(w)})' for m, w in top_me)}")
    if profile.artist_types:
        top_t = sorted(profile.artist_types.items(), key=lambda x: -x[1])
        lines.append(f"- 艺人形态：{', '.join(f'{t}({int(w)})' for t, w in top_t)}")
    if profile.lyricists:
        top_ly = sorted(profile.lyricists.items(), key=lambda x: -x[1])[:5]
        lines.append(f"- 偏好填词人：{', '.join(f'{l}({int(w)})' for l, w in top_ly)}")
    if profile.languages:
        top_l = sorted(profile.languages.items(), key=lambda x: -x[1])[:4]
        lines.append(f"- 语言偏好：{', '.join(f'{l}({int(w)})' for l, w in top_l)}")
    lines.append(f"- 偏好热度：约 {profile.popularity_pref:.0f} 分（越高越偏大众热歌）")
    lines.append("")
    lines.append("## 本周推荐（new-to-you）")
    lines.append("")
    if not songs:
        lines.append("_暂时没有可推荐的歌曲——曲库里可能没有与你喜好匹配的歌手，"
                     "或在 `listening_history.json` 里加入更多歌手试试。_")
    else:
        for i, s in enumerate(songs, 1):
            yr = f" ({s.year})" if s.year else ""
            lines.append(f"{i}. **{s.title}** — {s.artist}{yr}")
            if s.reason:
                lines.append(f"   - 为什么：{s.reason}")
    lines.append("")
    lines.append("---")
    lines.append(f"生成时间：{dt.datetime.now().strftime('%Y-%m-%d %H:%M')} ｜ "
                 f"引擎：{'QQ音乐(在线)' if os.environ.get('QQMUSIC_API_BASE') else '本地知识库(离线)'}")
    text = "\n".join(lines)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    return out_path


if __name__ == "__main__":
    p = generate()
    print(f"推荐歌单已生成: {p}")
