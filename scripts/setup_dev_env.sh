#!/bin/bash
# Vendor-neutral dev-environment bootstrap for abicheck.
#
# Installs what a from-scratch Linux dev environment needs to run the
# "fast"/"pr" scripts/verify.py profiles (CLAUDE.md/AGENTS.md "M0-3"):
#   1. abicheck itself, editable, with the dev/docs/dist extras (pytest,
#      ruff, mypy, hypothesis, mkdocs, build/twine, ...).
#   2. castxml, from conda-forge via a throwaway micromamba (castxml has
#      no apt/pip package) -- pinned to the exact linux-64 build
#      `pixi.lock` already commits to (see CASTXML_BUILD below), so this
#      never resolves a different CastXML/bundled-Clang version than the
#      one this repo has actually reviewed. Bump both together.
#
# This is the canonical, tool-agnostic setup logic (AGENTS.md's adapter
# principle: a command lives in one place, adapters call it rather than
# hand-duplicating it) -- e.g. `.claude/hooks/session-start.sh` sources
# this rather than re-implementing it.
#
# Not installed here (opt-in, not needed for day-to-day development):
#   - libabigail (abidiff) / abi-compliance-checker: only used by the
#     `libabigail`/`abicc` parity pytest markers, which the "fast"
#     profile already excludes. See pyproject.toml's
#     [tool.pixi.feature.parity], or `pixi install` generally, if you
#     want the full, pixi-managed toolchain instead of this script.
#   - A C/C++ compiler toolchain (gcc/g++/clang/cmake): expected to
#     already be present; this script only sanity-checks for it.
#   - MSVC/PDB tooling: Windows-only (`msvc` marker), not installable on
#     Linux.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

echo "==> Installing abicheck (editable) + dev/docs/dist extras"
# --ignore-installed: some Debian-based images ship dpkg-owned Python
# packages (e.g. python3-packaging) with no pip RECORD, which pip cannot
# uninstall to satisfy a newer version pin -- ignore-installed shadows
# them instead of trying to remove them first, which is pip's own
# recommended workaround for this PEP 668 / debian-packaged-python
# conflict. A no-op flag on a normal virtualenv.
pip install -q --ignore-installed -e ".[dev,docs,dist]"

echo "==> Checking native toolchain (gcc/g++/cmake)"
for tool in gcc g++ cmake; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "WARNING: $tool not found on PATH -- integration tests needing it will fail." >&2
  fi
done

# Pinned to match pixi.lock's committed linux-64 solve exactly (`grep
# castxml pixi.lock`) -- keep these two in lockstep on a version bump.
readonly CASTXML_BUILD="castxml=0.7.0=hde8d07d_0"
CASTXML_PREFIX="${HOME}/.cache/abicheck-castxml-conda"

if command -v castxml >/dev/null 2>&1; then
  echo "==> castxml already on PATH: $(command -v castxml)"
elif [ -x "${CASTXML_PREFIX}/bin/castxml" ]; then
  echo "==> castxml already installed at ${CASTXML_PREFIX} (cached)"
else
  echo "==> Installing ${CASTXML_BUILD} from conda-forge via micromamba"
  MICROMAMBA_BIN_DIR="$(mktemp -d)"
  curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest \
    | tar -xj -C "${MICROMAMBA_BIN_DIR}" bin/micromamba
  "${MICROMAMBA_BIN_DIR}/bin/micromamba" create -y \
    -p "${CASTXML_PREFIX}" \
    -c conda-forge \
    "${CASTXML_BUILD}"
  rm -rf "${MICROMAMBA_BIN_DIR}"
fi

if [ -x "${CASTXML_PREFIX}/bin/castxml" ] && ! command -v castxml >/dev/null 2>&1; then
  export PATH="${CASTXML_PREFIX}/bin:${PATH}"
fi

echo "==> castxml version: $(castxml --version 2>/dev/null | head -1)"
echo "==> setup_dev_env.sh complete."

# Callers that need castxml on PATH beyond this process (e.g. a
# SessionStart hook persisting env for the rest of the session) should
# add "${HOME}/.cache/abicheck-castxml-conda/bin" to PATH themselves --
# this script only guarantees it's on PATH for its own remaining steps.
