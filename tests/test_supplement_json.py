"""5.1：补录 JSON 格式检测（紧凑 vs 标准）及所有失败分支。

覆盖：
- parse_input_json 识别紧凑格式并正确分发；
- parse_input_json 识别标准格式并正确分发；
- 角色不存在时给出明确错误；
- 缺少 role/lines 字段时抛出可读错误；
- 非法 JSON 字符串抛出可读错误；
- 空文本/空行处理。
"""
from __future__ import annotations

import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from services.supplement import SupplementService  # noqa: E402


def _make_script(voices=None):
    """构建一个最小标准的剧本 dict 用于 parse_input_json 的 script 参数。"""
    return {
        "title": "测试剧本",
        "voices": voices or {" narrator": {"name": "旁白", "gender": "male"}},
        "chapters": [
            {
                "title": "第一章",
                "segments": [
                    {"id": "1-001", "role": " narrator", "text": "测试段"},
                ],
            }
        ],
    }


class TestParseCompactJson:
    """紧凑格式：{"role": "...", "lines": [...]}"""

    def test_happy_path_text_lines(self):
        """普通字符串列表 lines"""
        raw = {"role": " narrator", "lines": ["第一句", "第二句", "第三句"]}
        script = _make_script()
        role, lines = SupplementService.parse_input_json(raw, script)
        assert role == " narrator"
        assert lines == ["第一句", "第二句", "第三句"]

    def test_happy_path_object_lines(self):
        """lines 包含 {"text": ...} 对象"""
        raw = {
            "role": " narrator",
            "lines": [{"text": "第一句"}, {"text": "第二句"}],
        }
        script = _make_script()
        role, lines = SupplementService.parse_input_json(raw, script)
        assert role == " narrator"
        assert len(lines) == 2

    def test_role_not_in_voices(self):
        """角色不存在于剧本 voices 时抛 ValueError"""
        raw = {"role": "unknown_role", "lines": ["测试"]}
        script = _make_script()
        with pytest.raises(ValueError, match="未在项目剧本 voices 中定义"):
            SupplementService.parse_input_json(raw, script)

    def test_missing_role(self):
        """缺少 role 字段"""
        raw = {"lines": ["测试"]}
        script = _make_script()
        with pytest.raises(ValueError, match="role|格式|无法识别"):
            SupplementService.parse_input_json(raw, script)

    def test_missing_lines(self):
        """缺少 lines 字段"""
        raw = {"role": " narrator"}
        script = _make_script()
        with pytest.raises(ValueError, match="lines|格式|无法识别"):
            SupplementService.parse_input_json(raw, script)

    def test_empty_lines(self):
        """lines 为空列表"""
        raw = {"role": " narrator", "lines": []}
        script = _make_script()
        with pytest.raises(ValueError, match="空|empty|lines"):
            SupplementService.parse_input_json(raw, script)

    def test_invalid_json(self):
        """非法 JSON 字符串——dict 之外的顶层类型抛格式错误"""
        script = _make_script()
        with pytest.raises(ValueError, match="顶层应为对象"):
            SupplementService.parse_input_json("not valid json{{{", script)


class TestParseStructuredJson:
    """标准格式：包含 voices/chapters 的 structured_script.json 子集"""

    def test_happy_path(self):
        """标准 structured_script.json 子集"""
        raw = {
            "voices": {" narrator": {"name": "旁白", "gender": "male"}},
            "chapters": [
                {
                    "title": "补录章",
                    "segments": [
                        {"id": "sup-001", "role": " narrator", "text": "补录段"},
                    ],
                }
            ],
        }
        script = _make_script()
        role, lines = SupplementService.parse_input_json(raw, script)
        # 标准格式走原有 parse_structured_json，返回 role 和 lines
        assert role is not None
        assert len(lines) > 0

    def test_role_not_in_script_voices(self):
        """标准格式中 voice 不在父剧本 voices 内时抛出错误"""
        raw = {
            "voices": {"bad_role": {"name": "未知", "gender": "male"}},
            "chapters": [
                {
                    "title": "补录章",
                    "segments": [
                        {"id": "sup-001", "role": "bad_role", "text": "测试"},
                    ],
                }
            ],
        }
        script = _make_script()
        with pytest.raises((ValueError, KeyError)):
            SupplementService.parse_input_json(raw, script)


class TestParseInputJsonDispatch:
    """parse_input_json 的分发逻辑"""

    def test_compact_format_dispatch(self):
        """紧凑格式被正确分发到 parse_compact_json"""
        raw = {"role": " narrator", "lines": ["第一句"]}
        script = _make_script()
        role, lines = SupplementService.parse_input_json(raw, script)
        assert role == " narrator"
        assert lines == ["第一句"]

    def test_structured_format_dispatch(self):
        """标准格式被正确分发到 parse_structured_json"""
        raw = {
            "voices": {" narrator": {"name": "旁白", "gender": "male"}},
            "chapters": [
                {
                    "title": "章",
                    "segments": [
                        {"id": "sup-001", "role": " narrator", "text": "段"},
                    ],
                }
            ],
        }
        script = _make_script()
        role, lines = SupplementService.parse_input_json(raw, script)
        assert role is not None
        assert len(lines) > 0

    def test_unrecognized_format_raises(self):
        """既不是紧凑也不是标准格式时抛出可读错误"""
        raw = {"foo": "bar"}
        script = _make_script()
        with pytest.raises(ValueError, match="无法识别|格式|format"):
            SupplementService.parse_input_json(raw, script)
