# -*- coding: utf-8 -*-
"""分镜模块的公共常量与纯文本工具。

本文件刻意不依赖包内其它子模块（只依赖 ..loader 取 NOVEL_DIR），
供 prompts / shots / bible / continuity / compose / generate 各子模块安全复用，
避免出现循环导入。
"""
from __future__ import annotations

import os
import re

from ..loader import NOVEL_DIR

# 分镜落盘目录（与小说根目录并列）
STORYBOARD_DIR = os.path.join(NOVEL_DIR, "storyboard")
SERIES_DIR = os.path.join(NOVEL_DIR, "series")
CONTINUITY_DIR = os.path.join(NOVEL_DIR, "continuity")
BIBLE_DIR = os.path.join(NOVEL_DIR, "bible")

# ---- 输入清洗：剥离网文上架/求订阅等废话（会污染旁白与剧情） ----------------
_BOILER = re.compile(
    r"(上[架塞]|求订阅|求月票|求推荐票|求收藏|求追读|求支持|求投资|求打赏|"
    r"书友群|读者群|催更|加更|爆发|作者[说感]|新书[上开]|感谢[观阅]看|"
    r"ps[:：]|p\.s|^\s*【.*?】\s*$|起点[中首]文网|本书由.*提供)",
    re.I,
)


def _clean_text(text: str) -> str:
    """逐行剥离网文公告/求订阅等废话，保留正文章节文本。"""
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        if _BOILER.search(s):
            continue
        out.append(s)
    return "\n".join(out)


# 分镜字段。每个镜头都是一份可直接执行的导演单。
SHOT_FIELDS = (
    "scene, shot_type, camera, plot, arrangement, characters, dialogue, narration, "
    "action, lighting_tech, vfx, color_script, audio, emotion, video_prompt_anime"
)

# 一个高质量示范镜头——明确告诉 3B 模型"详细"的标尺。
_EXAMPLE_SHOT = {
    "scene": "石村·柳神古树前",
    "shot_type": "近景",
    "camera": "缓慢向前推进至古树主干，再低角度环绕树根一周，最后上摇定格在发光的枝条",
    "plot": "引入主角村落的精神图腾，暗示柳神即将在此觉醒，为后续奇遇埋下伏笔",
    "arrangement": "晨雾中的石村，茅屋错落，几名村民蹲坐石阶；柳神古树居中，垂落万条柳枝，背景是苍莽山脉的青灰剪影",
    "action": "微风掠过，柳条轻颤；一缕淡金光自最粗的枝条渗出，如活物般缓缓游动；小不点睁大眼，村民面露敬畏",
    "lighting_tech": "清晨侧逆光，色温约3200K暖调，树冠边缘勾出金边，地面为冷青影调，光比约3:1",
    "vfx": "枝条光晕做成体积光粒子，空气浮尘在光柱中可见，远景村民做浅景深虚化",
    "color_script": "整体青灰打底，金色光源为唯一暖色，形成冷暖对冲；饱和度中低，高光偏奶白，暗部保留青蓝",
    "audio": "远处鸡鸣、柳叶摩擦的沙沙声、低频嗡鸣渐起；配乐用埙与古筝散音，弱起渐强",
    "emotion": "静谧中透出敬畏，情绪张力约三成，为后续爆发蓄势",
    "bridge": "承接上一镜柳神枝条微光的特写，本镜以同主体匹配剪辑拉成近景，保持金光游动的视线连续，村民敬畏的表情作为反应镜头顺接",
    "characters": "柳神（未显形）、小不点、石云峰、村民",
    "dialogue": "小不点：族长爷爷，柳树在发光！ 石云峰：别出声，它在护佑我们。",
    "narration": "（慈祥男声，语速舒缓）在苍莽山脉深处，有这么一座与世无争的石村……",
    "video_prompt_anime": "cinematic close-up of an ancient willow tree, soft golden light flowing from its branches, "
                          "morning mist drifting, slow push-in then low orbit camera, Chinese donghua style, 4k, smooth motion",
}

_SHOT_SCHEMA_HINT = (
    "每个镜头必须是一份导演可执行的拍摄单，包含：\n"
    "- scene: 场景名（如'花果山山顶·雷击木前'，不要带括号）\n"
    "- shot_type: 景别（远景/全景/中景/近景/特写/大特写/过肩/俯拍/仰拍/航拍）\n"
    "- camera: 运镜（描述具体运动，例如'缓慢前推→环绕主体180°→急速后拉'）\n"
    "- plot: 剧情功能（本镜在叙事中承担什么作用，与上下镜的衔接）\n"
    "- arrangement: 场景布置（环境、人物站位、道具、氛围细节）\n"
    "- action: 人物动作/表演/服化道指示（角色怎么动、什么表情、穿什么，可稍长）\n"
    "- lighting_tech: 灯光与技术（光源方向、色温、光比、影调）\n"
    "- vfx: 特效与后期（转场、粒子、流体、景深、调色方向）\n"
    "- characters: 本镜出场角色（逗号分隔，如'孙悟空、唐僧'，禁止留空）\n"
    "- dialogue: 详细人物台词/对白（至少1-3行，格式'角色名：\"台词\"'；根据小说原对话改写，禁止留空）\n"
    "- narration: 该镜旁白/环境音/画外音说明（含情绪与音色提示，不要用上架通知等废话）\n"
    "- video_prompt_anime: 国漫版英文视频提示词（含 camera movement, shot type, 主体动作, 光影, Chinese donghua style，不要中文）\n"
    "\n"
    "输出规则：\n"
    "1. 根据内容多少动态决定镜头数量（4-8镜），内容长/转折多则多镜，短则少镜，绝不硬凑。\n"
    "2. 每个镜头都要有具体可拍的画面，不要概括性描述，不要留空，不要只写单字。\n"
    "3. dialogue 必须包含角色名和具体台词，根据小说原文对话改写，不是旁白，禁止留空。\n"
    "4. video_prompt_anime 用于国漫动态镜头，英文输出。\n"
    "5. 严格只输出一个 JSON 对象，结构必须为 {\"shots\": [ 镜头1, 镜头2, ... ]}，不要输出其他任何文字。\n"
)

# 单字运镜兜底（即使模型仍只给一字，也展开成可拍描述，避免留空）
_CAMERA_FALLBACK = {
    "推": "镜头缓慢向前推进，逼近主体",
    "拉": "镜头缓慢向后拉远，展现场景全貌",
    "摇": "水平摇摄，扫过整个场景",
    "移": "平移跟拍，保持主体在画面中",
    "跟": "跟随主体运动同步移动机位",
    "升": "镜头垂直上升，俯瞰全局",
    "降": "镜头垂直下降，压近地面",
}

# 剧情节拍信号：出现即意味着时间/空间/视点发生跳转，需要新镜头
_BEAT_KW = re.compile(
    r"(忽然|突然|猛然|骤然|蓦地|霎时|刹那|片刻后|不多时|少顷|良久|"
    r"次日|翌日|三日后|数日后|半年后|多年后|与此同时|另一边|远处|"
    r"这时|此刻|随即|紧接着|下一刻|转眼|话音刚落|只见|但见|"
    r"来到|走进|踏入|冲出|跃起|飞身|回到|抬头|转身|回头|站起|坐下|"
    r"开口|大喝|怒吼|惊呼|低语|冷笑|沉默)"
)
_DIALOG_RE = re.compile(r"[“\"「『][^”\"」』]{2,}[”\"」』]")

# ---- 一体化提示词风格尾巴（中文/英文） ---------------------------------------
_STYLE_ZH = "中国动漫风格，赛璐璐上色，精致作画，电影级构图，光影层次分明，4K 高清，运动流畅自然"
_STYLE_EN = ("Chinese donghua animation style, cel shading, cinematic composition, "
             "volumetric lighting, 4k, smooth motion")

_SHOT_TYPE_EN = {
    "远景": "extreme wide shot", "全景": "wide shot", "中景": "medium shot",
    "近景": "close shot", "特写": "close-up", "大特写": "extreme close-up",
    "过肩": "over-the-shoulder shot", "俯拍": "high angle shot",
    "仰拍": "low angle shot", "航拍": "aerial shot",
}

# ---- 技术参数推导用查表（不额外消耗 LLM，按景别与运镜规则算出） --------------
_SHOT_DURATION = {
    "大特写": 2.0, "特写": 2.5, "近景": 3.0, "中景": 3.5, "过肩": 3.5,
    "全景": 4.5, "远景": 5.5, "航拍": 6.0, "俯拍": 4.0, "仰拍": 3.5,
}
_SHOT_COMPOSITION = {
    "大特写": "85-135mm 长焦，极浅景深 f/1.8，主体占画面七成以上，背景完全虚化",
    "特写": "85mm 中长焦，浅景深 f/2.0，主体面部占画面主导，视线留白在朝向侧",
    "近景": "50mm 标准焦段，f/2.8，齐胸构图，背景可辨但虚化",
    "中景": "35mm，f/4，齐膝构图，人物与环境信息各占一半，三分法置位",
    "过肩": "50mm，f/2.8，前景肩部占画面三分之一虚化，焦点在对话对象",
    "全景": "24-28mm 广角，f/5.6，人物全身入画，环境交代完整，地平线压低",
    "远景": "18-24mm 超广角，f/8 深景深，人物占比小，强调环境体量与孤立感",
    "航拍": "16mm 广角俯视，深景深，强调地貌走向与空间关系",
    "俯拍": "35mm 高机位俯角约 30°，压缩人物存在感",
    "仰拍": "24mm 低机位仰角约 25°，抬高主体气势，天空作负空间",
}
_FAST_CAM = re.compile(r"(急速|快速|迅速|猛地|甩|急推|急拉|抽帧|闪切)")
_SLOW_CAM = re.compile(r"(缓慢|徐徐|轻缓|静止|固定|凝滞)")
