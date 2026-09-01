# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for declaration provenance (ADR-015, schema v6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    FactStatus,
    Function,
    RecordType,
    ScopeOrigin,
    Variable,
    Visibility,
)
from abicheck.provenance import (
    _absolutize_header_root,
    _is_toolchain_compiler_include_dir,
    _segments,
    apply_provenance,
    build_public_set,
    classify_origin,
    header_from_location,
    is_dependency_header,
    tag_provenance,
)
from abicheck.serialization import SCHEMA_VERSION, snapshot_from_dict, snapshot_to_dict

# ── header_from_location ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "loc,expected",
    [
        ("include/api.h:42", "include/api.h"),
        ("include/api.h:42:9", "include/api.h"),
        ("/build/src/foo.hpp:1", "/build/src/foo.hpp"),
        ("plain.h", "plain.h"),
        ("C:\\proj\\inc\\api.h:10", "C:\\proj\\inc\\api.h"),  # drive letter colon kept
        (None, None),
        ("", None),
    ],
)
def test_header_from_location(loc, expected):
    assert header_from_location(loc) == expected


# ── classify_origin ───────────────────────────────────────────────────────────


def _classify(header, public_headers=None, public_dirs=None):
    hs, ds, have = build_public_set(public_headers, public_dirs)
    return classify_origin(header, hs, ds, have_public_set=have)


def test_no_public_set_is_always_unknown():
    # Decision D4: without a public set, everything is UNKNOWN regardless of path.
    assert _classify("/usr/include/stdio.h") is ScopeOrigin.UNKNOWN
    assert _classify("include/api.h") is ScopeOrigin.UNKNOWN


def test_none_header_is_unknown_even_with_public_set():
    assert _classify(None, public_headers=["include/api.h"]) is ScopeOrigin.UNKNOWN


def test_exact_public_header_suffix_match_through_build_prefix():
    # Build path carries an absolute prefix the user never typed.
    origin = _classify(
        "/build/abc123/src/include/api.h",
        public_headers=["include/api.h"],
    )
    assert origin is ScopeOrigin.PUBLIC_HEADER


def test_basename_fallback_match():
    origin = _classify(
        "/wherever/it/landed/api.h",
        public_headers=["api.h"],
    )
    assert origin is ScopeOrigin.PUBLIC_HEADER


def test_public_header_dir_containment():
    origin = _classify(
        "/build/proj/include/sub/widget.h",
        public_dirs=["include"],
    )
    assert origin is ScopeOrigin.PUBLIC_HEADER


def test_system_header_classified_when_set_present():
    origin = _classify("/usr/include/stdio.h", public_headers=["include/api.h"])
    assert origin is ScopeOrigin.SYSTEM_HEADER


def test_system_header_with_sysroot_prefix():
    origin = _classify(
        "/opt/sysroot/usr/include/bits/types.h",
        public_headers=["include/api.h"],
    )
    assert origin is ScopeOrigin.SYSTEM_HEADER


def test_system_header_conda_forge_gcc_private_include():
    # A conda-forge/pixi GCC's own private headers -- e.g. what this
    # project's own GitHub Action installs -- sit under an arbitrary
    # environment prefix, not /usr, so the fixed _SYSTEM_HEADER_DIRS
    # prefixes never matched. Real conda-forge layout.
    origin = _classify(
        "/home/runner/.pixi/envs/scanner/lib/gcc/x86_64-conda-linux-gnu/"
        "14.3.0/include-fixed/limits.h",
        public_headers=["include/api.h"],
    )
    assert origin is ScopeOrigin.SYSTEM_HEADER


def test_gcc_include_dir_with_non_compiler_shaped_triple_and_version_not_system():
    # Round-3 review finding (Codex, fresh evidence): the triple/version
    # components between lib/gcc and include were unconditionally
    # wildcarded, so a project path that merely happens to have two
    # components there -- neither a real target triple nor a real GCC
    # version -- was misclassified as a toolchain system header, which can
    # silently drop the declarations under it from the public surface.
    # Tested directly against the primitive (not through classify_origin's
    # full pipeline, whose basename-fallback public match would otherwise
    # mask this specific check when the two files share a basename).
    segs = _segments("/project/lib/gcc/backend/v1/include/api.h")
    assert _is_toolchain_compiler_include_dir(segs) is False


def test_gcc_include_dir_with_compiler_shaped_triple_and_version_is_toolchain():
    # Positive control for the test above: a real-shaped triple/version
    # pair must still match (this is the case the fix must not regress).
    segs = _segments(
        "/opt/gcc/lib/gcc/x86_64-pc-linux-gnu/13.2.0/include/stddef.h"
    )
    assert _is_toolchain_compiler_include_dir(segs) is True


def test_gcc_include_dir_with_dotted_solaris_style_os_component_is_toolchain():
    # Round-4 review finding (Codex, fresh evidence): the triple regex
    # required every component to be plain alnum/underscore, rejecting a
    # real Solaris/AIX-style target triple whose OS component embeds a
    # dotted version (this repo's own toolchain_probe.py already recognizes
    # "x86_64-pc-solaris2.11" as a real triple). Under a relocatable prefix
    # not covered by _SYSTEM_HEADER_DIRS, this made a real compiler's own
    # private headers read as project declarations, reintroducing noisy/
    # false findings from the toolchain surface.
    segs = _segments(
        "/opt/gcc/lib/gcc/x86_64-pc-solaris2.11/13/include/stddef.h"
    )
    assert _is_toolchain_compiler_include_dir(segs) is True


def test_gcc_include_dir_debian_multiarch_libstdcxx_is_toolchain():
    # Round-5 review finding (Codex, fresh evidence): the anchored
    # usr/include/c++ check required "c++" immediately after the system
    # prefix, with no room for the real Debian/Ubuntu multiarch component
    # that sits between them for a multiarch-enabled GCC install
    # (`-I /usr/include/x86_64-linux-gnu/c++/12`). Under this layout,
    # libstdc++ headers like bits/c++config.h were never recognized as
    # system, letting them survive dependency exclusion as if they were
    # project declarations.
    segs = _segments("/usr/include/x86_64-linux-gnu/c++/12/bits/c++config.h")
    assert _is_toolchain_compiler_include_dir(segs) is True


def test_gcc_include_dir_debian_multiarch_with_non_triple_component_not_toolchain():
    # Negative control: a non-triple-shaped component between the system
    # prefix and c++ must NOT be accepted -- otherwise this would reopen
    # the same wildcarding gap round 3 already closed for lib/gcc.
    segs = _segments("/usr/include/notarealmultiarch/c++/12/vector")
    assert _is_toolchain_compiler_include_dir(segs) is False


def test_gcc_include_dir_triple_shaped_project_name_not_multiarch():
    # Round-6 review finding (Codex, fresh evidence): a two-word project
    # directory name that merely happens to be triple-SHAPED (`my-lib`,
    # matching _TARGET_TRIPLE_RE's bare "2-4 alnum components" grammar) but
    # names no real OS/libc-environment family must NOT be accepted as a
    # multiarch component -- otherwise an explicitly-declared -I under an
    # installed project layout like `/usr/include/my-lib/c++/api.h` would
    # be silently treated as a system path, discarding the user's own
    # public-scoping declaration.
    segs = _segments("/usr/include/my-lib/c++/api.h")
    assert _is_toolchain_compiler_include_dir(segs) is False


def test_gcc_include_dir_single_component_avr_target_is_toolchain():
    # Round-7 review finding (Codex, fresh evidence): a relocatable GCC
    # install targeting a single-component machine (AVR, embedded/bare-
    # metal) has no vendor/OS/environment components at all, so
    # _TARGET_TRIPLE_RE's own "at least one hyphen" requirement rejected
    # the real target directory -- these compiler-owned headers then
    # survived default dependency exclusion, or became public when
    # supplied via -I, risking false ABI findings from the toolchain
    # surface.
    segs = _segments("/opt/toolchain/lib/gcc/avr/12.2.0/include/stdint.h")
    assert _is_toolchain_compiler_include_dir(segs) is True


def test_gcc_include_dir_single_component_non_target_word_not_toolchain():
    # Positive control for round 3's own original finding: widening to
    # accept a real single-component GCC target must not reopen the
    # "any bare word is a target" gap -- an arbitrary project directory
    # name that is NOT a recognized bare-metal target must still be
    # rejected, even paired with a numeric-looking sibling directory.
    segs = _segments("/project/lib/gcc/backend/14/include/api.h")
    assert _is_toolchain_compiler_include_dir(segs) is False


def test_system_header_conda_forge_libstdcxx_predefined_ops():
    # The exact real-world path from a false-positive func_removed report:
    # abicheck's own GitHub Action reported libstdc++'s internal
    # _Iter_pred predicate helper as a removed *public* function, because
    # this path -- with its literal unnormalized `bin/..` segment -- didn't
    # match any known system-header prefix.
    origin = _classify(
        "/home/runner/work/_actions/abicheck/abicheck/main/.pixi/envs/"
        "scanner/bin/../lib/gcc/x86_64-conda-linux-gnu/14.3.0/include/c++/"
        "bits/predefined_ops.h",
        public_headers=["include/api.h"],
    )
    assert origin is ScopeOrigin.SYSTEM_HEADER


def test_system_header_conda_forge_clang_builtin_include():
    origin = _classify(
        "/opt/pixi/envs/scanner/lib/clang/18/include/stddef.h",
        public_headers=["include/api.h"],
    )
    assert origin is ScopeOrigin.SYSTEM_HEADER


def test_toolchain_include_dir_path_normalizes_dot_dot_segment():
    # A `foo/bar/../baz` segment sequence must classify identically to its
    # already-collapsed `foo/baz` form -- not just for the toolchain-dir
    # patterns above, but for _segments() generally (basic path-segment
    # equivalence, unrelated to any specific prefix list).
    from abicheck.provenance import _segments

    assert _segments("/a/b/../c") == _segments("/a/c")
    assert _segments("bin/../lib/x") == ("lib", "x")
    # A leading `..` with nothing to collapse against is kept as-is.
    assert _segments("../a/b") == ("..", "a", "b")


def test_conda_forge_project_header_outside_compiler_tree_stays_project():
    # A project header that merely happens to live under the same
    # conda/pixi environment prefix (but not inside the compiler's own
    # lib/gcc/.../include or include/c++/... tree) must not be swept up by
    # the structural toolchain-include-dir match.
    origin = _classify(
        "/home/runner/.pixi/envs/scanner/include/myproject/api.h",
        public_headers=["include/api.h"],
        public_dirs=["include/myproject"],
    )
    assert origin is ScopeOrigin.PUBLIC_HEADER


def test_bare_include_cxx_with_no_toolchain_prefix_is_not_system_header():
    # Codex review, real finding: an earlier revision matched a bare
    # "include/c++" *anywhere* in the path, with no requirement that it sit
    # under a recognized lib/gcc/.../ or lib/clang/.../ toolchain root -- a
    # project shipping its own "include/c++" directory (an unusual but legal
    # project layout) would have been misclassified as a system header,
    # letting default dependency scoping / public-surface evaluation drop
    # or demote the project's own declarations. Basename deliberately does
    # NOT match the public header ("vector", not "api.h") -- the basename
    # fallback in _matches_public would otherwise mask a system-header
    # misclassification behind a public-header match.
    origin = _classify(
        "/project/include/c++/vector",
        public_headers=["include/api.h"],
    )
    assert origin is not ScopeOrigin.SYSTEM_HEADER


def test_private_header_when_not_public_and_not_system():
    origin = _classify(
        "/build/proj/src/internal/impl.h",
        public_headers=["include/api.h"],
        public_dirs=["include"],
    )
    assert origin is ScopeOrigin.PRIVATE_HEADER


def test_public_takes_precedence_over_system_path():
    # A header that both matches the public set and lives under usr/include
    # should classify PUBLIC (public check runs first).
    origin = _classify(
        "/usr/include/mylib/api.h",
        public_dirs=["mylib"],
    )
    assert origin is ScopeOrigin.PUBLIC_HEADER


@pytest.mark.parametrize(
    "header",
    [
        "/build/proj/generated/messages.h",
        "/build/proj/src/moc_widget.cpp",
        "/build/proj/proto/service.pb.h",
        "/build/proj/schema_generated.h",
        "/build/proj/api.grpc.pb.h",
    ],
)
def test_generated_headers_classified(header):
    # A public set must be present (opt-in), but the generated path is neither
    # public nor system → GENERATED.
    origin = _classify(header, public_headers=["include/api.h"])
    assert origin is ScopeOrigin.GENERATED


def test_export_only_when_no_header_but_symbol_exported():
    hs, ds, have = build_public_set(["include/api.h"], None)
    origin = classify_origin(None, hs, ds, have_public_set=have, export_only=True)
    assert origin is ScopeOrigin.EXPORT_ONLY


def test_export_only_ignored_without_public_set():
    # D4: no public set → UNKNOWN regardless of export-only linkage.
    hs, ds, have = build_public_set(None, None)
    origin = classify_origin(None, hs, ds, have_public_set=have, export_only=True)
    assert origin is ScopeOrigin.UNKNOWN


def test_no_header_not_exported_is_unknown():
    hs, ds, have = build_public_set(["include/api.h"], None)
    origin = classify_origin(None, hs, ds, have_public_set=have, export_only=False)
    assert origin is ScopeOrigin.UNKNOWN


# ── apply_provenance ──────────────────────────────────────────────────────────


def _snapshot() -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so.1",
        version="1.0",
        functions=[
            Function(
                name="pub",
                mangled="pub",
                return_type="void",
                source_location="/build/include/api.h:10",
            ),
            Function(
                name="priv",
                mangled="priv",
                return_type="void",
                source_location="/build/src/impl.h:20",
            ),
            Function(name="noloc", mangled="noloc", return_type="void"),
        ],
        variables=[
            Variable(
                name="g",
                mangled="g",
                type="int",
                source_location="/build/include/api.h:5",
            ),
        ],
        types=[
            RecordType(
                name="S", kind="struct", source_location="/build/include/api.h:30"
            ),
        ],
        enums=[
            EnumType(
                name="E",
                members=[EnumMember(name="A", value=0)],
                source_location="/build/include/api.h:40",
            ),
        ],
    )


def test_apply_provenance_opt_in_classification():
    snap = apply_provenance(_snapshot(), public_headers=["include/api.h"])
    by_name = {f.name: f for f in snap.functions}
    assert by_name["pub"].source_header == "/build/include/api.h"
    assert by_name["pub"].origin is ScopeOrigin.PUBLIC_HEADER
    assert by_name["priv"].origin is ScopeOrigin.PRIVATE_HEADER
    # No source location → no header, UNKNOWN origin.
    assert by_name["noloc"].source_header is None
    assert by_name["noloc"].origin is ScopeOrigin.UNKNOWN
    assert snap.variables[0].origin is ScopeOrigin.PUBLIC_HEADER
    assert snap.types[0].origin is ScopeOrigin.PUBLIC_HEADER
    assert snap.enums[0].origin is ScopeOrigin.PUBLIC_HEADER


def test_apply_provenance_source_header_fact_matches_presence_of_a_real_header():
    # ADR-063 Phase 5 (Codex review): tag_provenance() must not claim
    # Fact.present(None) for a declaration whose location never resolved a
    # header at all -- source_header's own case-(b) convention (matching
    # every other field this phase converted) treats a None legacy value
    # as "not captured", not a confirmed-empty determination.
    snap = apply_provenance(
        AbiSnapshot(
            library="libfoo.so.1",
            version="1.0",
            types=[
                RecordType(
                    name="WithHeader",
                    kind="struct",
                    source_location="/build/include/api.h:30",
                ),
                RecordType(name="NoLocation", kind="struct"),
            ],
        ),
        public_headers=["include/api.h"],
    )
    by_name = {t.name: t for t in snap.types}
    with_header = by_name["WithHeader"]
    assert with_header.source_header == "/build/include/api.h"
    assert with_header.source_header_fact.status is FactStatus.PRESENT
    assert with_header.source_header_fact.value == "/build/include/api.h"

    no_location = by_name["NoLocation"]
    assert no_location.source_header is None
    assert no_location.source_header_fact.status is FactStatus.NOT_COLLECTED


def test_apply_provenance_no_set_keeps_unknown_but_fills_header():
    # source_header is descriptive metadata and is always populated; origin
    # stays UNKNOWN without a public set (decision D4).
    snap = apply_provenance(_snapshot())
    assert snap.functions[0].source_header == "/build/include/api.h"
    assert snap.functions[0].origin is ScopeOrigin.UNKNOWN
    assert snap.types[0].origin is ScopeOrigin.UNKNOWN


# ── include_search_dirs: headers reached transitively from a -H root ─────────


def _snapshot_with_transitive_private_header() -> AbiSnapshot:
    # `priv` mirrors a declaration reached only transitively -- via #include
    # -- from the -H root (`/build/include/api.h`), but physically living
    # under a *different* header nested inside the same -I include root
    # (`/build/include`). A header-AST dump only ever parses declarations
    # reachable by #include from its own -H root(s), so this is exactly the
    # shape produced by `dump -H include/api.h -I include`.
    return AbiSnapshot(
        library="libfoo.so.1",
        version="1.0",
        functions=[
            Function(
                name="pub",
                mangled="pub",
                return_type="void",
                source_location="/build/include/api.h:10",
            ),
            Function(
                name="priv",
                mangled="priv",
                return_type="void",
                source_location="/build/include/detail/impl.h:20",
            ),
        ],
    )


def test_include_search_dirs_promotes_transitively_included_header_to_public():
    # Defect: every header reached only transitively from the -H root
    # (rather than being the literal -H file itself) classified
    # PRIVATE_HEADER, even when it lives under the same -I include root the
    # dump was given -- silently dropping real findings out of the compared
    # surface. include_search_dirs (the dump's own -I roots) fixes this.
    snap = apply_provenance(
        _snapshot_with_transitive_private_header(),
        public_headers=["/build/include/api.h"],
        include_search_dirs=["/build/include"],
    )
    by_name = {f.name: f for f in snap.functions}
    assert by_name["pub"].origin is ScopeOrigin.PUBLIC_HEADER
    assert by_name["priv"].origin is ScopeOrigin.PUBLIC_HEADER


def test_include_search_dirs_omitted_keeps_prior_private_header_behavior():
    # Without include_search_dirs (e.g. a caller that never threaded -I
    # roots through), behavior is unchanged: only the literal -H file(s)
    # classify public.
    snap = apply_provenance(
        _snapshot_with_transitive_private_header(),
        public_headers=["/build/include/api.h"],
    )
    by_name = {f.name: f for f in snap.functions}
    assert by_name["pub"].origin is ScopeOrigin.PUBLIC_HEADER
    assert by_name["priv"].origin is ScopeOrigin.PRIVATE_HEADER


def test_include_search_dirs_cannot_opt_in_classification_by_itself():
    # include_search_dirs must never turn provenance classification on when
    # no real -H/--public-header-dir set was given -- ADR-015 D4's opt-in
    # contract stays intact.
    snap = apply_provenance(
        _snapshot_with_transitive_private_header(),
        include_search_dirs=["/build/include"],
    )
    by_name = {f.name: f for f in snap.functions}
    assert by_name["pub"].origin is ScopeOrigin.UNKNOWN
    assert by_name["priv"].origin is ScopeOrigin.UNKNOWN


def test_include_search_dirs_does_not_override_bare_system_prefix():
    # A stray -I /usr/include must not make every system header underneath
    # classify as project-owned.
    snap = apply_provenance(
        AbiSnapshot(
            library="libfoo.so.1",
            version="1.0",
            functions=[
                Function(
                    name="sys",
                    mangled="sys",
                    return_type="void",
                    source_location="/usr/include/stdio.h:1",
                ),
            ],
        ),
        public_headers=["/build/include/api.h"],
        include_search_dirs=["/usr/include"],
    )
    assert snap.functions[0].origin is ScopeOrigin.SYSTEM_HEADER


def test_include_search_dirs_does_not_promote_nested_toolchain_root():
    # Codex review, real finding: an explicit -I pointed *below* a system
    # boundary (e.g. /usr/include/c++/12, or the conda-forge-nested
    # equivalent) was not excluded by the exact-bare-boundary check, so it
    # was promoted to a public directory -- letting libstdc++ declarations
    # beneath it classify PUBLIC_HEADER (since classify_origin checks the
    # public match before the system-header one) and bypass dependency
    # exclusion, allowing transitive toolchain changes to produce false ABI
    # findings.
    snap = apply_provenance(
        AbiSnapshot(
            library="libfoo.so.1",
            version="1.0",
            functions=[
                Function(
                    name="vec_fn",
                    mangled="vec_fn",
                    return_type="void",
                    source_location="/usr/include/c++/12/vector:10",
                ),
            ],
        ),
        public_headers=["/build/include/api.h"],
        include_search_dirs=["/usr/include/c++/12"],
    )
    assert snap.functions[0].origin is ScopeOrigin.SYSTEM_HEADER


# ── origin_cache (tag_provenance / apply_provenance memoization) ─────────────
#
# apply_provenance() and the buildsource castxml extractor share one
# classify_origin() result across every declaration produced by the same
# header — these tests prove the cache actually gets *hit* (not just that
# overall output happens to be unchanged), and that it never conflates
# declarations under a different (source_header, export_only) key.


def _tag(decl, header_segs, dir_segs, have_set, *, origin_cache=None):
    tag_provenance(decl, header_segs, dir_segs, have_set, origin_cache=origin_cache)
    return decl


def test_origin_cache_hit_reuses_prior_classification(monkeypatch):
    """Two declarations sharing one header must classify_origin() only once."""
    calls = []
    real_classify_origin = classify_origin

    def _spy(*args, **kwargs):
        calls.append((args, tuple(sorted(kwargs.items()))))
        return real_classify_origin(*args, **kwargs)

    monkeypatch.setattr("abicheck.provenance.classify_origin", _spy)

    header_segs, dir_segs, have_set = build_public_set(["include/api.h"], None)
    origin_cache: dict = {}
    a = Function(
        name="a", mangled="a", return_type="void",
        source_location="/build/include/api.h:10",
    )
    b = Function(
        name="b", mangled="b", return_type="void",
        source_location="/build/include/api.h:99",  # same header, different line
    )
    _tag(a, header_segs, dir_segs, have_set, origin_cache=origin_cache)
    _tag(b, header_segs, dir_segs, have_set, origin_cache=origin_cache)

    assert a.origin is ScopeOrigin.PUBLIC_HEADER
    assert b.origin is ScopeOrigin.PUBLIC_HEADER
    assert len(calls) == 1  # the second call was served from origin_cache


def test_origin_cache_distinguishes_different_headers(monkeypatch):
    """Declarations from different headers must not share a cached result."""
    calls = []
    real_classify_origin = classify_origin

    def _spy(*args, **kwargs):
        calls.append(1)
        return real_classify_origin(*args, **kwargs)

    monkeypatch.setattr("abicheck.provenance.classify_origin", _spy)

    header_segs, dir_segs, have_set = build_public_set(["include/api.h"], None)
    origin_cache: dict = {}
    pub = Function(
        name="pub", mangled="pub", return_type="void",
        source_location="/build/include/api.h:10",
    )
    priv = Function(
        name="priv", mangled="priv", return_type="void",
        source_location="/build/src/impl.h:20",
    )
    _tag(pub, header_segs, dir_segs, have_set, origin_cache=origin_cache)
    _tag(priv, header_segs, dir_segs, have_set, origin_cache=origin_cache)

    assert pub.origin is ScopeOrigin.PUBLIC_HEADER
    assert priv.origin is ScopeOrigin.PRIVATE_HEADER
    assert len(calls) == 2  # distinct headers never share a cache entry


def test_origin_cache_distinguishes_export_only_from_same_header(monkeypatch):
    """Same (missing) header, different export_only, must not collide.

    ``export_only`` comes from ``Visibility.ELF_ONLY`` and only matters when
    there's no source_location at all — the cache key is
    ``(source_header, export_only)``, so two no-location declarations that
    differ only in visibility must classify independently.
    """
    header_segs, dir_segs, have_set = build_public_set(["include/api.h"], None)
    origin_cache: dict = {}
    exported = Function(
        name="exported", mangled="exported", return_type="void",
        visibility=Visibility.ELF_ONLY,
    )
    hidden = Function(
        name="hidden", mangled="hidden", return_type="void",
        visibility=Visibility.HIDDEN,
    )
    _tag(exported, header_segs, dir_segs, have_set, origin_cache=origin_cache)
    _tag(hidden, header_segs, dir_segs, have_set, origin_cache=origin_cache)

    assert exported.origin is ScopeOrigin.EXPORT_ONLY
    assert hidden.origin is ScopeOrigin.UNKNOWN
    assert len(origin_cache) == 2  # (None, True) and (None, False) both cached


def test_origin_cache_matches_uncached_result():
    """The cached and uncached (origin_cache=None) paths must agree exactly —
    the cache must be a pure optimization, never a behavior change."""
    header_segs, dir_segs, have_set = build_public_set(["include/api.h"], None)

    cached_decls = [
        Function(
            name=n, mangled=n, return_type="void",
            source_location=f"/build/include/api.h:{i}",
        )
        for i, n in enumerate(["a", "b", "c"])
    ]
    uncached_decls = [
        Function(
            name=n, mangled=n, return_type="void",
            source_location=f"/build/include/api.h:{i}",
        )
        for i, n in enumerate(["a", "b", "c"])
    ]

    origin_cache: dict = {}
    for d in cached_decls:
        _tag(d, header_segs, dir_segs, have_set, origin_cache=origin_cache)
    for d in uncached_decls:
        _tag(d, header_segs, dir_segs, have_set, origin_cache=None)

    assert [d.origin for d in cached_decls] == [d.origin for d in uncached_decls]
    assert [d.source_header for d in cached_decls] == [
        d.source_header for d in uncached_decls
    ]


def test_apply_provenance_shares_one_cache_across_all_declaration_kinds(monkeypatch):
    """apply_provenance() builds one origin_cache and threads it through
    functions/variables/types/enums — declarations of different *kinds*
    sharing api.h must still hit the same cache entry, not one per kind."""
    calls = []
    real_classify_origin = classify_origin

    def _spy(*args, **kwargs):
        calls.append(1)
        return real_classify_origin(*args, **kwargs)

    monkeypatch.setattr("abicheck.provenance.classify_origin", _spy)

    # _snapshot()'s pub/variable/type/enum all declare source_location
    # "/build/include/api.h:<n>" (public, non-export-only) — one shared key.
    apply_provenance(_snapshot(), public_headers=["include/api.h"])

    # api.h contributes 4 same-key declarations (pub func, var, type, enum) +
    # impl.h contributes 1 (priv func) + no-location contributes 1 (noloc
    # func, sh=None) = 3 distinct (source_header, export_only) keys total.
    assert len(calls) == 3


# ── serialization round-trip (schema v6) ──────────────────────────────────────


def test_serialization_round_trip_preserves_provenance():
    snap = apply_provenance(_snapshot(), public_headers=["include/api.h"])
    d = snapshot_to_dict(snap)
    assert d["schema_version"] == SCHEMA_VERSION
    # Enum value serialized as a plain string.
    assert d["functions"][0]["origin"] == "public_header"
    assert d["functions"][0]["source_header"] == "/build/include/api.h"

    back = snapshot_from_dict(d)
    assert back.functions[0].origin is ScopeOrigin.PUBLIC_HEADER
    assert back.functions[0].source_header == "/build/include/api.h"
    assert back.enums[0].origin is ScopeOrigin.PUBLIC_HEADER
    assert back.enums[0].source_header == "/build/include/api.h"
    assert back.types[0].origin is ScopeOrigin.PUBLIC_HEADER
    assert back.variables[0].origin is ScopeOrigin.PUBLIC_HEADER


def test_old_snapshot_without_provenance_loads_as_unknown():
    # A pre-v6 snapshot dict has no source_header / origin keys.
    legacy = {
        "library": "libold.so",
        "version": "1.0",
        "functions": [{"name": "f", "mangled": "f", "return_type": "void"}],
        "variables": [{"name": "v", "mangled": "v", "type": "int"}],
        "types": [{"name": "T", "kind": "struct"}],
        "enums": [{"name": "E", "members": []}],
    }
    snap = snapshot_from_dict(legacy)
    assert snap.functions[0].origin is ScopeOrigin.UNKNOWN
    assert snap.functions[0].source_header is None
    assert snap.variables[0].origin is ScopeOrigin.UNKNOWN
    assert snap.types[0].origin is ScopeOrigin.UNKNOWN
    assert snap.enums[0].origin is ScopeOrigin.UNKNOWN


# ── castxml dumper wires source_location onto records/variables/enums ─────────
# (regression guard for the dumper fix; uses synthetic XML, no castxml binary)


def _castxml_root():
    from xml.etree.ElementTree import Element, SubElement

    root = Element("CastXML")
    f = SubElement(root, "File")
    f.set("id", "f1")
    f.set("name", "/build/inc/api.h")
    # Direct file/line form on a struct.
    s = SubElement(root, "Struct")
    s.set("id", "_s")
    s.set("name", "Widget")
    s.set("size", "64")
    s.set("align", "32")
    s.set("file", "f1")
    s.set("line", "12")
    # Location-ref form on a variable.
    loc = SubElement(root, "Location")
    loc.set("id", "l1")
    loc.set("file", "f1")
    loc.set("line", "20")
    fund = SubElement(root, "FundamentalType")
    fund.set("id", "_int")
    fund.set("name", "int")
    v = SubElement(root, "Variable")
    v.set("id", "_v")
    v.set("name", "g_count")
    v.set("mangled", "g_count")
    v.set("type", "_int")
    v.set("location", "l1")
    # Enumeration with direct file/line.
    e = SubElement(root, "Enumeration")
    e.set("id", "_e")
    e.set("name", "Color")
    e.set("file", "f1")
    e.set("line", "30")
    return root


def test_castxml_populates_source_location_on_types_vars_enums():
    from abicheck.dumper import _CastxmlParser

    root = _castxml_root()
    parser = _CastxmlParser(
        root, exported_dynamic={"g_count"}, exported_static={"g_count"}
    )
    rec = next(t for t in parser.parse_types() if t.name == "Widget")
    assert rec.source_location == "/build/inc/api.h:12"
    var = next(v for v in parser.parse_variables() if v.name == "g_count")
    assert var.source_location == "/build/inc/api.h:20"
    enum = next(e for e in parser.parse_enums() if e.name == "Color")
    assert enum.source_location == "/build/inc/api.h:30"


# ── is_dependency_header ────────────────────────────────────────────────────


class TestIsDependencyHeaderRootResolution:
    """Regression coverage for a Codex-review P2 finding: a relative
    ``-H`` root (e.g. ``dump ... -H include/api.h``, the common invocation
    from a project's own root directory) must not turn its short relative
    parent directory (``include``) into a public-dir segment that then
    matches *any* unrelated path containing the same generic component --
    including real system headers like ``/usr/include/...`` -- which would
    defeat dependency exclusion entirely."""

    def test_relative_root_directory_does_not_leak_into_system_paths(self):
        root = "include/api.h"
        assert is_dependency_header("/usr/include/c++/11/string", [root]) is True

    def test_relative_root_still_recognizes_its_own_header(self):
        root = "include/api.h"
        resolved = str(Path(root).resolve())
        assert is_dependency_header(resolved, [root]) is False

    def test_relative_root_recognizes_sibling_private_header(self):
        root = "include/api.h"
        sibling = str(Path("include/detail/internal.h").resolve())
        assert is_dependency_header(sibling, [root]) is False

    def test_no_header_roots_falls_back_to_bare_heuristic(self):
        assert is_dependency_header("/usr/include/c++/11/string", None) is True
        assert is_dependency_header("/usr/include/c++/11/string", []) is True

    def test_absolute_root_under_system_prefix_still_kept(self):
        root = "/usr/include/mylib/api.h"
        assert is_dependency_header(root, [root]) is False

    def test_no_source_header_is_never_a_dependency(self):
        assert is_dependency_header(None, ["include/api.h"]) is False


class TestIsDependencyHeaderDirectoryRoot:
    """Regression coverage for a Codex-review P1 finding: ``-H`` accepts a
    directory as well as a file (``dump --header`` help text: "Public
    header file or directory"). Unconditionally widening every root to its
    *parent* over-widens a directory root -- ``-H /usr/include/mylib``
    must not turn into the public dir ``/usr/include``, which would make
    every unrelated header under that prefix (including real dependency
    headers) match as project-owned."""

    def test_directory_root_does_not_widen_to_parent(self, tmp_path):
        # "usr"/"include" as separate path segments (not one joined
        # component) so the real system-header heuristic recognizes the
        # sibling as a genuine dependency path, the same way it would
        # recognize a real /usr/include/otherlib on disk.
        root_dir = tmp_path / "usr" / "include" / "mylib"
        root_dir.mkdir(parents=True)
        sibling_dep = tmp_path / "usr" / "include" / "otherlib" / "dep.h"
        sibling_dep.parent.mkdir(parents=True)
        sibling_dep.write_text("", encoding="utf-8")

        assert is_dependency_header(str(sibling_dep), [str(root_dir)]) is True

    def test_directory_root_still_keeps_its_own_contents(self, tmp_path):
        root_dir = tmp_path / "usr" / "include" / "mylib"
        own_header = root_dir / "detail" / "api.h"
        own_header.parent.mkdir(parents=True)
        own_header.write_text("", encoding="utf-8")

        assert is_dependency_header(str(own_header), [str(root_dir)]) is False

    def test_file_root_still_widens_to_its_parent_directory(self, tmp_path):
        root_file = tmp_path / "mylib" / "api.h"
        root_file.parent.mkdir(parents=True)
        root_file.write_text("", encoding="utf-8")
        sibling = tmp_path / "mylib" / "detail" / "internal.h"
        sibling.parent.mkdir(parents=True)
        sibling.write_text("", encoding="utf-8")

        assert is_dependency_header(str(sibling), [str(root_file)]) is False


class TestIsDependencyHeaderFlatInstalledFileRoot:
    """Regression coverage for a Codex-review P1 finding: a file root
    installed flat in a system prefix (e.g. ``-H /usr/include/zlib.h``)
    must not widen its parent (the bare, unqualified ``/usr/include``) into
    a project directory -- unlike a root under its own subdirectory there
    (``-H /usr/include/mylib/api.h``), the bare system prefix has nothing
    project-specific about it, and treating it as project-owned would let
    every unrelated system header underneath match too."""

    def test_flat_installed_root_does_not_widen_bare_system_prefix(self):
        root = "/usr/include/zlib.h"
        assert is_dependency_header("/usr/include/c++/11/string", [root]) is True

    def test_flat_installed_root_itself_still_kept(self):
        root = "/usr/include/zlib.h"
        assert is_dependency_header(root, [root]) is False

    def test_subdirectory_installed_root_still_widens_its_own_parent(self):
        # The already-fixed case (TestInstalledLibraryUnderSystemPrefix):
        # a root under its own project subdirectory of a system prefix
        # must still widen to that subdirectory, unaffected by this fix.
        root = "/usr/include/mylib/api.h"
        sibling = "/usr/include/mylib/detail/internal.h"
        assert is_dependency_header(sibling, [root]) is False


class TestAbsolutizeHeaderRoot:
    """Regression coverage for a real Windows CI failure: an earlier version
    of this fix called ``Path(h).resolve()`` unconditionally on every root,
    including already-rooted ones. On Windows, resolving a POSIX-style
    already-rooted string (``/usr/include/mylib/api.h``, the convention this
    test suite -- and any snapshot produced on Linux/macOS -- uses)
    drive-anchors it to the current working directory's drive, producing a
    segment sequence that no longer matches that same string's own
    (never-resolved) form as a declaration's ``source_header``. Only a
    genuinely relative root should be absolutized."""

    def test_posix_rooted_path_returned_unchanged(self):
        assert _absolutize_header_root("/usr/include/mylib/api.h") == Path(
            "/usr/include/mylib/api.h"
        )

    def test_windows_drive_rooted_path_returned_unchanged(self):
        assert _absolutize_header_root("C:\\project\\include\\api.h") == Path(
            "C:\\project\\include\\api.h"
        )

    def test_relative_path_is_resolved_against_cwd(self):
        result = _absolutize_header_root("include/api.h")
        assert result.is_absolute()
        assert result == Path("include/api.h").resolve()

    def test_dependency_check_stable_for_posix_rooted_root_and_sibling(self):
        # The end-to-end regression this unit covers: a POSIX-rooted root and
        # an unresolved sibling source_header must still match consistently,
        # regardless of platform.
        root = "/usr/include/mylib/api.h"
        sibling = "/usr/include/mylib/detail/internal.h"
        assert is_dependency_header(sibling, [root]) is False


# ── _is_bare_system_dir: conda-forge/pixi toolchain include roots ────────────


class TestIsBareSystemDirToolchainIncludeRoots:
    """A directory root that IS a compiler's own private include tree
    (nothing project-specific appended after it) must not become a project
    directory when widened from a flat `-H <file>` root (Codex review, D5)."""

    def test_bare_gcc_private_include_dir(self):
        from abicheck.provenance import _is_bare_system_dir, _segments

        segs = _segments(
            "/home/runner/.pixi/envs/scanner/lib/gcc/"
            "x86_64-conda-linux-gnu/14.3.0/include"
        )
        assert _is_bare_system_dir(segs) is True

    def test_bare_gcc_include_fixed_dir(self):
        from abicheck.provenance import _is_bare_system_dir, _segments

        segs = _segments(
            "/home/runner/.pixi/envs/scanner/lib/gcc/"
            "x86_64-conda-linux-gnu/14.3.0/include-fixed"
        )
        assert _is_bare_system_dir(segs) is True

    def test_bare_libstdcxx_version_dir(self):
        from abicheck.provenance import _is_bare_system_dir, _segments

        segs = _segments(
            "/home/runner/.pixi/envs/scanner/lib/gcc/"
            "x86_64-conda-linux-gnu/14.3.0/include/c++"
        )
        assert _is_bare_system_dir(segs) is True

    def test_traditional_debian_style_split_libstdcxx_dir_is_bare(self):
        # Codex review, real finding: the traditional (non-conda-forge)
        # Debian/Ubuntu-style layout keeps libstdc++ separately at
        # /usr/include/c++/<version>/, not nested under lib/gcc/ at all.
        # An explicit -I /usr/include/c++/12 was NOT excluded by the
        # exact-bare-boundary check (it isn't a suffix of any
        # _SYSTEM_HEADER_DIRS entry, and the lib/gcc-anchored toolchain
        # check doesn't apply since there's no lib/gcc/ prefix here) --
        # letting the whole libstdc++ tree underneath be promoted to
        # PUBLIC_HEADER via an explicit -I, since classify_origin checks
        # the public match before the system-header one.
        from abicheck.provenance import _is_bare_system_dir, _segments

        segs = _segments("/usr/include/c++/12")
        assert _is_bare_system_dir(segs) is True

    def test_project_own_include_cxx_dir_with_no_system_prefix_is_not_bare(self):
        # The anchored usr/include/c++ recognition above must not regress
        # the false-positive-risk case already covered in
        # test_bare_include_cxx_with_no_toolchain_prefix_is_not_system_header:
        # a project's own "include/c++" directory with no system prefix at
        # all is not swept up.
        from abicheck.provenance import _is_bare_system_dir, _segments

        segs = _segments("/project/include/c++")
        assert _is_bare_system_dir(segs) is False

    def test_bare_clang_builtin_include_dir(self):
        from abicheck.provenance import _is_bare_system_dir, _segments

        segs = _segments("/opt/pixi/envs/scanner/lib/clang/18/include")
        assert _is_bare_system_dir(segs) is True

    def test_subdirectory_under_libstdcxx_tree_is_still_toolchain_owned(self):
        # Codex review, real finding: unlike the fixed-prefix
        # _SYSTEM_HEADER_DIRS case (where a real project CAN legitimately
        # live one level under a generic system prefix, e.g.
        # /usr/include/mylib), nothing can legitimately live under a
        # compiler's own private include tree -- bits/, ext/, backward/,
        # and every other subdirectory reachable from
        # lib/gcc/<triple>/<version>/include/c++/ are toolchain-owned too,
        # never a project's. An explicit -I pointed at such a subdirectory
        # must still be excluded from becoming a public directory (an
        # earlier revision of this fix only excluded the exact bare
        # boundary, leaving a subdirectory reachable and therefore
        # promotable to PUBLIC_HEADER).
        from abicheck.provenance import _is_bare_system_dir, _segments

        segs = _segments(
            "/home/runner/.pixi/envs/scanner/lib/gcc/"
            "x86_64-conda-linux-gnu/14.3.0/include/c++/bits"
        )
        assert _is_bare_system_dir(segs) is True

    def test_unrelated_project_dir_is_not_bare(self):
        from abicheck.provenance import _is_bare_system_dir, _segments

        segs = _segments("/home/runner/.pixi/envs/scanner/include/myproject")
        assert _is_bare_system_dir(segs) is False
