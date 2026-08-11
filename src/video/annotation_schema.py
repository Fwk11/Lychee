#!/usr/bin/env python3
"""统一工业级标注 schema（RLHF 分层维度树）加载器。

设计目标（呼应「数据集很多视频 → 统一模板」）：
  * schema 是唯一事实来源（single source of truth），存于 ``data/annotation_schema.json``，
    人类可直接编辑，无需改代码即可调整标签/维度。
  * 不同数据集可在 ``data/annotation_schema_{dataset}.json`` 放同名覆盖文件，
    实现「全局默认 + 按数据集微调」。
  * 导出函数（annotation_export）只认 schema，不再按单条视频的检测结果动态生成配置，
    因此同一数据集下所有样本打开 LS 都看到**完全一致的全量标签面板**。

维度分层：
  层1 时间轴标签（按镜头打，TimelineLabels）
  层2 美学评分（1-10 带锚，Rating）
  层3 技术质量评分（1-10 带锚，Rating）
  层4 合规与安全（Choices）
  层5 RLHF 偏好对齐（Choices + TextArea）
  层6 时序/剪辑（Choices）
"""
from __future__ import annotations

import json
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_SCHEMA_PATH = os.path.join(ROOT, "data", "annotation_schema.json")


def _schema_path(dataset: str | None = None) -> str:
    if dataset:
        cand = os.path.join(ROOT, "data", f"annotation_schema_{dataset}.json")
        if os.path.exists(cand):
            return cand
    return DEFAULT_SCHEMA_PATH


def load_schema(dataset: str | None = None) -> dict:
    """加载统一标注 schema；dataset 存在且覆盖文件存在则用它，否则用默认。"""
    path = _schema_path(dataset)
    with open(path, encoding="utf-8") as f:
        schema = json.load(f)
    schema["_source"] = path
    return schema


def save_schema_override(dataset: str, schema: dict) -> str:
    """为某数据集保存覆盖 schema（不改动默认模板）。"""
    path = os.path.join(ROOT, "data", f"annotation_schema_{dataset}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(schema, f, ensure_ascii=False, indent=2)
    return path


def timeline_label_names(schema: dict) -> list[str]:
    return [c["name"] for c in schema.get("timeline_labels", [])]


def score_names(schema: dict) -> list[str]:
    return [s["name"] for s in schema.get("scores", [])]


def choice_names(schema: dict) -> list[str]:
    return [c["name"] for c in schema.get("choices", [])]


def text_names(schema: dict) -> list[str]:
    return [t["name"] for t in schema.get("texts", [])]


# ---- 检测值 → 规范值归一化 ------------------------------------------------
# 让 Lychee 自动检测出的自由文本尽量命中统一 schema 的规范标签，
# 命中才预填，未命中则留给人工（不强行塞入造成脏数据）。
_CAMERA_MAP = {
    "固定": "固定",
    "推/变焦(放大)": "推",
    "拉/变焦(缩小)": "拉",
    "推·变焦(放大)": "推",
    "拉·变焦(缩小)": "拉",
    "摇(水平)": "摇(水平)",
    "摇(垂直)": "摇(垂直)",
    "手持": "手持",
    "移动/跟随": "跟拍",
    "移动·跟随": "跟拍",
    "轻微运动": "移",
    "斯坦尼康": "斯坦尼康",
    "航拍": "航拍",
    "环绕": "环绕",
    "变焦": "变焦",
    "甩镜": "甩镜",
    "旋转": "旋转",
    "升降": "升降",
}

# 报告 scores(0-5) → schema 评分维度名 的映射
# 注意：a_overall（美学综合）不在此映射——它由 to_label_studio 里专门的
# aesthetic_proxy 块负责，避免与 C2 重复生成同一 from_name 的两条 rating。
# 报告 color.color_temp 原始值 → 统一 schema 规范值（冷调/暖调/中性）
_COLOR_TEMP_MAP = {
    "warm": "暖调", "暖": "暖调", "warmth": "暖调", "orange": "暖调",
    "cool": "冷调", "冷": "冷调", "cold": "冷调", "blue": "冷调",
    "neutral": "中性", "中性": "中性", "gray": "中性", "grey": "中性",
    "灰": "中性", "daylight": "中性",
}


def normalize_color_temp(value: str | None) -> str | None:
    """把检测出的色温原始值归一化到 schema 规范值；未知值返回 None 留给人工。"""
    if not value:
        return None
    return _COLOR_TEMP_MAP.get(str(value).strip().lower(), None)


SCORE_MAP = {
    "B3": "a_comp",      # 构图美感
    "B4": "a_subject",   # 主体清晰
    "B5": "a_depth",     # 景深层次
    "B6": "a_rhythm",    # 节奏感
    "A1": "a_color",     # 色彩和谐度
    "A3": "a_light",     # 明暗对比
    "V1": "t_sharp",     # 清晰度
}


def normalize_camera(value: str | None) -> str | None:
    if not value:
        return None
    return _CAMERA_MAP.get(str(value).strip(), str(value).strip())


def scale_0_5_to_1_10(v) -> int | None:
    """把 0-5 分映射到 1-10（夹紧），None 原样返回。"""
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return max(1, min(10, int(round(x * 2))))
