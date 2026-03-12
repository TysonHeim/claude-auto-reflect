#!/usr/bin/env python3
"""
Eval-gated skill evolution: validate proposed changes against trigger evals
before applying them.

This is the safety mechanism that prevents self-improvement from introducing
regressions. Every proposed skill change must pass through this gate.

Usage:
    python3 -m auto_reflect.eval_gate --skill <name> --dry-run
    python3 -m auto_reflect.eval_gate --skill <name> --validate
    python3 -m auto_reflect.eval_gate --all --baseline
    python3 -m auto_reflect.eval_gate --report
"""

import json
import os
import sys
import subprocess
from datetime import datetime
from pathlib import Path

from auto_reflect.config import (
    SKILLS_DIR, SKILLS_REPO_DIR, EVAL_TOOLS_DIR,
    BASELINES_DIR, GATE_LOG, ensure_dirs,
)


ensure_dirs()


def find_skill_path(skill_name):
    """Find the actual skill directory (resolving symlinks)."""
    if not os.path.isdir(SKILLS_DIR):
        return None
    skill_link = os.path.join(SKILLS_DIR, skill_name)
    if os.path.islink(skill_link):
        return os.path.realpath(skill_link)
    elif os.path.isdir(skill_link):
        return skill_link
    if os.path.isdir(SKILLS_REPO_DIR):
        repo_path = os.path.join(SKILLS_REPO_DIR, skill_name)
        if os.path.isdir(repo_path):
            return repo_path
    return None


def find_eval_file(skill_path):
    """Find the trigger-eval.json for a skill."""
    eval_file = os.path.join(skill_path, "evals", "trigger-eval.json")
    if os.path.exists(eval_file):
        return eval_file
    return None


def load_baseline(skill_name):
    """Load the saved baseline for a skill."""
    baseline_file = os.path.join(BASELINES_DIR, f"{skill_name}.json")
    if os.path.exists(baseline_file):
        with open(baseline_file) as f:
            return json.load(f)
    return None


def save_baseline(skill_name, results):
    """Save eval results as the new baseline."""
    baseline_file = os.path.join(BASELINES_DIR, f"{skill_name}.json")
    data = {
        "skill": skill_name,
        "timestamp": datetime.now().isoformat(),
        "results": results,
        "summary": compute_summary(results),
    }
    with open(baseline_file, "w") as f:
        json.dump(data, f, indent=2)
    return baseline_file


def run_eval(skill_name, skill_path, eval_file):
    """Run the eval harness against a skill."""
    run_eval_script = os.path.join(EVAL_TOOLS_DIR, "scripts", "run_eval.py")
    if not os.path.exists(run_eval_script):
        print(f"Eval runner not found at {run_eval_script}", file=sys.stderr)
        print("Set AUTO_REFLECT_EVAL_TOOLS to your eval harness directory.", file=sys.stderr)
        return None

    cmd = [
        "python3", run_eval_script,
        "--eval-set", eval_file,
        "--skill-path", skill_path,
        "--verbose",
    ]

    print(f"Running evals for {skill_name}...", file=sys.stderr)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            cwd=EVAL_TOOLS_DIR,
        )
        return parse_eval_output(result.stdout, result.stderr)
    except subprocess.TimeoutExpired:
        print(f"Eval timed out for {skill_name}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Eval failed: {e}", file=sys.stderr)
        return None


def parse_eval_output(stdout, stderr):
    """Parse eval runner output into structured results."""
    results = []
    lines = (stdout + "\n" + stderr).split("\n")

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            if "query" in data or "result" in data:
                results.append(data)
        except json.JSONDecodeError:
            if "PASS" in line or "FAIL" in line:
                results.append({"raw": line})

    return results if results else None


def compute_summary(results):
    """Compute pass/fail summary from results."""
    if not results:
        return {"total": 0, "passed": 0, "failed": 0, "rate": 0}

    total = len(results)
    passed = sum(1 for r in results if r.get("passed", r.get("result") == "pass"))
    failed = total - passed

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "rate": round(passed / total, 2) if total > 0 else 0,
    }


def compare_results(baseline, current):
    """Compare current eval results against baseline."""
    if not baseline or not current:
        return {"status": "no_baseline", "message": "No baseline to compare against"}

    b_summary = baseline.get("summary", compute_summary(baseline.get("results", [])))
    c_summary = compute_summary(current)

    delta = c_summary["rate"] - b_summary["rate"]

    if delta < -0.1:
        return {
            "status": "regression",
            "message": f"Regression detected: {b_summary['rate']*100:.0f}% -> {c_summary['rate']*100:.0f}% ({delta*100:+.0f}%)",
            "baseline_rate": b_summary["rate"],
            "current_rate": c_summary["rate"],
            "delta": delta,
            "gate": "BLOCKED",
        }
    elif delta > 0.05:
        return {
            "status": "improvement",
            "message": f"Improvement: {b_summary['rate']*100:.0f}% -> {c_summary['rate']*100:.0f}% ({delta*100:+.0f}%)",
            "baseline_rate": b_summary["rate"],
            "current_rate": c_summary["rate"],
            "delta": delta,
            "gate": "PASSED",
        }
    else:
        return {
            "status": "stable",
            "message": f"Stable: {b_summary['rate']*100:.0f}% -> {c_summary['rate']*100:.0f}% ({delta*100:+.0f}%)",
            "baseline_rate": b_summary["rate"],
            "current_rate": c_summary["rate"],
            "delta": delta,
            "gate": "PASSED",
        }


def log_gate_result(skill_name, comparison, proposal=None):
    """Append gate result to the gate log."""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "skill": skill_name,
        "comparison": comparison,
        "proposal": proposal,
    }

    log = []
    if os.path.exists(GATE_LOG):
        try:
            with open(GATE_LOG) as f:
                log = json.load(f)
        except (json.JSONDecodeError, IOError):
            log = []

    log.append(entry)

    with open(GATE_LOG, "w") as f:
        json.dump(log, f, indent=2, default=str)


def get_all_skills_with_evals():
    """Find all skills that have trigger-eval.json files."""
    skills = []
    if not os.path.isdir(SKILLS_DIR):
        return skills
    for entry in os.scandir(SKILLS_DIR):
        skill_path = find_skill_path(entry.name)
        if skill_path:
            eval_file = find_eval_file(skill_path)
            if eval_file:
                skills.append({
                    "name": entry.name,
                    "path": skill_path,
                    "eval_file": eval_file,
                })
    return skills


def report():
    """Generate a health report of all skills with evals."""
    skills = get_all_skills_with_evals()

    if not skills:
        return "No skills with eval files found.\nSet AUTO_REFLECT_SKILLS_DIR if your skills are in a non-default location."

    lines = []
    lines.append(f"## Eval Health Report ({len(skills)} skills with evals)")
    lines.append("")
    lines.append("| Skill | Baseline Rate | Last Run | Status |")
    lines.append("|-------|--------------|----------|--------|")

    for s in sorted(skills, key=lambda x: x["name"]):
        baseline = load_baseline(s["name"])
        if baseline:
            summary = baseline.get("summary", {})
            rate = f"{summary.get('rate', 0)*100:.0f}%"
            ts = baseline.get("timestamp", "unknown")[:10]
            status = "has baseline"
        else:
            rate = "n/a"
            ts = "never"
            status = "needs baseline"
        lines.append(f"| {s['name']} | {rate} | {ts} | {status} |")

    lines.append("")
    lines.append(f"Total skills: {len(skills)}")
    no_baseline = sum(1 for s in skills if not load_baseline(s["name"]))
    lines.append(f"Missing baselines: {no_baseline}")

    return "\n".join(lines)


def main():
    args = sys.argv[1:]

    if "--report" in args:
        print(report())
        return

    if "--all" in args and "--baseline" in args:
        skills = get_all_skills_with_evals()
        print(f"Capturing baselines for {len(skills)} skills...", file=sys.stderr)
        for s in skills:
            results = run_eval(s["name"], s["path"], s["eval_file"])
            if results:
                path = save_baseline(s["name"], results)
                print(f"  {s['name']}: saved to {path}", file=sys.stderr)
            else:
                print(f"  {s['name']}: FAILED to run evals", file=sys.stderr)
        return

    skill_name = None
    for i, a in enumerate(args):
        if a == "--skill" and i + 1 < len(args):
            skill_name = args[i + 1]

    if not skill_name:
        print("Usage: python3 -m auto_reflect.eval_gate --skill <name> [--dry-run|--validate]", file=sys.stderr)
        sys.exit(1)

    skill_path = find_skill_path(skill_name)
    if not skill_path:
        print(f"Skill not found: {skill_name}", file=sys.stderr)
        sys.exit(1)

    eval_file = find_eval_file(skill_path)
    if not eval_file:
        print(f"No trigger-eval.json found for {skill_name}", file=sys.stderr)
        sys.exit(1)

    if "--dry-run" in args:
        baseline = load_baseline(skill_name)
        if baseline:
            summary = baseline["summary"]
            print(f"Baseline for {skill_name}: {summary['rate']*100:.0f}% ({summary['passed']}/{summary['total']})")
            print(f"Captured: {baseline['timestamp']}")
        else:
            print(f"No baseline for {skill_name}. Run with --validate to create one.")
        return

    results = run_eval(skill_name, skill_path, eval_file)
    if not results:
        print("Eval run failed — gate BLOCKED (cannot verify)", file=sys.stderr)
        sys.exit(1)

    baseline = load_baseline(skill_name)
    comparison = compare_results(baseline, results)

    print(f"\n{comparison['message']}")
    print(f"Gate: {comparison['gate']}")

    log_gate_result(skill_name, comparison)

    if not baseline:
        save_baseline(skill_name, results)
        print("(Saved as new baseline)")
    elif comparison.get("status") == "improvement":
        save_baseline(skill_name, results)
        print("(Baseline updated to new level)")

    if comparison["gate"] == "BLOCKED":
        sys.exit(1)


if __name__ == "__main__":
    main()
