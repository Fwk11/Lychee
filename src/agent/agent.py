#!/usr/bin/env python3
"""Lychee Agent: LangChain/LangGraph ReAct agent over deterministic tools.

Architecture:
    user instruction (自然语言)
        -> ReAct agent (Ollama qwen2.5:3b, tool-calling)
        -> tools.py (analyze_video / get_report / recommend_music / ...)
        -> deterministic pipelines (OpenCV, rule engines, recommender_v2)

Two-layer robustness (8GB machine, local LLM may be slow/unavailable):
    1. ReAct mode  : LLM decides which tool(s) to call and composes the answer.
    2. Fallback    : if Ollama is unreachable, a keyword router maps the
                     instruction straight to a tool -- degraded but functional.

Interview framing: the *tools are deterministic and auditable*; the LLM is
only an orchestrator. Swap qwen2.5:3b for any bigger model without touching
the data layer.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.agent.tools import (ALL_TOOLS, analyze_video,
                             list_novels, list_reports,
                             list_videos, music_profile,
                             read_novel_chapter,
                             recommend_music, storyboard_novel_chapters)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
AGENT_MODEL = os.environ.get("AGENT_MODEL", "qwen2.5:3b")

SYSTEM_PROMPT = (
    "你是 Lychee，一个本地多模态助手，能调度视频分析、音乐推荐和小说剧场三类工具。"
    "视频类：list_videos / analyze_video / get_report / list_reports；"
    "音乐类：music_profile / recommend_music / import_sheet；"
    "小说类：list_novels / read_novel_chapter / list_storyboards / storyboard_novel_chapters。"
    "根据用户指令选择合适的工具并调用；回答用简洁中文，"
    "直接给出工具结果的要点，不要编造工具没有返回的内容。"
)


def _ollama_alive() -> bool:
    import urllib.request
    try:
        urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=3)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# mode 1: ReAct agent (LangGraph + Ollama tool-calling)
# --------------------------------------------------------------------------

_APP = None


def _build_react_agent():
    global _APP
    if _APP is None:
        from langchain_ollama import ChatOllama
        from langgraph.prebuilt import create_react_agent
        llm = ChatOllama(model=AGENT_MODEL, base_url=OLLAMA_URL,
                         temperature=0.1, num_ctx=4096)
        _APP = create_react_agent(llm, ALL_TOOLS, prompt=SYSTEM_PROMPT)
    return _APP


def run_react(instruction: str, max_steps: int = 6) -> str:
    app = _build_react_agent()
    result = app.invoke(
        {"messages": [("user", instruction)]},
        config={"recursion_limit": max_steps * 2 + 1},
    )
    msgs = result.get("messages", [])
    # last AI message is the final answer
    for m in reversed(msgs):
        if getattr(m, "type", "") == "ai" and m.content:
            return m.content if isinstance(m.content, str) else str(m.content)
    return "(agent 没有产生回答)"


# --------------------------------------------------------------------------
# mode 2: keyword fallback router (no LLM needed)
# --------------------------------------------------------------------------

def run_fallback(instruction: str) -> str:
    text = instruction.lower()
    if any(k in text for k in ["有哪些视频", "视频列表", "list video"]):
        return list_videos.invoke({})
    if any(k in text for k in ["报告", "report"]):
        return list_reports.invoke({})
    if any(k in text for k in ["分析", "视频", "video", "美学", "镜头"]):
        # try to extract a filename-ish token
        for tok in instruction.replace("，", " ").split():
            if tok.lower().endswith((".mp4", ".mov", ".avi", ".mkv")) or tok.startswith("pexels"):
                return analyze_video.invoke({"video_name": tok})
        return list_videos.invoke({}) + "\n(请指定要分析的视频文件名)"
    if any(k in text for k in ["画像", "喜好", "口味", "profile"]):
        return music_profile.invoke({})
    if any(k in text for k in ["音乐", "歌", "推荐", "music", "听"]):
        return recommend_music.invoke({"top_n": 8})
    # novel fallback
    if any(k in text for k in ["小说", "书", "章节", "分镜", "storyboard"]):
        if any(k in text for k in ["分镜", "storyboard"]):
            name = _extract_novel_name(instruction)
            if name:
                return storyboard_novel_chapters.invoke({"name": name, "start": 1, "end": 5})
            return list_novels.invoke({}) + "\n(请指定要生成分镜的小说名)"
        if "第" in instruction and "章" in instruction:
            m = __import__("re").search(r"第\s*(\d+)\s*章", instruction)
            idx = int(m.group(1)) if m else 1
            name = _extract_novel_name(instruction)
            if name:
                return read_novel_chapter.invoke({"name": name, "chapter": idx})
        return list_novels.invoke({})
    return "我可以帮你分析视频、推荐音乐、或处理小说分镜。请说具体需求，例如「有哪些视频」「推荐几首歌」「给完美世界前5章分镜」。"


def _extract_novel_name(instruction: str) -> str | None:
    """从指令里猜小说名：优先匹配 data/novel 下存在的文件名。"""
    import os
    from src.novel.loader import list_novels as _list
    names = {os.path.splitext(n["file"])[0] for n in _list()}
    # 精确匹配
    for n in names:
        if n in instruction:
            return n
    # 常见别名
    aliases = {
        "完美": "完美世界",
        "遮天": "遮天",
        "斗破": "斗破苍穹",
        "凡人": "凡人修仙传",
    }
    for k, v in aliases.items():
        if k in instruction and v in names:
            return v
    return None


# --------------------------------------------------------------------------
# unified entry
# --------------------------------------------------------------------------

class LycheeAgent:
    """Unified entry. Prefers ReAct (LLM orchestration); falls back to
    keyword routing when Ollama is down."""

    def run(self, instruction: str, force_fallback: bool = False) -> str:
        if not force_fallback and _ollama_alive():
            try:
                return run_react(instruction)
            except Exception as e:
                print(f"[agent] ReAct 失败({e})，降级为关键词路由", file=sys.stderr)
        return run_fallback(instruction)

    # legacy API kept for scheduled jobs / older callers
    def recommend_music(self) -> str:
        return recommend_music.invoke({"top_n": 10})

    weekly_digest = recommend_music

    def analyze_video(self, input_dir: str | None = None) -> str:
        return run_fallback("分析视频")


def main():
    import argparse
    p = argparse.ArgumentParser(description="Lychee Agent (ReAct)")
    p.add_argument("instruction", nargs="*", help="自然语言指令")
    p.add_argument("--fallback", action="store_true", help="跳过LLM，用关键词路由")
    p.add_argument("--interactive", "-i", action="store_true", help="交互模式")
    args = p.parse_args()

    agent = LycheeAgent()
    if args.interactive:
        print("Lychee 交互模式（exit 退出）")
        while True:
            try:
                line = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if line in ("exit", "quit", "q"):
                break
            if line:
                print(agent.run(line, force_fallback=args.fallback))
    elif args.instruction:
        print(agent.run(" ".join(args.instruction), force_fallback=args.fallback))
    else:
        p.print_help()


if __name__ == "__main__":
    main()
