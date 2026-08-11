# -*- coding: utf-8 -*-
"""跨章人物一致性校验：把「随机生成」变成「带锁的生成」。

为什么需要它
------------
每一章分镜都是独立随机生成，若不锚定，2000 章必然越跑越散（第二章和第一章
大相庭径）。解决办法不是「生成得更准」，而是「冻结一份源 + 自动校验漂移」：

  * 冻结视觉源（cast）：人工定稿的角色参考卡（图片 + 锁定描述），全程复用。
  * 文字形象库（bible）：整本角色外貌设定，已在 build_full_bible 产出。

两类校验
--------
1. 描述一致性（无需图片，立即可用）：
   校验每镜 prompt 里出现的角色，是否仍引用「锁定 bible」里的固定外貌描述。
   一旦某章生成把「小不点」画成了「持剑少年」，立即抓出来。
2. 视觉一致性（生成帧后）：
   把生成的关键帧与「冻结角色卡」用本地 VLM（qwen2.5vl:3b）比对，
   输出漂移分 0-100 + 问题清单，低于阈值判不达标、需打回重生成。

另含风格锁检查：每镜是否仍带国漫/赛璐璐风格尾巴，防止画风漂移。
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.request
from typing import Optional

from ..loader import NOVEL_DIR
from ._common import BIBLE_DIR, STORYBOARD_DIR
from .bible import load_bible


# ---- 目录与配置 ----------------------------------------------------------
CAST_DIR = os.path.join(NOVEL_DIR, "cast")
os.makedirs(CAST_DIR, exist_ok=True)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
VLM_MODEL = os.environ.get("VLM_MODEL", "qwen2.5vl:3b")
_VLM_MAX_SIDE = int(os.environ.get("VLM_MAX_SIDE", "320"))

# 风格锁关键词：任一镜 prompt 必须命中其一，否则判风格漂移
_STYLE_KW = ["国漫", "赛璐璐", "动漫", "动画", "donghua", "cel shading"]

# 描述覆盖度低于该值即判「该角色外貌漂移」
_COVERAGE_GATE = 0.5


# ---- 冻结角色卡（视觉锚） -------------------------------------------------
def _cast_path(novel: str) -> str:
    safe = re.sub(r"[^\w\u4e00-\u9fff-]", "_", novel)
    return os.path.join(CAST_DIR, f"{safe}.json")


def load_cast(novel: str) -> dict:
    """读取该书已冻结的角色卡：{角色名: {image, desc, locked}}。"""
    p = _cast_path(novel)
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_cast(novel: str, cast: dict) -> None:
    os.makedirs(CAST_DIR, exist_ok=True)
    json.dump(cast, open(_cast_path(novel), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


def lock_cast_card(novel: str, name: str, image_path: str = "",
                   desc_zh: str = "", locked: bool = True) -> dict:
    """冻结一张角色参考卡：记录图片路径 + 锁定描述，供后续每章复用。

    角色卡是「投喂给工业视频模型角色参考功能的统一锚」——你把它（参考图 +
    锁定描述）交给可灵/即梦的「角色参考」，模型就会在 2000 章里继承同一张脸，
    而不是每章重新抽卡。image 可先空，待你放入 pilot 首帧或指定参考图再补。

    desc_zh 留空时自动取 bible 里的文字形象，省得手抄。
    """
    cast = load_cast(novel)
    if not desc_zh:
        b = load_bible(novel)
        desc_zh = (b.get(name) or {}).get("zh", "")
    prev = cast.get(name, {})
    cast[name] = {
        "image": image_path or prev.get("image", ""),
        "desc": desc_zh or prev.get("desc", ""),
        "locked": locked,
        "source": prev.get("source", "manual"),
    }
    save_cast(novel, cast)
    return cast


def freeze_from_bible(novel: str, characters: Optional[list] = None) -> dict:
    """一键把全书（或指定）角色从 bible 冻结成文字锚角色卡。

    这是「委托策略」下的关键一步：我们不需要自己生图，只要把每个角色的
    锁定文字形象固化成 cast 文件，它就是后续投喂视频模型角色参考的权威依据。
    image 留空——等你从 pilot 首帧挑一张好图、或指定参考图后，再用
    lock_cast_card 把图片补上即可。

    返回 {frozen: [新冻结的角色名], cast: 全量}。
    """
    bible = load_bible(novel)
    if not bible:
        return {"frozen": [], "cast": {}}
    cast = load_cast(novel)
    chars = characters or list(bible.keys())
    frozen = []
    for name in chars:
        if name not in bible:
            continue
        entry = bible[name] or {}
        desc = entry.get("zh") or entry.get("desc") or ""
        prev = cast.get(name, {})
        # 仅当该角色尚无 desc 时才写入，避免覆盖用户已补的参考图/描述
        if not prev.get("desc"):
            cast[name] = {
                "image": prev.get("image", ""),
                "desc": desc,
                "locked": True,
                "source": "bible",
            }
            frozen.append(name)
    save_cast(novel, cast)
    return {"frozen": frozen, "cast": cast}


# ---- 描述抽取与比对 -------------------------------------------------------
def _featured_chars(shot: dict) -> list[str]:
    """本镜出场角色（从 characters 字段拆出，去掉称谓括号与修饰）。"""
    c = shot.get("characters") or ""
    out = []
    for x in re.split(r"[,，、]", c):
        x = re.sub(r"[（(][^）)]*[）)]", "", x).strip()  # 整段去括号，如「中年男子（指点者）」
        if x:
            out.append(x)
    return out


def _extract_shot_descriptors(shot: dict) -> dict:
    """从单镜 prompt 抽 {角色名: 描述}，兼容旧 prompt（NAME——DESC）
    与新 video_prompt_cn（角色：NAME——DESC / NAME：DESC）。

    旧格式示例：『角色形象（每镜必须保持一致）：中年男子——肌体强健如虎豹...』
    新格式示例：『角色：小不点——四五岁的小男孩...』
    """
    text = shot.get("video_prompt_cn") or shot.get("prompt") or ""
    out: dict = {}
    # 角色名（中文/字母/·，1-12字）后接 —— 或 ：/:，再接描述（到句末或换行）
    pat = re.compile(r"([\u4e00-\u9fffA-Za-z·]{1,12})\s*(?:[—–_-]{1,2}|[:：])\s*([^。；;\n]{4,120})")
    for m in pat.finditer(text):
        name = m.group(1).strip().strip("—–_-：: ")
        desc = m.group(2).strip()
        if name and desc:
            out[name] = desc
    return out


def _resolve_main(name: str, bible: dict) -> Optional[str]:
    """把出场名（可能带称谓/括号）解析成 bible 里的主名；找不到返回 None。"""
    n = name.strip().strip("（）() ")
    if n in bible:
        return n
    for k in bible:
        if k and (k in n or n in k):
            return k
    return None


def _trait_keywords(desc: str) -> list[str]:
    """从 bible 描述里抽 2-4 字关键特征词，用于覆盖度比对。"""
    segs = re.split(r"[，,。.；;、（）()\s]+", desc or "")
    return [s for s in segs if 2 <= len(s) <= 4]


def _coverage(bible_desc: str, shot_desc: str) -> float:
    """角色外貌覆盖度：bible 关键特征词在 shots 描述中的命中比例。"""
    if not bible_desc:
        return 1.0
    traits = _trait_keywords(bible_desc)
    if not traits:
        return 1.0
    hit = sum(1 for t in traits if t in (shot_desc or ""))
    return hit / len(traits)


# ---- 1) 描述一致性 -------------------------------------------------------
def check_descriptive(novel: str, storyboard: dict) -> dict:
    """逐镜校验：出场角色的外貌描述是否仍锁定在 bible 上。"""
    bible = load_bible(novel)
    shots = storyboard.get("shots") or []
    issues_per_shot: list[dict] = []
    total_issues = 0
    for i, s in enumerate(shots):
        feat = _featured_chars(s)
        embedded = _extract_shot_descriptors(s)
        issues = []
        for name in feat:
            main = _resolve_main(name, bible)
            if not main or main not in bible:
                continue
            bdesc = bible[main].get("zh", "")
            sdesc = embedded.get(name) or embedded.get(main) or ""
            cov = _coverage(bdesc, sdesc) if sdesc else 0.0
            if cov < _COVERAGE_GATE:
                issues.append({
                    "character": name,
                    "coverage": round(cov, 2),
                    "bible_desc": bdesc,
                    "shot_desc": sdesc or "（本镜未嵌入该角色外貌描述）",
                })
        issues_per_shot.append({"shot_id": s.get("shot_id", i + 1), "issues": issues})
        total_issues += len(issues)

    n = len(shots) or 1
    score = max(0, 100 - total_issues * (100 / max(n, 5)))
    return {
        "level": "descriptive",
        "score": round(score, 1),
        "n_shots": len(shots),
        "total_issues": total_issues,
        "issues_per_shot": issues_per_shot,
    }


# ---- 风格锁检查 ----------------------------------------------------------
def check_style_lock(storyboard: dict) -> dict:
    """逐镜检查是否仍带国漫/赛璐璐风格尾巴，防画风漂移。"""
    shots = storyboard.get("shots") or []
    drift = []
    for i, s in enumerate(shots):
        text = (s.get("video_prompt_cn") or s.get("prompt") or "")
        text += " " + (s.get("video_prompt_anime") or "")
        if not any(kw.lower() in text.lower() for kw in _STYLE_KW):
            drift.append(s.get("shot_id", i + 1))
    score = round(100 * (1 - len(drift) / max(len(shots), 1)), 1)
    return {"level": "style_lock", "score": score,
            "drifted_shots": drift, "total_issues": len(drift)}


# ---- 2) 视觉一致性（本地 VLM） ------------------------------------------
def image_to_b64(path: str, max_side: int = _VLM_MAX_SIDE) -> Optional[str]:
    """读图并 base64（缩小到 max_side 防 8GB Mac OOM）。"""
    try:
        import cv2
    except Exception:
        try:
            from PIL import Image
            import io
            img = Image.open(path).convert("RGB")
            w, h = img.size
            if max(w, h) > max_side:
                sc = max_side / max(w, h)
                img = img.resize((int(w * sc), int(h * sc)))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=82)
            return base64.b64encode(buf.getvalue()).decode()
        except Exception:
            return None
    import numpy as np
    frame = cv2.imread(path)
    if frame is None:
        return None
    h, w = frame.shape[:2]
    if max(h, w) > max_side:
        sc = max_side / max(h, w)
        frame = cv2.resize(frame, (int(w * sc), int(h * sc)))
    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return base64.b64encode(buf).decode()


def vlm_consistency(frame_b64: str, ref_b64: str, character: str) -> dict:
    """用本地 VLM 比对生成帧 vs 角色卡，返回 {same, score, issues}。"""
    prompt = (
        f"第一张是【生成画面】，第二张是【角色参考卡】。请判断两者中的角色"
        f"「{character}」是否为同一人物——重点看脸型、发型、服饰、体型、配色是否一致。\n"
        "只输出一个 JSON 对象，字段："
        '{"same": true/false, "score": 0-100, "issues": ["问题1","问题2"]}。'
        "不要输出其他文字。"
    )
    payload = {
        "model": VLM_MODEL,
        "stream": False,
        "format": "json",
        "messages": [{"role": "user", "content": prompt,
                      "images": [frame_b64, ref_b64]}],
        "options": {"temperature": 0.2, "num_predict": 300},
    }
    try:
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/chat", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        resp = json.loads(urllib.request.urlopen(req, timeout=180).read())
        raw = resp["message"]["content"].strip()
        m = re.search(r"\{.*\}", raw, re.S)
        obj = json.loads(m.group(0)) if m else {}
    except Exception as e:
        return {"same": False, "score": 0, "issues": [f"VLM 调用失败：{e}"]}
    if not isinstance(obj, dict):
        return {"same": False, "score": 0, "issues": ["VLM 返回无法解析"]}
    try:
        obj["score"] = float(obj.get("score", 0))
    except Exception:
        obj["score"] = 0.0
    obj.setdefault("same", obj.get("score", 0) >= 70)
    obj.setdefault("issues", [])
    return obj


def check_visual(novel: str, storyboard: dict, frame_dir: str) -> dict:
    """逐镜：用冻结角色卡比对该镜代表帧（frame_dir 下按 shot_id 命名）。

    frame_dir 约定：每个镜头一张代表帧，文件名含 shot_id（如 003.jpg）。
    无角色卡或无帧时该镜跳过，不影响其它镜。
    """
    cast = load_cast(novel)
    shots = storyboard.get("shots") or []
    per_shot = []
    checked = 0
    for i, s in enumerate(shots):
        sid = s.get("shot_id", i + 1)
        feat = _featured_chars(s)
        # 找该镜某角色对应的冻结卡
        card = None
        char_name = None
        for name in feat:
            main = _resolve_main(name, cast)
            if main and cast.get(main, {}).get("image"):
                card = cast[main]["image"]
                char_name = name
                break
        if not card or not os.path.exists(card):
            per_shot.append({"shot_id": sid, "skipped": "无冻结角色卡"})
            continue
        # 找代表帧
        frame_path = None
        for fn in os.listdir(frame_dir):
            if str(sid) in fn and fn.lower().endswith((".jpg", ".png", ".jpeg")):
                frame_path = os.path.join(frame_dir, fn)
                break
        if not frame_path:
            per_shot.append({"shot_id": sid, "skipped": "无代表帧"})
            continue
        fb = image_to_b64(frame_path)
        rb = image_to_b64(card)
        if not fb or not rb:
            per_shot.append({"shot_id": sid, "skipped": "图像读取失败"})
            continue
        res = vlm_consistency(fb, rb, char_name)
        per_shot.append({"shot_id": sid, "character": char_name, **res})
        checked += 1
    return {"level": "visual", "checked": checked, "per_shot": per_shot}


# ---- 综合：一章一报告 -----------------------------------------------------
def check_chapter(novel: str, storyboard: dict,
                  frame_dir: Optional[str] = None) -> dict:
    """跑完描述 + 风格 +（可选）视觉，产出一章一致性报告与达标判定。"""
    desc = check_descriptive(novel, storyboard)
    style = check_style_lock(storyboard)
    report = {
        "novel": novel,
        "chapter": storyboard.get("chapter_index"),
        "descriptive": desc,
        "style_lock": style,
    }
    if frame_dir and os.path.isdir(frame_dir):
        report["visual"] = check_visual(novel, storyboard, frame_dir)
    # 综合分：描述为主，风格加权
    score = round(0.7 * desc["score"] + 0.3 * style["score"], 1)
    report["overall_score"] = score
    report["pass"] = score >= 70
    report["gate"] = "达标入库" if report["pass"] else "不达标 → 打回重生成"
    return report


def check_chapter_range(novel: str, rng: str,
                        frame_dir: Optional[str] = None) -> dict:
    """按章节区间 rng（如 '1' 或 '1-5'）载入分镜文件并校验。"""
    safe = re.sub(r"[^\w\u4e00-\u9fff-]", "_", novel)
    p = os.path.join(STORYBOARD_DIR, f"{safe}_ch{rng}.json")
    if not os.path.exists(p):
        raise FileNotFoundError(f"分镜不存在：{p}")
    with open(p, encoding="utf-8") as f:
        return check_chapter(novel, json.load(f), frame_dir=frame_dir)


if __name__ == "__main__":
    import sys
    nv = sys.argv[1] if len(sys.argv) > 1 else "完美世界"
    rng = sys.argv[2] if len(sys.argv) > 2 else "1"
    rep = check_chapter_range(nv, rng)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
