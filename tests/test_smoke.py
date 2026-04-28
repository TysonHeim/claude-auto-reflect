"""End-to-end smoke test: analyze → detect → propose → list against fixture.

Runs each module as a subprocess with AUTO_REFLECT_DIR + AUTO_REFLECT_SESSIONS_DIR
pointed at a tmpdir, so the test is hermetic and never touches the user's data.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "sample-session.jsonl"


def run(module, *args, env=None):
    """Run `python3 -m auto_reflect.<module> <args>` and return (rc, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, "-m", f"auto_reflect.{module}", *args],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.returncode, result.stdout, result.stderr


def main():
    print("Running end-to-end smoke test...\n")

    if not FIXTURE.exists():
        print(f"  ✗ fixture missing: {FIXTURE}")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        ar_dir = tmp / "auto-reflect"
        sessions_dir = tmp / "projects"
        sessions_dir.mkdir()
        # Copy fixture into the fake sessions dir
        shutil.copy(FIXTURE, sessions_dir / "smoke-session.jsonl")

        env = os.environ.copy()
        env["AUTO_REFLECT_DIR"] = str(ar_dir)
        env["AUTO_REFLECT_SESSIONS_DIR"] = str(sessions_dir)
        env["CLAUDE_DIR"] = str(tmp)
        env["PYTHONPATH"] = f"{ROOT}{os.pathsep}{env.get('PYTHONPATH', '')}"

        # 1. analyze the fixture session
        rc, out, err = run("analyze_session", "--latest", "--json", env=env)
        assert rc == 0, f"analyze_session failed: {err}"
        result = json.loads(out)
        assert "score" in result, f"analyze output missing 'score': {result}"
        assert isinstance(result["score"], (int, float))
        print(f"  ✓ analyze_session → score={result['score']}")

        observations = list((ar_dir / "observations").glob("*.json"))
        assert len(observations) >= 1, "no observation file written"
        print(f"  ✓ observation written: {observations[0].name}")

        # 2. detect_patterns (one observation → no patterns, but must not crash)
        rc, out, err = run("detect_patterns", "--json", env=env)
        assert rc == 0, f"detect_patterns failed: {err}"
        patterns_files = list((ar_dir / "patterns").glob("*.json"))
        assert len(patterns_files) >= 1, "no patterns file written"
        print(f"  ✓ detect_patterns → wrote {patterns_files[0].name}")

        # 3. propose_improvements (must run cleanly even with one observation)
        rc, out, err = run("propose_improvements", env=env)
        assert rc == 0, f"propose_improvements failed: {err}"
        print(f"  ✓ propose_improvements ran cleanly")

        # 4. proposals --list (renders something; may be empty but must not error)
        rc, out, err = run("proposals", "--list", env=env)
        assert rc == 0, f"proposals --list failed: {err}"
        # Either "No pending proposals." or "Pending Proposals (N)"
        assert ("No pending proposals" in out) or ("Pending Proposals" in out), \
            f"proposals --list unexpected output: {out[:200]}"
        print(f"  ✓ proposals --list ran cleanly")

        # 5. proposals --history (empty initially)
        rc, out, err = run("proposals", "--history", env=env)
        assert rc == 0, f"proposals --history failed: {err}"
        print(f"  ✓ proposals --history ran cleanly")

    print("\nSmoke test passed!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
