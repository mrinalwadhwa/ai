# Evidence in Project Conversations

## Contents

- Classify claims
- Record decision-driving evidence
- Match evidence to the claim
- Mark evidence gaps

## Classify claims

Classify claims that determine a next action or may change between sessions.

| Basis | Use when |
|-------|----------|
| `verified-now` | The saving or resuming agent checked an authoritative source during this invocation. |
| `artifact-backed` | A durable artifact supports the claim, but the underlying action was not rerun. |
| `reported` | The claim comes only from the visible conversation, a prior record, or a person. |
| `inferred` | The claim is a conclusion drawn from named facts rather than a directly observed result. |

Name the reporter or source. Do not promote `reported` to `verified-now` because the statement sounds precise.

## Record decision-driving evidence

Use this compact form. Omit fields that do not apply, but never omit a known gap.

```markdown
- Claim: <bounded claim and scope>.
  - Basis: <verified-now|artifact-backed|reported|inferred>.
  - Source: <command, durable path, system, person, or prior record>.
  - Checked: <ISO-8601 timestamp with offset>, when checked now.
  - Reproduce: <cwd plus command>, or `unavailable: <missing input or procedure>`.
  - Result: <number, exit status, state, or observation>.
  - Limits: <what this evidence does not establish>.
```

Every claim requires `Claim`, `Basis`, `Source`, and `Result`. A `verified-now` claim also requires `Checked` with an
ISO-8601 timestamp and offset. Use `Reproduce` for commands and measurements; when reproduction inputs are missing,
write `unavailable: <reason>` instead of reconstructing them. Use `Limits` whenever the evidence could be mistaken for
a broader guarantee.

For a test, include its working directory, exact command, result, time, and scope. For a measurement, include durable
inputs or labels, method or script, output artifact, sample size, time, and limits. If any required part was not
preserved, keep the result as `reported` and set Reproduce to `unavailable`.

## Match evidence to the claim

Use evidence from the same boundary as the claim. A transform that changes only punctuation proves that transform's
local behavior; it does not prove end-to-end word fidelity when a later model may rewrite the text.

Keep Git facts separate:

- A configured upstream comes only from `@{upstream}`.
- A remote-tracking ref at the same commit is a comparison result, not an upstream.
- A pushed commit does not prove that the worktree is clean, deployed, or running.
- Clean Git status does not include ignored conversation-file writes; keep the invocation's file list separately.

Keep implementation, test, deployment, and runtime claims separate for the same reason.

## Mark evidence gaps

Do not recreate missing evidence from memory. State the useful result, classify it as `reported`, identify the missing
inputs or command, and put the gap in Coverage gaps or Open questions when it affects the next decision.

Artifact locators must be usable by a fresh Claude or Codex session. Omit model-memory names and private client indexes
unless the underlying content is also available at a durable path. Otherwise mark the locator client-specific and
unavailable.
