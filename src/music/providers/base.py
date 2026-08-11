#!/usr/bin/env python3
"""Provider abstraction for the music recommender.

A provider turns a taste profile into candidate songs. The default
`LocalProvider` works fully offline from data/music/catalog.json. The
`QQMusicProvider` (pluggable) can pull live similar-artist / new-release
data when the user runs a QQ音乐 API server locally.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Song:
    artist: str
    title: str
    year: int | None = None
    reason: str = ""


@dataclass
class TasteProfile:
    """Aggregated taste built from imported listening history.

    Multi-dimensional: not just genre, but also artist, band-vs-solo,
    melody descriptors, popularity (comment-count proxy) and lyricists.
    """
    top_artists: list[str] = field(default_factory=list)
    genres: dict[str, float] = field(default_factory=dict)
    moods: dict[str, float] = field(default_factory=dict)
    languages: dict[str, float] = field(default_factory=dict)
    artist_types: dict[str, float] = field(default_factory=dict)  # solo/band/group
    melody: dict[str, float] = field(default_factory=dict)        # 激昂/舒缓/轻快/忧郁...
    lyricists: dict[str, float] = field(default_factory=dict)     # 填词人
    popularity_pref: float = 50.0                                 # 平均偏好的热度
    played: set[str] = field(default_factory=set)                 # lowercased names listened


class Provider(ABC):
    @abstractmethod
    def build_profile(self, history: list[dict]) -> TasteProfile:
        """Turn raw listening history into a TasteProfile."""

    @abstractmethod
    def recommend(self, profile: TasteProfile, top_n: int = 15) -> list[Song]:
        """Return a ranked list of recommended songs (new-to-user)."""
