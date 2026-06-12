# Codex token budget for Helpdesk / ES DESK

Short guide for reducing token usage on routine tasks.

## Task modes

### MICRO FIX

For a small change in 1-2 files.

Rules:
- do not read `CODEX_CONTEXT.md` unless the bug clearly depends on project-wide rules;
- do not read all docs;
- work only with the files named in the task or found by direct search in that area;
- keep the final report to 4 blocks: `????????`, `????????`, `??? ????????? ???????`, `Blockers`.

### NARROW AUDIT

For risky code paths without changing code.

Rules:
- search only in the requested zone;
- do not change code;
- do not make a commit;
- do not retell the whole project;
- return only: files/functions, risks, safe slice.

### FEATURE SLICE

For a new feature or risky lifecycle change.

Rules:
- read `CODEX_CONTEXT.md`;
- read only 1-2 relevant docs;
- do not read the whole project without a reason;
- do not mix separate tasks into one diff;
- do not commit without a direct user command.

### COMMIT / SPLIT COMMIT

For mixed working tree changes.

Rules:
- use selective staging;
- do not use `git add .`;
- split unrelated changes;
- show staged diff/stat before commit.

## When not to read documentation

You usually do not need project docs for:
- CSS-only changes;
- one JS file fix;
- a simple text correction;
- commit of an already verified diff;
- obvious bugfix in a well-known file.

If local file context is enough, stay local.

## Short Codex answer format

Use this format by default:
- `????????:`
- `????????:`
- `??? ????????? ???????:`
- `Blockers:`

Do not add a long project recap unless there is a blocker, hidden risk, or the user explicitly asks for details.

## Standard short checks

Python:

```powershell
python -m py_compile <files>
python tools/check_mojibake.py <files>
git diff --check
```

JavaScript:

```powershell
node --check <files>
python tools/check_mojibake.py <files>
git diff --check
```

Staged changes:

```powershell
python tools/check_staged_encoding.py
git diff --check --cached
```

## Practical defaults

- Start with the smallest viable context.
- Expand scope only after finding a real dependency.
- Prefer targeted `rg` search over reading long files end to end.
- Prefer one concise audit over repeated broad audits.
- If the task is docs-only, do not inspect app runtime without reason.
- If the task is commit-only, inspect diff and staging, not the whole architecture.

## Escalation rule

Expand context only when:
- the bug crosses router/service/frontend boundaries;
- there is lifecycle or role risk;
- the user asks for an audit or architecture decision;
- the diff contains mixed changes that need split staging.
