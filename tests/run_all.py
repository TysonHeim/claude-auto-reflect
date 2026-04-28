#!/usr/bin/env python3
"""Discover and run every tests/test_*.py as a script. Stdlib only.

Usage:
    python3 tests/run_all.py
    python3 tests/run_all.py -v         # show stdout from each test
"""

import os
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
ROOT_DIR = TESTS_DIR.parent

GREEN = "\033[0;32m"
RED = "\033[0;31m"
YELLOW = "\033[1;33m"
NC = "\033[0m"


def main():
    verbose = "-v" in sys.argv or "--verbose" in sys.argv

    test_files = sorted(TESTS_DIR.glob("test_*.py"))
    if not test_files:
        print(f"{RED}No test_*.py files found in {TESTS_DIR}{NC}")
        return 1

    env = os.environ.copy()
    env["PYTHONPATH"] = f"{ROOT_DIR}{os.pathsep}{env.get('PYTHONPATH', '')}"

    passed, failed = [], []
    for test_file in test_files:
        rel = test_file.relative_to(ROOT_DIR)
        print(f"{YELLOW}▸ {rel}{NC}")
        result = subprocess.run(
            [sys.executable, str(test_file)],
            cwd=str(ROOT_DIR),
            env=env,
            capture_output=not verbose,
            text=True,
        )
        if result.returncode == 0:
            print(f"  {GREEN}PASS{NC}")
            passed.append(rel)
        else:
            print(f"  {RED}FAIL{NC}")
            if not verbose:
                if result.stdout:
                    print(result.stdout)
                if result.stderr:
                    print(result.stderr, file=sys.stderr)
            failed.append(rel)

    print()
    print(f"{GREEN}{len(passed)} passed{NC}, {RED}{len(failed)} failed{NC}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
