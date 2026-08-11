# -*- coding: utf-8 -*-
"""自动读谱引擎（QQ 音乐公开数据版）。

调研结论（2026-07-28 实测）：
  - QQ 音乐没有公开的「简谱/吉他谱」接口（musicsheet 模块全部 500003）；
  - 全民K歌音高线接口也不可匿名访问（404）；
  - 但两个公开接口能拿到 **逐首真实** 的旋律侧数据：
      1) song detail  -> bpm(节拍速度)、genre(官方曲风编码)、时长、语种、发布日期
      2) lyric 时间轴 -> 逐行时间戳，可计算 节奏密度/句长分布/前奏长度/副歌重复度

  由这些真实数据组成每首歌的「歌曲结构画像」(SongSheetProfile)，
  作为"读谱"的落地实现。拿不到的字段明确置 None，绝不造假数据。

用法:
  from src.music.sheet_fetcher import fetch_sheet_for_song, fetch_sheets_for_playlist
  prof = fetch_sheet_for_song("001WrUzP2tEyg2")   # -> dict or None
"""
from __future__ import annotations

import base64
import json
import os
import re
import statistics
import time

import requests

try:
    from .netease_client import fetch_lyric as _netease_lyric_raw
except ImportError:  # pragma: no cover
    from src.music.netease_client import fetch_lyric as _netease_lyric_raw

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_PATH = os.path.join(ROOT, "data", "music", "sheet_profiles.json")

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Referer": "https://y.qq.com/",
}
_MUSICU = "https://u.y.qq.com/cgi-bin/musicu.fcg"
_LYRIC = "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg"

# QQ音乐 genre 编码 → 中文（常见值，实测归纳；未知编码返回 None）
GENRE_MAP = {
    1: "流行", 2: "摇滚", 3: "民谣", 4: "电子", 5: "说唱",
    6: "轻音乐", 7: "爵士", 8: "古典", 9: "乡村", 10: "蓝调",
    15: "R&B", 19: "民歌", 20: "拉丁", 21: "朋克", 22: "金属",
    25: "原声", 31: "古风", 33: "R&B", 36: "国风", 37: "流行",
    39: "摇滚", 41: "说唱",
}

# 元信息行（词曲编曲制作人等），不算入演唱节奏
_META_PAT = re.compile(
    r"(作?词|作?曲|编曲|制作人|混音|母带|录音|统筹|监制|吉他|贝斯|鼓|键盘|和声|发行|出品|企划|营销|OP|SP|请勿使用)")


def _song_detail(songmid: str, timeout: float = 8.0) -> dict | None:
    body = {"comm": {"ct": 24},
            "req": {"module": "music.pf_song_detail_svr",
                    "method": "get_song_detail",
                    "param": {"song_mid": songmid}}}
    try:
        r = requests.post(_MUSICU, json=body, headers={**_HEADERS, "Content-Type": "application/json"},
                          timeout=timeout)
        return r.json().get("req", {}).get("data", {}).get("track_info") or None
    except Exception:
        return None


def _parse_lrc_text(lrc: str) -> list[tuple[float, str]] | None:
    """把 LRC 文本解析为 [(秒, 歌词文本)]，已剔除元信息行。"""
    out = []
    for line in lrc.splitlines():
        m = re.match(r"\[(\d+):(\d+(?:\.\d+)?)\](.*)", line)
        if not m:
            continue
        sec = int(m.group(1)) * 60 + float(m.group(2))
        text = m.group(3).strip()
        if not text or _META_PAT.search(text):
            continue
        out.append((sec, text))
    return out or None


def _lyric_timeline(songmid: str, timeout: float = 8.0) -> list[tuple[float, str]] | None:
    """QQ音乐歌词时间轴：返回 [(秒, 歌词文本), ...]，失败返回 None。"""
    try:
        r = requests.get(_LYRIC, params={"songmid": songmid, "format": "json",
                                         "nobase64": 0, "g_tk": 5381},
                         headers=_HEADERS, timeout=timeout)
        txt = r.text
        j = json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
        b64 = j.get("lyric", "")
        if not b64:
            return None
        lrc = base64.b64decode(b64).decode("utf-8", "ignore")
    except Exception:
        return None
    return _parse_lrc_text(lrc)


def _netease_lyric_timeline(song_id: str | int, sleep: float = 0.0) -> list[tuple[float, str]] | None:
    """网易云歌词时间轴：返回 [(秒, 歌词文本), ...]，失败返回 None。"""
    try:
        lrc = _netease_lyric_raw(str(song_id), sleep=sleep)
    except Exception:
        return None
    return _parse_lrc_text(lrc) if lrc else None


def _analyze_timeline(timeline: list[tuple[float, str]], duration: float | None) -> dict:
    """从歌词时间轴计算节奏/结构特征。"""
    times = [t for t, _ in timeline]
    texts = [x for _, x in timeline]

    gaps = [b - a for a, b in zip(times, times[1:]) if 0 < b - a < 30]
    line_lens = [len(re.sub(r"\s", "", t)) for t in texts]

    # 副歌重复度：完全相同的行 / 总行数
    from collections import Counter
    cnt = Counter(texts)
    repeated = sum(c for c in cnt.values() if c >= 2)
    repeat_ratio = round(repeated / max(len(texts), 1), 3)

    # 演唱密度：字/秒（演唱段总字数 / 演唱跨度）
    span = (times[-1] - times[0]) if len(times) > 1 else None
    chars_per_sec = round(sum(line_lens) / span, 2) if span and span > 10 else None

    return {
        "intro_sec": round(times[0], 1),                              # 前奏长度
        "lines": len(texts),                                          # 演唱行数
        "line_gap_median": round(statistics.median(gaps), 2) if gaps else None,   # 句间隔中位
        "line_len_avg": round(statistics.mean(line_lens), 1) if line_lens else None,  # 平均句长(字)
        "chars_per_sec": chars_per_sec,                               # 演唱密度
        "repeat_ratio": repeat_ratio,                                 # 副歌重复度
    }


def fetch_sheet_for_song(songmid: str, sleep: float = 0.0) -> dict | None:
    """抓取并解析一首歌的结构画像。全部字段真实来源，拿不到的置 None。"""
    if not songmid:
        return None
    ti = _song_detail(songmid)
    if sleep:
        time.sleep(sleep)
    timeline = _lyric_timeline(songmid)
    if ti is None and timeline is None:
        return None

    duration = (ti or {}).get("interval") or None
    bpm = (ti or {}).get("bpm") or None            # 0 视为无数据
    genre_id = (ti or {}).get("genre")
    prof = {
        "songmid": songmid,
        "bpm": bpm if bpm else None,
        "genre_id": genre_id,
        "genre": GENRE_MAP.get(genre_id),
        "duration_sec": duration,
        "language": (ti or {}).get("language"),
        "time_public": (ti or {}).get("time_public") or None,
        "source": "qqmusic_public",
    }
    if timeline:
        prof.update(_analyze_timeline(timeline, duration))
        prof["has_timeline"] = True
    else:
        prof["has_timeline"] = False
    return prof


def fetch_lyric_profile(songmid: str, genre_id=None, duration=None,
                         sleep: float = 0.0) -> dict | None:
    """只读歌词时间轴 + 已知 genre/duration，构造结构画像。

    与 fetch_sheet_for_song 区别：不请求 song detail（新歌接口常常 bpm=0、
    且 genre/duration 在新歌榜抓取时已拿到），只取歌词时间轴算节奏/结构，
    节省一次请求，更不易被限流。bpm 明确置 None（诚实标注平台未提供）。
    """
    if not songmid:
        return None
    timeline = _lyric_timeline(songmid)
    if sleep:
        time.sleep(sleep)
    if not timeline:
        return None
    prof = {
        "songmid": songmid,
        "bpm": None,                       # 新歌榜接口不提供 bpm
        "genre_id": genre_id,
        "genre": GENRE_MAP.get(genre_id),
        "duration_sec": duration,
        "source": "qqmusic_lyric",
    }
    prof.update(_analyze_timeline(timeline, duration))
    prof["has_timeline"] = True
    return prof


def fetch_netease_lyric_profile(song_id: str | int, duration: float | None = None,
                                sleep: float = 0.2) -> dict | None:
    """网易云：只读歌词时间轴构造结构画像（无 bpm/genre，诚实标注）。"""
    if not song_id:
        return None
    timeline = _netease_lyric_timeline(song_id, sleep=sleep)
    if not timeline:
        return None
    prof = {
        "songmid": str(song_id),
        "bpm": None,
        "genre_id": None,
        "genre": None,
        "duration_sec": duration,
        "source": "netease_lyric",
    }
    prof.update(_analyze_timeline(timeline, duration))
    prof["has_timeline"] = True
    return prof


def _hash33(s: str) -> int:
    """QQ音乐 g_tk 算法（musickey → 数字签名）。"""
    h = 5381
    for c in s:
        h += (h << 5) + ord(c)
        h &= 0xffffffff
    return h


def _parse_qq_cookie(cookie: str) -> tuple[str, str, int]:
    """从 Cookie 字符串提取 (uin, musickey, g_tk)。拿不到则返回空。"""
    parts = [p.strip() for p in cookie.split(";")]
    kv = {}
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            kv[k.strip()] = v.strip()
    uin = kv.get("uin", "")
    musickey = kv.get("musickey") or kv.get("qm_keyst") or kv.get("wxuin") or ""
    g_tk = _hash33(musickey) if musickey else 5381
    return uin, musickey, g_tk


def fetch_qq_sheets(songmid: str, timeout: float = 8.0, max_sheets: int = 6,
                    cookie: str | None = None) -> list[dict] | None:
    """获取 QQ音乐曲谱列表（用户上传的图片谱 + 元信息）。

    实测（2026-07-29）：曲谱走 `music.mir.SheetMusicSvr` 模块。
      - 老歌：匿名（uin 空、g_tk=5381）即可拿到图片谱 URL；
      - 新歌：谱子被登录态拦截，需传 cookie（uin + musickey）才能拉到。
    返回的是**图片谱 URL + 元信息**，不含音符级数据。

    返回字段（取有用子集）：
      scoreMID / scoreName / picURLs / insType / strInsType（乐器：钢琴/吉他…）
      tonality / scoreType / strScoreType（五线谱/简谱） / difficulty / viewFrequency / url
    无谱返回 None。
    """
    if not songmid:
        return None
    # 构造 comm：有 cookie 用登录态，否则匿名
    if cookie:
        uin, musickey, g_tk = _parse_qq_cookie(cookie)
        comm = {"ct": 24, "cv": 0, "g_tk": g_tk, "uin": uin, "musickey": musickey,
                "format": "json", "inCharset": "utf-8", "outCharset": "utf-8",
                "notice": 0, "needNewCode": 1}
        req_headers = {**_HEADERS, "Content-Type": "application/json", "Cookie": cookie}
    else:
        comm = {"ct": 24, "cv": 0, "g_tk": 5381, "uin": "", "format": "json",
                "inCharset": "utf-8", "outCharset": "utf-8", "notice": 0, "needNewCode": 1}
        req_headers = {**_HEADERS, "Content-Type": "application/json"}

    def _api(method, param):
        body = {"comm": comm, "req": {"module": "music.mir.SheetMusicSvr",
                                      "method": method, "param": param}}
        try:
            r = requests.post(_MUSICU, json=body, headers=req_headers, timeout=timeout)
            return r.json()
        except Exception:
            return None

    h = _api("HasSheetMusic", {"songMid": songmid})
    hd = (h or {}).get("req", {}).get("data", {}) or {}
    flags = [k for k in ("hasGuitar", "hasMore", "hasLDY", "hasQRCX", "hasChongChong") if hd.get(k)]
    # 注意：不依赖 flags 硬跳过。QQ 的 AI 谱等新类型可能不在上述老 flag 中，
    # 且 HasSheetMusic 接口偶发失败会让本应有谱的歌误判为无谱。
    # 直接查 GetMoreSheetMusic（含 ttype=0 用户上传 + ttype=1 引擎/AI 曲谱）兜底。

    out = {}
    for ttype, st in [(0, -1), (1, -473)]:   # 0=用户上传, 1=引擎/AI曲谱
        g = _api("GetMoreSheetMusic",
                 {"songMid": songmid, "begin": 0, "end": 100, "scoreType": st, "ttype": ttype})
        res = (g or {}).get("req", {}).get("data", {}) or {}
        for it in (res.get("result") or []):
            mid = it.get("scoreMID")
            if mid and mid not in out:
                out[mid] = {
                    "scoreMID": mid,
                    "scoreName": it.get("scoreName"),
                    "picURLs": it.get("picURLs") or [],
                    "insType": it.get("insType"),
                    "strInsType": it.get("strInsType"),
                    "tonality": it.get("tonality"),
                    "scoreType": it.get("scoreType"),
                    "strScoreType": it.get("strScoreType"),
                    "difficulty": it.get("difficulty"),
                    "viewFrequency": it.get("viewFrequency"),
                    "url": it.get("url"),
                }
    sheets = list(out.values())[:max_sheets]
    if sheets:
        return sheets
    # 有谱标记但拉不到图片（常见于新歌需登录）：返回标记，让上层显示「去原站看」
    if flags:
        return [{"_available": True, "flags": flags}]
    return None


def fetch_sheets_for_playlist(songs: list[dict], cache: bool = True,
                              sleep: float = 0.25, progress: bool = True,
                              save_melody: bool = True) -> dict:
    """批量读谱。songs: [{songmid,title,...}]。带磁盘缓存，可断点续跑。

    save_melody=True 时会把抓到的 BPM/曲风/歌词节奏写入 sheets.json，
    供 recommender_v2 四维加权中的「旋律相似」维使用（真实数据优先于 proxy）。

    返回 {songmid: profile or None}
    """
    cached: dict = {}
    if cache and os.path.exists(CACHE_PATH):
        try:
            cached = json.load(open(CACHE_PATH, encoding="utf-8"))
        except Exception:
            cached = {}

    out = dict(cached)
    todo = [s for s in songs if (s.get("songmid") or s.get("mid")) not in cached]
    total = len(todo)
    for i, s in enumerate(todo):
        mid = s.get("songmid") or s.get("mid")
        prof = fetch_sheet_for_song(mid, sleep=sleep)
        out[mid] = prof
        if progress and (i + 1) % 25 == 0:
            ok = sum(1 for v in out.values() if v)
            print(f"  [{i+1}/{total}] 已读谱 {ok} 首成功", flush=True)
        if cache and (i + 1) % 50 == 0:
            _save_cache(out)
    if cache:
        _save_cache(out)

    # 桥接：把抓到的 BPM/曲风/歌词节奏写入 sheets.json，供推荐引擎旋律维使用
    if save_melody:
        _save_melody_profiles(out, songs)

    return out


def _save_cache(data: dict):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _save_melody_profiles(profiles: dict, songs: list[dict]):
    """把 fetch_sheet_for_song 的产出写入 sheets.json（旋律画像）。"""
    from src.music.melody import from_fetcher_data, import_sheet

    for s in songs:
        mid = s.get("songmid") or s.get("mid")
        prof = profiles.get(mid)
        if not prof or prof.get("_available"):
            continue
        # 歌曲结构画像 → MelodyProfile
        mp = from_fetcher_data(prof)
        title = s.get("title", "")
        artist = s.get("artist", "") or s.get("singer", "") or ""
        if isinstance(artist, list):
            artist = ", ".join(artist)
        import_sheet(mid, "", title=title, artist=artist)
        # 用真实数据覆盖 import_sheet 写入的 profile（import_sheet 需要 sheet_text 参数，
        # 我们传空字符串绕过解析，然后手动覆盖 profile 为 from_fetcher_data 的结果）
        _overwrite_sheet_profile(mid, mp.as_dict())


def _overwrite_sheet_profile(songmid: str, profile: dict):
    """覆盖 sheets.json 中某首歌的 profile 字段。"""
    from src.music.melody import _SHEETS_PATH
    os.makedirs(os.path.dirname(_SHEETS_PATH), exist_ok=True)
    data: dict = {}
    if os.path.exists(_SHEETS_PATH):
        with open(_SHEETS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    if songmid in data:
        data[songmid]["profile"] = profile
        with open(_SHEETS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    # 自测：3 首真实歌
    for mid in ["001WrUzP2tEyg2", "00090SHn45Vh3D", "001g5upQ19as74"]:
        p = fetch_sheet_for_song(mid, sleep=0.3)
        print(json.dumps(p, ensure_ascii=False))


def backfill_sheets(enriched_songs: list[dict] | None = None,
                    user_id: str | None = None):
    """一次性初始化：从 enriched_songs.json 批量抓取谱子数据写入 sheets.json。

    如果 enriched_songs 为 None，自动从 data/music/enriched_songs.json 读取。
    已经存在于 sheets.json 中的歌会跳过。
    """
    if enriched_songs is None:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "data", "music", "enriched_songs.json")
        if not os.path.exists(path):
            print("[backfill] enriched_songs.json 不存在，跳过")
            return
        with open(path, encoding="utf-8") as f:
            enriched_songs = json.load(f)

    from src.music.melody import load_sheets
    existing = load_sheets()
    songs = []
    for s in enriched_songs:
        mid = s.get("songmid") or s.get("mid")
        if mid and mid not in existing:
            songs.append({"songmid": mid, "title": s.get("title", ""),
                          "artist": s.get("singer", "") or s.get("artist", "")})

    if not songs:
        print("[backfill] 所有歌曲已有谱子画像，无需抓取")
        return

    print(f"[backfill] 需抓取 {len(songs)} 首歌曲的谱子数据...")
    fetch_sheets_for_playlist(songs, cache=True, sleep=0.3, save_melody=True)