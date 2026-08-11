"""网易云音乐 WEAPI 客户端（匿名公开接口）

仅用于获取「新歌速递」等公开数据，不登录、不破解版权。
加密算法参考：https://github.com/darknessomi/musicbox
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import time
from typing import Any

import requests

# 优先用 pycryptodome 的 Cryptodome 命名空间
try:
    from Cryptodome.Cipher import AES
except ImportError:  # pragma: no cover
    from Crypto.Cipher import AES

BASE_URL = "https://music.163.com"
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://music.163.com/",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Content-Type": "application/x-www-form-urlencoded",
}

MODULUS = (
    "00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7"
    "b725152b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280"
    "104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932"
    "575cce10b424d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b"
    "3ece0462db0a22b8e7"
)
PUBKEY = "010001"
NONCE = b"0CoJUm6Qyw8W8jud"
IV = b"0102030405060708"

_session = requests.Session()
_session.headers.update(DEFAULT_HEADERS)


def _create_key(size: int = 16) -> bytes:
    return binascii.hexlify(os.urandom(size))[:16]


def _aes(text: bytes, key: bytes) -> bytes:
    pad = 16 - len(text) % 16
    text = text + bytes([pad]) * pad
    cipher = AES.new(key, AES.MODE_CBC, IV)
    return base64.b64encode(cipher.encrypt(text))


def _rsa(text: bytes) -> str:
    text = text[::-1]
    rs = pow(int(binascii.hexlify(text), 16), int(PUBKEY, 16), int(MODULUS, 16))
    return format(rs, "x").zfill(256)


def _encrypted_request(text: Any) -> dict:
    data = json.dumps(text, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    secret = _create_key(16)
    params = _aes(_aes(data, NONCE), secret).decode()
    encseckey = _rsa(secret)
    return {"params": params, "encSecKey": encseckey}


def _post(path: str, payload: dict, retries: int = 2, sleep: float = 0.5) -> dict:
    url = f"{BASE_URL}{path}"
    payload.setdefault("csrf_token", "")
    data = _encrypted_request(payload)
    for attempt in range(retries + 1):
        try:
            r = _session.post(url, data=data, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == retries:
                return {"code": -1, "error": str(e)}
            time.sleep(sleep * (attempt + 1))
    return {"code": -1}


def _norm(text: str) -> str:
    """用于跨平台去重的归一化：小写、去空格、去常见后缀。"""
    if not text:
        return ""
    t = text.lower()
    for suffix in ("(live)", "[live]", "(explicit)", "[explicit]", "(acoustic)", "[acoustic]"):
        t = t.replace(suffix, "")
    return "".join(c for c in t if c.isalnum())


def _parse_item(item: dict) -> dict | None:
    """把网易云歌曲条目转为本项目通用候选结构。"""
    if not item or not item.get("id") or not item.get("name"):
        return None
    artists = [a.get("name") for a in item.get("artists", []) if a.get("name")]
    album = (item.get("album") or {}).get("name") or ""
    duration_ms = item.get("duration") or item.get("dt") or 0
    # publishTime 可能是时间戳(ms)或字符串
    pub = item.get("publishTime")
    release_date = ""
    if isinstance(pub, (int, float)) and pub > 1000000000000:
        release_date = time.strftime("%Y-%m-%d", time.localtime(pub / 1000))
    elif isinstance(pub, str):
        release_date = pub[:10]
    # 语言：网易云没有直接 language 字段，按歌名简单启发式兜底
    title = item.get("name", "")
    language = _guess_language(title)
    return {
        "songmid": str(item["id"]),  # 复用 songmid 字段，实际为网易云 id
        "title": title,
        "artists": "/".join(artists) if isinstance(artists, list) else str(artists),
        "artist_list": artists,
        "album": album,
        "duration": round(duration_ms / 1000) if duration_ms else 0,
        "interval": round(duration_ms / 1000) if duration_ms else 0,
        "release_date": release_date,
        "language": language,
        "platform": "netease",
        "_key": _norm(title) + "-" + _norm(" ".join(artists)),
    }


def _guess_language(title: str) -> str:
    # 简单启发：含大量假名→日语；含韩文→韩语；含中文→华语；否则默认国语/英语未知
    if any("\u3040" <= c <= "\u309f" or "\u30a0" <= c <= "\u30ff" for c in title):
        return "日语"
    if any("\uac00" <= c <= "\ud7af" for c in title):
        return "韩语"
    if any("\u4e00" <= c <= "\u9fff" for c in title):
        return "国语"
    return "英语"


def fetch_top_songs(area_id: int = 0, limit: int = 100, sleep: float = 0.25) -> list[dict]:
    """获取网易云「新歌速递」。area_id: 0全部 7华语 96欧美 8日本 16韩国。"""
    d = _post(
        "/weapi/v1/discovery/new/songs",
        {"areaId": area_id, "total": True},
    )
    if d.get("code") != 200:
        return []
    raw = d.get("data", []) or []
    out = []
    for item in raw[:limit]:
        parsed = _parse_item(item)
        if parsed:
            out.append(parsed)
        if sleep:
            time.sleep(sleep)
    return out


def fetch_lyric(song_id: int | str, sleep: float = 0.0) -> str:
    """获取网易云歌词原文（LRC 格式）。"""
    d = _post(
        "/weapi/song/lyric",
        {"id": str(song_id), "lv": -1, "tv": -1, "csrf_token": ""},
    )
    if sleep:
        time.sleep(sleep)
    if d.get("code") != 200:
        return ""
    lrc = d.get("lrc") or {}
    return lrc.get("lyric") or ""


if __name__ == "__main__":
    songs = fetch_top_songs(area_id=0, limit=5, sleep=0)
    print(f" fetched {len(songs)} netease songs")
    for s in songs[:3]:
        print(s["title"], s["artists"], s["release_date"], s["platform"])
