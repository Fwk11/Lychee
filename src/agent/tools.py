#!/usr/bin/env python3
"""LangChain tool registry for Lychee.

Each tool wraps an existing deterministic module so the ReAct agent
(src/agent/agent.py) can orchestrate them from natural language.

Design rule: tools return SHORT text summaries (LLM context is small on
an 8GB machine) and write full artifacts to disk like the pipeline does.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from langchain_core.tools import tool

REPORT_DIR = os.path.join(ROOT, "output", "reports")
VIDEO_DIR = os.path.join(ROOT, "data", "raw", "videos")
NOVEL_DIR = os.path.join(ROOT, "data", "novel")
AUDIO_DIR = os.path.join(NOVEL_DIR, "audio")
STORYBOARD_DIR = os.path.join(NOVEL_DIR, "storyboard")


# --------------------------------------------------------------------------
# video tools
# --------------------------------------------------------------------------

@tool
def list_videos() -> str:
    """列出 data/raw/videos 下可分析的视频文件名。当用户想知道有哪些视频可选时使用。"""
    if not os.path.isdir(VIDEO_DIR):
        return "视频目录不存在"
    files = sorted(f for f in os.listdir(VIDEO_DIR)
                   if f.lower().endswith((".mp4", ".mov", ".avi", ".mkv")))
    return f"共 {len(files)} 个视频:\n" + "\n".join(files[:40])


@tool
def analyze_video(video_name: str) -> str:
    """分析单个视频：镜头切分 + 色彩/光线/运镜 + VLM 画面描述 + 17维RLHF打分 + 合规判定。
    参数 video_name 是 data/raw/videos 下的文件名（可不带扩展名）。分析约需1-3分钟。"""
    from src.video.aesthetics_pipeline import run_pipeline

    path = os.path.join(VIDEO_DIR, video_name)
    if not os.path.exists(path):
        cands = [f for f in os.listdir(VIDEO_DIR) if f.startswith(video_name)]
        if not cands:
            return f"找不到视频 {video_name}，请先用 list_videos 查看可用文件"
        path = os.path.join(VIDEO_DIR, cands[0])

    report = run_pipeline(path)
    os.makedirs(REPORT_DIR, exist_ok=True)
    out = os.path.join(REPORT_DIR, report["video_id"] + ".json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    lines = [f"已分析 {report['video_id']}: {report['shot_count']} 个镜头, "
             f"时长 {report['duration_sec']}s, 报告 -> {out}"]
    for s in report["shots"][:5]:
        comp = (s.get("compliance") or {}).get("verdict", "?")
        cap = (s.get("content_caption") or "")[:40]
        lines.append(f"  {s['shot_id']} [{s['start_sec']}-{s['end_sec']}s] "
                     f"运镜:{s.get('camera_move')} 合规:{comp} 画面:{cap}")
    return "\n".join(lines)


@tool
def get_report(video_id: str) -> str:
    """读取某视频的已有分析报告摘要（镜头数/合规/美学分）。video_id 是不带扩展名的视频名。"""
    path = os.path.join(REPORT_DIR, video_id + ".json")
    if not os.path.exists(path):
        return f"报告不存在: {video_id}（可先用 analyze_video 生成）"
    r = json.load(open(path, encoding="utf-8"))
    lines = [f"{r['video_id']}: {r['shot_count']} 镜头, {r.get('duration_sec')}s"]
    for s in r.get("shots", []):
        comp = (s.get("compliance") or {}).get("verdict", "?")
        proxy = s.get("aesthetic_proxy")
        lines.append(f"  {s['shot_id']}: 合规={comp} 美学分={proxy} "
                     f"画面={(s.get('content_caption') or '')[:50]}")
    return "\n".join(lines[:25])


@tool
def list_reports() -> str:
    """列出所有已生成的视频分析报告及其镜头数。"""
    if not os.path.isdir(REPORT_DIR):
        return "还没有任何报告"
    out = []
    for f in sorted(os.listdir(REPORT_DIR)):
        if f.endswith(".json"):
            try:
                r = json.load(open(os.path.join(REPORT_DIR, f), encoding="utf-8"))
                out.append(f"{r['video_id']}: {r.get('shot_count')} 镜头")
            except Exception:
                continue
    return "\n".join(out) if out else "还没有任何报告"


# --------------------------------------------------------------------------
# music tools
# --------------------------------------------------------------------------

@tool
def music_profile() -> str:
    """查看用户音乐喜好画像：风格分布、情绪分布、最常听歌手、热度偏好。"""
    p = os.path.join(ROOT, "data", "music", "taste_profile.json")
    if not os.path.exists(p):
        return "喜好画像尚未生成，需先导入歌单（上传 QQ 音乐分享卡片或运行 import_qqmusic_playlist）"
    d = json.load(open(p, encoding="utf-8"))

    def _pct(dist: dict, k: int) -> str:
        total = sum(dist.values()) or 1.0
        top = sorted(dist.items(), key=lambda x: -x[1])[:k]
        return ", ".join(f"{name}({v / total:.0%})" for name, v in top)

    arts = d.get("top_artists", [])[:8]
    names = []
    for a in arts:
        if isinstance(a, dict):
            names.append(f"{a.get('name')}({a.get('plays', 0):.0f}次)")
        elif isinstance(a, (list, tuple)):
            names.append(str(a[0]))
        else:
            names.append(str(a))
    return (f"风格Top: {_pct(d.get('style_dist', {}), 6)}\n"
            f"情绪Top: {_pct(d.get('mood_dist', {}), 5)}\n"
            f"常听歌手: {', '.join(names)}\n"
            f"热度偏好: {d.get('pop_preference') or d.get('popularity_preference', '未知')}")


@tool
def recommend_music(top_n: int = 10) -> str:
    """生成多维音乐推荐（歌手/风格/热度/旋律四维加权），产出一套综合推荐歌单。
    top_n 是歌单的歌曲数（默认10）。"""
    from src.music.recommender_v2 import recommend_all
    d = recommend_all(top_n=top_n)
    lines = []
    for key, pl in d["playlists"].items():
        lines.append(f"== {pl['label']} ==")
        for s in pl["songs"][:5]:
            lines.append(f"  {s['title']} — {','.join(s['artists'])} "
                         f"(分{s['score']:.2f}) {s.get('reason', '')}")
    return "\n".join(lines[:60])


@tool
def recommend_music_for_others(card_text: str) -> str:
    """为「他人」做音乐推荐：粘贴对方分享的 QQ音乐/网易云「我喜欢的音乐」或歌单文本，
    解析出歌单后，用与本人相同的引擎基于对方口味生成推荐，不污染你自己的画像。
    card_text 是整段分享卡片文本或歌名列表。"""
    from src.music.recommender_v2 import recommend_for_card
    d = recommend_for_card(card_text)
    if d.get("error"):
        return d["error"]
    lines = [f"已为对方匹配 {d['matched']} 首已知歌曲"
             + (f"（{len(d['unmatched'])} 首未识别）" if d.get("unmatched") else "")
             + "，生成推荐："]
    for key, pl in d["playlists"].items():
        lines.append(f"== {pl['label']} ==")
        for s in pl["songs"][:5]:
            lines.append(f"  {s['title']} — {','.join(s['artists'])} "
                         f"(分{s['score']:.2f}) {s.get('reason', '')}")
    return "\n".join(lines[:60])


@tool
def import_sheet(songmid: str, sheet_text: str, title: str = "", artist: str = "") -> str:
    """导入某首歌的真实简谱（如 "3 5 6 1 2 3"），让该歌的旋律维度从代理特征变为真实特征。
    songmid 是QQ音乐歌曲id，sheet_text 是数字简谱文本。"""
    from src.music.melody import import_sheet as _imp
    r = _imp(songmid, sheet_text, title=title, artist=artist)
    return f"已导入简谱: {r}"


# --------------------------------------------------------------------------
# novel tools
# --------------------------------------------------------------------------

@tool
def list_novels() -> str:
    """列出 data/novel 下已上传的小说。当用户问"有哪些小说/书"时使用。"""
    from src.novel.loader import list_novels as _list
    novels = _list()
    if not novels:
        return "还没有上传小说。请到「小说剧场」tab 上传 txt/docx/epub/pdf。"
    lines = [f"共 {len(novels)} 本:"]
    for n in novels[:40]:
        name = os.path.splitext(n["file"])[0]
        mb = n.get("size", 0) / 1024 / 1024
        lines.append(f"  《{name}》 ({mb:.1f} MB)")
    return "\n".join(lines)


@tool
def read_novel_chapter(name: str, chapter: int, max_chars: int = 600) -> str:
    """读取指定小说的某一章正文摘要。name 是小说名（不带扩展名），chapter 是章序号。"""
    from src.novel.loader import load_novel
    path = os.path.join(NOVEL_DIR, f"{name}.txt")
    if not os.path.exists(path):
        return f"找不到小说《{name}》。可先用 list_novels 查看已上传小说。"
    try:
        novel = load_novel(path, name=name)
        if chapter < 1 or chapter > novel.n_chapters:
            return f"《{name}》只有 {novel.n_chapters} 章，第 {chapter} 章不存在。"
        ch = novel.chapters[chapter - 1]
        text = (ch.text or "（本章无正文）").strip()
        preview = text[:max_chars] + ("……" if len(text) > max_chars else "")
        return f"《{name}》第{chapter}章 · {ch.title}\n{preview}"
    except Exception as e:
        return f"读取章节失败：{e}"


@tool
def list_storyboards(name: str = "") -> str:
    """列出某小说已生成的分镜缓存。name 为空则列出全部。"""
    if not os.path.isdir(STORYBOARD_DIR):
        return "还没有分镜缓存。"
    files = sorted(f for f in os.listdir(STORYBOARD_DIR) if f.endswith(".json"))
    if name:
        files = [f for f in files if f.startswith(name + "_")]
    if not files:
        return f"《{name}》还没有分镜缓存" if name else "还没有任何分镜缓存。"
    lines = []
    for f in files[:40]:
        try:
            d = json.load(open(os.path.join(STORYBOARD_DIR, f), encoding="utf-8"))
            r = d.get("chapter_range", {})
            n = len(d.get("shots", []))
            lines.append(f"  {f.replace('.json', '')}: 第{r.get('start')}-{r.get('end')}章 · {n} 镜")
        except Exception:
            lines.append(f"  {f}")
    return "\n".join(lines)


@tool
def storyboard_novel_chapters(name: str, start: int = 1, end: int = 5) -> str:
    """为小说生成连续多章的国漫分镜方案（默认前5章）。name 是小说名，start/end 是章范围。
    该任务较重，会提交到后台；返回 task_id，可轮询 /api/tasks/{task_id} 查看进度。"""
    from src.novel.storyboard import storyboard_batch
    from src.api import tasks
    path = os.path.join(NOVEL_DIR, f"{name}.txt")
    if not os.path.exists(path):
        return f"找不到小说《{name}》。可先用 list_novels 查看已上传小说。"
    task_id = tasks.submit(storyboard_batch, path, start=start, end=end, novel_name=name)
    return (f"已提交《{name}》第{start}-{end}章分镜任务，task_id={task_id}。"
            f"请轮询 /api/tasks/{task_id}，或在「小说剧场」tab 查看分镜结果。")


ALL_TOOLS = [list_videos, analyze_video, get_report, list_reports,
             music_profile, recommend_music, recommend_music_for_others,
             import_sheet,
             list_novels, read_novel_chapter, list_storyboards,
             storyboard_novel_chapters]


if __name__ == "__main__":
    # smoke test: invoke each read-only tool directly
    print(list_videos.invoke({}))
    print("---")
    print(list_reports.invoke({}))
    print("---")
    print(music_profile.invoke({}))
