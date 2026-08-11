#!/usr/bin/env python3
"""Label Studio 桥接：把 Lychee 的视频标注结果一键推送成可复核的 LS 任务。

设计要点（呼应「凭据按使用者配置，而非全局一把 key」）：
  * LS 的 url / api_key 存于本地 ``data/label_studio.json``，由使用者自己设置一次。
  * 推送时优先用请求里带Override，否则读本地配置；谁配置谁生效，互不覆盖。
  * 推送 = 自动建项目（用本报告生成的 labeling config）+ 导入带预标注的视频任务，
    所以视频一进 LS 时间轴就已经画好运镜/景别/构图/光影/问题，直接可复核/改。

Label Studio 1.23 API 约定：
  POST /api/projects/              建项目（body 含 label_config）
  POST /api/projects/{id}/import   同步导入任务（JSON 数组；任务可带 annotations 预标注）
  编辑器地址：{url}/projects/{id}/data/{task_id}
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.error
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONFIG_PATH = os.path.join(ROOT, "data", "label_studio.json")

DEFAULT_URL = "http://127.0.0.1:8080"

# 项目命名固定前缀 + 视频 id，便于幂等复用同一项目（不重复建）
PROJECT_TITLE_PREFIX = "Lychee 视频标注 · "


def _project_title(video_id: str) -> str:
    """LS 项目标题上限 50 字符；超长时前缀 + 截断 + 短哈希，保证唯一且幂等。"""
    full = PROJECT_TITLE_PREFIX + video_id
    if len(full) <= 50:
        return full
    h = hashlib.md5(video_id.encode("utf-8")).hexdigest()[:8]
    room = 50 - len(PROJECT_TITLE_PREFIX) - 1 - 8
    return PROJECT_TITLE_PREFIX + video_id[:room] + "-" + h


# 数据集项目命名前缀（一个数据集 = 一个 LS 项目，共享统一模板）
DATASET_TITLE_PREFIX = "Lychee 数据集 · "


def _dataset_title(dataset: str) -> str:
    """数据集项目标题上限 50 字符；超长时前缀 + 截断 + 短哈希。"""
    full = DATASET_TITLE_PREFIX + dataset
    if len(full) <= 50:
        return full
    h = hashlib.md5(dataset.encode("utf-8")).hexdigest()[:8]
    room = 50 - len(DATASET_TITLE_PREFIX) - 1 - 8
    return DATASET_TITLE_PREFIX + dataset[:room] + "-" + h


def _read_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            return json.load(open(CONFIG_PATH, encoding="utf-8"))
        except Exception:
            pass
    return {}


def _write_config(cfg: dict) -> None:
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def get_config() -> dict:
    """返回当前使用者的 LS 配置（不含明文 key 的展示由前端决定）。"""
    cfg = _read_config()
    return {
        "url": cfg.get("url") or DEFAULT_URL,
        "configured": bool(cfg.get("api_key")),
    }


def set_config(url: str, api_key: str) -> dict:
    """使用者设置自己的 LS 地址 + key（只存本地，不进 git/代码）。"""
    cfg = _read_config()
    cfg["url"] = (url or DEFAULT_URL).rstrip("/")
    cfg["api_key"] = api_key
    _write_config(cfg)
    return get_config()


def _ls_call(url: str, api_key: str, method: str, path: str, body=None,
            auth: bool = True, scheme: str = "Token"):
    """最小化的 LS REST 调用（不引第三方依赖，保持 8GB 单机干净）。

    auth=False 时（如 token 刷新接口）不带 Authorization 头。
    scheme: "Token"（Account & Settings 里的不透明 API 令牌）或
            "Bearer"（JWT，如访问/刷新令牌，LS 1.23 的 API 认 Bearer 前缀）。
    """
    headers = {"Content-Type": "application/json"}
    if auth and api_key:
        headers["Authorization"] = f"{scheme} {api_key}"
    req = urllib.request.Request(
        url.rstrip("/") + path,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:300]
        return e.code, {"error": f"LS HTTP {e.code}: {detail}"}
    except Exception as e:  # 连不上 / 超时
        return 0, {"error": f"无法连接 Label Studio（{e}）"}


def _jwt_payload(token: str):
    """尽力解析 JWT 中段 payload；不是 JWT / 解析失败则返回 None。"""
    try:
        seg = token.split(".")[1]
        seg += "=" * (-len(seg) % 4)
        return json.loads(base64.urlsafe_b64decode(seg))
    except Exception:
        return None


def _is_jwt(token: str) -> bool:
    return _jwt_payload(token) is not None


def _resolve_access_token(url: str, key: str) -> str:
    """如果用户存的是 refresh（刷新）令牌，自动换成 access（访问）令牌再调用 API。

    这样用户只需在设置里贴一次刷新令牌（几乎不过期），推送时自动换取短期访问令牌，
    既兼容「贴访问令牌」也兼容「贴刷新令牌」两种情况。
    """
    payload = _jwt_payload(key)
    if payload and payload.get("token_type") == "refresh":
        st, resp = _ls_call(url, None, "POST", "/api/token/refresh/",
                            {"refresh": key}, auth=False)
        if st == 200 and resp.get("access"):
            return resp["access"]
        # 刷新失败则原样返回，交给后续真实调用暴露错误
    return key


def push_annotation(video_id: str, report: dict, lychee_source_url: str,
                    ls_url: str = None, ls_key: str = None,
                    dataset: str | None = None) -> dict:
    """把单条视频标注推成 LS 任务（带统一模板预标注），返回编辑器地址。

    lychee_source_url: 本后端提供源视频的地址（需带 Lychee key，LS 浏览器端加载用）。
    ls_url / ls_key: 可选覆盖；不传则用本地存储的当前使用者配置。
    dataset: 若提供，则推到「该数据集」对应的 LS 项目（一个数据集=一个项目，
             所有视频共享同一套统一模板）；不提供则退化为「每条视频一个项目」。
    """
    from src.video.annotation_schema import load_schema

    cfg = _read_config()
    url = (ls_url or cfg.get("url") or DEFAULT_URL).rstrip("/")
    key = ls_key or cfg.get("api_key")
    if not key:
        return {"ok": False,
                "error": "尚未配置 Label Studio API Key，请先在设置里填写（别人各自填自己的，互不影响）"}

    # 若存的是刷新令牌，先换成访问令牌（访问令牌 5 分钟过期，推送前现换最稳）
    key = _resolve_access_token(url, key)
    # JWT（访问/刷新令牌）用 Bearer 前缀；Account & Settings 里的不透明 API 令牌用 Token
    scheme = "Bearer" if _is_jwt(key) else "Token"

    # 统一标注 schema（按数据集可覆盖）：配置与时间轴 ranges 都用它
    schema = load_schema(dataset)
    fps = report.get("fps") or schema.get("default_framerate", 30)

    from src.video.annotation_export import to_label_studio, to_label_studio_config

    # 局部封装：自动带上当前 url / key / 鉴权方案（Token 或 Bearer）
    def _call(method, path, body=None):
        return _ls_call(url, key, method, path, body, scheme=scheme)

    # 1) 项目（按数据集或按视频幂等复用）
    title = _dataset_title(dataset) if dataset else _project_title(video_id)
    st, projects = _call("GET", "/api/projects/")
    if st != 200:
        return {"ok": False, "error": projects.get("error", "获取项目列表失败")}
    existing = next((p for p in projects.get("results", []) if p.get("title") == title), None)
    if existing:
        pid = existing["id"]
    else:
        config_xml = to_label_studio_config(schema, fps=fps)
        st, proj = _call("POST", "/api/projects/",
                         {"title": title, "label_config": config_xml})
        if st != 201:
            return {"ok": False, "error": (proj.get("error") or "建项目失败") + f"（HTTP {st}）"}
        pid = proj["id"]

    # 同一视频只在项目里保留一个任务：导入前清掉已有的（避免重复堆积，也便于重推覆盖）
    st, _tasks = _call("GET", f"/api/projects/{pid}/tasks/?page_size=100")
    if st == 200:
        _items = _tasks if isinstance(_tasks, list) else (_tasks.get("tasks") or [])
        for _t in _items:
            if isinstance(_t, dict) and video_id in str((_t.get("data") or {}).get("video_url", "")):
                _call("DELETE", f"/api/tasks/{_t['id']}/")

    # 2) 导入带统一模板预标注的任务
    ls_task = to_label_studio(report, video_url=lychee_source_url,
                              schema=schema, fps=fps)
    # LS 导入接口期望任务数组；annotations 内放预标注即导入即带时间轴
    st, imp = _call("POST", f"/api/projects/{pid}/import",
                    [{"data": ls_task["data"], "annotations": ls_task["annotations"]}])
    if st not in (200, 201):
        return {"ok": False, "error": (imp.get("error") or "导入任务失败") + f"（HTTP {st}）"}

    # 取刚导入的 task id（import 返回只有计数，没有 id；任务接口返回的是裸 list）
    st, tasks = _call("GET", f"/api/projects/{pid}/tasks/?page_size=100")
    tid = None
    if st == 200:
        items = tasks if isinstance(tasks, list) else (tasks.get("tasks") or [])
        # 多次推送会产生重复任务，按 data.video_url 含 video_id 且 id 最大者锁定本次
        matched = [t for t in items if isinstance(t, dict)
                   and video_id in str((t.get("data") or {}).get("video_url", ""))]
        if matched:
            tid = max(matched, key=lambda t: t.get("id", 0)).get("id")
        elif items:
            tid = items[0].get("id")

    # LS 1.23 编辑器正确路由：/projects/{pid}/data?task={tid}（/data/{tid} 会 404）
    editor_url = f"{url}/projects/{pid}/data?task={tid}" if tid else f"{url}/projects/{pid}"
    return {
        "ok": True,
        "project_id": pid,
        "task_id": tid,
        "editor_url": editor_url,
        "created": existing is None,
    }
