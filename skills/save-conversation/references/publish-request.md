# Conversation publication requests

Use one request to provide the complete checkpoint and Current Conversations to `scripts/publish_conversation.py`. The publisher derives every path and the Conversation Index. Request content cannot choose another output location.

## Contents

- Request format
- Capture base values
- Publish in one canonical mutation call
- Use a temporary request file
- Handle results

## Request format

Use this version 1 envelope:

```text
conversation_continuity_request: 1
boundary: <16-to-64 lowercase hexadecimal characters>
checkpoint_name: <version-1 checkpoint filename>
index_base: <absent|sha256:hash>
conversation: <conversation-id> <absent|sha256:hash>

--<boundary> checkpoint--
<complete checkpoint Markdown>
--<boundary> conversation <conversation-id>--
<complete Current Conversation Markdown>
--<boundary> end--
```

Repeat the `conversation` header and part once for each affected Project Conversation. Their sets must match. Put one blank line between the headers and first part. End every document and the request with a line feed.

Choose a new random boundary for the request and make sure no document contains one of its marker lines. The boundary separates inert document content; it is not written to a canonical file.

Use the checkpoint filename you would ordinarily choose. If that name already exists, the publisher adds `-2`, `-3`, or the next available suffix without replacing the published checkpoint. Write `@CHECKPOINT@` everywhere either document refers to the new filename. Every Current Conversation must contain the placeholder.

## Capture base values

Copy the `request_headers` lines exactly from:

```sh
python3 "<save-conversation-skill>/scripts/publish_conversation.py" \
  snapshot "<project-root>" \
  --conversation "<conversation-id>" [--conversation "<conversation-id>" ...]
```

`absent` requires the target to remain absent. The publisher rejects stale values while holding its publication lock.

## Publish in one canonical mutation call

Prefer a quoted stdin block when the client can send it safely:

```sh
python3 "<save-conversation-skill>/scripts/publish_conversation.py" \
  publish "<project-root>" --request - <<'CONVERSATION_SAVE_9f2a4c8e1b6d7350'
<complete request>
CONVERSATION_SAVE_9f2a4c8e1b6d7350
```

Replace the example nonce with a fresh value, then verify that the exact delimiter line is absent from the complete request. The quoted delimiter prevents command substitution, variable expansion, and other shell interpretation inside the request. The fresh value prevents document content from ending stdin early. Do not use an unquoted, fixed, or unchecked delimiter.

The normal path has two publisher calls: one to capture bases and one to publish. Only the second call changes canonical Project Conversation files.

## Use a temporary request file

When the client cannot send a large stdin block reliably, use a client-managed temporary directory accessible only to the current OS user. Create the request file exclusively with mode `0600` or an equivalent ACL, record it as a temporary side effect, and run:

```sh
python3 "<save-conversation-skill>/scripts/publish_conversation.py" \
  publish "<project-root>" --request "<request-file>"
```

The fallback adds one temporary Write. The publisher reads a regular non-symlink request file once and never deletes it. Do not place secrets in the request. If the client cannot guarantee private creation, use stdin. Leave removal to the client or operating-system temporary-file lifecycle unless cleanup is already authorized.

## Handle results

The publisher rejects unknown headers, duplicate conversations, malformed names, stale bases, invalid managed documents, unsafe paths, symlinks, and validation warnings before it changes canonical files.

Revise the request after `invalid-request` or `request-review-required`. After `conflict`, take another snapshot and reconcile the new canonical state. Never add a force flag or edit a canonical file to bypass a failure.

Follow the complete status table in the parent SKILL.md. In particular, a durability warning is not an ordinary cleanup warning, and `recovery-required` preserves the marker, manifest, candidates, and backups because restoration could not be proven safe.
