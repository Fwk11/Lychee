"""QQ音乐分享卡片二维码识别 → 歌单抓取 → 用户入职。

流程：
  1. 用户上传 QQ 音乐「我喜欢」分享卡片截图
  2. OpenCV QRCodeDetector 解析二维码得到短链
  3. 请求短链，从最终 URL 提取 disstid（歌单 ID）
  4. 调用 QQ 音乐歌单接口抓全量歌曲
  5. 返回结构化歌单条目，交给 new_releases.onboard_user 生成口味画像
"""

import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np
import requests

QQ_PLAYLIST_API = (
    "https://c.y.qq.com/qzone/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg"
    "?type=1&json=1&utf8=1&onlysong=0&disstid={disstid}"
)
QQ_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Referer": "https://y.qq.com/",
}


def decode_qr_from_image(image_path: str | Path) -> str | None:
    """从图片中解析第一个二维码的文本内容。"""
    img = cv2.imread(str(image_path))
    if img is None:
        raise ValueError(f"无法读取图片: {image_path}")
    detector = cv2.QRCodeDetector()
    data, _, _ = detector.detectAndDecode(img)
    return data or None


def extract_playlist_id_from_url(url: str) -> str | None:
    """从 QQ 音乐分享链接中提取歌单 ID（disstid / id）。"""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    # 短链最终落地页: .../playlist.html?...&id=2434197420&...
    for key in ("id", "disstid", "sid"):
        if key in qs and qs[key][0].isdigit():
            return qs[key][0]
    # 兜底：URL 路径里找数字段（/playlist/2434197420 或 /2434197420.html）
    m = re.search(r"/playlist/(\d{5,})(?:/|\?|$)", parsed.path)
    if m:
        return m.group(1)
    m = re.search(r"/(\d{5,})\.html", parsed.path)
    if m:
        return m.group(1)
    # 再兜底：query string 里任意长数字
    m = re.search(r"\b(\d{7,})\b", parsed.query)
    if m:
        return m.group(1)
    return None


def resolve_share_url(url: str, timeout: int = 15) -> str:
    """跟随短链 302 跳转，返回最终落地 URL。"""
    r = requests.get(url, headers=QQ_HEADERS, timeout=timeout,
                     allow_redirects=True)
    r.raise_for_status()
    return r.url


def fetch_playlist_songs(disstid: str, timeout: int = 20) -> list[dict]:
    """通过 QQ 音乐旧版接口抓取歌单全部歌曲。"""
    url = QQ_PLAYLIST_API.format(disstid=disstid)
    r = requests.get(url, headers=QQ_HEADERS, timeout=timeout)
    r.raise_for_status()
    text = r.text
    # 接口返回 JSONP: jsonCallback({...})
    m = re.search(r"jsonCallback\((.*)\)\s*$", text, re.S)
    if not m:
        raise ValueError("歌单接口返回非预期格式")
    data = json.loads(m.group(1))
    cdlist = data.get("cdlist", [])
    if not cdlist:
        code = data.get("code", "unknown")
        raise ValueError(f"无法获取歌单详情 (code={code})")
    raw_songs = cdlist[0].get("songlist", [])
    songs = []
    for s in raw_songs:
        singers = "/".join(si.get("name", "") for si in s.get("singer", []))
        songs.append({
            "title": s.get("songname", ""),
            "artists": singers,
            "songmid": s.get("songmid", ""),
            "album": s.get("albumname", ""),
        })
    return songs


def extract_playlist_from_qr_image(image_path: str | Path) -> dict:
    """一站式：图片 → 二维码 → 歌单。"""
    qr = decode_qr_from_image(image_path)
    if not qr:
        raise ValueError("未能识别图片中的二维码，请上传清晰的 QQ 音乐分享卡片")

    # 兼容非链接内容（极罕见）
    if not qr.startswith("http"):
        raise ValueError(f"二维码内容不是链接，无法解析歌单: {qr[:80]}")

    final_url = resolve_share_url(qr)
    disstid = extract_playlist_id_from_url(final_url)
    if not disstid:
        raise ValueError("无法从分享链接中提取歌单 ID，请确认是 QQ 音乐歌单分享卡片")

    songs = fetch_playlist_songs(disstid)
    if not songs:
        raise ValueError("歌单为空或需要登录权限")

    return {
        "qr_url": qr,
        "resolved_url": final_url,
        "disstid": disstid,
        "songs": songs,
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m src.music.qr_onboard <image_path>")
        raise SystemExit(1)
    out = extract_playlist_from_qr_image(sys.argv[1])
    print(json.dumps({
        "qr_url": out["qr_url"],
        "disstid": out["disstid"],
        "count": len(out["songs"]),
        "sample": out["songs"][:5],
    }, ensure_ascii=False, indent=2))
