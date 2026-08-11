#!/usr/bin/env python3
"""Security layer for the web service.

- API key auth (X-API-Key header), constant-time compare
- In-memory sliding-window rate limiter per client IP
- Security response headers middleware
- Filename / URL sanitizers (anti path-traversal, anti SSRF)
"""
from __future__ import annotations

import os
import re
import hmac
import time
import threading
from collections import deque

from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyHeader
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from .config import settings

# ---- API key auth ---------------------------------------------------------
_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_key(request: Request, key: str | None = Depends(_header)) -> str:
    # <img>/<video> 标签无法带自定义 header，允许 ?key= 兜底（本地单用户场景）
    token = key or request.query_params.get("key")
    if not token:
        raise HTTPException(status_code=401, detail="缺失 API Key")
    # 支持多 key（例如本地主 key 与公开站 key 分离），任一匹配即通过
    for valid in settings.api_keys:
        if hmac.compare_digest(token, valid):
            return token
    raise HTTPException(status_code=401, detail="无效或缺失的 API Key")


# ---- rate limiter ---------------------------------------------------------
class RateLimiter:
    """Simple sliding-window limiter, good enough for a local single-user app."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, client: str) -> bool:
        now = time.time()
        window_start = now - 60.0
        with self._lock:
            q = self._hits.setdefault(client, deque())
            while q and q[0] < window_start:
                q.popleft()
            if len(q) >= self.per_minute:
                return False
            q.append(now)
            return True


limiter = RateLimiter(settings.rate_limit_per_min)


async def rate_limit(request: Request) -> None:
    client = request.client.host if request.client else "unknown"
    if not limiter.check(client):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")


# ---- security headers -----------------------------------------------------
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        resp: Response = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        # 静态资源（JS/CSS）不长期缓存，改代码后刷新即生效（配合 index.html 的 ?v= 版本号）
        if request.url.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        # HTML 页面本身也不缓存，避免浏览器用旧 index.html（引用旧的无版本号 JS）
        if request.url.path == "/" or request.url.path.endswith(".html"):
            resp.headers["Cache-Control"] = "no-cache, must-revalidate"
            resp.headers["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' data:; "
                "style-src 'self' 'unsafe-inline'; script-src 'self'; "
                "connect-src 'self'; media-src 'self'"
            )
        return resp


# ---- sanitizers -----------------------------------------------------------
_VIDEO_NAME_RE = re.compile(r"^[\w\-.()（）一-鿿 ]+\.(mp4|mov|avi|mkv)$", re.IGNORECASE)
_VIDEO_ID_RE = re.compile(r"^[\w\-.()（）一-鿿 ]+$")
_QQ_LINK_RE = re.compile(r"^https://[a-z0-9.\-]*y\.qq\.com/[^\s]*$", re.IGNORECASE)
_DISSTID_RE = re.compile(r"^\d{5,12}$")


def safe_video_name(name: str) -> str:
    """Validate a user-supplied video filename; reject path traversal."""
    if not name or not _VIDEO_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="非法的视频文件名")
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail="非法的视频文件名")
    return name


def safe_video_id(video_id: str) -> str:
    if not video_id or not _VIDEO_ID_RE.match(video_id) or ".." in video_id:
        raise HTTPException(status_code=400, detail="非法的视频 id")
    return video_id


def safe_qq_source(src: str) -> str:
    """Accept only a QQ音乐 https link or a bare numeric disstid (anti-SSRF)."""
    src = (src or "").strip()
    if _DISSTID_RE.match(src):
        return src
    if _QQ_LINK_RE.match(src) and len(src) <= 300:
        return src
    raise HTTPException(status_code=400, detail="只接受 y.qq.com 的歌单链接或数字歌单 id")


def resolve_in(base: str, *parts: str) -> str:
    """Join and verify the result stays inside `base` (belt-and-braces)."""
    path = os.path.realpath(os.path.join(base, *parts))
    if not path.startswith(os.path.realpath(base) + os.sep):
        raise HTTPException(status_code=400, detail="非法路径")
    return path
