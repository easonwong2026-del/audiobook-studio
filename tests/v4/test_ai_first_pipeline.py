from __future__ import annotations

import json
from pathlib import Path

import pytest

from domain.v4 import (
    CharacterBibleDocument,
    ProjectManifest,
    ScriptDirectorBatch,
    SourceMetadata,
)
from domain.v4.ai_first import BibleCharacter, BibleEvidence
from domain.v4.models import source_sha256, stable_speaker_id
from repositories.project_v4_repository import ProjectV4Repository
from repositories.v4_analysis_repository import V4AnalysisRepository
from services.book_understanding_service import BookUnderstandingService
from services.source_segmenter import SourceSegmenter
from services.v4_project_analysis_pipeline import V4ProjectAnalysisPipeline
from services.v4_project_creation import V4ProjectCreationService


def _entry(name: str, chapter_id: str, evidence: str, aliases=None):
    return BibleCharacter(
        character_id=f"char_{name}",
        canonical_name=name,
        aliases=list(aliases or []),
        description="故事人物",
        importance="major",
        relationships=[],
        first_appearance_chapter=chapter_id,
        evidence=[BibleEvidence(chapter_id, evidence)],
        confidence=0.97,
        speaker_id=stable_speaker_id(name),
    )


class FakeBookUnderstanding:
    name = "fake-book"
    model = "fake-reasoner"

    def __init__(self):
        self.calls = []

    def read_chapter(self, **kwargs):
        self.calls.append(kwargs)
        chapter_id = kwargs["chapter_id"]
        values = [_entry("林晚", chapter_id, "林晚")]
        if chapter_id == "chapter_0002":
            values.append(_entry("顾川", chapter_id, "顾川"))
        return CharacterBibleDocument(
            source_sha256=kwargs["source_sha256"],
            characters=values,
            schema_version="character-bible-chapter-v1",
        )

    def finalize(self, *, source_sha256, memory):
        return CharacterBibleDocument.from_dict(memory)


class FakeScriptDirector:
    name = "fake-script"
    model = "fake-reasoner"

    def __init__(self):
        self.calls = []

    def analyze_batch(self, **kwargs):
        self.calls.append(kwargs)
        text = kwargs["text"]
        start = kwargs["source_start"]
        end = kwargs["source_end"]
        speaker = None
        for item in kwargs["bible"]["characters"]:
            if item["canonical_name"] in text:
                speaker = item["speaker_id"]
                break
        segment_type = "dialogue" if "说道" in text or "回答" in text else "narration"
        if segment_type == "narration":
            speaker = "narrator"
        return ScriptDirectorBatch.from_dict({
            "schema_version": "ai-script-director-v4",
            "chapter_id": kwargs["chapter_id"],
            "source_start": start,
            "source_end": end,
            "segments": [{
                "source_start": start,
                "source_end": end,
                "segment_type": segment_type,
                "speaker_id": speaker,
                "text": text,
                "confidence": 0.96,
            }],
        })


class FakeReviewer:
    name = "fake-review"
    model = "fake-reasoner"

    def __init__(self):
        self.calls = []

    def review_chapter(self, **_kwargs):
        self.calls.append(_kwargs)
        return {"schema_version": "ai-script-review-v1", "patches": []}


class EmptyBookUnderstanding:
    """AI 返回空人物圣经（0 角色）——用于模拟"假成功"缺陷。"""

    name = "fake-empty-book"
    model = "fake-reasoner"

    def __init__(self):
        self.calls = []

    def read_chapter(self, **kwargs):
        self.calls.append(kwargs)
        return CharacterBibleDocument(
            source_sha256=kwargs["source_sha256"],
            characters=[],
            schema_version="character-bible-chapter-v1",
        )

    def finalize(self, *, source_sha256, memory):
        return CharacterBibleDocument.from_dict(memory)


class EmptyScriptDirector:
    """AI 剧本导演全旁白化（0 对白、0 角色归属）。"""

    name = "fake-empty-script"
    model = "fake-reasoner"

    def __init__(self):
        self.calls = []

    def analyze_batch(self, **kwargs):
        self.calls.append(kwargs)
        start = kwargs["source_start"]
        end = kwargs["source_end"]
        return ScriptDirectorBatch.from_dict({
            "schema_version": "ai-script-director-v4",
            "chapter_id": kwargs["chapter_id"],
            "source_start": start,
            "source_end": end,
            "segments": [{
                "source_start": start,
                "source_end": end,
                "segment_type": "narration",
                "speaker_id": "narrator",
                "text": kwargs["text"],
                "confidence": 0.95,
            }],
        })


class EmptyReviewer:
    name = "fake-empty-review"
    model = "fake-reasoner"

    def __init__(self):
        self.calls = []

    def review_chapter(self, **_kwargs):
        self.calls.append(_kwargs)
        return {"schema_version": "ai-script-review-v1", "patches": []}


def _project(tmp_path: Path, source: str) -> Path:
    segmented = SourceSegmenter().source_only(source)
    now = "2026-08-02T00:00:00+00:00"
    metadata = SourceMetadata(
        original_filename="book.txt",
        source_format="txt",
        encoding="utf-8",
        normalization="none",
        char_count=len(source),
        sha256=source_sha256(source),
        imported_at=now,
    )
    manifest = ProjectManifest(
        project_id="project_ai_first",
        name="ai-first",
        title="ai-first",
        created_at=now,
        updated_at=now,
    )
    return ProjectV4Repository(tmp_path).create(
        directory_name="ai-first",
        manifest=manifest,
        source_text=source,
        source_metadata=metadata,
        script=segmented.script,
        speakers=segmented.speakers,
    )


def test_v4_creation_is_source_only_before_ai(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("第一章\n林晚说道：‘我们走吧。’", encoding="utf-8")
    result = V4ProjectCreationService(ProjectV4Repository(tmp_path / "projects")).create_from_source(
        source, "book", auto_analyze=False
    )
    speakers = json.loads(
        (result.project_path / "script/speakers.json").read_text(encoding="utf-8")
    )
    script = json.loads(
        (result.project_path / "script/script.json").read_text(encoding="utf-8")
    )
    assert [item["name"] for item in speakers["speakers"]] == ["旁白"]
    assert all(item["speaker_id"] is None for item in script["chapters"][0]["segments"])
    assert all(item["speaker_source"] == "unresolved" for item in script["chapters"][0]["segments"])


def test_ai_first_pipeline_reads_source_builds_script_and_resumes(tmp_path):
    source = "第一章\n林晚说道：‘我们走吧。’\n第二章\n顾川回答：‘我不同意。’"
    project = _project(tmp_path, source)
    book = FakeBookUnderstanding()
    director = FakeScriptDirector()
    reviewer = FakeReviewer()
    pipeline = V4ProjectAnalysisPipeline(
        project,
        book_understanding_adapter=book,
        script_director_adapter=director,
        script_review_adapter=reviewer,
    )
    result = pipeline.run()
    assert result.status == "completed"
    assert [item.display_name for item in result.speakers.speakers] == [
        "旁白", "林晚", "顾川"
    ]
    assert result.summary["analysis_mode"] == "ai-first"
    assert result.summary["identified_characters"] == 2
    assert all(
        item.speaker_source in {"ai", "unresolved"}
        for chapter in result.script.chapters
        for item in chapter.segments
    )
    result.script.validate(source)
    assert (project / "runtime/ai_first/book_understanding.json").is_file()
    assert (project / "runtime/ai_first/script_director.json").is_file()
    assert (project / "runtime/ai_first/script_review.json").is_file()
    calls_before = (len(book.calls), len(director.calls))

    resumed = V4ProjectAnalysisPipeline(
        project,
        book_understanding_adapter=book,
        script_director_adapter=director,
        script_review_adapter=reviewer,
    ).run()
    assert resumed.status == "completed"
    assert (len(book.calls), len(director.calls)) == calls_before
    assert len(reviewer.calls) == 2

    V4ProjectAnalysisPipeline(
        project,
        book_understanding_adapter=book,
        script_director_adapter=director,
        script_review_adapter=reviewer,
    ).run(force_reanalysis=True)
    assert len(book.calls) == calls_before[0] + 2
    assert len(director.calls) == calls_before[1] + 2
    assert len(reviewer.calls) == 4
    assert list((project / "revisions").glob("ai-analysis-*/script.json"))


# ── PR #22：AI 分析"假成功"回归（P0-12 ①~⑤）──


def test_empty_ai_result_is_not_completed_and_retries_once(tmp_path):
    """① 明显多对白书稿 + AI 返回空人物/全旁白 → 不 completed、自动重试一次、
    仍空则 needs_attention（含 reason_code 与用户可读 message）。"""
    source = (
        "第一章\n林晚说道：“我们走吧。”\n顾川问道：“去哪？”\n"
        "林晚回答：“回家。”\n顾川喊道：“快跑！”"
    )
    project = _project(tmp_path, source)
    book = EmptyBookUnderstanding()
    director = EmptyScriptDirector()
    reviewer = EmptyReviewer()
    pipeline = V4ProjectAnalysisPipeline(
        project,
        book_understanding_adapter=book,
        script_director_adapter=director,
        script_review_adapter=reviewer,
    )
    result = pipeline.run()
    assert result.status != "completed"
    assert result.status == "needs_attention"
    state = V4AnalysisRepository(project).load(result.script.source_sha256)
    assert state["status"] == "needs_attention"
    assert state["current_stage"] == "needs_attention"
    reason_codes = state["validity"]["reason_codes"]
    assert "empty_result_suspected" in reason_codes
    assert "dialogue_signal_no_dialogue" in reason_codes
    assert "dialogue_signal_no_characters" in reason_codes
    assert state["stats"]["retries"] == 1
    assert len(state["attempts"]) >= 2
    # 模型调用恰 2 次（1 次原分析 + 1 次重试），单章书稿下每阶段每次 1 次
    assert len(book.calls) == 2
    assert len(director.calls) == 2
    assert len(reviewer.calls) == 2
    assert "请重试或检查 Provider" in result.message


def test_narration_only_book_does_not_report_100_percent_coverage(tmp_path):
    """② 人物圣经空 + 剧本全旁白（原文无对白信号）→ dialogue_coverage 为
    None，不显示 100%，消息显示"未识别到对白"。"""
    source = "第一章\n清晨的阳光洒满大地。\n第二章\n他独自走在田间小路上。"
    project = _project(tmp_path, source)
    book = EmptyBookUnderstanding()
    director = EmptyScriptDirector()
    reviewer = EmptyReviewer()
    pipeline = V4ProjectAnalysisPipeline(
        project,
        book_understanding_adapter=book,
        script_director_adapter=director,
        script_review_adapter=reviewer,
    )
    result = pipeline.run()
    assert result.status == "completed"
    assert result.summary["dialogue_coverage"] is None
    assert "100%" not in result.message
    assert "未识别到对白" in result.message
    state = V4AnalysisRepository(project).load(result.script.source_sha256)
    assert state["summary"]["dialogue_coverage"] is None


def test_true_pure_narration_completes_without_fake_characters(tmp_path):
    """③ 真纯旁白短文 → 正常完成、无虚假人物、不因人物数 0 无条件失败。"""
    source = "第一章\n晨雾散去，他推开门。\n第二章\n远处传来钟声。"
    project = _project(tmp_path, source)
    book = EmptyBookUnderstanding()
    director = EmptyScriptDirector()
    reviewer = EmptyReviewer()
    pipeline = V4ProjectAnalysisPipeline(
        project,
        book_understanding_adapter=book,
        script_director_adapter=director,
        script_review_adapter=reviewer,
    )
    result = pipeline.run()
    assert result.status == "completed"
    assert [item.display_name for item in result.speakers.speakers] == ["旁白"]
    assert result.summary["identified_characters"] == 0
    assert result.summary["character_bible_count"] == 0
    assert result.summary["analysis_mode"] == "ai-first"


def test_suspicious_completed_cache_is_invalidated_and_reanalyzed(tmp_path):
    """④ 可疑空结果已缓存 completed → 再次分析不直接复用，旧缓存失效重执行，
    且保留人工快照（不删除用户数据）。"""
    source = "第一章\n林晚说道：“我们走吧。”\n顾川问道：“去哪？”"
    project = _project(tmp_path, source)
    sha = source_sha256(source)
    repo = V4AnalysisRepository(project)
    repo.save({
        "schema_version": "v4-analysis-state-v1",
        "source_sha256": sha,
        "status": "completed",
        "current_stage": "completed",
        "analysis_mode": "ai-first",
        "provider": "fake-empty-book",
        "stages": {},
        "summary": {"identified_characters": 0, "dialogue_total": 0},
        "errors": [],
        "message": "✅ 分析已完成",
    })
    (project / "runtime/character_bible.json").write_text(
        json.dumps({
            "schema_version": "character-bible-final-v1",
            "source_sha256": sha,
            "characters": [],
            "uncertain_entities": [],
            "revision": 1,
        }),
        encoding="utf-8",
    )
    book = EmptyBookUnderstanding()
    director = EmptyScriptDirector()
    reviewer = EmptyReviewer()
    result = V4ProjectAnalysisPipeline(
        project,
        book_understanding_adapter=book,
        script_director_adapter=director,
        script_review_adapter=reviewer,
    ).run()
    assert result.status != "completed"
    assert book.calls, "缓存失效后应重新请求模型"
    state = V4AnalysisRepository(project).load(sha)
    assert "cache_invalidated" in state["validity"]["reason_codes"]
    assert state["message"] != "✅ 分析已完成"
    # 快照保留（不删除人工锁定/指派/声音绑定）
    assert list((project / "revisions").glob("ai-analysis-*/script.json"))


def test_valid_ai_result_keeps_ai_first_and_resume_is_not_broken(tmp_path):
    """⑤ 有效 AI 结果 → 保持 AI-first、正常写入 stats/validity、断点续传不破坏。"""
    source = "第一章\n林晚说道：‘我们走吧。’\n第二章\n顾川回答：‘我不同意。’"
    project = _project(tmp_path, source)
    book = FakeBookUnderstanding()
    director = FakeScriptDirector()
    reviewer = FakeReviewer()
    pipeline = V4ProjectAnalysisPipeline(
        project,
        book_understanding_adapter=book,
        script_director_adapter=director,
        script_review_adapter=reviewer,
    )
    result = pipeline.run()
    assert result.status == "completed"
    assert result.summary["analysis_mode"] == "ai-first"
    state = V4AnalysisRepository(project).load(result.script.source_sha256)
    assert state["stats"]["ai_requests"] > 0
    assert state["stats"]["shards_total"] > 0
    assert state["validity"]["checked"] is True
    assert state["validity"]["is_suspicious"] is False
    assert state["validity"]["reason_codes"] == []
    assert state["model"] == "fake-reasoner"
    calls = (len(book.calls), len(director.calls), len(reviewer.calls))
    resumed = V4ProjectAnalysisPipeline(
        project,
        book_understanding_adapter=book,
        script_director_adapter=director,
        script_review_adapter=reviewer,
    ).run()
    assert resumed.status == "completed"
    assert (len(book.calls), len(director.calls), len(reviewer.calls)) == calls


# ── PR #22 实测反馈 R-4：证据校验容错 + 失败章节继续处理 ──


def _bible_with_evidence(source: str, evidence: BibleEvidence) -> CharacterBibleDocument:
    char = BibleCharacter(
        character_id="char_林晚",
        canonical_name="林晚",
        aliases=[],
        description="故事人物",
        importance="major",
        relationships=[],
        first_appearance_chapter=evidence.chapter_id,
        evidence=[evidence],
        confidence=0.97,
        speaker_id=stable_speaker_id("林晚"),
    )
    return CharacterBibleDocument(
        source_sha256=source_sha256(source), characters=[char]
    )


def test_evidence_shifted_coordinates_are_relocated_not_fatal():
    """证据文本与原文坐标不一致但文本可查 → 校验通过并修正坐标（不再判死整章）。"""
    source = "第一章\n林晚说道：‘我们走吧。’"
    script = SourceSegmenter().source_only(source).script
    chapter = script.chapters[0]
    real_start = source.index("林晚")
    wrong = BibleEvidence(
        chapter.chapter_id, "林晚", real_start + 2, real_start + 4
    )
    bible = _bible_with_evidence(source, wrong)
    corrected = BookUnderstandingService._validate_evidence(
        bible, source, script.chapters
    )
    evidence = corrected.characters[0].evidence[0]
    assert evidence.source_start == real_start
    assert evidence.source_end == real_start + len("林晚")
    assert source[evidence.source_start:evidence.source_end] == "林晚"


def test_evidence_whitespace_normalized_coordinates_are_relocated():
    """空白规范化后证据文本可查 → 修正坐标（容忍 AI 对空白/标点的偏差）。"""
    source = "第一章\n林 晚 说 道：‘我们走吧。’"
    script = SourceSegmenter().source_only(source).script
    chapter = script.chapters[0]
    evidence_text = "林晚说道"
    wrong = BibleEvidence(chapter.chapter_id, evidence_text, 0, 4)
    bible = _bible_with_evidence(source, wrong)
    corrected = BookUnderstandingService._validate_evidence(
        bible, source, script.chapters
    )
    evidence = corrected.characters[0].evidence[0]
    assert evidence.source_start != 0 or evidence.source_end != 4
    assert (
        source[evidence.source_start:evidence.source_end].replace(" ", "")
        == evidence_text
    )


def test_evidence_missing_from_source_still_raises():
    """证据文本在原文中不存在 → 仍抛错（真实性保护不被放宽）。"""
    source = "第一章\n林晚说道：‘我们走吧。’"
    script = SourceSegmenter().source_only(source).script
    chapter = script.chapters[0]
    wrong = BibleEvidence(chapter.chapter_id, "不存在的人物", 0, 4)
    bible = _bible_with_evidence(source, wrong)
    with pytest.raises(ValueError, match="人物证据"):
        BookUnderstandingService._validate_evidence(bible, source, script.chapters)


def test_evidence_without_coordinates_missing_from_source_raises():
    """无坐标证据文本不在原文中 → 抛错（真实性保护）。"""
    source = "第一章\n林晚说道：‘我们走吧。’"
    script = SourceSegmenter().source_only(source).script
    chapter = script.chapters[0]
    wrong = BibleEvidence(chapter.chapter_id, "不存在的人物")
    bible = _bible_with_evidence(source, wrong)
    with pytest.raises(ValueError, match="不在原文中"):
        BookUnderstandingService._validate_evidence(bible, source, script.chapters)


class PartialFailBookUnderstanding:
    """全书阅读第 1 章失败（模拟请求/校验异常），第 2 章正常返回。"""

    name = "fake-partial-book"
    model = "fake-reasoner"

    def __init__(self):
        self.calls = []

    def read_chapter(self, **kwargs):
        self.calls.append(kwargs)
        chapter_id = kwargs["chapter_id"]
        if chapter_id == "chapter_0001":
            raise ValueError("人物证据文本与原文不一致")
        values = [_entry("顾川", chapter_id, "顾川")]
        return CharacterBibleDocument(
            source_sha256=kwargs["source_sha256"],
            characters=values,
            schema_version="character-bible-chapter-v1",
        )

    def finalize(self, *, source_sha256, memory):
        return CharacterBibleDocument.from_dict(memory)


def test_pipeline_continues_after_one_failed_chapter(tmp_path):
    """多章书稿一章失败不中断整本：失败章节记录 failed，其余章节继续处理。"""
    source = "第一章\n林晚说道：‘我们走吧。’\n第二章\n顾川回答：‘我不同意。’"
    project = _project(tmp_path, source)
    book = PartialFailBookUnderstanding()
    director = FakeScriptDirector()
    reviewer = FakeReviewer()
    result = V4ProjectAnalysisPipeline(
        project,
        book_understanding_adapter=book,
        script_director_adapter=director,
        script_review_adapter=reviewer,
    ).run()
    # continue 语义：第 2 章仍被读取，不再停在失败章节
    assert any(call["chapter_id"] == "chapter_0002" for call in book.calls)
    # 第 2 章角色进入结果 → 不再只剩旁白
    assert result.summary["identified_characters"] >= 1
    assert "顾川" in [item.display_name for item in result.speakers.speakers]
    checkpoint = json.loads(
        (project / "runtime/ai_first/book_understanding.json").read_text(
            encoding="utf-8"
        )
    )
    # 失败章节保留 failed（下次运行重试），成功章节 completed（断点续传跳过）
    assert checkpoint["chapters"]["chapter_0001"]["status"] == "failed"
    assert checkpoint["chapters"]["chapter_0002"]["status"] == "completed"


class ShiftedEvidenceBookUnderstanding:
    """AI 返回的证据坐标与原文不一致但文本可查——校验层应修正坐标而非判死。"""

    name = "fake-shifted-book"
    model = "fake-reasoner"

    def __init__(self):
        self.calls = []

    def read_chapter(self, **kwargs):
        self.calls.append(kwargs)
        chapter_id = kwargs["chapter_id"]
        text = kwargs["text"]
        source_start = kwargs["source_start"]
        name = "林晚" if chapter_id == "chapter_0001" else "顾川"
        idx = text.find(name)
        character = BibleCharacter(
            character_id=f"char_{name}",
            canonical_name=name,
            aliases=[],
            description="故事人物",
            importance="major",
            relationships=[],
            first_appearance_chapter=chapter_id,
            evidence=[
                BibleEvidence(
                    chapter_id, name, source_start + idx + 2, source_start + idx + 4
                )
            ],
            confidence=0.97,
            speaker_id=stable_speaker_id(name),
        )
        return CharacterBibleDocument(
            source_sha256=kwargs["source_sha256"],
            characters=[character],
            schema_version="character-bible-chapter-v1",
        )

    def finalize(self, *, source_sha256, memory):
        return CharacterBibleDocument.from_dict(memory)


def test_pipeline_succeeds_when_ai_returns_shifted_evidence_coordinates(tmp_path):
    """端到端：AI 证据坐标偏移（文本可查）→ 校验层修正 → 全书完成，不再只剩旁白。"""
    source = "第一章\n林晚说道：‘我们走吧。’\n第二章\n顾川回答：‘我不同意。’"
    project = _project(tmp_path, source)
    book = ShiftedEvidenceBookUnderstanding()
    director = FakeScriptDirector()
    reviewer = FakeReviewer()
    result = V4ProjectAnalysisPipeline(
        project,
        book_understanding_adapter=book,
        script_director_adapter=director,
        script_review_adapter=reviewer,
    ).run()
    assert result.status == "completed"
    assert [item.display_name for item in result.speakers.speakers] == [
        "旁白", "林晚", "顾川"
    ]
    assert result.summary["identified_characters"] == 2
