#!/usr/bin/env python3
"""Import a QQ音乐 playlist (e.g. the "我喜欢" favourites) into the project.

Resolves a share short-link or a numeric disstid, pulls the full song list
via the public qzone endpoint (no login needed for public playlists), then:

  * writes data/music/listening_history.json  (artist -> like-count weights)
  * writes data/music/playlist_songs.json     (raw song rows for the web UI)

多用户：加 --user_id 会把数据写到 data/music/users/<user_id>/，
不传或传 default 则仍落 data/music（兼容存量默认用户）。

Usage:
    python scripts/import_qqmusic_playlist.py --link "https://c6.y.qq.com/base/fcgi-bin/u?__=XXXX"
    python scripts/import_qqmusic_playlist.py --disstid 2434197420
    python scripts/import_qqmusic_playlist.py --disstid 2434197420 --user_id alice
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import Counter

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def user_music_dir(user_id: str | None) -> str:
    """返回某用户的音乐数据目录。

    user_id 为空或 'default' 时回退到 data/music（兼容存量数据）；
    否则落到 data/music/users/<user_id>/，实现多用户隔离。
    """
    if not user_id or str(user_id).strip().lower() in ("default", "none", ""):
        return os.path.join(ROOT, "data", "music")
    sys.path.insert(0, os.path.join(ROOT, "src", "music"))
    from user_ctx import user_dir
    return user_dir(user_id)

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 Chrome/120 Safari/537.36",
      "Referer": "https://y.qq.com/"}


def _get(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout).read()


def resolve_disstid(link: str) -> str:
    """Follow a QQ音乐 share short-link to extract the numeric playlist id."""
    if re.fullmatch(r"\d+", link.strip()):
        return link.strip()

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    try:
        opener.open(urllib.request.Request(link, headers=UA), timeout=20)
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "") or ""
        m = re.search(r"[?&]id=(\d+)", loc)
        if m:
            return m.group(1)
    # fallback: some links already contain id=
    m = re.search(r"[?&]id=(\d+)", link)
    if m:
        return m.group(1)
    raise RuntimeError(f"无法从链接解析歌单 id: {link}")


def fetch_playlist(disstid: str) -> dict:
    url = ("https://c.y.qq.com/qzone/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg"
           f"?type=1&json=1&utf8=1&onlysong=0&disstid={disstid}"
           "&format=json&g_tk=5381&loginUin=0&hostUin=0&inCharset=utf8"
           "&outCharset=utf-8&notice=0&platform=yqq&needNewCode=0")
    data = json.loads(_get(url).decode("utf-8", "ignore"))
    if data.get("code") != 0:
        raise RuntimeError(f"接口返回错误 code={data.get('code')}")
    cd = (data.get("cdlist") or [{}])[0]
    songs = []
    for s in cd.get("songlist", []):
        singers = [a.get("name", "") for a in s.get("singer", []) if a.get("name")]
        if not singers:
            continue
        songs.append({
            "title": s.get("songname", ""),
            "artists": singers,
            "album": (s.get("album") or {}).get("name", ""),
            "songmid": s.get("songmid", ""),
        })
    return {
        "disstid": disstid,
        "name": cd.get("dissname", ""),
        "songnum": cd.get("songnum") or len(songs),
        "songs": songs,
    }


def import_playlist(link_or_id: str, user_id: str | None = None) -> dict:
    disstid = resolve_disstid(link_or_id)
    pl = fetch_playlist(disstid)

    # aggregate: each song contributes weight 1 to its PRIMARY artist,
    # 0.5 to each co-artist (collaborations still signal taste)
    counter: Counter[str] = Counter()
    for s in pl["songs"]:
        for i, a in enumerate(s["artists"]):
            counter[a] += 1.0 if i == 0 else 0.5

    history = [{"artist": a, "plays": c} for a, c in counter.most_common()]

    music_dir = user_music_dir(user_id)
    os.makedirs(music_dir, exist_ok=True)
    hist_path = os.path.join(music_dir, "listening_history.json")
    with open(hist_path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    raw_path = os.path.join(music_dir, "playlist_songs.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(pl, f, ensure_ascii=False, indent=2)

    return {
        "disstid": disstid,
        "playlist": pl["name"],
        "songs": len(pl["songs"]),
        "artists": len(history),
        "user_id": user_id or "default",
        "history_path": hist_path,
        "raw_path": raw_path,
        "top_artists": [a for a, _ in counter.most_common(20)],
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--link", help="QQ音乐歌单分享链接（短链或长链）")
    ap.add_argument("--disstid", help="歌单数字 id")
    ap.add_argument("--user_id", help="目标用户 id（多用户隔离；默认/不传=默认用户）")
    args = ap.parse_args()
    src = args.link or args.disstid
    if not src:
        ap.error("需要 --link 或 --disstid")
    try:
        result = import_playlist(src, user_id=args.user_id)
    except Exception as e:
        print(f"[error] 导入失败: {e}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))
