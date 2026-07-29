from ui.create_project_handlers import format_creation_warnings


def test_creation_warnings_are_escaped_and_limited():
    warnings = ["<script>alert(1)</script>"] + [f"warning-{index}" for index in range(25)]
    rendered = format_creation_warnings(warnings)
    assert "共 26 项 warning" in rendered
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert rendered.count("\n- ") == 10
    assert "另有 16 条未展示" in rendered


def test_empty_creation_warnings_render_nothing():
    assert format_creation_warnings([]) == ""
