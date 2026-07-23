# How to write clearly

Write for a reader who needs to understand something, do something, or make a decision. Use plain language and enough shared context for the reader to understand the point without reconstructing your private shorthand.

Use these rules by default for explanations and technical writing. When the requested form is persuasive, narrative, or creative, preserve its intended voice while keeping the meaning clear.

## Contents

- Write for the reader
  - Lead with the point
  - Explain unfamiliar terms
- Use plain language
- Prefer active verbs
  - Prefer `to + verb` when stating purpose
  - Know when noun forms belong
- Write with substance
  - State facts, do not sell
  - Be specific
  - Show the tradeoff
- Keep vocabulary consistent
- Avoid AI writing tells
  - Words to avoid
  - Phrases to avoid
  - Sentence patterns to avoid
  - Formatting tells
- Edit the result

## Write for the reader

Every paragraph should help the reader accomplish a task, understand a concept, or make a decision. Remove a paragraph that does none of these things.

### Lead with the point

Put the answer, command, outcome, or decision before the explanation. Do not make the reader cross a general introduction to reach the useful information.

### Explain unfamiliar terms

Assume only the context the reader has. Do not use terse invented terms, abbreviations, or shorthand from private reasoning or tracking documents without explanation.

Define an unfamiliar term the first time it appears. A short defining clause is usually enough.

Explain what a mechanism does instead of only naming it. Prefer a concrete example over a coined label.

## Use plain language

Use the simplest word that carries the meaning. Formal language creates distance without adding precision.

| Instead of | Use |
|------------|-----|
| utilize | use |
| facilitate | help, make possible |
| commence | start |
| leverage | use |
| endeavor | try |
| plethora | many |
| myriad | many |
| elucidate | explain |

Keep a technical term when it is the precise term. Explain it when the intended reader might not know it.

## Prefer active verbs

Prefer active verbs over nouns made from verbs. Active verbs state who or what performs the action and are easier to scan.

| Avoid | Prefer |
|-------|--------|
| User authentication handling | Authenticate users |
| WebSocket connection management | Manage WebSocket connections |
| Error logging and reporting | Log and report errors |
| Data validation | Validate data |
| Cache invalidation | Invalidate the cache |
| Request processing | Process requests |

### Prefer `to + verb` when stating purpose

Use a `to + verb` phrase when describing purpose.

| Avoid | Prefer |
|-------|--------|
| Provides functions for extracting audio | Provides functions to extract audio |
| For downloading and uploading files | To download and upload files |
| For managing webhooks | To manage webhooks |

### Know when noun forms belong

Noun forms are appropriate for type names, protocol names, module names, category labels, and established domain terms.

When unsure, ask “Who does what?” If the answer can use a subject and verb, prefer the active form.

## Write with substance

A short, correct explanation is better than a long explanation that guesses. Leave out an uncertain claim instead of using it to make the writing appear complete.

After each paragraph, ask what the intended reader learns or can do because of it. Remove filler or add the missing substance.

### State facts, do not sell

Describe what the subject does. Do not argue that it is good, important, innovative, or elegant. If the facts do not show the value, adjectives will not supply it.

Replace praise and adjective stacks such as “robust, scalable, enterprise-grade” with a specific property and evidence.

### Be specific

Replace vague categories with names, values, actions, and examples.

```text
vague:    significant performance improvement
specific: p99 latency dropped from 180 ms to 45 ms

vague:    the system handles errors gracefully
specific: when the API returns 429, the client waits five minutes and retries

vague:    supports multiple deployment targets
specific: runs locally on macOS and Linux, or on AWS Fargate
```

### Show the tradeoff

When explaining a decision, describe what was given up. A decision described only by its benefits reads like marketing.

```text
marketing: We chose S3 for reliable workspace transfer.
engineering: We chose S3 for workspace transfer. ECS Exec piping was attempted but Session Manager does not handle binary data reliably.
```

## Keep vocabulary consistent

Use one term for each concept. If “user” and “member” refer to the same person, or “batch job” and “scheduled task” refer to the same operation, choose one term. Consistent names make prose easier to understand and search.

## Avoid AI writing tells

Generated prose has recurring words, phrases, sentence patterns, and formatting habits. Even when the content is correct, clusters of these patterns make the writing feel generic and can conceal missing substance.

### Words to avoid

Avoid these words in their figurative or inflated senses:

**Strongest signals:** `delve`, `tapestry`, `realm`, `landscape` used metaphorically, `navigate` used metaphorically, `leverage` used as a verb, `utilize`, `harness`, `robust`, `seamless`, `pivotal`, `crucial`, `comprehensive`, `multifaceted`, `holistic`, `nuanced`, `paradigm`, `synergy`, `meticulously`, `vibrant`, `underscore` used figuratively, `foster`, `cultivate`, `showcasing`, `highlighting`, `emphasizing`, `enhance`, `align with`, `bolster`.

**Use with care:** `transformative`, `innovative`, `groundbreaking`, `game-changer`, `elevate`, `empower`, `streamline`, `optimize`, `spearhead`, `reimagine`, `redefine`, `deep dive`, `at its core`.

### Phrases to avoid

Do not start a section with stock framing:

- `In today’s...`
- `In the ever-evolving landscape of...`
- `As we navigate...`
- `As AI continues to...`
- `Let’s dive in.`
- `Let’s explore...`

Cut stock transitions or state the relationship directly:

- `It’s important to note that...`
- `It’s worth mentioning that...`
- `Furthermore`
- `Moreover`
- `Additionally`
- `As mentioned earlier`

Do not end with a stock closing:

- `The future of X is bright.`
- `Only time will tell.`
- `One thing is certain.`

### Sentence patterns to avoid

Avoid formulaic contrast such as “It is not just X; it is Y,” “More than X, it is Y,” and “Not only X, but also Y.” State the meaningful distinction directly.

Do not pose a question and immediately answer it as a rhetorical device. Ask a real question or state the answer.

Remove commentary about what the text is about to do. Replace “In this section, we will explore…” with the content itself.

Use plain `is`, `has`, or a concrete verb when they fit. Do not replace them by habit with `serves as`, `features`, or `offers`.

Do not default to groups of three. Use the number of examples, claims, or adjectives the content requires.

### Formatting tells

- Use bold only for information that needs emphasis. Do not scatter bold text through prose.
- Avoid repeated `**Heading:** description` list items. Use prose, ordinary bullets, or real subsections.
- Replace generic headings such as `Understanding X`, `The Importance of Y`, and `Key Takeaways` with specific headings.
- Vary paragraph length according to the content instead of giving every paragraph the same shape.
- Do not use emoji in headings.

## Edit the result

Before sending or publishing prose, check:

1. Can the intended reader understand every term without private context?
2. Does each paragraph teach the reader something or help them act?
3. Does each evaluative claim have a fact, value, action, or example behind it?
4. Can an active verb replace a noun phrase?
5. Does one name refer to each concept?
6. Does any stock phrase, inflated word, or formulaic pattern remain?
7. Can anything be removed without losing meaning?
