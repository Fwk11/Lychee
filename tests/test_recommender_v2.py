"""recommender_v2 推荐引擎单测。

覆盖：
  * score_all          确定性打分（同输入同输出）、分数范围
  * _build_playlists   多套主题歌单 + top_n 截断
  * recommend_all      通过 monkeypatch load_inputs 做不依赖磁盘的端到端校验
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import src.music.recommender_v2 as rv
from src.music.recommender_v2 import Scored


def _enriched(n: int) -> list:
    return [
        {
            "songmid": f"m{i}",
            "title": f"Song{i}",
            "artists": [f"Artist{i % 2}"],
            "styles": ["pop" if i % 2 == 0 else "rock"],
            "moods": ["happy" if i % 2 == 0 else "sad"],
            "popularity": 40.0 + i * 5.0,
            "melody": {},
        }
        for i in range(n)
    ]


def _profile() -> dict:
    return {
        "style_dist": {"pop": 1.0, "rock": 1.0},
        "mood_dist": {"happy": 1.0, "sad": 1.0},
        "avg_melody": {},
        "top_artists": ["Artist0", "Artist1"],
    }


def test_score_all_deterministic():
    enriched = _enriched(6)
    profile = _profile()
    s1 = rv.score_all(enriched, profile, [])
    s2 = rv.score_all(enriched, profile, [])
    assert [round(x.score, 6) for x in s1] == [round(x.score, 6) for x in s2]


def test_score_all_score_range():
    enriched = _enriched(4)
    scored = rv.score_all(enriched, _profile(), [])
    for s in scored:
        assert 0.0 <= s.score <= 1.0
        assert 0.0 <= s.f_style <= 1.0
        assert 0.0 <= s.f_pop <= 1.0


def test_build_playlists_top_n():
    scored = [
        Scored(song={"title": f"S{i}"}, f_singer=0.5, f_style=0.5,
               f_pop=0.5, f_melody=0.5, score=0.5, reason="x")
        for i in range(10)
    ]
    # 当前实现只出一套「为你精选」歌单（避免多套主题歌单信息过载）
    pls = rv._build_playlists(scored, {"style_dist": {}, "mood_dist": {}}, top_n=3)
    assert "featured" in pls
    assert pls["featured"]["label"] == "为你精选"
    assert len(pls["featured"]["songs"]) == 3
    # 每首仍带四维因子分解与理由
    song0 = pls["featured"]["songs"][0]
    assert set(["singer", "style", "pop", "melody"]) <= set(song0["factors"].keys())
    assert "reason" in song0


def test_recommend_all_via_monkeypatch(monkeypatch):
    enriched = _enriched(5)
    profile = _profile()
    history = [{"artist": "Artist0", "plays": 10}]
    monkeypatch.setattr(rv, "load_inputs", lambda *a, **k: (enriched, profile, history))

    d = rv.recommend_all(top_n=5)
    assert "error" not in d
    assert d["user_id"] == "default"
    assert "featured" in d["playlists"]
    assert len(d["playlists"]["featured"]["songs"]) >= 1
    # 因子分解存在
    song0 = d["playlists"]["featured"]["songs"][0]
    assert set(["singer", "style", "pop", "melody"]) <= set(song0["factors"].keys())
    assert "reason" in song0


def test_recommend_all_empty_enriched(monkeypatch):
    monkeypatch.setattr(rv, "load_inputs", lambda *a, **k: ([], {}, []))
    d = rv.recommend_all()
    assert "error" in d
    assert d["playlists"] == {}
