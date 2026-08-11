# -*- coding: utf-8 -*-
"""角色形象设定库：跨镜头锁死同一张脸，避免每镜画出来不是同一个人。

同时提供按小说的【整本人物库】落盘缓存（data/novel/bible/{小说}.json），
让分镜批量任务只生成一次、跨批次/跨次运行复用，避免每章重复调 LLM 建库。
"""
from __future__ import annotations

import json
import os
import re
from typing import List, Optional

from ..analyzer import _chat
from ._common import BIBLE_DIR, _clean_text
from .prompts import _build_bible_prompt


# prompt few-shot 示范里出现的角色名；模型偶尔会原样照抄，需在解析时剔除
_DEMO_NAMES = {"小不点", "石云峰", "柳神"}


def _bible_path(novel_name: str) -> str:
    safe = re.sub(r"[^\w\u4e00-\u9fff-]", "_", novel_name)
    return os.path.join(BIBLE_DIR, f"{safe}.json")


def load_bible(novel_name: str) -> dict:
    """读取该小说的整本人物库缓存；没有则返回空 dict。"""
    p = _bible_path(novel_name)
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_bible(novel_name: str, bible: dict) -> None:
    """把整本人物库落盘，供后续批次/次运行复用。"""
    if not bible:
        return
    os.makedirs(BIBLE_DIR, exist_ok=True)
    json.dump(bible, open(_bible_path(novel_name), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


def build_character_bible(ch_title: str, body: str, names: List[str]) -> dict:
    """一次 LLM 调用产出角色形象库：{角色名: {"zh": ..., "en": ...}}。空结果自动重试。"""
    bible: dict = {}
    for _ in range(3):
        raw = _chat(
            _build_bible_prompt(ch_title, body, names),
            system="你是动画角色设定师，只输出合法JSON对象。",
            num_predict=1200, json_mode=True,
        )
        try:
            obj = json.loads(raw)
        except Exception:
            m = re.search(r"\{.*\}", raw, re.S)
            if not m:
                continue
            try:
                obj = json.loads(m.group(0))
            except Exception:
                continue
        items = obj.get("characters") if isinstance(obj, dict) else None
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            name = (it.get("name") or "").strip().strip("（）() ")
            zh = (it.get("zh") or "").strip()
            en = (it.get("en") or "").strip()
            if not name or not zh:
                continue
            # 模型照抄 prompt 里的示范角色时丢弃（示范名是固定的，与具体小说无关；
            # 若原文本身就有这个名字则保留）
            if name in _DEMO_NAMES and name not in body:
                continue
            bible[name] = {"zh": zh, "en": en}
        if bible:
            break
    return bible


def _shot_character_design(shot: dict, bible: dict, lang: str = "zh") -> str:
    """取本镜出场角色的形象设定，拼成一句（喂给视频大模型保持一致）。"""
    if not bible:
        return ""
    chars = shot.get("characters") or ""
    hits = []
    for name, d in bible.items():
        if name and name in chars:
            desc = (d.get(lang) or "").strip().rstrip("。；;. ")
            if desc:
                hits.append(f"{name}——{desc}" if lang == "zh" else f"{name}: {desc}")
    return "；".join(hits) if lang == "zh" else "; ".join(hits)


# 纯称谓词：这类名字本身不含人名，原文里常与真名连写（「老族长石云峰」）
_TITLE_WORDS = {
    # 门派 / 势力
    "族长", "长老", "村长", "掌门", "门主", "宗主", "家主", "堂主", "教主",
    "城主", "庄主", "帮主", "国师", "酋长", "首领", "队长", "护法", "执事",
    "阁主", "岛主", "谷主", "峰主", "舵主", "寨主", "统领", "副手",
    # 师承 / 学堂
    "师尊", "师父", "师傅", "师兄", "师姐", "师弟", "师妹", "老师", "先生",
    "院长", "教习", "夫子", "祖师", "掌教",
    # 尊称 / 身份
    "公子", "姑娘", "小姐", "少爷", "大人", "将军", "道长", "和尚", "大师",
    "真人", "上人", "尊者", "前辈", "道友", "圣女", "圣子", "少主", "家老",
    "管家", "老板", "掌柜", "神医", "剑客",
    # 皇室
    "陛下", "殿下", "太子", "皇帝", "皇后", "太后", "王爷", "公主", "郡主",
    "驸马", "娘娘",
    # 年龄 / 亲属泛称
    "老者", "老人", "老头", "老妪", "少年", "少女", "丫头", "婆婆", "爷爷",
    "奶奶", "叔叔", "伯伯", "婶婶", "姑姑", "舅舅", "嬷嬷", "夫人", "娘子",
    "汉子", "妇人", "书生", "乞丐",
}
# 称谓可带的修饰前缀：老族长 / 大长老 / 小丫头
_TITLE_PREFIX = re.compile(r"^(?:老|小|大|阿|少|众|诸|副)+")
# 职衔+尊称的后缀：火影大人 / 叶前辈 / 青云真人 —— 前半是职衔或姓氏，不是真名全称
_TITLE_SUFFIX = (
    "大人", "阁下", "殿下", "陛下", "前辈", "长老", "真人", "尊者", "上人",
    "道友", "师叔", "师伯", "老祖", "圣人", "宗主", "掌门", "族长", "将军",
    "大师", "先生", "夫人", "公子", "姑娘", "少爷", "小姐",
)


def _is_title_only(name: str) -> bool:
    """判断这个名字是不是纯称谓（不含真实人名），如「老族长」「大长老」「火影大人」。"""
    name = (name or "").strip()
    if not name:
        return False
    # 先整体比对，避免「少年」「老者」「大人」被前缀正则剥成「年」「者」「人」而漏判
    if name in _TITLE_WORDS:
        return True
    core = _TITLE_PREFIX.sub("", name)
    if core and core in _TITLE_WORDS:
        return True
    # 职衔/姓氏 + 尊称后缀（火影大人、叶前辈、青云真人），限长防误吞真名
    if len(name) <= 5:
        for suf in _TITLE_SUFFIX:
            if name.endswith(suf) and len(name) > len(suf):
                return True
    return False


def _alias_merge_by_text(names: List[str], text: str) -> dict:
    """从原文的「称谓+姓名」连写识别同一人，返回 {称谓名: 真名}。

    典型场景：原文写「老族长石云峰」，3b 抽角色时拆成「老族长」和「石云峰」
    两条，导致同一人在角色形象库里分裂成两张脸。本地正则即可判定，不调 LLM。

    只在「一方是纯称谓、另一方不是」时合并，避免把「石昊」「石村」这类
    恰好相邻出现的普通词误判成同一人。
    """
    if not text or not names:
        return {}
    pool = [n for n in dict.fromkeys(names) if n and len(n) <= 16]
    out: dict = {}
    for a in pool:
        for b in pool:
            if a == b:
                continue
            # 必须一方是纯称谓、一方是真名，方向固定为 称谓 -> 真名
            if not (_is_title_only(a) and not _is_title_only(b)):
                continue
            # 原文中「称谓+真名」或「真名+称谓」连写才算同一人
            if re.search(re.escape(a) + re.escape(b), text) or \
               re.search(re.escape(b) + re.escape(a), text):
                out[a] = b
                break
    # 防成环/多级指向：a->b 且 b->c 时，统一指到最终真名
    for k in list(out.keys()):
        seen, v = {k}, out[k]
        while v in out and v not in seen:
            seen.add(v)
            v = out[v]
        out[k] = v
    return {k: v for k, v in out.items() if k != v}


def _alias_merge(new_names: List[str], existing_keys: List[str], use_llm: bool = True) -> dict:
    """判定新名字里哪些其实是 existing 某主名的别名/绰号/小名，返回 {别名: 主名}。

    解决 3b 把同一人写成两个名字（如「小不点」与「石昊」）导致角色形象库分裂的问题。

    - use_llm=False：纯本地规则（子串包含 / 去修饰词相等），不调 Ollama，供【单章生成】每章省一次 LLM 调用；
      能覆盖「小不点⊂石昊」「少年X==X」这类，覆盖不到的（如小不点 vs 石昊无字串关系）交给 build_full_bible 的 LLM 版一次性处理。
    - use_llm=True：走一次 LLM 判定，覆盖更复杂的异名，供【建全本角色库】使用。
    """
    novel = [n for n in new_names if n and n not in existing_keys]
    if not novel or not existing_keys:
        return {}
    if not use_llm:
        out = {}
        _pref = re.compile(r"^(少年|少女|老|小|大|阿|公子|姑娘|先生|师傅|族长|少主)")
        for n in novel:
            a = _pref.sub("", n)
            for k in existing_keys:
                b = _pref.sub("", k)
                if not a or not b:
                    continue
                if a == b or n in k or k in n:
                    out[n] = k
                    break
        return out
    prompt = (
        "下面是一本书里出现的角色名，其中一些可能是同一人的不同称呼（小名、绰号、别名，"
        "如「小不点」与「石昊」、「叶天帝」与「叶凡」）。\n"
        f"已有主名列表：{existing_keys}\n"
        f"新出现名列表：{novel}\n"
        "请判断每个新名是否为某个已有主名的同一人（别名/绰号/小名）。\n"
        "严格只输出一个 JSON 对象：{\"别名\": \"主名\"}，没有别名则输出空对象{}。"
    )
    raw = _chat(prompt, system="你是资深编辑，只输出合法JSON对象。",
               num_predict=700, json_mode=True)
    try:
        m = re.search(r"\{.*\}", raw, re.S)
        obj = json.loads(m.group(0)) if m else {}
    except Exception:
        return {}
    if not isinstance(obj, dict):
        return {}
    out = {}
    for k, v in obj.items():
        if k in novel and v in existing_keys:
            out[k] = v
    return out


def build_full_bible(novel, analysis: Optional[dict] = None,
                     max_chapters: int = 80) -> dict:
    """一次性建全本角色形象库并落盘，返回 bible。

    优先用小说分析已抽的 characters 名直接建外貌，再分块扫全书原文补全，
    避免逐章跑才累积、漏抽次要角色的问题。兼容现有 _merge_bible/load_bible 机制。
    """
    existing = load_bible(novel.name)
    merged = dict(existing)
    # 1) 用分析抽到的角色名直接建外貌（给点首章原文当背景）
    names = []
    if analysis and isinstance(analysis, dict):
        for c in (analysis.get("characters") or []):
            n = (c.get("name") or "").strip()
            if n and len(n) <= 14:
                names.append(n)
    if names and novel.chapters:
        seed = _clean_text(novel.chapters[0].text)[:1500]
        merged = _merge_bible(merged, build_character_bible(novel.name, seed, names[:14]))
    # 2) 分块扫全书原文补全（每 3 章一块，避免一次吐太多角色质量崩）
    chapters = novel.chapters[:max_chapters]
    for i in range(0, len(chapters), 3):
        seg = "\n\n".join(_clean_text(ch.text)[:1000] for ch in chapters[i:i + 3])
        if not seg.strip():
            continue
        merged = _merge_bible(merged, build_character_bible(novel.name, seg, []))
    save_bible(novel.name, merged)
    return merged
