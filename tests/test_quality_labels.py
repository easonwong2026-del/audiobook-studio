"""needs_review →「待试听确认」UI 文案回归测试。

只测展示层映射；后端枚举（not_started / needs_review / needs_fix /
technical_warning / regenerating / passed）与 MCP 返回保持机器值不变。
"""
from __future__ import annotations

from app import (
    _quality_status_label,
    _quality_summary_markdown,
    _review_status_label,
    _technical_outcome_label,
)
from ui.pages.review_page import REVIEW_STATUS_FILTER_CHOICES


def test_needs_review_label_is_待试听确认():
    assert _quality_status_label("needs_review") == "待试听确认"


def test_filter_choice_label_maps_to_needs_review_value():
    labels = dict(REVIEW_STATUS_FILTER_CHOICES)
    assert labels["待试听确认"] == "needs_review"
    assert labels["未生产"] == "not_started"
    assert labels["需修复"] == "needs_fix"
    assert labels["技术警告"] == "technical_warning"
    assert labels["已通过"] == "passed"


def test_not_started_still_shows_未生产_and_尚未开始生产():
    assert _quality_status_label("not_started") == "未生产"
    summary = _quality_summary_markdown({
        "summary": {
            "segments": 20,
            "not_started": 20,
            "production_status": "not_started",
            "needs_review": 0,
            "needs_fix": 0,
            "technical_warning": 0,
            "regenerating": 0,
            "passed": 0,
        },
    })
    assert "尚未开始生产" in summary
    assert "待试听确认" not in summary


def test_technical_warning_label_unchanged():
    assert _quality_status_label("technical_warning") == "技术警告"


def test_passed_label_unchanged():
    assert _quality_status_label("passed") == "通过"


def test_review_detail_dimensions_are_separated():
    # 技术检查维度
    assert _technical_outcome_label("pass") == "通过"
    assert _technical_outcome_label("none") == "未执行"
    assert _technical_outcome_label("fail") == "异常"
    # 人工试听维度
    assert _review_status_label("unreviewed") == "待确认"
    assert _review_status_label("passed") == "已通过"
    assert _review_status_label("needs_fix") == "需要修复"


def test_summary_uses_待试听确认_for_needs_review():
    summary = _quality_summary_markdown({
        "summary": {
            "segments": 45,
            "not_started": 20,
            "production_status": "started",
            "needs_review": 6,
            "needs_fix": 1,
            "technical_warning": 0,
            "regenerating": 0,
            "passed": 18,
        },
    })
    assert "待试听确认 **6**" in summary
    assert "待检查" not in summary
