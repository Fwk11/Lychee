# -*- coding: utf-8 -*-
"""跨章 / 跨批衔接状态：让 AI 视频大模型逐镜拼接时时空不跳戏。"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

from ..analyzer import _chat
from ._common import CONTINUITY_DIR


def _continuity_path(novel_name: str) -> str:
    safe = re.sub(r"[^\w\u4e00-\u9fff-]", "_", novel_name)
    return os.path.join(CONTINUITY_DIR, f"{safe}.json")


def _load_continuity(novel_name: str) -> Optional[dict]:
    """读取上一批（通常上一组 5 章）留下的衔接状态；没有则返回 None。"""
    p = _continuity_path(novel_name)
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return None
    return None


def _save_continuity(novel_name: str, state: dict) -> None:
    os.makedirs(CONTINUITY_DIR, exist_ok=True)
    json.dump(state, open(_continuity_path(novel_name), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


def _prior_block(state: dict) -> str:
    """把上一批的衔接状态编成'前文衔接'指令，塞进本批第一段的骨架 prompt。"""
    if not state:
        return ""
    chars = state.get("characters") or {}
    char_lines = "\n".join(
        f"  - {name}：所在地「{c.get('where','')}」｜状态「{c.get('state','')}」｜手持「{c.get('props','')}」"
        for name, c in chars.items()
    ) or "  （暂无）"
    threads = "\n".join(f"  - {t}" for t in (state.get("plot_threads") or [])) or "  （暂无）"
    return (
        "【前文衔接（本批必须从此处平滑接续，禁止凭空换场景、禁止让角色状态无故跳变）】\n"
        f"前文结束于：第{state.get('chapter_end','?')}章之后\n"
        f"当前所在：{state.get('scene','?')}\n"
        f"时间线：{state.get('time','?')}\n"
        f"剧情截至：{state.get('summary','?')}\n"
        f"角色现状：\n{char_lines}\n"
        f"未收束的剧情线：\n{threads}\n"
        f"上一组最后一个镜头画面（用于视觉接戏）：{state.get('last_shot','?')}\n"
    )


def _merge_bible(prior: dict, new: dict) -> dict:
    """跨批合并角色形象库：prior 的形象优先（保证同一角色每批画出来一致），新角色追加。"""
    merged = dict(new or {})
    for k, v in (prior or {}).items():
        merged[k] = v
    return merged


def _extract_continuity(novel_name: str, chapters_text: str, shots: list,
                        bible: dict, start: int, end: int) -> dict:
    """生成结束后抽一份'衔接下一阶段'的状态包，供下一批平滑接续（一次 LLM 调用）。"""
    last = shots[-1] if shots else {}
    last_shot = ((last.get("arrangement") or "") + "；" + (last.get("action") or "")).strip("； ").strip()[:200]
    ctx = (
        f"近几章原文节选：\n{chapters_text[:2000]}\n\n"
        f"本组最后一个镜头：场景={last.get('scene','')}，角色={last.get('characters','')}，"
        f"画面={last_shot}\n"
        f"本组已建立角色形象库：{', '.join(list((bible or {}).keys()))}"
    )
    prompt = (
        "你是连续剧编剧助理。根据上面几章内容与刚生成的分镜结尾，提炼出【衔接下一阶段】"
        "所需的最小状态包，让下一批分镜能平滑接戏。\n"
        "严格输出一个 JSON 对象，字段：\n"
        "- summary: 60 字内剧情截至概述（'刚才发生了什么'的口径）\n"
        "- scene: 当前故事所在的具体地点\n"
        "- time: 时间线/时段（如'清晨→正午'或'闭关三年后'）\n"
        "- characters: 对象，键=角色名，值={where:所在地, state:身体/情绪状态, props:手持或身上关键道具}\n"
        "- plot_threads: 数组，未收束的悬念/任务/冲突，每条一句话\n"
        "- last_shot: 一句话描述本组最后一个镜头画面，供下一阶段视觉接戏\n"
        "只输出 JSON，不要其他文字。"
    )
    raw = _chat(prompt + "\n\n" + ctx,
                system="你是影视编剧助理，只输出合法JSON。",
                num_predict=700, json_mode=True)
    try:
        m = re.search(r"\{.*\}", raw, re.S)
        st = json.loads(m.group(0)) if m else {}
    except Exception:
        st = {}
    st["novel"] = novel_name
    st["chapter_end"] = end
    st["summary"] = st.get("summary") or ""
    st["scene"] = st.get("scene") or (last.get("scene") or "")
    st["time"] = st.get("time") or ""
    st["characters"] = st.get("characters") or {}
    st["plot_threads"] = st.get("plot_threads") or []
    st["last_shot"] = st.get("last_shot") or last_shot[:120]
    return st


def _chapter_tail_state(prev_shots: list, prev_ch_index: int) -> dict:
    """用上一章结尾镜头构造一段轻量衔接状态，喂给下一章做视觉接戏（不额外调 LLM）。"""
    last = prev_shots[-1] if prev_shots else {}
    return {
        "novel": "",
        "chapter_end": prev_ch_index,
        "summary": "",
        "scene": last.get("scene", ""),
        "time": "",
        "characters": {},
        "plot_threads": [],
        "last_shot": ((last.get("arrangement") or "") + "；" + (last.get("action") or "")).strip("； ").strip()[:200],
    }
