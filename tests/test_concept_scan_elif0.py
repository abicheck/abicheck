"""Dead branches of an ``#if 0`` group, ``#elif 0`` included, are dropped.

The concept pre-scan read the body of an ``#elif 0`` that follows ``#if 0``
as live code, so a ``concept`` spelled only there made the snapshot record
``concept constraints unobserved`` for a header that has none (extractor
generation 59). Oracle: the code lines a preprocessor keeps, written here.
"""

from __future__ import annotations

import pytest

from abicheck.extract.headers.concept_scan import _drop_if0_blocks


@pytest.mark.parametrize(
    ("src", "kept"),
    [
        # The fix: an `#elif 0` of a dead group is dead too.
        ("a\n#if 0\nb\n#elif 0\nc\n#endif\nf", ["a", "f"]),
        ("a\n#if 0\nb\n#elif 0\nc\n#elif 0\nd\n#else\ne\n#endif\nf", ["a", "e", "f"]),
        # Siblings that must not change.
        ("a\n#if 0\nb\n#elif X\nc\n#endif\nf", ["a", "c", "f"]),
        ("a\n#if 0\nb\n#else\nc\n#endif\nf", ["a", "c", "f"]),
        ("a\n#if 0\n#if X\nb\n#elif 0\nc\n#endif\nd\n#endif\nf", ["a", "f"]),
        ("a\n#if X\nb\n#elif 0\nc\n#endif\nf", ["a", "b", "c", "f"]),
    ],
)
def test_dead_branches_of_an_if0_group_are_dropped(src: str, kept: list[str]) -> None:
    lines = [ln for ln in _drop_if0_blocks(src).split("\n") if not ln.startswith("#")]
    assert lines == kept


def test_a_concept_only_in_an_elif0_branch_is_not_reported(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from abicheck.extract.headers.concept_scan import concept_header_scan

    dead = tmp_path / "dead.h"
    dead.write_text(
        "#if 0\nint a;\n#elif 0\ntemplate<class T> concept C = true;\n#endif\n"
    )
    live = tmp_path / "live.h"
    live.write_text(
        "#if 0\nint a;\n#elif 1\ntemplate<class T> concept C = true;\n#endif\n"
    )
    assert not concept_header_scan([str(dead)])
    assert concept_header_scan([str(live)])
