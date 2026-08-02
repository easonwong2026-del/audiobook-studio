from domain.v4 import ProjectManifest, SourceMetadata
from domain.v4.character_extraction import (
    CharacterCandidate,
    CharacterCandidatesDocument,
    CharacterEvidence,
)
from domain.v4.models import source_sha256
from repositories.project_v4_repository import ProjectV4Repository
from services.character_candidate_service import CharacterCandidateReviewService
from services.source_segmenter import SourceSegmenter
from services.speaker_review_service import SpeakerReviewService


def test_review_assigns_and_locks_new_speaker():
    text = "“待确认。”"
    segmented = SourceSegmenter().segment(text)
    segment_id = segmented.script.chapters[0].segments[0].segment_id
    script, speakers = SpeakerReviewService.assign(
        segmented.script,
        segmented.speakers,
        segment_ids=[segment_id],
        new_speaker_name="林晚",
        lock_speaker=True,
    )
    segment = script.chapters[0].segments[0]
    assert (segment.status, segment.speaker_source) == ("confirmed", "manual")
    assert speakers.speakers[-1].locked is True
    assert (script.revision, speakers.revision) == (2, 2)


def test_review_merges_speaker_and_preserves_alias():
    text = "张三说：“甲。”李四说：“乙。”"
    segmented = SourceSegmenter().segment(text)
    characters = segmented.speakers.speakers[1:]
    source, target = characters
    script, speakers = SpeakerReviewService.merge_speakers(
        segmented.script,
        segmented.speakers,
        source_speaker_id=source.speaker_id,
        target_speaker_id=target.speaker_id,
    )
    assert source.display_name in speakers.speakers[1].aliases
    assert all(
        item.speaker_id != source.speaker_id
        for chapter in script.chapters
        for item in chapter.segments
    )


def test_review_persistence_creates_pre_edit_snapshot(tmp_path):
    text = "“待确认。”"
    segmented = SourceSegmenter().segment(text)
    metadata = SourceMetadata(
        original_filename="book.txt",
        source_format="txt",
        encoding="utf-8",
        normalization="audiobook-normalization-v1",
        char_count=len(text),
        sha256=source_sha256(text),
        imported_at="now",
    )
    manifest = ProjectManifest(
        project_id="project_review",
        name="review",
        created_at="now",
        updated_at="now",
    )
    repository = ProjectV4Repository(tmp_path)
    project = repository.create(
        "review",
        manifest,
        text,
        metadata,
        segmented.script,
        segmented.speakers,
    )
    segment_id = segmented.script.chapters[0].segments[0].segment_id
    script, speakers = SpeakerReviewService.assign(
        segmented.script,
        segmented.speakers,
        segment_ids=[segment_id],
        speaker_id="narrator",
    )
    repository.save_script_and_speakers(project, text, script, speakers)
    snapshots = list((project / "revisions").glob("routing-*"))
    assert len(snapshots) == 1
    assert '"revision": 1' in (
        snapshots[0] / "script.json"
    ).read_text(encoding="utf-8")
    assert '"revision": 2' in (
        project / "script/script.json"
    ).read_text(encoding="utf-8")


def test_ai_candidate_requires_explicit_human_confirmation_and_can_merge():
    text = "林晚推开门。"
    segmented = SourceSegmenter().segment(text)
    candidate = CharacterCandidate(
        candidate_id="candidate_late",
        display_name="林晚",
        aliases=["小晚"],
        confidence=0.92,
        evidence=[CharacterEvidence("chapter_0001", "林晚推开门。")],
        source="ai",
    )
    candidates = CharacterCandidatesDocument(
        source_sha256=source_sha256(text), candidates=[candidate]
    )
    script, speakers, updated_candidates = CharacterCandidateReviewService.confirm(
        segmented.script,
        segmented.speakers,
        candidates,
        candidate_id="candidate_late",
    )
    assert [item.display_name for item in speakers.speakers] == ["旁白", "林晚"]
    assert updated_candidates.candidates[0].status == "confirmed"
    assert script.revision == 2

def test_ai_candidate_can_be_rejected_without_creating_speaker():
    text = "“待确认。”"
    segmented = SourceSegmenter().segment(text)
    candidate = CharacterCandidate(
        candidate_id="candidate_noise",
        display_name="系统提示",
        aliases=[],
        confidence=0.88,
        evidence=[CharacterEvidence("chapter_0001", "“待确认。”")],
        source="ai",
    )
    candidates = CharacterCandidatesDocument(
        source_sha256=source_sha256(text), candidates=[candidate]
    )
    updated = CharacterCandidateReviewService.reject(
        candidates, candidate_id="candidate_noise"
    )
    assert updated.candidates[0].status == "rejected"
    assert len(segmented.speakers.speakers) == 1
