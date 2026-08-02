"""角色名规整：过滤情绪 / 动作 / 语气 / 叙述性后缀，去除规则与 AI 路由产生的噪音角色名。

规则切分（``services/source_segmenter``）与 AI 路由（``services/speaker_routing_service``）
在把角色名写入 ``speakers.json`` 之前都必须经过本模块，保证：

- ``"林晚轻声说"`` → ``"林晚"``
- ``"顾川急道"`` / ``"顾川急"`` → ``"顾川"``
- ``"她自言自语"`` → 无效（代词），保持 unresolved
- ``"轻声说"`` / ``"笑着问"`` → 无效（纯动作 / 语气），保持 unresolved
- ``"年轻人连声道谢"`` → 无效（泛指称呼 + 叙述性结构），保持 unresolved
- ``"顾川认真地说"`` → ``"顾川"``
"""
from __future__ import annotations

# 多字修饰后缀（动作 / 情绪 / 语气 / 叙述状语，长的优先匹配）
_MULTI_SUFFIXES = (
    "自言自语",
    "轻声说道", "低声说道", "大声说道", "高声说道", "小声说道", "笑着说道",
    "轻声说", "低声说", "大声说", "高声说", "小声说", "笑着说", "笑着问",
    "苦笑道", "冷笑道", "笑道", "问道", "答道", "怒道", "急道", "惊道",
    "喊道", "叫道", "喝道", "叹道", "沉声道", "冷冷道", "淡淡道", "缓缓道",
    "喃喃道", "低声道", "颤声道", "心想", "想道", "喃喃自语", "自言自语道",
    "轻声细语", "低声细语", "大声喊道",
    # 规则正则吃掉动词后残留的修饰语（如「林晚轻声」→ 剥「轻声」→「林晚」）
    "轻声", "低声", "大声", "高声", "小声", "笑着", "哭着", "怒声", "急声",
    # 叙述性状语残留（「顾川认真地说」「年轻人连声道谢」）
    "连声", "认真", "一字一句", "一字一顿", "低声", "平静", "坚定", "缓缓",
    "慢慢", "淡淡", "冷冷", "微微", "连忙", "急忙", "赶紧", "一边",
)

# 单字后缀：仅当剥离后仍剩 >=2 个字符（即原名 >=3 字符）时才允许剥离，
# 避免误伤两字角色名（如"王道""李道"）。
_SINGLE_SUFFIXES = ("说", "问", "答", "道", "喊", "叫", "笑", "叹", "急", "怒", "哼", "呵")

# 以「地」结尾的状语残留（「顾川认真地」→ 剥「地」→ 再按上面列表剥）
_ADVERB_DE = "地"

# 代词、叙述占位与泛指称呼：出现即视为无效角色名
_INVALID_NAMES = {
    "他", "她", "它", "我", "你", "我们", "你们", "他们", "她们", "它们",
    "旁白", "叙述", "作者", "声音", "内心", "画外音", "路人", "群众", "众人",
    "年轻人", "中年人", "老年人", "老人", "女人", "男人", "小孩", "孩子",
    "路人甲", "某人", "顾客", "店员", "老板", "老板娘", "男子", "女子",
}

# 单字动词 / 助词残留（「名叫」→「名」）
_SINGLE_NOISE = {
    "名", "叫", "作", "说", "问", "答", "道", "喊", "笑", "叹", "哼", "呵",
    "急", "怒", "地", "的", "得", "了", "着",
}

_MODIFIER_SUFFIXES = _MULTI_SUFFIXES + _SINGLE_SUFFIXES

# 角色名长度上限（中文角色名极少超过 8 字；与规则正则一致）
_MAX_NAME_LENGTH = 8


def normalize_speaker_name(name: str) -> str:
    """规整单个角色名；返回空字符串表示无效（应保持 unresolved / 丢弃该指派）。

    Args:
        name: 原始角色名（可含标点、动作/情绪/叙述后缀）。

    Returns:
        规整后的角色名；无法规整出有效角色时返回 ``""``。
    """
    text = (name or "").strip().strip("：:，,。.！!？? ")
    if not text:
        return ""
    stripped = _strip_modifier(text)
    if stripped != text:
        text = stripped
    # 剥离后整体是纯修饰词（如"轻声说""笑着问""连声"）→ 无效
    if text in _MODIFIER_SUFFIXES:
        return ""
    if text in _INVALID_NAMES:
        return ""
    if len(text) > _MAX_NAME_LENGTH:
        return ""
    if len(text) == 1 and text in _SINGLE_NOISE:
        return ""
    return text


def _strip_modifier(text: str) -> str:
    """剥离动作 / 情绪 / 语气 / 叙述性后缀（最多两层，含「地」结尾状语）。"""
    candidate = text
    for _ in range(2):
        stripped = _strip_one(candidate)
        if stripped is None:
            return candidate
        candidate = stripped
    return candidate


def _strip_one(text: str) -> str | None:
    """剥一层；返回 None 表示无法再剥。"""
    for suffix in _MULTI_SUFFIXES:
        if text.endswith(suffix):
            remainder = text[: -len(suffix)].strip()
            return remainder if remainder else ""
    for suffix in _SINGLE_SUFFIXES:
        if text.endswith(suffix) and len(text) >= 3:
            remainder = text[: -len(suffix)].strip()
            if len(remainder) >= 2:
                return remainder
    if text.endswith(_ADVERB_DE) and len(text) >= 3:
        remainder = text[: -len(_ADVERB_DE)].strip()
        if len(remainder) >= 2:
            return remainder
    return None
