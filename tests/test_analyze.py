"""Tests for session analysis."""

import json
import os
import sys
import tempfile

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auto_reflect.analyze_session import (
    parse_session,
    extract_messages,
    extract_tool_calls,
    detect_corrections,
    detect_retries,
    compute_score,
    analyze,
    _extract_text,
)

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample-session.jsonl")


def test_parse_session():
    entries = parse_session(FIXTURE)
    assert len(entries) == 11, f"Expected 11 entries, got {len(entries)}"
    assert entries[0]["type"] == "file-history-snapshot"
    print("  ✓ parse_session")


def test_extract_messages():
    entries = parse_session(FIXTURE)
    messages = extract_messages(entries)
    # Should have user text messages + assistant messages (skip pure tool_result entries)
    user_msgs = [m for m in messages if m["role"] == "user"]
    assistant_msgs = [m for m in messages if m["role"] == "assistant"]
    assert len(user_msgs) >= 2, f"Expected >= 2 user messages, got {len(user_msgs)}"
    assert len(assistant_msgs) >= 2, f"Expected >= 2 assistant messages, got {len(assistant_msgs)}"
    print("  ✓ extract_messages")


def test_extract_tool_calls():
    entries = parse_session(FIXTURE)
    tool_pairs = extract_tool_calls(entries)
    assert len(tool_pairs) == 3, f"Expected 3 tool pairs, got {len(tool_pairs)}"

    # First should be Read (success)
    assert tool_pairs[0]["name"] == "Read"
    assert tool_pairs[0]["is_error"] is False

    # Second should be Edit (error)
    assert tool_pairs[1]["name"] == "Edit"
    assert tool_pairs[1]["is_error"] is True

    # Third should be Edit (success)
    assert tool_pairs[2]["name"] == "Edit"
    assert tool_pairs[2]["is_error"] is False
    print("  ✓ extract_tool_calls")


def test_detect_corrections():
    entries = parse_session(FIXTURE)
    messages = extract_messages(entries)
    corrections = detect_corrections(messages)
    assert len(corrections) >= 1, f"Expected >= 1 correction, got {len(corrections)}"
    assert "don't use" in corrections[0]["text"].lower()
    print("  ✓ detect_corrections")


def test_detect_retries():
    entries = parse_session(FIXTURE)
    tool_pairs = extract_tool_calls(entries)
    retries = detect_retries(tool_pairs)
    assert len(retries) == 1, f"Expected 1 retry, got {len(retries)}"
    assert retries[0]["tool"] == "Edit"
    print("  ✓ detect_retries")


def test_compute_score():
    # Perfect session
    perfect = compute_score({
        "tool_call_count": 50,
        "error_count": 0,
        "correction_count": 0,
        "retry_count": 0,
    })
    assert perfect == 100, f"Perfect session should score 100, got {perfect}"

    # High error rate (30% errors)
    bad = compute_score({
        "tool_call_count": 10,
        "error_count": 3,
        "correction_count": 2,
        "retry_count": 2,
    })
    assert 40 <= bad <= 70, f"Bad session should score 40-70, got {bad}"

    # Zero tool calls shouldn't crash
    empty = compute_score({
        "tool_call_count": 0,
        "error_count": 0,
        "correction_count": 0,
        "retry_count": 0,
    })
    assert empty == 100, f"Empty session should score 100, got {empty}"
    print("  ✓ compute_score")


def test_extract_text():
    assert _extract_text("hello") == "hello"
    assert _extract_text([{"type": "text", "text": "hello"}]) == "hello"
    assert _extract_text([{"type": "tool_result"}]) == ""
    assert _extract_text(None) == ""
    print("  ✓ _extract_text")


def test_full_analyze():
    metrics = analyze(FIXTURE)
    assert metrics["session_id"] == "abcd1234-5678-9abc-def0-123456789abc"
    assert metrics["tool_call_count"] == 3
    assert metrics["error_count"] == 1
    assert metrics["correction_count"] >= 1
    assert metrics["retry_count"] == 1
    assert 30 <= metrics["score"] <= 95
    print("  ✓ full_analyze")


def test_subagent_detection():
    from auto_reflect.analyze_session import is_subagent_file
    assert is_subagent_file("agent-abc123.jsonl") is True
    assert is_subagent_file("/path/subagents/foo.jsonl") is True
    assert is_subagent_file("abc12345.jsonl") is False
    print("  ✓ subagent_detection")


if __name__ == "__main__":
    print("Running analyze_session tests...\n")
    test_parse_session()
    test_extract_messages()
    test_extract_tool_calls()
    test_detect_corrections()
    test_detect_retries()
    test_compute_score()
    test_extract_text()
    test_full_analyze()
    test_subagent_detection()
    print("\nAll tests passed!")
