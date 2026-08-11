"""QQ音乐 新歌抓取 + 口味匹配引擎（lychee · 纯 QQ）

从 QQ音乐 NewSongServer 抓取全地区（内地/港台/欧美/韩国/日本/其他）最新发布歌曲，
按用户口味画像（歌手/作词作曲/风格/热度/旋律）打分排序，产出本周新歌推荐。
"""
import json, math, sys, time, os
from pathlib import Path
from datetime import datetime, timedelta
from collections import Counter

import requests

from src.music.user_ctx import user_path, user_dir, ensure_user

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data" / "music"

API_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://y.qq.com/",
    "Content-Type": "application/json",
}

# 新歌分类: type → 语言标签
CATEGORIES = {1: "内地", 2: "港台", 3: "欧美", 4: "韩国", 5: "日本", 6: "其他"}


def _norm(text: str) -> str:
    """跨平台去重键：小写、去非字母数字、去常见版本后缀。"""
    if not text:
        return ""
    t = text.lower()
    for suffix in ("(live)", "[live]", "(explicit)", "[explicit]",
                   "(acoustic)", "[acoustic]", "(feat.", "ft.", "with "):
        t = t.replace(suffix, "")
    return "".join(c for c in t if c.isalnum())


def _platform_key(s: dict) -> str:
    return _norm(s.get("title", "")) + "-" + _norm(s.get("artists", ""))


def fetch_new_songs(size: int = 100) -> list[dict]:
    """从 QQ音乐新歌首发抓取最新歌曲（多语言分类合并）。

    Returns:
        list of {title, artists, songmid, album, release_date, language, chart_rank, platform}
    """
    all_songs = []
    for type_id, lang_label in CATEGORIES.items():
        body = {
            "comm": {"ct": 24},
            "req": {
                "module": "newsong.NewSongServer",
                "method": "get_new_song_info",
                "param": {"type": type_id, "size": size},
            },
        }
        try:
            r = requests.post(API_URL, json=body, headers=HEADERS, timeout=12)
            data = r.json().get("req", {}).get("data", {})
            songlist = data.get("songlist", [])
            for s in songlist:
                singers = "/".join(
                    si.get("name", "") for si in s.get("singer", [])
                )
                album = s.get("album", {})
                item = {
                    "title": s.get("name", "") or s.get("title", ""),
                    "artists": singers,
                    "songmid": s.get("mid", ""),
                    "album": album.get("name", "") if isinstance(album, dict) else "",
                    "release_date": s.get("time_public", "")
                                    or (album.get("time_public", "")
                                        if isinstance(album, dict) else ""),
                    "language": lang_label,
                    "interval": s.get("interval", 0),
                    "genre": s.get("genre", 0),
                    "chart_type": type_id,
                    "platform": "qq",
                }
                item["_key"] = _platform_key(item)
                all_songs.append(item)
            time.sleep(0.3)  # 礼貌限速
        except Exception as e:
            print(f"  [warn] 抓取 {lang_label} 新歌失败: {e}", file=sys.stderr)
    return all_songs


def fetch_all_new_songs(qq_size: int = 100) -> list[dict]:
    """抓取 QQ音乐全地区新歌（内地/港台/欧美/韩国/日本/其他），去重。

    lychee 仅用 QQ音乐，不再合并网易云。所有歌 platform='qq'。
    """
    print("[new_releases] 抓取 QQ音乐全地区新歌...")
    qq = fetch_new_songs(size=qq_size)
    # 跨分类去重
    seen = set()
    unique = []
    for s in qq:
        key = s.get("_key") or _platform_key(s)
        if key and key not in seen:
            seen.add(key)
            s["platform"] = "qq"
            unique.append(s)
    print(f"[new_releases] QQ音乐全地区去重后: {len(unique)} 首")
    return unique


# ---- 口味匹配 ----

def _user_data_path(user_id: str | None, name: str):
    from src.music.user_ctx import user_path
    return Path(user_path(user_id, name))


def _load_taste(user_id: str | None = None) -> dict:
    """优先加载五维口味模型 v3（701首完整富化），回退旧版。"""
    p3 = _user_data_path(user_id, "taste_profile_v3.json")
    if p3.exists():
        return json.load(open(p3, encoding="utf-8"))
    p = _user_data_path(user_id, "taste_profile.json")
    if p.exists():
        return json.load(open(p, encoding="utf-8"))
    return {}

def _load_enriched(user_id: str | None = None) -> list[dict]:
    p = _user_data_path(user_id, "enriched_songs.json")
    if p.exists():
        d = json.load(open(p, encoding="utf-8"))
        if isinstance(d, dict):
            return d.get("songs", list(d.values()))
        return d
    return []

def _load_artist_styles() -> dict:
    p = DATA / "artist_style_map.json"
    if p.exists():
        return json.load(open(p, encoding="utf-8"))
    return {}


# ---- 轻量入职（卡片导入） --------------------------------------------------

def build_lightweight_profile(user_id: str | None, library_size: int = 0) -> dict | None:
    """由 listening_history + 全局 artist_style_map 生成轻量口味画像。

    仅用于新歌推荐的「歌手 / 风格」个性化（不读谱、不逐首元数据富集，秒级完成）。
    写入用户目录的 taste_profile.json，weekly_new_recommendations 即可按该用户口味打分。
    返回画像 dict；无听歌记录时返回 None。
    """
    hist_path = user_path(user_id, "listening_history.json")
    if not os.path.exists(hist_path):
        return None
    history = json.load(open(hist_path, encoding="utf-8"))
    style_map = _load_artist_styles()

    style_counter: dict[str, float] = {}
    top_artists: list[dict] = []
    for row in history:
        a = (row.get("artist") or "").strip()
        if not a:
            continue
        plays = float(row.get("plays") or 0)
        top_artists.append({"name": a, "plays": plays})
        for st in style_map.get(a, {}).get("styles", []):
            style_counter[st] = style_counter.get(st, 0) + plays  # 按播放量加权

    if not top_artists:
        return None

    profile = {
        "library_size": library_size or len(top_artists),
        "listened_artists": len(top_artists),
        "top_artists": sorted(top_artists, key=lambda x: -x["plays"])[:20],
        "style_dist": {k: round(v, 1) for k, v in
                       sorted(style_counter.items(), key=lambda x: -x[1])[:30]},
        "source": "lightweight_card_import",
    }
    out = user_path(user_id, "taste_profile.json")
    json.dump(profile, open(out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    return profile


def onboard_from_songs(user_id: str, songs: list[dict], library_size: int | None = None,
                        source_name: str = "QQ歌单") -> dict:
    """他人入职核心：给定结构化歌曲列表 → 落盘该用户听歌记录 → 生成轻量口味画像。

    songs: [{"title": str, "artists": str}, ...]
    """
    ensure_user(user_id)
    if not songs:
        raise ValueError("歌单为空，无法生成口味画像")

    # 聚合歌手权重（主歌手 1.0，合作歌手 0.5）
    counter: Counter[str] = Counter()
    norm_entries = []
    for e in songs:
        arts = [a.strip() for a in (e.get("artists") or "").split("/") if a.strip()]
        title = (e.get("title") or "").strip()
        mid = (e.get("songmid") or e.get("mid") or "").strip()
        norm_entries.append({"title": title, "artists": arts, "songmid": mid})
        for i, a in enumerate(arts):
            counter[a] += 1.0 if i == 0 else 0.5

    history = [{"artist": a, "plays": round(c, 2)} for a, c in counter.most_common()]
    music_dir = user_dir(user_id)
    os.makedirs(music_dir, exist_ok=True)
    json.dump(history, open(os.path.join(music_dir, "listening_history.json"),
                            "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    pl = {"name": source_name, "songs": norm_entries}
    json.dump(pl, open(os.path.join(music_dir, "playlist_songs.json"),
                       "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    profile = build_lightweight_profile(user_id, library_size=library_size or len(songs))
    top = (profile or {}).get("top_artists", [])[:10]
    styles = list((profile or {}).get("style_dist", {}).keys())[:8]
    return {
        "user_id": user_id,
        "songs_parsed": len(songs),
        "artists": len(history),
        "top_artists": [t["name"] for t in top],
        "top_styles": styles,
        "has_profile": profile is not None,
    }


def onboard_user(user_id: str, card_text: str, library_size: int | None = None) -> dict:
    """他人入职：解析 QQ 分享卡片文本 → 落盘该用户听歌记录 → 生成轻量口味画像。

    保留文本解析入口，供脚本与旧集成使用；前端现已改用二维码图片入职。
    """
    from src.music.recommender_v2 import parse_share_card

    entries = parse_share_card(card_text)
    if not entries:
        raise ValueError("解析不出歌单，请粘贴 QQ音乐「我喜欢的音乐」分享文本（每行 歌名 - 歌手）")
    songs = [{"title": e.get("title", ""), "artists": e.get("artist", "")}
             for e in entries]
    return onboard_from_songs(user_id, songs, library_size=library_size,
                              source_name="QQ分享卡片文本")


def _load_user_melody(user_id: str | None):
    """按用户加载旋律画像：default 用全局；其他用户用自己的（没有则返回 None，不造假分）。"""
    from src.music.melody_preference import load_profile as load_global
    if not user_id or str(user_id).strip().lower() in ("default", "none", ""):
        return load_global()
    p = Path(user_path(user_id, "user_melody_profile.json"))
    if p.exists():
        return json.load(open(p, encoding="utf-8"))
    return None


def score_new_songs(new_songs: list[dict], taste: dict | None = None,
                    artist_styles: dict | None = None) -> list[dict]:
    """按用户口味给新歌打分。

    评分维度（0-100，用户定义的 5 大核心决策点，热度已降权）:
      - 曲谱旋律 (35): 读谱后与用户旋律画像的结构相似度（apply_melody_matching 注入，最高权重之一）
      - 风格   (35): 歌手已知风格与用户偏好风格重合度（最高权重之一）
      - 创作者 (15): 新歌歌手是用户偏好的唱作人/作曲人 → 加分
      - 歌手   (10): 新歌歌手在用户常听歌手列表 → 高分（真口味信号，但权重已下调）
      - 热度   (5): 新歌榜排名越靠前分越高（仅作极弱质量信号，不主导）
     语言/新鲜度不再作为打分因子（候选池本身已是上周新歌，地区差异意义不大）。
    """
    if taste is None:
        taste = _load_taste()
    if artist_styles is None:
        artist_styles = _load_artist_styles()

    # v3 五维数据（不存在时优雅降级为空）
    dims = taste.get("dimensions", {})
    v3_creators = set()
    for c in dims.get("creator", {}).get("top_composers", []):
        v3_creators.add(c.get("name", ""))
    v3_songwriters = set(dims.get("creator", {}).get("singer_songwriters", []))

    # 用户常听歌手集合 + 播放量
    top_artists = {}
    for a in taste.get("top_artists", []):
        if isinstance(a, dict):
            top_artists[a.get("name", "")] = a.get("plays", 1)
        elif isinstance(a, (list, tuple)) and len(a) >= 2:
            top_artists[str(a[0])] = a[1]
    top_artist_names = set(top_artists.keys())

    # 用户偏好风格
    pref_styles = set()
    for style, _ in taste.get("style_dist", {}).items():
        pref_styles.add(style)
    # 取 top 5 风格
    sorted_styles = sorted(taste.get("style_dist", {}).items(),
                           key=lambda x: -x[1])[:5]
    top_styles = {s for s, _ in sorted_styles}

    scored = []
    for i, song in enumerate(new_songs):
        artists_raw = song.get("artists", "")
        # 拆分多歌手
        artist_list = [a.strip() for a in artists_raw.split("/") if a.strip()]

            # 1. 歌手亲和 (0-10，权重已下调)
        artist_score = 0
        matched_artists = []
        for a in artist_list:
            if a in top_artist_names:
                plays = top_artists.get(a, 1)
                # 对数缩放，避免播放量差距过大
                artist_score = max(artist_score, min(10, 4 + math.log10(max(plays, 1)) * 3))
                matched_artists.append(a)
        # 歌手部分匹配（名字包含）
        if not matched_artists:
            for a in artist_list:
                for ta in top_artist_names:
                    if ta and (ta in a or a in ta):
                        artist_score = max(artist_score, 6)
                        matched_artists.append(a)
                        break

        # 2. 风格匹配 (0-35，最高权重之一)
        style_score = 0
        matched_styles = []
        for a in artist_list:
            styles = artist_styles.get(a, {}).get("styles", [])
            overlap = set(styles) & top_styles
            if overlap:
                style_score = max(style_score, min(35, len(overlap) * 13))
                matched_styles = list(overlap)

        # 3. 创作者维度 (0-15)：歌手是用户偏好的唱作人/常见作曲人
        creator_score = 0
        matched_creator = ""
        for a in artist_list:
            if a in v3_songwriters:
                creator_score = 15
                matched_creator = a
                break
            if a in v3_creators:
                creator_score = max(creator_score, 10)
                matched_creator = matched_creator or a

        # 4. 热度/排名 (0-5)：仅作极弱质量信号，不主导
        pop_score = round(5 * (1 - i / max(1, len(new_songs) - 1)), 1) if len(new_songs) > 1 else 5

        # 5. 曲谱旋律 (0-35) 由 apply_melody_matching 注入；此处先预留占位，不计入 base

        base = artist_score + style_score + creator_score + pop_score  # 0-65
        # 旋律读谱后由 apply_melody_matching 把 melody 叠加进 match_score（上限 +35 → 100）

        # 生成推荐理由
        reasons = []
        if matched_artists:
            reasons.append(f"你常听 { '/'.join(matched_artists[:2])}")
        if matched_creator and creator_score >= 10:
            reasons.append(f"{matched_creator} 是你偏好的唱作人")
        elif matched_creator:
            reasons.append(f"作曲人 {matched_creator} 出现在你的曲库")
        if matched_styles:
            reasons.append(f"风格匹配 { '/'.join(matched_styles[:2])}")
        if not reasons:
            reasons.append("新歌探索")

        scored.append({
            **song,
            "match_score": round(base, 1),
            "factors": {
                "artist": round(artist_score, 1),
                "style": round(style_score, 1),
                "creator": round(creator_score, 1),
                "popularity": round(pop_score, 1),
                "melody": None,  # 读谱后注入
            },
            "matched_artists": matched_artists,
            "matched_styles": matched_styles,
            "reason": " · ".join(reasons),
        })

    scored.sort(key=lambda x: -x["match_score"])
    return scored


def apply_melody_matching(scored: list[dict], top_k: int = 40,
                          sleep: float = 0.2,
                          user_id: str | None = None) -> list[dict]:
    """对基础分 Top-K 候选新歌逐首做「旋律」维度打分（方案3：无谱/代理）。

    ⚠️ 用户选定方案3：新歌旋律维度 = 代理（无谱/代理）。
    QQ音乐曲谱库对当周新歌覆盖率仅约 2%，真·OMR 对绝大多数新歌读不到谱，
    故新歌旋律用「BPM + 歌词时间轴节奏结构」代理（来自 QQ 公开数据，不经本地 VLM），
    并诚实标注「无谱·节奏代理（非真谱）」，绝不假装成真实曲谱匹配。

    用户的 701 首真实曲谱旋律画像（v4，真·OMR，由后台脚本独立生成）用于口味总结，
    不在此参与新歌打分——两条链路解耦，互不污染。

    旋律是用户定义的 5 大核心决策点之一（权重 35/100，与风格并列最高）：
    - 取到代理结构: melody 因子 = 相似度 * 35 * 置信度；
    - 取不到:       melody 因子 = None，标注「无谱子数据」，不给假分。
    """
    try:
        from .sheet_fetcher import (
            fetch_lyric_profile, fetch_netease_lyric_profile, fetch_sheet_for_song
        )
        from .melody_preference import load_profile, similarity
    except ImportError:  # 直接以脚本运行时
        from src.music.sheet_fetcher import (
            fetch_lyric_profile, fetch_netease_lyric_profile, fetch_sheet_for_song
        )
        from src.music.melody_preference import load_profile, similarity

    profile = _load_user_melody(user_id)
    if not profile:
        for s in scored:
            s["factors"]["melody"] = None
            s["melody_note"] = "旋律代理画像缺失（无 bpm/歌词结构数据）"
        return scored

    print(f"[new_releases] 对 Top{min(top_k, len(scored))} 候选逐首做旋律代理（无谱/代理）...")

    melody_songs = []  # 收集有数据的歌，用于写入 sheets.json

    for s in scored[:top_k]:
        platform = s.get("platform", "qq")
        sheet = None
        if platform == "netease":
            nid = s.get("songmid")
            if nid:
                sheet = fetch_netease_lyric_profile(nid, duration=s.get("interval"), sleep=sleep)
        else:
            mid = s.get("songmid") or s.get("mid")
            if mid:
                sheet = fetch_lyric_profile(mid, genre_id=s.get("genre"),
                                            duration=s.get("interval"), sleep=sleep)
                if sheet is None:
                    sheet = fetch_sheet_for_song(mid, sleep=sleep)
        if sheet and not sheet.get("_available"):
            melody_songs.append({"songmid": s.get("songmid") or s.get("mid"),
                                 "title": s.get("title", ""),
                                 "artist": s.get("singer", "") or s.get("artist", "")})
        sim, conf = similarity(profile, sheet)
        if sim is None:
            s["factors"]["melody"] = None
            s["melody_note"] = "无谱·节奏代理无法计算（QQ新歌未提供BPM/歌词结构）"
        else:
            melody_score = round(sim * 35 * conf, 1)
            s["factors"]["melody"] = melody_score
            s["match_score"] = round(s["match_score"] + melody_score, 1)
            bpm_val = sheet.get("bpm")
            bpm_txt = f"{bpm_val} BPM" if bpm_val else "BPM未公开"
            s["melody_note"] = (f"无谱·节奏代理（非真谱）: {bpm_txt} | "
                                f"演唱密度{sheet.get('chars_per_sec') or '?'}字/秒 | "
                                f"结构相似{sim:.0%}")
            if sim >= 0.7:
                s["reason"] += " · 旋律节奏合口味"
    for s in scored[top_k:]:
        s["factors"].setdefault("melody", None)

    scored.sort(key=lambda x: -x["match_score"])

    # 桥接：把抓到的 BPM/曲风/歌词节奏写入 sheets.json
    if melody_songs:
        try:
            from src.music.sheet_fetcher import _save_melody_profiles
            cached = {}
            cache_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "data", "music", "sheet_profiles.json")
            if os.path.exists(cache_path):
                with open(cache_path, encoding="utf-8") as f:
                    cached = json.load(f)
            _save_melody_profiles(cached, melody_songs)
        except Exception:
            pass

    return scored


def _last_week_range(today: datetime | None = None) -> tuple[datetime, datetime]:
    """返回「上周一 00:00」到「上周日 23:59:59」的日期范围。

    若今天是周一，则上周为前一周（Mon-Sun）；否则退回最近 7 天。
    """
    if today is None:
        today = datetime.now()
    # 先归到今天的 00:00:00，避免跨天时区/时间分量导致边界错误
    today_mid = today.replace(hour=0, minute=0, second=0, microsecond=0)
    # weekday(): Mon=0 ... Sun=6
    days_since_mon = today_mid.weekday()
    last_mon = today_mid - timedelta(days=days_since_mon + 7)
    last_sun = last_mon + timedelta(days=7) - timedelta(seconds=1)
    return last_mon, last_sun


def weekly_new_recommendations(top_n: int = 20, period: str = "last_week",
                               user_id: str | None = None) -> dict:
    """完整流程：抓新歌 → 打分 → 返回推荐。

    Args:
        top_n: 最多返回多少首（不再硬限 15）。
        period: "last_week" 仅保留上周发布；"recent" 保留最近 14 天（兜底）。

    Returns:
        {
            "fetch_date": "2026-07-28",
            "period": "last_week",
            "total_fetched": 94,
            "recommendations": [...top_n],
            "language_dist": {"内地": 30, ...}
        }
    """
    unique = fetch_all_new_songs(qq_size=100)

    taste = _load_taste(user_id=user_id)
    artist_styles = _load_artist_styles()
    scored = score_new_songs(unique, taste=taste, artist_styles=artist_styles)
    # 先读谱再判断：对全部候选逐首读谱（真实旋律/结构匹配）后重排，
    # 确保最终推荐都经过「曲谱旋律」这一核心决策点评估
    scored = apply_melody_matching(scored, top_k=len(scored), sleep=0.3,
                                   user_id=user_id)

    # 按用户要求：每周一推荐上周新歌；默认启用 last_week 过滤
    now = datetime.now()
    period_label = period
    filtered = []
    if period == "last_week":
        start, end = _last_week_range(now)
        filtered = []
        for s in scored:
            release = s.get("release_date", "")
            if not release:
                continue
            try:
                rd = datetime.strptime(release[:10], "%Y-%m-%d")
                if start <= rd <= end:
                    filtered.append(s)
            except (ValueError, TypeError):
                continue
        if filtered:
            top = filtered[:top_n]
        else:
            # 上周无数据时优雅降级到最近 14 天，避免页面空白
            top = scored[:top_n]
            period_label = "recent_fallback"
    else:
        top = scored[:top_n]
        filtered = scored

    period_release_count = len(filtered) if period == "last_week" else len(unique)

    lang_count = {}
    for s in unique:
        lang_count[s.get("language", "未知")] = lang_count.get(s.get("language", "未知"), 0) + 1

    # 构造口味画像摘要，供前端显示「分析曲目」等指标
    dims = taste.get("dimensions", {})
    mel = dims.get("melody", {})
    pop = dims.get("popularity", {})
    style_dist = taste.get("style_dist", {})
    if isinstance(style_dist, list):
        style_dist = {k: v for k, v in style_dist}
    prof_view = {
        "library_size": taste.get("library_size", 0),
        "listened_artists": dims.get("artist", {}).get("coverage"),
        "top_artists": taste.get("top_artists", [])[:10],
        "style_dist": style_dist,
        "mood_dist": {},
        "avg_melody": {
            "bpm": mel.get("bpm_median"),
            "duration_sec": mel.get("duration_median_sec"),
        },
        "popularity_pref": {
            "median": pop.get("median"),
            "p25": pop.get("p25"),
            "p75": pop.get("p75"),
        },
    }

    result = {
        "fetch_date": now.strftime("%Y-%m-%d %H:%M"),
        "period": period_label,
        "period_start": start.strftime("%Y-%m-%d") if period == "last_week" else "",
        "period_end": end.strftime("%Y-%m-%d") if period == "last_week" else "",
        "total_fetched": len(unique),
        "period_release_count": period_release_count,
        "recommendations": top,
        "language_dist": lang_count,
        "profile": prof_view,
    }

    # 落盘缓存
    from src.music.user_ctx import user_path
    cache_path = Path(user_path(user_id, "new_releases_cache.json"))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    json.dump(result, open(cache_path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"[new_releases] 缓存 → {cache_path}")

    return result


if __name__ == "__main__":
    r = weekly_new_recommendations(top_n=20)
    print(f"\n=== 本周新歌推荐（{r['fetch_date']}）===")
    print(f"共抓取 {r['total_fetched']} 首，推荐 Top {len(r['recommendations'])}：\n")
    for i, s in enumerate(r["recommendations"], 1):
        print(f"{i:2d}. {s['title']} — {s['artists']}")
        print(f"    发布: {s['release_date']} | 语言: {s['language']} | 分: {s['match_score']}")
        print(f"    理由: {s['reason']}")
        mel = s['factors'].get('melody')
        mel_txt = f"旋律{mel}" if mel is not None else "旋律—(无谱子数据)"
        print(f"    因子: 歌手{s['factors']['artist']} 风格{s['factors']['style']} 创作者{s['factors']['creator']} 热度{s['factors']['popularity']} 旋律{mel_txt}")
        if s.get('melody_note'):
            print(f"    {s['melody_note']}")
        print()