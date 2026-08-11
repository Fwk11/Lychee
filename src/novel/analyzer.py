# -*- coding: utf-8 -*-
"""小说内容分析：逐章摘要 + 人物档案。

使用本地 Ollama qwen2.5:3b（8GB Mac 可跑）。分析结果缓存到
 data/novel/<name>_analysis.json，支持断点续跑。
"""
from __future__ import annotations

import json
import os
import re
import threading
from typing import Optional

import requests

from .loader import load_novel, NOVEL_DIR

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
MODEL = os.environ.get("NOVEL_LLM", "qwen2.5:3b")

# 多实例负载均衡：设置 OLLAMA_URLS（逗号分隔）后，每个 _chat 请求按 round-robin
# 分发到不同本地 Ollama 实例，配合并发调用即可把多实例用满、突破单实例串行排队瓶颈。
OLLAMA_URLS = [u.strip() for u in os.environ.get("OLLAMA_URLS", "").split(",") if u.strip()]
if not OLLAMA_URLS:
    OLLAMA_URLS = [OLLAMA_URL]
_URL_LOCK = threading.Lock()
_URL_IDX = 0

def _next_ollama_url() -> str:
    global _URL_IDX
    with _URL_LOCK:
        u = OLLAMA_URLS[_URL_IDX % len(OLLAMA_URLS)]
        _URL_IDX += 1
    return u

MAX_CHAPTER_CHARS = 6000   # 单章送入 LLM 的最大字符（3b 小模型上下文有限）
MAX_PROFILE_CHARS = 25000  # 全书简介采样字符（3b 上下文有限）


def _chat(prompt: str, system: str = "", num_predict: int = 600,
          json_mode: bool = False) -> str:
    payload = {
        "model": MODEL,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": num_predict},
        "messages": ([{"role": "system", "content": system}] if system else [])
        + [{"role": "user", "content": prompt}],
    }
    if json_mode:
        payload["format"] = "json"
    r = requests.post(f"{_next_ollama_url()}/api/chat", json=payload, timeout=600)
    r.raise_for_status()
    return r.json()["message"]["content"].strip()


def _cache_path(novel_name: str) -> str:
    safe = re.sub(r"[^\w\u4e00-\u9fff-]", "_", novel_name)
    return os.path.join(NOVEL_DIR, f"{safe}_analysis.json")


def _load_cache(novel_name: str) -> dict:
    p = _cache_path(novel_name)
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return {"name": novel_name, "chapter_summaries": {}, "characters": None,
            "overview": None}


def _save_cache(novel_name: str, data: dict) -> None:
    os.makedirs(NOVEL_DIR, exist_ok=True)
    json.dump(data, open(_cache_path(novel_name), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


def summarize_chapter(ch_title: str, ch_text: str) -> str:
    """单章 150-250 字情节摘要。"""
    body = ch_text[:MAX_CHAPTER_CHARS]
    prompt = (
        f"下面是小说的一个章节《{ch_title}》，请用 150-250 字概括本章情节，"
        f"必须点出出场的主要人物和关键事件，用中文平实叙述，不要评论：\n\n{body}"
    )
    return _chat(prompt, system="你是一名中文小说编辑，擅长精准概括情节。")


def analyze_novel(path: str, name: Optional[str] = None,
                  max_chapters: Optional[int] = None,
                  progress_cb=None) -> dict:
    """完整分析：逐章摘要（带缓存断点续跑）→ 全书概览 → 人物档案。

    max_chapters: 原型阶段可只分析前 N 章。
    返回 analysis dict 并落盘。
    """
    novel = load_novel(path, name=name)
    data = _load_cache(novel.name)
    chapters = novel.chapters[:max_chapters] if max_chapters else novel.chapters

    # 1) 逐章摘要（断点续跑）
    for ch in chapters:
        key = str(ch.index)
        if key in data["chapter_summaries"]:
            continue
        summ = summarize_chapter(ch.title, ch.text)
        data["chapter_summaries"][key] = {"title": ch.title, "summary": summ}
        _save_cache(novel.name, data)
        if progress_cb:
            progress_cb(ch.index, len(chapters))

    done_summaries = [
        f"{v['title']}：{v['summary']}"
        for k, v in sorted(data["chapter_summaries"].items(), key=lambda x: int(x[0]))
    ]
    joined = "\n\n".join(done_summaries)[:8000]

    # 2) 全书/已读部分概览
    data["overview"] = _chat(
        f"以下是小说《{novel.name}》各章节的摘要，请写一段 300 字左右的整体故事概述，"
        f"交代主线剧情走向：\n\n{joined}",
        system="你是一名中文小说编辑。", num_predict=800)
    _save_cache(novel.name, data)

    # 3) 人物档案（主角+配角，故事线与性格）
    raw = _chat(
        "根据以下章节摘要，列出小说的主要人物（主角与重要配角，最多8人）。"
        "对每个人物给出：姓名、身份/角色定位、性格特点（3-5个词）、"
        "在故事中的经历概述（80字内）。严格输出 JSON 数组，格式："
        '[{"name":"..","role":"..","personality":"..","story":".."}]，'
        f"不要输出其他内容：\n\n{joined}",
        system="你是一名中文小说编辑，只输出合法 JSON。", num_predict=1200)
    try:
        m = re.search(r"\[.*\]", raw, re.S)
        data["characters"] = json.loads(m.group(0)) if m else []
    except Exception:
        data["characters"] = [{"name": "解析失败", "role": "", "personality": "",
                               "story": raw[:200]}]
    data["n_chapters_analyzed"] = len(data["chapter_summaries"])
    data["n_chapters_total"] = novel.n_chapters
    _save_cache(novel.name, data)
    return data


def _extract_profile(raw: str) -> Optional[dict]:
    """从模型输出里提取 JSON profile；失败返回 None。"""
    text = raw.strip()
    # 优先取 ```json ... ``` 代码块
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    else:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            text = m.group(0)
        else:
            return None
    try:
        profile = json.loads(text)
    except Exception:
        return None
    if not isinstance(profile, dict):
        return None
    # 只要核心字段有一个有效，就算成功
    core = ("main_characters", "world_building", "style_tags", "themes")
    if not any(profile.get(k) for k in core):
        return None
    return profile


def analyze_book_profile(path: str, name: Optional[str] = None,
                         progress_cb=None) -> dict:
    """全书解析简介：剧情梗概、主要人物、世界观/背景、风格标签、主题。

    结果写入 analysis cache 的 book_profile 字段，不依赖逐章分析。
    """
    novel = load_novel(path, name=name)
    data = _load_cache(novel.name)

    # 用章节正文采样，跳过目录/作者话/上架通知等过短片段
    _NOISE_RE = re.compile(r"上架|求订阅|月票|作者|单章|通知|推书|感言|完本|封推|请假")
    sample_parts = []
    chars = 0
    for ch in novel.chapters:
        if ch.n_chars < 300 or _NOISE_RE.search(ch.title):
            continue
        sample_parts.append(f"《{ch.title}》\n{ch.text}")
        chars += ch.n_chars
        if chars >= MAX_PROFILE_CHARS:
            break
    sample = "\n\n".join(sample_parts)[:MAX_PROFILE_CHARS]
    if not sample and novel.chapters:
        # 兜底：取最长的一章
        sample = max(novel.chapters, key=lambda c: c.n_chars).text[:MAX_PROFILE_CHARS]

    if progress_cb:
        progress_cb(1, 3)

    example = (
        '{\n'
        '  "synopsis": "200-300字剧情梗概",\n'
        '  "main_characters": [{"name":"姓名","role":"身份/定位","personality":"性格标签","story":"50字内经历"}],\n'
        '  "world_building": "世界观、时代背景、主要场景（100字内）",\n'
        '  "style_tags": ["标签1","标签2","标签3"],\n'
        '  "themes": ["主题1","主题2"]\n'
        '}'
    )

    prompt = (
        f"你是一位中文小说编辑。请根据下面小说正文片段，为《{novel.name}》生成一份全书解析简介。\n"
        "要求：\n"
        "1. 只输出 JSON，不要续写原文，不要加任何解释；\n"
        "2. 把 JSON 放在 ```json ... ``` 代码块里；\n"
        f"3. 严格按以下格式：\n{example}\n\n"
        f"正文片段：\n{sample}"
    )
    raw = _chat(prompt, system="你是一名中文小说编辑，只输出合法 JSON。",
                num_predict=1800, json_mode=True)

    if progress_cb:
        progress_cb(2, 3)

    profile = _extract_profile(raw)

    # Fallback：用首章较短文本再试一次（小模型对长上下文可能失控）
    if profile is None and novel.chapters:
        fallback_sample = "\n\n".join(
            c.text[:4000] for c in novel.chapters[:3] if c.n_chars > 80
        )[:12000]
        if fallback_sample:
            raw2 = _chat(
                f"请把下面小说内容总结成指定 JSON，只输出 JSON 代码块，不要续写：\n{example}\n\n"
                f"小说内容：\n{fallback_sample}",
                system="你是一名中文小说编辑，只输出合法 JSON。",
                num_predict=1800, json_mode=True,
            )
            profile = _extract_profile(raw2)

    if profile is None:
        profile = {
            "synopsis": raw[:400] or "解析失败",
            "main_characters": [],
            "world_building": "",
            "style_tags": [],
            "themes": [],
            "parse_error": raw[:300]
        }

    for k in ("synopsis", "main_characters", "world_building", "style_tags", "themes"):
        if k not in profile:
            profile[k] = "" if k in ("synopsis", "world_building") else []

    data["book_profile"] = profile
    data["n_chapters_total"] = novel.n_chapters
    _save_cache(novel.name, data)

    if progress_cb:
        progress_cb(3, 3)
    return data


def get_analysis(novel_name: str) -> Optional[dict]:
    p = _cache_path(novel_name)
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return None
    return None

