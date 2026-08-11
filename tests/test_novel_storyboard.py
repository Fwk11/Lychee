"""小说分镜关键交付物结构测试（纯函数，不依赖 Ollama）。

覆盖：
  * compose_prompt       一镜一条完整中文 prompt（含国漫风格、画面、台词）
  * compose_prompt_en    英文备选 prompt
  * _extract_dialogue    台词 100% 来自原文抽取，绝不编造
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.novel.storyboard.compose import compose_prompt, compose_prompt_en
from src.novel.storyboard.generate import _extract_dialogue


def test_compose_prompt_one_shot_complete():
    shot = {
        "shot_type": "中景",
        "arrangement": "石村少年立于悬崖之巅",
        "action": "迎风眺望远方",
        "emotion": "孤勇而坚定",
        "camera": "缓慢推近",
        "lighting_tech": "侧逆光勾勒轮廓",
        "color_script": "青绿主调",
        "dialogue": "我会变强。",
    }
    p = compose_prompt(shot)
    # 国漫风格尾巴
    assert "国漫" in p
    # 画面内容原样进入 prompt
    assert "石村少年立于悬崖之巅" in p
    # 台词原样进入 prompt
    assert "我会变强。" in p
    # 技术参数齐全
    assert "转场" in p
    assert "构图" in p


def test_compose_prompt_en_nonempty():
    shot = {
        "shot_type": "中景",
        "video_prompt_anime": "a teenage boy standing on a cliff, wind blowing",
    }
    en = compose_prompt_en(shot)
    assert en  # 非空
    assert "cliff" in en.lower()


def test_extract_dialogue_from_source():
    text = '石昊望着天空，说道：“我要成为最强者。” 老族长点头：“好，我信你。”'
    dlg = _extract_dialogue(text)
    lines = [d["line"] for d in dlg]
    assert "我要成为最强者。" in lines
    assert "好，我信你。" in lines
    # 台词是原文子串，绝不编造
    for d in dlg:
        assert d["line"] in text
