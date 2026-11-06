"""The release table's Breaking column can count gating findings no kind row shows.

``result_counts.gating`` (the audit's effective total) includes findings the
redundancy filter folded into another finding but the verdict still scored;
``gating_retained`` covers only what the review groups itemise. A member with
folded-scored findings must say so beside the Breaking column, or a reader sees
a total that the per-kind rows cannot add up to (oneDAL 2025.9 -> 2026.0: 4
extra breaks, identical per-kind counts).
"""

from __future__ import annotations

from abicheck.report.render_release_markdown import (
    _release_md_folded_gating,
    _release_md_libraries_table,
)

EMOJI = {"BREAKING": "❌", "NO_CHANGE": "="}


def _lib(name: str, *, gating: int, retained: int, breaking: int) -> dict[str, object]:
    return {
        "library": name,
        "verdict": "BREAKING",
        "breaking": breaking,
        "source_breaks": 0,
        "risk_changes": 0,
        "compatible_additions": 0,
        "result_counts": {"gating": gating, "gating_retained": retained},
    }


def test_folded_gating_sums_per_member_remainder() -> None:
    libs = [
        _lib("libA", gating=10, retained=6, breaking=10),
        _lib("libB", gating=3, retained=3, breaking=3),
        _lib("libC", gating=5, retained=5, breaking=5),
    ]
    assert _release_md_folded_gating(libs) == 4


def test_table_names_folded_gating_beside_breaking_column() -> None:
    libs = [_lib("libA", gating=10, retained=6, breaking=10)]
    text = "\n".join(_release_md_libraries_table(libs, EMOJI))
    assert "includes 4 gating findings folded into another finding" in text
    assert "still scored by the verdict" in text
    assert "`result_counts.gating_retained`" in text


def test_singular_wording_for_one_folded_finding() -> None:
    libs = [_lib("libA", gating=3, retained=2, breaking=3)]
    text = "\n".join(_release_md_libraries_table(libs, EMOJI))
    assert "includes 1 gating finding folded" in text


def test_no_note_when_nothing_is_folded() -> None:
    libs = [_lib("libA", gating=2, retained=2, breaking=2)]
    text = "\n".join(_release_md_libraries_table(libs, EMOJI))
    assert "folded" not in text


def test_member_without_result_counts_contributes_nothing() -> None:
    libs: list[dict[str, object]] = [{"library": "libX", "verdict": "NO_CHANGE"}]
    assert _release_md_folded_gating(libs) == 0
    text = "\n".join(_release_md_libraries_table(libs, EMOJI))
    assert "folded" not in text
