#!/usr/bin/env bash
# Install abicheck from the Action's own source tree ($1), once per job.
#
# A job that calls this Action several times (`uses: ./` in consecutive
# steps) used to rebuild and reinstall the identical wheel on every call --
# most of each call's wall time. This skips the install when the SAME
# interpreter already holds an install of the SAME source content, made by
# an earlier call in this job. The fingerprint covers the interpreter path,
# its version, and every file under pyproject.toml + abicheck/, so a job that
# changes the source (another checkout, a different action ref) reinstalls.
# Only the most recent install is remembered per interpreter: switching back
# to an earlier source reinstalls rather than trusting a stale marker. Any
# failure to fingerprint falls back to a plain install.
#
# Both inline Python probes run with `-I`: the job's working directory is the
# caller's (possibly untrusted) checkout, so without isolation Python would
# import a checkout-controlled `sitecustomize.py` at startup, and
# `import abicheck` would find the checkout's own `abicheck/` source tree
# instead of the installed package (tests/test_action_run_sh_py_safe_path.py).
set -uo pipefail

src="${1:?usage: install-abicheck.sh <action-path>}"

fingerprint() {
  local py
  py="$(command -v python)" || return 1
  {
    printf '%s\n' "$py"
    "$py" -I -c 'import sys; print(sys.version)' || return 1
    (cd "$src" && find pyproject.toml abicheck -type f -not -path '*/__pycache__/*' -print0 \
      | LC_ALL=C sort -z | xargs -0 sha256sum) || return 1
  } | sha256sum | cut -c1-64
}

marker_dir="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/abicheck-action-install"
fp="$(fingerprint 2>/dev/null)" || fp=""
if [ -n "$fp" ]; then
  py_key="$(command -v python | sha256sum | cut -c1-16)"
  marker="$marker_dir/$py_key"
  if [ -f "$marker" ] && [ "$(cat "$marker")" = "$fp" ] \
     && python -I -c 'import abicheck' >/dev/null 2>&1; then
    echo "abicheck from this source is already installed in this job's interpreter; skipping reinstall."
    exit 0
  fi
fi

pip install "$src" || exit $?

if [ -n "$fp" ]; then
  mkdir -p "$marker_dir" && printf '%s' "$fp" > "$marker"
fi
