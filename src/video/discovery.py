"""对话式视频发现：把一句自然语言变成一批本地视频文件。

给 ``POST /api/video/chat`` 用。纯本地文件系统逻辑，不依赖 FastAPI，
也不调用大模型——3B 小模型做 open-ended 意图决策不可靠，这里全部走规则。

安全边界：只允许扫描 ``data/raw/videos`` 与 ``~/project`` 两棵树，
任何越界路径（含 ``..``）一律返回 None。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from src.api.config import ROOT, VIDEOS_DIR

# 认得的视频容器格式
VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv", ".webm")

_HOME = str(Path.home())

# 单次扫描的目录深度与项目个数上限，避免在大目录上卡死
_MAX_DEPTH = 4
_MAX_PROJECTS = 5


def allowed_roots() -> list[Path]:
    """允许扫描的根目录：应用视频库 + 用户 ~/project。"""
    roots = [Path(VIDEOS_DIR)]
    project_root = Path(_HOME) / "project"
    if project_root.is_dir():
        roots.append(project_root)
    return roots


def _is_under_allowed(path: Path) -> bool:
    path = path.resolve()
    for root in allowed_roots():
        try:
            path.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


def resolve_chat_path(raw: str) -> Path | None:
    """把用户写在对话框里的路径解析成安全绝对路径，越界返回 None。"""
    raw = raw.strip().strip("'\"")
    if raw.startswith("~"):
        raw = os.path.join(_HOME, raw[1:].lstrip("/"))
    p = Path(raw)
    if not p.is_absolute():
        # 相对路径先按 ~/project 解释，再按项目根解释
        candidates = [Path(_HOME) / "project" / raw, Path(ROOT) / raw]
        p = next((c for c in candidates if c.exists()), candidates[0])
    if ".." in p.parts or not _is_under_allowed(p):
        return None
    return p.resolve()


def extract_paths(message: str) -> list[str]:
    """从自然语言里抠出疑似路径 / 目录别名。"""
    found: list[str] = []
    for pat in (r"((?:~|/Users|/home|[A-Za-z]:)[^\s，。！？\n]*)",
                r"(project/[^\s，。！？\n]+)"):
        for m in re.finditer(pat, message):
            candidate = m.group(1).rstrip("，。！？")
            if candidate and candidate not in found:
                found.append(candidate)
    if any(k in message.lower() for k in ("videos", "视频目录", "视频文件夹")):
        if VIDEOS_DIR not in found:
            found.append(VIDEOS_DIR)
    return found


def extract_video_keywords(message: str) -> list[str]:
    """抠出用户点名的文件名关键词（引号内的完整文件名优先）。"""
    quoted = re.findall(r"['\"]([^'\"]+?\.(?:mp4|mov|avi|mkv|webm))['\"]", message, re.I)
    named = re.findall(r"(?:视频|文件|file)\s*[叫 named]*\s*['\"]?([^\s'\"，。！？]+)['\"]?", message)
    return [x for x in quoted + named if x]


def _default_search_dirs() -> list[Path]:
    """用户没指路径时的默认扫描范围：视频库 + ~/project 下含视频的项目。"""
    dirs = [Path(VIDEOS_DIR)]
    project_root = Path(_HOME) / "project"
    if not project_root.is_dir():
        return dirs
    hits: list[Path] = []
    for d in project_root.iterdir():
        if not d.is_dir():
            continue
        for _, _, files in os.walk(d):
            if any(f.lower().endswith(VIDEO_EXTS) for f in files):
                hits.append(d)
                break
        if len(hits) >= _MAX_PROJECTS:
            break
    return dirs + hits


def _entry(path: Path, project: str) -> dict:
    return {"path": str(path), "name": path.name, "video_id": path.stem, "project": project}


def discover_videos(message: str) -> tuple[list[dict], list[str]]:
    """按聊天内容发现本地视频。

    Returns:
        ``(videos, search_dirs)``；videos 每项含 path / name / video_id / project。
    """
    paths = extract_paths(message)
    if paths:
        search_dirs = [p for p in (resolve_chat_path(r) for r in paths) if p and p.exists()]
    else:
        search_dirs = _default_search_dirs()

    keywords = [k.lower() for k in extract_video_keywords(message)]

    videos: list[dict] = []
    seen: set[str] = set()
    for d in search_dirs:
        # 用户直接点名一个视频文件
        if d.is_file():
            if d.suffix.lower() in VIDEO_EXTS and str(d) not in seen:
                seen.add(str(d))
                videos.append(_entry(d, d.parent.name))
            continue
        if not d.is_dir():
            continue
        for root, _, files in os.walk(d):
            if len(Path(root).relative_to(d).parts) > _MAX_DEPTH:
                continue
            for f in files:
                if not f.lower().endswith(VIDEO_EXTS):
                    continue
                if keywords and not any(k in f.lower() for k in keywords):
                    continue
                fp = Path(root) / f
                abs_path = str(fp)
                # 跳过死符号链接（历史事故留下过一批）
                if abs_path in seen or not os.path.exists(abs_path):
                    continue
                seen.add(abs_path)
                videos.append(_entry(fp, d.name))
    return videos, [str(d) for d in search_dirs]
