#!/usr/bin/env python3
"""Local provider: content-based recommendation from a curated catalog.

Works fully offline. Maps the user's imported listening history onto the
catalog by artist name, builds a taste profile (genre/mood/language
weights + top artists), then ranks every other catalog artist by feature
similarity and returns their songs as "new-to-you" recommendations.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .base import Provider, TasteProfile, Song


def _safe(name: str) -> str:
    return (name or "").strip().lower()


class LocalProvider(Provider):
    def __init__(self, catalog: dict):
        self.artists = {_safe(a["name"]): a for a in catalog.get("artists", [])}
        # index: similar-link adjacency for bonus scoring
        self.similar_index: dict[str, set[str]] = defaultdict(set)
        for a in catalog.get("artists", []):
            for s in a.get("similar", []):
                self.similar_index[_safe(a["name"])].add(_safe(s))

    # ---- profile -----------------------------------------------------
    def build_profile(self, history: list[dict]) -> TasteProfile:
        genres = defaultdict(float)
        moods = defaultdict(float)
        langs = defaultdict(float)
        artist_types = defaultdict(float)
        melody = defaultdict(float)
        lyricists = defaultdict(float)
        plays = defaultdict(float)        # canonical name -> weight
        raw_played = set()                # lowercased names, for matching
        pop_num, pop_den = 0.0, 0.0       # weighted avg popularity
        for row in history:
            raw = row.get("artist")
            name = _safe(raw)
            if not name:
                continue
            w = float(row.get("weight", row.get("plays", 1)) or 1)
            raw_played.add(name)
            art = self.artists.get(name)
            canon = art["name"] if art else raw
            plays[canon] += w
            if art:
                for g in art.get("genres", []):
                    genres[g] += w
                for m in art.get("mood", []):
                    moods[m] += w
                langs[art.get("language", "未知")] += w
                artist_types[art.get("type", "solo")] += w
                for mel in art.get("melody", []):
                    melody[mel] += w
                for ly in art.get("lyricists", []):
                    lyricists[ly] += w
                if art.get("popularity") is not None:
                    pop_num += float(art["popularity"]) * w
                    pop_den += w
        top = sorted(plays.items(), key=lambda x: -x[1])
        return TasteProfile(
            top_artists=[n for n, _ in top],
            genres=dict(genres),
            moods=dict(moods),
            languages=dict(langs),
            artist_types=dict(artist_types),
            melody=dict(melody),
            lyricists=dict(lyricists),
            popularity_pref=(pop_num / pop_den) if pop_den else 50.0,
            played=raw_played,
        )

    # ---- similarity --------------------------------------------------
    @staticmethod
    def _overlap(a: Iterable[str], b: set[str]) -> float:
        a = [x for x in a if x]
        if not a or not b:
            return 0.0
        inter = len(set(a) & b)
        union = len(set(a) | b)
        return inter / union if union else 0.0

    def _score(self, artist: dict, profile: TasteProfile) -> float:
        known = self.artists.get(_safe(artist["name"]))
        if not known:
            return 0.0
        g = self._overlap(artist.get("genres", []), set(profile.genres)) * 2.0
        m = self._overlap(artist.get("mood", []), set(profile.moods)) * 1.5
        mel = self._overlap(artist.get("melody", []), set(profile.melody)) * 1.2
        lang_match = 1.0 if artist.get("language") in profile.languages else 0.0
        # artist-type match (band vs solo preference)
        type_match = 0.5 if artist.get("type", "solo") in profile.artist_types else 0.0
        # lyricist match: if user loves a lyricist this artist uses
        ly_match = 0.0
        for ly in artist.get("lyricists", []):
            if ly in profile.lyricists:
                ly_match = 0.8
        # direct similar-link bonus
        bonus = 0.0
        for ta in profile.top_artists[:8]:
            if _safe(artist["name"]) in self.similar_index.get(ta, set()):
                bonus = max(bonus, 1.2)
        return g + m + mel + lang_match + type_match + ly_match + bonus

    # ---- recommend ---------------------------------------------------
    def recommend(self, profile: TasteProfile, top_n: int = 15) -> list[Song]:
        if not profile.top_artists:
            return []
        scored = []
        for art in self.artists.values():
            name = _safe(art["name"])
            if name in profile.played:
                continue  # new-to-you only
            s = self._score(art, profile)
            if s > 0:
                scored.append((s, art))
        scored.sort(key=lambda x: -x[0])

        songs: list[Song] = []
        for score, art in scored:
            for song in art.get("songs", []):
                songs.append(Song(
                    artist=art["name"],
                    title=song.get("title", ""),
                    year=song.get("year"),
                    reason=f"相似度 {score:.2f}｜曲风 {','.join(art.get('genres', [])[:2])}",
                ))
            if len(songs) >= top_n:
                break
        # sort by year desc (newer first) for a "fresh" feel, keep top_n
        songs.sort(key=lambda s: (s.year or 0), reverse=True)
        return songs[:top_n]
