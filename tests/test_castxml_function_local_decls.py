"""castxml's function-local declarations never reach the snapshot.

castxml emits a declaration local to a function body whenever an emitted
signature refers to it -- a deduced ``auto`` return type that is a local
alias, enum or class. The parser used to group it like any namespace-scope
declaration, which (a) gave it a namespace-level identity (``q::V`` for
``q::mk()::V``) that collides with a real ``q::V``, and (b) for a typedef,
made the typedef identity sidecar and ``SemanticIR`` disagree, so the whole
dump was refused. libstdc++'s C++20 ``<string>`` triggers (b) for any header
that uses ``std::string``'s ``operator<=>``.

The invariant: a declaration whose ``context`` chain reaches a function is
not grouped, at any nesting depth, and nothing else changes. The oracle for
the real-tool test is the clang backend, which never emits them.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from abicheck.extract.headers.castxml.context import FUNCTION_TAGS, CastxmlParserContext

_GROUPED_TAGS = (
    "Struct",
    "Class",
    "Union",
    "Enumeration",
    "Typedef",
    "Variable",
    *FUNCTION_TAGS,
)


def _context(xml: str) -> CastxmlParserContext:
    ctx = CastxmlParserContext(ET.fromstring(xml), set(), set())
    ctx.build_id_map()
    return ctx


def _grouped_ids(ctx: CastxmlParserContext) -> set[str]:
    lists = (
        ctx.function_els,
        ctx.variable_els,
        ctx.record_els,
        ctx.enum_els,
        ctx.typedef_els,
    )
    return {el.get("id", "") for group in lists for el in group}


@pytest.mark.parametrize("function_tag", FUNCTION_TAGS)
@pytest.mark.parametrize("local_tag", _GROUPED_TAGS)
def test_every_local_kind_under_every_function_kind_is_excluded(
    function_tag: str, local_tag: str
) -> None:
    """Exhaustive over (enclosing function kind) x (local declaration kind),
    including a member nested one level inside a local record."""
    ctx = _context(
        f"""<CastXML>
          <Namespace id="_1" name="::"/>
          <Namespace id="_2" name="q" context="_1"/>
          <Struct id="_3" name="Outer" context="_2"/>
          <{function_tag} id="_4" name="f" context="_3"/>
          <Struct id="_5" name="Local" context="_4"/>
          <{local_tag} id="_6" name="x" context="_4"/>
          <{local_tag} id="_7" name="y" context="_5"/>
          <{local_tag} id="_8" name="z" context="_2"/>
        </CastXML>"""
    )
    grouped = _grouped_ids(ctx)
    assert {"_5", "_6", "_7"}.isdisjoint(grouped)
    assert {"_3", "_4", "_8"} <= grouped
    # Still resolvable, so a signature naming a local type renders.
    assert all(ctx.resolve(i) is not None for i in ("_5", "_6", "_7"))


def test_order_of_elements_does_not_matter() -> None:
    """castxml ids can point forward; the answer cannot depend on order."""
    forward = """<CastXML>
      <Typedef id="_9" name="L" context="_4"/>
      <Function id="_4" name="g" context="_2"/>
      <Namespace id="_2" name="q" context="_1"/>
      <Namespace id="_1" name="::"/>
    </CastXML>"""
    assert _grouped_ids(_context(forward)) == {"_4"}


def test_context_cycle_is_not_local_and_terminates() -> None:
    ctx = _context(
        """<CastXML>
          <Struct id="_1" name="A" context="_2"/>
          <Struct id="_2" name="B" context="_1"/>
        </CastXML>"""
    )
    assert _grouped_ids(ctx) == {"_1", "_2"}


# ── Real tools: castxml must agree with the clang backend ─────────────────

_LOCAL_SHAPES = """
#pragma once
#include <string>
#include <compare>
namespace q {
struct R { int real; };  // a real namespace-scope record
inline auto mk() { struct V { int x; }; return V{}; }
inline auto mk2() { enum class K { a, b }; return K::a; }
inline auto mk3() { using T = long; return T{}; }
template <class T> constexpr auto g() {
  if constexpr (requires { typename T::type; }) { using L = typename T::type; return L{}; }
  else { return 0; }
}
struct W { using type = int; };
inline int h() { return g<W>(); }
inline auto cmp(const std::string& a, const std::string& b) { return a <=> b; }
int use();
}
"""


def _tools_available() -> bool:
    return all(shutil.which(t) for t in ("castxml", "clang++", "g++"))


@pytest.mark.integration
@pytest.mark.skipif(not _tools_available(), reason="needs castxml, clang++ and g++")
@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="builds and dumps an ELF shared library",
)
def test_castxml_and_clang_backends_agree_on_local_declarations(tmp_path: Path) -> None:
    from abicheck.serialization import load_snapshot

    header = tmp_path / "api.h"
    header.write_text(_LOCAL_SHAPES)
    src = tmp_path / "a.cpp"
    src.write_text(
        '#include "api.h"\nint q::use(){ return q::mk().x + (int)q::mk2() + (int)q::mk3() + q::h(); }\n'
    )
    lib = tmp_path / "libq.so"
    subprocess.run(
        [
            "g++",
            "-std=c++20",
            "-shared",
            "-fPIC",
            f"-I{tmp_path}",
            str(src),
            "-o",
            str(lib),
        ],
        check=True,
    )
    surfaces = {}
    for frontend in ("castxml", "clang"):
        config = tmp_path / f"{frontend}.yml"
        config.write_text(f"compile:\n  std: c++20\n  frontend: {frontend}\n")
        out = tmp_path / f"{frontend}.json"
        result = subprocess.run(
            [
                "abicheck",
                "dump",
                str(lib),
                "-H",
                str(header),
                "--config",
                str(config),
                "-o",
                str(out),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr[-2000:]
        snap = load_snapshot(out)
        surfaces[frontend] = {
            "types": sorted(t.name for t in snap.types if t.name in ("V", "R")),
            "enums": sorted(e.name for e in snap.enums if e.name == "K"),
            "typedefs": sorted(n for n in snap.typedefs if n in ("T", "L")),
            "q_functions": sorted(
                f.name
                for f in snap.functions
                if f.name in ("mk", "mk2", "mk3", "h", "cmp", "use", "V", "~V")
            ),
        }
        # A local record's own members (castxml emits its implicit special
        # members) must not leak either.
        surfaces[frontend]["local_members"] = sorted(
            f.name for f in snap.functions if f.name in ("V", "~V")
        )
    assert surfaces["castxml"] == surfaces["clang"]
    # The namespace-scope record is real and must survive the exclusion.
    assert surfaces["castxml"]["types"] == ["R"]
    assert surfaces["castxml"]["local_members"] == []
