"""工作台状态卡的纯展示层。"""
from __future__ import annotations

from html import escape
from typing import Iterable


def _issues_html(issues: Iterable[tuple[str, str]]) -> str:
    rows = list(issues)
    if not rows:
        return """
        <div class="workbench-card workbench-issues is-clear">
          <div class="card-eyebrow">待处理问题</div>
          <strong>✓ 当前没有阻塞项</strong>
          <p>可以继续检查成品或进入交付。</p>
        </div>
        """
    items = "".join(
        f"<li><span class=\"issue-dot {escape(level)}\"></span>{escape(message)}</li>"
        for level, message in rows
    )
    return f"""
    <div class="workbench-card workbench-issues">
      <div class="card-eyebrow">待处理问题</div>
      <ul>{items}</ul>
    </div>
    """


def empty_dashboard_html() -> tuple[str, str, str, str]:
    """返回尚未选择项目时的四个工作台展示块。"""
    return (
        """
        <section class="workbench-hero empty-state">
          <div><span class="eyebrow">当前项目状态</span>
          <h2>选择项目后开始制作</h2>
          <p>打开已有项目，或创建项目并导入结构化书稿。</p></div>
          <span class="hero-icon">🎧</span>
        </section>
        """,
        """
        <div class="workbench-card next-step-card">
          <div class="card-eyebrow">下一步</div>
          <strong>创建或打开项目</strong>
          <p>先选择一本书，工作台才会显示生产进度和待处理事项。</p>
        </div>
        """,
        """
        <div class="workbench-card task-card">
          <div class="card-eyebrow">最近任务</div>
          <strong>暂无生产任务</strong>
          <p>打开项目后即可查看合成与质检状态。</p>
        </div>
        """,
        _issues_html([]),
    )


def project_dashboard_html(
    *,
    title: str,
    project_name: str,
    chapters_done: int,
    chapters_total: int,
    segments_done: int,
    segments_total: int,
    roles_bound: int,
    roles_total: int,
    task_label: str,
    task_detail: str,
    next_step: str,
    next_detail: str,
    issues: Iterable[tuple[str, str]],
) -> tuple[str, str, str, str]:
    """把工作台所需的摘要数据渲染为四个独立 HTML 区块。"""
    safe_title = escape(title)
    safe_project = escape(project_name)
    safe_task_label = escape(task_label)
    safe_task_detail = escape(task_detail)
    safe_next_step = escape(next_step)
    safe_next_detail = escape(next_detail)
    segment_percent = round((segments_done / segments_total * 100) if segments_total else 0)
    voice_percent = round((roles_bound / roles_total * 100) if roles_total else 0)
    header = f"""
    <section class="workbench-hero">
      <div>
        <span class="eyebrow">当前项目 · {safe_project}</span>
        <h2>{safe_title}</h2>
        <p>查看生产状态，并从下一步操作继续制作。</p>
      </div>
      <div class="hero-progress"><span>{segment_percent}%</span><small>段落完成度</small></div>
    </section>
    """
    status = f"""
    <div class="dashboard-metrics">
      <div class="metric-card"><span>完成章节</span><strong>{chapters_done}<i>/</i>{chapters_total}</strong><small>章节全部段落已完成</small></div>
      <div class="metric-card"><span>合成进度</span><strong>{segments_done}<i>/</i>{segments_total}</strong><div class="metric-track"><b style="width:{segment_percent}%"></b></div></div>
      <div class="metric-card"><span>角色绑定</span><strong>{roles_bound}<i>/</i>{roles_total}</strong><div class="metric-track voices"><b style="width:{voice_percent}%"></b></div></div>
    </div>
    <div class="workbench-card next-step-card">
      <div class="card-eyebrow">下一步</div><strong>{safe_next_step}</strong><p>{safe_next_detail}</p>
    </div>
    """
    task = f"""
    <div class="workbench-card task-card">
      <div class="card-eyebrow">最近任务</div>
      <strong>{safe_task_label}</strong><p>{safe_task_detail}</p>
    </div>
    """
    return header, status, task, _issues_html(issues)
