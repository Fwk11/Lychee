"""Lychee API 路由：小说剧场（书架 / 阅读 / 国漫分镜）。

端点分三组：
  书架   list / upload / search / download / books/{name}(DELETE)
  阅读   {name}/chapters、{name}/chapters/{idx}、analyze、{name}/analysis
  分镜   storyboard、storyboard/batch、{name}/storyboard/{rng}

长任务（分析 / 分镜）一律走 ``tasks.submit`` 返回 task_id，
前端用 ``GET /api/tasks/{task_id}`` 轮询。
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src.api import tasks
from src.api.security import rate_limit, require_key
from src.novel import analyzer as novel_analyzer
from src.novel import parsers as novel_parsers
from src.novel import qidian as novel_qidian
from src.novel import storyboard as novel_storyboard
from src.novel.loader import NOVEL_DIR, list_novels, load_novel

log = logging.getLogger("lychee")

router = APIRouter()

_UPLOAD_MAX = 50 * 1024 * 1024  # 单本书上限 50MB


def _safe_name(raw: str) -> str:
    """书名消毒：只保留中英文、数字、下划线与连字符，防路径穿越。"""
    return re.sub(r"[^\w\u4e00-\u9fff-]", "_", raw).strip("_") or "upload"


def _novel_path(name: str) -> str:
    """书名 → 书架上的 txt 路径；不存在直接 404。"""
    path = os.path.join(NOVEL_DIR, f"{_safe_name(name)}.txt")
    if not os.path.exists(path):
        # 兼容书名本身带特殊字符、落盘时未消毒的老数据
        legacy = os.path.join(NOVEL_DIR, f"{name}.txt")
        if os.path.exists(legacy):
            return legacy
        raise HTTPException(404, "小说不存在")
    return path


# ---- 书架 ---------------------------------------------------------------------
@router.get("/api/novel/list", dependencies=[Depends(require_key)])
def novel_list() -> dict:
    """列出书架上的小说，并标注分析进度与是否已有连续剧方案。"""
    novels = list_novels()
    for n in novels:
        name = os.path.splitext(n["file"])[0]
        ana = novel_analyzer.get_analysis(name)
        n["analyzed"] = bool(ana)
        n["n_chapters_analyzed"] = len(ana.get("chapter_summaries", {})) if ana else 0
        series = os.path.join(novel_storyboard.SERIES_DIR, f"{_safe_name(name)}_series.json")
        n["has_series"] = os.path.exists(series)
    return {"novels": novels}


@router.post("/api/novel/upload", dependencies=[Depends(require_key), Depends(rate_limit)])
def novel_upload(file: UploadFile = File(...)) -> dict:
    """上传本地书籍，统一转成 UTF-8 txt 存进书架。

    支持 txt / docx / epub / pdf / mobi / azw3（后两者需本机装 Calibre）。
    """
    raw = file.filename or "upload.txt"
    ext = Path(raw).suffix.lower()
    if ext not in novel_parsers.SUPPORTED_EXTS:
        raise HTTPException(400, f"不支持的格式 {ext or '(无扩展名)'}，"
                                 "请上传 txt / docx / epub / pdf / mobi / azw3")

    data = file.file.read(_UPLOAD_MAX + 1)
    if len(data) > _UPLOAD_MAX:
        raise HTTPException(413, "文件超过 50MB")

    safe = _safe_name(Path(raw).stem)
    # 解析器按扩展名分派，需要真实文件路径，先落到临时目录
    with tempfile.TemporaryDirectory() as td:
        tmp = os.path.join(td, f"{safe}{ext}")
        with open(tmp, "wb") as f:
            f.write(data)
        try:
            text, _, _ = novel_parsers.parse_book_file(tmp)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            raise HTTPException(500, f"解析失败：{e}")

    if not text.strip():
        raise HTTPException(422, "未能从文件中提取到正文，建议先转成 txt 再上传")

    os.makedirs(NOVEL_DIR, exist_ok=True)
    out_path = os.path.join(NOVEL_DIR, f"{safe}.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    return {"name": safe, "path": out_path, "size": len(text), "message": f"已上传《{safe}》"}


class NovelSearchReq(BaseModel):
    title: str = Field(min_length=1, max_length=100)


@router.post("/api/novel/search", dependencies=[Depends(require_key), Depends(rate_limit)])
def novel_search(req: NovelSearchReq) -> dict:
    """按书名在起点搜索。搜不到时返回空列表+引导文案，不抛错。"""
    try:
        results = novel_qidian.search_qidian(req.title, max_results=5)
    except Exception as e:
        return {"results": [], "message": f"搜索失败：{e}。可直接上传文件或粘贴书籍页链接。"}
    if not results:
        return {"results": [], "message": "未找到结果。可直接上传文件或粘贴书籍页链接。"}
    return {"results": results}


class NovelDownloadReq(BaseModel):
    url: str = ""      # 起点书籍页网址
    title: str = ""    # 留空则由页面/正文推断
    author: str = ""   # 粘贴正文模式下的作者
    text: str = ""     # 粘贴正文模式：直接给出章节正文


@router.post("/api/novel/download", dependencies=[Depends(require_key), Depends(rate_limit)])
def novel_download(req: NovelDownloadReq) -> dict:
    """入库一本书：起点书籍页 URL，或直接粘贴正文（起点反爬时的兜底）。"""
    url = (req.url or "").strip()
    try:
        if req.text and not url:
            return novel_qidian.fetch_qidian_by_paste(req.title, req.author, req.text)
        return novel_qidian.fetch_qidian_book(url, title=req.title or None, max_chapters=200)
    except Exception as e:
        raise HTTPException(502, f"下载失败：{e}")


@router.delete("/api/novel/books/{name}", dependencies=[Depends(require_key)])
def novel_delete(name: str) -> dict:
    """删除整本书及其全部缓存（分析 / 音频 / 分镜 / 衔接状态）。"""
    safe = _safe_name(name)
    candidates = [os.path.join(NOVEL_DIR, f"{name}.txt"),
                  os.path.join(NOVEL_DIR, f"{safe}.txt"),
                  os.path.join(NOVEL_DIR, f"{safe}_analysis.json")]
    # 子目录里的批次产物：{书名}_ch1-5.mp3 / .json、continuity/{书名}.json
    for pat in (f"{safe}_ch*", f"_{safe}_ch*", f"{safe}_series.json", f"{safe}.json"):
        for sub in ("audio", "storyboard", "series", "continuity"):
            candidates += glob.glob(os.path.join(NOVEL_DIR, sub, pat))

    removed = []
    for c in sorted(set(candidates)):
        if os.path.isfile(c):
            try:
                os.remove(c)
                removed.append(os.path.basename(c))
            except OSError:
                pass
    return {"removed": removed, "count": len(removed)}


# ---- 阅读与分析 ----------------------------------------------------------------
@router.get("/api/novel/{name}/chapters", dependencies=[Depends(require_key)])
def novel_chapters(name: str) -> dict:
    """章节目录。"""
    return load_novel(_novel_path(name), name=name).summary_dict()


@router.get("/api/novel/{name}/chapters/{idx}", dependencies=[Depends(require_key)])
def novel_chapter_text(name: str, idx: int) -> dict:
    """单章正文（在线阅读器用）。"""
    novel = load_novel(_novel_path(name), name=name)
    if idx < 1 or idx > novel.n_chapters:
        raise HTTPException(404, "章节不存在")
    ch = novel.chapters[idx - 1]
    return {"index": ch.index, "title": ch.title, "text": ch.text}


class NovelAnalyzeReq(BaseModel):
    name: str
    max_chapters: int = Field(5, ge=1, le=120)


@router.post("/api/novel/analyze", dependencies=[Depends(require_key), Depends(rate_limit)])
def novel_analyze(req: NovelAnalyzeReq) -> dict:
    """后台分析前 N 章（逐章摘要 + 人物档案，本地 LLM，可断点续跑）。"""
    task_id = tasks.submit(novel_analyzer.analyze_novel, _novel_path(req.name),
                           name=req.name, max_chapters=req.max_chapters)
    return {"task_id": task_id, "message": f"已开始分析《{req.name}》前 {req.max_chapters} 章"}


@router.get("/api/novel/{name}/analysis", dependencies=[Depends(require_key)])
def novel_analysis(name: str) -> dict:
    """读取分析结果（概览 + 逐章摘要 + 人物档案）。"""
    ana = novel_analyzer.get_analysis(name)
    if not ana:
        raise HTTPException(404, "尚未分析")
    return ana


# ---- 分镜 ---------------------------------------------------------------------
class StoryboardReq(BaseModel):
    name: str
    chapter: int = Field(1, ge=1, le=200)
    n_shots: int = Field(6, ge=3, le=12)


@router.post("/api/novel/storyboard", dependencies=[Depends(require_key), Depends(rate_limit)])
def novel_storyboard_chapter(req: StoryboardReq) -> dict:
    """单章分镜（本地 LLM，后台任务）。"""
    task_id = tasks.submit(novel_storyboard.storyboard_chapter, _novel_path(req.name),
                           chapter_index=req.chapter, novel_name=req.name,
                           n_shots=req.n_shots)
    return {"task_id": task_id, "message": f"已开始生成《{req.name}》第{req.chapter}章分镜"}


class StoryboardBatchReq(BaseModel):
    name: str
    start: int = Field(1, ge=1)
    end: int = Field(5, ge=1)
    n_shots: int | None = None


@router.post("/api/novel/storyboard/batch", dependencies=[Depends(require_key), Depends(rate_limit)])
def novel_storyboard_batch(req: StoryboardBatchReq) -> dict:
    """批量分镜：第 start~end 章生成连贯分镜，自动衔接上一批的时空与人物。"""
    task_id = tasks.submit(novel_storyboard.storyboard_batch, _novel_path(req.name),
                           req.start, req.end, novel_name=req.name, n_shots=req.n_shots)
    return {"task_id": task_id,
            "message": f"已开始生成《{req.name}》第{req.start}-{req.end}章分镜"}


@router.get("/api/novel/{name}/storyboards", dependencies=[Depends(require_key)])
def novel_storyboard_list(name: str) -> dict:
    """列出该书已生成的分镜，供前端标注「已生成」并直接读缓存，不重复跑 LLM。

    返回 ``ranges``（如 ``["1", "3", "1-5"]``）与 ``chapters``（章号 → 该章可读的
    分镜 key，单章缓存优先于批量区间）。
    """
    d = os.path.join(NOVEL_DIR, "storyboard")
    prefix = f"{_safe_name(name)}_ch"
    ranges: list[str] = []
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if fn.startswith(prefix) and fn.endswith(".json"):
                rng = fn[len(prefix):-len(".json")]
                if re.fullmatch(r"\d+(-\d+)?", rng):
                    ranges.append(rng)
    chapters: dict[str, str] = {}
    # 先铺区间，再用单章覆盖，保证单章缓存优先
    for rng in sorted(ranges, key=lambda r: "-" not in r):
        if "-" in rng:
            a, b = (int(x) for x in rng.split("-", 1))
            if a <= b and b - a < 500:
                for i in range(a, b + 1):
                    chapters[str(i)] = rng
        else:
            chapters[rng] = rng
    return {"ranges": ranges, "chapters": chapters}


@router.get("/api/novel/{name}/storyboard/{rng}", dependencies=[Depends(require_key)])
def novel_storyboard_get(name: str, rng: str) -> dict:
    """读取已生成的分镜（rng 形如 ``5`` 或 ``1-5``）。"""
    safe = _safe_name(f"{name}_ch{rng}")
    p = os.path.join(NOVEL_DIR, "storyboard", f"{safe}.json")
    if not os.path.exists(p):
        raise HTTPException(404, "分镜尚未生成")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# ---- 整本角色库预生成 --------------------------------------------------------
class BuildBibleReq(BaseModel):
    name: str
    max_chapters: int = Field(80, ge=5, le=400)


def _build_bible_task(name: str, max_chapters: int):
    """后台任务：一次性建全本角色形象库并落盘，顺便把角色卡冻结成投喂锚。"""
    from src.novel import get_analysis
    novel = load_novel(_novel_path(name), name=name)
    analysis = get_analysis(name)
    bible = novel_storyboard.build_full_bible(novel, analysis=analysis, max_chapters=max_chapters)
    # 建完角色库立刻把角色卡冻结（文字锚），后续可直接投喂视频模型角色参考
    try:
        from src.novel.storyboard import consistency as cons
        cons.freeze_from_bible(name)
    except Exception:
        pass
    return bible


@router.post("/api/novel/build-bible", dependencies=[Depends(require_key), Depends(rate_limit)])
def api_build_bible(req: BuildBibleReq) -> dict:
    """一次性建全本角色形象库（后台任务）：让整本书几百个角色都有稳定形象设定，跨章不分裂。"""
    task_id = tasks.submit(_build_bible_task, req.name, req.max_chapters)
    return {"task_id": task_id,
            "message": f"已开始建《{req.name}》全本角色库（后台，跑完自动缓存，可在分镜时复用）"}


# ---- 冻结角色卡（视觉锚） + 一致性守门 ------------------------------------
class CastLockReq(BaseModel):
    name: str                       # 小说名
    character: str                  # 角色名
    image_path: str = ""            # 定稿参考图（磁盘路径，置于 cast/ 下最佳）
    desc: str = ""                  # 锁定文字描述（可取自 bible）


@router.post("/api/novel/cast/lock", dependencies=[Depends(require_key)])
def api_cast_lock(req: CastLockReq) -> dict:
    """冻结一张角色参考卡：图片路径 + 锁定描述，供后续每章复用（视觉锚）。"""
    from src.novel.storyboard import consistency as cons
    cast = cons.lock_cast_card(req.name, req.character, req.image_path, req.desc)
    return {"cast": cast, "message": f"已冻结角色卡：{req.character}"}


@router.get("/api/novel/{name}/cast", dependencies=[Depends(require_key)])
def api_cast_get(name: str) -> dict:
    """读取该书已冻结的角色卡。"""
    from src.novel.storyboard import consistency as cons
    return {"cast": cons.load_cast(name)}


class CastFreezeReq(BaseModel):
    name: str
    characters: list[str] = []       # 空 = 全书角色


@router.post("/api/novel/cast/freeze-from-bible", dependencies=[Depends(require_key)])
def api_cast_freeze(req: CastFreezeReq) -> dict:
    """一键从 bible 冻结全书角色卡（文字锚），作为投喂视频模型角色参考的统一依据。

    角色卡 = 委托策略下的人物一致性锚：image 留空，待你从 pilot 首帧挑图或指定
    参考图后，用 /cast/lock 把图片补上。
    """
    from src.novel.storyboard import consistency as cons
    res = cons.freeze_from_bible(req.name, req.characters or None)
    return {"frozen": res.get("frozen", []),
            "message": f"已冻结 {len(res.get('frozen', []))} 个角色卡（共 {len(res.get('cast', {}))} 个）"}


class ConsistencyCheckReq(BaseModel):
    name: str
    rng: str = "1"                  # 章节区间，如 "1" 或 "1-5"
    frame_dir: str = ""             # 可选：本章各镜代表帧目录（触发视觉校验）


@router.post("/api/novel/consistency/check", dependencies=[Depends(require_key), Depends(rate_limit)])
def api_consistency_check(req: ConsistencyCheckReq) -> dict:
    """跑跨章一致性守门：描述锁定 + 风格锁 +（有帧时）视觉比对。

    返回综合分与判定：达标入库 / 不达标打回重生成。
    """
    from src.novel.storyboard import consistency as cons
    try:
        report = cons.check_chapter_range(req.name, req.rng,
                                          frame_dir=req.frame_dir or None)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return {"report": report}
