#!/bin/bash
# Vendor-neutral dev-environment bootstrap for abicheck.
#
# Installs what a from-scratch Linux dev environment needs to run the
# "fast"/"pr" scripts/verify.py profiles (CLAUDE.md/AGENTS.md "M0-3"):
#   1. abicheck itself, editable, with the dev/docs/dist extras (pytest,
#      ruff, mypy, hypothesis, mkdocs, build/twine, ...), into an isolated
#      venv (DEV_VENV_DIR, scripts/dev_venv_pin.env) rather than the
#      system interpreter -- some Debian-based images ship dpkg-owned
#      Python packages (e.g. python3-packaging) with no pip RECORD, which
#      an in-place `pip install` on the system interpreter can't upgrade
#      cleanly. `--ignore-installed` "fixes" that particular conflict, but
#      it's a whole-invocation flag, not scoped to the one conflicting
#      package: pip warns it can overwrite *any* package another package
#      manager installed, and it would rerun on every session regardless
#      of what's already satisfied. A dedicated venv has no dpkg-owned
#      packages to conflict with in the first place, so nothing needs
#      ignoring.
#   2. castxml, from conda-forge via a throwaway micromamba (castxml has
#      no apt/pip package) -- pinned to the exact linux-64 build `pixi.lock`
#      already commits to (CASTXML_BUILD, scripts/castxml_pin.env), *and*
#      its complete transitive runtime closure pinned exactly too
#      (CASTXML_LOCKED_SPECS -- castxml's own conda `depends:` only bounds
#      those to ranges, so a direct-matchspec-only install can still drift
#      on when/where it runs even with CASTXML_BUILD fixed). Bump both
#      together with pixi.lock. The micromamba bootstrap binary itself is
#      pinned by version + verified sha256 (MICROMAMBA_VERSION/
#      MICROMAMBA_SHA256 below), the same download-then-verify shape
#      `action/install-castxml.sh` uses for its own archive -- it is code
#      that runs before any of the above, so an unpinned "latest" fetch
#      would be exactly the unverified-supply-chain gap this pin closes
#      for castxml itself.
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

# shellcheck source=dev_venv_pin.env
source "${REPO_ROOT}/scripts/dev_venv_pin.env"

# Require a real, working pip, not just the python3 executable: `venv`
# installs pip via ensurepip as a separate step after creating the
# interpreter, so a session interrupted between the two would otherwise
# leave a venv this check calls "done" but that has no pip -- the later
# bare `pip install` would then silently fall through PATH to whatever
# pip comes next (the system interpreter this venv exists to avoid), and
# no future run would ever repair it since the python3 executable alone
# already looks complete.
if [ ! -x "${DEV_VENV_DIR}/bin/python3" ] || ! "${DEV_VENV_DIR}/bin/python3" -m pip --version >/dev/null 2>&1; then
  echo "==> Creating dev venv at ${DEV_VENV_DIR}"
  rm -rf "${DEV_VENV_DIR}"
  "$(dev_venv_python)" -m venv "${DEV_VENV_DIR}"
fi
# Ahead of the rest of PATH for the remainder of this script: every
# `python3`/`pip` call below (and the castxml_policy import further down)
# resolves inside the venv, not the system interpreter.
export PATH="${DEV_VENV_DIR}/bin:${PATH}"

echo "==> Installing abicheck (editable) + dev/docs/dist extras"
# Explicit venv path, not a bare `pip`: PATH was just changed in this same
# process, and a subshell/later refactor that runs this before the export
# takes effect should still install into the venv, never fall through to
# whatever pip happens to be on the pre-existing PATH.
"${DEV_VENV_DIR}/bin/pip" install -q -e ".[dev,docs,dist]"

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
  # Only linux-64 has a pinned+verified micromamba asset and a reviewed
  # castxml build (CASTXML_BUILD above, and pixi.lock's own platform list
  # -- linux-64/osx-64/osx-arm64/win-64 -- carries no linux-aarch64 pin to
  # point at either). Fail clearly rather than silently fetching the
  # wrong-architecture micromamba binary (checksum would pass, then
  # `Exec format error` on the first run) or an unpinned castxml.
  HOST_ARCH="$(uname -m)"
  if [ "$(uname -s)" != "Linux" ] || [ "${HOST_ARCH}" != "x86_64" ]; then
    echo "ERROR: no pinned castxml/micromamba install path for $(uname -s) ${HOST_ARCH}." >&2
    echo "This script only supports linux-64 (x86_64 Linux). Install castxml" >&2
    echo "yourself (conda-forge/Homebrew/apt) and ensure it's on PATH, or use" >&2
    echo "'pixi install' (pyproject.toml's [tool.pixi.feature.native-toolchain]" >&2
    echo "already covers osx-64/osx-arm64 too)." >&2
    exit 1
  fi

  echo "==> Fetching micromamba ${MICROMAMBA_VERSION} (verifying sha256)"
  WORK_DIR="$(mktemp -d)"
  trap 'rm -rf "${WORK_DIR}"' EXIT
  curl -Ls -o "${WORK_DIR}/micromamba" \
    "https://github.com/mamba-org/micromamba-releases/releases/download/${MICROMAMBA_VERSION}/micromamba-linux-64"
  echo "${MICROMAMBA_SHA256}  ${WORK_DIR}/micromamba" | sha256sum -c -
  chmod +x "${WORK_DIR}/micromamba"

  echo "==> Installing ${CASTXML_BUILD} (full locked closure) from conda-forge via micromamba"
  "${WORK_DIR}/micromamba" create -y \
    -p "${CASTXML_PREFIX}" \
    -c conda-forge \
    "${CASTXML_LOCKED_SPECS[@]}"
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

# Defer to abicheck's own authoritative CastXML/bundled-Clang policy gate
# (abicheck/castxml_policy.py's evaluate_castxml_version) instead of a
# hand-rolled version-substring check: it's the exact same check abicheck
# itself runs before an authoritative L2 scan (a CastXML version range
# *and* a minimum bundled-Clang major -- a version-only check would pass
# e.g. CastXML 0.7.0 linked against Clang 17, which this policy still
# rejects), so "setup succeeded" and "abicheck will actually accept this
# toolchain" can't silently disagree. Importable here because pip install
# above already put abicheck on this interpreter's path. A castxml
# resolved via the "already on PATH" branch above is the one case this
# can actually trip -- this script's own pinned conda-forge install is
# always in range. Warn, don't fail: a deliberately different system/pixi
# castxml on PATH is that branch's whole reason to exist.
if ! python3 -c '
import sys
from abicheck.castxml_policy import evaluate_castxml_version

result = evaluate_castxml_version(sys.stdin.read())
if not result.supported:
    sys.stderr.write(result.message() + "\n")
    sys.exit(1)
' <<<"${CASTXML_FULL_OUTPUT}"; then
  echo "WARNING: the castxml on PATH does not meet abicheck's own CastXML/Clang" >&2
  echo "policy (above) -- an authoritative L2 scan will reject it at run time." >&2
fi
echo "==> setup_dev_env.sh complete."

# Callers that need the dev venv's tools (pytest/ruff/mypy/...) or castxml
# on PATH beyond this process (e.g. a SessionStart hook persisting env for
# the rest of the session) should source scripts/dev_venv_pin.env /
# scripts/castxml_pin.env themselves and add "${DEV_VENV_DIR}/bin" /
# "${CASTXML_PREFIX}/bin" to PATH -- this script only guarantees they're
# on PATH for its own remaining steps.
