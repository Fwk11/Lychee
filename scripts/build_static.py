#!/usr/bin/env python3
"""构建静态站点 dist/，用于部署到云端公开链接。

把需要后端的计算（推荐/画像/曲库）在构建期预计算并落盘到 dist/data/，
前端以 window.__STATIC__=true 模式直接读取本地 JSON，无需后端即可完整运行
音乐模块；视频模块在静态站显示「连接本地后端」说明。
"""
from __future__ import annotations

import json
import os
import shutil
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from src.music.recommender_v2 import recommend_all

WEB = os.path.join(ROOT, "web")
DIST = os.path.join(ROOT, "output", "dist")
DATA = os.path.join(DIST, "data")
ENRICHED = os.path.join(ROOT, "data", "music", "enriched_songs.json")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--api-base', default='', help='远端后端地址，如 https://xxx.trycloudflare.com；传入则生成混合模式（音乐打包+视频小说走后端）')
    args = ap.parse_args()
    API_BASE = args.api_base
    if not os.path.exists(ENRICHED):
        print("[build] 错误：enriched_songs.json 不存在，请先运行 scripts/archive/enrich_music.py")
        sys.exit(1)
    os.makedirs(DATA, exist_ok=True)
    # 清理旧 dist
    if os.path.exists(DIST):
        shutil.rmtree(DIST)
    os.makedirs(DATA)

    # 预计算
    rec = recommend_all(top_n=18)
    with open(os.path.join(DATA, "recommend.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)
    with open(os.path.join(DATA, "profile.json"), "w", encoding="utf-8") as f:
        json.dump(rec.get("profile", {}), f, ensure_ascii=False)

    # 曲库（摘要字段）
    songs = json.load(open(ENRICHED, encoding="utf-8"))
    out = [{"songmid": s.get("songmid"), "title": s.get("title"),
            "artists": s.get("artists", []), "styles": s.get("styles", []),
            "moods": s.get("moods", []), "popularity": s.get("popularity")}
           for s in songs]
    with open(os.path.join(DATA, "explore.json"), "w", encoding="utf-8") as f:
        json.dump({"total": len(out), "songs": out}, f, ensure_ascii=False)

    # 新歌推荐（用缓存，构建时不实时抓取）
    cache = os.path.join(ROOT, "data", "music", "new_releases_cache.json")
    if os.path.exists(cache):
        shutil.copy(cache, os.path.join(DATA, "new_releases.json"))
        print(f"        新歌推荐: 已打包缓存")
    else:
        # 无缓存则写空
        with open(os.path.join(DATA, "new_releases.json"), "w", encoding="utf-8") as f:
            json.dump({"fetch_date": "", "total_fetched": 0,
                       "recommendations": [], "language_dist": {}}, f, ensure_ascii=False)
        print(f"        新歌推荐: 无缓存（需本地后端抓取）")

    # 拷贝前端，改写为静态模式
    import re, time
    ts = int(time.time())
    html = open(os.path.join(WEB, "index.html"), encoding="utf-8").read()

    # 样式表：/static/style.css(?v=...) → 相对路径 + 新时间戳
    html = re.sub(r'href="/static/style\.css(\?[^"]*)?"',
                  f'href="style.css?v={ts}"', html)

    # 资源文件（图标等）：/static/assets/... → assets/...
    html = re.sub(r'src="/static/assets/([^"]+)"', r'src="assets/\1"', html)
    html = re.sub(r'href="/static/assets/([^"]+)"', r'href="assets/\1"', html)

    # js 脚本：收集 index.html 中的加载顺序，统一改为相对路径 + 新时间戳
    js_names = re.findall(r'/static/js/([a-z]+)\.js', html)
    if not js_names:
        js_names = ["util", "theme", "api", "route", "music",
                    "video", "novel", "effects", "agent", "bootstrap"]

    def _js_repl(m):
        return f'js/{m.group(1)}.js?v={ts}'
    html = re.sub(r'/static/js/([a-z]+)\.js(\?[^"]*)?', _js_repl, html)

    # 在第一个 js 脚本前注入静态模式开关
    first_tag = f'js/{js_names[0]}.js?v={ts}'
    static_script = '<script>window.__STATIC__=true;'
    if API_BASE:
        static_script += 'window.__API_BASE__=' + json.dumps(API_BASE) + ';'
        # 公开站注入「公开 key」（api_keys 最后一个），与本地主 key 分离，泄露可单独吊销
        from src.api.config import settings as _cfg
        _public_key = _cfg.api_keys[-1] if len(_cfg.api_keys) > 1 else _cfg.api_keys[0]
        static_script += 'window.__API_KEY__=' + json.dumps(_public_key) + ';'
    static_script += '</script>\n'
    html = html.replace(f'<script src="{first_tag}"',
                        static_script + f'<script src="{first_tag}"', 1)

    open(os.path.join(DIST, "index.html"), "w", encoding="utf-8").write(html)
    shutil.copy(os.path.join(WEB, "style.css"), os.path.join(DIST, "style.css"))
    # 拷贝整段 js 脚本目录（不再打包单文件 app.js）
    js_src = os.path.join(WEB, "js")
    js_dst = os.path.join(DIST, "js")
    if os.path.exists(js_dst):
        shutil.rmtree(js_dst)
    shutil.copytree(js_src, js_dst)

    # 拷贝资源目录
    assets_src = os.path.join(WEB, "assets")
    assets_dst = os.path.join(DIST, "assets")
    if os.path.exists(assets_dst):
        shutil.rmtree(assets_dst)
    if os.path.exists(assets_src):
        shutil.copytree(assets_src, assets_dst)

    print(f"[build] 完成 → {DIST}")
    print(f"        数据: recommend/profile/explore 共 {len(out)} 首")
    print(f"        歌单套数: {len(rec.get('playlists', {}))}")


if __name__ == "__main__":
    main()
