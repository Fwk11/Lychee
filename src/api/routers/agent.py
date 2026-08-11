"""Lychee API 路由：统一智能助手（自然语言总入口）。

只有一个端点：把用户的一句话丢给 LycheeAgent 后台执行，返回 task_id。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from src.agent.agent import LycheeAgent
from src.api import tasks
from src.api.security import rate_limit, require_key

log = logging.getLogger("lychee")

router = APIRouter()


class AgentChatReq(BaseModel):
    """智能助手自然语言指令。"""
    instruction: str = Field(min_length=1, max_length=2000)


def _module_of(instruction: str, answer: str) -> str | None:
    """从指令与回答里猜出本次任务最相关的模块，用于「在模块中打开」深链。"""
    text = (instruction + " " + answer).lower()
    if any(k in text for k in ["视频", "video", "标注", "分析", "镜头", "美学", "运镜"]):
        return "video"
    if any(k in text for k in ["音乐", "music", "歌", "推荐", "歌单", "听", "谱子"]):
        return "music"
    if any(k in text for k in ["小说", "书", "分镜", "storyboard", "章节", "角色"]):
        return "novel"
    return None


_SUGGESTIONS = {
    "video": ["🎬 重新标注一个视频", "📤 导出到 Label Studio 复核", "🎞 看一段标注报告"],
    "music": ["🤝 识别朋友的歌单并推荐", "🎼 看看我的真·谱子画像", "🔄 换一批新推荐"],
    "novel": ["📖 给下一章做分镜", "🔍 做一致性校验", "🗂 生成角色设定库"],
}


def _run_agent(instruction: str) -> dict:
    """后台执行 Agent 指令，返回结构化结果信封。

    LycheeAgent 优先用 ReAct（本地 Ollama 编排工具），Ollama 不可用时
    自动降级为关键词路由，保证 8GB 单机始终可用。

    返回 dict：
      answer      自然语言回答（来自工具结果要点）
      open_tab    最相关模块（music/video/novel 或 None），供前端一键深链
      suggestions 2~3 条上下文相关的「下一步」建议，让对话有 agent 的主动性
    """
    answer = LycheeAgent().run(instruction)
    module = _module_of(instruction, answer)
    suggestions = _SUGGESTIONS.get(module, [
        "🎵 推荐几首歌", "🎬 分析一个视频", "📖 给小说做分镜"
    ])
    return {"answer": answer, "open_tab": module, "suggestions": suggestions}


@router.post("/api/agent/chat", dependencies=[Depends(require_key), Depends(rate_limit)])
def agent_chat(req: AgentChatReq) -> dict:
    """提交一条自然语言指令给 Lychee Agent，返回用于轮询的 task_id。"""
    task_id = tasks.submit(_run_agent, req.instruction)
    return {"task_id": task_id}


