#!/usr/bin/env python3
"""Re-record the extractor/resolver generation fingerprints (ADR-062 D2).

The only sanctioned way to change ``abicheck/storage/generation_fingerprints.json``.
Run it after you edit a scoped module, in one of two ways:

``--bump``
    The semantics changed. First raise ``{AXIS}_GENERATION`` in
    ``abicheck/storage/versioning.py`` by exactly one; this refuses unless the
    constant is one above the recorded generation.

``--rerecord-unshipped --reason TEXT``
    Re-record the current generation in place as a semantic change, for a
    generation that has not shipped. Allowed only while the recorded generation
    is above the one recorded in ``origin/main``'s fingerprint file (read with
    ``git show``); a shipped generation, an unreadable main, or a missing
    reason is refused. The reason is written into the record.

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
import json
import subprocess
import sys
from collections.abc import Callable

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


MAIN_RECORD_PATH = "abicheck/storage/generation_fingerprints.json"


def _read_main_record_text() -> str:
    """``origin/main``'s fingerprint file, read through git (fails closed)."""
    done = subprocess.run(
        ["git", "show", f"origin/main:{MAIN_RECORD_PATH}"],
        capture_output=True,
        text=True,
        cwd=str(scope.REPO_ROOT),
        check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(
            f"cannot read origin/main:{MAIN_RECORD_PATH}: {done.stderr.strip()}"
        )
    return done.stdout


def main_recorded_generation(
    axis: str, read_main_text: Callable[[], str] = _read_main_record_text
) -> int:
    """*axis*'s generation in ``origin/main``'s fingerprint file; raises if unreadable."""
    try:
        data = json.loads(read_main_text())
        return int(data[axis]["generation"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"origin/main's {axis} generation is unreadable: {exc}"
        ) from exc


def main(
    argv: list[str] | None = None,
    *,
    read_main_text: Callable[[], str] = _read_main_record_text,
) -> int:
    """Re-record one axis after a bump or a reviewed no-semantic-change; returns the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--axis", required=True, choices=["extractor", "resolver", "source"]
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--bump", action="store_true")
    mode.add_argument("--no-semantic-change", action="store_true")
    mode.add_argument("--rerecord-unshipped", action="store_true")
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
    elif args.rerecord_unshipped:
        if not args.reason.strip():
            print(
                "refusing: --rerecord-unshipped requires a non-empty --reason",
                file=sys.stderr,
            )
            return 2
        if recorded is None or constant != recorded:
            print(
                "refusing: --rerecord-unshipped re-records the recorded generation; "
                f"versioning.{constant_name} is {constant} but the record is at {recorded}",
                file=sys.stderr,
            )
            return 2
        try:
            shipped = main_recorded_generation(args.axis, read_main_text)
        except RuntimeError as exc:
            print(f"refusing: {exc}", file=sys.stderr)
            return 2
        if recorded <= shipped:
            print(
                f"refusing: generation {recorded} has shipped (origin/main records "
                f"{shipped}); use --bump for a semantic change",
                file=sys.stderr,
            )
            return 2
        record[args.axis] = _entry(args.axis, constant, args.reason.strip())
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
