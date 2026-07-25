#!/usr/bin/env bash
# Install system dependencies for abicheck via the conda-forge `scanner` pixi
# environment (pyproject.toml's [tool.pixi.feature.native-toolchain]) instead
# of the system/checksum-pinned-Superbuild path (install-deps.sh). Called by
# the composite action when dependency-source=conda-forge.
#
# Prepending the whole pixi environment's bin/ directory to PATH would also
# put its own `python`/`python3` ahead of whatever actions/setup-python
# configured for the rest of the job (the workspace-level
# `[tool.pixi.pypi-dependencies] abicheck = {path=".", editable=true}` pulls
# in a conda Python transitively, even for a compiler-only environment) --
# a real footgun for any consumer workflow with steps after this action that
# expect their own configured Python. Instead this symlinks only the specific
# compiler/scanner binaries this environment actually provides into a
# dedicated directory and prepends that -- everything else on PATH is
# unaffected.
set -euo pipefail

echo "::group::Install system dependencies for abicheck (conda-forge)"

action_dir="$(cd "$(dirname "$0")/.." && pwd)"
pixi_env_bin="${action_dir}/.pixi/envs/scanner/bin"

if [ ! -d "$pixi_env_bin" ]; then
  echo "::error::pixi 'scanner' environment not found at ${pixi_env_bin}." \
    "The 'Set up pixi (conda-forge)' step must run before this script."
  exit 1
fi

shim_dir="${RUNNER_TEMP:-/tmp}/abicheck-conda-forge-bin"
mkdir -p "$shim_dir"

# Only the tools the conda-forge native-toolchain feature actually
# provisions (castxml + a C/C++ compiler) -- deliberately not python/pip/etc.
for tool in castxml gcc g++ cc c++ gcc-ar gcc-nm gcc-ranlib; do
  if [ -x "${pixi_env_bin}/${tool}" ]; then
    ln -sf "${pixi_env_bin}/${tool}" "${shim_dir}/${tool}"
  fi
done

echo "$shim_dir" >> "${GITHUB_PATH:?GITHUB_PATH not set}"
export PATH="${shim_dir}:${PATH}"

echo "::endgroup::"

# Verify castxml is available (same verification contract as install-deps.sh)
if command -v castxml &> /dev/null; then
  echo "castxml version: $(castxml --version 2>&1 | head -1)"
else
  echo "::warning::castxml not found in the conda-forge scanner environment. Header analysis will not be available."
  echo "Binary-only mode (exports/imports) will still work."
fi

if command -v clang &> /dev/null; then
  echo "clang version: $(clang --version 2>&1 | head -1)"
else
  echo "::notice::clang not found in the conda-forge scanner environment. Source-ABI replay (L4) and source graphs (L5)"
  echo "used by 'scan --sources' will be skipped; abicheck degrades gracefully (L0-L2 stay authoritative)."
  echo "Use dependency-source=system (or install clang manually) to enable source scanning."
fi

if command -v bear &> /dev/null; then
  echo "bear version: $(bear --version 2>&1 | head -1)"
else
  echo "::notice::bear not found. Make/Autotools projects that do not emit a"
  echo "compile_commands.json will fall back to reduced-confidence 'make -n'"
  echo "scraping for L3; wrap the build with 'bear -- make …' for authoritative L3/L4."
fi
