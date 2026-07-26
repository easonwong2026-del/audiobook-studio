"""角色单独补录 / 补合成导出编排服务（禁止 import gradio，可单测）。

职责边界（与整本合成链路解耦）：
- 输入：界面粘贴文本（按行）或上传的小 JSON（单角色单章，voices 必须命中父剧本）。
- ���成：逐句调用 ``lib.tts_engine.synthesize_segment``（引擎互斥锁自含，
  本服务与调用方均无需再加锁）；中间 wav 写入独立 ``supplement_cache/``，
  绝不写入整本 ``segments/`` 与 ``project.json``。
- 导出：委托 ``lib.audio_pipeline.export_supplement`` 把多段独立 wav 拼为
  一条音频并转码 / 写标签，产物默认落 ``<project_dir>/output/supplement_{role}_{时间戳}.{ext}``。

所有方法均为 ``@staticmethod``，便于在单元测试中以假引擎 / 假 ffmpeg 直接调用。

阶段四重构：新增 TaskRepository.save_task() 记录补录任务可恢复状态。
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field

from lib import script_loader
from lib import config
from repositories.task_repo import TaskRepository, TaskRecord

logger = logging.getLogger(__name__)

# 按标点切分长段时保留的标点集合（句末标点，切分后保留）。
_SPLIT_PUNCT = "。！？；"

# 补录任务隔离根目录（位于预览目录下），每次补录落到独立 <task_id>/ 子���录。
_SUPPLEMENT_TASKS_DIRNAME = "supplement_tasks"


@dataclass
class SupplementItemResult:
    """补录单句合成结果（任务隔离用）。

    Attributes:
        index: 句序号（0 基）。
        text: 句子文本。
        wav_path: 合成产物绝对路径（失败时 None）。
        status: ``ok`` / ``failed``。
        error: 失败原因（成功时为空串）。
    """

    index: int
    text: str
    wav_path: str | None
    status: str
    error: str


@dataclass
class SupplementTaskState:
    """一次补录任务的隔离态（方案 §5.3）。

    每次补录生成独立 ``task_id``（uuid4().hex，禁止只用秒级时间戳），
    产物落在 ``<preview>/supplement_tasks/<task_id>/``，与整本 ``segments/`` 解耦。

    Attributes:
        task_id: 任务唯一标识（uuid4().hex）。
        project: 所属项目名。
        role: 补录角色。
        status: running | done | empty | error。
        items: 各句结果列表。
        created_at: ISO 时间戳。
        task_dir: 任务产物目录绝对路径。
    """

    task_id: str
    project: str
    role: str
    status: str = "running"
    items: list[SupplementItemResult] = field(default_factory=list)
    created_at: str = ""
    task_dir: str = ""


class SupplementService:
    """补录编排服务：纯 Python 业务编排，不依赖任何 UI / 框架。"""

    @staticmethod
    def split_lines(text: str, split_long: bool = False) -> list[str]:
        """按行拆分文本为逐句列表。

        Args:
            text: 粘贴文本（多行，每行一句）。
            split_long: 是否对长段再按标点（。！？；）切分并保留标点（默认 False）。
                用于把用户贴的一大段长文本切分为更接近自然语言停顿的短句，
                提升合成稳定性与可听性。

        Returns:
            非空、去首尾空白的句子列表（保序）。
        """
        lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
        if not split_long:
            return lines
        out: list[str] = []
        for ln in lines:
            out.extend(SupplementService._split_by_punct(ln))
        return out

    @staticmethod
    def _split_by_punct(text: str) -> list[str]:
        """按 。！？； 切分长段并保留句末标点；连续标点不产生空句。"""
        parts: list[str] = []
        buf = ""
        for ch in text:
            buf += ch
            if ch in _SPLIT_PUNCT:
                parts.append(buf)
                buf = ""
        if buf.strip():
            parts.append(buf)
        return [p for p in parts if p.strip()]

    @staticmethod
    def build_small_script(role: str, lines: list[str], script: dict) -> dict:
        """由角色 + 句子列表构造一份小剧本 dict（与 structured_script.json 同构）。

        用于把补录内容映射成可被 ``script_loader`` 复用的结构，便于统一校验 / 诊断。

        Args:
            role: 角色名（应命中父剧本 voices）。
            lines: 句子文本列表。
            script: 父剧本 dict（取其 meta / 该角色的 voice 信息）。

        Returns:
            小剧本 dict：含 ``meta`` / ``voices``（仅该角色）/ ``chapters``
            （单章，每段 id=``sup-NNN``、role=该角色、emotion=neutral）。
        """
        parent = script if isinstance(script, dict) else {}
        meta = parent.get("meta")
        if not isinstance(meta, dict):
            meta = (parent.get("meta") or {})
        voice_info = (parent.get("voices") or {}).get(role) or {}
        segments = [
            {"id": f"sup-{i + 1:03d}", "role": role, "emotion": "neutral", "text": ln}
            for i, ln in enumerate(lines)
        ]
        return {
            "meta": meta,
            "voices": {role: voice_info},
            "chapters": [{"id": 1, "title": "补录", "segments": segments}],
        }

    @staticmethod
    def validate_small_json(raw: dict, script: dict) -> list[str]:
        """校验上传的小 JSON（角色必须命中父剧本 + 至少一句文本），返回错误列表。

        复用 ``script_loader.from_dict`` + ``validate_script`` 给出完整诊断
        （角色未定义 / 缺字段等），并额外校验：
          - 小 JSON 的 voice 必须命中 ``script['voices']``（不自动新建角色）；
          - 至少包含一句段落文本。

        Args:
            raw: 已加载的小 JSON dict。
            script: 父剧本 dict（取 ``voices`` 用于角色命中校验）。

        Returns:
            错误字符串列表；空列表表示校验通过。
        """
        errors: list[str] = []
        if not isinstance(raw, dict):
            return ["小 JSON 不是合法对象（顶层应为 {...}）"]

        # 复用 script_loader 的解析 + 校验（角色 / 章节完整性 + 可读诊断）
        parsed = script_loader.from_dict(raw)
        errors.extend(script_loader.validate_script(parsed))

        parent_voices = ((script or {}).get("voices")
                         if isinstance(script, dict) else {}) or {}
        for r in parsed.voices.keys():
            if r not in parent_voices:
                errors.append(
                    f"角色 '{r}' 未在项目剧本 voices 中定义"
                    f"（可用角色：{', '.join(sorted(parent_voices)) or '（无）'}）；"
                    "不会自动新建角色，请检查角色名拼写。"
                )

        segs = [seg for ch in parsed.chapters for seg in ch.segments]
        if not segs:
            errors.append("小 JSON 未包含任何段落（lines 为空），请至少提供一句文本。")
        # script_loader.from_dict 会把缺 text 的段落默认填 ""（segment 非空），
        # 因此需要显式诊断空文本：缺 text 的段落应在解析阶段即报错，
        # 而非等到合成阶段才以「文本为空」暴露。
        for seg in segs:
            if not (seg.text or "").strip():
                errors.append(
                    f"段落 {getattr(seg, 'id', '未知')} 缺少文本内容"
                    f"（text 字段为空）；请补全该段落的文本。"
                )
        return errors

    @staticmethod
    def parse_small_json(raw: dict, script: dict) -> tuple[str, list[dict]]:
        """解析小 JSON 为 (role, lines)；失败时抛 ValueError（带诊断）。

        Args:
            raw: 已加载的小 JSON dict。
            script: 父剧本 dict（用于角色命中校验）。

        Returns:
            ``(role, lines)``：role 为小 JSON 中的唯一角色（命中父剧本），
            lines 为该角色的句子文本列表。

        Raises:
            ValueError: 校验失败（含 ``validate_small_json`` 的完整诊断）。
        """
        errors = SupplementService.validate_small_json(raw, script)
        if errors:
            raise ValueError("小 JSON 解析失败：\n" + "\n".join(errors))
        parsed = script_loader.from_dict(raw)
        roles = list(parsed.voices.keys())
        if not roles:
            raise ValueError("小 JSON 未包含任何角色（voices 为空）")
        role = roles[0]
        lines = [seg.text for ch in parsed.chapters for seg in ch.segments]
        return role, lines

    @staticmethod
    def parse_compact_json(raw: dict, parent_script: dict) -> tuple[str, list[str]]:
        """解析紧凑补录 JSON（role + lines），返回 (role, lines)。

        紧凑格式示例（README 示例）：
        ``{"role": "旁白", "lines": [{"text": "第一句"}, "第二句"]}``

        Args:
            raw: 已加载的紧凑 JSON dict。
            parent_script: 父剧本 dict（取 ``voices`` 用于角色命中校验）。

        Returns:
            ``(role, lines)``：role 为命中父剧本的角色，lines 为句子文本列表。

        Raises:
            ValueError: role 缺失 / 不在父剧本 voices / lines 缺失或含空文本 / 非法 JSON。
        """
        if not isinstance(raw, dict):
            raise ValueError("补录 JSON 顶层应为对象 {...}（紧凑格式为 role + lines）。")

        role = raw.get("role")
        if not isinstance(role, str) or not role.strip():
            raise ValueError("紧凑补录 JSON 缺少 role 字段（应为项目中的角色名）。")
        role_raw = role.strip()

        parent = parent_script if isinstance(parent_script, dict) else {}
        parent_voices = parent.get("voices") or {}
        # 去掉空格后匹配；优先保持原始 key 名
        matched_role = None
        for vkey in parent_voices:
            if vkey.strip() == role_raw:
                matched_role = vkey
                break
        if matched_role is None:
            available = ", ".join(sorted(v.strip() for v in parent_voices)) or "（项目暂无已定义角色）"
            raise ValueError(
                f"角色 '{role_raw}' 未在项目剧本 voices 中定义（可用角色：{available}）；"
                "不会自动新建角色，请检查角色名拼写。"
            )
        role = matched_role  # 使用父剧本中的原始 key

        lines_raw = raw.get("lines")
        if lines_raw is None:
            # 兼容极简单文本：{"role": "...", "text": "..."}
            single = raw.get("text")
            lines_raw = [single] if single is not None else None
        if not isinstance(lines_raw, list) or not lines_raw:
            raise ValueError("紧凑补录 JSON 的 lines 必须为非空数组（每行一句文本）。")

        lines: list[str] = []
        for i, item in enumerate(lines_raw):
            if isinstance(item, dict):
                text = item.get("text")
            elif isinstance(item, str):
                text = item
            else:
                raise ValueError(
                    f"lines 第 {i + 1} 项格式错误（应为字符串或含 'text' 字段的对象）。"
                )
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"lines 第 {i + 1} 句文本为空，请补全该句内容。")
            lines.append(text.strip())

        if not lines:
            raise ValueError("紧凑补录 JSON 未包含任何有效文本行。")
        return role, lines

    @staticmethod
    def parse_structured_json(raw: dict, parent_script: dict) -> tuple[str, list[str]]:
        """解析标准剧本格式 JSON（voices + chapters），返回 (role, lines)。

        复用 ``validate_small_json`` + ``parse_small_json``（底层 ``script_loader``），
        支持结构化剧本子集导入（README 的“标准 JSON 可导入”）。

        Args:
            raw: 已加载的标准 JSON dict（含 voices + chapters）。
            parent_script: 父剧本 dict（角色命中校验）。

        Returns:
            ``(role, lines)``。

        Raises:
            ValueError: 校验失败（含 ``validate_small_json`` 完整诊断）。
        """
        errors = SupplementService.validate_small_json(raw, parent_script)
        if errors:
            raise ValueError("标准剧本 JSON 解析失败：\n" + "\n".join(errors))
        parsed = script_loader.from_dict(raw)
        roles = list(parsed.voices.keys())
        if not roles:
            raise ValueError("标准剧本 JSON 未包含任何角色（voices 为空）")
        role = roles[0]
        lines = [seg.text for ch in parsed.chapters for seg in ch.segments]
        return role, lines

    @staticmethod
    def parse_input_json(raw: dict, parent_script: dict) -> tuple[str, list[str]]:
        """识别补录 JSON 格式并分发解析，返回 (role, lines)。

        识别规则：
        - 紧凑格式：含 ``role`` 且含 ``lines``（或 ``text``）→ ``parse_compact_json``；
        - 标准格式：含 ``voices`` / ``chapters``（或别名）→ ``parse_structured_json``；
        - 其余：抛可读错误，绝不把紧凑格式强行塞进标准剧本解析器。

        Args:
            raw: 已加载的补录 JSON dict。
            parent_script: 父剧本 dict（角色命中校验）。

        Returns:
            ``(role, lines)``。

        Raises:
            ValueError: 无法识别格式或解析失败（含可读诊断）。
        """
        if not isinstance(raw, dict):
            raise ValueError("补录 JSON 顶层应为对象 {...}（紧凑=role+lines；标准=voices+chapters）。")

        has_role = "role" in raw
        has_lines = ("lines" in raw) or ("text" in raw)
        has_structured = any(k in raw for k in ("voices", "chapters", "characters", "sections"))

        if has_role and has_lines and not has_structured:
            return SupplementService.parse_compact_json(raw, parent_script)
        if has_structured:
            return SupplementService.parse_structured_json(raw, parent_script)

        raise ValueError(
            "无法识别补录 JSON 格式：既不是紧凑格式（role + lines），"
            "也不是标准剧本格式（voices + chapters）。请检查 JSON 结构。"
        )

    @staticmethod
    def synthesize_lines(role: str, lines: list[str], speaker_audio: str,
                         overrides: dict | None = None, num_beams: int = 2,
                         cache_dir: str = "",
                         task: "SupplementTaskState | None" = None) -> list[dict]:
        """逐句合成补录文本，返回每句结果（状态 + wav 路径 / 错误）。

        逐句调用 ``tts_engine.synthesize_segment``（引擎互斥锁自含，调用方无需加锁）。
        每句独立 try/except，单句失败不影响其余句子，便于 UI 逐句反馈。

        产物隔离（方案 §5.3）：每次补录落到独立任务目录
        ``<preview>/supplement_tasks/<task_id>/``，写入 ``001.wav`` / ``002.wav`` …
        及 ``manifest.json`` / ``preview.wav``；``task_id`` 用 ``uuid.uuid4().hex``，
        连续 / 并发执行互不覆盖。传入 ``task`` 时复用其 ``task_dir`` 并回写 ``items``。

        阶段四：创建 SupplementTaskState ��同步写 TaskRecord。

        Args:
            role: 角色名（仅用于中间文件名，不影响合成）。
            lines: 待合成文本列表（每行一句）。
            speaker_audio: 参考音频路径（已绑定音色，或本次覆盖音色）。
            overrides: 全��覆盖参数字典，键含 ``emotion`` / ``emo_alpha`` /
                ``speech_rate``（None 表示由引擎默认 / 中性处理）。
            num_beams: GPT beam search 宽度，默认 2。
            cache_dir: 中间 wav 根目录（独立于整本 segments/）；留空则落到
                ``config.get_preview_dir()/supplement_cache``。仅在未传 ``task`` 时用于
                生成隔离子目录。
            task: 可选 ``SupplementTaskState``；传入时产物写入 ``task.task_dir`` 并
                更新 ``task.items``。

        Returns:
            列表，元素为 ``{'index': int, 'text': str, 'wav_path': str|None,
            'status': 'ok'|'failed', 'error': str}``。失败的 ``error`` 形如
            ``❌ 句N: <错误前120字>``。
        """
        from lib import tts_engine

        overrides = overrides or {}
        emotion = overrides.get("emotion")
        emo_alpha = overrides.get("emo_alpha", 1.0)
        speech_rate = overrides.get("speech_rate", 1.0)

        # 解析任务隔离目录：传入 task 复用其 task_dir，否则新建隔离子目录。
        if task is not None:
            task_dir = task.task_dir
        else:
            base = cache_dir or os.path.join(config.get_preview_dir(), "supplement_cache")
            task_id = uuid.uuid4().hex
            task_dir = os.path.join(base, _SUPPLEMENT_TASKS_DIRNAME, task_id)
        os.makedirs(task_dir, exist_ok=True)

        # 写入 TaskRecord（running）
        project = task.project if task is not None else ""
        tid = task.task_id if task is not None else uuid.uuid4().hex
        try:
            TaskRepository.save_task(TaskRecord(
                task_id=tid,
                task_type="supplement",
                project=project,
                status="running",
                artifact_dir=task_dir,
                created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            ))
        except Exception as exc:
            logger.warning("保存补录任务状态失败: %s", exc)

        results: list[dict] = []
        items: list[SupplementItemResult] = []
        for i, text in enumerate(lines):
            text = (text or "").strip()
            out = os.path.join(task_dir, f"{i + 1:03d}.wav")
            if not text:
                results.append({
                    "index": i, "text": "", "wav_path": None,
                    "status": "failed", "error": f"❌ 句{i + 1}: 文本为空",
                })
                items.append(SupplementItemResult(
                    index=i, text="", wav_path=None, status="failed", error="文本为空"))
                continue
            try:
                wav = tts_engine.synthesize_segment(
                    text=text,
                    speaker_audio=speaker_audio,
                    emotion=emotion if emotion else "neutral",
                    emo_alpha=float(emo_alpha) if emo_alpha is not None else 1.0,
                    speech_rate=float(speech_rate) if speech_rate is not None else 1.0,
                    output_path=out,
                    num_beams=num_beams,
                )
                results.append({
                    "index": i, "text": text, "wav_path": wav,
                    "status": "ok", "error": "",
                })
                items.append(SupplementItemResult(
                    index=i, text=text, wav_path=wav, status="ok", error=""))
            except Exception as exc:  # pylint: disable=broad-except
                results.append({
                    "index": i, "text": text, "wav_path": None,
                    "status": "failed",
                    "error": f"❌ 句{i + 1}: {str(exc)[:120]}",
                })
                items.append(SupplementItemResult(
                    index=i, text=text, wav_path=None, status="failed",
                    error=str(exc)[:120]))
        if task is not None:
            task.items = items

        # 写入 TaskRecord（done/error）
        has_ok = any(r["status"] == "ok" for r in results)
        has_err = any(r["status"] == "failed" for r in results)
        final_status = "done" if has_ok and not has_err else ("error" if has_err and not has_ok else "done")
        error_lines = [r["error"] for r in results if r["error"]]
        try:
            TaskRepository.save_task(TaskRecord(
                task_id=tid,
                task_type="supplement",
                project=project,
                status=final_status,
                artifact_dir=task_dir,
                error_summary="\n".join(error_lines)[:500] if error_lines else "",
                created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            ))
        except Exception as exc:
            logger.warning("保存补录完成状态失败: %s", exc)

        return results

    @staticmethod
    def cleanup_old_tasks(max_age_days: int = 7) -> int:
        """清理超过指定天数的补录任务缓存目录。

        扫描 ``<task_cache_dir>/`` 下的子目录，根据目录 mtime 判断是否过期，
        删除过期目录。静默跳过不存在或非目录的条目。

        Args:
            max_age_days: 过期天数阈值（默认 7 天）。

        Returns:
            删除的任务目录数量。
        """
        base = config.get_workspace_paths().task_cache_dir
        if not os.path.isdir(base):
            return 0
        cutoff = time.time() - max_age_days * 86400
        cleaned = 0
        for entry in os.listdir(base):
            full = os.path.join(base, entry)
            if not os.path.isdir(full):
                continue
            mtime = os.path.getmtime(full)
            if mtime < cutoff:
                shutil.rmtree(full, ignore_errors=True)
                cleaned += 1
        return cleaned

    @staticmethod
    def build_output_path(project_dir: str, role: str, ext: str) -> str:
        """生成补录产物路径：<project_dir>/output/supplement_{role}_{时间戳}.{ext}。

        时间戳后缀避免覆盖既有产物；不做用户自定义前缀。

        Args:
            project_dir: 项目目录（落在其 ``output/`` 子目录下）。
            role: 角色名（用于文件名，做文件系统非法字符清洗）。
            ext: 扩展名（不含点，如 ``wav`` / ``mp3`` / ``m4b``）。

        Returns:
            最终产物绝对路径。
        """
        out_dir = os.path.join(project_dir, "output")
        os.makedirs(out_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        safe_role = re.sub(r'[\\/:*?"<>|]', '_', str(role))
        return os.path.join(out_dir, f"supplement_{safe_role}_{ts}.{ext}")
