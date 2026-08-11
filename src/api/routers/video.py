"""Lychee API 路由：视频（分析 / 报告 / 镜头标注 / 导出 / 删除）。

端点分五组：
  视频库   videos、videos/{name}/stream、video/upload、videos/upload
  分析     analyze、video/chat（对话式，发现逻辑在 src/video/discovery.py）
  报告     reports、reports/{id}、reports/{id}/shots/{sid}/frame
  标注     annotate/{id}（GET 取模板/已存，POST 保存 D1-D6）
  导出删除 reports/{id}/annotations、reports/{id}(DELETE)、reports/batch-delete

耗时的分析一律 ``tasks.submit`` 后台跑，前端轮询 ``/api/tasks/{task_id}``。
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import List
from urllib.parse import quote

import cv2
import mimetypes
from fastapi import (APIRouter, Depends, File, HTTPException, Query,
                     Request, UploadFile)
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from src.api import tasks
from src.api.config import (ANNOTATIONS_DIR, FRAMES_DIR, OUTPUT_DIR,
                            REPORTS_DIR, VIDEOS_DIR, settings)
from src.api.security import (rate_limit, require_key, resolve_in,
                              safe_video_id, safe_video_name)
from src.video.aesthetics_pipeline import run_pipeline
from src.video.annotation_export import (to_annotation_json, to_csv,
                                            to_csv_batch,
                                            to_label_studio,
                                            to_label_studio_config)
from src.video.discovery import VIDEO_EXTS, allowed_roots, discover_videos

log = logging.getLogger("lychee")

router = APIRouter()

_UPLOAD_MAX = 300 * 1024 * 1024   # 单个视频上限 300MB
_MAX_AUTO_TASKS = 20              # 对话式一次最多自动启动的分析任务数（8GB 机器保护）
_TRASH_DIR = os.path.join(OUTPUT_DIR, ".trash")


# ---- 报告读写 -----------------------------------------------------------------
def _report_path(video_id: str) -> str:
    return resolve_in(REPORTS_DIR, f"{safe_video_id(video_id)}.json")


def _load_report(video_id: str) -> dict | None:
    path = _report_path(video_id)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---- 分析 ---------------------------------------------------------------------
def _analyze(video_name: str, fast: bool) -> dict:
    """分析视频库里的一个文件。"""
    path = resolve_in(VIDEOS_DIR, safe_video_name(video_name))
    return _analyze_path(path, fast, display_name=video_name)


def _analyze_path(video_path: str, fast: bool, display_name: str | None = None) -> dict:
    """分析任意已鉴权的绝对路径视频，报告落到 REPORTS_DIR。

    video_id 统一取「父目录名_文件名」，跨目录重名也不会互相覆盖。
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"视频不存在: {video_path}")
    name = display_name or os.path.basename(video_path)
    p = Path(video_path)
    video_id = safe_video_id(f"{p.parent.name}_{p.stem}".replace(" ", "_"))

    log.info("开始分析 %s (fast=%s, video_id=%s)", name, fast, video_id)
    result = run_pipeline(video_path, with_caption=not fast)
    result["video_id"] = video_id      # 覆盖成稳定 id
    result["source"] = name
    result["_path"] = video_path        # 外部目录视频回查抽帧要用

    out = _report_path(video_id)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log.info("分析完成 %s -> %s", name, out)
    return {"video_id": video_id, "shot_count": result["shot_count"], "cached": False}


# ---- 视频库 -------------------------------------------------------------------
# 视频库目录名，用来给 video_id 加前缀（如 videos_xxx）。所有端点必须一致，
# 否则前端拿到的 id 对不上报告文件，批量删除会 404。
_LIB_PREFIX = Path(VIDEOS_DIR).name


def _video_id_from_name(name: str) -> str:
    """视频库文件名 → 稳定 video_id，与 _analyze_path 的规则保持一致。"""
    return safe_video_id(f"{_LIB_PREFIX}_{os.path.splitext(name)[0]}".replace(" ", "_"))


def _list_videos() -> list[str]:
    """视频库文件名列表（跳过死符号链接）。"""
    if not os.path.isdir(VIDEOS_DIR):
        return []
    return [f for f in sorted(os.listdir(VIDEOS_DIR))
            if f.lower().endswith(VIDEO_EXTS[:4])
            and os.path.exists(os.path.join(VIDEOS_DIR, f))]


@router.get("/api/videos", dependencies=[Depends(require_key), Depends(rate_limit)])
def list_videos() -> dict:
    """视频库清单，带体积与是否已标注。"""
    items = []
    for name in _list_videos():
        vid = _video_id_from_name(name)
        items.append({
            "name": name,
            "video_id": vid,
            "size_mb": round(os.path.getsize(os.path.join(VIDEOS_DIR, name)) / 1e6, 1),
            "analyzed": os.path.exists(_report_path(vid)),
        })
    return {"videos": items}


_CHUNK = 1024 * 1024  # 视频流分块大小 1MB


def _content_disposition(filename: str | None) -> str | None:
    """生成可含中文文件名的 Content-Disposition（RFC 5987）。

    HTTP 头值必须能用 latin-1 编码，直接放中文会触发 UnicodeEncodeError。
    这里对 ASCII 文件名用普通 filename="..."；含非 ASCII 时额外追加
    filename*=UTF-8''percent-encoded，兼顾旧浏览器与 Label Studio。
    """
    if not filename:
        return None
    base = os.path.basename(filename)
    ascii_name = base.encode("ascii", "ignore").decode().strip()
    if ascii_name == base:
        return f'inline; filename="{base}"'
    encoded = quote(base, safe="")
    # latin-1 安全 fallback：如果只剩扩展名或空，则用通用名
    fallback = ascii_name if ascii_name and not ascii_name.startswith(".") else "video.mp4"
    if "." in base and "." not in fallback:
        ext = os.path.splitext(base)[1]
        if ext and ext.encode("ascii", "ignore").decode() == ext:
            fallback += ext
    return f'inline; filename="{fallback}"; filename*=UTF-8\'{encoded}'


def _stream_video_response(path: str, request: Request,
                            filename: str | None = None) -> Response:
    """Range 感知的视频流响应。

    支持 ``206 Partial Content`` + ``Content-Range`` + ``Accept-Ranges``，
    让浏览器 / Label Studio 拖时间轴可分段 seek，而非被迫整段加载后再播放。
    同时回显 CORS 头，满足 LS 跨域 crossOrigin 取帧的需求。
    """
    size = os.path.getsize(path)
    mime = mimetypes.guess_type(path)[0] or "video/mp4"
    origin = request.headers.get("origin")

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": mime,
        "Access-Control-Allow-Origin": origin or "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "*",
        "Cross-Origin-Resource-Policy": "cross-origin",
    }
    cd = _content_disposition(filename)
    if cd:
        headers["Content-Disposition"] = cd

    range_hdr = request.headers.get("range")
    start, end = 0, size - 1
    status = 200

    if range_hdr:
        m = re.match(r"bytes=(\d*)-(\d*)", range_hdr)
        if m:
            gs, ge = m.group(1), m.group(2)
            if gs:
                start = max(0, min(int(gs), size - 1))
            if ge:
                end = max(start, min(int(ge), size - 1))
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            headers["Content-Length"] = str(end - start + 1)
        else:
            headers["Content-Length"] = str(size)
    else:
        headers["Content-Length"] = str(size)

    if request.method in ("HEAD", "OPTIONS"):
        return Response(status_code=200, headers=headers)

    def _iter():
        with open(path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = f.read(min(_CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(_iter(), status_code=status, headers=headers, media_type=mime)


@router.api_route("/api/videos/{name}/stream", methods=["GET", "HEAD", "OPTIONS"],
                  dependencies=[Depends(require_key)])
def stream_video(name: str, request: Request) -> Response:
    """回放视频库里的原片（支持 Range 分段播放）。"""
    path = resolve_in(VIDEOS_DIR, safe_video_name(name))
    if not os.path.exists(path):
        raise HTTPException(404, "视频不存在")
    return _stream_video_response(path, request)


# ---- 上传 ---------------------------------------------------------------------
def _save_upload(file: UploadFile, fast: bool) -> dict:
    """保存单个上传视频并启动标注（单文件与批量上传共用）。"""
    name = safe_video_name(file.filename or "upload.mp4")
    if not name.lower().endswith(VIDEO_EXTS):
        raise HTTPException(400, f"仅支持视频文件: {'/'.join(VIDEO_EXTS)}")

    os.makedirs(VIDEOS_DIR, exist_ok=True)
    base, ext = os.path.splitext(name)
    dest = os.path.join(VIDEOS_DIR, name)
    i = 1
    while os.path.exists(dest):                 # 重名自动加序号
        dest = os.path.join(VIDEOS_DIR, f"{base}_{i}{ext}")
        i += 1

    size = 0
    try:
        with open(dest, "wb") as f:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > _UPLOAD_MAX:
                    f.close()
                    os.remove(dest)
                    raise HTTPException(413, "文件过大（>300MB），请压缩或裁剪后再上传")
                f.write(chunk)
    finally:
        file.file.close()

    saved = os.path.basename(dest)
    log.info("上传视频 %s (%.1fMB) -> 启动标注", saved, size / 1e6)
    return {"status": "started",
            "task_id": tasks.submit(_analyze, saved, fast),
            "video_id": _video_id_from_name(saved),
            "name": saved,
            "size_mb": round(size / 1e6, 1)}


@router.post("/api/video/upload", dependencies=[Depends(require_key), Depends(rate_limit)])
def upload_video(file: UploadFile = File(...), fast: bool = Query(False)) -> dict:
    """上传单个视频并自动标注。"""
    return _save_upload(file, fast)


@router.post("/api/videos/upload", dependencies=[Depends(require_key), Depends(rate_limit)])
def upload_videos(files: List[UploadFile] = File(...), fast: bool = Query(False)) -> dict:
    """批量上传视频。单个失败不影响其余，逐条返回结果。"""
    results = []
    for file in files:
        try:
            results.append(_save_upload(file, fast))
        except HTTPException as e:
            results.append({"status": "error", "name": file.filename, "detail": e.detail})
        except Exception as e:
            results.append({"status": "error", "name": file.filename, "detail": str(e)})
    return {"results": results}


class AnalyzeReq(BaseModel):
    video: str = Field(min_length=1, max_length=200)
    fast: bool = False       # 跳过 VLM 描述，只做镜头切分与色彩/运镜
    refresh: bool = False    # 忽略已有报告，强制重跑


@router.post("/api/analyze", dependencies=[Depends(require_key), Depends(rate_limit)])
def analyze(req: AnalyzeReq) -> dict:
    """分析视频库里的一个视频；已有报告且未要求刷新时直接返回缓存。"""
    name = safe_video_name(req.video)
    vid = _video_id_from_name(name)
    if not req.refresh and os.path.exists(_report_path(vid)):
        r = _load_report(vid) or {}
        return {"status": "cached", "video_id": vid, "shot_count": r.get("shot_count")}
    return {"status": "started",
            "task_id": tasks.submit(_analyze, name, req.fast),
            "video_id": vid}


# ---- 对话式标注 ----------------------------------------------------------------
class VideoChatReq(BaseModel):
    message: str = Field(min_length=1, max_length=800)
    fast: bool = False
    refresh: bool = False


@router.post("/api/video/chat", dependencies=[Depends(require_key), Depends(rate_limit)])
def video_chat(req: VideoChatReq) -> dict:
    """对话式标注：一句话描述要标哪些视频，后端自动发现并开跑。"""
    msg = req.message
    fast = req.fast or any(k in msg for k in ("快速", "fast", "跳过VLM"))
    refresh = req.refresh or any(k in msg for k in ("重新", "refresh", "再来一次"))

    videos, dirs = discover_videos(msg)
    if not videos:
        scanned = ", ".join(dirs) or "默认视频目录与 project 下的项目"
        return {"reply": f"没在这些目录里找到视频：{scanned}。可以写具体路径或文件名再试。",
                "found": [], "started": [], "cached": [], "searched_dirs": dirs}

    started, cached, queued = [], [], []
    for v in videos:
        stable_id = safe_video_id(f"{v['project']}_{Path(v['path']).stem}".replace(" ", "_"))
        v["video_id"] = stable_id
        if not refresh and os.path.exists(_report_path(stable_id)):
            cached.append(v)
        elif len(started) < _MAX_AUTO_TASKS:
            started.append({**v, "task_id": tasks.submit(_analyze_path, v["path"], fast),
                            "fast": fast})
        else:
            queued.append(v)

    lines = [f"已识别到 {len(videos)} 个视频（来自 {', '.join(dirs)}）："]
    lines += [f"• {v['name']}（{v['project']}）" for v in videos[:10]]
    if len(videos) > 10:
        lines.append(f"… 还有 {len(videos) - 10} 个")
    if started:
        lines.append(f"\n已启动 {len(started)} 个标注任务{'（快速模式）' if fast else ''}。")
    if queued:
        lines.append(f"\n还有 {len(queued)} 个未启动，可指定更具体的文件名再标注。")
    if cached:
        lines.append(f"\n{len(cached)} 个已标注过，{'已强制重跑' if refresh else '直接用缓存'}。")

    return {"reply": "\n".join(lines), "found": videos, "started": started,
            "cached": cached, "searched_dirs": dirs}


# ---- 报告 ---------------------------------------------------------------------
@router.get("/api/reports", dependencies=[Depends(require_key)])
def list_reports() -> dict:
    """已有报告清单（前端左侧列表用，带原文件名与标注时间）。"""
    items = []
    if os.path.isdir(REPORTS_DIR):
        for f in sorted(os.listdir(REPORTS_DIR)):
            if not f.endswith(".json"):
                continue
            vid = f[:-5]
            r = _load_report(vid) or {}
            mtime = os.path.getmtime(os.path.join(REPORTS_DIR, f))
            items.append({
                "video_id": vid,
                "name": r.get("source") or vid,
                "shot_count": r.get("shot_count"),
                "duration_sec": r.get("duration_sec"),
                "analyzed_at": datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
            })
    return {"reports": items}


@router.get("/api/reports/{video_id}", dependencies=[Depends(require_key)])
def get_report(video_id: str) -> dict:
    """读取单份报告。"""
    r = _load_report(video_id)
    if r is None:
        raise HTTPException(404, "报告不存在，请先标注")
    return r


def _locate_video_for_frame(report: dict) -> str | None:
    """为抽帧定位源视频：报告记录的原路径 → 视频库 → 允许目录里按文件名反查。

    对话式标注常来自 Desktop / Downloads 等外部目录，这些目录 macOS 可能
    直接拒绝访问，所有文件系统调用都要能吞掉 OSError。
    """
    src_path = report.get("_path") or report.get("source_path")
    try:
        if src_path and os.path.exists(src_path):
            return src_path
    except OSError:
        pass

    source = report.get("source")
    if not source:
        return None
    lib = os.path.join(VIDEOS_DIR, os.path.basename(source))
    try:
        if os.path.exists(lib):
            return lib
    except OSError:
        pass

    target = os.path.basename(source)
    for root in allowed_roots():
        try:
            for dirpath, _, files in os.walk(root):
                if target in files:
                    return os.path.join(dirpath, target)
        except OSError:
            continue
    return None


@router.get("/api/reports/{video_id}/shots/{shot_id}/frame", dependencies=[Depends(require_key)])
def shot_frame(video_id: str, shot_id: str) -> FileResponse:
    """报告卡片用的镜头中间帧 JPEG（抽一次后落盘缓存）。"""
    safe_video_id(video_id)
    if not re.fullmatch(r"[A-Za-z0-9_]+", shot_id):
        raise HTTPException(400, "非法 shot id")

    frame_path = resolve_in(FRAMES_DIR, video_id, f"{shot_id}.jpg")
    if not os.path.exists(frame_path):
        r = _load_report(video_id)
        if r is None:
            raise HTTPException(404, "报告不存在，请先标注")
        shot = next((s for s in r["shots"] if s["shot_id"] == shot_id), None)
        if shot is None:
            raise HTTPException(404, "镜头不存在")
        video_path = _locate_video_for_frame(r)
        if not video_path:
            raise HTTPException(404, "找不到源视频，无法抽帧")

        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        mid = (shot["start_sec"] + shot["end_sec"]) / 2.0
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(mid * fps)))
        ok, frame = cap.read()
        cap.release()
        if not ok:
            raise HTTPException(500, "抽帧失败")
        os.makedirs(os.path.dirname(frame_path), exist_ok=True)
        cv2.imwrite(frame_path, frame)
    return FileResponse(frame_path, media_type="image/jpeg")


# ---- 镜头标注 D1-D6 -------------------------------------------------------------
class ShotAnnotation(BaseModel):
    shot_id: str
    shot_scale: str | None = None          # D1 景别
    camera_move: str | None = None         # D2 运镜
    composition: str | None = None         # D3 构图
    mood: str | None = None                # D4 情绪
    aesthetic_score: float | None = None   # D5 审美评分 0-10
    compliance: str | None = None          # D6 合规（合规/需复核/违规）
    note: str | None = None


class AnnotationPayload(BaseModel):
    video_id: str
    shots: list[ShotAnnotation]


def _annotation_path(video_id: str) -> str:
    os.makedirs(ANNOTATIONS_DIR, exist_ok=True)
    return resolve_in(ANNOTATIONS_DIR, f"{safe_video_id(video_id)}.json")


@router.get("/api/annotate/{video_id}", dependencies=[Depends(require_key)])
def get_annotation(video_id: str) -> dict:
    """已有标注；没有就按报告的镜头数返回空白模板。"""
    safe_video_id(video_id)
    path = _annotation_path(video_id)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    r = _load_report(video_id)
    if r is None:
        raise HTTPException(404, "报告不存在，请先标注视频")
    return {"video_id": video_id,
            "shots": [{"shot_id": s["shot_id"], "shot_scale": None, "camera_move": None,
                       "composition": None, "mood": None, "aesthetic_score": None,
                       "compliance": None, "note": None} for s in r["shots"]]}


@router.post("/api/annotate/{video_id}", dependencies=[Depends(require_key), Depends(rate_limit)])
def save_annotation(video_id: str, payload: AnnotationPayload) -> dict:
    """保存标注。只接受报告里真实存在的 shot_id，防注入。"""
    safe_video_id(video_id)
    if payload.video_id != video_id:
        raise HTTPException(400, "video_id 不匹配")
    r = _load_report(video_id)
    if r is None:
        raise HTTPException(404, "报告不存在，请先标注视频")

    valid = {s["shot_id"] for s in r["shots"]}
    cleaned = [s.model_dump() for s in payload.shots if s.shot_id in valid]
    saved = {"video_id": video_id, "shots": cleaned,
             "updated_at": datetime.datetime.now().isoformat(timespec="seconds")}
    with open(_annotation_path(video_id), "w", encoding="utf-8") as f:
        json.dump(saved, f, ensure_ascii=False, indent=2)
    return {"status": "saved", "count": len(cleaned)}


@router.api_route("/api/reports/{video_id}/source", methods=["GET", "HEAD", "OPTIONS"],
                  dependencies=[Depends(require_key)])
def serve_source_video(video_id: str, request: Request):
    """提供原始视频文件下载/播放，供 Label Studio 等外部工具引用。

    视频在浏览器里由 Label Studio 前端（通常端口 8080）跨域拉取，且 LS 的
    <Video> 组件以 crossOrigin 方式加载（需 canvas 取帧做时间轴缩略图），
    因此必须返回 CORS 头，否则浏览器会拦截媒体、画面不显示。同时支持 Range
    分段（206），使 LS 拖时间轴可逐段 seek，而非整段加载后播放。
    """
    report = _load_report(video_id)
    if not report:
        raise HTTPException(404, "报告不存在")
    path = report.get("_path") or _resolve_source_path(video_id, report)
    if not path or not os.path.exists(path):
        raise HTTPException(404, "源视频文件不存在")
    return _stream_video_response(path, request, filename=os.path.basename(path))


@router.get("/api/reports/{video_id}/annotations", dependencies=[Depends(require_key)])
def export_annotations(video_id: str, request: Request,
                       format: str = Query("json", pattern="^(json|csv|label_studio|label_studio_config)$")):
    """导出训练数据：JSON / CSV / Label Studio 标注 JSON / Label Studio 配置 XML。"""
    report = _load_report(video_id)
    if not report:
        raise HTTPException(404, "报告不存在，请先标注该视频")
    if format == "csv":
        return Response(
            content=to_csv(report),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{video_id}_annotations.csv"'},
        )
    if format == "label_studio_config":
        xml = to_label_studio_config()
        cd = _content_disposition(f"{video_id}_label_studio_config.xml")
        headers = {"Content-Type": "application/xml; charset=utf-8"}
        if cd:
            headers["Content-Disposition"] = cd
        return Response(content=xml, headers=headers)
    if format == "label_studio":
        # Label Studio 运行在不同端口，视频 URL 必须是绝对地址，否则浏览器会按 LS 域名解析。
        # <video> 标签无法带自定义 header，所以把 key 挂在 query string 上供 LS 浏览器端加载。
        lychee_key = (settings.api_keys or [""])[0]
        video_url = f"{request.base_url}api/reports/{video_id}/source?key={lychee_key}"
        return to_label_studio(report, video_url=video_url)
    return to_annotation_json(report)


class BatchExportReq(BaseModel):
    video_ids: List[str] = Field(default_factory=list)
    format: str = "csv"


@router.post("/api/reports/annotations/batch", dependencies=[Depends(require_key), Depends(rate_limit)])
def batch_export_annotations(req: BatchExportReq):
    """批量导出多个视频标注为单一文件。video_ids 为空表示导出全部已标注视频。"""
    if req.format not in ("csv", "json"):
        raise HTTPException(400, "format 仅支持 csv / json")
    if req.video_ids:
        ids = req.video_ids
    elif os.path.isdir(REPORTS_DIR):
        ids = sorted(f[:-5] for f in os.listdir(REPORTS_DIR) if f.endswith(".json"))
    else:
        ids = []
    reports = []
    for vid in ids:
        r = _load_report(vid)
        if r:
            r.setdefault("video_id", vid)
            reports.append(r)
    if not reports:
        raise HTTPException(404, "没有可导出的标注记录")
    if req.format == "csv":
        return Response(
            content=to_csv_batch(reports),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="lychee_annotations_batch.csv"'},
        )
    payload = json.dumps({"count": len(reports), "reports": reports}, ensure_ascii=False)
    return Response(
        content=payload,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="lychee_annotations_batch.json"'},
    )


# ---- 删除 ---------------------------------------------------------------------
def _source_path(video_id: str) -> str | None:
    """在视频库里按 video_id 找源文件副本。"""
    for ext in VIDEO_EXTS:
        p = os.path.join(VIDEOS_DIR, video_id + ext)
        if os.path.exists(p):
            return p
    return None


def _resolve_source_path(video_id: str, report: dict | None = None) -> str | None:
    """定位源视频：先视频库，再按报告 source 名在允许目录里反查。"""
    sp = _source_path(video_id)
    if sp:
        return sp
    if report and report.get("source"):
        for root in allowed_roots():
            cand = os.path.join(str(root), report["source"])
            if os.path.exists(cand):
                return cand
    return None


def _trash_file(path: str) -> str:
    """先尝试移进 output/.trash，移不动才真删。"""
    try:
        os.makedirs(_TRASH_DIR, exist_ok=True)
        dest = os.path.join(_TRASH_DIR, f"{int(time.time() * 1000)}_{os.path.basename(path)}")
        shutil.move(path, dest)
        return dest
    except OSError:
        try:
            os.remove(path)
        except OSError:
            pass
        return path


def _delete_report(video_id: str, include_source: bool) -> dict:
    """删一份报告及其衍生物（标注 / 抽帧），可选连源视频副本一起删。"""
    removed = []
    for p in (_report_path(video_id), _annotation_path(video_id)):
        if os.path.exists(p):
            _trash_file(p)
            removed.append(p)
    frames = os.path.join(FRAMES_DIR, video_id)
    if os.path.isdir(frames):
        shutil.rmtree(frames, ignore_errors=True)
        removed.append(frames)
    if include_source:
        src = _resolve_source_path(video_id, _load_report(video_id))
        if src and os.path.exists(src):
            _trash_file(src)
            removed.append(src)
    return {"video_id": video_id, "removed": removed}


@router.delete("/api/reports/{video_id}", dependencies=[Depends(require_key)])
def delete_report(video_id: str, include_source: bool = Query(False)) -> dict:
    """删除单份报告（可选连源视频副本）。"""
    v = safe_video_id(video_id)
    if not os.path.exists(_report_path(v)) and not include_source:
        raise HTTPException(404, "报告不存在")
    return {"removed": _delete_report(v, include_source)["removed"], "video_id": v}


class BatchDeleteReq(BaseModel):
    video_ids: List[str]
    include_source: bool = False


@router.post("/api/reports/batch-delete", dependencies=[Depends(require_key), Depends(rate_limit)])
def batch_delete_reports(req: BatchDeleteReq) -> dict:
    """批量删除报告。"""
    deleted = skipped = 0
    for vid in req.video_ids:
        v = safe_video_id(vid)
        if not os.path.exists(_report_path(v)):
            skipped += 1
            continue
        _delete_report(v, req.include_source)
        deleted += 1
    return {"deleted": deleted, "skipped": skipped}
