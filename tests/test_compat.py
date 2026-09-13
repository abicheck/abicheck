"""Tests for Sprint 5: ABICC compat layer (descriptor parser + HTML report)."""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest

from abicheck.compat import CompatDescriptor, parse_descriptor
from abicheck.html_report import generate_html_report

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_xml(tmp_path: Path, content: str, name: str = "desc.xml") -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(content).strip(), encoding="utf-8")
    return p


def _make_fake_result(verdict: str = "COMPATIBLE", breaking: int = 0) -> object:
    """Minimal stand-in for CompareResult (avoids importing checker)."""
    from types import SimpleNamespace

    summary = {
        "breaking": breaking,
        "compatible_additions": 0,
        "total_changes": breaking,
        "source_breaks": 0,
    }
    changes = []
    v = SimpleNamespace(value=verdict)
    return SimpleNamespace(
        verdict=v,
        summary=summary,
        changes=changes,
        suppressed_count=0,
        suppression_file_provided=False,
    )


# ---------------------------------------------------------------------------
# Descriptor parsing
# ---------------------------------------------------------------------------


def test_parse_descriptor_basic(tmp_path: Path) -> None:
    xml = """
    <descriptor>
      <version>2025.3</version>
      <headers>/usr/include/mylib</headers>
      <libs>/usr/lib/libmylib.so</libs>
    </descriptor>
    """
    desc_path = _write_xml(tmp_path, xml)
    desc = parse_descriptor(desc_path)

    assert isinstance(desc, CompatDescriptor)
    assert desc.version == "2025.3"
    assert len(desc.libs) == 1
    assert re.fullmatch(r"([A-Za-z]:)?/usr/lib/libmylib\.so", desc.libs[0].as_posix())
    assert len(desc.headers) == 1
    assert re.fullmatch(r"([A-Za-z]:)?/usr/include/mylib", desc.headers[0].as_posix())


def test_parse_descriptor_multiple_headers(tmp_path: Path) -> None:
    xml = """
    <descriptor>
      <version>1.0</version>
      <headers>/usr/include/foo</headers>
      <headers>/usr/include/foo/detail</headers>
      <libs>/usr/lib/libfoo.so</libs>
    </descriptor>
    """
    desc = parse_descriptor(_write_xml(tmp_path, xml))
    assert len(desc.headers) == 2
    assert any(
        re.fullmatch(r"([A-Za-z]:)?/usr/include/foo/detail", h.as_posix())
        for h in desc.headers
    )


def test_parse_descriptor_multiple_libs(tmp_path: Path) -> None:
    xml = """
    <descriptor>
      <version>3.0</version>
      <headers>/inc</headers>
      <libs>/lib/liba.so</libs>
      <libs>/lib/libb.so</libs>
    </descriptor>
    """
    desc = parse_descriptor(_write_xml(tmp_path, xml))
    assert len(desc.libs) == 2


def test_parse_descriptor_no_headers_ok(tmp_path: Path) -> None:
    """Headers are optional — not all ABICC descriptors include them."""
    xml = """
    <descriptor>
      <version>1.0</version>
      <libs>/usr/lib/libx.so</libs>
    </descriptor>
    """
    desc = parse_descriptor(_write_xml(tmp_path, xml))
    assert desc.version == "1.0"
    assert desc.headers == []


def test_parse_descriptor_missing_libs_raises(tmp_path: Path) -> None:
    xml = """
    <descriptor>
      <version>1.0</version>
      <headers>/usr/include/mylib</headers>
    </descriptor>
    """
    with pytest.raises(ValueError, match="missing <libs>"):
        parse_descriptor(_write_xml(tmp_path, xml))


def test_parse_descriptor_missing_version_raises(tmp_path: Path) -> None:
    xml = """
    <descriptor>
      <headers>/usr/include/mylib</headers>
      <libs>/usr/lib/libx.so</libs>
    </descriptor>
    """
    with pytest.raises(ValueError, match="missing <version>"):
        parse_descriptor(_write_xml(tmp_path, xml))


def test_parse_descriptor_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        parse_descriptor(tmp_path / "nonexistent.xml")


def test_parse_descriptor_relative_lib_resolved(tmp_path: Path) -> None:
    """Relative <libs> paths are resolved relative to the descriptor file."""
    xml = """
    <descriptor>
      <version>0.1</version>
      <libs>mylib.so</libs>
    </descriptor>
    """
    desc = parse_descriptor(_write_xml(tmp_path, xml))
    # Must be absolute and relative to descriptor's directory
    assert desc.libs[0].is_absolute()
    assert desc.libs[0].parent == tmp_path.resolve()


def test_parse_descriptor_accepts_an_abicc_fragment(tmp_path: Path) -> None:
    """A rootless ABICC descriptor *fragment* parses.

    This test previously asserted the opposite -- that a fragment was
    rejected with an XML error -- pinning a documented limitation rather
    than a desired behaviour. The limitation mattered: ABICC's real
    descriptors are fragments (sibling ``<version>``/``<headers>``/``<libs>``
    with no wrapper root), so ``abicheck compat``, whose whole purpose is to
    be a drop-in replacement for ABICC, could not read the descriptors of
    the tool it replaces. Intel MKL's own CI descriptors hit exit 6 on this.
    """
    xml = """
    <version>1.0</version>
    <libs>/usr/lib/libx.so</libs>
    """
    d = tmp_path / "frag.xml"
    d.write_text(xml, encoding="utf-8")

    desc = parse_descriptor(d)
    assert desc.version == "1.0"
    assert [str(p) for p in desc.libs] == ["/usr/lib/libx.so"]


def test_parse_descriptor_fragment_with_xml_declaration(tmp_path: Path) -> None:
    """A fragment carrying an ``<?xml?>`` declaration parses too.

    A declaration is only legal at the very start of a document, so it
    cannot survive being wrapped in a synthetic root -- a fragment file that
    carries one (generators emit them even for fragments) would otherwise
    fail the retry for a reason unrelated to why the first parse failed.
    """
    d = tmp_path / "frag_decl.xml"
    d.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<version>2.0</version>\n<libs>/usr/lib/liby.so</libs>\n",
        encoding="utf-8",
    )

    desc = parse_descriptor(d)
    assert desc.version == "2.0"


def test_parse_descriptor_still_rejects_genuinely_malformed_xml(
    tmp_path: Path,
) -> None:
    """The fragment retry is an accommodation, not a blanket "try harder".

    A file that is malformed for a reason wrapping cannot explain away -- an
    unclosed tag -- must still be rejected, and with the *document*-level
    error, not one naming a synthetic line number the user's file does not
    have.
    """
    d = tmp_path / "bad.xml"
    d.write_text("<version>1.0</version><libs>/usr/lib/libx.so", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid XML"):
        parse_descriptor(d)


def test_parse_descriptor_invalid_xml_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.xml"
    bad.write_text("<unclosed>", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid XML"):
        parse_descriptor(bad)


# ---------------------------------------------------------------------------
# HTML report generation
# ---------------------------------------------------------------------------


def test_html_report_contains_verdict() -> None:
    result = _make_fake_result(verdict="BREAKING", breaking=3)
    html = generate_html_report(
        result, lib_name="libtest", old_version="1.0", new_version="2.0"
    )
    assert "BREAKING" in html
    assert "libtest" in html


def test_html_report_compatible_verdict() -> None:
    result = _make_fake_result(verdict="COMPATIBLE")
    html = generate_html_report(result)
    assert "COMPATIBLE" in html


def test_html_report_bc_percent_shown() -> None:
    result = _make_fake_result(verdict="COMPATIBLE")
    html = generate_html_report(result)
    assert "Binary Compatibility" in html
    assert "100.0%" in html


def test_html_report_bc_percent_breaking() -> None:
    result = _make_fake_result(verdict="BREAKING", breaking=2)
    html = generate_html_report(
        result, lib_name="lib", old_version="1", new_version="2"
    )
    assert "0.0%" in html


def test_html_report_is_valid_html() -> None:
    result = _make_fake_result(verdict="NO_CHANGE")
    html_out = generate_html_report(result)
    assert html_out.startswith("<!DOCTYPE html>")
    assert "</html>" in html_out


def test_html_report_versions_in_title(tmp_path: Path) -> None:
    result = _make_fake_result(verdict="COMPATIBLE")
    html_out = generate_html_report(
        result, lib_name="libfoo", old_version="2025.0", new_version="2025.3"
    )
    assert "2025.0" in html_out
    assert "2025.3" in html_out
    assert "libfoo" in html_out


def test_html_report_xss_escape() -> None:
    """Library name with HTML special chars must be escaped."""
    result = _make_fake_result()
    html_out = generate_html_report(result, lib_name="<script>alert(1)</script>")
    assert "<script>" not in html_out
    assert "&lt;script&gt;" in html_out


def test_write_html_report_creates_dirs(tmp_path: Path) -> None:
    from abicheck.html_report import write_html_report

    result = _make_fake_result()
    out = tmp_path / "deep" / "nested" / "report.html"
    write_html_report(result, out)
    assert out.exists()
    assert out.stat().st_size > 100


def test_parse_descriptor_first_lib_used_warning(tmp_path: Path) -> None:
    """When descriptor has multiple <libs>, parse still succeeds with both libs captured."""
    xml = """
    <descriptor>
      <version>2.0</version>
      <libs>/lib/liba.so</libs>
      <libs>/lib/libb.so</libs>
    </descriptor>
    """
    desc = parse_descriptor(_write_xml(tmp_path, xml))
    # Both are captured — CLI is responsible for emitting the warning
    assert len(desc.libs) == 2
    assert re.fullmatch(r"([A-Za-z]:)?/lib/liba\.so", desc.libs[0].as_posix())


class TestDescriptorSkipAndCompileElements:
    """``<skip_*>`` / ``<include_paths>`` / ``<defines>`` / ``<gcc_options>``.

    ``parse_descriptor`` read three elements and dropped every other child
    silently. A descriptor could therefore declare 16 exclusions, have none
    of them applied, and still produce a confident verdict computed over the
    surface it had asked to narrow -- and a descriptor that fully specified
    how to compile its own headers still failed to compile them.
    """

    @staticmethod
    def _write(tmp_path: Path, body: str) -> Path:
        d = tmp_path / "desc.xml"
        d.write_text(
            f"<descriptor><version>1.0</version>"
            f"<libs>/usr/lib/libx.so</libs>{body}</descriptor>",
            encoding="utf-8",
        )
        return d

    def test_skip_elements_are_parsed(self, tmp_path: Path) -> None:
        d = self._write(
            tmp_path,
            "<skip_headers>internal.h</skip_headers>"
            "<skip_namespaces>detail</skip_namespaces>"
            "<skip_constants>MKL_PRIVATE</skip_constants>"
            "<skip_symbols>_mkl_internal</skip_symbols>"
            "<skip_types>InternalT</skip_types>",
        )
        desc = parse_descriptor(d)
        assert desc.skip_headers == ["internal.h"]
        assert desc.skip_namespaces == ["detail"]
        assert desc.skip_constants == ["MKL_PRIVATE"]
        assert desc.skip_symbols == ["_mkl_internal"]
        assert desc.skip_types == ["InternalT"]

    def test_whitespace_separated_and_repeated_elements_agree(
        self, tmp_path: Path
    ) -> None:
        """ABICC descriptors spell a list either way, and real ones mix the
        two. Both must reach the same parsed list, or a descriptor's rules
        apply or not depending on how it happened to be formatted."""
        block = self._write(
            tmp_path, "<skip_headers>\n  a.h\n  b.h\n  c.h\n</skip_headers>"
        )
        parsed_block = parse_descriptor(block).skip_headers

        repeated = tmp_path / "repeated.xml"
        repeated.write_text(
            "<descriptor><version>1.0</version><libs>/usr/lib/libx.so</libs>"
            "<skip_headers>a.h</skip_headers><skip_headers>b.h</skip_headers>"
            "<skip_headers>c.h</skip_headers></descriptor>",
            encoding="utf-8",
        )
        assert (
            parsed_block
            == parse_descriptor(repeated).skip_headers
            == [
                "a.h",
                "b.h",
                "c.h",
            ]
        )

    def test_skip_including_folds_into_skip_headers(self, tmp_path: Path) -> None:
        d = self._write(
            tmp_path,
            "<skip_headers>a.h</skip_headers><skip_including>b.h</skip_including>",
        )
        assert parse_descriptor(d).skip_headers == ["a.h", "b.h"]

    def test_compile_elements_are_parsed(self, tmp_path: Path) -> None:
        d = self._write(
            tmp_path,
            "<include_paths>/opt/inc</include_paths>"
            "<add_include_paths>/opt/inc2</add_include_paths>"
            "<defines>MKL_ILP64</defines>"
            "<gcc_options>-std=c++17</gcc_options>",
        )
        desc = parse_descriptor(d)
        assert [str(p) for p in desc.include_paths] == ["/opt/inc", "/opt/inc2"]
        assert desc.defines == ["MKL_ILP64"]
        assert desc.gcc_options == ["-std=c++17"]

    def test_compile_elements_become_compiler_flags(self, tmp_path: Path) -> None:
        from abicheck.compat.cli import _descriptor_compile_options

        d = self._write(
            tmp_path,
            "<include_paths>/opt/inc</include_paths>"
            "<defines>MKL_ILP64</defines>"
            "<defines>-DALREADY_PREFIXED</defines>"
            "<gcc_options>-std=c++17</gcc_options>",
        )
        opts = _descriptor_compile_options(parse_descriptor(d))
        assert "-I/opt/inc" in opts
        assert "-DMKL_ILP64" in opts
        # A define already written with its flag must not become `-D-DX`.
        assert "-DALREADY_PREFIXED" in opts
        assert "-D-D" not in opts
        assert "-std=c++17" in opts

    def test_unread_elements_warn_rather_than_vanish(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The whole class of defect, stated directly: an element this parser
        does not act on must say so. A rule that silently does nothing is
        worse than a rejected one, because the run still reports a confident
        verdict over a surface the descriptor said to narrow."""
        d = self._write(
            tmp_path, "<skip_libs>libinternal.so</skip_libs><nonsense>x</nonsense>"
        )
        with caplog.at_level("WARNING"):
            parse_descriptor(d)
        messages = " ".join(r.getMessage() for r in caplog.records)
        assert "skip_libs" in messages
        assert "nonsense" in messages

    def test_supported_elements_do_not_warn(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Vacuity guard on the warning above: it must not fire for the
        elements that *are* applied, or the signal is noise and every real
        descriptor trains the reader to ignore it."""
        d = self._write(
            tmp_path,
            "<headers>/usr/include/x</headers>"
            "<skip_headers>a.h</skip_headers>"
            "<skip_namespaces>detail</skip_namespaces>"
            "<skip_constants>C</skip_constants>"
            "<skip_symbols>s</skip_symbols>"
            "<skip_types>T</skip_types>"
            "<include_paths>/opt/inc</include_paths>"
            "<add_include_paths>/opt/inc2</add_include_paths>"
            "<skip_including>b.h</skip_including>"
            "<defines>D</defines>"
            "<gcc_options>-O2</gcc_options>",
        )
        with caplog.at_level("WARNING"):
            parse_descriptor(d)
        assert [r for r in caplog.records if "element" in r.getMessage()] == []


class TestDescriptorSuppression:
    """The ``<skip_*>`` rules, once parsed, actually suppress."""

    def test_namespace_symbol_type_and_constant_rules_are_built(self) -> None:
        from abicheck.compat.descriptor_expansion import build_descriptor_suppression
        from abicheck.compat.descriptor import CompatDescriptor

        desc = CompatDescriptor(
            version="1",
            headers=[],
            libs=[],
            skip_namespaces=["detail"],
            skip_symbols=["_internal"],
            skip_types=["InternalT"],
            skip_constants=["MKL_PRIVATE"],
        )
        sl = build_descriptor_suppression([desc])
        assert sl is not None
        reasons = [s.reason for s in sl._suppressions]
        assert any("skip_namespaces" in r for r in reasons if r)
        assert any("skip_symbols" in r for r in reasons if r)
        assert any("skip_types" in r for r in reasons if r)
        assert any("skip_constants" in r for r in reasons if r)

    def test_both_sides_union_without_duplicating(self) -> None:
        """Two sides declaring the same rule produce one rule, not two. A
        duplicated rule is not wrong, but it doubles every audit trail the
        disposition ledger renders for a release that declared its skips on
        both descriptors -- which is every release."""
        from abicheck.compat.descriptor_expansion import build_descriptor_suppression
        from abicheck.compat.descriptor import CompatDescriptor

        desc = CompatDescriptor(
            version="1", headers=[], libs=[], skip_namespaces=["detail"]
        )
        sl = build_descriptor_suppression([desc, desc])
        assert sl is not None
        assert len(sl._suppressions) == 1

    def test_no_rules_means_no_suppression_list(self) -> None:
        """A descriptor with no skips must not attach an empty suppression
        list: `suppression_file_provided`-style disclosure elsewhere treats
        "rules were supplied" as a fact about the run."""
        from abicheck.compat.descriptor_expansion import build_descriptor_suppression
        from abicheck.compat.descriptor import CompatDescriptor

        assert (
            build_descriptor_suppression(
                [CompatDescriptor(version="1", headers=[], libs=[])]
            )
            is None
        )

    def test_namespace_rule_does_not_leak_into_sibling_segments(self) -> None:
        """`detail` must not suppress `details` or `detail_public`.

        Found in review. The rule was generated as a `detail*` glob, on the
        assumption that a trailing wildcard was needed to reach nested
        namespaces. It is not -- the selector already has ancestor
        semantics -- and the wildcard actively suppressed real breaks in
        *unrelated public* namespaces that merely share a prefix, which the
        descriptor never named. Asserted against the sibling spellings
        directly, since the nesting test below passes either way and so
        could not catch this.
        """
        from abicheck.checker_policy import ChangeKind
        from abicheck.checker_types import Change
        from abicheck.compat.descriptor_expansion import build_descriptor_suppression
        from abicheck.compat.descriptor import CompatDescriptor

        sl = build_descriptor_suppression(
            [
                CompatDescriptor(
                    version="1", headers=[], libs=[], skip_namespaces=["detail"]
                )
            ]
        )
        assert sl is not None
        leaked = [
            sym
            for sym in ("details::gone", "detail_public::gone", "detailed::gone")
            if sl.is_suppressed(
                Change(kind=ChangeKind.FUNC_REMOVED, symbol=sym, description="")
            )
        ]
        assert not leaked, (
            "a <skip_namespaces>detail</skip_namespaces> rule suppressed "
            f"breaks in unrelated namespaces: {leaked}"
        )

    def test_namespace_rule_covers_nested_namespaces(self) -> None:
        """`detail` must cover `detail::impl::T`, or the rule only matches a
        namespace with no children -- which is not what anyone means by
        "skip this namespace"."""
        from abicheck.checker_policy import ChangeKind
        from abicheck.checker_types import Change
        from abicheck.compat.descriptor_expansion import build_descriptor_suppression
        from abicheck.compat.descriptor import CompatDescriptor

        sl = build_descriptor_suppression(
            [
                CompatDescriptor(
                    version="1", headers=[], libs=[], skip_namespaces=["detail"]
                )
            ]
        )
        assert sl is not None
        nested = Change(
            kind=ChangeKind.FUNC_REMOVED, symbol="detail::impl::gone", description=""
        )
        outside = Change(
            kind=ChangeKind.FUNC_REMOVED, symbol="public::gone", description=""
        )
        assert sl.is_suppressed(nested)
        assert not sl.is_suppressed(outside)
