# AI

Skills, expertise, and configuration for working with AI agents.

## Install skills

```sh
npx skills add mrinalwadhwa/ai#main --skill save-conversation --skill resume-conversation --agent claude-code codex --global --yes
```

## Install configuration

```sh
./configuration/install --conversation-continuity
```

Install both conversation skills first; the lifecycle checks invoke them by name.

## Update and check the installation

```sh
./configuration/update --conversation-continuity
./configuration/doctor --conversation-continuity
```

`update` installs the managed skills for Claude and Codex from each manifest's source branch and verifies them against the checkout before removing anything retired from those clients. It then installs the configuration and runs the doctor. Managed skill changes must be committed on that branch and available from the source repository. The command does not update the Git checkout itself.

`doctor` is read-only. It checks the installed instructions, expertise registry, skill contents and provenance, retired skills, lifecycle controller, and Claude and Codex hooks.

[`configuration/skills.json`](configuration/skills.json) records the source branch and directory for each managed skill. A retired entry records its former directory, replacement, and recognized source repositories; those fields determine whether `update` may remove it.

A Claude session that invoked a retired skill can retain its definition after the installed skill is removed. Save the conversation, start a new session, and resume it instead of modifying the old transcript.

The optional flag installs lifecycle checks for top-level Claude and Codex sessions. A new or cleared session conditionally loads matching Project Conversation state. A save check runs after the first turn, then after eight later turns or 45 minutes, and after compaction. Claude also requests checks at 70% and 85% context use when it does not already have a custom status line. Turn and time intervals start when the preceding check finishes, not when it starts.

Automatic checks are silent when they succeed or make no changes. Cadence triggers an evaluation, not necessarily a checkpoint. Recoverable live state is never saved. Unfinished discussion is saved only when the session is intentionally pausing or visible context is at risk.

Each save check tells a long-running session to reload the installed save skill before acting. Managed Project Conversation files are published through the skill's bundled publisher rather than separate Write or Edit calls. A needed checkpoint uses one snapshot call and one publish call, with the request passed from memory over stdin. The controller, skill, and publisher must declare the same publication protocol; an incomplete or mismatched installation stops without changing conversation files.

Checks follow the project associated with the session's working directory. The controller resolves a `main/` durable checkout from its workspace container and from linked worktrees. For work deliberately conducted across another project, use an explicit save or resume request to name that project.

The checks are best-effort. An interrupt or client crash can occur before a Stop hook runs. Save checks remain pending while the client reports plan mode and run after plan mode ends.

Automatic first-save bootstrapping requires a Git checkout or an existing managed Conversation Index. Use an explicit save request for a new non-Git directory.

The installer preserves unrelated Claude and Codex hooks. Review and trust the installed Codex definitions with `/hooks`. Set `AGENT_CONVERSATION_CONTINUITY=off` to disable the controller for a process, or `force` to run it in bypass-permission mode. Optional threshold overrides live in `~/.agents/conversation-continuity.json`:

```json
{
  "save_every_turns": 8,
  "save_every_minutes": 45,
  "context_thresholds": [70, 85]
}
```

The controller records one metadata-only JSON event for each completed check that it requests under `~/.agents/state/conversation-continuity/events/`. Events include the trigger causes, duration, and whether the Conversation Index changed. They do not include prompts, responses, or saved conversation content.

## Skills

| Skill | Use |
|-------|-----|
| [save-conversation](skills/save-conversation/SKILL.md) | Save the visible agent session as durable Project Conversation state |
| [resume-conversation](skills/resume-conversation/SKILL.md) | Load saved Project Conversation state without changing project files |
