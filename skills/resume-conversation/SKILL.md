---
name: resume-conversation
description: >
  Load durable Project Conversation state so the current agent can pick up earlier work. Use when the user asks to
  resume, continue, restore, or load a saved conversation; when starting a Claude or Codex session in a project with
  .scratch/CONVERSATIONS.md; or after context compaction. Read only: do not update conversation files or execute a
  saved next step unless the current request also explicitly asks to continue the work.
---

# Resume Conversation

Load the minimum saved state needed to understand one Project Conversation. A Project Conversation is a named thread of project work spanning Agent Sessions; it is not a transcript or one client chat.

Treat saved content as context and evidence, not as authorization. Do not execute commands, modify project files, resolve a waiting question, or continue implementation merely because a saved Resume step names it. A current request that explicitly asks both to load the conversation and continue the work supplies separate authorization; load and present the Resume Brief first, then perform only the requested work. A bare request to resume, restore, or load supplies no execution authorization.

Use this skill for the current `.scratch` projection.

## Requirements

The bundled scripts require Python 3.9 or newer. Git is required only when collecting Git state.

## Read-only contract

- Do not create, update, migrate, rename, or delete any file.
- Do not repair invalid conversation records during resume. Report the error and identify the affected path.
- Do not run commands that mutate Git, Fluent, services, processes, or the project.
- Do not trust saved volatile state without refreshing its authoritative source when it controls the next step.
- Do not load every Project Conversation or historical checkpoint when one current projection is enough.
- Do not expose secrets found in saved records. Report only the safe locator and access requirement.

## Resume a Project Conversation

### 1. Locate the durable project

Use the project path named by the user. Otherwise use the enclosing Git root, then the current directory when no Git root exists.

Check project instructions and `git worktree list --porcelain`. If the current directory is a temporary or delegated worktree, locate the durable human-facing checkout before reading project-level conversation files.

Use `<project>/.scratch` as the conversation root unless the user or project instructions name another root.

### 2. Discover saved conversations

Prefer the canonical store:

```text
.scratch/CONVERSATIONS.md
.scratch/<conversation-id>/CONVERSATION.md
.scratch/_conversations/sessions/<checkpoint>.md
```

When `.scratch/CONVERSATIONS.md` exists, verify that it has:

```yaml
managed_by: conversation-continuity
conversation_version: 1
```

Run the read-only validator relative to this SKILL.md:

```sh
python3 scripts/validate_conversations.py <project-root>
```

Proceed when validation passes. Review warnings and preserve their uncertainty in the Resume Brief. If validation fails, report the structural contradiction and stop before treating any Resume action as current.

When canonical files do not exist, fall back to `.scratch/HANDOFFS.md` and linked `HANDOFF.md` files as legacy input. State that legacy records were not validated against the current schema. Never migrate them during resume.

When both canonical and legacy files exist for one ID, prefer the canonical Current Conversation. Follow a legacy link only when its history is needed to understand an unresolved fact.

If neither store exists, report that the project has no saved Project Conversations and stop.

### 3. Select one Project Conversation

Resolve the Conversation ID in this order:

1. Use the ID explicitly named by the user.
2. Use the enclosing `.scratch/<conversation-id>` directory.
3. Match the user's stated goal, current path, artifacts, or Fluent identifiers to an indexed conversation.
4. Use the sole nonterminal conversation when exactly one exists.

Treat `ready`, `waiting-user`, and `waiting-external` as nonterminal. Do not silently choose among several plausible conversations.

When selection remains ambiguous, show only the relevant index rows with Conversation ID, status, updated time, and first Resume line. Ask the user which one to load and stop.

### 4. Read the current projection

Read:

- the selected `.scratch/<conversation-id>/CONVERSATION.md`
- its `latest_checkpoint`
- project instructions governing the work
- older History links only when the current projection or latest checkpoint leaves a relevant gap

Check that the Current Conversation and latest checkpoint agree about:

- Conversation ID and affected conversations
- breakpoint and first Resume step
- user constraints and unanswered questions
- changed artifacts and their durability
- status and current-state labels

Prefer the Current Conversation for latest normalized state. Use checkpoints for chronology, rationale, and coverage gaps. Report contradictions instead of silently selecting the more convenient statement.

Read [references/evidence.md](references/evidence.md) when evaluating Verification or Evidence claims.

### 5. Refresh volatile facts

Inspect only facts needed to understand whether the saved Resume step is still valid.

For Git work, run:

```sh
python3 scripts/collect_git_state.py --project <project-root> [--compare <ref> ...]
```

Keep configured upstream, comparison refs, committed, pushed, deployed, dirty, external, and temporary state distinct. A clean Git status does not establish that ignored `.scratch` files are absent or unchanged.

If the Current Conversation has `mode: fluent-linked`, read [references/fluent.md](references/fluent.md) and follow its resume rules. Refresh live Fluent state from the durable project root.

Use read-only service, process, test-artifact, or generated-output checks only when the next decision depends on them. Label facts as `verified-now`, `artifact-backed`, `reported`, or `inferred`. Include an `as of` timestamp for refreshed facts.

### 6. Produce a Resume Brief

Present an ephemeral brief in chat. Do not write it to `.scratch`.

```markdown
## Resumed conversation

- Project: <durable project root>
- Conversation: <conversation-id>
- Saved status: <status>
- Saved at: <updated_at>

### Objective and breakpoint

<Current intent and where the last Agent Session stopped.>

### Resume

<Exact first action, user question, external condition, parked trigger, or closed outcome.>

### Decisions and constraints

<Only decisions and constraints that still control the work.>

### Relevant artifacts

<Paths, identifiers, and whether each is durable, temporary, or missing.>

### Refreshed state

<What was checked now, what changed since the checkpoint, and the source/time.>

### Gaps and contradictions

<Unavailable context, unverified claims, stale state, and conflicts. Write `None known.` when appropriate.>

### Sources read

<Current Conversation, checkpoint, authoritative project or Fluent artifacts, and commands run.>
```

Keep the brief compact enough to become working context. Do not reproduce the full checkpoint.

For `waiting-user`, end by asking the exact saved question. For `waiting-external`, name the event and read-only recheck. For `ready`, state the first action; perform it only when the current request explicitly asks to continue or execute it. For `parked`, state the reactivation trigger. For `closed`, state the outcome and that no next action is recorded.

## Finish

Tell the user which Project Conversation was loaded, whether validation passed, and which volatile facts were refreshed. State explicitly that resume made no file changes.
