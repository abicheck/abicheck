#!/usr/bin/env python3
"""Re-record the extractor/resolver generation fingerprints (ADR-062 D2).

The only sanctioned way to change ``abicheck/storage/generation_fingerprints.json``.
Run it after you edit a scoped module, in one of two ways:

``--bump``
    The semantics changed. First raise ``{AXIS}_GENERATION`` in
    ``abicheck/storage/versioning.py`` by exactly one; this refuses unless the
    constant is one above the recorded generation.

``--no-semantic-change --reason TEXT``
    The scoped edit changes no fact the extractor records and no graph the
    resolver derives (a refactor, a rename, a comment). The generation stays
    put. The reason is written into the JSON so the claim shows up in the PR
    diff for review. Required; an empty reason is refused.

With no record yet, ``--no-semantic-change`` writes the initial recording at
the current constant.
"""

from __future__ import annotations

import argparse
import sys

from abicheck.storage import generation_scope as scope, versioning


def _entry(axis: str, generation: int, reason: str) -> dict[str, object]:
    """The record entry for *axis* at *generation*: digests, fingerprint and reason."""
    files = scope.file_digests(scope.scope_files(axis))
    return {
        "generation": generation,
        "fingerprint": scope.fingerprint(files),
        "reason": reason,
        "files": files,
    }


def main(argv: list[str] | None = None) -> int:
    """Re-record one axis after a bump or a reviewed no-semantic-change; returns the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--axis", required=True, choices=["extractor", "resolver"])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--bump", action="store_true")
    mode.add_argument("--no-semantic-change", action="store_true")
    parser.add_argument("--reason", default="")
    args = parser.parse_args(argv)

    constant_name = f"{args.axis.upper()}_GENERATION"
    constant = int(getattr(versioning, constant_name))
    record = scope.load_record()
    previous = record.get(args.axis)
    recorded = int(previous["generation"]) if isinstance(previous, dict) else None

    if args.bump:
        if recorded is None or constant != recorded + 1:
            print(
                f"refusing: versioning.{constant_name} is {constant} but the record is "
                f"at {recorded}; raise the constant by exactly one first",
                file=sys.stderr,
            )
            return 2
        reason = args.reason or f"bumped to {constant} for a semantic change"
        record[args.axis] = _entry(args.axis, constant, reason)
    else:
        if not args.reason.strip():
            print(
                "refusing: --no-semantic-change requires a non-empty --reason",
                file=sys.stderr,
            )
            return 2
        if recorded is not None and constant != recorded:
            print(
                f"refusing: versioning.{constant_name} is {constant} but the record is at "
                f"{recorded}; use --bump for a generation change",
                file=sys.stderr,
            )
            return 2
        record[args.axis] = _entry(args.axis, constant, args.reason.strip())

    scope.write_record(record)
    print(
        f"recorded {args.axis} at generation {constant}: {record[args.axis]['fingerprint']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
