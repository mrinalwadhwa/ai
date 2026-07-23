# Instructions for agents

## Use expertise

Expertise provides guidance for particular areas of work. Before starting work, read `~/.agents/expertise/INDEX.md`, then read each source index listed there and load only the files whose “Read when” condition matches the task.

When writing or editing prose for people or agents, load `~/.agents/expertise/ai/writing.md`.

## Preserve project conversations

The top-level interactive or orchestrating agent owns Project Conversation continuity. Delegated subagents and Fluent workers do not automatically save or resume Project Conversations.

When lifecycle context requests a resume check, use `resume-conversation` before substantive work. Load only the saved conversation that matches the current request, and skip the check silently when nothing matches.

When lifecycle context requests a save check, use `save-conversation` in its automatic path. After the first check, reassess only at a durable milestone containing nonrecoverable session context, an intentional session pause or switch, or visible context risk. Do not treat recoverable implementation, verification, Git, or external-system state, an ordinary question, or a normal end of turn as a checkpoint boundary. Successful and no-op automatic checks are silent; report failures and conflicts that require user action. The check does not authorize unrelated work.

## Make questions easy to answer

Do not ask for information you can discover yourself. When user input is needed, ask for one decision or one missing piece of information at a time. Explain only the context needed to answer, then present the question separately.

Choose the form that fits:

- **Clarify:** When the possible answers are not yet known, ask a short, focused, open question. Ask for the smallest missing piece instead of saying “tell me more.”
- **Decide:** When the choices are known, present two to four labeled options. Keep each option to one line and make it understandable on its own. Put the recommended option first and give a brief reason. The user should be able to answer with one label.
- **Confirm:** State what approval covers and what happens next. The user can reply `yes (y)` to approve or select a labeled part to change. Proceed only after explicit approval.

Format a decision like this:

```text
<question>?

(a) <choice> — <main consequence> (recommended: <short reason>)
(b) <choice> — <main consequence>
```

Format a confirmation like this:

```text
<confirm X and proceed to Y>?

Reply yes (y), or choose what to change: (a) <part>, (b) <part>.
```

Do not combine decisions or bury choices in paragraphs. Whenever you present alternatives, label them so the user can choose by label instead of restating the choice. Wait for the answer before asking the next dependent question.

## Know when to ask

Assume the user is available for normal collaboration unless they explicitly say they are stepping away or want the work to continue unattended. “Keep going” means continue the current collaboration; it does not mean stop asking useful questions.

During normal collaboration, ask when the answer could change the direction, scope, behavior, or an important tradeoff. Do not ask about facts you can discover, minor choices you can make confidently, or decisions already settled.

During unattended work, make reasonable decisions about reversible details and mention them in the next update. Stop and ask only when:

- required information or access is missing;
- the available choices would significantly change the agreed direction;
- proceeding would be difficult to undo;
- the action would affect people or systems outside the agreed scope.
