#!/usr/bin/env python3
"""Lychee web service —— 应用装配层。

本文件只负责「组装」，不写任何业务逻辑：

    日志 → FastAPI 实例 → 中间件（安全头 / CORS）→ 全局异常处理
         → 挂载各领域路由 → 挂载前端静态资源

业务端点全部拆分在 ``src/api/routers/`` 下，按领域分文件：

    system.py  健康检查、任务状态、配置等通用端点
    video.py   视频美学分析、镜头标注、报告管理、对话式标注
    music.py   听歌口味画像、每周新歌推荐、曲谱旋律画像
    novel.py   小说书架、阅读、国漫分镜、连续剧
    agent.py   自然语言 Agent 入口

安全：/api/* 全量 API-key 鉴权、按 IP 限流、路径穿越消毒、安全响应头，
默认只监听 127.0.0.1。

启动：
    cd lychee
    uvicorn src.api.server:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import json
import logging
import os
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

# 项目根目录入 sys.path，保证 `src.*` 绝对导入在任何启动方式下都可用
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from src.api.config import LOGS_DIR, WEB_DIR, settings  # noqa: E402
from src.api.security import SecurityHeadersMiddleware  # noqa: E402
from src.api.routers import agent, label_studio, music, novel, system, video  # noqa: E402

# ---- 日志 --------------------------------------------------------------------
os.makedirs(LOGS_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[logging.FileHandler(os.path.join(LOGS_DIR, "app.log"), encoding="utf-8"),
              logging.StreamHandler()],
)
log = logging.getLogger("lychee")

# ---- 应用与中间件 --------------------------------------------------------------
# 关闭 docs/redoc/openapi：本服务只给自家前端用，不对外暴露接口清单
app = FastAPI(title="lychee", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SecurityHeadersMiddleware)

# 允许跨域：公开站（CloudStudio）前端跨域调用本机隧道后端
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
def _global_exception_handler(_request, exc: Exception):
    """未捕获异常统一返回 JSON，避免前端收到 HTML 500。"""
    log.exception("unhandled exception")
    return Response(
        content=json.dumps({"detail": f"Internal Server Error: {exc}"}, ensure_ascii=False),
        status_code=500,
        media_type="application/json",
    )


# ---- 路由挂载 -----------------------------------------------------------------
app.include_router(system.router)
app.include_router(video.router)
app.include_router(music.router)
app.include_router(novel.router)
app.include_router(agent.router)
app.include_router(label_studio.router)


# ---- 前端 --------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """单页应用入口。"""
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


if __name__ == "__main__":
    import uvicorn
    _key = settings.api_keys[0] if settings.api_keys else "(未配置)"
    print(f"\n  Lychee 已启动: http://{settings.host}:{settings.port}/?key={_key}\n")
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning")
