#!/usr/bin/env bash
# Resolve one component of a shared `library-spec` into the root Action's
# new-side operands (artifact, headers, include roots), written to
# $GITHUB_OUTPUT as artifact=/header=/include=. The spec is the same file
# actions/baseline's `library-spec` consumes, so the baseline job and the
# compare gate cannot drift apart on a target's header set.
set -euo pipefail

if [[ -z "${INPUT_LIBRARY_SPEC_TARGET:-}" ]]; then
  echo "::error::library-spec is set but library-spec-target is empty -- name the component in the spec this run compares."
  exit 64
fi
conflicts=()
for pair in "new-library:${INPUT_NEW_LIBRARY:-}" "header:${INPUT_HEADER:-}" \
            "new-header:${INPUT_NEW_HEADER:-}" "include:${INPUT_INCLUDE:-}" \
            "new-include:${INPUT_NEW_INCLUDE:-}"; do
  [[ -n "${pair#*:}" ]] && conflicts+=("${pair%%:*}")
done
if (( ${#conflicts[@]} )); then
  echo "::error::library-spec supplies this target's new-side artifact, headers and include roots; remove ${conflicts[*]} -- two declarations of one target is the drift library-spec exists to remove."
  exit 64
fi

elf_flag=--require-elf
if [[ "${RUNNER_OS:-Linux}" != "Linux" ]]; then
  elf_flag=--no-require-elf
fi
python -m abicheck.frontends.action.cli resolve-libraries \
  "$INPUT_LIBRARY_SPEC" \
  --root . \
  "$elf_flag" \
  --target "$INPUT_LIBRARY_SPEC_TARGET" \
  --github-output "${GITHUB_OUTPUT:?}" >/dev/null
