# -*- coding: utf-8 -*-
"""分镜 prompt 构造：骨架 / 富化 / 角色设定 / 台词。

所有函数都把"示范镜头"与"详细程度标尺"内嵌进 prompt，专门驯服本地 3B 模型，
让它稳定输出字段填满、可执行的导演单。
"""
from __future__ import annotations

import json

from ._common import _EXAMPLE_SHOT

# ---- 分阶段 prompt 用到的字段清单 ---------------------------------------------
_SKELETON_FIELDS = (
    "scene", "shot_type", "camera", "plot", "arrangement",
    "characters", "narration"
)
_SKELETON_SCHEMA = (
    "每个镜头必须包含以下字段：\n"
    "- scene: 场景名（如'石村·柳神古树前'，不要带括号）\n"
    "- shot_type: 景别（远景/全景/中景/近景/特写等）\n"
    "- characters: 本镜出场角色（逗号分隔，只能用原文里出现的真实姓名或称谓，"
    "禁止用'中年男子''小孩们''村民'等泛称，禁止加括号注释如'指点者'，禁止自造人名）\n"
    "- camera: 运镜（描述具体运动，≥15字，如'缓慢前推→环绕主体180°→急速后拉'）\n"
    "- plot: 剧情功能（本镜在叙事中承担什么作用，与上下镜衔接）\n"
    "- arrangement: 场景布置（环境、人物站位、道具、氛围细节）\n"
    "- narration: 旁白/环境音说明（含情绪与音色提示，不要上架通知等废话）\n"
)

_RICH_FIELDS = (
    "action", "lighting_tech", "vfx", "color_script", "audio", "emotion",
    "bridge", "video_prompt_anime"
)

_BIBLE_EXAMPLE = {
    "characters": [{
        "name": "小不点",
        "zh": "八九岁男童，身量瘦小，头发枯黄扎成两个小揪，圆眼黑亮，脸颊有高原红；"
              "穿灰褐色粗麻短褂，袖口磨破，赤脚，腰间系一条兽皮绳",
        "en": "a scrawny 8-year-old boy, dry yellowish hair in two small buns, round bright black eyes, "
              "ruddy cheeks, wearing a patched grey-brown coarse linen tunic with frayed cuffs, "
              "barefoot, an animal-hide cord around his waist",
    }]
}


def _build_skeleton_prompt(ch_title: str, body: str, n_shots: int, extra: str = "") -> str:
    """阶段1：生成分镜骨架（不含台词与提示词，3b 能稳定填满）。"""
    example = json.dumps({
        "characters": [{
            "name": "示例角色",
            "zh": "年龄段与体型，发型发色，眼睛特征，服装款式与材质与配色，随身标志物",
            "en": "age and body type, hairstyle and color, eye feature, outfit style, material, color, accessory",
        }],
        "shots": [{
            "scene": _EXAMPLE_SHOT["scene"],
            "shot_type": _EXAMPLE_SHOT["shot_type"],
            "characters": _EXAMPLE_SHOT["characters"],
            "camera": _EXAMPLE_SHOT["camera"],
            "plot": _EXAMPLE_SHOT["plot"],
            "arrangement": _EXAMPLE_SHOT["arrangement"],
            "narration": _EXAMPLE_SHOT["narration"],
        }],
    }, ensure_ascii=False)
    extra_block = f"\n{extra}\n" if extra else ""
    return (
        f"你是一名影视/动漫导演。请把下面小说内容《{ch_title}》改编成恰好 {n_shots} 个镜头的分镜骨架。\n\n"
        f"{_SKELETON_SCHEMA}\n"
        f"【示范】\n{example}\n\n"
        f"输出规则：\n"
        f"1. 必须输出恰好 {n_shots} 个镜头，外加一个 characters 数组（列出本章出现的有名有姓的主要角色及其固定形象设定，"
        f"最多 8 个，群众角色不写，用于后续镜头复用以保证同一张脸）。\n"
        f"2. 每个字段都要写满具体内容，禁止留空、禁止只写单字。\n"
        f"3. 严格只输出 JSON 对象，结构为 {{\"characters\": [...], \"shots\": [ 镜头1, 镜头2, ... ]}}，不要其他文字。\n"
        f"4. 镜头之间必须时空连续：前一镜的地点/角色/道具要在后续镜头里合理延续，"
        f"转场要自然，不要无故跳场。\n"
        f"5. 每个镜头的 scene 必须是本章内【各不相同】的具体机位或子场景"
        f"（如'石村·祭台前''石村·祭台侧''柳神古树·树下''石村·村口石阶''石村·祠堂内'），"
        f"{n_shots} 个镜头至少要覆盖 4 个不同子场景，严禁出现重复的 scene 名字；"
        f"景别与子场景配合变化，让整章镜头有空间推进感。\n"
        f"6. 上面的 characters 数组只是【独立列出】角色的固定形象供后续镜头复用，"
        f"它不影响本镜 scene 的多样性，不要因为列了角色就给所有镜头套用同一个场景。\n"
        f"{extra_block}"
        f"小说内容：\n{body[:2000]}"
    )


def _build_rich_prompt(shot: dict, body: str) -> str:
    """阶段2a：补全单镜的制作细节（不含 dialogue，台词单独生成）。"""
    ctx = json.dumps({
        "scene": shot.get("scene", ""),
        "shot_type": shot.get("shot_type", ""),
        "plot": shot.get("plot", ""),
        "arrangement": shot.get("arrangement", ""),
        "characters": shot.get("characters", ""),
        "narration": shot.get("narration", ""),
    }, ensure_ascii=False)
    ex = {k: _EXAMPLE_SHOT[k] for k in ("action", "lighting_tech", "vfx",
                                         "color_script", "audio", "emotion",
                                         "bridge", "video_prompt_anime")}
    return (
        f"你是影视/动漫导演。下面给了一个分镜的核心信息，请补全它的制作细节。\n\n"
        f"【忠实性要求】所有 action/lighting/vfx/color/audio/emotion 等制作细节，"
        f"必须基于本镜的 scene/characters/plot 合理展开，不得编造原文未提及的新人物、新事件或离谱环境；"
        f"video_prompt_anime 不得添加原著没有的内容。\n\n"
        f"【该镜核心信息】\n{ctx}\n\n"
        f"请严格输出一个 JSON 对象，包含以下 7 个字段（全部禁止留空，中文字段每个 30 字以上）：\n"
        f"- action: 人物动作/表演/服化道。写清每个角色的具体动作、面部表情、身体朝向、"
        f"服装material与配色、手里的道具。要能被动画师直接执行。\n"
        f"- lighting_tech: 灯光与技术。光源方向与性质（硬光/柔光）、色温开尔文值、"
        f"光比、影调（高调/低调）、是否有轮廓光与环境光反弹。\n"
        f"- vfx: 特效与后期。粒子/流体/能量效果的形态与运动方式、景深虚化程度、"
        f"动态模糊、镜头光晕、后期调色方向。\n"
        f"- color_script: 色彩脚本。主色调、辅助色、点缀色的具体描述，冷暖关系，"
        f"饱和度高低，与相邻镜头的色彩对比意图。\n"
        f"- audio: 声音设计。环境音、拟音细节、配乐编制与情绪走向、有无静默留白。\n"
        f"- emotion: 情绪与节奏。本镜的情绪基调、张力强度、在整段叙事中的节奏位置"
        f"（铺垫/推进/爆发/回落）。\n"
        f"- bridge: 镜头衔接。先判断本镜 scene 与上一镜 scene 是否为同一场景："
        f"【同场景】才可写匹配剪辑、动作顺接、反应镜头等接戏方式；"
        f"【不同场景】必须写转场方式（硬切/叠化/空镜过渡/声音先入），"
        f"明确交代地点已改变，严禁写成“延续上一镜的场景”之类与实际场景矛盾的描述。"
        f"写清怎样保持时空与人物连续，让视频大模型逐镜拼接时不跳戏。\n"
        f"- video_prompt_anime: 英文视频提示词，一整句 70-110 词，必须把【本镜】的景别、"
        f"角色外貌与服装、主体动作、环境细节、光影、色彩、特效、镜头运动全部写进去。"
        f"只写英文，不要加风格后缀（系统会统一追加）。\n\n"
        f"【重要】禁止输出 dialogue 字段：人物台词由系统从原著原文中直接抽取，"
        f"你不得创作、不得编造任何对白或旁白文字。\n\n"
        f"【格式示范（只学格式与详细程度，内容必须换成本镜的，"
        f"严禁照抄示范里的柳树/willow/石村/小不点）】\n"
        f"{json.dumps(ex, ensure_ascii=False)}\n\n"
        f"严格只输出一个 JSON 对象，不要其他文字。小说背景参考：\n{body[:1500]}"
    )


def _build_bible_prompt(ch_title: str, body: str, names: list) -> str:
    """为指定角色写【固定形象设定】prompt（跨镜头锁死同一张脸）。"""
    hint = "、".join(names[:14]) if names else "（自行从原文中提取）"
    return (
        f"你是动画角色设定师。请为《{ch_title}》里的主要角色写【固定形象设定】，"
        f"用于保证每一个镜头画出来都是同一个人。\n\n"
        f"需要设定的角色：{hint}\n\n"
        f"严格输出一个 JSON 对象：{{\"characters\": [{{\"name\": \"角色名\", "
        f"\"zh\": \"中文外貌设定\", \"en\": \"english appearance\"}}]}}\n\n"
        f"要求：\n"
        f"1. 每个角色的 zh 写 40-70 字，必须包含：年龄段、体型身高、发型发色、"
        f"眼睛特征、面部特点、服装款式与材质与配色、随身标志物。\n"
        f"2. en 是 zh 的英文版，用于喂英文视频模型，写成一串逗号分隔的外貌描述短语。\n"
        f"3. 只写外貌，不写性格、不写剧情、不写能力。\n"
        f"4. 最多写 12 个角色，只写有名有姓的重要角色，群众角色不写。\n"
        f"5. 严禁照抄下面示范里的“小不点”，除非原文真有这个角色。\n"
        f"6. 每个角色必须独立成项，name 字段只放【单个角色名】，禁止把多个角色名用逗号/顿号拼在一个 name 里"
        f"（例如禁止写'小不点, 石云峰'或'老族长石云峰'这种串，应拆成两条）；name 中也不要带括号与修饰词。\n\n"
        f"【格式示范】\n{json.dumps(_BIBLE_EXAMPLE, ensure_ascii=False)}\n\n"
        f"只输出 JSON。原文节选：\n{body[:2000]}"
    )


def _build_dialogue_prompt(shot: dict, body: str, forbid: str = "") -> str:
    """阶段2b：单独生成人物台词（模型弱项，独立一次调用，避免预算被英文提示词挤占）。

    forbid: 上一镜的台词，若非空则在指令中明确禁止与上一镜雷同（相邻镜头去重兜底用）。
    """
    ctx = json.dumps({
        "scene": shot.get("scene", ""),
        "characters": shot.get("characters", ""),
        "plot": shot.get("plot", ""),
    }, ensure_ascii=False)
    return (
        f"你是影视编剧。请为下面这个分镜写【人物台词】。\n\n"
        f"【该镜信息】\n{ctx}\n\n"
        f"要求：\n"
        f"1. 只输出一个 JSON 对象：{{\"dialogue\": \"台词内容\"}}，不要其他字段、不要其他文字。\n"
        f"2. dialogue 写 2-4 行，每行格式'角色名（情绪）：台词'，多行用空格分隔。"
        f"括号里写这句的语气情绪，2-4 个字，如（急切）（冷冷）（压低声音）（颤抖），用于配音与口型。\n"
        f"3. 必须让 characters 里列出的每个角色都至少说一句贴合本镜剧情的话（不要只让一个人说话）。\n"
        f"4. 台词要具体、有戏剧性、有潜台词，紧扣本镜的 plot 与场景；"
        f"符合角色身份与年龄的说话方式；不要写空话套话，也不要和别的镜头雷同。\n"
        f"4b. 严格忠实原著：台词只能基于原著中该角色在此情境下的言行，"
        f"严禁编造原文完全没有的对白；若原著中本镜本就没有对白，可省略 dialogue 字段，不要硬编。\n"
        f"5. 严格只写'人'说出口的话；禁止把动作/神态/场景描写冒充台词"
        f"（例如'小不点：用力挥动手臂'、'大年青人：认真地指导着'都是错的，"
        f"那是动作不是说话；'石村：一阵狂风吹过'也错，石村是地点不是角色）。\n"
        f"6. 每行用中文冒号'：'分隔角色名与台词，禁止用「」『』“”等引号包裹台词，"
        f"禁止输出 \\uXXXX 这类转义序列。\n"
        f"7. 若某角色此刻真的没话，就省略该角色的台词行，不要硬写动作描写凑数。\n"
        f"8. 好台词示例（写这种口语化、带情绪的话）：小不点（惊喜）：爷爷，今天的雾好大！ "
        f"石云峰（压低声音）：莫怕，跟着柳神的光走。 坏台词示例（禁止写这种）："
        f"小不点：用力挥动手臂。 中年男子：微笑着点头。\n\n"
        + (f"9. 禁止与上一镜台词雷同：上一镜台词是「{forbid}」。本镜必须写出不同的新台词，"
           f"不要重复上一镜的任何一句。\n\n" if forbid else "")
        + f"小说背景参考：\n{body[:1800]}"
    )


def _build_batch_rich_prompt(shots: list, body: str) -> str:
    """把一批(≤3镜)的骨架 shell 一次性补全全部制作细节+台词，1次调用产出等长数组。

    把逐镜「每镜1次调用」压缩成「每批1次调用」，是本地 Ollama 串行实例下最实在的提速手段。
    """
    ctx = json.dumps([{
        "scene": s.get("scene", ""),
        "shot_type": s.get("shot_type", ""),
        "plot": s.get("plot", ""),
        "arrangement": s.get("arrangement", ""),
        "characters": s.get("characters", ""),
        "narration": s.get("narration", ""),
    } for s in shots], ensure_ascii=False)
    ex = {k: _EXAMPLE_SHOT[k] for k in ("action", "lighting_tech", "vfx",
                                         "color_script", "audio", "emotion",
                                         "bridge", "video_prompt_anime")}
    return (
        f"你是影视/动漫导演。下面给了 {len(shots)} 个分镜的核心信息，请一次性补全它们的制作细节，"
        f"输出一个 JSON 对象，结构为 {{\"shots\": [ 镜头1, 镜头2, ... ]}}，数组长度与输入相同、顺序一一对应。\n\n"
        f"【镜头核心信息（按顺序）】\n{ctx}\n\n"
        f"数组中每个对象包含以下 8 个字段（全部禁止留空，中文字段每个 30 字以上）：\n"
        f"- action: 人物动作/表演/服化道，写清每角色具体动作、表情、朝向、服装材质配色、道具，供动画师直接执行。\n"
        f"- lighting_tech: 灯光与技术，光源方向与性质(硬光/柔光)、色温开尔文、光比、影调、轮廓光与环境光反弹。\n"
        f"- vfx: 特效与后期，粒子/流体/能量形态与运动、景深虚化、动态模糊、镜头光晕、调色方向。\n"
        f"- color_script: 色彩脚本，主/辅/点缀色及冷暖关系、饱和度、与相邻镜色彩对比意图。\n"
        f"- audio: 声音设计，环境音、拟音、配乐编制与情绪走向、有无静默留白。\n"
        f"- emotion: 情绪与节奏，本镜情绪基调、张力强度、在叙事中的节奏位置(铺垫/推进/爆发/回落)。\n"
        f"- bridge: 镜头衔接，说明本镜如何与上一镜接戏(匹配剪辑/动作顺接/反应镜头/硬切/视角切换)，保持时空人物连续。\n"
        f"- video_prompt_anime: 英文视频提示词，一整句 70-110 词，把本镜景别、角色外貌与服装、主体动作、环境、光影、色彩、特效、镜头运动全写进去；只写英文不加风格后缀。\n\n"
        f"【重要】禁止输出 dialogue 字段：人物台词由系统从原著原文中直接抽取，"
        f"你不得创作、不得编造任何对白或旁白文字。\n\n"
        f"【格式示范（只学格式与详细程度，内容必须换成本批镜头的，"
        f"严禁照抄示范里的柳树/willow/石村/小不点）】\n"
        f"{json.dumps({'shots': [ex]}, ensure_ascii=False)}\n\n"
        f"严格只输出一个 JSON 对象，不要其他文字。小说背景参考：\n{body[:1500]}"
    )


def _build_scene_prompt(ch_title: str, seg: str, want: int) -> str:
    """场景级分镜：一次调用同时产出『本场信息(scene_meta)』+『镜头骨架(shots)』。

    按场组织（而非平铺 N 个镜头），让整章被拆成若干「戏」，每场镜头数由内容体量自然决定，
    不再硬塞一个镜头上限。适配本地 3B 小模型：单场体量小、输出完整、不截断。
    """
    example = json.dumps({
        "scene_meta": {
            "heading": "石村·柳神觉醒",
            "time": "清晨",
            "location": "石村·柳神古树前",
            "characters": "小不点、石云峰、村民",
            "event": "柳神枝条渗出金光，村民敬畏",
        },
        "shots": [{
            "scene": _EXAMPLE_SHOT["scene"],
            "shot_type": _EXAMPLE_SHOT["shot_type"],
            "characters": _EXAMPLE_SHOT["characters"],
            "camera": _EXAMPLE_SHOT["camera"],
            "plot": _EXAMPLE_SHOT["plot"],
            "arrangement": _EXAMPLE_SHOT["arrangement"],
            "narration": _EXAMPLE_SHOT["narration"],
        }],
    }, ensure_ascii=False)
    return (
        f"你是一名影视/动漫导演。请把下面小说《{ch_title}》的【这一小段内容】，改写成「一场戏」的分镜。\n\n"
        f"{_SKELETON_SCHEMA}\n"
        f"【示范】\n{example}\n\n"
        f"输出规则：\n"
        f"1. 先输出一个 scene_meta 对象（本场信息）：\n"
        f"   - heading：本场戏标题（8-14字，凝聚核心事件，如'石村·柳神觉醒'）\n"
        f"   - time：本场发生的时段（简短，如'清晨''暴雨夜'；原文未明确说明则留空字符串）\n"
        f"   - location：具体地点（如'石村·柳神古树前'；原文未明确说明则留空字符串）\n"
        f"   - characters：本场出场角色（逗号分隔，只能用原文出现的真实姓名/称谓，"
        f"禁止泛称与括号注释与自造人名；若难以确定可留空字符串）\n"
        f"   - event：本场核心事件（一句话，20字内；是对原著的忠实概括，不得编造原文没有的情节）\n"
        f"   以上 time/location/characters/event 四个字段【严禁编造】，原文没有明确写到就留空，宁缺毋滥。\n"
        f"2. 再输出恰好 {want} 个镜头（shots 数组），每个镜头必须包含上面列出的全部字段，"
        f"每个字段写满具体内容，禁止留空或只写单字。\n"
        f"3. 镜头之间必须时空连续，前一镜地点/角色要在后续镜头合理延续，转场自然，不要无故跳场。\n"
        f"4. 所有内容必须严格忠实于下面的原著段落，只做影视化改编，不得添加原文没有的人物、事件、对白或细节描写。\n"
        f"5. 严格只输出一个 JSON 对象，结构为 "
        f"{{\"scene_meta\": {{...}}, \"shots\": [ 镜头1, 镜头2, ... ]}}，不要其他文字。\n\n"
        f"小说内容：\n{seg[:1500]}"
    )


def _build_director_note_prompt(ch_title: str, body: str) -> str:
    """整章一次生成『导演阐述』，作为全章基调与 AI 视频生成的统一要求（纯文本）。"""
    return (
        f"你是影视导演。请为小说《{ch_title}》这一章写一段「导演阐述」，用中文、120字以内。\n"
        "说明：这一章的整体基调、最该被观众记住的画面或情绪、视觉风格方向（色调/节奏/表演风格），"
        "以及给 AI 视频生成时的统一要求。只输出纯文本，不要 JSON、不要引号包裹、"
        "不要加『导演阐述：』这类前缀。"
        f"\n\n本章正文节选：\n{body[:2000]}"
    )
