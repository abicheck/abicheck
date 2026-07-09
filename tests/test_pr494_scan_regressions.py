from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from abicheck.cli_scan import _run_baseline_compare


class _Verdict:
    def __init__(self, value: str) -> None:
        self.value = value


def test_scan_baseline_compare_preserves_hard_l0_elf_removal(monkeypatch) -> None:
    """source/full scan must not filter away old-only ELF removals.

    Regression for case97: richer header/source public-surface scoping can miss a
    macro-conditioned old export, but the hard L0 func_removed_elf_only finding
    remains authoritative and must be folded into the final scan verdict.
    """

    old_snap = SimpleNamespace(build_source=None)
    new_snap = SimpleNamespace(build_source=None)
    hard_l0 = SimpleNamespace(kind=SimpleNamespace(value="func_removed_elf_only"))
    calls: list[dict[str, object]] = []

    def fake_resolve_input(*args, **kwargs):  # noqa: ANN002, ANN003
        return old_snap

    def fake_prepare_embedded_build_source(
        old, new, collect_mode, extra_changes, *args  # noqa: ANN001, ANN002
    ):
        return list(extra_changes), [], {}, None

    def fake_compare_snapshots(old, new, *, extra_changes, scope_to_public_surface):  # noqa: ANN001
        calls.append(
            {
                "extra_changes": list(extra_changes),
                "scope_to_public_surface": scope_to_public_surface,
            }
        )
        if not scope_to_public_surface:
            return SimpleNamespace(
                breaking=[hard_l0],
                source_breaks=[],
                risk=[],
                compatible=[],
                verdict=_Verdict("BREAKING"),
            )
        return SimpleNamespace(
            breaking=list(extra_changes),
            source_breaks=[],
            risk=[],
            compatible=[],
            verdict=_Verdict("BREAKING" if extra_changes else "NO_CHANGE"),
        )

    monkeypatch.setattr("abicheck.service.resolve_input", fake_resolve_input)
    monkeypatch.setattr("abicheck.service.compare_snapshots", fake_compare_snapshots)
    monkeypatch.setattr(
        "abicheck.cli_buildsource.prepare_embedded_build_source",
        fake_prepare_embedded_build_source,
    )

    verdict, exit_code, summary = _run_baseline_compare(
        Path("old.so"),
        Path("new.so"),
        new_snap,
        [],
        "c++",
        "graph-full",
        [],
        [],
        [],
        [],
    )

    assert verdict == "BREAKING"
    assert exit_code == 4
    assert summary["breaking"] == 1
    assert calls[0]["scope_to_public_surface"] is False
    assert calls[1]["scope_to_public_surface"] is True
    assert hard_l0 in calls[1]["extra_changes"]


def test_scan_baseline_compare_does_not_promote_advisory_l0_findings(monkeypatch) -> None:
    """Only explicit hard L0 removals are preserved; crosschecks stay advisory."""

    old_snap = SimpleNamespace(build_source=None)
    new_snap = SimpleNamespace(build_source=None)
    advisory = SimpleNamespace(kind=SimpleNamespace(value="header_build_context_mismatch"))

    def fake_resolve_input(*args, **kwargs):  # noqa: ANN002, ANN003
        return old_snap

    def fake_prepare_embedded_build_source(
        old, new, collect_mode, extra_changes, *args  # noqa: ANN001, ANN002
    ):
        return list(extra_changes), [], {}, None

    def fake_compare_snapshots(old, new, *, extra_changes, scope_to_public_surface):  # noqa: ANN001
        if not scope_to_public_surface:
            return SimpleNamespace(
                breaking=[advisory],
                source_breaks=[],
                risk=[],
                compatible=[],
                verdict=_Verdict("BREAKING"),
            )
        assert extra_changes == []
        return SimpleNamespace(
            breaking=[],
            source_breaks=[],
            risk=[],
            compatible=[],
            verdict=_Verdict("NO_CHANGE"),
        )

    monkeypatch.setattr("abicheck.service.resolve_input", fake_resolve_input)
    monkeypatch.setattr("abicheck.service.compare_snapshots", fake_compare_snapshots)
    monkeypatch.setattr(
        "abicheck.cli_buildsource.prepare_embedded_build_source",
        fake_prepare_embedded_build_source,
    )

    verdict, exit_code, summary = _run_baseline_compare(
        Path("old.so"), Path("new.so"), new_snap, [], "c++", "graph-full", [], [], [], []
    )

    assert verdict == "NO_CHANGE"
    assert exit_code == 0
    assert summary == {"breaking": 0, "api_break": 0, "risk": 0, "compatible": 0}
