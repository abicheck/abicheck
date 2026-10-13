"""Every dump-affecting request field changes the whole-snapshot cache key.

The key is the only thing that decides which warm entry a dump reads. A field
that reaches the snapshot but not the key would serve a stale snapshot once
that field changes, which is the class of bug this module's single owner
exists to prevent. Each property changes one field of a cacheable request and
asserts that the key moves. A shape that is no longer cacheable derives the
empty key, which also misses, so it counts as a change too.

The completeness check fails when a new :class:`DumpCacheRequest` field has
neither a generator nor a written exemption, so a new input cannot slip past
this test unnoticed.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest
from hypothesis import HealthCheck, assume, given, settings, strategies as st

from abicheck.compile_context import CompileContext
from abicheck.dump_manifest import parse_manifest
from abicheck.workflows.dump.cache import api
from abicheck.workflows.dump.cache.api import DumpCacheRequest

#: Fields the key deliberately ignores for a cacheable request, each with the
#: reason. Every other field needs a generator in :func:`_alternatives`.
_EXEMPT: dict[str, str] = {
    "debuginfod_url": (
        "read only when enable_debuginfod is set, which makes the dump "
        "uncacheable; with it off the URL never reaches the snapshot"
    ),
}

_HYPOTHESIS = settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


@pytest.fixture(autouse=True)
def _default_toolchain_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the AST-backend environment so the key sees the default toolchain."""
    monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
    monkeypatch.delenv("ABICHECK_ALLOW_AST_FALLBACK", raising=False)


def _base(tmp_path: Path) -> DumpCacheRequest:
    """A cacheable, header-bearing request. Every file any alternative points
    at is written here, before the base key is taken: a file created later
    under a hashed include directory would change the base key itself and let
    every property pass vacuously."""
    root = tmp_path / "root"
    inc = root / "inc"
    inc.mkdir(parents=True)
    (inc / "api.h").write_text("int api(void);\n")
    (inc / "extra.h").write_text("int extra(void);\n")
    binary = root / "lib.so"
    binary.write_bytes(b"\x7fELF base")
    other = root / "other.so"
    other.write_bytes(b"\x7fELF other")
    return DumpCacheRequest(
        path=Path(os.path.abspath(binary)),
        binary_fmt="elf",
        headers=[inc / "api.h"],
        includes=[inc],
        version="1.0",
        lang="c++",
        lang_explicit=False,
        pdb_path=None,
        dwarf_only=False,
        debug_roots=None,
        enable_debuginfod=False,
        debuginfod_url=None,
        debug_format=None,
        symbols_only=False,
        debug_presence_only=False,
        public_headers=None,
        public_header_dirs=None,
        header_backend="castxml",
        compile=None,
        include_labels=None,
        dump_manifest=None,
        include_dependencies=True,
        public_include_search_dirs=None,
    )


def _alternatives(base: DumpCacheRequest) -> dict[str, st.SearchStrategy]:
    """A strategy of values for each key-affecting field. Each value is drawn
    to differ from *base*; :func:`assume` drops the rare equal draw."""
    root = base.path.parent
    inc = root / "inc"
    manifest = parse_manifest(
        "roots: [inc/api.h]\ntranslation_units:\n  - name: tu_a\n    forced_includes: [inc/api.h]\n",
        base_dir=root,
        source="<property>",
    )
    return {
        "binary_fmt": st.sampled_from(["pe", "macho"]),
        "path": st.just(root / "other.so"),
        "headers": st.just([]),
        "includes": st.just([root / "elsewhere"]),
        "version": st.text(alphabet="abcdefgh0123456789", max_size=8),
        "lang": st.just("c"),
        "lang_explicit": st.just(True),
        "pdb_path": st.just(root / "lib.pdb"),
        "dwarf_only": st.just(True),
        "debug_roots": st.just([root]),
        "enable_debuginfod": st.just(True),
        "debug_format": st.just("dwarf"),
        "symbols_only": st.just(True),
        "debug_presence_only": st.just(True),
        "public_headers": st.just([inc / "api.h"]),
        "public_header_dirs": st.just([inc]),
        "header_backend": st.sampled_from(["clang", "hybrid"]),
        "compile": st.sampled_from(["-DA", "-DB"]).map(
            lambda tok: CompileContext(gcc_option_tokens=(tok,))
        ),
        "include_labels": st.just({inc: "LABEL"}),
        "dump_manifest": st.just(manifest),
        "include_dependencies": st.just(False),
        "public_include_search_dirs": st.sampled_from([[], [inc]]),
    }


_FIELD_NAMES = sorted(f.name for f in dataclasses.fields(DumpCacheRequest))


def test_every_request_field_is_classified(tmp_path: Path) -> None:
    """Each field has a generator or a written exemption, and not both."""
    generators = set(_alternatives(_base(tmp_path)))
    assert not generators & set(_EXEMPT)
    assert generators | set(_EXEMPT) == set(_FIELD_NAMES), sorted(
        set(_FIELD_NAMES) ^ (generators | set(_EXEMPT))
    )


def test_base_request_is_cacheable_and_key_is_deterministic(tmp_path: Path) -> None:
    base = _base(tmp_path)
    key = api._cache_key_for(base)
    assert key, "the base shape must be cacheable for the properties to mean anything"
    assert api._cache_key_for(base) == key


@pytest.mark.parametrize("name", [n for n in _FIELD_NAMES if n not in _EXEMPT])
def test_changing_a_dump_affecting_field_changes_the_key(
    name: str, tmp_path: Path
) -> None:
    base = _base(tmp_path)
    base_key = api._cache_key_for(base)
    assert base_key
    strategy = _alternatives(base)[name]

    @_HYPOTHESIS
    @given(value=strategy)
    def check(value: object) -> None:
        alt = dataclasses.replace(base, **{name: value})
        assume(alt != base)
        assert api._cache_key_for(alt) != base_key, (
            f"changing {name!r} to {value!r} left the cache key unchanged"
        )

    check()


def test_exempt_fields_do_not_change_the_key(tmp_path: Path) -> None:
    base = _base(tmp_path)
    base_key = api._cache_key_for(base)
    assert base_key

    @_HYPOTHESIS
    @given(url=st.text(max_size=30))
    def check(url: str) -> None:
        assert (
            api._cache_key_for(dataclasses.replace(base, debuginfod_url=url))
            == base_key
        )

    check()


# ---- the manifest's structural identity (ADR-050 D1, G32 Phase E) ----------
#
# A content walk over the manifest's paths cannot see a TU rename or a flipped
# flag, so those reach the key only through the structural field. TU *order* is
# deliberately not in it: manifest_tu_scope_field sorts TUs by name.

_MANIFEST = """\
roots: [inc/api.h]
translation_units:
  - name: tu_a
    forced_includes: [inc/api.h]
    includes: [inc]
  - name: tu_b
    forced_includes: [inc/api.h]
"""


@pytest.mark.parametrize(
    "variant",
    [
        pytest.param(_MANIFEST.replace("tu_a", "tu_renamed"), id="tu-renamed"),
        pytest.param(
            _MANIFEST.replace("roots: [inc/api.h]", "roots: [inc/api.h, inc/extra.h]"),
            id="roots-changed",
        ),
        pytest.param(
            _MANIFEST.replace(
                "roots: [inc/api.h]\n", "roots: [inc/api.h]\nfrontend_context: device\n"
            ),
            id="frontend-context",
        ),
        pytest.param(
            _MANIFEST.replace(
                "    includes: [inc]\n",
                "    includes: [{path: inc, project_owned: true}]\n",
            ),
            id="project-owned-flipped",
        ),
    ],
)
def test_manifest_structural_fields_change_the_key(
    variant: str, tmp_path: Path
) -> None:
    base = _base(tmp_path)
    root = base.path.parent

    def key_for(body: str) -> str:
        manifest = parse_manifest(body, base_dir=root, source="<manifest>")
        return api._cache_key_for(
            dataclasses.replace(
                base, headers=None, includes=None, dump_manifest=manifest
            )
        )

    base_key = key_for(_MANIFEST)
    assert base_key
    assert key_for(variant) != base_key
