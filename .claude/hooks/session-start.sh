#!/bin/bash
# SessionStart hook for Claude Code on the web.
#
# Thin Claude-specific adapter over scripts/setup_dev_env.sh (the
# canonical, vendor-neutral setup logic -- AGENTS.md's adapter
# principle: don't hand-duplicate a command here). This hook only adds
# what's specific to *this* runtime:
#   1. Runs only in a remote session ($CLAUDE_CODE_REMOTE guard).
#   2. Persists the dev venv's and castxml's PATH entries into
#      $CLAUDE_ENV_FILE so they're on PATH for the rest of the session,
#      not just this hook process.
#   3. That venv-bin entry also happens to fix a real, observed problem:
#      this image ships a pre-existing `uv tool install`-managed
#      pytest/ruff in ~/.local/bin, earlier on PATH than anything a plain
#      `pip install` would use, silently shadowing the project-pinned
#      versions (observed: ruff 0.15.8 instead of CLAUDE.md's pinned
#      0.16.3) with no error, just a different lint verdict. Putting the
#      venv first on PATH wins over that shadow the same way it would for
#      any other pre-existing PATH entry.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"

bash scripts/setup_dev_env.sh

# The cloud clone is shallow, and check_ai_readiness's ADR "Verified" receipt
# check needs the cited commits reachable from origin/main: on a shallow
# clone it reports them unreachable and reads as a real failure. Best-effort
# so an offline start still completes.
if [ "$(git rev-parse --is-shallow-repository 2>/dev/null)" = "true" ]; then
  git fetch --quiet --unshallow origin || echo "==> warning: git fetch --unshallow failed; ADR receipt checks may misreport"
fi

# Put the dev venv ahead of ~/.local/bin so the pinned pytest/ruff/mypy
# setup_dev_env.sh just installed win over the pre-existing uv-tool
# shadow described above.
# shellcheck source=../../scripts/dev_venv_pin.env
source scripts/dev_venv_pin.env
echo "export PATH=\"${DEV_VENV_DIR}/bin:\$PATH\"" >> "${CLAUDE_ENV_FILE}"

# shellcheck source=../../scripts/castxml_pin.env
source scripts/castxml_pin.env
if [ -x "${CASTXML_PREFIX}/bin/castxml" ] && ! command -v castxml >/dev/null 2>&1; then
  echo "export PATH=\"${CASTXML_PREFIX}/bin:\$PATH\"" >> "${CLAUDE_ENV_FILE}"
fi

echo "==> Setup complete."
