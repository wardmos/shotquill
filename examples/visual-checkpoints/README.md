# Visual checkpoints for coding agents

A thin walkthrough of the [`visual-checkpoints`](../../skills/visual-checkpoints/SKILL.md)
skill: how a **coding agent** (Claude Code, Codex, OpenCode, …) leaves a
ShotQuill visual trace at the few moments it verifies its work on screen — a
frontend page, an app window, a built UI — without recording its edits or shell
output.

There is **no new code here**. The integration is the MCP tool contract: any host
that speaks MCP connects to `squill mcp` and calls the recording tools. The skill
supplies the *recipe* (when to checkpoint, how to assert); this file shows the
wiring and one concrete run.

## Why MCP (and why coding agents are different)

A coding agent mostly edits files and runs commands — there is no screen to
record. It touches a GUI only in a narrow slice: **verifying** that a change
rendered. The recipe records exactly that slice. Because the seam is the MCP
**tool contract** (not a vendor SDK), the same setup works across hosts with no
per-vendor code — unlike a computer-use loop, which is written per provider (see
[`examples/computer_use`](../computer_use/README.md)).

## Register `squill mcp`

**Claude Code**

```bash
claude mcp add shotquill -- squill mcp
```

**Codex / OpenCode** (and Claude Desktop) — add an MCP stdio server to the host's
config. The shape is the same everywhere; the file differs per host:

```json
{
  "mcpServers": {
    "shotquill": { "command": "squill", "args": ["mcp"] }
  }
}
```

> Some hosts (e.g. Codex's `config.toml`) use TOML rather than JSON; translate the
> same `command` / `args` pair into the host's MCP-server syntax. Check your
> host's "add an MCP server" docs for the exact file and key names — don't assume
> it's JSON.

Requires a real display and screen-recording permission on the machine that runs
the app under test. If a capture is refused, the `doctor` tool names the
per-platform fix.

## A run, end to end

Task: *"add an export button to the reports page and make sure it shows up."* The
agent edits code and runs the dev server as usual (none of that is recorded),
then checkpoints the result:

```
record_start  agent="claude-code"  label="add export button to reports"
  → { conversation_id: "conv-2026-…" }

# (agent edits the component, starts the dev server — not recorded)

record_frame  session="conv-2026-…"  tool="verify"
              label="reports page shows the Export button"
              app="Chromium"  contains=["Export"]
  → { assertion_passed: true,  index: 1,  image: ".../frames/0001.png" }

record_end    session="conv-2026-…"
  → { filmstrip: ".../index.html",  frames: 1 }
```

If the button hadn't rendered, `assertion_passed` would be `false`, the frame is
**still** filed and marked failed in the filmstrip, and the OTLP span is set to
error — the failing checkpoint *is* the bug report. The agent points the user (or
a reviewing AI) at the filmstrip path, or an OTel backend ingests
`trace.otlp.json` from the session directory.

## What not to record

Only the visual verification points. The agent's edits, `bash` output, file
reads, and test logs have no GUI and stay out of the trace — recording them would
bury the checkpoints that matter. See the skill's "What to checkpoint — and what
not to" for the discipline.

## Privacy

Blocklist redaction is on and cannot be disabled mid-session; pass `app` /
`window_id` / `region` to frame just the app under test. This minimizes the
captured surface and keeps the trace local — it is **not** a guarantee the frames
are free of user content. ShotQuill makes no network requests; the session is
files on disk until someone moves them.
