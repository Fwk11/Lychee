# -*- coding: utf-8 -*-
"""小说加载与章节切分。

支持本地 txt（UTF-8/GBK 自动检测），按中文章回体或"第N章"切分。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import List, Optional

NOVEL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "novel",
)

# 章节标题模式：第X回 / 第X章 / 第X节（中文数字或阿拉伯数字）
_CHAPTER_RE = re.compile(
    r"^\s*(第[一二三四五六七八九十百千零〇0-9]+[回章节卷])[\s　]*(.{0,40})$",
    re.MULTILINE,
)

# 清理标题里已有的原始章节号（如“第19章 第一章 …”→“第一章 …”），避免与重建后的 index 冲突。
_LEADING_CHAPTER_RE = re.compile(
    r"^第[一二三四五六七八九十百千零〇0-9]+[回章节卷][\s　]*"
)

# Project Gutenberg 头尾标记
_PG_START = re.compile(r"\*\*\* START OF (THE|THIS) PROJECT GUTENBERG.*?\*\*\*", re.S)
_PG_END = re.compile(r"\*\*\* END OF (THE|THIS) PROJECT GUTENBERG.*?\*\*\*", re.S)

# 作者公告/上架感言/访谈/书单等伪章节（网文 txt 常把“第2章 上架了，求订阅”之类塞进正文）
# 命中硬关键词即判为伪章节；或正文很短且含推广词时也判为伪章节，避免污染分镜/分析。
_PROMO_HARD = re.compile(
    r"上架了|求订阅|求月票|求推荐|求收藏|加更|爆发|单章|感言|访谈|封推|三江|"
    r"人物出场表|角色出场表|未全|连载中|持续更新|"
    r"腾讯视频|独播|上架感言|完本感言|致读者|新书即将开始|请假条|断更|开播了|"
    r"读者调查|意见征集|关于本书|写在前面|作者的话|作者有话说|"
    r"番外|外传|后记|跋|序言|序章|楔子|"
    r"起点.*盘点|.*网游|游戏授权|手游|页游| chapter.*done|本章.*完毕|今日.*完毕|完毕|"
    r"搜狐畅游|完美世界游戏|游戏改编|改编权|版权|授权|上市|公测|内测"
)
_PROMO_SOFT = re.compile(r"上架|订阅|月票|推荐|收藏|开播|独播|腾讯视频|访谈|感言|封推|三江|出场表|调查|网游|游戏|盘点|授权|改编")


def _is_promo(title: str, body: str) -> bool:
    if _PROMO_HARD.search(title):
        return True
    if len(body) < 500 and _PROMO_SOFT.search(body[:300]):
        return True
    return False


@dataclass
class Chapter:
    index: int              # 1-based
    title: str              # e.g. 第一回 灵根育孕源流出 心性修持大道生
    text: str               # 正文
    n_chars: int = 0

    def __post_init__(self):
        self.n_chars = len(self.text)


@dataclass
class Novel:
    name: str
    path: str
    chapters: List[Chapter] = field(default_factory=list)

    @property
    def n_chapters(self) -> int:
        return len(self.chapters)

    @property
    def n_chars(self) -> int:
        return sum(c.n_chars for c in self.chapters)

    def summary_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "n_chapters": self.n_chapters,
            "n_chars": self.n_chars,
            "chapters": [
                {"index": c.index, "title": c.title, "n_chars": c.n_chars}
                for c in self.chapters
            ],
        }


def _read_text(path: str) -> str:
    raw = open(path, "rb").read()
    for enc in ("utf-8", "gb18030", "big5"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _strip_gutenberg(text: str) -> str:
    m = _PG_START.search(text)
    if m:
        text = text[m.end():]
    m = _PG_END.search(text)
    if m:
        text = text[: m.start()]
    return text


def load_novel(path: str, name: Optional[str] = None) -> Novel:
    """加载并按章节切分小说。若识别不到章节标记，整本作为单章。"""
    text = _strip_gutenberg(_read_text(path))
    name = name or os.path.splitext(os.path.basename(path))[0]

    matches = list(_CHAPTER_RE.finditer(text))
    chapters: List[Chapter] = []
    if len(matches) >= 2:
        # 去重：目录区可能重复出现回目，取正文中最后一组连续出现
        seen_titles = {}
        for m in matches:
            key = m.group(1)
            seen_titles.setdefault(key, []).append(m)
        # 每个回目取最后一次出现（目录在前、正文在后）
        picked = sorted((v[-1] for v in seen_titles.values()), key=lambda m: m.start())
        for i, m in enumerate(picked):
            start = m.start()
            end = picked[i + 1].start() if i + 1 < len(picked) else len(text)
            title = (m.group(1) + " " + (m.group(2) or "").strip()).strip()
            body = text[start:end].strip()
            if len(body) > 100:  # 过滤目录残片
                chapters.append(Chapter(index=len(chapters) + 1, title=title, text=body))
    if not chapters:
        chapters = [Chapter(index=1, title=name, text=text.strip())]
    # 过滤作者公告/上架感言等伪章节（标题或短文本体含推广词）
    filtered = [c for c in chapters if not _is_promo(c.title, c.text)]
    if filtered:
        chapters = filtered
        for i, c in enumerate(chapters, 1):
            c.index = i
            # 去掉标题开头的旧章节号，防止出现“第19章 第一章 …”这类与 index 不匹配的情况。
            c.title = _LEADING_CHAPTER_RE.sub('', c.title).strip() or f"第{i}章"
    return Novel(name=name, path=path, chapters=chapters)


def list_novels() -> List[dict]:
    """列出 data/novel 下可用小说 txt。"""
    out = []
    if not os.path.isdir(NOVEL_DIR):
        return out
    for f in sorted(os.listdir(NOVEL_DIR)):
        if f.endswith(".txt") and not f.startswith("."):
            p = os.path.join(NOVEL_DIR, f)
            out.append({"file": f, "path": p, "size": os.path.getsize(p)})
    return out

