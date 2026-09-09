#!/usr/bin/env bash
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Install apt packages on a GitHub-hosted runner, resiliently.
#
# Reads the space-separated package list from $INPUT_PACKAGES (deliberately
# an environment variable, not a command-line interpolation, so a workflow
# expression can never be spliced into this script's own command line).
#
# The invariant this script exists to hold: **`apt-get update`'s exit status
# never gates the job; `apt-get install`'s does.** `apt-get update` fails as
# a whole when *any* configured source fails, including the third-party
# vendor repositories pre-baked into GitHub's runner images
# (dl.google.com/linux/chrome-stable, packages.microsoft.com, ...) that none
# of the packages we ask for ever come from. When one of those serves a
# stale index ("Hash Sum mismatch") or a 403, an `update && install` chain
# aborts before `install` runs at all -- so a Chrome index that nothing here
# uses takes out gcc/g++/cmake and every downstream step. apt itself is
# explicit that this is survivable: it prints "They have been ignored, or
# old ones used instead" and keeps the previously-fetched indexes, which on
# a runner image are already populated for every Ubuntu archive.
#
# Failing loudly is still preserved, just moved to the step that can
# actually speak to it: if a package really is unavailable (a genuinely
# broken *Ubuntu* mirror, a typo, a repository the caller added that did not
# fetch), `apt-get install` fails, the attempt is retried, and the run ends
# with a clear diagnostic naming the packages.
set -uo pipefail

read -r -a packages <<<"${INPUT_PACKAGES:-}"
if [ "${#packages[@]}" -eq 0 ]; then
  echo "::error::install-system-deps: no packages given (INPUT_PACKAGES is empty)" >&2
  exit 2
fi

attempts="${APT_INSTALL_ATTEMPTS:-3}"
retry_delay="${APT_INSTALL_RETRY_DELAY:-10}"
update_timeout="${APT_INSTALL_UPDATE_TIMEOUT:-120}"
install_timeout="${APT_INSTALL_INSTALL_TIMEOUT:-240}"

# Post-condition: every requested package is actually installed. `apt-get
# install -y` exiting 0 is the primary signal, but with `update` no longer
# gating, this is the check standing between a degraded package index and a
# lane that believes it has a toolchain it does not have.
#
# `dpkg-query -W` exits non-zero for a name it holds no record of, which is
# the answer for a package that was never installed -- and, indistinguishably,
# for a purely virtual name satisfied through another package's `Provides:`.
# Since the two cannot be told apart here, this treats "no record" as
# missing: every package list in this repository names a real package, and a
# false failure that says exactly which package is absent is recoverable,
# whereas a false success hands the lane a missing compiler and lets it fail
# later, somewhere less legible.
verify_installed() {
  missing=()
  local pkg status
  for pkg in "${packages[@]}"; do
    # Skip anything that is an apt option rather than a package name.
    case "$pkg" in -*) continue ;; esac
    status="$(dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null)" || status=""
    if [ "$status" != "install ok installed" ]; then
      missing+=("$pkg")
    fi
  done
  [ "${#missing[@]}" -eq 0 ]
}

for attempt in $(seq 1 "$attempts"); do
  # `timeout`'s default signal is SIGTERM, which a caught/ignored signal in
  # apt-get, dpkg, or sudo's own child-signal handling can survive
  # indefinitely -- `timeout` then just waits forever for the process to
  # exit, never escalating on its own (Codex review, confirmed against a
  # real TERM-ignoring subprocess: `timeout N cmd` alone did not bound it,
  # `timeout -k 10 N cmd` did). Without `--kill-after` this reintroduces the
  # exact silent-hang failure mode this action exists to close. `-k 10`
  # sends SIGKILL 10s after the initial SIGTERM if still alive.
  if ! timeout -k 10 "$update_timeout" sudo apt-get update -qq; then
    echo "::warning::apt-get update reported an error on attempt $attempt/$attempts (an unrelated third-party source is the usual cause); continuing with the existing package index"
  fi
  if timeout -k 10 "$install_timeout" sudo apt-get install -y "${packages[@]}"; then
    if verify_installed; then
      exit 0
    fi
    echo "::warning::apt-get install exited 0 but these packages are not installed: ${missing[*]}"
  fi
  if [ "$attempt" -lt "$attempts" ]; then
    echo "::warning::apt-get attempt $attempt/$attempts failed or timed out, retrying in ${retry_delay}s..."
    sleep "$retry_delay"
  fi
done
echo "::error::apt-get install failed after $attempts attempts (packages: ${packages[*]})"
exit 1
