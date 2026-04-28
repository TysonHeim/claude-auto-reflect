"""Tests for proposal generation (pure functions; no file IO)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auto_reflect.propose_improvements import (
    similarity,
    word_set,
    normalize_text,
    cluster_corrections,
    strip_exit_prefix,
    deduplicate_proposals,
    filter_rejected,
    _dedupe_fingerprint,
    is_rejected,
    generate_correction_proposals,
    generate_pattern_proposals,
)


def test_normalize_and_similarity():
    # punctuation collapsed to space, lowercased; trailing whitespace tolerated
    assert normalize_text("Hello, World!").strip() == "hello world"
    # identical → 1.0
    assert similarity("don't use Bash", "don't use Bash") == 1.0
    # disjoint → 0.0
    assert similarity("apple", "zebra") == 0.0
    # partial overlap is between 0 and 1
    s = similarity("don't use Bash for files", "don't use Bash for git")
    assert 0 < s < 1
    print("  ✓ normalize_text + similarity")


def test_strip_exit_prefix():
    assert strip_exit_prefix("Exit code 1\nfile not found") == "file not found"
    assert strip_exit_prefix("plain error") == "plain error"
    print("  ✓ strip_exit_prefix")


def test_cluster_corrections_groups_similar():
    obs = [
        {"session_id": "a", "score": 80, "corrections": ["don't use Bash for reading files"]},
        {"session_id": "b", "score": 70, "corrections": ["don't use Bash for reading files please"]},
        {"session_id": "c", "score": 75, "corrections": ["totally unrelated correction text here"]},
    ]
    clusters = cluster_corrections(obs)
    # First two should cluster (high overlap), third is a singleton (filtered)
    assert len(clusters) == 1, f"Expected 1 cluster, got {len(clusters)}: {clusters}"
    assert len(clusters[0]["items"]) == 2
    assert clusters[0]["sessions"] == {"a", "b"}
    print("  ✓ cluster_corrections groups similar corrections")


def test_cluster_corrections_drops_singletons():
    obs = [{"session_id": "a", "corrections": ["only seen once"]}]
    assert cluster_corrections(obs) == []
    print("  ✓ cluster_corrections drops singletons")


def test_dedupe_fingerprint_stable():
    p1 = {"type": "investigation", "content": {"target": "Session quality trend",
                                               "issue": "score 77.8"}}
    p2 = {"type": "investigation", "content": {"target": "Session quality trend",
                                               "issue": "score 78.1"}}  # numeric drift
    # Same fingerprint despite issue text drift — proves dedup is stable
    assert _dedupe_fingerprint(p1) == _dedupe_fingerprint(p2)
    print("  ✓ _dedupe_fingerprint stable across numeric drift")


def test_deduplicate_against_existing():
    existing = [
        {"type": "feedback_memory", "content": {"target": "Bash old_string"}}
    ]
    new = [
        {"type": "feedback_memory", "content": {"target": "Bash old_string"}},  # dup
        {"type": "feedback_memory", "content": {"target": "Edit not_found"}},   # new
    ]
    unique = deduplicate_proposals(new, existing)
    assert len(unique) == 1
    assert unique[0]["content"]["target"] == "Edit not_found"
    print("  ✓ deduplicate_proposals filters against existing")


def test_filter_rejected_uses_cache():
    rejected_fp = _dedupe_fingerprint(
        {"type": "feedback_memory", "content": {"target": "Bash old_string"}}
    )
    cache = {rejected_fp: {"rejected_at": "2026-04-01T00:00:00"}}
    new = [
        {"type": "feedback_memory", "content": {"target": "Bash old_string"}},  # rejected
        {"type": "feedback_memory", "content": {"target": "Edit fresh"}},       # ok
    ]
    kept = filter_rejected(new, cache)
    assert len(kept) == 1
    assert kept[0]["content"]["target"] == "Edit fresh"
    print("  ✓ filter_rejected drops cached rejections")


def test_generate_correction_proposals_shape():
    clusters = [
        {
            "representative": "use the project logger, not console.log",
            "items": [
                {"text": "use logger", "session_id": "a"},
                {"text": "use logger", "session_id": "b"},
                {"text": "use logger", "session_id": "c"},
            ],
            "sessions": {"a", "b", "c"},
        }
    ]
    props = generate_correction_proposals(clusters)
    assert len(props) == 1
    p = props[0]
    assert p["type"] == "feedback_memory"
    assert p["status"] == "pending_review"
    assert "body" in p["content"]
    assert "evidence" in p["content"]
    assert p["source"] == "auto-reflect"
    print("  ✓ generate_correction_proposals shape")


def test_generate_pattern_proposals_score_decline():
    patterns = [
        {"type": "score_decline", "recent_avg": 70.0, "earlier_avg": 90.0, "delta": -20.0},
        {"type": "frequent_tool_errors", "tool": "Edit", "error_rate": 0.6},  # ignored here
    ]
    props = generate_pattern_proposals(patterns)
    types = {p["type"] for p in props}
    assert "investigation" in types, f"Expected investigation proposal, got {types}"
    print("  ✓ generate_pattern_proposals: score_decline → investigation")


if __name__ == "__main__":
    print("Running propose_improvements tests...\n")
    test_normalize_and_similarity()
    test_strip_exit_prefix()
    test_cluster_corrections_groups_similar()
    test_cluster_corrections_drops_singletons()
    test_dedupe_fingerprint_stable()
    test_deduplicate_against_existing()
    test_filter_rejected_uses_cache()
    test_generate_correction_proposals_shape()
    test_generate_pattern_proposals_score_decline()
    print("\nAll tests passed!")
