#!/usr/bin/env python3
"""Audiobook Studio v3 -- Stripe 浅色风 UI（完整重做：左侧分组侧边栏 + 顶部状态条）

v3：基于设计稿（DESIGN.md / brand-spec.md）落地 Stripe 浅色招牌风：
- 主题从暗色（gr.themes.Soft + 注入暗色 <style>）切换为 Stripe 浅色（gr.themes.Default + 浅色令牌）
- 布局从「顶部 4 Tab」重构为「左侧分组侧边栏（6 分类）+ 右侧主工作区 + 顶部常驻状态条」
- 业务逻辑（handlers）完全沿用 v2，未改动。
"""
from __future__ import annotations
import logging
import os, sys, time, tempfile, shutil, json
import gradio as gr

logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ui.theme import THEME, LIGHT_CSS
from ui.navigation import _goto, _GROUPS, create_nav_buttons
from ui.shared import create_status_bar
from ui.pages import (
    create_overview_page,
    create_project_page,
    create_review_page,
    create_export_page,
    create_supplement_page,
    create_voice_page,
    create_synthesis_page,
)
from lib import tts_engine
from lib import script_loader
from lib import segment_cache
from lib import config
from lib import project_manager as _pm
from lib import progress as synth_progress
from lib import audio_pipeline
from lib import voice_lib
from lib import dataframe_style as df_style

from services import ProjectService, ExportService, SynthesisService, SupplementService, SupplementTaskState
from services.session import SessionState
from services.synthesis import SynthesisState

BASE = os.path.dirname(os.path.abspath(__file__))
# 音色库外置于数据目录（默认 ~/AudiobookStudio/voice_library），与程序目录解耦。
# 注意：音色库路径必须在调用时动态解析（config.get_voice_library），
# 不得在此处模块级缓存，否则运行期切换数据目录后路径不会更新（见方案 §5.2）。




# ═══════════ callbacks (unchanged logic, 业务编排迁入 services) ═══════════

def create_project(name, script_file, ss):
    import json as _json
    if not name or not script_file:
        return name, None, "### ⚠ 请输入项目名称并上传 JSON 文件", gr.update()
    try:
        # B12: 先在导入阶段校验剧本，避免非法剧本在合成中途 KeyError 崩溃
        script = script_loader.load_script(script_file)
        errors = script_loader.validate_script(script)
        if errors:
            err_msg = "### ❌ 剧本校验失败：\n" + "\n".join(f"- {e}" for e in errors)
            return name, None, err_msg, gr.update()
        # 业务委托 ProjectService（写 workspace + 写 project.json）
        ProjectService.create_project(name, script_file)
        # 写入会话态（多标签各自独立，不共享全局可变 S）
        ss.set_project(name, None, {})
        return "", None, f"### ✅ 项目「{name}」创建成功！请在右侧下拉框选中它，点击「打开项目」", gr.update(choices=ProjectService.scan_projects())
    except _json.JSONDecodeError:
        # 文件不是合法 JSON（如用户传了 TXT 改名）：给出明确、可操作的提示
        return name, None, (
            "### ❌ 创建失败：上传的文件不是合法 JSON。\n"
            "请确认上传的是由 WorkBuddy 生成的 `structured_script.json`，"
            "而非 .txt / .md 等文本文件改名而来。"
        ), gr.update()
    except Exception as e:
        return name, None, f"### ❌ 创建失败: {e}", gr.update()

def _snap(ss):
    """读取（必要时重建）当前项目快照：优先用会话态快照，缺失时按项目名重建。"""
    s = ss.ensure_snapshot()
    if s is not None:
        return s
    if ss and ss.project:
        rebuilt = ProjectService.open_project_as_snapshot(ss.project)
        ss.set_snapshot(rebuilt)
        return rebuilt
    return None

def open_project(name, ss):
    if not name: return "📖 等待打开项目",gr.update(),gr.update(choices=[]),gr.update(),"",""
    try:
        # 业务委托 ProjectService.open_project_as_snapshot（包 pm.load_snapshot）
        snap = ProjectService.open_project_as_snapshot(name)
        ss.set_project(name, snap.script, snap.bindings)
        ss.set_snapshot(snap)
        role_categories = snap.role_categories
        choices = _pm.build_role_choices(snap.script, ss.bindings, role_categories)
        roles = list(snap.script.get("voices",{}).keys())
        vcount = len(roles)
        bound = sum(1 for v in ss.bindings.values() if v)

        info = f"""### 🎧 {snap.script['meta'].get('title',name)}
<div style="display:flex;gap:20px;margin-top:8px">
<span>📄 **{snap.meta.total_chapters}** 章</span>
<span>🎯 **{vcount}** 角色（{bound} 已绑定）</span>
<span>✅ **{snap.meta.completed_count}** 段已合成</span>
</div>"""
        if snap.meta.failed_count: info += f"\n<span class='status-err'>⚠ {snap.meta.failed_count} 段失败</span>"

        seg_dir = os.path.join(ProjectService.get_project_dir(name),"segments")
        existing = scan_existing_raw(snap, seg_dir)
        log_init = "\n".join(existing[-15:]) if existing else "等待音色配置完成后开始合成..."

        vtable = "| 角色 | 声线 | 音频 |\n|------|------|------|\n"
        for n,i in snap.script.get("voices",{}).items():
            a = ss.bindings.get(n)
            s = f"<span class='status-ok'>✅</span> {os.path.basename(a)}" if a else "<span class='status-warn'>⚠ 待绑定</span>"
            vtable += f"| {n} | {i.get('description','')} | {s} |\n"

        return (info,
                gr.update(visible=True, value=vtable),
                gr.update(choices=choices,value=choices[0][1] if choices else None),
                gr.update(choices=_lib_voices(),value=None),
                log_init,
                gr.update(visible=True))
    except Exception as e:
        return f"### 打开失败\n{e}",gr.update(),gr.update(),gr.update(),"",gr.update()




def refresh_top_status(ss):
    """O11：刷新顶部全局状态栏文本（项目 / 章节 / 进度 / 引擎加载状态）。"""
    if not ss or not ss.project:
        return "*等待打开项目…*"
    try:
        snap = _snap(ss)
        if snap is None:
            meta, script, _ = ProjectService.open_project(ss.project)
        else:
            meta, script = snap.meta, snap.script
        chapters = len(script.get("chapters", []))
        done = getattr(meta, "completed_count", 0)
        total = getattr(meta, "total_segments", 0)
        title = script.get("meta", {}).get("title", ss.project)
        engine_state = "已加载" if getattr(tts_engine, "_tts", None) is not None else "未加载"
        return (f"📖 **{title}** · {chapters} 章 · {done}/{total} 段 · "
                f"引擎: {engine_state}")
    except Exception as exc:
        return f"📖 {ss.project}（状态读取失败：{exc}）"

def delete_project(name):
    if name: ProjectService.delete_project(name)
    return gr.update(choices=ProjectService.scan_projects())


def apply_data_dir(new_dir):
    """应用用户指定的数据保存位置（持久化到 config.json，本会话立即生效）。"""
    if not new_dir or not new_dir.strip():
        return "⚠ 请填写保存位置", config.get_data_dir()
    try:
        d = ProjectService.set_data_dir(new_dir.strip())
        return f"✅ 数据目录已设置为：{d}（本会话立即生效）", d
    except Exception as e:
        return f"❌ 设置失败：{e}", config.get_data_dir()


def open_data_dir():
    """在资源管理器中打开当前数据目录。"""
    d = config.get_data_dir()
    os.makedirs(d, exist_ok=True)
    try:
        os.startfile(d)
    except OSError as exc:
        logger.warning("打开数据目录失败: %s", exc)
    return ""

def _voice_status(s,b):
    rows=[]
    for n,i in s.get("voices",{}).items():
        a=b.get(n);rows.append(f"|{n}|{i.get('description','')}|{'<span class=status-ok>✅</span>' if a else '<span class=status-warn>⚠</span>'}")
    return "|角色|声线|状态|\n|------|------|------|\n"+"\n".join(rows)

def _lib_voices():
    vlib = config.get_voice_library()
    os.makedirs(vlib, exist_ok=True)
    return [f for f in os.listdir(vlib) if f.endswith(('.wav', '.mp3'))]
def _lib_path(n):
    vlib = config.get_voice_library()
    return os.path.join(vlib, n) if n else None
def _safe_name(s):
    """Sanitize filename: replace filesystem-illegal chars (/ : * ? " < > |) with _"""
    import re
    return re.sub(r'[\\/:*?"<>|]', '_', s)

def bind_voice(role, audio_file, from_lib, ss):
    if not ss.project or not role: return "请先打开项目", gr.update(), gr.update(), gr.update()
    src = _lib_path(from_lib) if from_lib else audio_file
    if not src: return "请上传音频、录制或从音色库选择", gr.update(), gr.update(), gr.update()
    # 业务委托 ProjectService.bind_voice（拷贝 + 写 voice_bindings.json），返回 dest
    cat = voice_lib._category_of(os.path.basename(src)) if from_lib else "未分类"
    dest = ProjectService.bind_voice(ss.project, role, src, category=cat)
    # 原地 mutate 会话态绑定表（R1：多标签隔离，不靠返回值回传）
    ss.bindings[role] = dest
    # 写盘后重建快照并刷新会话态绑定表 / 分类映射
    snap = ProjectService.open_project_as_snapshot(ss.project)
    ss.set_snapshot(snap)
    ss.bindings = snap.bindings
    rc = snap.role_categories
    rchoices = _pm.build_role_choices(snap.script, ss.bindings, rc)
    return f"{role} 已绑定", _voice_status(snap.script, ss.bindings), gr.update(), gr.update(choices=rchoices, value=role)

def preview_bound_voice(role, ss):
    """试听 Tab1 当前所选角色已绑定的参考音色（D4 完善）。

    取 ss.bindings[role] 作为参考音频；若存在且文件存在，则初始化引擎后用三句测试句
    （陈述 / 疑问 / 感叹）试音，把三句拼接成一段连续音频返回，便于用单一 ``gr.Audio``
    组件完整播放。否则返回 None。
    """
    if not role:
        return None
    audio = ss.bindings.get(role)
    if not audio or not os.path.isfile(audio):
        return None
    try:
        tts_engine.init_engine()
        parts = tts_engine.test_voice(audio)
        if not parts or not all(os.path.isfile(p) for p in parts):
            return None
        # 把三句测试句拼接为一段连续音频，供单一 gr.Audio 播放
        out_dir = config.get_preview_dir()
        out = os.path.join(out_dir, f"preview_{_safe_name(role)}.wav")
        tts_engine._concat_wavs(parts, out)
        return out if os.path.isfile(out) else None
    except Exception:
        return None

def do_synthesis(ss, num_beams=2, progress=gr.Progress(),
                emotion="(按剧本默认)", s_override=False, emo_alpha=1.0, speech_rate=1.0,
                selected_chapters=None):
    """开始合成：提交后台队列并轮询进度（R1 后台化，不再阻塞 UI）。

    2.3 O2：接收合成期情感 / 语速全局覆盖，持久化到项目 ``synthesis_overrides.json``
    并透传至 ``SynthesisService.start``，保证预览 / 导出缓存键一致。
    """
    proj = ss.project
    bindings = ss.bindings
    script = ss.script or {}
    if not proj:
        yield ("请先在项目管理中打开项目", [])
        return
    missing = [n for n in (script.get("voices", {}) or {}) if n not in bindings or not bindings[n]]
    if missing:
        yield (f"以下角色未绑定: {', '.join(missing)}", [])
        return
    try:
        tts_engine.init_engine()
    except Exception as e:
        yield (f"模型加载失败: {e}", [])
        return
    # 2.3 O2：解析覆盖并持久化，保证预览 / 导出一致
    emotion_override = None if emotion == "(按剧本默认)" else emotion
    overrides = {
        "emotion": emotion_override,
        "override": bool(s_override),
        "emo_alpha": float(emo_alpha),
        "speech_rate": float(speech_rate),
    }
    try:
        _pm.set_synthesis_overrides(proj, overrides)
    except Exception as exc:
        logger.warning("保存合成覆盖参数失败: %s", exc)
    # 5.4：已有合成任务进行中（pending/running/pausing/paused/cancelling）时禁止开启新任务，
    # 避免第二个整本任务覆盖 state 引用导致第一个任务失控。
    if ss.synthesis is not None and ss.synthesis.status in (
        "pending", "running", "pausing", "paused", "cancelling"
    ):
        yield ("⚠ 已有合成任务进行中（状态：" + ss.synthesis.status
               + "），请先停止当前任务再开始新的合成。", [])
        return
    # 准备本次合成任务态（每会话独立），提交后台
    ss.synthesis = SynthesisState(task_id=f"task_{int(time.time()*1000)}", project=proj)
    # O3：初始化内存段态列表（与 O11 共享真相，绝不反向写 meta.segments_status）
    # O5：传入 selected_chapters，使未选中段在内存态标 skipped（⏭）
    ss.synthesis.segment_states = synth_progress.build_segment_states(proj, selected_chapters)
    # O5：持久化本次勾选（非破坏性，与 synthesis_overrides.json 同构）
    try:
        _pm.set_synthesis_selections(proj, {"chapters": selected_chapters or []})
    except Exception as exc:
        logger.warning("保存合成勾选失败: %s", exc)
    SynthesisService.start(
        ss.synthesis, proj, bindings, num_beams=num_beams,
        emotion=emotion_override,
        emo_alpha=emo_alpha if s_override else None,
        speech_rate=speech_rate if s_override else None,
        selected_chapters=selected_chapters,
    )
    state = ss.synthesis
    # 轮询直到终态，~0.5s 刷新一次日志 + 进度条 + 队列列表
    while state.status not in ("done", "cancelled", "error"):
        time.sleep(0.5)
        try:
            progress(state.progress, f"{state.completed}/{state.total}")
        except Exception as exc:
            logger.debug("进度回调异常（进行中）: %s", exc)
        yield (state.snapshot_text(), df_style.style_dataframe(synth_progress.to_queue_rows(state.segment_states), synth_progress.QUEUE_HEADERS, status_col=0, status_color_map=df_style.ICON_COLORS))
    # 终态再刷一次
    try:
        progress(state.progress, f"{state.completed}/{state.total}")
    except Exception as exc:
        logger.debug("进度回调异常（终态）: %s", exc)
    yield (state.snapshot_text(), df_style.style_dataframe(synth_progress.to_queue_rows(state.segment_states), synth_progress.QUEUE_HEADERS, status_col=0, status_color_map=df_style.ICON_COLORS))

def cancel(ss):
    """停止合成：置协作取消标志（worker 在下一段前检查 -> 段边界生效）。"""
    if ss.synthesis is not None:
        SynthesisService.cancel(ss.synthesis)
    return "停止中..."

def pause_synthesis(ss):
    """O12：暂停合成（协作暂停，段边界挂起，不杀进行中进程）。

    仅在 ``ss.synthesis`` 存在且 ``status in (running, paused)`` 时生效；否则返回提示不报错。
    返回 (队列列表, 暂停按钮, 恢复按钮) 的更新三元组。
    """
    if ss.synthesis is None or ss.synthesis.status not in ("running", "paused"):
        return (gr.update(), gr.update(), gr.update())
    SynthesisService.pause(ss.synthesis)
    rows = df_style.style_dataframe(
        synth_progress.to_queue_rows(ss.synthesis.segment_states),
        synth_progress.QUEUE_HEADERS,
        status_col=0,
        status_color_map=df_style.ICON_COLORS,
    )
    return (
        rows,
        gr.update(value="⏸ 已暂停", interactive=False),
        gr.update(interactive=True),
    )

def resume_synthesis(ss):
    """O12：恢复合成（paused -> running，worker 退出段边界挂起继续提交新段）。

    仅在 ``ss.synthesis`` 存在且 ``status == 'paused'`` 时生效；否则返回提示不报错。
    返回 (队列列表, 暂停按钮, 恢复按钮) 的更新三元组。
    """
    if ss.synthesis is None or ss.synthesis.status != "paused":
        return (gr.update(), gr.update(), gr.update())
    SynthesisService.resume(ss.synthesis)
    rows = df_style.style_dataframe(
        synth_progress.to_queue_rows(ss.synthesis.segment_states),
        synth_progress.QUEUE_HEADERS,
        status_col=0,
        status_color_map=df_style.ICON_COLORS,
    )
    return (
        rows,
        gr.update(value="⏸ 暂停", interactive=True),
        gr.update(interactive=False),
    )

def refresh_queue_list(ss):
    """O3：空闲/打开项目时填充队列进度列表（读内存段态或据项目重建）。

    与 O11 ``refresh_top_status`` 共享状态源约定：O11 读 meta（粗粒度），本函数读
    ``state.segment_states``（细粒度）；不互相写、不反向写 meta。
    """
    if ss and ss.synthesis is not None and ss.synthesis.segment_states:
        return df_style.style_dataframe(
            synth_progress.to_queue_rows(ss.synthesis.segment_states),
            synth_progress.QUEUE_HEADERS,
            status_col=0,
            status_color_map=df_style.ICON_COLORS,
        )
    if ss and ss.project:
        try:
            return df_style.style_dataframe(
                synth_progress.to_queue_rows(synth_progress.build_segment_states(ss.project)),
                synth_progress.QUEUE_HEADERS,
                status_col=0,
                status_color_map=df_style.ICON_COLORS,
            )
        except Exception:
            return df_style.style_dataframe([], synth_progress.QUEUE_HEADERS, status_col=0, status_color_map=df_style.ICON_COLORS)
    return df_style.style_dataframe([], synth_progress.QUEUE_HEADERS, status_col=0, status_color_map=df_style.ICON_COLORS)

def scan_existing_raw(snap, seg_dir):
    # 阶段三：直接读快照的 meta.segments_status + script，不再重复读盘。
    meta = snap.meta
    script = snap.script
    lines=[]
    for ch in script.get("chapters",[]):
        for seg in ch.get("segments",[]):
            if meta.segments_status.get(seg['id'])=="done":
                lines.append(f"✅ {seg['id']} {seg['role']}")
    return lines

def _safe_path_for_file_component(path):
    """确保返回给 gr.File 的路径位于 Gradio allowed_paths 内（data_dir 子树或 tempdir）。

    导出目录若设在数据目录（app.launch 的 allowed_paths）之外，gr.File 会因
    InvalidPathError 报错。用户的目标文件已落在其指定目录，这里仅给应用内下载
    链接返回一份 data_dir / tempdir 内的副本，原文件不动。
    """
    if not path or not os.path.isfile(path):
        return path
    data_dir = config.get_data_dir()
    if data_dir:
        try:
            if os.path.commonpath([os.path.abspath(path), os.path.abspath(data_dir)]) == os.path.abspath(data_dir):
                return path  # 已在白名单内，原样返回
        except ValueError:
            pass
    # 落到 tempdir（Gradio 默认允许 serve），复制一份副本供下载
    tmp_dir = tempfile.gettempdir()
    base = os.path.basename(path)
    dst = os.path.join(tmp_dir, f"audiobook_export_{base}")
    if os.path.exists(dst):
        dst = os.path.join(tmp_dir, f"audiobook_export_{int(time.time() * 1000)}_{base}")
    try:
        shutil.copy2(path, dst)
    except Exception:
        return path  # 复制失败就退回原路径，不阻断导出结果
    return dst


def do_export(fmt, bitrate, output_dir, *args):
    """一键导出（D1：用 *args 吸收 ss，零改动过 glue 测试）。"""
    ss = args[0] if args else None
    if not ss or not ss.project:
        return None, "请先打开项目"
    try:
        out = ExportService.export(ProjectService.get_project_dir(ss.project), fmt, bitrate, output_dir)
        return _safe_path_for_file_component(out), "导出完成"
    except Exception as e:
        # R2：显式报错（含中间 WAV 路径 / ffmpeg 安装链接 / 可改 WAV 建议）
        return None, str(e)

def do_export_subtitles(ss, sub_choice):
    """O1：生成字幕（srt / lrc），走全新 handler，绝不改 do_export 三参签名与接线。

    Args:
        ss: 会话态（首参，满足 AST 红线 handler 必接 ss）。
        sub_choice: 字幕格式选择，"none" / "srt" / "lrc" / "both"。
    """
    if not ss or not ss.project:
        return None, "请先打开项目"
    if not sub_choice or sub_choice == "none":
        return None, "未选择字幕格式"
    fmts = ("srt", "lrc") if sub_choice == "both" else (sub_choice,)
    try:
        paths = ExportService.export_subtitles(
            ProjectService.get_project_dir(ss.project), formats=fmts
        )
        if not paths:
            return None, "未找到已合成段落，无法生成字幕（请先合成）"
        return paths, "字幕已生成"
    except Exception as e:
        return None, str(e)

def preview_chapters(ss):
    if not ss.project: return "*请先在项目管理中打开项目*",None,gr.update(choices=[])
    # 阶段三：复用会话态快照的剧本 dict，不再直接读盘。
    snap = _snap(ss); script = snap.script
    proj_dir=ProjectService.get_project_dir(ss.project)
    seg_dir=os.path.join(proj_dir,"segments")
    def _f(sid,t,r,e,ea=1.0,sr=1.0,ph=None):
        # B7：参数感知缓存键优先，旧版裸文件回退
        return segment_cache.find_segment_wav(seg_dir, sid, t, r, e, ea, sr, ph)
    lines=["| 章节 | 完成 | 详情 |","|------|------|------|"]
    chapter_rows=[]; first_audio=None; seg_choices=[]; td=0; ta=0
    for ch in script.get("chapters",[]):
        segs=ch.get("segments",[]); ta+=len(segs)
        done=[]; miss=[]
        for seg in segs:
            fp=_f(seg['id'],seg['text'],seg['role'],seg.get('emotion','neutral'),seg.get('emo_alpha',1.0),seg.get('speech_rate',1.0),seg.get('pinyin_hints'))
            if fp: done.append(seg['id']); seg_choices.append(f"{seg['id']} {seg['role']}")
            else: miss.append(seg['id'])
            if first_audio is None and fp: first_audio=fp
        td+=len(done)
        d=f"{len(done)}/{len(segs)}"
        if done: d+=f" ✅ {', '.join(done[:4])}"+(f" +{len(done)-4}" if len(done)>4 else "")
        if miss and len(miss)<=2: d+=f" ❌ {', '.join(miss)}"
        chapter_rows.append(f"| 第{ch['id']}章 {ch['title']} | {len(done)}/{len(segs)} | {d} |")
    # T5：仅截断「展示用 summary 文本」（按章上限，超出加说明）；
    #     严禁截断 seg_choices（e_seg_sel 下拉需完整，供长书导出页选段试听/重合成）。
    MAX_CHAPTER_ROWS=100
    if len(chapter_rows)>MAX_CHAPTER_ROWS:
        lines+=chapter_rows[:MAX_CHAPTER_ROWS]
        lines.append(f"| … | … | 其余 {len(chapter_rows)-MAX_CHAPTER_ROWS} 章（详情见导出页） |")
    else:
        lines+=chapter_rows
    summary=f"### 📊 {td}/{ta} 段已完成\n\n"+"\n".join(lines)
    if td==0: summary+="\n\n⚠ 未检测到合成段落"
    return summary,first_audio,gr.update(choices=seg_choices,value=seg_choices[0] if seg_choices else None)

def play_segment(choices, ss):
    if not ss.project or not choices: return None
    if isinstance(choices,list): choices=choices[0] if choices else None
    if not choices: return None
    # 阶段三：复用会话态快照的剧本 dict。
    script = _snap(ss).script
    sid=choices.split(" ")[0]; proj_dir=ProjectService.get_project_dir(ss.project)
    seg_dir=os.path.join(proj_dir,"segments")
    # B7：参数感知缓存键优先，旧版裸文件回退
    for ch in script.get("chapters",[]):
        for seg in ch.get("segments",[]):
            if seg["id"]==sid:
                return segment_cache.find_segment_wav(
                    seg_dir, sid, seg["text"], seg["role"],
                    seg.get("emotion","neutral"),
                    seg.get("emo_alpha",1.0),
                    seg.get("speech_rate",1.0),
                    seg.get("pinyin_hints"),
                )
    return None

def regenerate_segment(choices, emotion, emo_alpha, speech_rate, voice_choice, ss):
    if not ss.project or not choices: return None,"请选择段落"
    if isinstance(choices,str): choices=[choices]
    bindings=ss.bindings
    # 阶段三：复用会话态快照的剧本 dict。
    script = _snap(ss).script
    proj_dir=ProjectService.get_project_dir(ss.project)
    tts_engine.init_engine(); seg_dir=os.path.join(proj_dir,"segments")
    os.makedirs(seg_dir,exist_ok=True); results=[]
    # 如果选了音色库音频，覆盖绑定
    override_voice = _lib_path(voice_choice) if voice_choice else None
    for choice in choices:
        sid=choice.split(" ")[0]
        for ch in script.get("chapters",[]):
            for seg in ch.get("segments",[]):
                if seg["id"]!=sid: continue
                speaker = override_voice or bindings.get(seg["role"])
                if not speaker: results.append(f"❌ {sid}: 角色未绑定"); break
                try:
                    # B7：重合成写入参数感知缓存键路径，与批量链路命名一致
                    out=segment_cache.segment_wav_path(seg_dir, sid, emotion, emo_alpha, speech_rate, seg.get("pinyin_hints"))
                    tts_engine.synthesize_segment(text=seg["text"],speaker_audio=speaker,
                        emotion=emotion, emo_alpha=emo_alpha, speech_rate=speech_rate,
                        pinyin_hints=seg.get("pinyin_hints"), output_path=out)
                    ProjectService.update_segment_status(ss.project,sid,"done"); results.append(f"✅ {sid}")
                except Exception as e: results.append(f"❌ {sid}: {str(e)[:40]}")
                break
    first_sid=choices[0].split(" ")[0]; first_fp=None
    for ch in script.get("chapters",[]):
        for seg in ch.get("segments",[]):
            if seg["id"]==first_sid:
                # B7：用同一缓存键推导真实 wav 名
                first_fp=segment_cache.find_segment_wav(seg_dir, first_sid, seg["text"], seg["role"],
                    seg.get("emotion","neutral"), seg.get("emo_alpha",1.0),
                    seg.get("speech_rate",1.0), seg.get("pinyin_hints"))
                break
        if first_fp: break
    # 2.4 M-3：批量重合成结束后释放碎片化显存（不卸载模型）
    tts_engine.empty_cache()
    # 段状态已写盘（ProjectService.update_segment_status），使快照失效以便下次读取重载
    ss.invalidate_snapshot()
    return (first_fp, "\n".join(results))

# ═══════════ 角色单独补录 / 补合成导出（T1-T4） ═══════════

def refresh_supplement_roles(ss):
    """补录角色下拉懒刷新：仅列已绑定音色的角色（未开项目/未绑定时禁用并提示）。

    约定：补录角色下拉 ``sup_role`` 只列「已绑定音色角色」，用
    ``project_manager.build_bound_role_choices(script, bindings)``；刷新时机为
    ``nav_supplement.click`` 时懒刷新；刷新时机与打开项目链路解耦（阶段三重构后已无 22 元组契约）。
    """
    if not ss or not ss.project or not ss.script:
        return gr.update(interactive=False, choices=[], value=None,
                         info="请先打开项目并绑定角色音色")
    choices = _pm.build_bound_role_choices(ss.script, ss.bindings)
    if not choices:
        return gr.update(interactive=False, choices=[], value=None,
                         info="请先打开项目并绑定角色音色")
    return gr.update(interactive=True, choices=choices, value=choices[0][1])


def do_supplement_parse_json(sup_json, ss):
    """解析上传的小 JSON：校验角色命中 + 至少一句文本，回填角色下拉与状态 state。

    Returns:
        ``(sup_role 更新, sup_json_role state, sup_json_lines state, 状态 markdown)``。
        失败时不改变 state（保持原角色 / 文本），仅给出诊断 markdown。
    """
    if not ss or not ss.project or not ss.script:
        return (gr.update(interactive=False, choices=[], value=None,
                          info="请先打开项目并绑定角色音色"),
                "", [], "❌ 请先打开项目")
    if not sup_json:
        return (gr.update(), "", [], "❌ 请先上传小 JSON 文件")
    # gr.File 在 4.x 返回 FileData；兼容 str 与 .name
    path = sup_json if isinstance(sup_json, str) else getattr(sup_json, "name", None)
    if not path or not os.path.isfile(path):
        return (gr.update(), "", [], "❌ 小 JSON 文件无效或不存在")
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        return (gr.update(), "", [], f"❌ 小 JSON 不是合法 JSON：{e}")
    except Exception as e:
        return (gr.update(), "", [], f"❌ 读取小 JSON 失败：{e}")
    try:
        role, lines = SupplementService.parse_input_json(raw, ss.script)
    except ValueError as e:
        return (gr.update(), "", [], "❌ 小 JSON 解析失败：\n" + str(e))
    except Exception as e:
        return (gr.update(), "", [], f"❌ 小 JSON 解析异常：{e}")
    preview = "### ✅ 小 JSON 解析成功\n"
    preview += f"- **角色**：{role}\n"
    preview += f"- **句数**：{len(lines)}\n"
    preview += "\n" + "\n".join(f"{i + 1}. {ln[:50]}" for i, ln in enumerate(lines[:20]))
    if len(lines) > 20:
        preview += f"\n… 其余 {len(lines) - 20} 句"
    return (gr.update(value=role), role, lines, preview)


def do_supplement_synth(sup_role, sup_mode, sup_text, sup_json_role, sup_json_lines,
                        sup_emotion, sup_emo_alpha, sup_rate, sup_quality,
                        sup_split_punct, sup_voice, ss):
    """逐句补合成：按模式取（角色, 文本）→ 逐句 synthesize → 收集 wav + 逐句状态。

    输入模式（``sup_mode``）：
      - ``"paste"``：角色=``sup_role`` 下拉，文本=``sup_text`` 按行拆分（可选按标点切长段）；
      - ``"json"``：角色/文本来自解析小 JSON 的 state（``sup_json_role`` / ``sup_json_lines``）。

    Returns:
        ``(sup_wavs state, 状态 markdown)``；状态 markdown 含逐句 ✅ / ❌ 句N 反馈。
    """
    if not ss or not ss.project or not ss.script:
        return [], "❌ 请先打开项目并绑定角色音色"
    # 决定角色与文本
    if sup_mode == "json":
        role = sup_json_role
        lines = list(sup_json_lines or [])
    else:
        role = sup_role
        lines = SupplementService.split_lines(sup_text or "", split_long=bool(sup_split_punct))
    if not role:
        return [], "❌ 未选择角色（请先刷新并选择已绑定音色的角色）"
    if not lines:
        return [], "❌ 没有可合成的文本（请粘贴内容，或先解析小 JSON）"

    # 音色真相源：参考音频唯一来自 ss.bindings[role]；P1 换音色仅本次覆盖、不回写 ss.bindings。
    override_voice = _lib_path(sup_voice) if sup_voice else None
    speaker = override_voice or ss.bindings.get(role)
    if not speaker:
        return [], f"❌ 角色「{role}」未绑定音色，且未选择替换音色"

    # 全局覆盖参数（P1 透传 synthesize_lines(overrides)）；(按默认) 时走引擎默认。
    use_override = sup_emotion not in (None, "(按默认)")
    overrides = {
        "emotion": (sup_emotion if use_override else None),
        "emo_alpha": (float(sup_emo_alpha) if use_override else None),
        "speech_rate": (float(sup_rate) if use_override else None),
    }
    num_beams = int(sup_quality) if sup_quality else 2

    # 5.3：任务隔离——每次补录独立目录（task_id 用 uuid，非秒级时间戳），互不覆盖。
    # 产物落在 <data_dir>/preview/supplement_tasks/<task_id>/（001.wav... + manifest.json + preview.wav）。
    import uuid as _uuid
    from lib import audio_format as _af
    task_id = _uuid.uuid4().hex
    task_dir = os.path.join(config.get_workspace_paths().task_cache_dir, task_id)
    os.makedirs(task_dir, exist_ok=True)
    task = SupplementTaskState(
        task_id=task_id, project=ss.project, role=role,
        status="running",
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        task_dir=task_dir,
    )
    # 清理过期任务（>7 天），避免 supplement_tasks 无限增长
    try:
        SupplementService.cleanup_old_tasks(max_age_days=7)
    except Exception:  # pylint: disable=broad-except
        pass

    tts_engine.init_engine()
    try:
        results = SupplementService.synthesize_lines(
            role=role, lines=lines, speaker_audio=speaker,
            overrides=overrides, num_beams=num_beams, task=task,
        )
    except Exception as e:
        tts_engine.empty_cache()
        task.status = "error"
        return [], f"❌ 补合成异常：{str(e)[:200]}"
    finally:
        # 2.4 M-3：补合成结束后释放碎片化显存（不卸载模型，与 regenerate_segment 同策略）。
        tts_engine.empty_cache()

    # 写 manifest.json（任务隔离产物清单，便于回放 / 调试）
    try:
        manifest = {
            "task_id": task.task_id,
            "project": task.project,
            "role": task.role,
            "created_at": task.created_at,
            "items": [
                {"index": it.index, "text": it.text,
                 "wav_path": it.wav_path, "status": it.status, "error": it.error}
                for it in task.items
            ],
        }
        with open(os.path.join(task_dir, "manifest.json"), "w", encoding="utf-8") as mf:
            json.dump(manifest, mf, ensure_ascii=False, indent=2)
    except Exception:  # pylint: disable=broad-except
        pass

    ok_items = [it for it in task.items if it.status == "ok"]
    task.status = "done" if ok_items else "error"

    # 生成预览合并音频 preview.wav（任务目录内，供试听）
    wav_paths = [it.wav_path for it in task.items if it.wav_path]
    preview_path = os.path.join(task_dir, "preview.wav")
    if wav_paths:
        try:
            combined, rate, _ = _af.concatenate_normalized(
                wav_paths, target_rate=None, target_channels=1,
                target_dtype=_af.DEFAULT_TARGET_DTYPE,
            )
            _af.write_wav(preview_path, combined, rate)
        except Exception:  # pylint: disable=broad-except
            preview_path = None

    md = [f"### 🎙 补合成完成（{len(wav_paths)}/{len(results)} 成功）"]
    for r in results:
        txt = (r.get("text") or "")[:30]
        if r["status"] == "ok":
            md.append(f"- ✅ 句{r['index'] + 1}: {txt}")
        else:
            md.append(f"- {r['error']}")
    md.append(f"\n> 任务 ID：`{task_id}`｜产物目录：`{task_dir}`")
    return wav_paths, "\n".join(md)


def do_supplement_export(sup_format, sup_bitrate, sup_wavs, sup_role, ss):
    """把已合成的补录 wav 导出为独立音频（不进整本拼接），经白名单后返回下载路径。

    Returns:
        ``(_safe_path, msg)``；``_safe_path`` 经 ``_safe_path_for_file_component``
        确保落在 Gradio allowed_paths 内，再交给 ``gr.File`` 下载。
    """
    if not ss or not ss.project:
        return None, "请先打开项目"
    wavs = [w for w in (sup_wavs or []) if w and os.path.isfile(w)]
    if not wavs:
        return None, "❌ 没有可导出的补录音频（请先逐句补合成）"
    role = sup_role or "角色"
    project_dir = ProjectService.get_project_dir(ss.project)
    out_path = SupplementService.build_output_path(project_dir, role, sup_format)
    meta = ss.script.get("meta", {}) if isinstance(ss.script, dict) else {}
    title = f"{meta.get('title', 'audiobook')} - {role} 补录" if meta else None
    artist = meta.get("author") if meta else None
    try:
        final = audio_pipeline.export_supplement(
            paths=wavs, out_path=out_path, format=sup_format, bitrate=sup_bitrate,
            title=title, artist=artist,
        )
        return _safe_path_for_file_component(final), f"✅ 导出完成：{os.path.basename(final)}"
    except Exception as e:
        return None, str(e)


def play_supplement_preview(which, sup_wavs, ss):
    """P1 试听：which='all' 拼接整段试听；which='seg' 试听首段（逐句入口）。

    Returns:
        音频文件路径（gr.Audio type=filepath）或 None。
    """
    wavs = [w for w in (sup_wavs or []) if w and os.path.isfile(w)]
    if not wavs:
        return None
    if which == "all":
        out = os.path.join(config.get_preview_dir(),
                           f"supplement_preview_{int(time.time() * 1000)}.wav")
        try:
            return audio_pipeline.export_supplement(paths=wavs, out_path=out, format="wav")
        except Exception:
            return wavs[0]
    # 逐句：返回第一段
    return wavs[0]


def play_lib_voice(choice):
    fp=_lib_path(choice) if choice else None
    return fp if fp and os.path.isfile(fp) else None

def save_to_lib(recorded, uploaded, name, category, ss):
    """保存到音色库（业务委托 ProjectService.save_to_lib，支持分类前缀）。"""
    try:
        dest = ProjectService.save_to_lib(recorded, uploaded, name, category=category or "")
    except ValueError as e:
        return str(e), gr.update(), gr.update(), gr.update()
    # 刷新所有依赖音色列表的组件（绑定下拉 + 浏览器 + 试听页换音色 + 分类下拉）
    cats = voice_lib.list_categories()
    return (f"已保存至音色库: {os.path.basename(dest)}",
            gr.update(choices=_lib_voices()),
            gr.update(choices=_lib_voices()),
            gr.update(choices=cats + ["— 新建 —"] if cats else ["未分类", "— 新建 —"], value=category or "未分类"))


def filter_vlib_by_category(category):
    """按分类筛选音色库 → 返回可选音色列表（供绑定区 v_lib 使用）。"""
    voices = voice_lib.scan_voice_library(category=category or None)
    return gr.update(choices=[v["name"] for v in voices])

def open_segments_folder(ss):
    if not ss.project: return "请先打开项目"
    d = ProjectService.get_project_dir(ss.project)
    sd = os.path.join(d, "segments")
    os.makedirs(sd, exist_ok=True)
    os.startfile(sd)
    return ""

# ═══════════ O4/O5/O9/O13 新增 handler（仅追加，不触碰既有红线接线） ═══════════

# ── O4：书架 + 章节树 ──
def refresh_bookshelf():
    """刷新书架 Dataframe（返回着色契约 dict，列：项目|章|段进度|状态）。"""
    projects = ProjectService.list_projects()
    rows = [[p["name"], p["chapters"], f"{p['done']}/{p['total']}", p["status"]] for p in projects]
    return df_style.style_dataframe(
        rows,
        df_style.BOOKSHELF_HEADERS,
        status_col=3,
        status_color_map=df_style.STATUS_WORD_COLORS,
    )


def select_project_from_bookshelf(evt, rows):
    """点选书架某行 → 回填 p_sel（项目页 Dropdown，唯一项目选择真相源）。"""
    if evt is None or evt.index is None:
        return gr.update()
    try:
        rows = rows["data"] if isinstance(rows, dict) else rows
        name = rows[evt.index[0]][0]
    except Exception:
        return gr.update()
    return gr.update(value=name)


def render_chapter_tree(project):
    """渲染章节折叠树 HTML（O4 右栏）。project 为空返回提示。"""
    if not project:
        return "<i>未打开项目</i>"
    return _pm.build_chapter_tree(project)


def refresh_projects_full():
    """p_refresh 全量刷新：仅刷新 p_sel 选项（书架入口已统一到概览页）。"""
    choices = ProjectService.scan_projects()
    return gr.update(choices=choices)


# ── O5：合成前分段预览 / 勾选 ──
def render_preview(ss):
    """渲染合成前预览 Dataframe + 章节勾选（回填已持久化选择）。

    返回 (预览行, gr.update(章节选项+勾选值))。
    """
    if not ss or not ss.project:
        return [], gr.update(choices=[], value=[])
    snap = _snap(ss)
    script = snap.script
    chapters = script.get("chapters", [])
    chapter_options = [str(ch.get("id")) for ch in chapters]
    chapter_labels = {
        str(ch.get("id")): f"第{ch.get('id')}章 {ch.get('title', '')}"
        for ch in chapters
    }
    rows = synth_progress.build_preview_rows_from_script(snap.script)
    # 回填勾选：读 synthesis_selections.json
    sel = _pm.get_synthesis_selections(ss.project)
    saved = sel.get("chapters")
    if saved is not None:
        chosen = [c for c in saved if c in chapter_options]
    else:
        chosen = list(chapter_options)
    return df_style.style_dataframe(rows, synth_progress.PREVIEW_HEADERS, status_col=None), gr.update(
        choices=[(chapter_labels.get(c, c), c) for c in chapter_options],
        value=chosen,
    )


# ── O9：音色库浏览 / 搜索 ──
def refresh_voice_lib(search, category):
    """刷新音色库浏览器（Dataframe 行 + 分类下拉选项）。"""
    voices = voice_lib.scan_voice_library(search=search or "", category=category)
    rows = []
    for v in voices:
        rows.append([v["name"], v["category"], v["size_kb"], v["path"]])
    cats = voice_lib.list_categories()
    return df_style.style_dataframe(rows, df_style.VOICE_HEADERS, status_col=None), gr.update(choices=cats, value=category)


def select_voice_from_browser(evt, rows):
    """点选音色库某行 → 回填 v_lib（触发既有 v_lib.change 自动试听）+ 喂共享试听器。"""
    if evt is None or evt.index is None:
        return gr.update(), None
    try:
        rows = rows["data"] if isinstance(rows, dict) else rows
        name = rows[evt.index[0]][0]
    except Exception:
        return gr.update(), None
    path = _lib_path(name)
    return gr.update(value=name), (path if path and os.path.isfile(path) else None)


# ── O13：章节级合并试听 ──
def preview_chapter_options(ss):
    """刷新章节合并试听下拉选项。"""
    if not ss or not ss.project:
        return gr.update(choices=[], value=None)
    script = _snap(ss).script
    opts = [
        (f"第{ch.get('id')}章 {ch.get('title', '')}", str(ch.get("id")))
        for ch in script.get("chapters", [])
    ]
    return gr.update(choices=opts, value=opts[0][1] if opts else None)


def preview_chapter(ss, chapter_id):
    """合并试听单章：调 audio_pipeline.concat_for_preview 返回路径。"""
    if not ss or not ss.project or not chapter_id:
        return None
    proj_dir = ProjectService.get_project_dir(ss.project)
    out_path = os.path.join(config.get_preview_dir(), f"chapter_{chapter_id}.wav")
    try:
        return audio_pipeline.concat_for_preview(proj_dir, chapter_id, out_path)
    except Exception:
        return None


# ═══════════ UI ═══════════

# ═══════════ 页面级刷新辅助（打开项目统一链路复用） ═══════════

def refresh_categories():
    """刷新绑定/保存分类下拉（v_bind_category / v_save_category）。"""
    cats = voice_lib.list_categories()
    return (
        gr.update(choices=cats or ["未分类"]),
        gr.update(
            choices=(cats or []) + ["— 新建 —"] if cats else ["未分类", "— 新建 —"],
            value="未分类",
        ),
    )


def refresh_overview(ss):
    """刷新概览页顶栏状态 + 书架。"""
    return refresh_top_status(ss), refresh_bookshelf()


def refresh_p_sel(name):
    """刷新项目下拉选项（确保选中项在 choices 内）。"""
    return gr.update(choices=ProjectService.scan_projects(), value=name)


def _open_chain_rest(event):
    """把打开项目后的 10 步刷新接到 event 的 .then 链上（3 入口复用）。

    顺序与原 22 元组全量刷新契约一致，覆盖：顶栏 / 章节表 / 章节试听
    选项 / 队列列表 / 章节树 / 合成预览 / 音色库 / 分类下拉 / 概览 / 项目下拉。
    """
    e = event
    e = e.then(refresh_top_status, [ss], [top_status])
    e = e.then(preview_chapters, [ss], [e_chapter_table, e_seg_audio, e_seg_sel])
    e = e.then(preview_chapter_options, [ss], [e_chapter_sel])
    e = e.then(refresh_queue_list, [ss], [s_queue_list])
    e = e.then(render_chapter_tree, [p_sel], [p_chapter_tree])
    e = e.then(render_preview, [ss], [s_preview_df, s_chapters_sel])
    e = e.then(refresh_voice_lib, [v_lib_search, v_lib_category], [v_lib_browser, v_lib_category])
    e = e.then(refresh_categories, [], [v_bind_category, v_save_category])
    e = e.then(refresh_overview, [ss], [ov_status, ov_bookshelf])
    e = e.then(refresh_p_sel, [p_sel], [p_sel])
    return e


with gr.Blocks(theme=THEME, title="Audiobook Studio") as app:
    # 每会话独立的真相源（取代全局可变 S，多标签不再互相踩状态）
    ss = gr.State(SessionState())

    gr.HTML(LIGHT_CSS)

    # 顶部状态栏（从 ui/shared 抽离）
    shared_components = create_status_bar()
    top_status = shared_components["top_status"]

    with gr.Row():
        # 侧边栏导航按钮（从 ui/navigation 抽离）
        nav = create_nav_buttons()
        # 解包导航按钮（保持变量名兼容接线代码）
        nav_overview = nav["nav_overview"]
        nav_project = nav["nav_project"]
        nav_voices = nav["nav_voices"]
        nav_synth = nav["nav_synth"]
        nav_review = nav["nav_review"]
        nav_export = nav["nav_export"]
        nav_supplement = nav["nav_supplement"]

        # ═══ 右侧主工作区 ═══
        with gr.Column(scale=1, elem_classes=["main-area"]) as main_col:

            # ───────── 概览 ─────────
            ov_page = create_overview_page()
            grp_overview = ov_page["group"]
            ov_status = ov_page["ov_status"]
            ov_bookshelf = ov_page["ov_bookshelf"]
            ov_open = ov_page["ov_open"]
            ov_synth = ov_page["ov_synth"]
            ov_export = ov_page["ov_export"]

            # ───────── 项目 ─────────
            prj_page = create_project_page()
            grp_project = prj_page["group"]
            p_name = prj_page["p_name"]
            p_script = prj_page["p_script"]
            p_create = prj_page["p_create"]
            p_create_msg = prj_page["p_create_msg"]
            p_sel = prj_page["p_sel"]
            p_refresh = prj_page["p_refresh"]
            p_open = prj_page["p_open"]
            p_del = prj_page["p_del"]
            p_open_msg = prj_page["p_open_msg"]
            p_summary = prj_page["p_summary"]
            p_chapter_tree = prj_page["p_chapter_tree"]
            data_dir_box = prj_page["data_dir_box"]
            data_apply = prj_page["data_apply"]
            data_open = prj_page["data_open"]
            data_dir_msg = prj_page["data_dir_msg"]

            # ───────── 音色资产 ─────────
            vce_page = create_voice_page()
            grp_voices = vce_page["group"]
            v_status = vce_page["v_status"]
            v_table = vce_page["v_table"]
            v_bind_category = vce_page["v_bind_category"]
            v_audio = vce_page["v_audio"]
            v_role = vce_page["v_role"]
            v_lib = vce_page["v_lib"]
            v_current = vce_page["v_current"]
            v_bind = vce_page["v_bind"]
            v_bind_msg = vce_page["v_bind_msg"]
            v_preview_btn = vce_page["v_preview_btn"]
            v_preview_audio = vce_page["v_preview_audio"]
            v_record = vce_page["v_record"]
            v_upload_clone = vce_page["v_upload_clone"]
            v_save_name = vce_page["v_save_name"]
            v_save_category = vce_page["v_save_category"]
            v_save_btn = vce_page["v_save_btn"]
            v_save_msg = vce_page["v_save_msg"]
            v_lib_search = vce_page["v_lib_search"]
            v_lib_category = vce_page["v_lib_category"]
            v_lib_browser = vce_page["v_lib_browser"]

            # ───────── 合成 ─────────
            syn_page = create_synthesis_page()
            grp_synth = syn_page["group"]
            s_preview_df = syn_page["s_preview_df"]
            s_chapters_sel = syn_page["s_chapters_sel"]
            s_log = syn_page["s_log"]
            s_emo = syn_page["s_emo"]
            s_override = syn_page["s_override"]
            s_alpha = syn_page["s_alpha"]
            s_rate = syn_page["s_rate"]
            s_beam = syn_page["s_beam"]
            s_start = syn_page["s_start"]
            s_cancel = syn_page["s_cancel"]
            s_queue_list = syn_page["s_queue_list"]
            s_pause = syn_page["s_pause"]
            s_resume = syn_page["s_resume"]
            s_open_btn = syn_page["s_open_btn"]
            s_open_msg = syn_page["s_open_msg"]

            # ───────── 试听与质检 ─────────
            review_page = create_review_page()
            grp_review = review_page["group"]
            e_chapter_table = review_page["e_chapter_table"]
            e_chapter_sel = review_page["e_chapter_sel"]
            e_chapter_audio = review_page["e_chapter_audio"]
            e_seg_sel = review_page["e_seg_sel"]
            e_emo = review_page["e_emo"]
            e_alpha = review_page["e_alpha"]
            e_rate = review_page["e_rate"]
            e_voice = review_page["e_voice"]
            e_regenerate = review_page["e_regenerate"]
            e_seg_audio = review_page["e_seg_audio"]
            e_regenerate_msg = review_page["e_regenerate_msg"]

            # ───────── 导出 ─────────
            export_page = create_export_page()
            grp_export = export_page["group"]
            e_fmt = export_page["e_fmt"]
            e_br = export_page["e_br"]
            e_save_dir = export_page["e_save_dir"]
            e_go = export_page["e_go"]
            e_out = export_page["e_out"]
            e_path = export_page["e_path"]
            e_subtitle = export_page["e_subtitle"]
            e_subtitle_btn = export_page["e_subtitle_btn"]
            e_subtitle_out = export_page["e_subtitle_out"]
            e_subtitle_msg = export_page["e_subtitle_msg"]

            # ───────── 角色单独补录 / 补合成导出 ─────────
            supplement_page = create_supplement_page()
            grp_supplement = supplement_page["group"]
            sup_role = supplement_page["sup_role"]
            sup_refresh = supplement_page["sup_refresh"]
            sup_text = supplement_page["sup_text"]
            sup_split_punct = supplement_page["sup_split_punct"]
            sup_json = supplement_page["sup_json"]
            sup_json_parse = supplement_page["sup_json_parse"]
            sup_json_role = supplement_page["sup_json_role"]
            sup_json_lines = supplement_page["sup_json_lines"]
            sup_emotion = supplement_page["sup_emotion"]
            sup_emo_alpha = supplement_page["sup_emo_alpha"]
            sup_rate = supplement_page["sup_rate"]
            sup_quality = supplement_page["sup_quality"]
            sup_voice = supplement_page["sup_voice"]
            sup_mode = supplement_page["sup_mode"]
            sup_synth = supplement_page["sup_synth"]
            sup_synth_status = supplement_page["sup_synth_status"]
            sup_wavs = supplement_page["sup_wavs"]
            sup_play_all = supplement_page["sup_play_all"]
            sup_play_seg = supplement_page["sup_play_seg"]
            sup_audio = supplement_page["sup_audio"]
            sup_format = supplement_page["sup_format"]
            sup_bitrate = supplement_page["sup_bitrate"]
            sup_export = supplement_page["sup_export"]
            sup_out = supplement_page["sup_out"]
            sup_path = supplement_page["sup_path"]

    # 填充 _GROUPS（运行时装载，供 navigation._goto 使用）
    _GROUPS[:] = [grp_overview, grp_project, grp_voices, grp_synth, grp_review, grp_export, grp_supplement]

    # ═══════════ 侧边栏导航切换 ═══════════

    # 旧的全量刷新契约（22 元组）已移除（阶段三：open_project 首步 + _open_chain_rest 打开链）

    nav_overview.click(
        lambda: _goto("overview"), None, _GROUPS,
        js="(x) => { document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active')); document.getElementById('nav-overview')?.classList.add('active'); }").then(
        lambda s: (refresh_top_status(s), refresh_bookshelf()), [ss], [ov_status, ov_bookshelf])
    nav_project.click(
        lambda: _goto("project"), None, _GROUPS,
        js="(x) => { document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active')); document.getElementById('nav-project')?.classList.add('active'); }")
    nav_voices.click(
        lambda: _goto("voices"), None, _GROUPS,
        js="(x) => { document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active')); document.getElementById('nav-voices')?.classList.add('active'); }").then(
        lambda: (gr.update(choices=voice_lib.list_categories() or ["未分类"]),
                 gr.update(choices=voice_lib.list_categories() or ["未分类"],
                           value=None),
                 gr.update(choices=(voice_lib.list_categories() or []) + ["— 新建 —"] if voice_lib.list_categories() else ["未分类", "— 新建 —"],
                           value="未分类")),
        [], [v_bind_category, v_lib_category, v_save_category]).then(
        refresh_voice_lib, [v_lib_search, v_lib_category], [v_lib_browser, v_lib_category])
    nav_synth.click(
        lambda: _goto("synth"), None, _GROUPS,
        js="(x) => { document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active')); document.getElementById('nav-synth')?.classList.add('active'); }")
    nav_review.click(
        lambda: _goto("review"), None, _GROUPS,
        js="(x) => { document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active')); document.getElementById('nav-review')?.classList.add('active'); }").then(
        preview_chapters, [ss], [e_chapter_table, e_seg_audio, e_seg_sel]).then(
        preview_chapter_options, [ss], [e_chapter_sel])
    nav_export.click(
        lambda: _goto("export"), None, _GROUPS,
        js="(x) => { document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active')); document.getElementById('nav-export')?.classList.add('active'); }").then(
        preview_chapters, [ss], [e_chapter_table, e_seg_audio, e_seg_sel]).then(
        preview_chapter_options, [ss], [e_chapter_sel])
    nav_supplement.click(
        lambda: _goto("supplement"), None, _GROUPS,
        js="(x) => { document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active')); document.getElementById('nav-supplement')?.classList.add('active'); }").then(
        refresh_supplement_roles, [ss], [sup_role])

    # ── 概览页：书架点选 → 回填 p_sel → open_project 首步 → 打开链刷新 → 切页 ──
    chain = ov_bookshelf.select(
        select_project_from_bookshelf, [ov_bookshelf], [p_sel]
    ).then(open_project, [p_sel, ss], [p_summary, v_table, v_role, v_lib, s_log, v_status])
    _open_chain_rest(chain).then(
        lambda: _goto("project"), None, _GROUPS,
        js="(x) => { document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active')); document.getElementById('nav-project')?.classList.add('active'); }"
    )

    # ── 概览页快捷操作：「打开项目」切页 → open_project 首步 → 打开链刷新 ──
    chain = ov_open.click(
        lambda: _goto("project"), None, _GROUPS,
        js="(x) => { document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active')); document.getElementById('nav-project')?.classList.add('active'); }"    ).then(open_project, [p_sel, ss], [p_summary, v_table, v_role, v_lib, s_log, v_status])
    _open_chain_rest(chain)
    ov_synth.click(
        lambda: _goto("synth"), None, _GROUPS,
        js="(x) => { document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active')); document.getElementById('nav-synth')?.classList.add('active'); }")
    ov_export.click(
        lambda: _goto("export"), None, _GROUPS,
        js="(x) => { document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active')); document.getElementById('nav-export')?.classList.add('active'); }").then(
        preview_chapters, [ss], [e_chapter_table, e_seg_audio, e_seg_sel]).then(
        preview_chapter_options, [ss], [e_chapter_sel])

    # ═══════════ events（业务接线，沿用 v2） ═══════════
    p_refresh.click(refresh_projects_full, [], [p_sel])
    p_create.click(create_project, [p_name, p_script, ss], [p_name, p_script, p_create_msg, p_sel])
    chain = p_open.click(open_project, [p_sel, ss], [p_summary, v_table, v_role, v_lib, s_log, v_status])
    _open_chain_rest(chain)
    p_del.click(delete_project, p_sel, p_sel)
    data_apply.click(apply_data_dir, [data_dir_box], [data_dir_msg, data_dir_box])
    data_open.click(open_data_dir, [], [data_dir_msg])
    v_bind.click(bind_voice, [v_role, v_audio, v_lib, ss], [v_bind_msg, v_table, v_lib, v_role])
    v_lib.change(play_lib_voice, v_lib, v_audio)
    v_lib.change(lambda c: f"*当前参考音频: 音色库/{c}*" if c else "*当前参考音频: 未选择*", v_lib, v_current)
    v_audio.change(lambda f: f"*当前参考音频: {os.path.basename(f) if f else '未选择'}*", v_audio, v_current)
    v_save_btn.click(save_to_lib, [v_record, v_upload_clone, v_save_name, v_save_category, ss], [v_save_msg, v_lib, e_voice, v_save_category])
    v_bind_category.change(filter_vlib_by_category, [v_bind_category], v_lib)
    v_lib_search.change(refresh_voice_lib, [v_lib_search, v_lib_category], [v_lib_browser, v_lib_category])
    v_lib_category.change(refresh_voice_lib, [v_lib_search, v_lib_category], [v_lib_browser, v_lib_category])
    v_lib_browser.select(select_voice_from_browser, [v_lib_browser], [v_lib, v_preview_audio])
    s_start.click(do_synthesis, [ss, s_beam, s_emo, s_override, s_alpha, s_rate, s_chapters_sel], outputs=[s_log, s_queue_list]).then(
        refresh_top_status, [ss], [top_status])
    s_cancel.click(cancel, [ss], outputs=s_log).then(refresh_top_status, [ss], [top_status])
    s_pause.click(pause_synthesis, [ss], [s_queue_list, s_pause, s_resume])
    s_resume.click(resume_synthesis, [ss], [s_queue_list, s_pause, s_resume])
    s_open_btn.click(open_segments_folder, [ss], s_open_msg)
    e_chapter_sel.change(preview_chapter, [ss, e_chapter_sel], [e_chapter_audio])
    e_seg_sel.change(play_segment, [e_seg_sel, ss], e_seg_audio)
    e_regenerate.click(regenerate_segment, [e_seg_sel, e_emo, e_alpha, e_rate, e_voice, ss], [e_seg_audio, e_regenerate_msg])
    e_go.click(do_export, [e_fmt, e_br, e_save_dir, ss], [e_out, e_path])
    e_subtitle_btn.click(do_export_subtitles, [ss, e_subtitle], [e_subtitle_out, e_subtitle_msg])
    v_preview_btn.click(preview_bound_voice, [v_role, ss], v_preview_audio)

    # ── 角色单独补录 / 补合成导出 ──
    sup_refresh.click(refresh_supplement_roles, [ss], [sup_role])
    sup_json_parse.click(do_supplement_parse_json, [sup_json, ss],
                         [sup_role, sup_json_role, sup_json_lines, sup_synth_status])
    sup_synth.click(do_supplement_synth,
                    [sup_role, sup_mode, sup_text, sup_json_role, sup_json_lines,
                     sup_emotion, sup_emo_alpha, sup_rate, sup_quality,
                     sup_split_punct, sup_voice, ss],
                    [sup_wavs, sup_synth_status])
    sup_export.click(do_supplement_export,
                     [sup_format, sup_bitrate, sup_wavs, sup_role, ss],
                     [sup_out, sup_path])
    sup_play_all.click(lambda wavs, ss: play_supplement_preview("all", wavs, ss),
                       [sup_wavs, ss], [sup_audio])
    sup_play_seg.click(lambda wavs, ss: play_supplement_preview("seg", wavs, ss),
                       [sup_wavs, ss], [sup_audio])

if __name__ == "__main__":
    os.chdir(BASE)
    from lib.logging_setup import setup_logging
    setup_logging(log_dir=os.path.join(BASE, "logs"))
    # 数据目录外置后，首次启动把程序目录内的旧克隆音色迁移到外置 voice_library（一次性、安全拷贝）。
    config.migrate_legacy_voice_library()
    # Gradio 默认只允许 serve 当前 cwd 与 tempdir 下的文件。数据目录（音色库、预览、
    # 合成产物、导出）已全部外置到 config.get_data_dir()（如 D:\AudiobookStudio），
    # 不在 cwd 内，返回其下音频路径给 Audio/File 组件会在序列化阶段触发 InvalidPathError
    # 导致前端显示「错误」。将其加入 allowed_paths 白名单，递归放行其下所有子目录。
    app.queue().launch(server_name="0.0.0.0", server_port=7862, share=False, inbrowser=True,
                       allowed_paths=[config.get_data_dir()])
