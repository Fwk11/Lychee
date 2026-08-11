# -*- coding: utf-8 -*-
"""小说连续剧分镜框架。

诚实说明：当前技术无法一比一还原原著的"真人动漫剧"成片。
本模块做可落地的替代——把小说拆成「连续剧拍摄方案」：
1. LLM（本地 qwen2.5:3b）把整部/分章小说改编为多集连续剧方案，每集含多组分镜；
2. 每个分镜给出：场景 / 景别 / 运镜 / 剧情功能 / 场景布置 / 表演动作 /
   灯光与技术 / 特效 / 旁白台词 / 国漫视频提示词；
3. 用户把视频提示词用于生成动态镜头。

全部为本地 LLM 生成，零成本、零额度消耗（视频生成那一步才需要你自己的额度）。

关键质量保障：
- 强制 Ollama JSON 模式，杜绝"半截自然语言+残缺 JSON → 字段全空"；
- 在 prompt 内放一个完整示范镜头，给 3B 模型定下"详细到什么程度"的标杆；
- 输入先剥离"上架/求订阅/求月票"等网文废话，避免污染旁白；
- 解析后若关键字段大面积留空，自动重试一次（更强约束）；
- 跨章/跨批衔接状态，保证逐镜投喂视频大模型时时空与人物连续。

本包原先是单一 1280+ 行文件，现按职责拆为子模块：
- _common   公共常量与纯文本工具（无包内依赖）
- prompts   各类 prompt 构造（骨架/富化/角色设定/台词）
- shots     镜头解析、剧情密度、文本切分
- bible     角色形象设定库
- continuity 跨章/跨批衔接状态
- compose   一体化提示词编织与技术参数推导
- generate  生成编排（对外公开 API 在此）
"""
from __future__ import annotations

# 公共常量
from ._common import (
    CONTINUITY_DIR,
    SERIES_DIR,
    SHOT_FIELDS,
    STORYBOARD_DIR,
)

# 生成编排（对外公开 API）
from .generate import (
    get_series_plan,
    make_director_script,
    make_series_plan,
    make_storyboard,
    series_plan,
    storyboard_batch,
    storyboard_chapter,
)

# 提示词与解析（供其它模块复用，也保留为包级 API）
from .prompts import (
    _BIBLE_EXAMPLE,
    _RICH_FIELDS,
    _SKELETON_FIELDS,
    _SKELETON_SCHEMA,
    _build_bible_prompt,
    _build_dialogue_prompt,
    _build_rich_prompt,
    _build_skeleton_prompt,
)
from .shots import (
    _clean_dialogue,
    _collect_names,
    _dynamic_shot_count,
    _parse_dialogue,
    _parse_rich,
    _parse_shots,
    _parse_skeleton,
    _segment_text,
    _story_density,
)
from .bible import (
    _alias_merge,
    _shot_character_design,
    build_character_bible,
    build_full_bible,
)
from .continuity import (
    _chapter_tail_state,
    _continuity_path,
    _extract_continuity,
    _load_continuity,
    _merge_bible,
    _prior_block,
    _save_continuity,
)
from .compose import (
    _derive_spec,
    _is_copied_example,
    _ngrams,
    _too_similar,
    _uniq_segments,
    attach_all,
    attach_prompts,
    compose_prompt,
    compose_prompt_en,
)

__all__ = [
    # 常量
    "STORYBOARD_DIR", "SERIES_DIR", "CONTINUITY_DIR", "SHOT_FIELDS",
    # 公开 API
    "storyboard_chapter", "storyboard_batch", "make_storyboard",
    "series_plan", "get_series_plan", "make_series_plan",
    "build_character_bible", "build_full_bible", "compose_prompt", "compose_prompt_en",
    "attach_prompts", "attach_all",
    # 子模块内部工具（保留为包级 API，便于复用与测试）
    "_BIBLE_EXAMPLE", "_RICH_FIELDS", "_SKELETON_FIELDS", "_SKELETON_SCHEMA",
    "_build_bible_prompt", "_build_dialogue_prompt", "_build_rich_prompt", "_build_skeleton_prompt",
    "_clean_dialogue", "_collect_names", "_dynamic_shot_count", "_parse_dialogue",
    "_parse_rich", "_parse_shots", "_parse_skeleton", "_segment_text", "_story_density",
    "_shot_character_design", "_chapter_tail_state", "_continuity_path",
    "_extract_continuity", "_load_continuity", "_merge_bible", "_prior_block", "_save_continuity",
    "_derive_spec", "_is_copied_example", "_ngrams", "_too_similar", "_uniq_segments",
]
