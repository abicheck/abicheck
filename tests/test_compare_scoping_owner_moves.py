"""Unit coverage for helpers moved out of ``cli_helpers_compare``.

These pin the branches the CLI-level tests reach only incidentally: the
redundant-change merge, and the optional provenance fields of a consumer
summary (present only when the consumer carried that provenance).
"""

from __future__ import annotations

from types import SimpleNamespace

from abicheck.workflows.compare_finalize import _merge_redundant_changes
from abicheck.workflows.consumer_scope_gate import _app_compat_summary


def _change(name: str, caused_count: int = 0, caused_by_type: str | None = None):
    return SimpleNamespace(
        name=name, caused_count=caused_count, caused_by_type=caused_by_type
    )


def test_merge_appends_redundant_changes_and_clears_their_cause() -> None:
    kept = [_change("a", caused_count=3)]
    redundant = [_change("b", caused_by_type="root"), _change("c", caused_by_type="x")]
    result = SimpleNamespace(
        changes=list(kept),
        redundant_changes=list(redundant),
        redundant_count=2,
    )

    _merge_redundant_changes(result)

    assert [c.name for c in result.changes] == ["a", "b", "c"]
    assert result.redundant_changes == []
    assert result.redundant_count == 0
    # Kept changes lose their caused_count; redundant ones lose their cause.
    assert kept[0].caused_count == 0
    assert all(c.caused_by_type is None for c in redundant)


def test_merge_with_nothing_redundant_only_resets_counts() -> None:
    result = SimpleNamespace(
        changes=[_change("only", caused_count=5)],
        redundant_changes=[],
        redundant_count=0,
    )

    _merge_redundant_changes(result)

    assert [c.name for c in result.changes] == ["only"]
    assert result.changes[0].caused_count == 0


def _summary_source(**extra: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "app_path": "/opt/app",
        "verdict": SimpleNamespace(value="COMPATIBLE"),
        "required_symbol_count": 4,
        "missing_symbols": ["sym_a"],
        "missing_versions": [],
        "breaking_for_app": ["x", "y"],
        "symbol_coverage": 87.456,
    }
    base.update(extra)
    return SimpleNamespace(**base)


def test_summary_omits_provenance_for_a_bare_consumer() -> None:
    summary = _app_compat_summary(
        _summary_source(
            platform=None,
            profile=None,
            provider_baseline=None,
            digest=None,
            requirement="required",
            unreadable=False,
        )
    )

    assert summary == {
        "app": "/opt/app",
        "verdict": "COMPATIBLE",
        "required_symbol_count": 4,
        "missing_symbols": ["sym_a"],
        "missing_versions": [],
        "relevant_change_count": 2,
        "symbol_coverage": 87.5,
    }


def test_summary_carries_provenance_and_unreadable_reason_when_present() -> None:
    summary = _app_compat_summary(
        _summary_source(
            platform="linux-x86_64",
            profile="sdk",
            provider_baseline="base-1",
            digest="sha256:abc",
            requirement="advisory",
            unreadable=True,
            unreadable_reason="permission denied",
        )
    )

    assert summary["platform"] == "linux-x86_64"
    assert summary["profile"] == "sdk"
    assert summary["provider_baseline"] == "base-1"
    assert summary["digest"] == "sha256:abc"
    assert summary["requirement"] == "advisory"
    assert summary["unreadable"] is True
    assert summary["unreadable_reason"] == "permission denied"
