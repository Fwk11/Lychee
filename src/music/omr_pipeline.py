#!/usr/bin/env python3
"""OMR 曲谱识别流水线 —— 生成 user_melody_profile_v4.json。

流程：
  1. 从 enriched_songs.json 读取用户歌单
  2. 逐首调用 fetch_qq_sheets() 抓取 QQ 音乐曲谱图片 URL
  3. 下载曲谱图片 → 送 qwen2.5vl:3b 做 OMR 识别
  4. 聚合结果（调式/音区/旋律走向/节奏/拍号）→ 写入 user_melody_profile_v4.json

用法：
  python -m src.music.omr_pipeline              # 完整跑
  python -m src.music.omr_pipeline --limit 5    # 只跑前 5 首（测试）
  python -m src.music.omr_pipeline --resume     # 断点续跑
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import urllib.request
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from src.music.sheet_fetcher import fetch_qq_sheets
from src.music.user_ctx import user_path

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/chat")
OLLAMA_MODEL = os.environ.get("VLM_MODEL", "qwen2.5vl:3b")

OMR_PROMPT = (
    "你是一位专业音乐分析师。请分析这张曲谱图片，返回以下 JSON（只返回 JSON，不要其他文字）：\n"
    "{\n"
    '  "key_mode": "大调" 或 "小调" 或 "未知",\n'
    '  "register": "高" 或 "中" 或 "低" 或 "未知",\n'
    '  "contour": "级进" 或 "跳进" 或 "平稳" 或 "未知",\n'
    '  "rhythm": "密集" 或 "适中" 或 "稀疏" 或 "未知",\n'
    '  "meter": "4/4" 或 "3/4" 或 "6/8" 或 "2/4" 或 "未知"\n'
    "}"
)


def _ollama_chat(image_b64: str, prompt: str = OMR_PROMPT,
                 model: str = OLLAMA_MODEL, timeout: int = 180) -> str:
    """调用 Ollama 多模态模型做单张图片识别。

    用 requests 而非 urllib：urllib 的 urlopen(timeout=) 在"连接已建立但
    服务端迟迟不返回数据"的读取阶段不生效，Ollama 一旦挂死会永久卡住整个
    进程。requests 的 timeout 对读取阶段可靠生效，超时即抛异常由上层跳过。
    """
    import requests
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt, "images": [image_b64]}],
        "stream": False,
        "options": {"num_predict": 200, "temperature": 0.1},
    }
    r = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
    r.raise_for_status()
    resp = r.json()
    return resp["message"]["content"].strip()


def _download_image_b64(url: str, timeout: int = 15) -> str | None:
    """下载图片并返回 base64。"""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36",
            "Referer": "https://y.qq.com/",
        })
        data = urllib.request.urlopen(req, timeout=timeout).read()
        return base64.b64encode(data).decode()
    except Exception:
        return None


def _parse_omr_response(text: str) -> dict | None:
    """从 VLM 返回的文本中提取 JSON。"""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r'\{[^{}]*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return None


def _recognize_one(songmid: str, title: str, artist: str,
                   cookie: str | None = None) -> dict | None:
    """识别一首歌的曲谱（仅 QQ 音乐自带谱）。

    返回识别结果 dict 或 None（None = QQ 无谱）。
    """
    if not songmid:
        return None  # 无 songmid 无法查 QQ 谱，直接视为无谱
    sheets = fetch_qq_sheets(songmid, cookie=cookie)
    if not sheets or sheets[0].get("_available"):
        return None
    for sheet in sheets:
        urls = sheet.get("picURLs") or []
        if not urls:
            continue
        img_b64 = _download_image_b64(urls[0])
        if not img_b64:
            continue
        try:
            raw = _ollama_chat(img_b64)
            parsed = _parse_omr_response(raw)
            if parsed:
                parsed["_source"] = sheet.get("scoreName", "")
                parsed["_tonality_meta"] = sheet.get("tonality", "")
                return parsed
        except Exception as e:
            print(f"  VLM 识别失败 {title}: {e}", flush=True)
            continue
    return None


def _cache_path(user_id: str | None = None) -> str:
    """OMR 缓存按用户隔离，避免多用户结果互相串味 / 污染聚合。"""
    return user_path(user_id, "omr_cache.json")


def _load_cache(user_id: str | None = None) -> dict:
    p = _cache_path(user_id)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_cache(data: dict, user_id: str | None = None):
    p = _cache_path(user_id)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _save_profile(profile: dict, user_id: str | None = None) -> str:
    """把聚合结果写入 user_melody_profile_v4.json，返回输出路径。"""
    out_path = user_path(user_id, "user_melody_profile_v4.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)
    return out_path


def _aggregate(results: dict) -> dict:
    """聚合所有识别结果 → user_melody_profile_v4 格式。"""
    recognized = 0
    key_modes = []
    registers = []
    contours = []
    rhythms = []
    meters = []

    for mid, r in results.items():
        if not r or r.get("_available"):
            continue
        km = r.get("key_mode", "未知")
        reg = r.get("register", "未知")
        con = r.get("contour", "未知")
        rhy = r.get("rhythm", "未知")
        met = r.get("meter", "未知")
        if km == "未知" and reg == "未知" and con == "未知" and rhy == "未知" and met == "未知":
            continue
        recognized += 1
        key_modes.append(km)
        registers.append(reg)
        contours.append(con)
        rhythms.append(rhy)
        meters.append(met)

    def _dist(vals: list[str]) -> dict:
        total = len(vals) or 1
        c = Counter(vals)
        c.pop("未知", None)
        return {k: round(v / total, 3) for k, v in c.most_common()}

    return {
        "recognized": recognized,
        "total_songs": len(results),
        "key_mode_dist": _dist(key_modes),
        "register_dist": _dist(registers),
        "contour_dist": _dist(contours),
        "rhythm_dist": _dist(rhythms),
        "meter_dist": _dist(meters),
        "note": "基于 qwen2.5vl:3b OMR 识别 QQ 音乐曲谱图片，未识别的记为未知",
    }


def _default_cookie() -> str | None:
    """读取 data/music/.qq_cookie（若存在），用于解锁 QQ 登录态谱子。"""
    p = os.path.join(ROOT, "data", "music", ".qq_cookie")
    if os.path.exists(p):
        try:
            return open(p, encoding="utf-8").read().strip() or None
        except Exception:
            return None
    return None


def run(limit: int = 0, resume: bool = True, sleep: float = 0.5,
        cookie: str | None = None, user_id: str | None = None,
        retry_missing: bool = False,
        on_progress: Callable[[int, int, str], None] | None = None) -> dict:
    """运行 OMR 流水线。

    Args:
        limit: 最多识别几首（0=全部）
        resume: 是否从缓存续跑
        sleep: 每首间隔秒数
        cookie: QQ 音乐登录 cookie（新歌曲谱需登录）
        user_id: 用户 ID（多用户隔离）
        retry_missing: 对「无谱 / 仅标记无图」的歌重抓 QQ 谱（如 cookie 更新后解锁）
    """
    cookie = cookie or _default_cookie()
    enriched_path = user_path(user_id, "enriched_songs.json")
    playlist_path = user_path(user_id, "playlist_songs.json")
    if os.path.exists(enriched_path):
        with open(enriched_path, encoding="utf-8") as f:
            songs = json.load(f)
    elif os.path.exists(playlist_path):
        # 访客通过卡片 onboard 只有 playlist_songs.json（无 songmid）→ QQ 无谱则标记无谱
        with open(playlist_path, encoding="utf-8") as f:
            pl = json.load(f)
        raw = pl.get("songs", []) if isinstance(pl, dict) else pl
        songs = []
        for s in raw:
            arts = s.get("artists") or s.get("artist") or []
            if isinstance(arts, list):
                artist = ", ".join(str(a) for a in arts)
            else:
                artist = str(arts)
            songs.append({"title": s.get("title", ""),
                          "artist": artist,
                          "songmid": s.get("songmid") or ""})
        print(f"[omr] 使用 playlist_songs.json（{len(songs)} 首，无 songmid 的歌将判为 QQ 无谱）")
    else:
        print(f"[omr] 歌单不存在: {enriched_path} / {playlist_path}")
        return {}

    cache = _load_cache(user_id) if resume else {}
    todo = []
    for s in songs:
        mid = s.get("songmid") or s.get("mid") or ""
        if not mid:
            # 无 songmid（访客卡片）：用 歌名|歌手 作为稳定缓存键
            mid = "~" + (s.get("title", "")) + "|" + (s.get("artist", ""))
        if mid not in cache:
            todo.append(s)
            continue
        # retry_missing：把上次「无谱 / 仅标记无图」的歌重新尝试抓 QQ 谱
        if retry_missing:
            prev = cache[mid]
            if prev is None or (isinstance(prev, dict) and prev.get("_available")):
                todo.append(s)
    if limit > 0:
        todo = todo[:limit]

    total = len(todo)
    print(f"[omr] 共 {len(songs)} 首歌，已缓存 {len(cache)} 首，待识别 {total} 首")
    if not todo:
        print("[omr] 全部已完成，直接聚合并写盘")
        profile = _aggregate(cache)
        out_path = _save_profile(profile, user_id=user_id)
        recognized = profile.get("recognized", 0)
        print(f"[omr] 已写入 {out_path}（识别 {recognized}/{len(cache)} 首）")
        return profile

    t0 = time.time()
    for i, s in enumerate(todo):
        mid = s.get("songmid") or s.get("mid") or ""
        if not mid:
            mid = "~" + s.get("title", "") + "|" + s.get("artist", "")
        title = s.get("title", "")
        artist = s.get("artist", "") or s.get("singer", "") or ""
        if isinstance(artist, list):
            artist = ", ".join(str(a) for a in artist)
        print(f"  [{i+1}/{total}] {title} - {artist} ...", end=" ", flush=True)
        try:
            result = _recognize_one(mid, title, artist, cookie=cookie)
            if result:
                if result.get("_available"):
                    print("无谱")
                else:
                    km = result.get("key_mode", "?")
                    reg = result.get("register", "?")
                    print(f"{km} {reg}区")
            else:
                print("无谱")
            cache[mid] = result
        except Exception as e:
            print(f"失败: {e}")
            cache[mid] = None
        if (i + 1) % 10 == 0:
            _save_cache(cache, user_id)
        if on_progress:
            try:
                on_progress(i + 1, total, f"{title} - {artist}")
            except Exception:
                pass
        time.sleep(sleep)

    _save_cache(cache, user_id)
    elapsed = time.time() - t0
    profile = _aggregate(cache)
    out_path = _save_profile(profile, user_id=user_id)
    recognized = profile.get("recognized", 0)
    print(f"\n[omr] 完成！耗时 {elapsed:.0f}s，识别 {recognized}/{total} 首")
    print(f"[omr] 已写入 {out_path}")
    return profile


if __name__ == "__main__":
    import argparse
    import traceback
    p = argparse.ArgumentParser(description="OMR 曲谱识别")
    p.add_argument("--limit", type=int, default=0, help="最多识别几首（0=全部）")
    p.add_argument("--no-resume", action="store_true", help="不从缓存续跑")
    p.add_argument("--retry-missing", action="store_true",
                   help="对上次「无谱/仅标记无图」的歌重跑（含联网搜谱兜底）")
    p.add_argument("--sleep", type=float, default=0.5, help="每首间隔秒数")
    p.add_argument("--cookie", type=str, default=None, help="QQ 音乐 Cookie")
    p.add_argument("--user", type=str, default=None, help="用户 ID")
    args = p.parse_args()
    try:
        run(limit=args.limit, resume=not args.no_resume, sleep=args.sleep,
            cookie=args.cookie, user_id=args.user, retry_missing=args.retry_missing)
    except Exception:
        # 兜底打印堆栈，避免静默退出难以排查
        traceback.print_exc()
        raise