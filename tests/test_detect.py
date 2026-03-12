"""Tests for pattern detection."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auto_reflect.detect_patterns import (
    detect_error_patterns,
    detect_correction_patterns,
    detect_retry_patterns,
    detect_score_trends,
)


def make_observations(n, **overrides):
    """Generate n synthetic observations."""
    obs = []
    for i in range(n):
        o = {
            "score": 90,
            "tool_distribution": {"Edit": 10, "Read": 5},
            "error_distribution": {},
            "corrections": [],
            "retries": [],
            "skills_used": [],
            "start_time": f"2026-03-{i+1:02d}T10:00:00Z",
        }
        o.update(overrides)
        obs.append(o)
    return obs


def test_error_patterns_need_minimum_data():
    # Too few sessions → no patterns
    obs = make_observations(3, error_distribution={"Edit": 5})
    patterns = detect_error_patterns(obs)
    assert len(patterns) == 0, "Should not detect patterns with < 5 sessions"
    print("  ✓ error_patterns minimum threshold")


def test_error_patterns_detected():
    # 10 sessions, Edit errors in 8 of them → should detect
    obs = []
    for i in range(10):
        o = {
            "tool_distribution": {"Edit": 10},
            "error_distribution": {"Edit": 3} if i < 8 else {},
        }
        obs.append(o)
    patterns = detect_error_patterns(obs)
    edit_patterns = [p for p in patterns if p["tool"] == "Edit"]
    assert len(edit_patterns) == 1, f"Expected 1 Edit pattern, got {len(edit_patterns)}"
    assert edit_patterns[0]["error_rate"] == 0.8
    print("  ✓ error_patterns detected")


def test_score_trends_need_minimum():
    obs = make_observations(10)
    patterns = detect_score_trends(obs)
    assert len(patterns) == 0, "Should need 20+ observations for trends"
    print("  ✓ score_trends minimum threshold")


def test_score_decline_detected():
    # 25 observations: first 20 score 95, last 5 score 70
    obs = make_observations(20, score=95) + make_observations(5, score=70)
    # Fix timestamps so sorting works
    for i, o in enumerate(obs):
        o["start_time"] = f"2026-03-{i+1:02d}T10:00:00Z" if i < 28 else f"2026-04-{i-27:02d}T10:00:00Z"
    patterns = detect_score_trends(obs)
    decline = [p for p in patterns if p["type"] == "score_decline"]
    assert len(decline) == 1, f"Expected score decline, got {len(decline)}"
    print("  ✓ score_decline detected")


def test_corrections_need_multiple_sessions():
    obs = make_observations(5)
    obs[0]["corrections"] = ["no don't do that"]
    patterns = detect_correction_patterns(obs)
    assert len(patterns) == 0, "Should need corrections in 2+ sessions"
    print("  ✓ corrections minimum threshold")


def test_retry_minimum_threshold():
    obs = make_observations(5)
    for o in obs:
        o["retries"] = [{"tool": "Bash"}]
    patterns = detect_retry_patterns(obs)
    # 5 retries < minimum of 10
    assert len(patterns) == 0, "Should need 10+ retries"
    print("  ✓ retry minimum threshold")


if __name__ == "__main__":
    print("Running detect_patterns tests...\n")
    test_error_patterns_need_minimum_data()
    test_error_patterns_detected()
    test_score_trends_need_minimum()
    test_score_decline_detected()
    test_corrections_need_multiple_sessions()
    test_retry_minimum_threshold()
    print("\nAll tests passed!")
