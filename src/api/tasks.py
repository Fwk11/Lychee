#!/usr/bin/env python3
"""In-process background task registry.

Video analysis with VLM captioning takes tens of seconds per shot, so it
runs off the request thread. Single worker keeps memory pressure low on
an 8GB Mac (opencv + Ollama are heavy). Tasks live only in memory; results
are persisted to output/reports/*.json so nothing is lost on restart.
"""
from __future__ import annotations

import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

_executor = ThreadPoolExecutor(max_workers=3)
_lock = threading.Lock()
_tasks: dict[str, dict[str, Any]] = {}
_current = threading.local()


def report(text: str, cur: int | None = None, total: int | None = None) -> None:
    """任务内部上报进度。3b 生成一章分镜要几分钟，没有进度用户会以为卡死。"""
    task_id = getattr(_current, "task_id", None)
    if not task_id:
        return
    with _lock:
        t = _tasks.get(task_id)
        if t is not None:
            t["progress"] = {"text": text, "cur": cur, "total": total}


def submit(fn: Callable[..., Any], *args, **kwargs) -> str:
    task_id = uuid.uuid4().hex[:12]
    with _lock:
        _tasks[task_id] = {"status": "pending", "result": None, "error": None,
                           "progress": None}

    def _run() -> None:
        _current.task_id = task_id
        with _lock:
            _tasks[task_id]["status"] = "running"
        try:
            result = fn(*args, **kwargs)
            with _lock:
                _tasks[task_id].update(status="done", result=result)
        except Exception as e:  # noqa: BLE001 - surface to caller via status
            traceback.print_exc()
            with _lock:
                _tasks[task_id].update(status="error", error=str(e))

    _executor.submit(_run)
    return task_id


def status(task_id: str) -> dict[str, Any] | None:
    with _lock:
        t = _tasks.get(task_id)
        return dict(t) if t else None
