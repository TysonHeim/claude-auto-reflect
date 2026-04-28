"""Tests for proposals (list/approve/reject/expire) — pure functions only."""

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auto_reflect.proposals import (
    parse_indices,
    expire_old_proposals,
    format_list,
    format_history,
    _fingerprint,
    _text_overlap,
)


def test_parse_indices_csv():
    assert parse_indices("1,3,5") == {1, 3, 5}
    print("  ✓ parse_indices: csv")


def test_parse_indices_range():
    assert parse_indices("2-4") == {2, 3, 4}
    print("  ✓ parse_indices: range")


def test_parse_indices_mixed():
    assert parse_indices("1,3-5,7") == {1, 3, 4, 5, 7}
    print("  ✓ parse_indices: mixed")


def test_expire_old_proposals():
    now = datetime.now()
    fresh = (now - timedelta(days=2)).isoformat()
    stale = (now - timedelta(days=10)).isoformat()
    pending = [
        {"id": "fresh", "created": fresh, "content": {}},
        {"id": "stale", "created": stale, "content": {}},
        {"id": "no_date", "content": {}},  # missing created → skipped, not expired
    ]
    expired = expire_old_proposals(pending)
    expired_ids = {p["id"] for p in expired}
    assert expired_ids == {"stale"}, f"Expected only 'stale', got {expired_ids}"
    print("  ✓ expire_old_proposals: only stale flagged")


def test_format_list_empty():
    assert format_list([]) == "No pending proposals."
    print("  ✓ format_list: empty state")


def test_format_list_renders_proposal():
    pending = [{
        "type": "feedback_memory",
        "created": datetime.now().isoformat(),
        "content": {"description": "use the project logger, not console.log",
                    "priority": "high"},
    }]
    out = format_list(pending)
    assert "feedback_memory" in out
    assert "use the project logger" in out
    assert "Pending Proposals (1)" in out
    print("  ✓ format_list: renders proposal")


def test_format_list_json_strips_internals():
    import json
    pending = [{
        "type": "feedback_memory",
        "_summary": "internal",  # leading underscore → stripped
        "content": {"body": "x"},
    }]
    out = format_list(pending, show_json=True)
    parsed = json.loads(out)
    assert "_summary" not in parsed[0]
    assert parsed[0]["type"] == "feedback_memory"
    print("  ✓ format_list: --json strips internals")


def test_format_history_empty():
    assert format_history([]) == "No proposal history yet."
    print("  ✓ format_history: empty state")


def test_fingerprint_stable():
    p1 = {"type": "feedback_memory", "content": {"target": "Bash old_string"}}
    p2 = dict(p1)  # same content
    assert _fingerprint(p1) == _fingerprint(p2)
    p3 = {"type": "feedback_memory", "content": {"target": "Edit different"}}
    assert _fingerprint(p1) != _fingerprint(p3)
    print("  ✓ _fingerprint: stable + distinguishing")


def test_text_overlap():
    # Identical → 1.0 (after stop-word filtering)
    assert _text_overlap("logger console structured", "logger console structured") == 1.0
    # Disjoint → 0.0
    assert _text_overlap("apple banana cherry", "zebra giraffe yak") == 0.0
    # Empty → 0.0 (no fail)
    assert _text_overlap("", "logger") == 0.0
    print("  ✓ _text_overlap: identical/disjoint/empty")


if __name__ == "__main__":
    print("Running proposals tests...\n")
    test_parse_indices_csv()
    test_parse_indices_range()
    test_parse_indices_mixed()
    test_expire_old_proposals()
    test_format_list_empty()
    test_format_list_renders_proposal()
    test_format_list_json_strips_internals()
    test_format_history_empty()
    test_fingerprint_stable()
    test_text_overlap()
    print("\nAll tests passed!")
