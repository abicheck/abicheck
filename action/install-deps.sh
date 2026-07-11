#!/usr/bin/env bash
# Install system dependencies for abicheck:
#   - castxml + gcc/g++  → L2 public-header analysis (always)
#   - clang/clang++      → L4 source-ABI replay, the S2 preprocessor pre-scan,
#                          and L5 call/include graphs used by `scan --sources`
# Called by the composite action when install-deps=true.
set -euo pipefail

echo "::group::Install system dependencies for abicheck"

OS="$(uname -s)"
case "$OS" in
  Linux)
    if ! command -v apt-get &> /dev/null; then
      echo "::warning::apt-get not found. Skipping automatic dependency installation on Linux."
      echo "Please ensure castxml, clang, and a C++ compiler are installed manually."
    elif ! command -v sudo &> /dev/null; then
      echo "::warning::sudo not found. Skipping automatic dependency installation."
      echo "Please ensure castxml, clang, and a C++ compiler are installed manually."
    else
      sudo apt-get update -qq
      # clang enables L4 source-ABI replay + L5 graphs for `scan --sources`;
      # castxml/gcc remain the L2 header path and the L4 declaration fallback;
      # bear generates a compile_commands.json for Make/Autotools projects that
      # do not emit one (`bear -- make …`), which is what unlocks L3/L4/L5 there.
      sudo apt-get install -y -qq castxml gcc g++ clang bear > /dev/null
    fi
    ;;
  Darwin)
    # macOS: castxml via Homebrew; clang/clang++ are pre-installed via Xcode
    # (so L4/L5 source scanning works out of the box).
    if ! command -v brew &> /dev/null; then
      echo "::warning::Homebrew not found. Skipping automatic castxml installation on macOS."
      echo "Please install castxml manually: https://github.com/CastXML/CastXML/releases"
    elif ! command -v castxml &> /dev/null; then
      brew install castxml
    fi
    ;;
  MINGW*|MSYS*|CYGWIN*|Windows_NT)
    echo "::warning::Windows dependency installation is not automated."
    echo "Please ensure castxml and a C++ compiler are on PATH."
    echo "See: https://github.com/CastXML/CastXML/releases"
    ;;
  *)
    echo "::warning::Unknown OS '$OS'. Skipping dependency installation."
    ;;
esac

echo "::endgroup::"

# Verify castxml is available
if command -v castxml &> /dev/null; then
  echo "castxml version: $(castxml --version 2>&1 | head -1)"
else
  echo "::warning::castxml not found. Header analysis will not be available."
  echo "Binary-only mode (exports/imports) will still work."
fi

# Verify bear is available (generates compile_commands.json for Make projects)
if command -v bear &> /dev/null; then
  echo "bear version: $(bear --version 2>&1 | head -1)"
else
  echo "::notice::bear not found. Make/Autotools projects that do not emit a"
  echo "compile_commands.json will fall back to reduced-confidence 'make -n'"
  echo "scraping for L3; wrap the build with 'bear -- make …' for authoritative L3/L4."
fi

# Verify clang is available (used by source scans: L4 replay, S2, L5 graphs)
if command -v clang &> /dev/null; then
  echo "clang version: $(clang --version 2>&1 | head -1)"
else
  echo "::warning::clang not found. Source-ABI replay (L4) and source graphs (L5)"
  echo "used by 'scan --sources' will be skipped; abicheck degrades gracefully"
  echo "(L0-L2 stay authoritative). Install clang to enable source scanning."
fi
