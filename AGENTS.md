# AGENTS.md

ShotQuill is a Python 3.10+ PySide6 desktop app for screenshot capture,
annotation, CLI, and MCP workflows.

## Setup Commands

- Install dev dependencies with `pip install -e ".[dev]"`.
- Common checks are `ruff check src tests`, `ruff format --check src tests`, and
  `pytest`.

## Code Style

- Keep changes small, focused, and consistent with existing module boundaries.
- Do not introduce new dependencies unless they are clearly justified.
- Keep user-facing behavior cross-platform by default. Isolate
  platform-specific code behind existing backend boundaries and call out
  intentional gaps.

## Testing

- Run the relevant checks before submitting. If a check cannot run locally,
  document the reason in the PR.
- Use `QT_QPA_PLATFORM=offscreen` for headless Qt tests when appropriate.
- For platform code, test the portable backend contract where possible.
- Capture, hotkey, and window-activation behavior may require validation on the
  target OS.
- Add or update tests for behavior changes, bug fixes, and privacy-sensitive
  code paths.

## Security

- Do not weaken privacy, redaction, allowlist/blocklist, audit-log, CLI, or MCP
  behavior without focused tests.
- Avoid logging desktop content, credentials, tokens, or private user data.
- Do not commit screenshots, recordings, logs, fixtures, or docs containing
  secrets, credentials, private user data, or unrelated desktop content.

## Commit Messages

- Use Conventional Commit style, such as `feat(capture): add display selection`.
- Keep messages focused on the code change and avoid local environment details.

## Branch Names

- Name release preparation branches `release/v<version>`, for example
  `release/v0.0.15`.

## Pull Requests

- Use `.github/pull_request_template.md` for PR descriptions.
