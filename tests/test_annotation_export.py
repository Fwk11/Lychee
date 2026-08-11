"""annotation_export 导出逻辑单测。

覆盖：
  * to_csv         单行报告 → 扁平 CSV（表头 + 每镜头一行）
  * to_csv_batch   多报告合并 → 单一 CSV（表头只出现一次）
  * build_shot_annotation 单镜头标注结构
  * annotation_schema 归一化函数（色温 / 运镜 / 0-5→1-10）
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.video.annotation_export import (
    build_shot_annotation,
    to_annotation_json,
    to_csv,
    to_csv_batch,
)
from src.video.annotation_schema import (
    normalize_camera,
    normalize_color_temp,
    scale_0_5_to_1_10,
)

VID_A = "vidA"
VID_B = "vidB"


def _shot(i: int) -> dict:
    return {
        "shot_id": f"s{i}",
        "start_sec": i * 2.0,
        "end_sec": (i + 1) * 2.0,
        "color": {
            "dominant_colors": [[255, 0, 0], [0, 255, 0], [0, 0, 255]],
            "saturation_mean": 0.5,
            "brightness_mean": 0.6,
            "color_temp": "warm",
            "color_contrast": 0.4,
        },
        "lighting": {"exposure": "normal", "dynamic_range": 0.7},
        "compliance": {"verdict": "pass", "reasons": [], "faces_detected": 0},
        "scores": {"A1": 0.8, "B3": 0.6},
        "mood": "calm",
        "camera_move": "pan",
        "shot_scale": "medium",
        "composition": "rule_of_thirds",
        "content_caption": "a scene",
    }


def _report(vid: str, n: int) -> dict:
    shots = [_shot(i) for i in range(n)]
    return {"video_id": vid, "shots": shots, "shot_count": n}


def test_to_csv_single_header_and_rows():
    text = to_csv(_report(VID_A, 3))
    lines = text.strip().split("\n")
    assert lines[0].startswith("video_id,")          # 表头存在
    assert len(lines) == 4                            # 表头 + 3 镜头
    assert all(VID_A in ln for ln in lines[1:])


def test_to_csv_batch_merges_with_single_header():
    text = to_csv_batch([_report(VID_A, 2), _report(VID_B, 3)])
    lines = text.strip().split("\n")
    headers = [ln for ln in lines if ln.startswith("video_id,")]
    assert len(headers) == 1                          # 表头仅一次
    assert len(lines) == 1 + 2 + 3                    # 表头+2+3 行
    assert any(VID_A in ln for ln in lines)
    assert any(VID_B in ln for ln in lines)


def test_to_csv_batch_empty_returns_header_only():
    text = to_csv_batch([])
    lines = text.strip().split("\n")
    assert len(lines) == 1
    assert lines[0].startswith("video_id,")


def test_build_shot_annotation_structure():
    a = build_shot_annotation(_shot(0), VID_A)
    assert a["video_id"] == VID_A
    assert a["labels"]["camera_motion"] == "pan"
    assert len(a["labels"]["color"]["dominant_colors_hex"]) == 3
    assert a["labels"]["quality_scores"]["dimensions"]["A1"]["score"] == 0.8


def test_build_shot_annotation_data_value_from_report():
    """data_value 是视频级字段，传入 report 后应正确填充到每镜标注里。"""
    rep = _report(VID_A, 1)
    rep["data_value"] = {
        "tech": {"width": 1920, "height": 1080, "sharpness": 800.0, "resolution": "1920x1080"},
        "phash": "abcd1234",
    }
    a = build_shot_annotation(_shot(0), VID_A, report=rep)
    dv = a["labels"]["data_value"]
    assert dv["tech_quality"]["resolution"] == "1920x1080"
    assert dv["phash"] == "abcd1234"


def test_to_annotation_json_populates_data_value():
    """端到端：to_annotation_json 应把报告级 data_value 透传到每镜标注。"""
    rep = _report(VID_A, 2)
    rep["data_value"] = {"tech": {"width": 1280, "resolution": "1280x720"}, "phash": "ff00"}
    j = to_annotation_json(rep)
    for a in j["annotations"]:
        assert a["labels"]["data_value"]["phash"] == "ff00"
        assert a["labels"]["data_value"]["tech_quality"]["resolution"] == "1280x720"


def test_schema_normalizers():
    # 色温
    assert normalize_color_temp("warm") == "暖调"
    assert normalize_color_temp("cool") == "冷调"
    assert normalize_color_temp("neutral") == "中性"
    assert normalize_color_temp("unknown_x") is None
    # 运镜：命中映射用规范值，未知值原样返回
    assert normalize_camera("固定") == "固定"
    assert normalize_camera("移动/跟随") == "跟拍"
    assert normalize_camera("weird_move") == "weird_move"
    # 0-5 → 1-10 夹紧
    assert scale_0_5_to_1_10(0.0) == 1
    assert scale_0_5_to_1_10(2.5) == 5
    assert scale_0_5_to_1_10(5.0) == 10
    assert scale_0_5_to_1_10(None) is None
