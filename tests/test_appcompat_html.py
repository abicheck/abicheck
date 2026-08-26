"""Tests for appcompat HTML report generator."""
from __future__ import annotations

from types import SimpleNamespace

from abicheck.appcompat_html import appcompat_to_html
from abicheck.checker import Verdict


def _appcompat_result(
    verdict: Verdict = Verdict.COMPATIBLE,
    breaking: list | None = None,
    irrelevant: list | None = None,
    missing: list | None = None,
    missing_versions: list | None = None,
    with_metadata: bool = False,
) -> object:
    full_diff = SimpleNamespace(
        verdict=verdict,
        policy="strict_abi",
        old_metadata=None,
        new_metadata=None,
        confidence=None,
        evidence_tiers=[],
        coverage_warnings=[],
    )
    if with_metadata:
        full_diff.old_metadata = SimpleNamespace(
            path="/old/lib.so", sha256="aa" * 32, size_bytes=4096
        )
        full_diff.new_metadata = SimpleNamespace(
            path="/new/lib.so", sha256="bb" * 32, size_bytes=8192
        )
        full_diff.confidence = SimpleNamespace(value="medium")
        full_diff.evidence_tiers = ["elf", "header"]

    return SimpleNamespace(
        app_path="/bin/myapp",
        old_lib_path="/old/lib.so",
        new_lib_path="/new/lib.so",
        verdict=verdict,
        symbol_coverage=95.0,
        required_symbol_count=20,
        missing_symbols=missing or [],
        missing_versions=missing_versions or [],
        breaking_for_app=breaking or [],
        irrelevant_for_app=irrelevant or [],
        full_diff=full_diff,
    )


def test_html_is_valid_document() -> None:
    out = appcompat_to_html(_appcompat_result())
    assert out.startswith("<!DOCTYPE html>")
    assert "</html>" in out


def test_html_contains_verdict() -> None:
    out = appcompat_to_html(_appcompat_result(Verdict.BREAKING))
    assert "BREAKING" in out


def test_html_contains_app_path() -> None:
    out = appcompat_to_html(_appcompat_result())
    assert "/bin/myapp" in out


def test_html_contains_library_paths() -> None:
    out = appcompat_to_html(_appcompat_result())
    assert "/old/lib.so" in out
    assert "/new/lib.so" in out


def test_html_shows_symbol_coverage() -> None:
    out = appcompat_to_html(_appcompat_result())
    assert "95%" in out
    assert "20 required symbols" in out


def test_html_shows_missing_symbols() -> None:
    out = appcompat_to_html(_appcompat_result(missing=["foo", "bar"]))
    assert "Missing Symbols" in out
    assert "foo" in out
    assert "bar" in out


def test_html_demangles_missing_symbols() -> None:
    """Codex review: missing_symbols is rendered via a bespoke table that
    bypasses _changes_table/_symbol_cell entirely, so it never demangled
    at all -- the most important missing linker symbols stayed raw even
    though every other symbol-bearing field in the report is demangled."""
    out = appcompat_to_html(_appcompat_result(missing=["_ZN3FooC1Ev"]))
    assert "Foo::Foo()" in out


def test_html_preserves_mangled_identity_for_missing_symbols() -> None:
    """Codex review, fresh evidence: _ZN3FooC1Ev (the complete-object
    constructor) and _ZN3FooC2Ev (the base-object constructor) are two
    ABI-distinct linker symbols that both demangle to the identical
    "Foo::Foo()" text. Plain text replacement made them read as duplicate,
    indistinguishable rows with the exact linker names gone entirely;
    each mangled name must survive as an <abbr> tooltip, mirroring
    html_report._symbol_cell's own contract."""
    out = appcompat_to_html(
        _appcompat_result(missing=["_ZN3FooC1Ev", "_ZN3FooC2Ev"])
    )
    assert out.count("Foo::Foo()") == 2
    assert '<abbr title="_ZN3FooC1Ev">Foo::Foo()</abbr>' in out
    assert '<abbr title="_ZN3FooC2Ev">Foo::Foo()</abbr>' in out


def test_html_shows_file_metadata() -> None:
    out = appcompat_to_html(_appcompat_result(with_metadata=True))
    assert "Library Files" in out
    assert "/old/lib.so" in out
    assert "4096" in out


def test_html_file_metadata_with_none_sha256() -> None:
    """sha256=None on metadata must not raise TypeError."""
    r = _appcompat_result(with_metadata=True)
    r.full_diff.old_metadata.sha256 = None
    r.full_diff.new_metadata.sha256 = None
    out = appcompat_to_html(r)
    assert "Library Files" in out
    assert "SHA-256" in out


def test_html_shows_confidence() -> None:
    out = appcompat_to_html(_appcompat_result(with_metadata=True))
    assert "Analysis Confidence" in out
    assert "MEDIUM" in out
    assert "elf" in out


def test_html_shows_no_relevant_changes() -> None:
    from enum import Enum

    class K(str, Enum):
        V = "func_added"

    change = SimpleNamespace(
        kind=K.V, symbol="new_func", description="added",
        old_value=None, new_value=None, source_location=None,
        affected_symbols=None, caused_by_type=None, caused_count=0,
        demangled_symbol="new_func",
    )
    out = appcompat_to_html(_appcompat_result(irrelevant=[change]))
    assert "No Relevant Changes" in out
    assert "Irrelevant Changes" in out


def test_html_confidence_absent_without_metadata() -> None:
    """When confidence is None the Analysis Confidence section is omitted."""
    out = appcompat_to_html(_appcompat_result(with_metadata=False))
    assert "Analysis Confidence" not in out


def test_html_shows_missing_versions() -> None:
    out = appcompat_to_html(_appcompat_result(missing_versions=["GLIBC_2.34", "GLIBC_2.38"]))
    assert "Missing Symbol Versions" in out
    assert "GLIBC_2.34" in out
    assert "GLIBC_2.38" in out


def test_html_shows_breaking_for_app() -> None:
    from enum import Enum

    class K(str, Enum):
        V = "func_removed"

    change = SimpleNamespace(
        kind=K.V, symbol="removed_func", description="Public function removed",
        old_value="removed_func", new_value=None, source_location=None,
        affected_symbols=None, caused_by_type=None, caused_count=0,
        demangled_symbol="removed_func",
    )
    out = appcompat_to_html(_appcompat_result(
        verdict=Verdict.BREAKING, breaking=[change],
    ))
    assert "Relevant Changes" in out
    assert "removed_func" in out


def test_html_escapes_xss_in_app_path() -> None:
    """Malicious app_path must be escaped in output."""
    r = _appcompat_result()
    r.app_path = "<script>alert(1)</script>"
    out = appcompat_to_html(r)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_html_escapes_xss_in_library_paths() -> None:
    """Library paths with HTML must be escaped."""
    r = _appcompat_result()
    r.old_lib_path = "<img src=x onerror=alert(1)>"
    out = appcompat_to_html(r)
    assert "<img " not in out
    assert "&lt;img " in out


def test_html_escapes_xss_in_missing_symbols() -> None:
    """Missing symbol names with HTML must be escaped."""
    out = appcompat_to_html(_appcompat_result(missing=["<b>evil</b>"]))
    assert "<b>evil</b>" not in out
    assert "&lt;b&gt;" in out


def test_prewarms_the_demangle_cache_before_rendering_rows(monkeypatch) -> None:
    """Codex review: appcompat_to_html() renders its Relevant/Irrelevant
    Changes tables via the shared _changes_table()/_symbol_cell() helpers
    (the same ones generate_html_report() uses), but -- unlike
    generate_html_report() -- never called prewarm_demangle_batch() first.
    Without it, each row's symbol/description demangles one at a time on a
    cache miss, paying a fresh c++filt subprocess per row instead of one
    batched call for the whole report. The first call to demangle_batch
    must already carry every symbol from both the breaking and irrelevant
    lists, proving the whole report was batched upfront."""
    import abicheck.demangle as demangle_mod
    from abicheck.checker import Change
    from abicheck.checker_policy import ChangeKind

    breaking = [
        Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_ZN3FooC1Ev",
            description="Function removed: _ZN3FooC1Ev",
        )
    ]
    irrelevant = [
        Change(
            kind=ChangeKind.FUNC_ADDED,
            symbol="_ZN3Bar3runEv",
            description="Function added: _ZN3Bar3runEv",
        )
    ]

    calls: list[list[str]] = []
    orig = demangle_mod.demangle_batch

    def spy(batch: list[str], **kw) -> dict[str, str]:
        calls.append(list(batch))
        return orig(batch, **kw)

    monkeypatch.setattr(demangle_mod, "demangle_batch", spy)
    appcompat_to_html(
        _appcompat_result(
            verdict=Verdict.BREAKING, breaking=breaking, irrelevant=irrelevant
        )
    )
    assert calls, "demangle_batch was never called"
    assert {"_ZN3FooC1Ev", "_ZN3Bar3runEv"} <= set(calls[0]), calls


def test_missing_symbols_prewarm_accepts_macho_prefix(monkeypatch) -> None:
    """CodeRabbit review, fresh evidence: the missing_symbols prewarm called
    demangle_batch() without accept_macho_prefix=True, unlike
    _missing_symbol_cell() (which resolves a Mach-O `__Z...` symbol via
    _abbr_symbol_text()). A report with many distinct missing Mach-O
    symbols would batch-warm none of them, falling back to one demangle()
    call per row instead of the single batched call this prewarm exists
    for."""
    import abicheck.appcompat_html as appcompat_html_mod
    import abicheck.demangle as demangle_mod

    calls: list[dict] = []
    orig = demangle_mod.demangle_batch

    def spy(batch: list[str], **kw) -> dict[str, str]:
        calls.append(kw)
        return orig(batch, **kw)

    # appcompat_html.py does `from .demangle import demangle_batch`, binding
    # its own module-local name -- patching abicheck.demangle.demangle_batch
    # alone would miss appcompat_html's own direct call.
    monkeypatch.setattr(appcompat_html_mod, "demangle_batch", spy)
    appcompat_to_html(_appcompat_result(missing=["__ZN3FooC1Ev"]))
    assert calls, "demangle_batch was never called"
    assert calls[-1].get("accept_macho_prefix") is True, calls


def test_demangle_false_keeps_missing_symbols_raw() -> None:
    """Codex review, fresh evidence: appcompat_to_html() had no equivalent
    to the CLI's --no-demangle -- it always demangled unconditionally."""
    out = appcompat_to_html(
        _appcompat_result(missing=["_ZN3FooC1Ev"]), demangle=False
    )
    assert "_ZN3FooC1Ev" in out
    assert "Foo::Foo()" not in out


def test_demangle_false_keeps_breaking_changes_raw() -> None:
    from abicheck.checker import Change
    from abicheck.checker_policy import ChangeKind

    change = Change(
        kind=ChangeKind.FUNC_REMOVED,
        symbol="_ZN3FooC1Ev",
        description="Function removed: _ZN3FooC1Ev",
    )
    out = appcompat_to_html(
        _appcompat_result(verdict=Verdict.BREAKING, breaking=[change]),
        demangle=False,
    )
    assert "_ZN3FooC1Ev" in out
    assert "Foo::Foo()" not in out


def test_write_appcompat_html_passes_demangle_through(tmp_path) -> None:
    from abicheck.appcompat_html import write_appcompat_html

    out = tmp_path / "report.html"
    write_appcompat_html(
        _appcompat_result(missing=["_ZN3FooC1Ev"]), out, demangle=False
    )
    content = out.read_text()
    assert "_ZN3FooC1Ev" in content
    assert "Foo::Foo()" not in content
