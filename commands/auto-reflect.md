---
description: "Analyze session performance, detect patterns across sessions, and propose self-improvements. Use after completing work to build the self-improvement feedback loop."
---

# Auto-Reflect: Self-Improvement Loop

You are running the auto-reflect loop — a self-improvement system that analyzes session performance, detects patterns across sessions, and proposes concrete improvements validated by evals.

## Quick Status Check

First, check the current state of the system:

```bash
python3 -m auto_reflect.orchestrate --status
```

## Full Analysis Loop

Run the orchestrator for the full pipeline (analyze → detect → propose):

```bash
python3 -m auto_reflect.orchestrate --latest
```

Or for batch analysis of recent sessions:

```bash
python3 -m auto_reflect.orchestrate --batch 10
```

## Contextual Self-Assessment

Beyond the automated metrics, reflect on the current session and assess:

1. **Goal achievement** — Did the user get what they asked for? Were there detours?
2. **Approach quality** — Was the first approach correct, or were there false starts?
3. **Tool efficiency** — Were the right tools used? Any unnecessary tool calls?
4. **Skill awareness** — Were relevant skills invoked when they should have been?
5. **Communication** — Was output concise? Did the user have to repeat themselves?

Write a brief (3-5 sentence) qualitative assessment.

## Managing Proposals

List pending proposals:
```bash
python3 -m auto_reflect.proposals --list
```

Present each proposal to the user with a numbered list. After the user indicates which to approve:

```bash
# Approve specific proposals
python3 -m auto_reflect.proposals --approve 1,3,5

# Reject specific proposals
python3 -m auto_reflect.proposals --reject 2,4

# Reject all remaining after a review batch
python3 -m auto_reflect.proposals --reject-all
```

**Proposals expire after 7 days** — any not reviewed in time are auto-rejected by the cron job.

## Executing Approved Proposals

After the user approves proposals, execute them:

### Feedback Memories
- Draft a concrete feedback memory with the pattern, **Why**, and **How to apply**
- Check existing memories for duplicates
- Save the memory file and update your memory index

### Skill Patches
- Identify the specific skill file that needs updating
- Draft the exact change (section, current content, proposed content)
- **Run eval gate before applying**: `python3 -m auto_reflect.eval_gate --skill <name> --validate`
- Only apply if gate passes (no regression >10%)

### Investigations
- List the specific sessions contributing to the issue
- Identify common factors
- Propose a focused investigation plan

## Summary Format

Present a concise summary:

```
Session Score: XX/100
Observations: X total (X new)
Avg Score (last 10): XX/100
Patterns: X detected
Proposals: X pending review (X expiring soon)
Eval Gate: X skills baselined
```

## Important Rules

- NEVER auto-apply improvements without user approval
- Always save the observation (analyzer does this automatically)
- Be honest about the score — don't inflate it
- Focus on high-impact, recurring issues — not one-off problems
- Run eval gate on any skill change before applying
- Proposals not reviewed within 7 days are auto-rejected
