#!/usr/bin/env python3
"""Central configuration for the Lychee web service.

Everything is driven by environment variables (optionally from a local
`.env` file in the project root, which is git-ignored). If no API key is
configured, one is generated on first run and persisted to `.env`.
"""
from __future__ import annotations

import os
import secrets

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
ENV_FILE = os.path.join(ROOT, ".env")

VIDEOS_DIR = os.path.join(ROOT, "data", "raw", "videos")
MUSIC_DIR = os.path.join(ROOT, "data", "music")
OUTPUT_DIR = os.path.join(ROOT, "output")
REPORTS_DIR = os.path.join(OUTPUT_DIR, "reports")
ANNOTATIONS_DIR = os.path.join(OUTPUT_DIR, "annotations")
FRAMES_DIR = os.path.join(OUTPUT_DIR, "frames")
LOGS_DIR = os.path.join(ROOT, "logs")
WEB_DIR = os.path.join(ROOT, "web")


def _read_env_file() -> dict[str, str]:
    values: dict[str, str] = {}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    values[k.strip()] = v.strip()
    return values


def _persist_key(key: str) -> None:
    with open(ENV_FILE, "a", encoding="utf-8") as f:
        f.write(f"LYCHEE_API_KEY={key}\n")


class Settings:
    def __init__(self) -> None:
        file_env = _read_env_file()

        def get(name: str, default: str = "") -> str:
            # 兼容旧 AESTHETIC_* 与新 LYCHEE_* 环境变量
            if name.startswith("AESTHETIC_"):
                alt = name.replace("AESTHETIC_", "LYCHEE_", 1)
            elif name.startswith("LYCHEE_"):
                alt = name.replace("LYCHEE_", "AESTHETIC_", 1)
            else:
                alt = name
            return os.environ.get(name) or os.environ.get(alt) or file_env.get(name) or file_env.get(alt) or default

        self.host: str = get("LYCHEE_HOST", "127.0.0.1")  # 默认只监听本机
        self.port: int = int(get("LYCHEE_PORT", "8000"))
        self.rate_limit_per_min: int = int(get("LYCHEE_RATE_LIMIT", "120"))
        self.max_body_bytes: int = int(get("LYCHEE_MAX_BODY", str(2 * 1024 * 1024)))

        self.api_keys: list[str] = self._load_keys(get)
        self.cors_origins: list[str] = self._load_cors(get)


    def _load_keys(self, get) -> list[str]:
        """支持 LYCHEE_API_KEYS（逗号分隔多 key，便于单独吊销）或单 key 回退；兼容旧 AESTHETIC_*。"""
        raw = get("LYCHEE_API_KEYS") or get("LYCHEE_API_KEY")
        if raw:
            keys = [k.strip() for k in raw.split(",") if k.strip()]
            if keys:
                return keys
        key = secrets.token_urlsafe(24)
        try:
            _persist_key(key)
        except OSError:
            pass  # 只读环境下就本次有效
        return [key]

    def _load_cors(self, get) -> list[str]:
        """允许跨域的来源白名单（公开站域名 + 本地）。避免 allow_origins=* 的隐患。"""
        raw = get("LYCHEE_CORS_ORIGINS")
        if raw:
            origins = [o.strip() for o in raw.split(",") if o.strip()]
            if origins:
                return origins
        return [
            "https://13f9c5e197bd47979eab7ef144788288.app.codebuddy.work",
            "http://127.0.0.1:8000",
            "http://localhost:8000",
            # Label Studio 本地前端（媒体走跨域拉取，需 CORS 放行）
            "http://127.0.0.1:8080",
            "http://localhost:8080",
        ]


settings = Settings()
