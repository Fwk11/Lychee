"""Lychee API 路由：音乐（听歌口味画像 / 每周推荐 / 曲谱 / 新歌）。

``/api/music/*``    第一代单维推荐，前端已不再调用，保留供脚本与回归对比。
``/api/music/v2/*`` 现役多维引擎：四维加权画像 + 主题歌单 + 新歌榜匹配。
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from src.api import tasks
from src.api.config import MUSIC_DIR, ROOT
from src.api.security import rate_limit, require_key, safe_qq_source
from src.music.melody import import_sheet, load_sheets
from src.music.new_releases import weekly_new_recommendations, onboard_user, onboard_from_songs
from src.music import qr_onboard
from src.music.recommender import build_recommendation
from src.music.recommender import generate as generate_weekly
from src.music.recommender_v2 import recommend_all, recommend_for_card
from src.music.plus import (
    contextual_recommend, music_personality, music_evolution,
    music_rlhf_annotate, deconstruct,
)
from src.music.user_ctx import list_users, ensure_user, user_dir, user_path

log = logging.getLogger("lychee")

router = APIRouter()

# 记录每个用户最近一次 OMR 任务，便于 melody-profile 返回「生成中」状态
_omr_tasks: dict[str, str] = {}


@router.get("/api/music/profile", dependencies=[Depends(require_key)])
def music_profile() -> dict:
    rec = build_recommendation(top_n=0)
    return {"profile": rec["profile"], "used_example": rec["used_example"],
            "engine": rec["engine"]}

@router.get("/api/music/recommend", dependencies=[Depends(require_key)])
def music_recommend() -> dict:
    rec = build_recommendation(top_n=15)
    path = generate_weekly()  # 同时落盘 markdown
    return {**rec, "markdown_path": path}

@router.get("/api/music/playlist", dependencies=[Depends(require_key)])
def music_playlist(
    user_id: str = Query(None, max_length=64),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
) -> dict:
    path = user_path(user_id, "playlist_songs.json")
    if not os.path.exists(path):
        return {"name": None, "total": 0, "page": page, "songs": [],
                "user_id": user_id or "default"}
    with open(path, encoding="utf-8") as f:
        pl = json.load(f)
    songs = pl.get("songs", [])
    start = (page - 1) * size
    return {"name": pl.get("name"), "total": len(songs), "page": page,
            "songs": songs[start:start + size], "user_id": user_id or "default"}

class ImportReq(BaseModel):
    source: str = Field(min_length=1, max_length=300)
    user_id: str | None = Field(None, max_length=64)

@router.post("/api/music/import", dependencies=[Depends(require_key), Depends(rate_limit)])
def music_import(req: ImportReq) -> dict:
    src = safe_qq_source(req.source)
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from import_qqmusic_playlist import import_playlist
    task_id = tasks.submit(import_playlist, src, req.user_id)
    return {"status": "started", "task_id": task_id, "user_id": req.user_id or "default"}

@router.get("/api/music/v2/profile", dependencies=[Depends(require_key)])
def music_profile_v2(user_id: str = Query(None, max_length=64)) -> dict:
    rec = recommend_all(user_id=user_id)
    if rec.get("error"):
        raise HTTPException(409, rec["error"])
    return rec["profile"]

@router.get("/api/music/v2/recommend", dependencies=[Depends(require_key)])
def music_recommend_v2(
    top_n: int = Query(18, ge=1, le=50),
    user_id: str = Query(None, max_length=64),
) -> dict:
    rec = recommend_all(top_n=top_n, user_id=user_id)
    if rec.get("error"):
        raise HTTPException(409, rec["error"])
    return {"profile": rec["profile"], "playlists": rec["playlists"],
            "weights": rec["weights"], "melody_source": rec["melody_source"],
            "user_id": rec.get("user_id", user_id or "default")}

class ForCardReq(BaseModel):
    card_text: str = Field(min_length=1, max_length=20000)
    top_n: int = Field(18, ge=1, le=50)

@router.post("/api/music/v2/recommend-for-card",
             dependencies=[Depends(require_key), Depends(rate_limit)])
def music_recommend_for_card(
    req: ForCardReq,
    user_id: str = Query(None, max_length=64),
) -> dict:
    """识别他人分享的音乐卡片（QQ音乐/网易云逐行文本或 JSON）→ 为「他」生成推荐。

    基于当前 user_id 的曲库做匹配，临时画像只在此请求内生效，不污染用户画像。
    """
    d = recommend_for_card(req.card_text, top_n=req.top_n, user_id=user_id)
    # 解析失败/无匹配也返回 200 + error 字段，方便前端直接提示
    return d

@router.get("/api/music/v2/explore", dependencies=[Depends(require_key)])
def music_explore(user_id: str = Query(None, max_length=64)) -> dict:
    """返回整库富数据，供前端做可交互筛选/排序。"""
    path = user_path(user_id, "enriched_songs.json")
    if not os.path.exists(path):
        raise HTTPException(409, "enriched_songs.json 尚未生成，请先运行 enrichment")
    with open(path, encoding="utf-8") as f:
        songs = json.load(f)
    sheets = load_sheets()
    out = []
    for s in songs:
        out.append({
            "songmid": s.get("songmid"), "title": s.get("title"),
            "artists": s.get("artists", []), "styles": s.get("styles", []),
            "moods": s.get("moods", []), "popularity": s.get("popularity"),
            "has_sheet": s.get("songmid") in sheets,
        })
    return {"total": len(out), "songs": out}

class SheetReq(BaseModel):
    songmid: str = Field(min_length=1, max_length=64)
    sheet: str = Field(min_length=1, max_length=2000)
    title: str = ""
    artist: str = ""

@router.post("/api/music/v2/sheets", dependencies=[Depends(require_key), Depends(rate_limit)])
def music_import_sheet(req: SheetReq) -> dict:
    """导入真实简谱/音高序列，覆盖该歌的旋律画像（旋律相似度随之变真实）。"""
    if not req.songmid.isalnum():
        raise HTTPException(400, "非法 songmid")
    prof = import_sheet(req.songmid, req.sheet, req.title, req.artist)
    return {"status": "saved", "songmid": req.songmid, "profile": prof}

@router.get("/api/music/v2/new-releases", dependencies=[Depends(require_key)])
def new_releases(
    top_n: int = Query(20, ge=1, le=50),
    refresh: bool = Query(False),
    user_id: str = Query(None, max_length=64),
):
    """本周新歌推荐：从 QQ音乐新歌榜抓取 → 按口味画像匹配 → 推荐 Top N。"""
    cache = user_path(user_id, "new_releases_cache.json")
    if not refresh and os.path.exists(cache):
        try:
            data = json.load(open(cache, encoding="utf-8"))
            # 缓存有效就直接用
            recs = data.get("recommendations", [])[:top_n]
            data["recommendations"] = recs
            return data
        except Exception:
            pass
    # 抓取新数据
    try:
        result = weekly_new_recommendations(top_n=top_n, user_id=user_id)
        return result
    except Exception as e:
        raise HTTPException(502, f"抓取新歌失败: {e}")


# ---- 他人入职（上传 QQ 分享卡片 → 生成该用户口味画像） ----------------------
class OnboardReq(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    card_text: str = Field(min_length=1, max_length=20000)


@router.post("/api/music/v2/onboard", dependencies=[Depends(require_key)])
def music_onboard(req: OnboardReq) -> dict:
    """他人入职：粘贴 QQ音乐「我喜欢的音乐」分享文本，为该 user_id 生成专属口味画像。

    保留给脚本/旧集成使用；前端主入口已改为 /onboard-qr 二维码图片识别。
    """
    uid = req.user_id.strip()
    if not uid or "/" in uid or ".." in uid or uid.lower() == "default":
        raise HTTPException(400, "非法 user_id（不能用 default / 含 / 或 ..）")
    try:
        return onboard_user(uid, req.card_text)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.post("/api/music/v2/onboard-qr", dependencies=[Depends(require_key)])
async def music_onboard_qr(
    user_id: str = Query(..., min_length=1, max_length=64),
    file: UploadFile = File(...),
) -> dict:
    """他人入职（二维码图片）：上传 QQ 音乐分享卡片截图，自动识别二维码并抓取歌单，
    为该 user_id 生成专属口味画像。

    生成后该用户的 /api/music/v2/new-releases 即按其口味匹配（多用户隔离）。
    """
    uid = user_id.strip()
    if not uid or "/" in uid or ".." in uid or uid.lower() == "default":
        raise HTTPException(400, "非法 user_id（不能用 default / 含 / 或 ..）")
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(415, "请上传图片文件（jpg/png）")

    import tempfile, shutil
    suffix = Path(file.filename or "card.jpg").suffix or ".jpg"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name
    try:
        info = qr_onboard.extract_playlist_from_qr_image(tmp_path)
        result = onboard_from_songs(
            uid, info["songs"], library_size=len(info["songs"]),
            source_name="QQ音乐分享卡片"
        )
        result["disstid"] = info["disstid"]
        result["qr_url"] = info["qr_url"]
        # 异步触发该用户的「真·谱子画像」识别（OMR），不阻塞上传响应
        from src.music.omr_pipeline import run as omr_run
        task_id = tasks.submit(
            _omr_for_user, uid,
            on_progress=lambda cur, total, text: tasks.report(f"{text}（{cur}/{total}）", cur, total),
        )
        _omr_tasks[uid] = task_id
        result["omr_task_id"] = task_id
        return result
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        log.exception("二维码入职失败")
        raise HTTPException(500, f"处理失败: {e}")
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def _omr_for_user(user_id: str, on_progress=None) -> dict:
    """供 tasks 后台执行的 OMR 封装：进度上报到任务系统。"""
    from src.music.omr_pipeline import run as omr_run
    return omr_run(user_id=user_id, on_progress=on_progress)


@router.get("/api/music/v2/status", dependencies=[Depends(require_key)])
def music_status(user_id: str = Query(None, max_length=64)) -> dict:
    """轻量查询某用户是否已生成口味画像（避免为未入职用户触发昂贵的抓歌）。"""
    p_v3 = user_path(user_id, "taste_profile_v3.json")
    p = user_path(user_id, "taste_profile.json")
    has = os.path.exists(p) or os.path.exists(p_v3)
    lib = 0
    if has:
        try:
            d = json.load(open(p if os.path.exists(p) else p_v3, encoding="utf-8"))
            lib = d.get("library_size", 0)
        except Exception:
            pass
    return {"user_id": user_id or "default", "has_profile": has, "library_size": lib}


# ---- 旋律画像（真实曲谱 OMR 聚合） ------------------------------------------
# ---- 进阶特色功能（主流音乐媒体不具备） -------------------------------------
# 1) 「此刻」情境推荐：时间×天气×活动×情绪 → 可解释推荐
@router.get("/api/music/plus/contextual", dependencies=[Depends(require_key)])
def music_plus_contextual(
    hour: int = Query(None, ge=0, le=23),
    activity: str = Query(None, max_length=20),
    weather: str = Query(None, max_length=20),
    mood: str = Query(None, max_length=20),
    top_n: int = Query(12, ge=1, le=30),
    user_id: str = Query(None, max_length=64),
) -> dict:
    ctx = {k: v for k, v in
           {"hour": hour, "activity": activity, "weather": weather, "mood": mood}.items()
           if v is not None}
    return contextual_recommend(ctx, top_n=top_n, user_id=user_id)


# 2) 音乐人格画像
@router.get("/api/music/plus/personality", dependencies=[Depends(require_key)])
def music_plus_personality(user_id: str = Query(None, max_length=64)) -> dict:
    return music_personality(user_id=user_id)


# 3) 口味演化时间线
@router.get("/api/music/plus/evolution", dependencies=[Depends(require_key)])
def music_plus_evolution(user_id: str = Query(None, max_length=64)) -> dict:
    return music_evolution(user_id=user_id)


# 4) 歌曲工业级 RLHF 标注模板（含预填）
@router.get("/api/music/plus/rlhf", dependencies=[Depends(require_key)])
def music_plus_rlhf(
    q: str = Query(None, max_length=200),
    user_id: str = Query(None, max_length=64),
) -> dict:
    return music_rlhf_annotate(q, user_id=user_id)


# 5) AI 段落解构 + 接歌/混音建议
@router.get("/api/music/plus/deconstruct", dependencies=[Depends(require_key)])
def music_plus_deconstruct(
    q: str = Query(None, max_length=200),
    top_n: int = Query(8, ge=1, le=20),
    user_id: str = Query(None, max_length=64),
) -> dict:
    return deconstruct(q, top_n=top_n, user_id=user_id)


@router.get("/api/music/v2/melody-profile", dependencies=[Depends(require_key)])
def melody_profile_v2(user_id: str = Query(None, max_length=64)) -> dict:
    """返回用户真实曲谱画像。

    数据来自 QQ音乐曲谱图片经本地 qwen2.5vl:3b OMR 识别后的聚合
    （user_melody_profile_v4.json），未识别的记为 None，绝不造假。
    """
    path = user_path(user_id, "user_melody_profile_v4.json")
    if not os.path.exists(path):
        # 若该用户有 OMR 任务在跑，返回「生成中」而非直接报错
        tid = _omr_tasks.get(user_id or "default")
        if tid:
            st = tasks.status(tid)
            if st and st.get("status") in ("pending", "running"):
                return {"status": "generating", "task_id": tid,
                        "progress": st.get("progress")}
        raise HTTPException(409, "曲谱画像尚未生成，请先运行谱子识别")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---- 多用户管理 ---------------------------------------------------------------
class UserCreateReq(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)


@router.get("/api/music/users", dependencies=[Depends(require_key)])
def music_users() -> dict:
    """列出所有音乐用户目录。"""
    return {"users": list_users(), "default": "default"}


@router.post("/api/music/users", dependencies=[Depends(require_key)])
def music_create_user(req: UserCreateReq) -> dict:
    """创建新的音乐用户数据目录。"""
    uid = req.user_id.strip()
    if not uid or "/" in uid or ".." in uid:
        raise HTTPException(400, "非法 user_id")
    d = ensure_user(uid)
    return {"user_id": uid, "dir": d, "created": True}


@router.post("/api/music/v2/backfill-sheets", dependencies=[Depends(require_key)])
def music_backfill_sheets(user_id: str = Query(None, max_length=64)) -> dict:
    from src.music.sheet_fetcher import backfill_sheets
    from src.music.melody import load_sheets
    from src.music.omr_pipeline import run as omr_run
    before = len(load_sheets())
    backfill_sheets()
    after = len(load_sheets())
    # 抓取完曲谱后，自动聚合生成「真·谱子画像」(user_melody_profile_v4.json)
    profile = omr_run(user_id=user_id)
    return {
        "sheets_before": before,
        "sheets_after": after,
        "sheets_added": after - before,
        "melody_profile": {
            "recognized": profile.get("recognized", 0),
            "total_songs": profile.get("total_songs", 0),
        },
    }


@router.post("/api/music/v2/omr", dependencies=[Depends(require_key)])
def music_omr_run(user_id: str = Query(None, max_length=64),
                  retry_missing: bool = Query(False)) -> dict:
    from src.music.omr_pipeline import run as omr_run
    profile = omr_run(user_id=user_id, retry_missing=retry_missing)
    return {
        "recognized": profile.get("recognized", 0),
        "total_songs": profile.get("total_songs", 0),
        "note": profile.get("note", ""),
    }