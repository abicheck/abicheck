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
#      one this repo has actually reviewed. Bump both together. The
#      micromamba bootstrap binary itself is pinned by version + verified
#      sha256 (MICROMAMBA_VERSION/MICROMAMBA_SHA256 below), the same
#      download-then-verify shape `action/install-castxml.sh` uses for its
#      own archive -- it is code that runs before any of the above, so an
#      unpinned "latest" fetch would be exactly the unverified-supply-chain
#      gap this pin closes for castxml itself.
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

# shellcheck source=castxml_pin.env
source "${REPO_ROOT}/scripts/castxml_pin.env"

# Pinned micromamba release (mamba-org/micromamba-releases), verified
# against its published sha256 before it is ever executed -- this
# bootstrap binary itself runs unsandboxed, so "latest" + no verification
# would be an unpinned, unverified code-execution step ahead of castxml's
# own pin.
readonly MICROMAMBA_VERSION="2.9.0-0"
readonly MICROMAMBA_SHA256="366cd9cd8be14df1ab8ed50352a82111082a36686b2d389fdb79a92c3fafb3e3"

if command -v castxml >/dev/null 2>&1; then
  echo "==> castxml already on PATH: $(command -v castxml)"
elif [ -x "${CASTXML_PREFIX}/bin/castxml" ]; then
  echo "==> castxml already installed at ${CASTXML_PREFIX} (cached, pinned build)"
else
  echo "==> Fetching micromamba ${MICROMAMBA_VERSION} (verifying sha256)"
  WORK_DIR="$(mktemp -d)"
  trap 'rm -rf "${WORK_DIR}"' EXIT
  curl -Ls -o "${WORK_DIR}/micromamba" \
    "https://github.com/mamba-org/micromamba-releases/releases/download/${MICROMAMBA_VERSION}/micromamba-linux-64"
  echo "${MICROMAMBA_SHA256}  ${WORK_DIR}/micromamba" | sha256sum -c -
  chmod +x "${WORK_DIR}/micromamba"

  echo "==> Installing ${CASTXML_BUILD} from conda-forge via micromamba"
  "${WORK_DIR}/micromamba" create -y \
    -p "${CASTXML_PREFIX}" \
    -c conda-forge \
    "${CASTXML_BUILD}"
  rm -rf "${WORK_DIR}"
  trap - EXIT
fi

if [ -x "${CASTXML_PREFIX}/bin/castxml" ] && ! command -v castxml >/dev/null 2>&1; then
  export PATH="${CASTXML_PREFIX}/bin:${PATH}"
fi

# Explicit failure branch: `echo "$(cmd)"` never fails even if cmd does,
# so a castxml that's on PATH but broken (bad install, wrong arch, a
# missing shared lib) would otherwise be silently reported as success.
# No `| head -1` here: under `set -o pipefail`, castxml exiting early from
# SIGPIPE once head is done reading would itself look like a failure --
# take the first line in bash instead, after capturing the real exit code.
if ! CASTXML_FULL_OUTPUT="$(castxml --version 2>&1)"; then
  echo "ERROR: castxml is on PATH but 'castxml --version' failed:" >&2
  echo "${CASTXML_FULL_OUTPUT}" >&2
  exit 1
fi
echo "==> castxml version: ${CASTXML_FULL_OUTPUT%%$'\n'*}"
echo "==> setup_dev_env.sh complete."

# Callers that need castxml on PATH beyond this process (e.g. a
# SessionStart hook persisting env for the rest of the session) should
# source scripts/castxml_pin.env themselves and add "${CASTXML_PREFIX}/bin"
# to PATH -- this script only guarantees it's on PATH for its own
# remaining steps.
