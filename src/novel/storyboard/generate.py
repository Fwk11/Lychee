# -*- coding: utf-8 -*-
"""分镜生成编排：单章 / 连续多章（带衔接）/ 整部连续剧分集方案。

对外公开 API（由包 __init__ 再导出）：
- storyboard_chapter / storyboard_batch：端到端生成并落盘
- series_plan / get_series_plan / make_series_plan：整部→连续剧分集
- make_storyboard：单章 → 分镜脚本（不落盘）
"""
from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

from ..analyzer import _chat, analyze_novel, get_analysis
from ...api.tasks import report as _report
from ..loader import load_novel
from ._common import (
    SERIES_DIR,
    STORYBOARD_DIR,
    _EXAMPLE_SHOT,
    _SHOT_SCHEMA_HINT,
    _clean_text,
)
from .bible import (
    _alias_merge,
    _alias_merge_by_text,
    build_character_bible,
    load_bible,
    save_bible,
)
from .compose import attach_all
from .continuity import (
    _chapter_tail_state,
    _extract_continuity,
    _load_continuity,
    _merge_bible,
    _prior_block,
    _save_continuity,
)
from .prompts import (
    _build_dialogue_prompt,
    _build_director_note_prompt,
    _build_rich_prompt,
    _build_scene_prompt,
    _build_skeleton_prompt,
)
from .shots import (
    _collect_names,
    _dynamic_shot_count,
    _parse_characters,
    _parse_dialogue,
    _parse_rich,
    _parse_scene,
    _parse_shots,
    _parse_skeleton,
    _segment_text,
)


def _dup_ratio(a: str, b: str) -> float:
    """相邻镜头台词/剧情的 2-gram 重合率，用于检测 3b 照抄。返回 0~1。"""
    def _ng(s: str, n: int = 2):
        s = re.sub(r"[\s，。；、,.;：:！!？?]", "", s or "")
        return {s[i:i + n] for i in range(max(0, len(s) - n + 1))}
    A, B = _ng(a), _ng(b)
    if not A or not B:
        return 0.0
    return len(A & B) / min(len(A), len(B))


# ---- 原文对白抽取（台词 100% 来自原文，不让模型编造）---------------------------
# 说话动词。注意：正则交替按从左到右首次命中，多字动词必须排在单字「道」「说」之前，
# 否则「老人喊道」会被切成说话人「老人喊」+ 动词「道」。
_DLG_VERB = (r'(?:回应道|开口道|高声道|轻声道|接口道|冷声道|自言自语道|'
             r'说道|喊道|笑道|怒道|喝道|叫道|叹道|问道|沉声道|冷笑道|低声道|'
             r'苦笑道|急道|应道|答道|反问道|嘀咕道|笑骂道|骂道|'
             r'低语|沉声|冷笑|笑骂|询问|反问|嘟囔|嘀咕|'
             r'道|说|问|曰|喊|叫|笑|叹)')
_DLG_QUOTE = r'["“「『](.*?)["”」』]'
# 叙述连接词/副词，绝不可能是说话人，命中则丢弃整条候选
_DLG_BLACK = {
    "而后又", "这时", "于是", "只见", "接着", "忽然", "随后", "就", "便", "却",
    "那", "这", "此时", "当下", "原来", "果然", "不过", "其实", "总之", "其中",
    "与此同时", "说罢", "至此", "当下", "此刻", "瞬间", "片刻", "稍后", "次日",
    "当天", "当日", "当时", "不久", "很快", "正在", "突然", "猛地", "半晌",
}


# 引号包裹的一段话（中文小说对白的唯一可靠标志），长度不设上限——原文说多长就搬多长
_QUOTE_SPAN = re.compile(r'[“"「『]([^”"」』\n]{1,300})[”"」』]')
# 说话人候选里的"角色性名词"，用于从长定语中摘出真正的说话人
_ROLE_NOUN = re.compile(
    r'([一-龥]{0,3}(?:男子|女子|男人|女人|老人|老者|老头|少年|少女|孩子|家伙|'
    r'族长|长老|村长|头领|汉子|妇人|大汉|青年|小子|娃子|众人|村民|弟子|师父|师尊))')
# 站点/排版噪音，命中即判定不是说话人（起点等平台会在章末插入"本章说""同人创作"等）
_SPK_NOISE = ("本章", "评论", "创作", "рекл", "书评", "订阅", "推荐票", "月票")


def _extract_dialogue(text: str) -> list:
    """从原著原文抽取真实对白，台词内容 100% 原样照搬，绝不改写、不截断、不编造。

    以「引号片段」为唯一锚点（中文小说对白必带引号），逐条向前后文推断说话人：
    - 引号前：`说话人+说/道：` → 直接取
    - 引号后：`……的中年男子……喝道` → 从长定语里摘角色性名词
    - 都推不出：speaker 留空（宁可不标，也不瞎猜）
    返回按原文出现顺序的 [{'speaker','line','pos'}]，同一句原话只保留首次出现。
    """
    def _trim(s: str) -> str:
        """去定语残留与尾部动词：'拍的小家伙'→'小家伙'，'而后又喝'→'而后又'。"""
        if '的' in s:
            s = s.rsplit('的', 1)[-1]
        s = s.strip().lstrip('一个那这些几名位又还也都并才就')
        # 剥尾部残留的语气/动作修饰（贪婪匹配会多吃，如「林轩沉声」「老族长笑骂」）
        s = re.sub(r'(?:沉声|冷笑|低声|高声|轻声|苦笑|大声|柔声|厉声|怒声|急声|'
                   r'笑骂|摇头|点头|叹息|开口|接口|回应|连忙|急忙|缓缓)$', '', s) or s
        # 剥尾部残留的单字说话动词（如「老人喊」「而后又喝」）
        s = re.sub(r'[道说问喊叫笑叹喝骂应答]+$', '', s) or s
        return s.strip()

    def _pick_speaker(chunk: str) -> str:
        """从说话人候选串（可能是长定语）里摘出真正的角色称谓。"""
        chunk = (chunk or '').strip().strip('，。、；：""''（） ')
        if not chunk or any(w in chunk for w in _SPK_NOISE):
            return ''
        # 长定语：优先摘"中年男子/小家伙/老族长"这类角色名词
        hits = _ROLE_NOUN.findall(chunk)
        if hits:
            cand = _trim(hits[-1])
            if cand and cand not in _DLG_BLACK:
                return cand
        # 已是短称谓/人名，直接用
        cand = _trim(chunk)
        if 2 <= len(cand) <= 6 and cand not in _DLG_BLACK:
            return cand
        return ''

    out, seen = [], set()
    for m in _QUOTE_SPAN.finditer(text):
        line = (m.group(1) or '').strip()
        if len(line) < 2 or line in seen:
            continue
        seen.add(line)
        speaker = ''
        # ① 引号前 30 字内找 "说话人 + 动词 +（：）"
        before = text[max(0, m.start() - 30):m.start()]
        mb = re.search(r'([一-龥]{2,12})' + _DLG_VERB + r'[：:]?\s*$', before)
        if mb:
            speaker = _pick_speaker(mb.group(1))
        # ② 引号后 40 字内找 "说话人 …… 动词"（本书主流写法）
        if not speaker:
            after = text[m.end():m.end() + 40]
            ma = re.match(r'([^。！？\n“”"]{0,32}?)' + _DLG_VERB, after)
            if ma:
                speaker = _pick_speaker(ma.group(1))
        # ③ 同一人连说两句（"……"XX一脸严肃，而后又喝道："……"）：
        #    说话人推不出、但紧邻上一条且中间有承接词时，继承上一条的说话人。
        if not speaker and out:
            gap = text[out[-1]["end"]:m.start()]
            if len(gap) <= 60 and re.search(r'而后|接着|随即|继而|又|顿了顿|补充|续道', gap):
                speaker = out[-1]["speaker"]
        out.append({"speaker": speaker, "line": line,
                    "pos": m.start(), "end": m.end()})
    return out


def _enrich_shot(shot: dict, body_slice: str) -> dict:
    """补全单镜制作细节 + 台词（_build_rich_prompt 已合并 dialogue，一次调用足够）。

    模块级函数，供单章（按场）与批量两条链路共用，避免重复定义。
    """
    rich = None
    for _ in range(2):
        raw = _chat(
            _build_rich_prompt(shot, body_slice),
            system="你是一名资深影视导演，擅长写AI视频生成提示词与人物台词，只输出合法JSON对象。",
            num_predict=850, json_mode=True,
        )
        rich = _parse_rich(raw)
        if rich.get("video_prompt_anime") and rich.get("action"):
            break
    if rich:
        shot.update({k: v for k, v in rich.items() if v})
    return shot


# ---- 单章 → 多镜头（适配 3b 小模型）------------------------------------------
# 阶段1：先生成"骨架"分镜（不含台词与提示词，3b 能稳定填满）。
# 阶段2：逐镜补全（每镜两次小调用：动作/灯光/特效 + 台词），避免预算被英文提示词挤占。
def _make_shots(ch_title: str, body: str, n_shots: int,
                system: str, num_predict: int = 2400,
                prior_state: Optional[dict] = None,
                bible: Optional[dict] = None,
                single_pass: bool = False) -> dict:
    """骨架 + 角色设定库 + 逐镜补全，适配 3b 小模型。

    衔接处理：
    - 跨批：prior_state 非空时，首段骨架 prompt 注入「前文衔接」指令，从上一组结尾平滑接续。
    - 批内：每段骨架生成后记录结尾镜头，下一段 prompt 注入「承接上一段」，保持时空连续。
    - 人物库：bible 非空时直接复用（整批/整本只生成一次），跳过本函数内的建库 LLM 调用。
    - single_pass：把整章正文作为一段生成骨架，不内部切分（适用于单章生成，避免用户感知"被拆成好几部分"）。
    返回 {"shots": [...], "bible": {...}}。
    """
    # 阶段1：生成骨架。
    # 默认按语义段分批：3b 一次吐 20 镜必定截断；每段只要 2-6 镜，输出完整，
    # 且长章节/高密度章节自然获得更多镜头，短过渡章自然更少。
    # single_pass=True 时把整章塞给一次骨架调用，章节不太长时又快又符合直觉。
    segments = [body] if single_pass else _segment_text(body)
    if not segments:
        return {"shots": [], "bible": {}}
    total_len = sum(len(s) for s in segments) or 1
    prior_block = _prior_block(prior_state) if prior_state else ""
    skeleton: List[dict] = []
    skel_chars: dict = {}
    last_seg_shot = None  # 上一段结尾镜头，用于批内衔接
    for si, seg in enumerate(segments, 1):
        _report(f"拆分镜头 {si}/{len(segments)} 段", si, len(segments))
        want = int(round(n_shots * len(seg) / total_len))
        want = max(2, min(6, want))
        # 第一段承接「前文衔接」，其余段承接「上一段结尾」
        extra = ""
        if si == 1 and prior_block:
            extra = prior_block
        elif last_seg_shot is not None:
            extra = (
                "【承接上一段】上一段结尾：场景="
                f"{last_seg_shot.get('scene','')}，角色={last_seg_shot.get('characters','')}，"
                f"剧情={last_seg_shot.get('plot','')}。"
                "本段请平滑接续，保持同一时空与人物状态，不要无故跳场。"
            )
        part: List[dict] = []
        skel_raw = None
        for attempt in range(2):
            cur_seg = seg[:1200] if attempt == 1 else seg
            cur_want = max(2, want - 1) if attempt == 1 else want
            raw = _chat(
                _build_skeleton_prompt(ch_title, cur_seg, cur_want, extra=extra),
                system="你是一名资深影视分镜导演，只输出合法JSON对象。",
                num_predict=num_predict, json_mode=True,
            )
            skel_raw = raw
            part = _parse_skeleton(raw, cur_want)
            if part:
                break
        if part:
            last_seg_shot = part[-1]
            skel_chars.update(_parse_characters(skel_raw))
        skeleton.extend(part)
    if not skeleton:
        return {"shots": [], "bible": {}}
    # 严格截断到 n_shots：3b 偶尔多吐镜头，超出部分既拖慢又稀释单镜信息量
    if len(skeleton) > n_shots:
        skeleton = skeleton[:n_shots]
    for i, s in enumerate(skeleton, 1):
        s["shot_id"] = i

    # 阶段1.5：角色形象库（整段只调一次，供所有镜头共用，锁死人物一致性）
    # 优化：bible 由调用方整批/整本预生成并复用，避免每章重复调 LLM 建库。
    if bible:
        # 复用整本角色库，同时把本章新出现的角色补进去（skel_chars），
        # 避免「整本已存在时单章新角色丢失、跨章形象不一致」。
        if skel_chars:
            bible = _merge_bible(bible, skel_chars)
        _report(f"复用并补全角色形象库（{len(bible)} 个角色，跨章一次生成）", 0, len(skeleton))
    elif skel_chars:
        _report(f"从骨架提取角色形象库（{len(skel_chars)} 个角色）", 0, len(skeleton))
        bible = skel_chars
    else:
        _report(f"共 {len(skeleton)} 个镜头，正在建立人物形象设定", 0, len(skeleton))
        bible = build_character_bible(ch_title, body, _collect_names(skeleton))

    # 阶段2：逐批补全（每批一次 LLM 调用，Ollama 本地串行，减少调用次数才是真提速）。
    # 整批补全全部镜头细节 + 台词；若整批解析失败则回退逐镜串行兜底；个别漏写台词再补一次。
    body_slice = body[:1500]
    n_total = len(skeleton)

    # 逐镜补全：本地 Ollama 单实例对并发请求串行排队，逐镜（每镜独立、差异化好）比批量更稳；
    # 批量一次吐多镜会让 3b 把同一段台词/动作复制到各镜，质量崩。骨架已省掉独立的建库调用，
    # 单章实际调用 = 骨架(1) + 逐镜(N) 次；N 上限已压到 5，兼顾速度与可用镜头数。
    # 并发补全：逐镜 enrich 彼此独立，用线程池并发打满多个 Ollama 实例，
    # 突破单实例串行排队瓶颈；并发度不超过实例数，避免多余的排队开销。
    import os as _os
    _n_urls = len([u for u in _os.environ.get("OLLAMA_URLS", "").split(",") if u.strip()]) or 1
    # 单实例(仅1个URL)时不要并发：Ollama 单实例对并发请求会串行排队且每请求变慢，
    # 并发反而更慢。只有多实例(多GPU/云)时才并发打满。
    _workers = 1 if _n_urls <= 1 else max(2, min(n_total, _n_urls))
    with ThreadPoolExecutor(max_workers=_workers) as _ex:
        _futs = {_ex.submit(_enrich_shot, shot, body_slice): idx
                 for idx, shot in enumerate(skeleton, 1)}
        _done = 0
        for _fut in as_completed(_futs):
            _idx = _futs[_fut]
            _filled = _fut.result()
            skeleton[_idx - 1] = _filled
            _done += 1
            _report(f"打磨镜头 {_idx}/{n_total}", _done, n_total)

    # 首镜 bridge 保护：模型在 rich 阶段可能自行生成"承接上一镜…"，但首镜无前置镜头，
    # 必须清除这类指向前置的措辞，改为开场起幅；其余镜头保持兼容旧逻辑的兜底。
    _BRIDGE_FWD = re.compile(r"上一镜|上一段|承接|前一个|前镜|前文|上一个镜头")
    for i, shot in enumerate(skeleton):
        if i == 0 and _BRIDGE_FWD.search(shot.get("bridge", "")):
            shot["bridge"] = ""
        if not shot.get("bridge"):
            if i == 0:
                shot["bridge"] = "开场起幅：本章首镜，从环境或人物特写建立切入，无需承接任何前置镜头。"
            elif (skeleton[i - 1].get("scene") or "") == (shot.get("scene") or ""):
                shot["bridge"] = "同场景内部切换，保持轴线与人物站位一致，动作顺接上一镜。"
            else:
                shot["bridge"] = "跨场景叠化转场，前一镜收尾画面作为过渡起点，保持主体连续性。"

    # 台词 100% 来自原著原文抽取：按对白在正文中的位置落到对应镜头，
    # 一镜可多条，绝不因镜头不够而丢弃原文对白。
    body_dlg = _extract_dialogue(body)
    dlg_buckets: List[List[str]] = [[] for _ in skeleton]
    if skeleton:
        for d in body_dlg:
            idx = min(len(skeleton) - 1,
                      int(d["pos"] / max(1, len(body)) * len(skeleton)))
            who = d["speaker"]
            dlg_buckets[idx].append(f"{who}：{d['line']}" if who else d["line"])
    for bi, shot in enumerate(skeleton):
        shot.pop("dialogue", None)
        if dlg_buckets[bi]:
            shot["dialogue"] = "\n".join(dlg_buckets[bi])
    attach_all(skeleton, bible)
    return {"shots": skeleton, "bible": bible}


def make_director_script(ch_title: str, ch_text: str, novel_name: Optional[str] = None,
                         max_chars: int = 7000) -> dict:
    """章节 → 按场组织的导演级剧本（完整覆盖，不再限制镜头数）。

    流程：先把正文按语义段切成若干「场」，每场一次 LLM 调用同时产出
    [本场信息 scene_meta] + [该场镜头骨架 shots]，再逐镜补全制作细节与台词，
    最后整章生成一段「导演阐述」。镜头数完全由内容体量自然决定，不硬塞上限。
    返回 {"scenes":[...], "shots":[...], "director_note":str, "characters_bible":{}}。
    """
    body = _clean_text(ch_text)[:max_chars]
    # 每场约 450 字：一章 2600 字自然切出 5-6 场，配合下面每场 2-5 镜，
    # 整章 15-20 镜，才谈得上"完整覆盖"。旧值 850 会把一章压成 2-3 场，
    # 每镜要背 800+ 字剧情，等于没覆盖。
    segments = _segment_text(body, target=450)
    if not segments:
        return {"scenes": [], "shots": [], "director_note": "", "characters_bible": {}}
    bible = load_bible(novel_name) if novel_name else None

    # 导演阐述（整章一次，纯文本）
    director_note = ""
    try:
        director_note = _chat(
            _build_director_note_prompt(ch_title, body),
            system="你是一名影视导演，只输出纯文本。", num_predict=320)
    except Exception:
        director_note = ""

    scenes: List[dict] = []
    flat: List[dict] = []
    shot_counter = 0
    for si, seg in enumerate(segments, 1):
        # 本场镜头数由内容密度决定：叙事体量（每 160 字一镜）+ 对白条数（每 2 条加一镜），
        # 上限 5 镜是 3b 单次 JSON 输出不截断的稳定区间，不是产品限制。
        seg_dlg = _extract_dialogue(seg)
        want = round(len(seg) / 160) + round(len(seg_dlg) / 2)
        want = max(2, min(5, want))
        _report(f"拆解第{si}/{len(segments)}场", si, len(segments))
        raw = _chat(
            _build_scene_prompt(ch_title, seg, want),
            system="你是一名资深动漫分镜导演，只输出合法JSON对象。",
            num_predict=max(1400, want * 320), json_mode=True,
        )
        meta, shots = _parse_scene(raw, want)
        if not shots:
            continue
        body_slice = seg[:1500]
        # 台词 100% 来自原文：按对白在本段中的位置落到对应镜头，
        # 一个镜头可承载多条（原文该说几句就是几句），绝不因镜头不够而丢弃对白。
        buckets: List[List[str]] = [[] for _ in shots]
        for d in seg_dlg:
            idx = min(len(shots) - 1,
                      int(d["pos"] / max(1, len(seg)) * len(shots)))
            who = d["speaker"]
            buckets[idx].append(f"{who}：{d['line']}" if who else d["line"])
        for bi, shot in enumerate(shots):
            _enrich_shot(shot, body_slice)
            # 丢弃模型可能顺手编的 dialogue，只用原文抽取结果
            shot.pop("dialogue", None)
            if buckets[bi]:
                shot["dialogue"] = "\n".join(buckets[bi])
            shot_counter += 1
            shot["shot_id"] = shot_counter
            flat.append(shot)
        scenes.append({
            "scene_id": len(scenes) + 1,
            "heading": meta.get("heading") or f"第{si}场",
            "time": meta.get("time", ""),
            "location": meta.get("location", ""),
            "characters": meta.get("characters", ""),
            "event": meta.get("event", ""),
            "shots": shots,
        })
    # 台词全部来自原文抽取，不再让模型生成；上面已按段分配，这里无需去重/补生成。

    if not flat:
        return {"scenes": scenes, "shots": [], "director_note": director_note, "characters_bible": bible or {}}
    # 复用已有角色形象库（单章不再重复调 LLM 建库）
    if bible:
        _report(f"复用角色形象库（{len(bible)} 个角色，跨章一次生成）", 0, len(flat))
    else:
        bible = build_character_bible(ch_title, body, _collect_names(flat))
    # flat 与 scene.shots 是同一批 dict 对象，attach_all 会同时刷新两者
    attach_all(flat, bible)
    return {"scenes": scenes, "shots": flat, "director_note": director_note, "characters_bible": bible}


def make_storyboard(ch_title: str, ch_text: str, n_shots: int | None = None,
                    max_chars: int = 7000, novel_name: Optional[str] = None) -> dict:
    """兼容别名：旧调用方仍可用；新代码请用 make_director_script。"""
    res = make_director_script(ch_title, ch_text, novel_name=novel_name, max_chars=max_chars)
    return {"shots": res["shots"], "bible": res.get("characters_bible") or {}}


def storyboard_chapter(novel_path: str, chapter_index: int = 1,
                       novel_name: Optional[str] = None,
                       n_shots: int | None = None) -> dict:
    """端到端：加载章节 → 分镜脚本 → 落盘。"""
    novel = load_novel(novel_path, name=novel_name)
    if chapter_index < 1 or chapter_index > novel.n_chapters:
        raise ValueError(f"章节号超界（共 {novel.n_chapters} 章）")
    ch = novel.chapters[chapter_index - 1]
    res = make_director_script(ch.title, ch.text, novel_name=novel.name)
    shots, bible = res["shots"], res.get("characters_bible") or {}
    # 别名归一：本章新角色若其实是整本已有角色的别名/小名/绰号，合并到主名，
    # 避免同一人在整本库里分裂成两条（如「小不点」与「石昊」）。
    existing_b = load_bible(novel.name)
    # ① 原文「称谓+姓名」连写识别（如原文写「老族长石云峰」，3b 却抽成两个角色）。
    #    本地正则判定，覆盖【同章内】分裂——这类分裂比对已有库根本发现不了。
    _names = list(bible.keys()) + list(existing_b.keys())
    for _s in shots:
        _names += re.split(r"[、,，/;；\s]+", _s.get("characters", "") or "")
    alias = _alias_merge_by_text(_names, ch.text)
    # 已有库里若存的是称谓（如「老族长」），把形象改挂到真名下，避免整本库残留分身
    for _a, _m in list(alias.items()):
        if _a in existing_b:
            existing_b.setdefault(_m, existing_b.pop(_a))
    # ② 单章用本地规则做别名合并（不调 LLM，省一次调用）；复杂异名交给「建全本角色库」一次性 LLM 处理。
    alias.update(_alias_merge(list(bible.keys()), list(existing_b.keys()), use_llm=False))
    if alias:
        for s in shots:
            cs = s.get("characters", "") or ""
            for a, m in alias.items():
                # 用捕获组替代 look-behind，避免 Python re 不支持变宽 look-behind 报错
                cs = re.sub(
                    rf"(^|[、,，/;；\s]){re.escape(a)}($|[、,，/;；\s])",
                    rf"\1{m}\2",
                    cs,
                )
            s["characters"] = cs
        bible = {k: v for k, v in bible.items() if k not in alias}
        attach_all(shots, bible)  # 用主名重算角色形象注入，保证 prompt 一致
    # 落盘整本人物库缓存：已有形象优先，本单章新角色追加，便于后续批量/重跑复用
    bible = _merge_bible(existing_b, bible)
    # 过滤群众/群体角色与脏名（含逗号/顿号的多角色串），避免污染角色库
    bible = {k: v for k, v in bible.items() if k and not any(
        w in k for w in ("村民", "众人", "群众", "路人", "旁白", "无"))
        and ("," not in k) and ("，" not in k) and ("、" not in k)}
    save_bible(novel.name, bible)
    os.makedirs(STORYBOARD_DIR, exist_ok=True)
    safe = re.sub(r"[^\w\u4e00-\u9fff-]", "_", f"{novel.name}_ch{chapter_index}")
    out = {
        "novel": novel.name,
        "chapter_index": chapter_index,
        "chapter_title": ch.title,
        "style": "anime",
        "n_shots": len(shots),
        "director_note": res.get("director_note", ""),
        "scenes": res.get("scenes", []),
        "characters_bible": bible,
        "shots": shots,
    }
    json.dump(out, open(os.path.join(STORYBOARD_DIR, f"{safe}.json"), "w",
                        encoding="utf-8"), ensure_ascii=False, indent=1)
    return out


def storyboard_batch(novel_path: str, start: int = 1, end: int = 5,
                     novel_name: Optional[str] = None,
                     n_shots: int | None = None) -> dict:
    """连续多章（默认最多 5 章）逐章生成并自动衔接，组成连贯的导演级分镜方案。

    关键改动：逐章生成（不再把多章拼成一个大文本），每一章都按【自身剧情密度】产出镜头，
    保证全部章节内容都被覆盖（合并式会把每章丰富度摊薄、只覆盖一小部分）；
    章与章之间用上一章结尾镜头做视觉接戏，批与批之间读取上一组 continuity 状态平滑接续；
    角色形象库跨章/跨批合并，保证同一角色每批画出来一致。
    """
    novel = load_novel(novel_path, name=novel_name)
    if start < 1 or end > novel.n_chapters or start > end:
        raise ValueError(f"章节范围超界（共 {novel.n_chapters} 章）")
    end = min(end, novel.n_chapters)
    chapters = novel.chapters[start - 1:end]

    # 跨批衔接：读上一组状态
    prior = _load_continuity(novel.name)
    prior_ok = bool(prior) and prior.get("chapter_end") == start - 1
    prior_state = prior if prior_ok else None
    prior_bible = (prior or {}).get("bible") or {}

    # 优化：整批只生成一次人物库，并落地为小说级缓存，后续批次/次运行直接复用，
    # 不再每章重复调 LLM 建库。做法：合并「已有整本缓存」与「本批从合并原文一次性抽取」，
    # 已有形象优先（锁死同一张脸），新角色追加。
    existing_bible = load_bible(novel.name)
    combined_for_bible = "\n\n".join(_clean_text(ch.text)[:1200] for ch in chapters)[:4000]
    batch_bible = build_character_bible(
        f"第{start}-{end}章", combined_for_bible, names=[])
    batch_bible = _merge_bible(existing_bible, batch_bible)
    save_bible(novel.name, batch_bible)

    all_shots: List[dict] = []
    merged_bible: dict = _merge_bible(prior_bible, batch_bible)
    running_state = prior_state  # 章间/批间衔接状态的载体
    full_text: List[str] = []

    for ci, ch in enumerate(chapters):
        ch_idx = start + ci
        ch_text = _clean_text(ch.text)
        ch_n = n_shots or _dynamic_shot_count(ch_text, chapter_count=1)
        _report(f"生成第{ch_idx}章分镜（约{ch_n}镜）", ci + 1, len(chapters))
        res = _make_shots(
            f"第{ch_idx}章 {ch.title}", ch_text, ch_n,
            system="你是一名资深动漫导演，把这一章小说改编成详细、连贯、可拍摄的分镜，只输出合法JSON数组。",
            num_predict=2400,
            prior_state=running_state,
            bible=batch_bible,
        )
        shots = res["shots"]
        attach_all(shots, merged_bible)
        # 章间衔接：用本章结尾镜头喂给下一章（不额外调 LLM）
        running_state = _chapter_tail_state(shots, ch_idx)
        all_shots.extend(shots)
        full_text.append(ch.text)
        _report(f"第{ch_idx}章完成（{len(shots)}镜）", ci + 1, len(chapters))

    # 重排镜头序号连续
    for i, s in enumerate(all_shots, 1):
        s["shot_id"] = i

    combined_text = "\n\n".join(full_text)
    # 抽衔接状态供下一组（一次 LLM 调用）
    state = _extract_continuity(novel.name, combined_text, all_shots, merged_bible, start, end)
    state["bible"] = merged_bible
    _save_continuity(novel.name, state)

    os.makedirs(STORYBOARD_DIR, exist_ok=True)
    safe = re.sub(r"[^\w\u4e00-\u9fff-]", "_", f"{novel.name}_ch{start}-{end}")
    cont_out = None
    if prior_state:
        cont_out = {
            "summary": prior_state.get("summary", ""),
            "scene": prior_state.get("scene", ""),
            "time": prior_state.get("time", ""),
            "last_shot": prior_state.get("last_shot", ""),
        }
    out = {
        "novel": novel.name,
        "chapter_range": {"start": start, "end": end},
        "chapter_title": f"第{start}-{end}章",
        "style": "anime",
        "n_shots": len(all_shots),
        "characters_bible": merged_bible,
        "continuity_loaded": bool(prior_state),
        "continuity": cont_out,
        "shots": all_shots,
    }
    json.dump(out, open(os.path.join(STORYBOARD_DIR, f"{safe}.json"), "w",
                        encoding="utf-8"), ensure_ascii=False, indent=1)
    return out


# ---- 整部 → 连续剧分集方案 ----------------------------------------------------
def _build_brief(novel, analysis: Optional[dict], max_chars: int = 2600) -> str:
    """把概览/人物/章节梗概压成适合 3b 的简要 brief。"""
    parts = []
    if analysis and analysis.get("overview"):
        parts.append("【全书概览】\n" + analysis["overview"][:900])
    chars = (analysis or {}).get("characters") or []
    if chars:
        lines = []
        for c in chars[:8]:
            line = f"- {c.get('name','')}（{c.get('role','')}）：{c.get('personality','')}；{c.get('story','')}"
            lines.append(line[:130])
        parts.append("【主要人物】\n" + "\n".join(lines))
    summ = (analysis or {}).get("chapter_summaries") or {}
    if summ:
        lines = []
        for k, v in sorted(summ.items(), key=lambda x: int(x[0]))[:80]:
            lines.append(f"第{v.get('title','')}：{v.get('summary','')}")
        parts.append("【章节梗概】\n" + "\n".join(lines)[:max_chars])
    return "\n\n".join(parts)


def _split_episodes(brief: str, n_episodes: int, novel_name: str) -> List[dict]:
    raw = _chat(
        f"你是一名资深编剧/导演。根据下面小说的概览、人物与章节梗概，把它改编成 {n_episodes} 集连续剧动画。\n"
        "要求：每集有独立戏剧弧光与悬念钩子，集与集之间情节连贯；覆盖全部章节梗概。\n"
        "输出JSON数组，每集字段：\n"
        "- ep_index: 集号(1..N)\n"
        "- title: 集名（有网感、国漫味）\n"
        "- arc: 本集核心戏剧弧（一句话）\n"
        "- chapters: 覆盖的原著章节号数组（基于给出的章节梗概序号，1-based）\n"
        "- logline: 15字内的本集一句话卖点\n"
        '严格输出JSON数组，不要其他内容：\n'
        f"【小说名】《{novel_name}》\n\n{brief}",
        system="你是一名影视编剧，只输出合法JSON。", num_predict=1600,
    )
    try:
        m = re.search(r"\[.*\]", raw, re.S)
        eps = json.loads(m.group(0)) if m else []
        out = []
        for i, e in enumerate(eps, 1):
            if not e.get("title"):
                continue
            e["ep_index"] = i
            e.setdefault("arc", "")
            e.setdefault("logline", "")
            chs = e.get("chapters") or []
            e["chapters"] = [int(x) for x in chs if str(x).isdigit()]
            out.append(e)
        return out
    except Exception:
        return []


def _episode_shots(brief: str, ep: dict, n_shots: int, novel_name: str) -> dict:
    ep_brief = (
        f"【小说】《{novel_name}》\n"
        f"【本集】《第{ep['ep_index']}集 {ep['title']}》\n"
        f"【本集戏剧弧】{ep.get('arc','')}\n"
        f"【本集卖点】{ep.get('logline','')}\n\n"
        f"本集覆盖的原著章节梗概：\n{brief}"
    )
    example = json.dumps({"shots": [_EXAMPLE_SHOT]}, ensure_ascii=False)
    prompt = (
        f"你是连续剧分镜导演。为下面这一集设计 {n_shots} 个镜头的详细分镜。\n"
        f"【每个镜头的字段与要求】\n{_SHOT_SCHEMA_HINT}\n"
        f"【示范输出结构（shots 里放全部镜头）】\n{example}\n\n"
        f"【输出规则】\n"
        f"1. 每个字段都必须填写具体、可拍的内容，禁止留空、禁止只写单字（运镜必须≥15字）。\n"
        f"2. 严格只输出 JSON 对象，结构为 {{\"shots\": [ 镜头1, 镜头2, ... ]}}，不要输出其他文字。\n\n"
        f"{ep_brief}"
    )
    raw = _chat(
        prompt,
        system="你是一名动漫分镜导演，只输出合法JSON对象，shots 字段放全部镜头。", num_predict=4800, json_mode=True,
    )
    shots = _parse_shots(raw, n_shots)
    ep_out = {
        "ep_index": ep["ep_index"],
        "title": ep["title"],
        "arc": ep.get("arc", ""),
        "logline": ep.get("logline", ""),
        "chapters": ep.get("chapters", []),
        "n_shots": len(shots),
        "shots": shots,
    }
    return ep_out


def make_series_plan(novel, analysis: Optional[dict], n_episodes: int = 5,
                     n_shots: int = 8) -> dict:
    """整部小说 → 连续剧分集方案（导演级分镜）。返回 dict。"""
    brief = _build_brief(novel, analysis)
    episodes = _split_episodes(brief, n_episodes, novel.name)
    if not episodes:
        episodes = [{"ep_index": 1, "title": novel.name, "arc": "全本主线",
                     "logline": "全本改编", "chapters": list(range(1, novel.n_chapters + 1))}]
    result_eps = []
    for ep in episodes:
        summ = (analysis or {}).get("chapter_summaries") or {}
        ep_lines = []
        for ci in ep.get("chapters", []):
            v = summ.get(str(ci))
            if v:
                ep_lines.append(f"第{v.get('title','')}：{v.get('summary','')}")
        ep_brief = "\n".join(ep_lines)[:2200] or brief[:1500]
        result_eps.append(_episode_shots(ep_brief, ep, n_shots, novel.name))
    return {
        "novel": novel.name,
        "n_episodes": len(result_eps),
        "n_shots_per_episode": n_shots,
        "episodes": result_eps,
    }


def series_plan(novel_path: str, n_episodes: int = 5, n_shots: int = 8,
                novel_name: Optional[str] = None,
                analysis: Optional[dict] = None) -> dict:
    """端到端：加载小说 →（用已有分析或现场分析）→ 连续剧分集方案 → 落盘。"""
    novel = load_novel(novel_path, name=novel_name)
    if analysis is None:
        analysis = get_analysis(novel.name)
        if not analysis or not analysis.get("chapter_summaries"):
            analysis = analyze_novel(
                novel_path, name=novel.name, max_chapters=min(novel.n_chapters, 40))
    os.makedirs(SERIES_DIR, exist_ok=True)
    safe = re.sub(r"[^\w\u4e00-\u9fff-]", "_", novel.name)
    plan = make_series_plan(novel, analysis, n_episodes=n_episodes, n_shots=n_shots)
    plan["n_chapters_analyzed"] = len((analysis or {}).get("chapter_summaries", {}))
    json.dump(plan, open(os.path.join(SERIES_DIR, f"{safe}_series.json"), "w",
                         encoding="utf-8"), ensure_ascii=False, indent=1)
    return plan


def get_series_plan(novel_name: str) -> Optional[dict]:
    safe = re.sub(r"[^\w\u4e00-\u9fff-]", "_", novel_name)
    p = os.path.join(SERIES_DIR, f"{safe}_series.json")
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return None
    return None
