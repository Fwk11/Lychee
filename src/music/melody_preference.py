# -*- coding: utf-8 -*-
"""旋律偏好模型：从歌单读谱结果聚合「用户偏好的歌曲结构画像」。

输入: data/music/sheet_profiles.json  (sheet_fetcher 批量产出)
输出: data/music/user_melody_profile.json

画像字段（全部来自真实数据聚合）:
  bpm_mean / bpm_median          偏好节拍
  intro_sec_median               偏好前奏长度
  line_gap_median                偏好句间隔（旋律呼吸感）
  line_len_avg                   偏好句长
  chars_per_sec_median           偏好演唱密度（快嘴/舒缓）
  repeat_ratio_mean              偏好副歌重复度
  genre_dist                     官方 genre 分布
  coverage                       各字段数据覆盖率（诚实标注）

匹配: similarity(profile, song_sheet) -> 0..1
  按字段可用性动态加权——某字段任一方缺失则跳过该字段并降低总置信度。
"""
from __future__ import annotations

import json
import os
import statistics

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHEETS_PATH = os.path.join(ROOT, "data", "music", "sheet_profiles.json")
PROFILE_PATH = os.path.join(ROOT, "data", "music", "user_melody_profile.json")

# 数值字段: (名称, 合理差值上限——差值达到上限时相似度为0)
_NUM_FIELDS = [
    ("bpm", 60.0),
    ("intro_sec", 25.0),
    ("line_gap_median", 4.0),
    ("line_len_avg", 10.0),
    ("chars_per_sec", 2.5),
    ("repeat_ratio", 0.6),
]


def build_profile(sheets: dict | None = None, save: bool = True) -> dict:
    """聚合歌单读谱结果 → 用户旋律偏好画像。"""
    if sheets is None:
        sheets = json.load(open(SHEETS_PATH, encoding="utf-8"))

    valid = [v for v in sheets.values() if v]
    total = len(sheets)

    def collect(key):
        return [v[key] for v in valid if v.get(key) is not None]

    prof: dict = {"total_songs": total, "fetched": len(valid)}
    coverage = {}
    for key, _ in _NUM_FIELDS:
        vals = collect(key)
        coverage[key] = round(len(vals) / max(total, 1), 3)
        if vals:
            prof[f"{key}_median"] = round(statistics.median(vals), 2)
            prof[f"{key}_mean"] = round(statistics.mean(vals), 2)
            if len(vals) > 5:
                prof[f"{key}_stdev"] = round(statistics.stdev(vals), 2)

    # genre 分布
    genres = collect("genre")
    from collections import Counter
    gc = Counter(genres)
    prof["genre_dist"] = {k: round(v / max(len(genres), 1), 3)
                          for k, v in gc.most_common(10)}
    coverage["genre"] = round(len(genres) / max(total, 1), 3)

    prof["coverage"] = coverage
    prof["note"] = "全部字段来自QQ音乐公开数据(bpm/歌词时间轴)聚合，无造假；coverage 为字段覆盖率"

    if save:
        os.makedirs(os.path.dirname(PROFILE_PATH), exist_ok=True)
        with open(PROFILE_PATH, "w", encoding="utf-8") as f:
            json.dump(prof, f, ensure_ascii=False, indent=1)
    return prof


def load_profile() -> dict | None:
    if os.path.exists(PROFILE_PATH):
        try:
            return json.load(open(PROFILE_PATH, encoding="utf-8"))
        except Exception:
            return None
    return None


def similarity(profile: dict, sheet: dict | None) -> tuple[float | None, float]:
    """用户旋律画像 vs 一首歌的结构画像。

    返回 (similarity 0..1 或 None, confidence 0..1)。
    None = 该歌完全无数据，调用方应明确展示「无谱子数据」而非给假分。
    """
    if not sheet:
        return None, 0.0

    sims, weights = [], []
    for key, span in _NUM_FIELDS:
        ref = profile.get(f"{key}_median")
        val = sheet.get(key)
        if ref is None or val is None:
            continue
        diff = abs(float(val) - float(ref))
        sims.append(max(0.0, 1.0 - diff / span))
        weights.append(1.0)

    # genre 匹配（占一个字段权重）
    gd = profile.get("genre_dist") or {}
    g = sheet.get("genre")
    if gd and g:
        sims.append(min(1.0, gd.get(g, 0.0) * 2.5))  # 出现率40%以上视为满分
        weights.append(1.0)

    if not sims:
        return None, 0.0
    conf = min(1.0, len(sims) / (len(_NUM_FIELDS) + 1))
    score = sum(s * w for s, w in zip(sims, weights)) / sum(weights)
    return round(score, 3), round(conf, 3)


if __name__ == "__main__":
    prof = build_profile()
    print(json.dumps(prof, ensure_ascii=False, indent=1))

