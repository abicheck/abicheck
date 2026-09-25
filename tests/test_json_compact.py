"""``storage.json_compact``: compact, ASCII-only re-encoding of a JSON document.

Oracle: ``json.loads`` of the output equals ``json.loads`` of the input -- the
stdlib decoder, not anything the implementation shares. Inputs are generated
documents pretty-printed at several indents (clang's shape) with adversarial
string content (whitespace, ``": "``, quotes, backslashes, non-ASCII and
astral characters), streamed through chunk sizes small enough that every
chunk boundary lands somewhere different.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.storage.json_compact import (
    CompactedAst,
    compact_json_stream,
    compacted_ast,
)

_text = st.text(
    alphabet=st.sampled_from(list(' \t"\\:,{}[]\n\u00e9\u2014\U0001f600ab \u00a0')),
    max_size=12,
)
_scalars = (
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | _text
)
_values = st.recursive(
    _scalars,
    lambda inner: (
        st.lists(inner, max_size=4) | st.dictionaries(_text, inner, max_size=4)
    ),
    max_leaves=25,
)


def _compact(doc: bytes, chunk_size: int) -> bytes:
    out = io.BytesIO()
    written = compact_json_stream(io.BytesIO(doc), out, chunk_size=chunk_size)
    assert written == len(out.getvalue())
    return out.getvalue()


@settings(max_examples=400, deadline=None)
@given(
    value=_values,
    indent=st.sampled_from([None, 0, 1, 2, 4, "\t"]),
    chunk_size=st.integers(min_value=1, max_value=64),
)
def test_compaction_preserves_the_value_and_is_ascii(value, indent, chunk_size):
    doc = json.dumps(value, indent=indent, ensure_ascii=False).encode("utf-8")
    out = _compact(doc, chunk_size)
    assert json.loads(out) == json.loads(doc)
    assert out.isascii()
    assert b"\n" not in out
    assert len(out) <= len(json.dumps(value, indent=indent).encode())


@given(value=_values, chunk_size=st.integers(min_value=1, max_value=64))
def test_chunking_never_changes_the_output(value, chunk_size):
    doc = json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8")
    assert _compact(doc, chunk_size) == _compact(doc, 1 << 20)


def test_non_bmp_character_becomes_a_surrogate_pair():
    doc = json.dumps({"k": "a\U0001f600\u2014"}, ensure_ascii=False, indent=2).encode()
    out = _compact(doc, 4)
    assert b"\\ud83d\\ude00" in out and b"\\u2014" in out
    assert json.loads(out) == {"k": "a\U0001f600\u2014"}


def test_one_non_ascii_char_no_longer_widens_the_decoded_string():
    """The measured failure: one em dash made the whole document 2 B/char."""
    import sys

    doc = json.dumps({"a": "x" * 50_000, "b": "\u2014"}, indent=2, ensure_ascii=False)
    out = _compact(doc.encode(), 1024).decode("ascii")
    assert sys.getsizeof(out) < sys.getsizeof(doc) * 0.6


def test_compacted_ast_publishes_by_rename(tmp_path):
    src = tmp_path / "ast.json"
    src.write_text(json.dumps({"k": [1, 2]}, indent=2))
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    with compacted_ast(src, cache_dir) as doc:
        assert doc.path != src and doc.path.parent == cache_dir
        doc.publish(cache_dir / "entry.json")
    assert (cache_dir / "entry.json").read_bytes() == b'{"k": [1,2]}'
    assert sorted(p.name for p in cache_dir.iterdir()) == ["entry.json"]
    assert src.exists()


def test_unpublished_copy_is_removed(tmp_path):
    src = tmp_path / "ast.json"
    src.write_text('{\n  "k": 1\n}')
    with compacted_ast(src, None) as doc:
        compact = doc.path
        assert compact.read_bytes() == b'{"k": 1}'
    assert not compact.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ast.json"]


def test_compaction_failure_degrades_to_the_original(tmp_path):
    src = tmp_path / "ast.json"
    src.write_bytes(b'{\n"k": "\xff"\n}')  # not UTF-8
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    with compacted_ast(src, cache_dir) as doc:
        assert doc.path == src
        doc.publish(cache_dir / "entry.json")
    assert (cache_dir / "entry.json").read_bytes() == src.read_bytes()
    assert sorted(p.name for p in cache_dir.iterdir()) == ["entry.json"]


def test_publish_across_directories_copies(tmp_path):
    src = tmp_path / "ast.json"
    src.write_text('{"k": 1}')
    other = tmp_path / "other"
    other.mkdir()
    CompactedAst(src, None).publish(other / "e.json")
    assert (other / "e.json").read_text() == '{"k": 1}'


@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not found")
def test_header_graph_projection_agrees_on_compact_and_pretty_ast(tmp_path):
    """The streaming projection reads the cache entry; it must not care which form."""
    from abicheck.buildsource.header_graph_ast_stream import (
        project_header_graph_ast_file,
    )

    header = tmp_path / "h.hpp"
    header.write_text(
        "namespace n { struct A { int x; }; struct B : A { A a; };\n"
        "B make(const A&); /* \u2014 */ int use(B b) { return make(b).x; } }\n"
    )
    pretty = tmp_path / "pretty.json"
    with pretty.open("wb") as fh:
        subprocess.run(
            [
                "clang",
                "-x",
                "c++",
                "-fsyntax-only",
                "-Xclang",
                "-ast-dump=json",
                str(header),
            ],
            stdout=fh,
            check=True,
        )
    compact = tmp_path / "compact.json"
    with pretty.open("rb") as src, compact.open("wb") as dst:
        compact_json_stream(src, dst, chunk_size=4096)
    assert compact.stat().st_size < pretty.stat().st_size * 0.5
    assert project_header_graph_ast_file(compact) == project_header_graph_ast_file(
        pretty
    )


def test_module_does_not_depend_on_line_layout_beyond_rfc():
    """A raw line feed inside a string is invalid JSON; the stdlib agrees."""
    with pytest.raises(json.JSONDecodeError):
        json.loads(b'"a\nb"')


def test_failed_cross_directory_publish_removes_its_temp_and_raises(
    tmp_path, monkeypatch
):
    import abicheck.storage.json_compact as jc

    src = tmp_path / "ast.json"
    src.write_text('{"k": 1}')
    other = tmp_path / "other"
    other.mkdir()

    def _boom(_src, _dst):
        raise OSError("disk full")

    monkeypatch.setattr(jc.shutil, "copyfileobj", _boom)
    with pytest.raises(OSError, match="disk full"):
        CompactedAst(src, None).publish(other / "e.json")
    assert list(other.iterdir()) == []


class TestMigrateLegacyEntry:
    """On-read migration of pretty-printed cache entries (value-preserving, idempotent)."""

    @staticmethod
    def _docs():
        import random

        rng = random.Random(1234)
        docs = [
            {
                "kind": "TranslationUnitDecl",
                "inner": [{"name": "café — \U0001f600", "loc": {"line": 1}}],
            },
            [1, 2.5, None, True, "a\\nb", {"x": " spaced  value "}],
        ]
        for _ in range(40):

            def gen(depth):
                r = rng.random()
                if depth > 3 or r < 0.3:
                    return rng.choice(
                        [rng.randint(-9, 9), "sé" * rng.randint(0, 3), None, "  x \t"]
                    )
                if r < 0.65:
                    return [gen(depth + 1) for _ in range(rng.randint(0, 4))]
                return {f"k{i}—": gen(depth + 1) for i in range(rng.randint(0, 4))}

            docs.append(gen(0))
        return docs

    def test_pretty_entry_is_rewritten_equal_and_idempotent(self, tmp_path):
        import json

        from abicheck.storage.json_compact import migrate_legacy_entry

        for i, doc in enumerate(self._docs()):
            p = tmp_path / f"e{i}.json"
            p.write_text(
                json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            if b"\n" not in p.read_bytes():
                continue  # scalar-only document: already compact
            assert migrate_legacy_entry(p) is True
            raw = p.read_bytes()
            assert b"\n" not in raw and raw.isascii()
            assert json.loads(raw) == doc
            assert migrate_legacy_entry(p) is False
            assert p.read_bytes() == raw

    def test_compact_or_missing_entry_untouched(self, tmp_path):
        from abicheck.storage.json_compact import migrate_legacy_entry

        p = tmp_path / "c.json"
        p.write_bytes(b'{"a": [1,2]}')
        assert migrate_legacy_entry(p) is False
        assert p.read_bytes() == b'{"a": [1,2]}'
        assert migrate_legacy_entry(tmp_path / "missing.json") is False
        assert list(tmp_path.iterdir()) == [p]

    def test_load_cached_ast_migrates_on_read(self, tmp_path):
        import json

        from abicheck.dumper_cache import load_cached_ast

        doc = {"kind": "TranslationUnitDecl", "inner": [{"name": "—"}]}
        p = tmp_path / "ast.json"
        p.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        assert load_cached_ast("k", "clang", p, memoize=False) == doc
        assert b"\n" not in p.read_bytes() and p.read_bytes().isascii()
        assert load_cached_ast("k", "clang", p, memoize=False) == doc
