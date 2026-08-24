from lib import project_paths  # noqa: E402
"""工作流测试：合成工作流（假引擎，§10.3 11-20）

在临时目录中模拟合成全流程：
  1. 对项目全部段落「合成」（写假 WAV + 更新 project.json）
  2. 重新合成某一段（覆盖旧 WAV）
  3. （可选）取消中的状态转换

不依赖真实 GPU / IndexTTS / 网络，所有文件操作隔离在 tmp_path 内。
"""
import sys
import os
import json
import struct
import wave
import time

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import lib.segment_cache as segment_cache  # noqa: E402
from repositories.project_repo import ProjectRepository  # noqa: E402


# ── 假音频生成 ────────────────────────────────────────────────────────────────

def _make_fake_wav(path: str, sample_rate: int = 22050, duration: float = 0.3):
    """生成一个指定采样率和时长的静音 WAV 文件。"""
    n_samples = int(sample_rate * duration)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{n_samples}h", *([0] * n_samples)))


SCRIPT = {
    "meta": {"title": "合成测试书"},
    "voices": {"旁白": {"description": "沉稳男中音"}, "小明": {"description": "少年音"}},
    "chapters": [
        {
            "id": 1, "title": "第一章",
            "segments": [
                {"id": "s1", "role": "旁白", "text": "从前有座山。", "emotion": "neutral"},
                {"id": "s2", "role": "小明", "text": "山上有个庙。", "emotion": "neutral"},
            ],
        },
        {
            "id": 2, "title": "第二章",
            "segments": [
                {"id": "s3", "role": "旁白", "text": "庙里有个老和尚。", "emotion": "neutral"},
                {"id": "s4", "role": "小明", "text": "和一个小和尚。", "emotion": "neutral"},
            ],
        },
    ],
}


@pytest.fixture
def synth_project(tmp_path, monkeypatch):
    """创建 2 章 4 段项目，环境变量已重定向到 tmp_path。"""
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(ProjectRepository, "WORKSPACE_ROOT", str(tmp_path / "projects"))
    monkeypatch.setattr(ProjectRepository, "LEGACY_ROOT", str(tmp_path / "legacy"))
    monkeypatch.setattr(ProjectRepository, "_INITIALIZED", True)

    script_path = tmp_path / "script.json"
    with open(script_path, "w", encoding="utf-8") as f:
        json.dump(SCRIPT, f, ensure_ascii=False, indent=2)

    ProjectRepository.create_project("synth_book", str(script_path))

    # 创建一个参考音频并写入 bindings
    proj_dir = ProjectRepository.get_project_dir("synth_book")
    voices_dir = project_paths.project_dir(proj_dir, "voices", create=True)
    ref_wav = os.path.join(voices_dir, "ref_旁白.wav")
    _make_fake_wav(ref_wav)
    ref_wav2 = os.path.join(voices_dir, "ref_小明.wav")
    _make_fake_wav(ref_wav2)

    bp = project_paths.project_file(proj_dir, "voice_bindings")
    with open(bp, encoding="utf-8") as f:
        bd = json.load(f)
    bd["bindings"]["旁白"] = ref_wav
    bd["bindings"]["小明"] = ref_wav2
    with open(bp, "w", encoding="utf-8") as f:
        json.dump(bd, f, ensure_ascii=False, indent=2)

    return "synth_book"


# ── 辅助：模拟合成一段 ────────────────────────────────────────────────────────

def _fake_synthesize(project: str, seg_id: str, duration: float = 0.3):
    """模拟合成一段：写 WAV + 更新 project.json 状态为 done。"""
    proj_dir = ProjectRepository.get_project_dir(project)
    seg_dir = project_paths.project_dir(proj_dir, "segments", create=True)

    # 用参数感知的缓存键名写 WAV（与真实合成路径一致）
    cache_key = segment_cache.segment_cache_key(seg_id, "neutral", 1.0, 1.0, None)
    wav_path = os.path.join(seg_dir, f"{cache_key}.wav")
    _make_fake_wav(wav_path, duration=duration)

    # 更新状态
    ProjectRepository.update_segment_status(project, seg_id, "done")
    return wav_path


# ── 测试用例 ──────────────────────────────────────────────────────────────────


class TestSynthesisLifecycle:
    """合成工作流测试（假引擎）。"""

    def test_synthesize_segments(self, synth_project):
        """全量合成 4 段 → 每段 WAV 存在且状态为 done。"""
        seg_ids = ["s1", "s2", "s3", "s4"]

        for seg_id in seg_ids:
            _fake_synthesize(synth_project, seg_id)

        # ��证 meta
        meta, script, bindings = ProjectRepository.load_project(synth_project)
        assert meta.completed_count == 4
        assert meta.pending_count == 0
        assert meta.failed_count == 0

        for seg_id in seg_ids:
            assert meta.segments_status[seg_id] == "done"

        # 验证 WAV 文件存在
        proj_dir = ProjectRepository.get_project_dir(synth_project)
        seg_dir = project_paths.project_dir(proj_dir, "segments", create=True)
        for seg_id in seg_ids:
            cache_key = segment_cache.segment_cache_key(seg_id, "neutral", 1.0, 1.0, None)
            wav_path = os.path.join(seg_dir, f"{cache_key}.wav")
            assert os.path.isfile(wav_path), f"缺失 WAV: {wav_path}"
            # 非空
            assert os.path.getsize(wav_path) > 44, f"WAV 文件太小（仅文件头）: {wav_path}"

    def test_regenerate_segment(self, synth_project):
        """重新合成某一段 → 新 WAV 覆盖旧 WAV。"""
        # 先全部合成
        for seg_id in ["s1", "s2", "s3", "s4"]:
            _fake_synthesize(synth_project, seg_id, duration=0.3)

        proj_dir = ProjectRepository.get_project_dir(synth_project)
        seg_dir = project_paths.project_dir(proj_dir, "segments", create=True)
        cache_key = segment_cache.segment_cache_key("s1", "neutral", 1.0, 1.0, None)
        wav_path = os.path.join(seg_dir, f"{cache_key}.wav")

        old_size = os.path.getsize(wav_path)

        # 等待至少 1 秒确保 mtime 变化（或直接重新写不同大小的文件）
        time.sleep(0.01)  # 极小延迟即可让新文件有不同的 mtime

        # 重新合成 s1（不同时长，产生不同大小）
        _fake_synthesize(synth_project, "s1", duration=0.5)
        new_size = os.path.getsize(wav_path)

        # 新 WAV 存在且被覆盖
        assert os.path.isfile(wav_path)
        # 因为时长变了（0.3→0.5），文件大小应该不同
        assert new_size != old_size, "重新合成后 WAV 文件大小应变化"

        # 验证 meta 依然正确（全部 done）
        meta, _, _ = ProjectRepository.load_project(synth_project)
        assert meta.completed_count == 4
        assert meta.segments_status["s1"] == "done"

    def test_get_remaining_after_synthesis(self, synth_project):
        """合成后 get_remaining 返回空（全部完成）。"""
        for seg_id in ["s1", "s2", "s3", "s4"]:
            _fake_synthesize(synth_project, seg_id)

        remaining = ProjectRepository.get_remaining(synth_project)
        assert remaining == [], f"全部合成后应有空剩余，实际: {remaining}"
