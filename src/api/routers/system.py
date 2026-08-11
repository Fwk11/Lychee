"""Lychee API 路由：通用端点（健康检查 / 后台任务轮询）。

首页与静态资源由 ``src/api/server.py`` 统一挂载，本模块不重复注册。
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException

from src.api import tasks
from src.api.config import VIDEOS_DIR
from src.api.security import require_key

log = logging.getLogger("lychee")

router = APIRouter()

# 视频库统计只认这几种容器格式
_VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv")


@router.get("/api/health")
def health() -> dict:
    """健康检查：返回服务状态与视频库文件数（免鉴权，供探活使用）。"""
    n = 0
    if os.path.isdir(VIDEOS_DIR):
        n = len([f for f in os.listdir(VIDEOS_DIR) if f.lower().endswith(_VIDEO_EXTS)])
    return {"status": "ok", "videos": n}


@router.get("/api/tasks/{task_id}", dependencies=[Depends(require_key)])
def task_status(task_id: str) -> dict:
    """轮询后台任务状态（分析 / 分镜等长任务统一走这里）。"""
    if not task_id.isalnum() or len(task_id) > 32:
        raise HTTPException(400, "非法 task id")
    t = tasks.status(task_id)
    if t is None:
        raise HTTPException(404, "任务不存在")
    return t
