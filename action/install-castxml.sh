#!/usr/bin/env bash
# Install the pinned CastXML Superbuild used by abicheck CI.
#
# The Ubuntu package currently bundles Clang 17, which cannot parse some GCC 13
# libstdc++ headers (notably the statement form of __attribute__((__assume__))).
# Keep the release tag and SHA256 values explicit so a runner image update cannot
# silently change the header-AST frontend.
set -euo pipefail

readonly CASTXML_TAG="v2026.01.30"
readonly CASTXML_RELEASE_BASE="https://github.com/CastXML/CastXMLSuperbuild/releases/download/${CASTXML_TAG}"
readonly EXPECTED_CASTXML_VERSION="0.6.20260105-g9864b1e"
readonly EXPECTED_BUNDLED_CLANG_VERSION="21.1.8"
readonly MIN_BUNDLED_CLANG_MAJOR=18

fail() {
  echo "::error::$*" >&2
  exit 1
}

[ "$(uname -s)" = "Linux" ] || fail "The pinned installer currently supports Linux only. Use Homebrew/conda-forge on this platform."
[ -r /etc/os-release ] || fail "Cannot identify this Linux distribution (/etc/os-release is missing)."
# shellcheck disable=SC1091
. /etc/os-release
[ "${ID:-}" = "ubuntu" ] || fail "The pinned CastXML assets are validated for Ubuntu; detected ${ID:-unknown}."

case "${VERSION_ID:-}:$(uname -m)" in
  22.04:x86_64)
    asset="castxml-ubuntu-22.04-x86_64.tar.gz"
    sha256="6df17fe726e48bbe1d584e6aa508de5427e65ab2dc9be4a7795c88f1679da9ab"
    ;;
  22.04:aarch64|22.04:arm64)
    asset="castxml-ubuntu-22.04-arm-aarch64.tar.gz"
    sha256="4ad76d41c8e82845f116ecef6d65e2e0b08801dd06b8f8eab8f84be0dad3c304"
    ;;
  24.04:x86_64)
    asset="castxml-ubuntu-24.04-x86_64.tar.gz"
    sha256="76e7183f8f15bf3ada2009e18e34717366771ca3d2bc23ab1acee315171fdc93"
    ;;
  24.04:aarch64|24.04:arm64)
    asset="castxml-ubuntu-24.04-arm-aarch64.tar.gz"
    sha256="6cacf64b8207c53a27da7f5604e9260374fa4470cbcf8ae2726506bce8f7f86f"
    ;;
  *)
    fail "No pinned CastXML ${CASTXML_TAG} asset for Ubuntu ${VERSION_ID:-unknown} $(uname -m). Use conda-forge or install a verified Superbuild manually."
    ;;
esac

command -v curl >/dev/null 2>&1 || fail "curl is required to download CastXML."
command -v sha256sum >/dev/null 2>&1 || fail "sha256sum is required to verify CastXML."
command -v tar >/dev/null 2>&1 || fail "tar is required to extract CastXML."

install_base="${ABICHECK_CASTXML_INSTALL_ROOT:-${RUNNER_TEMP:-${TMPDIR:-/tmp}}/abicheck-castxml}"
install_dir="${install_base}/${CASTXML_TAG}/${asset%.tar.gz}"
bin_dir="${install_dir}/bin"
castxml_bin="${bin_dir}/castxml"

if [ ! -x "$castxml_bin" ]; then
  work_dir="$(mktemp -d "${TMPDIR:-/tmp}/abicheck-castxml.XXXXXX")"
  trap 'rm -rf "$work_dir"' EXIT
  archive="${work_dir}/${asset}"
  url="${CASTXML_RELEASE_BASE}/${asset}"

  if [ -n "${ABICHECK_CASTXML_ARCHIVE:-}" ]; then
    [ -f "$ABICHECK_CASTXML_ARCHIVE" ] || fail "ABICHECK_CASTXML_ARCHIVE does not exist: ${ABICHECK_CASTXML_ARCHIVE}"
    echo "Installing pinned CastXML ${CASTXML_TAG} from local archive: ${ABICHECK_CASTXML_ARCHIVE}"
    cp "$ABICHECK_CASTXML_ARCHIVE" "$archive"
  else
    echo "Downloading pinned CastXML ${CASTXML_TAG}: ${asset}"
    curl --fail --location --silent --show-error --retry 3 --retry-all-errors \
      --output "$archive" "$url"
  fi
  printf '%s  %s\n' "$sha256" "$archive" | sha256sum --check --strict -

  extract_dir="${work_dir}/extract"
  mkdir -p "$extract_dir"
  # Official archives contain one top-level `castxml/` directory.
  tar -xzf "$archive" -C "$extract_dir" --strip-components=1
  [ -x "${extract_dir}/bin/castxml" ] || fail "Archive ${asset} does not contain castxml/bin/castxml."

  mkdir -p "$(dirname "$install_dir")"
  rm -rf "$install_dir"
  mv "$extract_dir" "$install_dir"
fi

version_output="$($castxml_bin --version 2>&1)" || fail "Pinned CastXML failed its version probe."
castxml_version="$(printf '%s\n' "$version_output" | sed -nE 's/^castxml version ([^[:space:]]+).*/\1/p' | head -1)"
clang_version="$(printf '%s\n' "$version_output" | sed -nE 's/.*clang version ([^[:space:]]+).*/\1/p' | head -1)"
clang_major="${clang_version%%.*}"
[ "$castxml_version" = "$EXPECTED_CASTXML_VERSION" ] || \
  fail "Expected CastXML ${EXPECTED_CASTXML_VERSION}, got ${castxml_version:-unknown}."
[ "$clang_version" = "$EXPECTED_BUNDLED_CLANG_VERSION" ] || \
  fail "Expected bundled Clang ${EXPECTED_BUNDLED_CLANG_VERSION}, got ${clang_version:-unknown}."
[ "$clang_major" -ge "$MIN_BUNDLED_CLANG_MAJOR" ] || \
  fail "CastXML bundles Clang ${clang_major}; Clang >= ${MIN_BUNDLED_CLANG_MAJOR} is required."

export PATH="${bin_dir}:${PATH}"
if [ -n "${GITHUB_PATH:-}" ]; then
  printf '%s\n' "$bin_dir" >> "$GITHUB_PATH"
fi

printf '%s\n' "Selected CastXML: ${castxml_bin}"
printf '%s\n' "$version_output"
