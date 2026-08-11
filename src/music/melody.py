#!/usr/bin/env python3
"""旋律分析引擎 —— 把「歌」变成可计算的特征向量。

两种输入：
  1. 真实谱子：from_sheet("1 2 3 5 6 ...")  解析简谱/音高序列 → MelodyProfile
  2. 代理特征：proxy_from_style("R&B")      按曲风给典型音型（无谱子时使用）

所有旋律画像都能用 similarity() 互相比对，得到 0~1 的「旋律相似度」。
这样推荐时就能把「旋律像你爱听的歌」作为一维打分，而不是瞎猜。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, asdict
from typing import Optional


# --------------------------------------------------------------------------
# 受控曲风词表（与 enrichment / recommender 共用，保证标签一致）
# --------------------------------------------------------------------------
STYLE_VOCAB = [
    "华语流行", "R&B", "嘻哈", "说唱", "情歌", "民谣", "摇滚", "电子",
    "舞曲", "国风", "古风", "爵士", "轻音乐", "英伦", "蓝调", "金属",
    "乡村", "拉丁",
]

# --------------------------------------------------------------------------
# 曲风 → 典型旋律画像（代理特征）。
# 字段含义（全部归一到 0~1 或 -1~1，便于相似度计算）：
#   pitch_range     音域宽度  0窄 ~ 1宽
#   mean_pitch      平均音高  0低 ~ 1高
#   contour         旋律走向 -1下行 ~ +1上行（0=平稳）
#   mode            调式      -1小调(暗) 0五声  +1大调(亮)
#   rhythmic        节奏密度  0稀疏 ~ 1密集
#   tempo           速度代理  60~160 BPM
#   interval        音程变化  0级进 ~ 1大跳
# --------------------------------------------------------------------------
STYLE_MELODY_SEED = {
    "华语流行": dict(pitch_range=0.5, mean_pitch=0.55, contour=0.1, mode=0.3, rhythmic=0.45, tempo=110, interval=0.4),
    "R&B":      dict(pitch_range=0.45, mean_pitch=0.5, contour=-0.1, mode=0.0, rhythmic=0.55, tempo=92, interval=0.35),
    "嘻哈":      dict(pitch_range=0.25, mean_pitch=0.45, contour=0.0, mode=-0.2, rhythmic=0.85, tempo=96, interval=0.2),
    "说唱":      dict(pitch_range=0.2, mean_pitch=0.45, contour=0.0, mode=-0.2, rhythmic=0.9, tempo=100, interval=0.15),
    "情歌":      dict(pitch_range=0.55, mean_pitch=0.6, contour=0.2, mode=-0.5, rhythmic=0.3, tempo=72, interval=0.5),
    "民谣":      dict(pitch_range=0.4, mean_pitch=0.5, contour=0.15, mode=0.0, rhythmic=0.25, tempo=82, interval=0.45),
    "摇滚":      dict(pitch_range=0.7, mean_pitch=0.6, contour=0.3, mode=0.4, rhythmic=0.7, tempo=140, interval=0.6),
    "电子":      dict(pitch_range=0.5, mean_pitch=0.55, contour=0.0, mode=0.5, rhythmic=0.8, tempo=128, interval=0.3),
    "舞曲":      dict(pitch_range=0.5, mean_pitch=0.6, contour=0.1, mode=0.6, rhythmic=0.9, tempo=124, interval=0.35),
    "国风":      dict(pitch_range=0.6, mean_pitch=0.55, contour=0.2, mode=0.0, rhythmic=0.35, tempo=88, interval=0.55),
    "古风":      dict(pitch_range=0.6, mean_pitch=0.55, contour=0.2, mode=0.0, rhythmic=0.35, tempo=84, interval=0.55),
    "爵士":      dict(pitch_range=0.55, mean_pitch=0.5, contour=0.0, mode=0.2, rhythmic=0.6, tempo=104, interval=0.7),
    "轻音乐":    dict(pitch_range=0.4, mean_pitch=0.5, contour=0.05, mode=0.4, rhythmic=0.2, tempo=76, interval=0.4),
    "英伦":      dict(pitch_range=0.55, mean_pitch=0.52, contour=0.15, mode=0.0, rhythmic=0.5, tempo=118, interval=0.5),
    "蓝调":      dict(pitch_range=0.5, mean_pitch=0.45, contour=-0.1, mode=-0.6, rhythmic=0.55, tempo=96, interval=0.6),
    "金属":      dict(pitch_range=0.75, mean_pitch=0.65, contour=0.3, mode=0.3, rhythmic=0.75, tempo=150, interval=0.65),
    "乡村":      dict(pitch_range=0.45, mean_pitch=0.5, contour=0.1, mode=0.4, rhythmic=0.45, tempo=112, interval=0.45),
    "拉丁":      dict(pitch_range=0.5, mean_pitch=0.55, contour=0.2, mode=0.5, rhythmic=0.85, tempo=120, interval=0.4),
}


# --------------------------------------------------------------------------
# 旋律画像
# --------------------------------------------------------------------------
@dataclass
class MelodyProfile:
    pitch_range: float = 0.5
    mean_pitch: float = 0.5
    contour: float = 0.0
    mode: float = 0.0
    rhythmic: float = 0.4
    tempo: float = 100.0
    interval: float = 0.4
    source: str = "proxy"          # proxy=代理特征 / sheet=真实谱子 / none=无
    sheet_ref: Optional[str] = None  # 若来自真实谱子，记录 songmid

    def to_vec(self) -> list[float]:
        """相似度计算用的数值向量（tempo 单独归一）。"""
        return [
            self.pitch_range, self.mean_pitch, self.contour, self.mode,
            self.rhythmic, self._norm_tempo(), self.interval,
        ]

    def _norm_tempo(self) -> float:
        return max(0.0, min(1.0, (self.tempo - 60.0) / 100.0))

    def as_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# 解析真实谱子 → MelodyProfile
# --------------------------------------------------------------------------
# 简谱音名 → 相对音级（以 C 大调 do=0）
_DEGREE = {"1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6}
# 调式 → mode 数值映射
_MODE_MAP = {"大调": 1.0, "小调": -1.0, "五声": 0.0, "宫调": 0.0, "羽调": -0.5,
             "major": 1.0, "minor": -1.0, "pentatonic": 0.0}


def from_sheet(text: str) -> MelodyProfile:
    """解析简谱 / 音高序列文本。

    支持写法：
      - 数字简谱: "3 5 6 1 2 3"  (1=do ... 7=si)
      - 带八度点: "1·" 高八度 / "1," 低八度 (用 · 或 ' 或 , 表示)
      - 减时线/增时线: "-" 表示时值延长（用于节奏密度估计）
      - 0 表示休止
      - 可包含调式标注: 行内出现 "大调/小调/五声" 会用于 mode
    返回 MelodyProfile(source="sheet")。
    """
    text = (text or "").strip()
    if not text:
        return MelodyProfile(source="none")

    # 调式识别
    mode_val = 0.0
    for kw, mv in _MODE_MAP.items():
        if kw in text:
            mode_val = mv
            break

    # 提取音级序列（忽略换行/空格/标点，保留数字、八度记号、0、减时线）
    degrees: list[int] = []
    tokens = re.findall(r"[0-7·'`,\-]", text)
    cur_oct = 0
    for t in tokens:
        if t in _DEGREE:
            deg = _DEGREE[t] + 12 * cur_oct
            degrees.append(deg)
            cur_oct = 0  # 八度记号只作用到下一个音
        elif t in ("·", "'"):
            cur_oct = 1
        elif t in (",", "`"):
            cur_oct = -1
    # 若没有显式八度记号，给一个基准八度让音高落在中音区
    if degrees:
        degrees = [d + 12 * 4 for d in degrees]  # 移到第4八度附近

    if not degrees:
        return MelodyProfile(mode=mode_val, source="sheet")

    # 指标计算
    lo, hi = min(degrees), max(degrees)
    pitch_range = max(0.0, min(1.0, (hi - lo) / 24.0))      # 两个八度封顶
    mean_pitch = max(0.0, min(1.0, (sum(degrees) / len(degrees) - 36) / 24.0))
    # 走向：首尾音差
    delta = degrees[-1] - degrees[0]
    contour = max(-1.0, min(1.0, delta / 12.0))
    # 音程变化：相邻音级差的绝对值均值
    steps = [abs(degrees[i] - degrees[i - 1]) for i in range(1, len(degrees))]
    interval = max(0.0, min(1.0, (sum(steps) / len(steps)) / 5.0)) if steps else 0.3
    # 节奏密度：减时线占比（"-"）
    dash = text.count("-")
    total = len(tokens) if tokens else 1
    rhythmic = max(0.0, min(1.0, dash / total))
    # tempo 代理：无法从纯谱子精确得到，用节奏密度外推
    tempo = 70.0 + rhythmic * 80.0

    return MelodyProfile(
        pitch_range=pitch_range, mean_pitch=mean_pitch, contour=contour,
        mode=mode_val, rhythmic=rhythmic, tempo=tempo, interval=interval,
        source="sheet",
    )


def proxy_from_style(style: str) -> MelodyProfile:
    """按曲风返回典型旋律画像（无真实谱子时的代理特征）。"""
    seed = STYLE_MELODY_SEED.get(style)
    if seed is None:
        # 未知曲风 → 用通用流行画像
        seed = STYLE_MELODY_SEED["华语流行"]
    return MelodyProfile(**seed, source="proxy")


def proxy_from_styles(styles: list[str]) -> MelodyProfile:
    """多曲风取平均。"""
    if not styles:
        return MelodyProfile(source="proxy")
    vecs = [proxy_from_style(s) for s in styles]
    n = len(vecs)
    return MelodyProfile(
        pitch_range=sum(v.pitch_range for v in vecs) / n,
        mean_pitch=sum(v.mean_pitch for v in vecs) / n,
        contour=sum(v.contour for v in vecs) / n,
        mode=sum(v.mode for v in vecs) / n,
        rhythmic=sum(v.rhythmic for v in vecs) / n,
        tempo=sum(v.tempo for v in vecs) / n,
        interval=sum(v.interval for v in vecs) / n,
        source="proxy",
    )


# --------------------------------------------------------------------------
# 相似度
# --------------------------------------------------------------------------
def similarity(a: MelodyProfile, b: MelodyProfile) -> float:
    """返回 0~1 旋律相似度（1=完全一样）。"""
    va, vb = a.to_vec(), b.to_vec()
    diffs = [abs(x - y) for x, y in zip(va, vb)]
    mean_diff = sum(diffs) / len(diffs)
    sim = max(0.0, 1.0 - mean_diff)
    return round(sim, 4)


# --------------------------------------------------------------------------
# 真实谱子存取（落盘到 data/music/sheets.json）
# --------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_SHEETS_PATH = os.path.join(os.path.dirname(_HERE), "..", "data", "music", "sheets.json")


def import_sheet(songmid: str, sheet_text: str, title: str = "", artist: str = "") -> dict:
    """导入一首歌的真实谱子文本，解析并保存。返回该歌的 MelodyProfile(dict)。"""
    prof = from_sheet(sheet_text)
    os.makedirs(os.path.dirname(_SHEETS_PATH), exist_ok=True)
    data: dict = {}
    if os.path.exists(_SHEETS_PATH):
        with open(_SHEETS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    data[songmid] = {
        "title": title, "artist": artist,
        "sheet": sheet_text, "profile": prof.as_dict(),
    }
    with open(_SHEETS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return prof.as_dict()


def load_sheets() -> dict:
    if not os.path.exists(_SHEETS_PATH):
        return {}
    with open(_SHEETS_PATH, encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------
# 从 sheet_fetcher 产出构造旋律画像（无简谱但有 BPM/曲风/歌词时间轴）
# --------------------------------------------------------------------------
def from_fetcher_data(prof: dict) -> MelodyProfile:
    """从 sheet_fetcher 抓取的公开数据构造 MelodyProfile。

    prof 是 fetch_sheet_for_song / fetch_lyric_profile 的返回值。
    没有真实简谱音高数据，但可用 BPM、曲风、歌词节奏密度做真实画像。
    source 标记为 "qqmusic"（区别于 "sheet" 真实简谱和 "proxy" 代理特征）。
    """
    # tempo: 优先用 BPM，否则用曲风代理
    bpm = prof.get("bpm")
    if bpm and bpm > 0:
        tempo = float(bpm)
    else:
        genre = prof.get("genre", "")
        seed = STYLE_MELODY_SEED.get(genre, STYLE_MELODY_SEED["华语流行"])
        tempo = seed["tempo"]

    # mode: 按曲风映射
    genre = prof.get("genre", "")
    seed = STYLE_MELODY_SEED.get(genre, {})
    mode_val = seed.get("mode", 0.0)

    # rhythmic: 有歌词时间轴用 chars_per_sec 归一化，否则用曲风代理
    cps = prof.get("chars_per_sec")
    if cps is not None:
        rhythmic = max(0.0, min(1.0, cps / 5.0))
    else:
        rhythmic = seed.get("rhythmic", 0.4)

    # pitch_range / mean_pitch / contour / interval:
    # 无真实简谱拿不到音高，用曲风代理（诚实标注）
    return MelodyProfile(
        pitch_range=seed.get("pitch_range", 0.5),
        mean_pitch=seed.get("mean_pitch", 0.5),
        contour=seed.get("contour", 0.0),
        mode=mode_val,
        rhythmic=rhythmic,
        tempo=tempo,
        interval=seed.get("interval", 0.4),
        source="qqmusic",
    )


# --------------------------------------------------------------------------
# 便捷：把一批旋律画像聚合成「用户平均旋律画像」
# --------------------------------------------------------------------------
def aggregate(profiles: list[MelodyProfile]) -> MelodyProfile:
    profs = [p for p in profiles if p.source != "none"]
    if not profs:
        return MelodyProfile(source="none")
    n = len(profs)
    return MelodyProfile(
        pitch_range=sum(p.pitch_range for p in profs) / n,
        mean_pitch=sum(p.mean_pitch for p in profs) / n,
        contour=sum(p.contour for p in profs) / n,
        mode=sum(p.mode for p in profs) / n,
        rhythmic=sum(p.rhythmic for p in profs) / n,
        tempo=sum(p.tempo for p in profs) / n,
        interval=sum(p.interval for p in profs) / n,
        source="proxy",
    )


if __name__ == "__main__":
    s1 = proxy_from_style("情歌")
    s2 = proxy_from_style("R&B")
    print("情歌画像:", s1.as_dict())
    print("R&B 画像:", s2.as_dict())
    print("情歌 vs R&B 相似度:", similarity(s1, s2))
    sheet = from_sheet("3 5 6 1 2 3 2 - 1 - 6 5 3")
    print("谱子画像:", sheet.as_dict())