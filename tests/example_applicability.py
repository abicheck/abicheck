"""Toolchain applicability of a catalog case's transition.

``not_applicable_toolchains`` is not a detection gap: the producer never
creates the artifact transition the case encodes (Clang never emits
``STB_GNU_UNIQUE``, so case180's v1/v2 are both plain weak symbols). A lane on
such a producer checks the verdict that transition-free pair actually warrants
(``not_applicable_expected``) and reports ``NOT_APPLICABLE`` -- neither a PASS
that proves the canonical verdict nor an XFAIL that excuses a miss. Shared by
``validate_examples.py``; split out of it to keep that runner's size bounded.
"""

from __future__ import annotations


def not_applicable_on(entry: dict, family: str) -> tuple[str, str] | None:
    """``(expected verdict, reason)`` when the case's transition does not exist
    under the producer *family* ("gcc"/"clang") that built it, else ``None``."""
    toolchains = entry.get("not_applicable_toolchains")
    if not toolchains or family not in toolchains:
        return None
    return (
        str(entry.get("not_applicable_expected", "NO_CHANGE")),
        str(entry.get("not_applicable_reason", "")),
    )


def not_applicable_outcome(got: str, na_expected: str, reason: str) -> tuple[str, str]:
    """``(status, detail)`` for a not-applicable case that reported *got*."""
    if got == na_expected:
        return "NOT_APPLICABLE", reason
    return (
        "FAIL",
        f"transition not applicable under this toolchain; expected "
        f"{na_expected!r} for the transition-free pair, got {got!r}",
    )


def outcome(entry: dict, family: str, got: str) -> tuple[str, str] | None:
    """``(status, detail)`` when the case is not applicable under *family*,
    else ``None`` (evaluate it normally)."""
    na = not_applicable_on(entry, family)
    return None if na is None else not_applicable_outcome(got, *na)
