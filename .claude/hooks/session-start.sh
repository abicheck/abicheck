#!/bin/bash
# SessionStart hook for Claude Code on the web.
#
# Installs everything the abicheck "fast"/"pr" verify profiles need
# (CLAUDE.md/AGENTS.md "M0-3", scripts/verify.py):
#   1. abicheck itself + its dev/docs/dist extras (pytest, ruff, mypy,
#      hypothesis, mkdocs, build/twine, ...) so `pytest`, `ruff`,
#      `mypy`, and `python scripts/verify.py --profile pr` all work
#      out of the box.
#   2. castxml, from conda-forge (via a throwaway micromamba, since
#      castxml has no apt/pip package) — the default L2 header-AST
#      backend `dumper_castxml.py` shells out to, and what the
#      `integration` pytest marker needs. Skipped if a system
#      castxml is already on PATH (e.g. a pixi-provisioned image).
#   3. Fixes up PATH so the pip-installed, correctly-pinned pytest/ruff/
#      mypy (CLAUDE.md's ruff==0.16.3/mypy==1.19.1 pins) actually run
#      when you type `pytest`/`ruff`/`mypy` -- this image also ships a
#      pre-existing `uv tool install`-managed pytest/ruff in
#      ~/.local/bin, earlier on PATH than pip's /usr/local/bin, silently
#      shadowing the project-pinned versions (observed: ruff 0.15.8
#      instead of the pinned 0.16.3) with no error, just a different lint
#      verdict.
#
# gcc/g++/clang/cmake are already present in this container's base
# image (Ubuntu 24.04), so they are only sanity-checked here, not
# installed.
#
# Not installed (opt-in, not needed for day-to-day development):
#   - libabigail (abidiff) / abi-compliance-checker: only used by the
#     `libabigail`/`abicc` parity pytest markers, which the "fast"
#     profile already excludes. See pyproject.toml's
#     [tool.pixi.feature.parity] if you need them.
#   - MSVC/PDB tooling: Windows-only (`msvc` marker), not installable
#     on Linux.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"

echo "==> Installing abicheck (editable) + dev/docs/dist extras"
# --ignore-installed: this image's system python has dpkg-owned packages
# (e.g. python3-packaging) with no pip RECORD, which pip cannot uninstall
# to satisfy a newer version pin -- ignore-installed shadows them instead
# of trying to remove them first, which is what pip itself recommends for
# this exact PEP 668 / debian-packaged-python conflict.
pip install -q --ignore-installed -e ".[dev,docs,dist]"

# Put pip's install dir ahead of ~/.local/bin so the pinned pytest/ruff/
# mypy this just installed win over any pre-existing `uv tool` shadow.
export PATH="/usr/local/bin:${PATH}"
echo "export PATH=\"/usr/local/bin:\$PATH\"" >> "${CLAUDE_ENV_FILE}"

echo "==> Checking native toolchain (gcc/g++/cmake)"
for tool in gcc g++ cmake; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "::warning::$tool not found on PATH — integration tests needing it will fail." >&2
  fi
done

CASTXML_PREFIX="${HOME}/.cache/abicheck-castxml-conda"

if command -v castxml >/dev/null 2>&1; then
  echo "==> castxml already on PATH: $(command -v castxml)"
elif [ -x "${CASTXML_PREFIX}/bin/castxml" ]; then
  echo "==> castxml already installed at ${CASTXML_PREFIX} (cached)"
else
  echo "==> Installing castxml from conda-forge via micromamba"
  MICROMAMBA_BIN_DIR="$(mktemp -d)"
  curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest \
    | tar -xj -C "${MICROMAMBA_BIN_DIR}" bin/micromamba
  "${MICROMAMBA_BIN_DIR}/bin/micromamba" create -y \
    -p "${CASTXML_PREFIX}" \
    -c conda-forge \
    castxml
  rm -rf "${MICROMAMBA_BIN_DIR}"
fi

if [ -x "${CASTXML_PREFIX}/bin/castxml" ] && ! command -v castxml >/dev/null 2>&1; then
  export PATH="${CASTXML_PREFIX}/bin:${PATH}"
  echo "export PATH=\"${CASTXML_PREFIX}/bin:\$PATH\"" >> "${CLAUDE_ENV_FILE}"
fi

echo "==> castxml version: $(castxml --version 2>/dev/null | head -1)"
echo "==> Setup complete."
