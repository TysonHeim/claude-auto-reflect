---
description: "Analyze session performance, detect patterns across sessions, and propose self-improvements. Use after completing work to build the self-improvement feedback loop."
---

# Auto-Reflect: Self-Improvement Loop

A simple loop: analyze session → detect cross-session patterns → propose improvements → user approves.

## Run the loop

Analyze the latest session:
```bash
python3 -m auto_reflect.analyze_session --latest
```

Detect patterns across all observations (needs ~10+ sessions for signal):
```bash
python3 -m auto_reflect.detect_patterns
```

Generate proposals from observations + patterns:
```bash
python3 -m auto_reflect.propose_improvements
```

The SessionEnd hook runs the first two automatically. You usually only need to run `propose_improvements` and then review.

## Contextual self-assessment

Beyond the automated metrics, reflect on the current session:

1. **Goal achievement** — Did the user get what they asked for?
2. **Approach quality** — Was the first approach correct, or were there false starts?
3. **Tool efficiency** — Were the right tools used?
4. **Skill awareness** — Were relevant skills invoked when they should have been?
5. **Communication** — Was output concise?

Write a brief (3-5 sentence) qualitative assessment.

## Review proposals

```bash
python3 -m auto_reflect.proposals --list
```

Present each proposal to the user as a numbered list. Then:

```bash
python3 -m auto_reflect.proposals --approve 1,3,5
python3 -m auto_reflect.proposals --reject 2,4
python3 -m auto_reflect.proposals --reject-all      # batch cleanup
python3 -m auto_reflect.proposals --expire          # auto-reject >7 days old
```

## Executing approved proposals

### Feedback memories
- Draft a concrete feedback memory with the pattern, **Why**, and **How to apply**
- Check existing memories for duplicates
- Save the memory file and update the memory index

### Skill patches
- Identify the skill file
- Draft the exact change (section, current content, proposed content)
- Apply it

### Investigations
- List the sessions contributing to the issue
- Identify common factors
- Propose a focused investigation plan

## Rules

- NEVER auto-apply improvements without user approval
- Be honest about the score — don't inflate it
- Focus on high-impact, recurring issues — not one-off problems
- Proposals not reviewed within 7 days are auto-rejected (run `--expire`)
