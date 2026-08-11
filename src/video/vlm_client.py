#!/usr/bin/env python3
"""VLM 画面内容理解（A1 内容描述 / 导演级标签）。

两个后端：
  * "ollama"（默认）→ 本地 Ollama 多模态模型 qwen2.5vl:3b，全程在本机运行，
        无需网络/HF 下载。这是「读取视频画面与内容」的诚实本地实现。
  * "blip"   → Salesforce BLIP（需 HF 下载，本机受限；保留给其他环境做离线兜底）。

运镜（camera_move）不在此处理——它是单帧 VLM 看不到的时序信号，由
camera_motion.py（光流）负责该维度。一次 director_tags 调用同时返回
内容描述/景别/构图/情绪/美学分，减少对小模型的调用次数。
"""
from __future__ import annotations

import os
import re
import cv2
import json
import base64
import urllib.request

# ---- Ollama config -------------------------------------------------------
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/chat")
DEFAULT_OLLAMA_MODEL = os.environ.get("VLM_MODEL", "qwen2.5vl:3b")
# keep frames small so the 8GB Mac doesn't OOM on cold-start
_MAX_SIDE = int(os.environ.get("VLM_MAX_SIDE", "320"))


def _resize_max(frame_bgr, max_side: int = _MAX_SIDE):
    h, w = frame_bgr.shape[:2]
    if max(h, w) <= max_side:
        return frame_bgr
    scale = max_side / max(h, w)
    return cv2.resize(frame_bgr, (int(w * scale), int(h * scale)))


def _frame_to_b64(frame_bgr, max_side: int = _MAX_SIDE) -> str:
    small = _resize_max(frame_bgr, max_side)
    _, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return base64.b64encode(buf).decode()


def caption_image_ollama(frame_bgr, prompt: str | None = None,
                         model: str = DEFAULT_OLLAMA_MODEL,
                         max_side: int = _MAX_SIDE,
                         num_predict: int = 96) -> str:
    """Caption a single BGR frame using a local Ollama vision model."""
    if prompt is None:
        prompt = ("用一句话中文描述这张画面：主体、场景、动作、氛围。"
                  "只输出描述本身，不要解释。")
    b64 = _frame_to_b64(frame_bgr, max_side)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt, "images": [b64]}],
        "stream": False,
        "options": {"num_predict": num_predict},
    }
    req = urllib.request.Request(
        OLLAMA_URL, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req, timeout=180).read())
    return resp["message"]["content"].strip()


# ---- BLIP fallback (offline-capable elsewhere, blocked on this box) -------
_MODEL = None
_PROCESSOR = None
DEVICE = "cpu"


def _load_blip(model_name: str = "Salesforce/blip-image-captioning-base"):
    global _MODEL, _PROCESSOR
    if _MODEL is not None:
        return _MODEL, _PROCESSOR
    from transformers import BlipProcessor, BlipForConditionalGeneration
    _PROCESSOR = BlipProcessor.from_pretrained(model_name)
    _MODEL = BlipForConditionalGeneration.from_pretrained(model_name)
    _MODEL.eval()
    return _MODEL, _PROCESSOR


def caption_image_blip(frame_bgr, model_name: str = "Salesforce/blip-image-captioning-base") -> str:
    model, processor = _load_blip(model_name)
    from PIL import Image
    import torch
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    img = Image.fromarray(rgb)
    inputs = processor(img, return_tensors="pt")
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=30)
    return processor.decode(out[0], skip_special_tokens=True)


# ---- unified entrypoint --------------------------------------------------
def caption_image(frame_bgr, backend: str = "ollama", **kw) -> str:
    if backend == "ollama":
        return caption_image_ollama(frame_bgr, **kw)
    if backend == "blip":
        return caption_image_blip(frame_bgr, **kw)
    raise ValueError(f"unknown backend: {backend}")


def caption_video(video_path: str, sec: float,
                  backend: str = "ollama", **kw) -> str:
    """Caption the frame at `sec` seconds of a video."""
    from .video_utils import frame_at
    frame = frame_at(video_path, sec)
    if frame is None:
        return ""
    return caption_image(frame, backend=backend, **kw)


def director_tags_ollama(frame_bgr, model: str = DEFAULT_OLLAMA_MODEL,
                         max_side: int = _MAX_SIDE,
                         num_predict: int = 180) -> str:
    """One VLM call that returns structured director tags for a frame.

    Returns a single-line string of the form:
      描述：...|景别：...|构图：...|情绪：...|美学：N
    Parse with `_parse_director`.
    """
    prompt = (
        "请分析这张视频帧。严格按以下格式用一行中文回答，字段间用竖线「|」分隔，"
        "不要换行、不要多余解释：\n"
        "描述：<一句话描述主体/场景/动作/氛围>|"
        "景别：<远景/全景/中景/近景/特写>|"
        "构图：<三分法/居中/对称/引导线/其他>|"
        "情绪：<2-4字中文情绪>|"
        "美学：<1到10的整数>"
    )
    b64 = _frame_to_b64(frame_bgr, max_side)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt, "images": [b64]}],
        "stream": False,
        "options": {"num_predict": num_predict},
    }
    req = urllib.request.Request(
        OLLAMA_URL, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req, timeout=180).read())
    return resp["message"]["content"].strip()


def _parse_director(text: str) -> dict:
    """Parse the `描述：...|景别：...|...` string into a dict.

    Falls back gracefully: a bare description (no pipes) becomes
    content_caption; missing fields stay None.
    """
    out = {"content_caption": None, "shot_scale": None,
           "composition": None, "mood": None, "aesthetic_score": None}
    if not text:
        return out
    text = text.strip()
    if "|" not in text:
        out["content_caption"] = text
        return out
    for part in text.split("|"):
        if ":" not in part and "：" not in part:
            continue
        k, _, v = part.partition("：" if "：" in part else ":")
        k, v = k.strip(), v.strip()
        if k in ("描述", "caption"):
            out["content_caption"] = v or None
        elif k in ("景别", "scale", "shot_scale"):
            out["shot_scale"] = v or None
        elif k in ("构图", "composition"):
            out["composition"] = v or None
        elif k in ("情绪", "mood"):
            out["mood"] = v or None
        elif k in ("美学", "aesthetic", "score"):
            m = re.search(r"\d+", v)
            if m:
                out["aesthetic_score"] = max(1, min(10, int(m.group())))
    if not out["content_caption"]:
        out["content_caption"] = text  # safety: keep something readable
    return out


def director_tags_video(video_path: str, sec: float,
                        backend: str = "ollama",
                        tries: int = 2, **kw) -> dict:
    """VLM director tags for the frame at `sec`; retries on frame/VLM failure.

    Tries the mid frame first, then start/end frames as fallbacks so a
    single unreadable frame does not silently drop the caption.
    Returns the parsed dict (all None on total failure).
    """
    from .video_utils import frame_at
    for attempt_sec in (sec, max(0.0, sec - 0.5), sec + 0.5):
        frame = frame_at(video_path, attempt_sec)
        if frame is None:
            continue
        for _ in range(tries):
            try:
                raw = director_tags_ollama(frame, **kw)
                parsed = _parse_director(raw)
                if parsed.get("content_caption") or parsed.get("shot_scale"):
                    return parsed
            except Exception:
                continue
    return {"content_caption": None, "shot_scale": None,
            "composition": None, "mood": None, "aesthetic_score": None}


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python -m src.video.vlm_client <video> [sec] [backend]")
        sys.exit(1)
    video = sys.argv[1]
    sec = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
    backend = sys.argv[3] if len(sys.argv) > 3 else "ollama"
    print(json.dumps({"video": video, "sec": sec, "backend": backend,
                      "caption": caption_video(video, sec, backend)},
                     ensure_ascii=False))
