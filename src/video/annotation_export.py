"""自动标注导出模块 —— 把视频分析报告转为标准训练数据标注格式

支持两种导出格式：
  - JSON: 结构化标注，每镜头一条，字段对齐主流多模态训练标注 schema
  - CSV:  扁平表格，一行一镜头，方便 Excel/Pandas 查看
"""
import json, csv, io

from .annotation_schema import (
    load_schema, normalize_camera, normalize_color_temp,
    scale_0_5_to_1_10,
    SCORE_MAP as _SCORE_MAP,
)


# ---- 标注字段定义（对齐主流多模态训练数据标注 schema）----

SCORE_DIM_NAMES = {
    "A1": "色彩和谐度", "A2": "饱和度适宜", "A3": "明暗对比",
    "B1": "镜头稳定", "B2": "运镜流畅", "B3": "构图美感",
    "B4": "主体清晰", "B5": "景深层次", "B6": "节奏感",
    "C1": "情绪标签", "C2": "美学综合", "C3": "人工复核",
    "V1": "数据价值-清晰度", "V2": "数据价值-信息量",
    "V3": "数据价值-多样性", "V4": "数据价值-标注难度",
    "V5": "数据价值-可用性", "V6": "数据价值-独特性",
}

COMPLIANCE_GATES = {
    "G1": "内容安全", "G2": "暴力血腥", "G3": "敏感标识",
    "G4": "水印版权", "G5": "人脸隐私",
}


def _hex_color(rgb: list[int]) -> str:
    """[r,g,b] → #rrggbb"""
    if not rgb or len(rgb) < 3:
        return ""
    return "#{:02x}{:02x}{:02x}".format(*rgb[:3])


def build_shot_annotation(shot: dict, video_id: str = "",
                          report: dict | None = None) -> dict:
    """把单个镜头的分析结果转为标准标注格式。

    report 传入后可取视频级 data_value（分辨率/清晰度/phash），否则该块为空。
    """
    color = shot.get("color", {}) or {}
    compliance = shot.get("compliance", {}) or {}
    scores = shot.get("scores", {}) or {}
    lighting = shot.get("lighting", {}) or {}

    # 数据价值来自视频级（报告的 data_value），不是镜头级；
    # 报告里存的是 {"tech": {...}, "phash": "...}，这里对齐字段名。
    dv = {}
    if report:
        rdv = report.get("data_value", {}) or {}
        dv = {
            "tech_quality": rdv.get("tech") or {},
            "phash": rdv.get("phash", ""),
        }

    # 评分维度展开（带中文名）
    score_labels = {}
    for dim, name in SCORE_DIM_NAMES.items():
        val = scores.get(dim)
        if val is not None:
            score_labels[dim] = {"name": name, "score": val}

    # 合规门展开
    compliance_labels = {}
    for gate, desc in COMPLIANCE_GATES.items():
        val = compliance.get(gate)
        if val is not None:
            compliance_labels[gate] = {"check": desc, "result": val}

    # 主色转 hex
    dominant_hex = [_hex_color(c) for c in color.get("dominant_colors", [])[:3]]

    return {
        # ---- 基础元信息 ----
        "video_id": video_id,
        "shot_id": shot.get("shot_id", ""),
        "start_sec": round(shot.get("start_sec", 0), 2),
        "end_sec": round(shot.get("end_sec", 0), 2),
        "duration_sec": round(
            shot.get("end_sec", 0) - shot.get("start_sec", 0), 2
        ),
        "frame_count": shot.get("frame_count", 0),

        # ---- 自动标注标签 ----
        "labels": {
            "content_description": shot.get("content_caption", ""),
            "camera_motion": shot.get("camera_move", ""),
            "shot_scale": shot.get("shot_scale", ""),
            "composition": shot.get("composition", ""),
            "mood": shot.get("mood", ""),

            # 色彩标注
            "color": {
                "dominant_colors_hex": dominant_hex,
                "saturation": round(color.get("saturation_mean", 0), 3),
                "brightness": round(color.get("brightness_mean", 0), 3),
                "color_temperature": color.get("color_temp", ""),
                "contrast": round(color.get("color_contrast", 0), 3),
            },

            # 光线标注
            "lighting": {
                "exposure": lighting.get("exposure", ""),
                "dynamic_range": round(lighting.get("dynamic_range", 0), 3),
            },

            # 合规标注
            "compliance": {
                "verdict": compliance.get("verdict", ""),
                "gates": compliance_labels,
                "issues": compliance.get("reasons", []),
                "faces_detected": compliance.get("faces_detected", 0),
            },

            # RLHF / 美学评分
            "quality_scores": {
                "dimensions": score_labels,
                "aesthetic_proxy": shot.get("aesthetic_proxy"),
                "aesthetic_raw": shot.get("aesthetic_score"),
            },

            # 数据价值
            "data_value": {
                "tech_quality": dv.get("tech_quality"),
                "phash": dv.get("phash", ""),
            },
        },

        # ---- 标注元信息 ----
        "annotation_source": "auto",
        "annotator": "lychee-vlm-pipeline",
        "needs_review": compliance.get("verdict") != "compliant"
                        or any(v in (None, "null") for v in [scores.get("B3"), scores.get("C3")]),
    }


def to_annotation_json(report: dict) -> dict:
    """完整报告 → 标准标注 JSON。"""
    video_id = report.get("video_id", "")
    shots = report.get("shots", [])
    annotations = [build_shot_annotation(s, video_id, report=report) for s in shots]

    return {
        "task": "video_aesthetic_annotation",
        "schema_version": "1.0",
        "video": {
            "video_id": video_id,
            "source": report.get("source", ""),
            "duration_sec": report.get("duration_sec", 0),
            "fps": report.get("fps", 0),
            "frame_count": report.get("frame_count", 0),
            "shot_count": report.get("shot_count", 0),
        },
        "annotations": annotations,
        "summary": {
            "total_shots": len(annotations),
            "compliant": sum(1 for a in annotations
                             if a["labels"]["compliance"]["verdict"] == "compliant"),
            "needs_review": sum(1 for a in annotations if a["needs_review"]),
            "blocked": sum(1 for a in annotations
                           if a["labels"]["compliance"]["verdict"] == "blocked"),
            "avg_aesthetic": round(
                sum(a["labels"]["quality_scores"]["aesthetic_proxy"] or 0
                    for a in annotations) / max(len(annotations), 1), 2
            ) if annotations else 0,
        },
        "generated_at": _now(),
    }


# ---- Label Studio 视频时间轴标注导出 ---------------------------------------

def _ls_item(idx: str, from_name: str, to_name: str, type_name: str,
             labels: list[str], ranges: list[dict]) -> dict:
    """构造一条 Label Studio result 项（TimelineLabels / Choices 等）。

    注意：Label Studio 官方文档要求 TimelineLabels 的 result value 键为
    ``timelinelabels``（数组），与控件类型名一致；用 ``labels`` 会导致 LS
    时间轴上显示 "Empty"。
    """
    return {
        "value": {"ranges": ranges, "timelinelabels": labels},
        "id": idx,
        "from_name": from_name,
        "to_name": to_name,
        "type": type_name,
    }


def _xml_escape(v: str) -> str:
    """XML 属性/文本转义，避免标签值里的 & < > " 破坏 Label Studio 配置。"""
    return (str(v).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# 各时间轴维度的固定配色（统一模板，保证跨视频一致）
_LABEL_COLORS = {
    "camera": "#3498db", "scale": "#9b59b6", "angle": "#e67e22",
    "lens": "#16a085", "composition": "#1abc9c", "exposure": "#f39c12",
    "light_position": "#f1c40f", "light_quality": "#d4ac0d",
    "light_source": "#f7dc6f", "tone": "#c0392b", "color_temp": "#e74c3c",
    "saturation": "#e84393", "color_scheme": "#fd79a8", "people": "#00b894",
    "text_in_frame": "#636e72", "issue": "#e74c3c",
}


def _label_color(name: str) -> str:
    return _LABEL_COLORS.get(name, "#95a5a6")


def _tier_from_proxy(v) -> str | None:
    """把 0-5 的 aesthetic_proxy 映射成 RLHF 质量梯队。"""
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x >= 4.0:
        return "S"
    if x >= 3.0:
        return "A"
    if x >= 2.0:
        return "B"
    return "C"


def to_label_studio(report: dict, video_url: str = "", schema=None, fps: int = 30) -> dict:
    """完整报告 → Label Studio Video TimelineLabels 导入格式（统一模板预标注）。

    时间轴标签按镜头生成（运镜/景别/构图/曝光/色温/问题），评分与 RLHF 偏好作为
    整片预标注（Rating/Choices）。所有标签值都归一化到统一 schema 的规范值，
    未命中规范值的不强行预填，留给人工补。

    注意：LS 1.23 的 TimelineRegion 模型要求 ranges 的 start/end 为**整数帧号**
    （mobx-state-tree: types.maybeNull(types.integer)），因此必须按 fps 把秒转成帧。
    """
    if schema is None:
        schema = load_schema()
    if not fps or fps <= 0:
        fps = schema.get("default_framerate", 30)
    video_id = report.get("video_id", "")
    shots = report.get("shots", [])
    result = []
    for i, s in enumerate(shots):
        # 秒 → 整数帧号；LS TimelineRange 只接受整数（frame index）
        start_frame = int(round(float(s.get("start_sec", 0)) * fps))
        end_frame = int(round(float(s.get("end_sec", 0)) * fps))
        rng = {"start": start_frame, "end": end_frame}
        sid = str(s.get("shot_id", i))

        cam = normalize_camera(s.get("camera_move"))
        if cam:
            result.append(_ls_item(f"cam_{sid}", "camera", "video",
                                   "timelinelabels", [cam], [rng]))
        if s.get("shot_scale"):
            result.append(_ls_item(f"sc_{sid}", "scale", "video",
                                   "timelinelabels", [_xml_escape(s["shot_scale"])], [rng]))
        if s.get("composition"):
            result.append(_ls_item(f"co_{sid}", "composition", "video",
                                   "timelinelabels", [_xml_escape(s["composition"])], [rng]))
        light = s.get("lighting", {}) or {}
        if light.get("exposure"):
            result.append(_ls_item(f"ex_{sid}", "exposure", "video",
                                   "timelinelabels", [_xml_escape(light["exposure"])], [rng]))
        color = s.get("color", {}) or {}
        if color.get("color_temp"):
            ct = normalize_color_temp(color["color_temp"])
            if ct:
                result.append(_ls_item(f"ct_{sid}", "color_temp", "video",
                                       "timelinelabels", [ct], [rng]))
        issues = (s.get("compliance", {}) or {}).get("reasons", [])
        if issues:
            result.append(_ls_item(f"is_{sid}", "issue", "video",
                                   "timelinelabels",
                                   [_xml_escape(x) for x in issues if x], [rng]))

    # ---- 整片评分预填（0-5 → 1-10，跨镜头取均值）----
    per_shot_scores = [s.get("scores", {}) or {} for s in shots]
    for src, dst in _SCORE_MAP.items():
        vals = [float(ss.get(src)) for ss in per_shot_scores
                if isinstance(ss.get(src), (int, float))]
        if vals:
            result.append({"value": {"amount": scale_0_5_to_1_10(sum(vals) / len(vals))},
                           "id": f"rt_{dst}", "from_name": dst,
                           "to_name": "video", "type": "rating"})
    overall_vals = [float(s.get("aesthetic_proxy")) for s in shots
                    if isinstance(s.get("aesthetic_proxy"), (int, float))]
    if overall_vals:
        ov = scale_0_5_to_1_10(sum(overall_vals) / len(overall_vals))
        if ov is not None:
            result.append({"value": {"amount": ov}, "id": "rt_a_overall",
                           "from_name": "a_overall", "to_name": "video",
                           "type": "rating"})

    # ---- RLHF 偏好预填 ----
    avg_proxy = (sum(overall_vals) / len(overall_vals)) if overall_vals else None
    tier = _tier_from_proxy(avg_proxy)
    if tier:
        result.append({"value": {"choices": [tier]}, "id": "ch_tier",
                       "from_name": "quality_tier", "to_name": "video",
                       "type": "choices"})
    needs_review = any((s.get("compliance", {}) or {}).get("verdict") != "compliant"
                       for s in shots)
    result.append({"value": {"choices": ["是" if needs_review else "否"]},
                   "id": "ch_re", "from_name": "reannotate",
                   "to_name": "video", "type": "choices"})
    faces = sum((s.get("compliance", {}) or {}).get("faces_detected", 0) or 0
                for s in shots)
    if faces:
        result.append({"value": {"choices": ["有路人"]}, "id": "ch_fp",
                       "from_name": "face_privacy", "to_name": "video",
                       "type": "choices"})

    return {
        "data": {"video_url": video_url or report.get("source", "")},
        "annotations": [{"result": result}],
        "meta": {"video_id": video_id, "exported_by": "lychee",
                 "schema": schema.get("name")},
    }


def to_label_studio_config(schema=None, fps: int = 30) -> str:
    """根据统一标注 schema 生成 Label Studio 项目配置 XML（全量标签面板）。

    不再按单条视频的检测结果动态裁剪：同一 schema 下所有视频打开都是一致的模板，
    满足「数据集统一规格」的要求。fps 用于 Video 标签的帧率提示
    （数据集模式通常取 schema 默认 30）。
    """
    if schema is None:
        schema = load_schema()
    if not fps or fps <= 0:
        fps = schema.get("default_framerate", 30)
    legend = schema.get("legend_1_10", "")
    lines = ['<View>']
    if legend:
        lines.append(f'  <Header value="{_xml_escape(legend)}"/>')
    lines.append(f'  <Video name="video" value="$video_url" framerate="{int(fps)}" height="400"/>')

    # 层1 时间轴标签（全量规范值，跨视频一致）
    for cat in schema.get("timeline_labels", []):
        lines.append(f'  <TimelineLabels name="{cat["name"]}" toName="video" '
                     f'showInline="true" timeUnit="frame">')
        for v in cat.get("values", []):
            lines.append(f'    <Label value="{_xml_escape(v)}" background="{_label_color(cat["name"])}"/>')
        lines.append('  </TimelineLabels>')

    # 层2/3 评分（按 group 分组，1-10）
    scores = schema.get("scores", [])
    if scores:
        lines.append('  <Header value="美学评分（1-10，对照上方锚点）"/>')
        for s in scores:
            if (s.get("group") or "").startswith("美学"):
                lines.append(f'  <Rating name="{s["name"]}" toName="video" maxRating="10"/>')
        lines.append('  <Header value="技术质量评分（1-10）"/>')
        for s in scores:
            if (s.get("group") or "") == "技术":
                lines.append(f'  <Rating name="{s["name"]}" toName="video" maxRating="10"/>')

    # 层4/5/6 选择类
    choices = schema.get("choices", [])
    if choices:
        lines.append('  <Header value="合规 / RLHF 偏好 / 剪辑"/>')
        for c in choices:
            lines.append(f'  <Choices name="{c["name"]}" toName="video" showInline="true">')
            for v in c.get("values", []):
                lines.append(f'    <Choice value="{_xml_escape(v)}"/>')
            lines.append('  </Choices>')

    # 文本
    for t in schema.get("texts", []):
        lines.append(f'  <TextArea name="{t["name"]}" toName="video"/>')

    lines.append('</View>')
    return "\n".join(lines) + "\n"


def to_csv(report: dict) -> str:
    """完整报告 → 扁平 CSV（一行一镜头）。"""
    video_id = report.get("video_id", "")
    shots = report.get("shots", [])

    buf = io.StringIO()
    fieldnames = [
        "video_id", "shot_id", "start_sec", "end_sec", "duration_sec",
        "content_description", "camera_motion", "shot_scale", "composition",
        "mood", "dominant_color_1", "dominant_color_2", "dominant_color_3",
        "saturation", "brightness", "color_temp", "contrast",
        "exposure", "dynamic_range",
        "compliance_verdict", "faces_detected", "compliance_issues",
        "aesthetic_proxy", "aesthetic_raw",
    ]
    # 加所有评分维度
    for dim in SCORE_DIM_NAMES:
        fieldnames.append(f"score_{dim}")

    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()

    for s in shots:
        ann = build_shot_annotation(s, video_id, report=report)
        l = ann["labels"]
        row = {
            "video_id": video_id,
            "shot_id": ann["shot_id"],
            "start_sec": ann["start_sec"],
            "end_sec": ann["end_sec"],
            "duration_sec": ann["duration_sec"],
            "content_description": l["content_description"],
            "camera_motion": l["camera_motion"],
            "shot_scale": l["shot_scale"],
            "composition": l["composition"],
            "mood": l["mood"],
            "dominant_color_1": l["color"]["dominant_colors_hex"][0] if len(l["color"]["dominant_colors_hex"]) > 0 else "",
            "dominant_color_2": l["color"]["dominant_colors_hex"][1] if len(l["color"]["dominant_colors_hex"]) > 1 else "",
            "dominant_color_3": l["color"]["dominant_colors_hex"][2] if len(l["color"]["dominant_colors_hex"]) > 2 else "",
            "saturation": l["color"]["saturation"],
            "brightness": l["color"]["brightness"],
            "color_temp": l["color"]["color_temperature"],
            "contrast": l["color"]["contrast"],
            "exposure": l["lighting"]["exposure"],
            "dynamic_range": l["lighting"]["dynamic_range"],
            "compliance_verdict": l["compliance"]["verdict"],
            "faces_detected": l["compliance"]["faces_detected"],
            "compliance_issues": "; ".join(l["compliance"]["issues"]),
            "aesthetic_proxy": l["quality_scores"]["aesthetic_proxy"],
            "aesthetic_raw": l["quality_scores"]["aesthetic_raw"],
        }
        for dim in SCORE_DIM_NAMES:
            d = l["quality_scores"]["dimensions"].get(dim)
            row[f"score_{dim}"] = d["score"] if d else ""
        writer.writerow(row)

    return buf.getvalue()


def to_csv_batch(reports: list) -> str:
    """多个报告 → 单一扁平 CSV（合并全部镜头，含 video_id 区分；表头只出现一次）。"""
    if not reports:
        return to_csv({})
    chunks = []
    for i, r in enumerate(reports):
        text = to_csv(r)
        if i == 0:
            chunks.append(text.rstrip("\n"))
        else:
            lines = text.splitlines()
            if lines:
                chunks.append("\n".join(lines[1:]))   # 去掉重复表头
    return "\n".join(chunks) + "\n"


def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "output/reports/_multishot_test.json"
    report = json.load(open(path, encoding="utf-8"))
    print(f"视频: {report.get('video_id')} | 镜头数: {report.get('shot_count')}")

    j = to_annotation_json(report)
    print("\n=== JSON 标注 ===")
    print(f"总镜头: {j['summary']['total_shots']}")
    print(f"合规: {j['summary']['compliant']} | 待复核: {j['summary']['needs_review']} | 拦截: {j['summary']['blocked']}")
    print(f"平均美学分: {j['summary']['avg_aesthetic']}")
    print("\n首镜头标注样例:")
    a = j["annotations"][0]
    print(f"  shot_id: {a['shot_id']} ({a['start_sec']}-{a['end_sec']}s)")
    print(f"  内容: {a['labels']['content_description']}")
    print(f"  运镜: {a['labels']['camera_motion']}")
    print(f"  色彩: {a['labels']['color']['dominant_colors_hex']}")
    print(f"  合规: {a['labels']['compliance']['verdict']}")
    print(f"  美学代理分: {a['labels']['quality_scores']['aesthetic_proxy']}")
    print(f"  待人工复核: {a['needs_review']}")

    print("\n=== CSV 前 3 行 ===")
    csv_text = to_csv(report)
    for line in csv_text.strip().split("\n")[:4]:
        print(f"  {line[:120]}")
