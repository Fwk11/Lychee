#!/usr/bin/env python3
"""QQ音乐 provider adapter (pluggable, optional).

The official QQ音乐 web API requires cookies/sign and is not directly
callable. The common approach is to run a local gateway such as
`QQMusicApi` / `qq-music-api` (open-source) and point this provider at it.

Env:
    QQMUSIC_API_BASE  e.g. http://127.0.0.1:3300  (the local gateway)
    QQMUSIC_COOKIE    optional cookies for higher rate limits

Endpoints assumed (adjust to your gateway's actual routes):
    GET {base}/search?key=<artist>&type=singer
    GET {base}/singer/songs?id=<singer_mid>
    GET {base}/singer/similar?id=<singer_mid>
    GET {base}/new/song                (new releases board)

Without QQMUSIC_API_BASE set, every call raises a clear SetupNeeded error
instead of silently returning empty/fake data.
"""
from __future__ import annotations

import os
import json
import urllib.request
import urllib.parse

from .base import Provider, TasteProfile, Song


class SetupNeeded(RuntimeError):
    pass


class QQMusicProvider(Provider):
    def __init__(self, api_base: str | None = None, cookie: str | None = None):
        self.base = (api_base or os.environ.get("QQMUSIC_API_BASE") or "").rstrip("/")
        self.cookie = cookie or os.environ.get("QQMUSIC_COOKIE")
        if not self.base:
            raise SetupNeeded(
                "QQ音乐 provider 未配置：请先本地运行一个 QQ音乐 API 网关 "
                "(如 QQMusicApi)，再设置环境变量 QQMUSIC_API_BASE=http://127.0.0.1:3300"
            )

    def _get(self, path: str, params: dict) -> dict:
        url = f"{self.base}{path}?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        if self.cookie:
            req.add_header("Cookie", self.cookie)
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))

    def build_profile(self, history: list[dict]) -> TasteProfile:
        # profile building relies on the local catalog (genres/moods live there),
        # so QQMusicProvider delegates to LocalProvider for the profile part.
        from .local import LocalProvider
        # LocalProvider needs a catalog; we pass an empty one and only use
        # top_artists + played (no genre expansion) -- genres come from QQ later.
        lp = LocalProvider({"artists": []})
        return lp.build_profile(history)

    def recommend(self, profile: TasteProfile, top_n: int = 15) -> list[Song]:
        songs: list[Song] = []
        for artist in profile.top_artists[:10]:
            try:
                res = self._get("/search", {"key": artist, "type": "singer"})
                singer_mid = self._extract_singer_mid(res, artist)
                if not singer_mid:
                    continue
                sim = self._get("/singer/similar", {"id": singer_mid})
                for s in sim.get("data", {}).get("list", [])[:5]:
                    smid = s.get("mid") or s.get("singer_mid")
                    if not smid:
                        continue
                    sg = self._get("/singer/songs", {"id": smid})
                    for item in sg.get("data", {}).get("list", [])[:2]:
                        songs.append(Song(
                            artist=item.get("singer", {}).get("name", s.get("name", "")),
                            title=item.get("title", item.get("songname", "")),
                            reason=f"QQ音乐相似歌手（源自你喜欢 {artist}）",
                        ))
            except Exception as e:
                # one artist failing shouldn't kill the whole run
                print(f"  [qqmusic] {artist} 查询失败: {e}")
            if len(songs) >= top_n:
                break
        return songs[:top_n]

    @staticmethod
    def _extract_singer_mid(res: dict, artist: str) -> str | None:
        for item in res.get("data", {}).get("list", []):
            if artist.lower() in (item.get("name", "") or "").lower():
                return item.get("mid") or item.get("singer_mid")
        # fallback: first result
        lst = res.get("data", {}).get("list", [])
        return lst[0].get("mid") if lst else None
