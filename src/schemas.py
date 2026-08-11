#!/usr/bin/env python3
"""Unified data models for Lychee (pydantic v2).

These schemas are the single source of truth for the shapes that flow
between the video pipeline, the compliance/RLHF scorers, the music
recommendation engine, the FastAPI layer and the agent tools.

They intentionally mirror the JSON produced today (output/reports/*.json,
data/music/enriched_songs.json ...) so existing artifacts validate as-is:

    from src.schemas import VideoReport
    VideoReport.model_validate(json.load(open("output/reports/xxx.json")))

All fields that current upstream steps may leave empty are Optional.
"""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


# --------------------------------------------------------------------------
# video side
# --------------------------------------------------------------------------

class ColorStats(BaseModel):
    """D3 colour block computed by src/video/color_analysis.py."""
    model_config = ConfigDict(extra="allow")

    dominant_colors: Optional[list] = None
    brightness_mean: Optional[float] = None
    saturation_mean: Optional[float] = None
    color_contrast: Optional[float] = None
    warm_cool: Optional[str] = None


class Lighting(BaseModel):
    """B5 lighting derived from colour metrics."""
    model_config = ConfigDict(extra="allow")

    exposure: Optional[str] = None          # 欠曝 / 正常 / 过曝
    dynamic_range: Optional[float] = None


class ComplianceResult(BaseModel):
    """G-block compliance gate (src/video/compliance.py)."""
    model_config = ConfigDict(extra="allow")

    verdict: str = "review"                 # compliant / review / blocked
    reasons: list[str] = Field(default_factory=list)
    faces_detected: Optional[int] = None


class Shot(BaseModel):
    """One shot record inside a video report."""
    model_config = ConfigDict(extra="allow")

    shot_id: int | str                      # pipeline emits "shot_001" style ids
    start_sec: float
    end_sec: float
    frame_count: Optional[int] = None

    color: Optional[ColorStats] = None
    lighting: Optional[Lighting] = None
    camera_move: Optional[str] = None
    motion: Optional[dict] = None

    content_caption: Optional[str] = None   # A1, local VLM
    shot_scale: Optional[str] = None
    composition: Optional[str] = None
    mood: Optional[str] = None

    # RLHF reward features (aesthetic_scorer.py): A1-A3/B1-B5/C1-C2/V1-V6
    scores: Optional[dict] = None
    aesthetic_proxy: Optional[float] = None
    mood_label: Optional[str] = None
    aesthetic_score: Optional[float] = None

    compliance: Optional[ComplianceResult] = None


class DataValue(BaseModel):
    model_config = ConfigDict(extra="allow")

    tech: Optional[dict] = None             # resolution/fps/bitrate quality
    phash: Optional[str] = None             # perceptual hash for dedup


class VideoReport(BaseModel):
    """Full per-video analysis report (output/reports/<video_id>.json)."""
    model_config = ConfigDict(extra="allow")

    video_id: str
    source: Optional[str] = None
    duration_sec: Optional[float] = None
    fps: Optional[float] = None
    frame_count: Optional[int] = None
    shot_count: int = 0
    data_value: Optional[DataValue] = None
    shots: list[Shot] = Field(default_factory=list)


# --------------------------------------------------------------------------
# music side
# --------------------------------------------------------------------------

class Song(BaseModel):
    """One enriched song (data/music/enriched_songs.json)."""
    model_config = ConfigDict(extra="allow")

    title: str
    artists: list[str] = Field(default_factory=list)
    album: Optional[str] = None
    songmid: Optional[str] = None
    styles: list[str] = Field(default_factory=list)
    moods: list[str] = Field(default_factory=list)
    popularity: Optional[float] = None       # 0..100 (play-count proxy)
    melody: Optional[dict] = None             # MelodyProfile dict (proxy or real)
    melody_source: Optional[str] = None       # proxy / sheet


class TasteProfile(BaseModel):
    """Aggregated listener taste (data/music/taste_profile.json)."""
    model_config = ConfigDict(extra="allow")

    style_dist: dict[str, float] = Field(default_factory=dict)
    mood_dist: dict[str, float] = Field(default_factory=dict)
    top_artists: list = Field(default_factory=list)
    pop_preference: Optional[float] = None
    melody_centroid: Optional[dict] = None


class ScoredSong(BaseModel):
    """One recommendation with factor breakdown (recommender_v2 output)."""
    model_config = ConfigDict(extra="allow")

    title: str
    artists: list[str] = Field(default_factory=list)
    score: float = 0.0
    factors: dict[str, float] = Field(default_factory=dict)  # singer/style/pop/melody
    reason: Optional[str] = None
    styles: list[str] = Field(default_factory=list)
    moods: list[str] = Field(default_factory=list)


class Playlist(BaseModel):
    model_config = ConfigDict(extra="allow")

    label: str
    songs: list[ScoredSong] = Field(default_factory=list)


class RecommendResult(BaseModel):
    """Multi-playlist recommendation payload (/api/music/v2/recommend)."""
    model_config = ConfigDict(extra="allow")

    playlists: dict[str, Playlist] = Field(default_factory=dict)
    generated_at: Optional[str] = None


# --------------------------------------------------------------------------
# self test
# --------------------------------------------------------------------------

if __name__ == "__main__":
    import json, glob, os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    ok = err = 0
    for f in glob.glob(os.path.join(root, "output", "reports", "*.json")):
        try:
            VideoReport.model_validate(json.load(open(f)))
            ok += 1
        except Exception as e:
            err += 1
            print(f"  INVALID {os.path.basename(f)}: {str(e)[:120]}")
    print(f"video reports: {ok} valid, {err} invalid")

    es = os.path.join(root, "data", "music", "enriched_songs.json")
    if os.path.exists(es):
        songs = json.load(open(es))
        bad = 0
        for s in songs:
            try:
                Song.model_validate(s)
            except Exception:
                bad += 1
        print(f"songs: {len(songs) - bad}/{len(songs)} valid")

    tp = os.path.join(root, "data", "music", "taste_profile.json")
    if os.path.exists(tp):
        TasteProfile.model_validate(json.load(open(tp)))
        print("taste profile: valid")
