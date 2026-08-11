"""Lychee API 路由：Label Studio 桥接（每使用者自配凭据 + 一键推送预标注）。

  GET  /api/label-studio/config        读取当前 LS 配置（是否已配置）
  PUT  /api/label-studio/config        设置 url + api_key（存本地，不进代码/git）
  GET  /api/label-studio/schema        返回当前统一标注模板（工业级 RLHF 维度树）
  POST /api/label-studio/push          把某视频标注推成 LS 任务，返回编辑器地址
  POST /api/label-studio/push-dataset  把整个数据集的视频批量推成一个 LS 项目
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from src.api import tasks
from src.api.config import ROOT, settings
from src.api.routers.video import _load_report  # 复用报告加载
from src.api.security import rate_limit, require_key
from src.label_studio.client import (
    get_config, push_annotation, set_config,
)
from src.video.annotation_schema import load_schema

log = logging.getLogger("lychee")

router = APIRouter()


class LSConfigReq(BaseModel):
    url: str = Field(default="", max_length=500)
    api_key: str = Field(min_length=1, max_length=500)


class LSPushReq(BaseModel):
    video_id: str = Field(min_length=1, max_length=200)
    ls_url: str | None = Field(default=None, max_length=500)
    ls_key: str | None = Field(default=None, max_length=500)
    dataset: str | None = Field(default=None, max_length=200)


class LSPushDatasetReq(BaseModel):
    dataset: str = Field(min_length=1, max_length=200)
    video_ids: list[str] = Field(default_factory=list)  # 空=该数据集下全部已分析视频
    ls_url: str | None = Field(default=None, max_length=500)
    ls_key: str | None = Field(default=None, max_length=500)


@router.get("/api/label-studio/config", dependencies=[Depends(require_key)])
def ls_config() -> dict:
    return get_config()


@router.put("/api/label-studio/config",
            dependencies=[Depends(require_key), Depends(rate_limit)])
def ls_set_config(req: LSConfigReq) -> dict:
    return set_config(req.url, req.api_key)


def _push(instruction: dict) -> dict:
    video_id = instruction["video_id"]
    report = _load_report(video_id)
    if not report:
        return {"ok": False, "error": "报告不存在，请先标注该视频"}
    # 源视频地址：本后端 /source 端点（带 Lychee key，供 LS 浏览器端加载）
    lychee_key = (settings.api_keys or [""])[0]
    source_url = (f"http://127.0.0.1:8000/api/reports/{video_id}/source"
                  f"?key={lychee_key}")
    return push_annotation(video_id, report, source_url,
                           ls_url=instruction.get("ls_url"),
                           ls_key=instruction.get("ls_key"),
                           dataset=instruction.get("dataset"))


@router.get("/api/label-studio/schema", dependencies=[Depends(require_key)])
def ls_schema(dataset: str | None = None) -> dict:
    """返回当前统一标注模板（工业级 RLHF 维度树），前端可展示/编辑。"""
    return load_schema(dataset)


@router.post("/api/label-studio/push",
             dependencies=[Depends(require_key), Depends(rate_limit)])
def ls_push(req: LSPushReq) -> dict:
    """同步推送（LS 建项目+导任务通常 <3s），直接返回编辑器地址。"""
    return _push({"video_id": req.video_id, "ls_url": req.ls_url,
                  "ls_key": req.ls_key, "dataset": req.dataset})


@router.post("/api/label-studio/push-dataset",
             dependencies=[Depends(require_key), Depends(rate_limit)])
def ls_push_dataset(req: LSPushDatasetReq) -> dict:
    """把整个数据集的视频批量推成「一个」LS 项目（共享统一模板）。

    video_ids 为空时，推送 output/reports 下所有已分析视频；否则只推指定列表。
    返回项目地址 + 每条视频的推送结果汇总。
    """
    import glob, json as _json

    video_ids = list(req.video_ids)
    if not video_ids:
        rep_dir = os.path.join(ROOT, "output", "reports")
        for f in glob.glob(os.path.join(rep_dir, "*.json")):
            try:
                d = _json.load(open(f, encoding="utf-8"))
                vid = d.get("video_id")
                if vid:
                    video_ids.append(vid)
            except Exception:
                pass

    pushed, failed = [], []
    project_id = None
    editor_url = None
    for vid in video_ids:
        r = _push({"video_id": vid, "ls_url": req.ls_url,
                   "ls_key": req.ls_key, "dataset": req.dataset})
        if r.get("ok"):
            pushed.append(vid)
            project_id = r.get("project_id")
            editor_url = r.get("editor_url")
        else:
            failed.append({"video_id": vid, "error": r.get("error")})
    return {
        "ok": True,
        "dataset": req.dataset,
        "project_id": project_id,
        "editor_url": editor_url,
        "pushed": pushed,
        "failed": failed,
        "pushed_count": len(pushed),
        "failed_count": len(failed),
    }
