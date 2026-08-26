"""Round 2B behavior and ownership contracts for voice presentation helpers."""
from __future__ import annotations

from pathlib import Path

import pytest

from ui.components import voice_binding


ROOT = Path(__file__).parents[1]
VOICE_SOURCE = (ROOT / "ui" / "components" / "voice_binding.py").read_text(
    encoding="utf-8"
)


CASES = [
    pytest.param(
        "A_all_unbound",
        {"voices": {"旁白": {"description": "Narrator"}, "小明": {"description": "Kid"}, "配角": {"description": "Side"}}},
        {},
        {},
        [("【未绑定】小明", "小明"), ("【未绑定】旁白", "旁白"), ("【未绑定】配角", "配角")],
        [],
        [("【未绑定】小明（Kid）", "小明"), ("【未绑定】旁白（Narrator）", "旁白"), ("【未绑定】配角（Side）", "配角")],
        [],
    ),
    pytest.param(
        "B_partial_no_categories",
        {"voices": {"旁白": {}, "小明": {}, "配角": {}}},
        {"旁白": "/a.wav", "小明": "", "配角": "/c.wav"},
        {},
        [("【未绑定】小明", "小明"), ("【未分类】旁白", "旁白"), ("【未分类】配角", "配角")],
        [("【已绑定】旁白", "旁白"), ("【已绑定】配角", "配角")],
        [("【未绑定】小明", "小明"), ("【未分类】旁白", "旁白"), ("【未分类】配角", "配角")],
        [("【已绑定】旁白", "旁白"), ("【已绑定】配角", "配角")],
    ),
    pytest.param(
        "C_categories",
        {"voices": {"z": {}, "a": {}, "m": {}, "u": {}, "n": {}}},
        {"z": "/z", "a": "/a", "m": "", "u": "", "n": "/n"},
        {"z": "主角", "a": "主角", "m": "配角", "n": "主角"},
        [("【主角】a", "a"), ("【主角】n", "n"), ("【主角】z", "z"), ("【配角】m", "m"), ("【未绑定】u", "u")],
        [("【已绑定】z", "z"), ("【已绑定】a", "a"), ("【已绑定】n", "n")],
        [("【主角】a", "a"), ("【主角】n", "n"), ("【主角】z", "z"), ("【配角】m", "m"), ("【未绑定】u", "u")],
        [("【已绑定】z", "z"), ("【已绑定】a", "a"), ("【已绑定】n", "n")],
    ),
    pytest.param(
        "D_same_category",
        {"voices": {"z": {}, "a": {}, "m": {}}},
        {"z": "/z", "a": "/a", "m": "/m"},
        {"z": "同组", "a": "同组", "m": "同组"},
        [("【同组】a", "a"), ("【同组】m", "m"), ("【同组】z", "z")],
        [("【已绑定】z", "z"), ("【已绑定】a", "a"), ("【已绑定】m", "m")],
        [("【同组】a", "a"), ("【同组】m", "m"), ("【同组】z", "z")],
        [("【已绑定】z", "z"), ("【已绑定】a", "a"), ("【已绑定】m", "m")],
    ),
    pytest.param(
        "E_tail_order",
        {"voices": {"u": {}, "b": {}, "c": {}, "a": {}}},
        {"u": "", "b": "/b", "c": "/c", "a": ""},
        {},
        [("【未绑定】a", "a"), ("【未绑定】u", "u"), ("【未分类】b", "b"), ("【未分类】c", "c")],
        [("【已绑定】b", "b"), ("【已绑定】c", "c")],
        [("【未绑定】a", "a"), ("【未绑定】u", "u"), ("【未分类】b", "b"), ("【未分类】c", "c")],
        [("【已绑定】b", "b"), ("【已绑定】c", "c")],
    ),
    pytest.param(
        "F_bound_only",
        {"voices": {"z": {}, "a": {}, "m": {}}},
        {"z": "/z", "a": "", "m": "/m"},
        {},
        [("【未绑定】a", "a"), ("【未分类】m", "m"), ("【未分类】z", "z")],
        [("【已绑定】z", "z"), ("【已绑定】m", "m")],
        [("【未绑定】a", "a"), ("【未分类】m", "m"), ("【未分类】z", "z")],
        [("【已绑定】z", "z"), ("【已绑定】m", "m")],
    ),
    pytest.param(
        "G_description",
        {"voices": {"旁白": {"description": "Narrator", "name": "Name"}}},
        {"旁白": "/a"},
        {"旁白": "主角"},
        [("【主角】旁白", "旁白")],
        [("【已绑定】旁白", "旁白")],
        [("【主角】旁白（Narrator）", "旁白")],
        [("【已绑定】旁白（Narrator）", "旁白")],
    ),
    pytest.param(
        "H_name_only",
        {"voices": {"旁白": {"name": "Name"}}},
        {"旁白": "/a"},
        {"旁白": "主角"},
        [("【主角】旁白", "旁白")],
        [("【已绑定】旁白", "旁白")],
        [("【主角】旁白（Name）", "旁白")],
        [("【已绑定】旁白（Name）", "旁白")],
    ),
    pytest.param(
        "I_empty_voice_metadata",
        {"voices": {"旁白": {}}},
        {"旁白": "/a"},
        {"旁白": "主角"},
        [("【主角】旁白", "旁白")],
        [("【已绑定】旁白", "旁白")],
        [("【主角】旁白", "旁白")],
        [("【已绑定】旁白", "旁白")],
    ),
]
@pytest.mark.parametrize(
    "name,script,bindings,role_categories,expected_roles,expected_bound,expected_formatted,expected_formatted_bound",
    CASES,
)
def test_voice_choice_helpers_match_baseline_fixture(
    name,
    script,
    bindings,
    role_categories,
    expected_roles,
    expected_bound,
    expected_formatted,
    expected_formatted_bound,
):
    """Expected tuples are the pre-migration outputs captured on the baseline."""
    assert name
    assert voice_binding.build_role_choices(script, bindings, role_categories) == expected_roles
    assert voice_binding.build_bound_role_choices(script, bindings) == expected_bound
    assert voice_binding.format_role_choices(script, bindings, role_categories) == expected_formatted
    assert voice_binding.format_bound_role_choices(script, bindings) == expected_formatted_bound


def test_role_choice_presentation_is_ui_owned():
    assert "project_manager" not in VOICE_SOURCE
    assert "_pm" not in VOICE_SOURCE


def test_role_choice_helpers_are_pure_presentation_only():
    assert "ProjectRepository" not in VOICE_SOURCE
    assert "ProjectService" not in VOICE_SOURCE
    assert "voice_bindings.json" not in VOICE_SOURCE
