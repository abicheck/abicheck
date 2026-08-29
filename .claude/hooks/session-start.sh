#!/bin/bash
# SessionStart hook for Claude Code on the web.
#
# Thin Claude-specific adapter over scripts/setup_dev_env.sh (the
# canonical, vendor-neutral setup logic -- AGENTS.md's adapter
# principle: don't hand-duplicate a command here). This hook only adds
# what's specific to *this* runtime:
#   1. Runs only in a remote session ($CLAUDE_CODE_REMOTE guard).
#   2. Persists castxml's PATH entry into $CLAUDE_ENV_FILE so it's on
#      PATH for the rest of the session, not just this hook process.
#   3. Reorders PATH so the pip-installed, correctly-pinned pytest/ruff/
#      mypy (CLAUDE.md's ruff==0.16.3/mypy==1.19.1 pins) actually run
#      when you type `pytest`/`ruff`/`mypy` -- this image also ships a
#      pre-existing `uv tool install`-managed pytest/ruff in
#      ~/.local/bin, earlier on PATH than pip's /usr/local/bin, silently
#      shadowing the project-pinned versions (observed: ruff 0.15.8
#      instead of the pinned 0.16.3) with no error, just a different
#      lint verdict.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"

bash scripts/setup_dev_env.sh

# Put pip's install dir ahead of ~/.local/bin so the pinned pytest/ruff/
# mypy setup_dev_env.sh just installed win over the pre-existing uv-tool
# shadow described above.
echo "export PATH=\"/usr/local/bin:\$PATH\"" >> "${CLAUDE_ENV_FILE}"

# shellcheck source=../../scripts/castxml_pin.env
source scripts/castxml_pin.env
if [ -x "${CASTXML_PREFIX}/bin/castxml" ] && ! command -v castxml >/dev/null 2>&1; then
  echo "export PATH=\"${CASTXML_PREFIX}/bin:\$PATH\"" >> "${CLAUDE_ENV_FILE}"
fi

echo "==> Setup complete."
