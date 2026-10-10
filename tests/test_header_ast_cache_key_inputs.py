"""The header-AST cache key covers what abicheck generates, not only its inputs.

Bug class ``cache.computed_output_keyed_without_code_identity``: the castxml/
clang header-AST cache keyed the headers, the toolchain and a hand-bumped
schema constant. abicheck *generates* part of what the frontend runs -- the
aggregate header, castxml's preamble, the command line -- and, for clang,
decides what is stored (compaction, DPC++ document selection, the failing-
header retry). A change there with no matching bump served an AST built from
different input.

* ``TestInvocationIsInTheKey`` -- every independently generated piece of the
  frontend's input changes the key, the key is independent of the per-run
  temporary paths, and a probe key (no ``invocation_tool``) is unaffected.
* ``TestClangOutputCode`` -- the code that shapes a stored clang entry is in
  the clang key and not in the castxml key; the module list names real modules.
* ``test_traced_clang_output_path_is_classified`` (integration) -- traces a
  real clang header dump and fails if any abicheck module runs between
  clang's exit and the cache write without being classified, so the list
  cannot silently go stale.
* ``TestRealDumpsMissOnGeneratedInputChange`` (integration) -- the real
  castxml/clang dump hits its own entry, and misses after the generated input
  or the output-shaping code changes.
* ``test_projection_sidecar_rejects_another_builds_projection`` -- the
  header-graph projection derived from a cached AST is abicheck output too.
"""

from __future__ import annotations

import shutil
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

import abicheck.extract.headers.ast_config as cfg
from abicheck.extract import header_ast_cache_producers as producers
from abicheck.storage.code_identity import PACKAGE_ROOT, module_source_path


def _key(
    tmp_path: Path, backend: str, *, tool: tuple[str, ...] | None = None, **kw
) -> str:
    header = tmp_path / "api.h"
    if not header.exists():
        header.write_text("struct S { int a; };\n")
    if tool is None:
        tool = (
            ("cc", "gnu", "castxml")
            if backend == "castxml"
            else ("clang", "gnu", "False", "False")
        )
    return cfg._cache_key(
        [header], [], "cc", backend=backend, invocation_tool=tool, force_cpp=False, **kw
    )


_CASTXML_MUTATIONS = {
    "command line": lambda mp: mp.setattr(
        cfg,
        "_build_castxml_command",
        _appending(cfg._build_castxml_command, "-DABICHECK_NEW_FLAG"),
    ),
    "preamble text": lambda mp: mp.setattr(
        cfg, "CASTXML_HEADER_PREAMBLE", cfg.CASTXML_HEADER_PREAMBLE + "/* changed */\n"
    ),
    "aggregate text": lambda mp: mp.setattr(
        cfg,
        "castxml_aggregate_text",
        lambda headers, pre: (
            "#define CHANGED 1\n" + "".join(f'#include "{h}"\n' for h in headers)
        ),
    ),
    "preamble file name": lambda mp: mp.setattr(
        cfg, "PREAMBLE_FILENAME", "renamed_preamble.hpp"
    ),
}
_CLANG_MUTATIONS = {
    "command line": lambda mp: mp.setattr(
        cfg,
        "_build_clang_header_command",
        _appending(cfg._build_clang_header_command, "-DABICHECK_NEW_FLAG"),
    ),
    "aggregate text": lambda mp: mp.setattr(
        cfg,
        "clang_aggregate_text",
        lambda headers: (
            "#define CHANGED 1\n" + "".join(f'#include "{h}"\n' for h in headers)
        ),
    ),
    "output-shaping code": lambda mp: mp.setattr(
        cfg, "clang_ast_output_fingerprint", lambda: "different-code"
    ),
}


def _appending(fn, extra: str):
    def wrapped(*a, **k):
        return [*fn(*a, **k), extra]

    return wrapped


class TestInvocationIsInTheKey:
    @pytest.mark.parametrize("mutation", sorted(_CASTXML_MUTATIONS))
    def test_castxml_generated_input_changes_key(
        self, tmp_path, monkeypatch, mutation
    ) -> None:
        before = _key(tmp_path, "castxml")
        _CASTXML_MUTATIONS[mutation](monkeypatch)
        assert _key(tmp_path, "castxml") != before

    @pytest.mark.parametrize("mutation", sorted(_CLANG_MUTATIONS))
    def test_clang_generated_input_changes_key(
        self, tmp_path, monkeypatch, mutation
    ) -> None:
        before = _key(tmp_path, "clang")
        _CLANG_MUTATIONS[mutation](monkeypatch)
        assert _key(tmp_path, "clang") != before

    @pytest.mark.parametrize(
        "field, values",
        [
            (
                "tool",
                [
                    ("cc", "gnu", "castxml"),
                    ("cc", "gnu", "/opt/castxml"),
                    ("g++", "gnu", "castxml"),
                    ("cc", "msvc", "castxml"),
                ],
            ),
        ],
    )
    def test_each_tool_component_is_keyed(self, tmp_path, field, values) -> None:
        keys = {_key(tmp_path, "castxml", tool=v) for v in values}
        assert len(keys) == len(values)

    def test_clang_dpcpp_mode_is_keyed(self, tmp_path) -> None:
        variants = [
            ("clang", "gnu", m, h) for m in ("False", "True") for h in ("False", "True")
        ]
        assert len({_key(tmp_path, "clang", tool=v) for v in variants}) == len(variants)

    @pytest.mark.parametrize("backend", ["castxml", "clang"])
    def test_key_is_independent_of_temporary_paths(
        self, tmp_path, monkeypatch, backend
    ) -> None:
        import tempfile

        first = _key(tmp_path, backend)
        other = tmp_path / "other-tmp"
        other.mkdir()
        monkeypatch.setattr(tempfile, "tempdir", str(other))
        assert _key(tmp_path, backend) == first == _key(tmp_path, backend)

    @pytest.mark.parametrize("backend", ["castxml", "clang"])
    def test_header_order_is_keyed(self, tmp_path, backend) -> None:
        # Order is part of the parse (an earlier header's macros and
        # declarations change how later ones parse); the key used to sort the
        # header paths and served one order's AST for the other.
        a, b = tmp_path / "a.h", tmp_path / "b.h"
        a.write_text("#define FROM_A 1\n")
        b.write_text("int f(void);\n")
        tool = (
            ("cc", "gnu", "castxml")
            if backend == "castxml"
            else ("clang", "gnu", "False", "False")
        )

        def key(headers):
            return cfg._cache_key(
                headers,
                [],
                "cc",
                backend=backend,
                invocation_tool=tool,
                force_cpp=False,
            )

        assert key([a, b]) != key([b, a])
        assert key([a, b]) == key([a, b])

    def test_probe_key_without_a_tool_is_unchanged_by_generation(
        self, tmp_path, monkeypatch
    ) -> None:
        before = _key(tmp_path, "castxml", tool=())
        for mutate in _CASTXML_MUTATIONS.values():
            mutate(monkeypatch)
        assert _key(tmp_path, "castxml", tool=()) == before


class TestClangOutputCode:
    def test_only_the_clang_key_carries_output_code(
        self, tmp_path, monkeypatch
    ) -> None:
        castxml, clang = _key(tmp_path, "castxml"), _key(tmp_path, "clang")
        monkeypatch.setattr(
            cfg, "clang_ast_output_fingerprint", lambda: "different-code"
        )
        assert _key(tmp_path, "castxml") == castxml
        assert _key(tmp_path, "clang") != clang

    def test_listed_modules_exist_and_lists_are_disjoint(self) -> None:
        listed = set(producers.CLANG_AST_OUTPUT_MODULES) | set(
            producers.NON_SHAPING_MODULES
        )
        assert not set(producers.CLANG_AST_OUTPUT_MODULES) & set(
            producers.NON_SHAPING_MODULES
        )
        missing = sorted(
            m for m in listed if module_source_path(PACKAGE_ROOT, m) is None
        )
        assert missing == []
        assert all(reason.strip() for reason in producers.NON_SHAPING_MODULES.values())

    def test_fingerprint_follows_a_listed_module_edit(self, tmp_path) -> None:
        from abicheck.storage.code_identity import compute_modules_fingerprint

        root = tmp_path / "abicheck"
        (root / "storage").mkdir(parents=True)
        (root / "storage" / "json_compact.py").write_text("x = 1\n")
        (root / "unrelated.py").write_text("y = 1\n")
        mods = ("abicheck.storage.json_compact", "abicheck.not_there")
        before = compute_modules_fingerprint(root, mods)
        (root / "unrelated.py").write_text("y = 2\n")
        assert compute_modules_fingerprint(root, mods) == before
        (root / "storage" / "json_compact.py").write_text("x = 2\n")
        assert compute_modules_fingerprint(root, mods) != before
        (root / "not_there.py").write_text(
            "z = 1\n"
        )  # a missing module appearing is a change too
        assert compute_modules_fingerprint(root, mods) != before


# -- real frontends -------------------------------------------------------


def _isolated_cache(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "xdg"))


def _recording_castxml_runner() -> tuple[list[list[str]], Any]:
    """The default castxml runner, recording every command it runs; injected
    with ``castxml_dump(..., run=...)`` rather than patched in."""
    from abicheck.extract.headers.castxml.probe import run_castxml

    runs: list[list[str]] = []

    def run(cmd: list[str], **kwargs: Any) -> Any:
        runs.append(list(cmd))
        return run_castxml(cmd, **kwargs)

    return runs, run


_PLAIN = {"api.hpp": "struct S { int a; virtual ~S(); };\nnamespace n { int f(S*); }\n"}
#: Two headers, one an internal header raising a direct-inclusion guard: the
#: failing-header retry drops it, rebuilds the input and writes the sidecar.
_RETRY = {
    "good.hpp": "struct S { int a; };\n",
    "internal.hpp": '#error "Do not #include this internal header directly"\n',
}


@pytest.mark.integration
@pytest.mark.parametrize(
    "scenario", [_PLAIN, _RETRY], ids=["plain", "failing-header-retry"]
)
def test_traced_clang_output_path_is_classified(
    tmp_path, monkeypatch, scenario
) -> None:
    if not shutil.which("clang++"):
        pytest.skip("clang++ not on PATH")
    from abicheck.extract.headers.clang import backend as clang_backend

    _isolated_cache(monkeypatch, tmp_path)
    headers = []
    for name, text in scenario.items():
        (tmp_path / name).write_text(text)
        headers.append(tmp_path / name)
    seen: set[str] = set()
    depth = [0]

    def profiler(frame, event, _arg):
        if event == "call" and depth[0]:
            name = frame.f_globals.get("__name__", "")
            if name.startswith("abicheck"):
                seen.add(name)

    def traced(fn):
        def wrapped(*a, **k):
            depth[0] += 1
            sys.setprofile(profiler)
            threading.setprofile(profiler)
            try:
                return fn(*a, **k)
            finally:
                sys.setprofile(None)
                threading.setprofile(None)
                depth[0] -= 1

        return wrapped

    monkeypatch.setattr(
        clang_backend,
        "_parse_clang_ast_result",
        traced(clang_backend._parse_clang_ast_result),
    )
    monkeypatch.setattr(
        clang_backend,
        "retry_excluding_error_headers",
        traced(clang_backend.retry_excluding_error_headers),
    )
    clang_backend.clang_header_dump(headers, [], "clang++", lang="c++", memoize=False)

    assert "abicheck.dumper_clang_errors" in seen, (
        "the trace did not observe the output path at all"
    )
    if scenario is _RETRY:
        assert "abicheck.storage.ast_parse_exclusions" in seen, (
            "the retry scenario did not exclude a header"
        )
    unclassified = sorted(
        seen
        - set(producers.CLANG_AST_OUTPUT_MODULES)
        - set(producers.NON_SHAPING_MODULES)
    )
    assert unclassified == [], (
        "these modules run between clang's exit and the cache write: list each in "
        "CLANG_AST_OUTPUT_MODULES (shapes the stored entry) or NON_SHAPING_MODULES (with why)"
    )


@pytest.mark.integration
class TestRealDumpsMissOnGeneratedInputChange:
    def _castxml_or_skip(self):
        if not shutil.which("castxml"):
            pytest.skip("castxml not on PATH")

    def test_castxml(self, tmp_path, monkeypatch) -> None:
        self._castxml_or_skip()
        from abicheck.extract.headers.castxml.backend import castxml_dump

        _isolated_cache(monkeypatch, tmp_path)
        runs, run = _recording_castxml_runner()
        header = tmp_path / "api.h"
        header.write_text("struct S { int a; };\nint f(struct S*);\n")

        def castxml_runs() -> int:
            # Parse runs only, not the macro-table ``-E -dM`` sibling run.
            return sum(
                1 for c in runs if "castxml" in Path(c[0]).name and "-dM" not in c
            )

        castxml_dump([header], [], "cc", lang="c", run=run)
        assert castxml_runs() == 1
        castxml_dump([header], [], "cc", lang="c", run=run)
        assert castxml_runs() == 1, (
            "an unchanged second dump must be served from the cache"
        )
        monkeypatch.setattr(
            cfg,
            "CASTXML_HEADER_PREAMBLE",
            cfg.CASTXML_HEADER_PREAMBLE + "/* changed */\n",
        )
        castxml_dump([header], [], "cc", lang="c", run=run)
        assert castxml_runs() == 2, "a changed generated input must re-run castxml"

    def test_clang(self, tmp_path, monkeypatch) -> None:
        if not shutil.which("clang++"):
            pytest.skip("clang++ not on PATH")
        from abicheck import dumper_clang_errors
        from abicheck.extract.headers.clang import backend as clang_backend

        _isolated_cache(monkeypatch, tmp_path)
        header = tmp_path / "api.hpp"
        header.write_text("struct S { int a; };\nint f(S*);\n")
        calls: list[int] = []
        real = dumper_clang_errors.run_clang_ast

        def spy(*a, **k):
            calls.append(1)
            return real(*a, **k)

        def dump() -> None:
            clang_backend.clang_header_dump(
                [header], [], "clang++", lang="c++", memoize=False, run_ast=spy
            )

        dump()
        assert len(calls) == 1
        dump()
        assert len(calls) == 1, "an unchanged second dump must be served from the cache"
        monkeypatch.setattr(
            cfg, "clang_ast_output_fingerprint", lambda: "different-code"
        )
        dump()
        assert len(calls) == 2, "changed output-shaping code must re-run clang"
        monkeypatch.setattr(
            cfg,
            "_build_clang_header_command",
            _appending(cfg._build_clang_header_command, "-DABICHECK_NEW_FLAG"),
        )
        monkeypatch.setattr(
            clang_backend,
            "_build_clang_header_command",
            cfg._build_clang_header_command,
        )
        dump()
        assert len(calls) == 3, "a changed generated command line must re-run clang"


def test_projection_sidecar_rejects_another_builds_projection(monkeypatch) -> None:
    import abicheck.buildsource.header_graph_projection_cache as pc
    from abicheck.buildsource.header_graph_ast_projection import (
        HeaderGraphAstProjection,
    )

    projection = HeaderGraphAstProjection(
        type_files={"S": "api.h"},
        entity_files={},
        type_edges=[],
        call_edges=[],
        special_member_names=frozenset(),
    )
    monkeypatch.setattr(pc, "abicheck_code_fingerprint", lambda: "code-A")
    blob = pc.encode_projection(projection)
    assert pc.decode_projection(blob) == projection
    monkeypatch.setattr(pc, "abicheck_code_fingerprint", lambda: "code-B")
    assert pc.decode_projection(blob) is None
