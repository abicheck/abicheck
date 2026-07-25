#!/usr/bin/env bash
# Install system dependencies for abicheck via one of the conda-forge pixi
# environments in pyproject.toml (scanner / gcc14 / clang20 -- selected by
# $ABICHECK_PIXI_ENV, set by action.yml from the resolved dependency-source)
# instead of the system/checksum-pinned-Superbuild path (install-deps.sh).
# Called by the composite action when dependency-source starts with
# "conda-forge".
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

pixi_env="${ABICHECK_PIXI_ENV:-scanner}"

echo "::group::Install system dependencies for abicheck (conda-forge, pixi env: ${pixi_env})"

action_dir="$(cd "$(dirname "$0")/.." && pwd)"
pixi_env_bin="${action_dir}/.pixi/envs/${pixi_env}/bin"

if [ ! -d "$pixi_env_bin" ]; then
  echo "::error::pixi '${pixi_env}' environment not found at ${pixi_env_bin}." \
    "The 'Set up pixi (conda-forge)' step must run before this script."
  exit 1
fi

shim_dir="${RUNNER_TEMP:-/tmp}/abicheck-conda-forge-bin"
mkdir -p "$shim_dir"

# Only the compiler/scanner tools each environment's pixi feature actually
# provisions -- deliberately not python/pip/etc. gcc14 and the default
# scanner environment (native-toolchain's unbound c-compiler/cxx-compiler)
# both resolve to the same gcc-family binary names today; clang20 is a
# distinct clang-family tool list.
case "$pixi_env" in
  clang20)
    tools=(castxml clang clang++)
    ;;
  *)
    tools=(castxml gcc g++ cc c++ gcc-ar gcc-nm gcc-ranlib)
    ;;
esac
for tool in "${tools[@]}"; do
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
  echo "::warning::castxml not found in the conda-forge ${pixi_env} environment. Header analysis will not be available."
  echo "Binary-only mode (exports/imports) will still work."
fi

if command -v clang &> /dev/null; then
  echo "clang version: $(clang --version 2>&1 | head -1)"
else
  echo "::notice::clang not found in the conda-forge ${pixi_env} environment. Source-ABI replay (L4) and source graphs (L5)"
  echo "used by 'scan --sources' will be skipped; abicheck degrades gracefully (L0-L2 stay authoritative)."
  echo "Use dependency-source=conda-forge-clang20 (or =system, or install clang manually) to enable source scanning."
fi

if command -v bear &> /dev/null; then
  echo "bear version: $(bear --version 2>&1 | head -1)"
else
  echo "::notice::bear not found. Make/Autotools projects that do not emit a"
  echo "compile_commands.json will fall back to reduced-confidence 'make -n'"
  echo "scraping for L3; wrap the build with 'bear -- make …' for authoritative L3/L4."
fi
