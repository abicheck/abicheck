"""Unit tests for abicheck.header_utils: implicit header includes, public header input splitting and inferred header roots."""

from __future__ import annotations

from pathlib import Path

import pytest

# ── _implicit_header_includes() (P3: -H umbrella resolves without -I) ────────


class TestImplicitHeaderIncludes:
    def test_directory_input_is_its_own_root(self, tmp_path):
        from abicheck.header_utils import _implicit_header_includes

        inc = tmp_path / "include"
        inc.mkdir()
        assert _implicit_header_includes([inc]) == [inc]

    def test_file_at_root_adds_parent(self, tmp_path):
        from abicheck.header_utils import _implicit_header_includes

        inc = tmp_path / "include"
        inc.mkdir()
        umb = inc / "dnnl.hpp"
        umb.write_text("// umbrella")
        assert _implicit_header_includes([umb]) == [inc]

    def test_nested_umbrella_adds_include_root_ancestor(self, tmp_path):
        # include/oneapi/tbb.h → both its parent (include/oneapi) and the
        # conventional include root (include/) must be on the search path so
        # `#include "oneapi/tbb/..."` resolves.
        from abicheck.header_utils import _implicit_header_includes

        root = tmp_path / "include"
        nested = root / "oneapi"
        nested.mkdir(parents=True)
        umb = nested / "tbb.h"
        umb.write_text("// umbrella")
        dirs = _implicit_header_includes([umb])
        assert nested in dirs
        assert root in dirs

    def test_namespace_directory_adds_include_root_ancestor(self, tmp_path):
        # A -H *directory* nested under a conventional root, e.g.
        # `-H include/oneapi`, must add BOTH itself and the include root —
        # headers inside still `#include "oneapi/..."` relative to include/.
        from abicheck.header_utils import _implicit_header_includes

        root = tmp_path / "include"
        nested = root / "oneapi"
        nested.mkdir(parents=True)
        dirs = _implicit_header_includes([nested])
        assert nested in dirs
        assert root in dirs

    def test_deduplicates(self, tmp_path):
        from abicheck.header_utils import _implicit_header_includes

        inc = tmp_path / "include"
        inc.mkdir()
        (inc / "a.h").write_text("")
        (inc / "b.h").write_text("")
        # Two files in the same dir → the root appears once.
        assert _implicit_header_includes([inc / "a.h", inc / "b.h"]) == [inc]

    def test_skips_nonexistent_parent(self, tmp_path):
        # A -H file whose parent dir does not exist contributes nothing.
        from abicheck.header_utils import _implicit_header_includes

        ghost = tmp_path / "absent" / "x.h"
        assert _implicit_header_includes([ghost]) == []


class TestSplitPublicHeaderInputs:
    """A `-H`/`--header`/`--devel-pkg` input may name a file or a whole
    directory; the two must route to resolve_input's public_headers vs.
    public_header_dirs separately (real CI incident, comparability.py's
    scope_fingerprint common-root leak when a directory entry was
    fingerprinted as an individual file identity)."""

    def test_all_files(self, tmp_path):
        from abicheck.header_utils import split_public_header_inputs

        a = tmp_path / "a.h"
        a.write_text("// a")
        b = tmp_path / "b.h"
        b.write_text("// b")
        files, dirs = split_public_header_inputs([a, b])
        assert files == [a, b]
        assert dirs == []

    def test_all_directories(self, tmp_path):
        from abicheck.header_utils import split_public_header_inputs

        d1 = tmp_path / "include"
        d1.mkdir()
        d2 = tmp_path / "usr" / "include"
        d2.mkdir(parents=True)
        files, dirs = split_public_header_inputs([d1, d2])
        assert files == []
        assert dirs == [d1, d2]

    def test_mixed_files_and_directories_preserve_relative_order(self, tmp_path):
        from abicheck.header_utils import split_public_header_inputs

        f = tmp_path / "a.h"
        f.write_text("// a")
        d = tmp_path / "include"
        d.mkdir()
        files, dirs = split_public_header_inputs([f, d])
        assert files == [f]
        assert dirs == [d]

    def test_empty_input(self):
        from abicheck.header_utils import split_public_header_inputs

        assert split_public_header_inputs([]) == ([], [])


class TestResolveInferredHeaderRoots:
    def _umbrella(self, tmp_path):
        root = tmp_path / "include"
        (root / "oneapi").mkdir(parents=True)
        umb = root / "oneapi" / "tbb.h"
        umb.write_text("// umbrella")
        return root, umb

    def test_no_build_context_uses_plain_I(self, tmp_path):
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        inc, toks = resolve_inferred_header_roots([umb], [])
        assert root in inc and toks == []

    def test_isystem_context_defers_via_isystem(self, tmp_path):
        # A build-context -isystem makes the inferred root defer — emitted as
        # -isystem (below build context, above standard system dirs), not -I.
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        inc, toks = resolve_inferred_header_roots(
            [umb], [], gcc_option_tokens=("-isystem", "/gen")
        )
        assert inc == []
        # every inferred root is emitted as -isystem (not -I, not -idirafter)
        assert str(root) in toks
        assert toks[toks.index(str(root)) - 1] == "-isystem"
        assert "-idirafter" not in toks and "-I" not in toks

    def test_gcc_options_include_string_detected(self, tmp_path):
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        inc, toks = resolve_inferred_header_roots(
            [umb], [], gcc_options="-I /build/include -O2"
        )
        assert inc == [] and str(root) in toks

    @pytest.mark.parametrize(
        ("tok", "want"),
        [
            ("/Ibuild\\generated", "/I"),
            ("/external:Igen", "/external:I"),
            ("/imsvc", "/imsvc"),
        ],
    )
    def test_msvc_slash_I_context_detected(self, tmp_path, tok, want):
        # An MSVC/clang-cl build context (/I, /external:I, /imsvc) must also count
        # as build context so the inferred root defers instead of shadowing it,
        # and in the MSVC dialect (never GNU -isystem, which cl.exe/clang-cl
        # would ignore). The deferred bucket mirrors the context's own lowest
        # bucket so the root can't shadow /external:I//imsvc system dirs (#454):
        # a plain /I context stays /I; a system-bucket context echoes it.
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        inc, toks = resolve_inferred_header_roots([umb], [], gcc_option_tokens=(tok,))
        assert inc == []  # detected as build context → deferred
        assert str(root) in toks
        assert toks[toks.index(str(root)) - 1] == want
        assert "-isystem" not in toks

    def test_msvc_system_bucket_root_does_not_shadow(self, tmp_path):
        # #454 item 3: when the MSVC context uses a system bucket, the deferred
        # root must echo that bucket (not collapse to /I, which clang-cl lowers
        # to -I and searches *above* the /external:I//imsvc system dirs). With
        # both a plain /I and a system bucket present, the system bucket wins so
        # the root sits below every build-context include dir.
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        _, ext = resolve_inferred_header_roots(
            [umb], [], gcc_options="/I build\\gen /external:I third_party"
        )
        assert ext[ext.index(str(root)) - 1] == "/external:I"
        _, imsvc = resolve_inferred_header_roots(
            [umb], [], gcc_options="/I build\\gen /imsvc clang_sys"
        )
        assert imsvc[imsvc.index(str(root)) - 1] == "/imsvc"
        # When both appear, the *lowest-searched* bucket wins: clang-cl searches
        # /imsvc (%INCLUDE%-style) dirs after /external:I, so deferring into
        # /imsvc keeps the root below the build's /imsvc dirs too (Codex review).
        # A context using /imsvc is necessarily clang-cl, so /imsvc is supported.
        _, both = resolve_inferred_header_roots(
            [umb], [], gcc_options="/imsvc a /external:I b"
        )
        assert both[both.index(str(root)) - 1] == "/imsvc"

    def test_msvc_bucket_not_fooled_by_include_operand(self, tmp_path):
        # A spaced /I operand that merely *starts with* a bucket name (a dir
        # literally called /imsvc-sdk) must NOT be read as an /imsvc flag — the
        # only real flag here is /I, so the deferred root stays /I (CodeRabbit
        # review). Picking /imsvc would emit a flag cl.exe rejects.
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        _, toks = resolve_inferred_header_roots(
            [umb], [], gcc_option_tokens=("/I", "/imsvc-sdk")
        )
        assert toks[toks.index(str(root)) - 1] == "/I"

        # The mirror case: a *GNU* -I context whose operand dir starts with a
        # slash spelling must not be misclassified as MSVC (dialect detection
        # filters operands too) — it stays the GNU -isystem bucket.
        _, gnu_toks = resolve_inferred_header_roots(
            [umb], [], gcc_option_tokens=("-I", "/imsvc-sdk")
        )
        assert gnu_toks[gnu_toks.index(str(root)) - 1] == "-isystem"

    def test_deferred_flag_dialect_matches_build_context(self, tmp_path):
        # The deferred flag matches the build context's lowest include bucket:
        # above-system GNU (-I/-isystem) → -isystem; MSVC /I → /I; an
        # -idirafter-only context → -idirafter (so its below-system fallback
        # keeps priority instead of being shadowed by an -isystem root).
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        _, gnu = resolve_inferred_header_roots([umb], [], gcc_options="-I /build/gen")
        assert gnu[gnu.index(str(root)) - 1] == "-isystem"
        _, msvc = resolve_inferred_header_roots([umb], [], gcc_options="/I build\\gen")
        assert msvc[msvc.index(str(root)) - 1] == "/I"
        _, after = resolve_inferred_header_roots(
            [umb], [], gcc_options="-idirafter /build/gen"
        )
        assert after[after.index(str(root)) - 1] == "-idirafter"
        assert "-isystem" not in after

    def test_mixed_above_system_and_idirafter_defaults_to_isystem(self, tmp_path):
        # #454 item 2: a mixed GNU context (-I + -idirafter) is unsatisfiable
        # with a single flag — the root can't be both above the system dirs
        # (to win the -I-context basename collision) and below -idirafter.
        # -isystem is the documented default (favors the common collision
        # case; compile DBs essentially never emit -idirafter). This locks
        # that choice in so a refactor can't silently flip it to -idirafter.
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        _, mixed = resolve_inferred_header_roots(
            [umb], [], gcc_options="-I /build/primary -idirafter /build/generated"
        )
        assert mixed[mixed.index(str(root)) - 1] == "-isystem"
        assert "-idirafter" not in mixed

    def test_root_already_in_build_context_is_skipped(self, tmp_path):
        # A root the build context already supplies as -I must NOT be re-added as
        # -isystem (GCC would then ignore the build's -I). Here the build provides
        # the include root; only the *other* inferred ancestor (oneapi) defers.
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)  # include/, umbrella at include/oneapi
        nested = root / "oneapi"
        inc, toks = resolve_inferred_header_roots([umb], [], gcc_options=f"-I {root}")
        assert inc == []
        # the include root is in the build context → not re-emitted at all
        assert str(root) not in toks
        # the nested ancestor is not in the build context → deferred via -isystem
        assert str(nested) in toks
        assert toks[toks.index(str(nested)) - 1] == "-isystem"

    def test_build_context_dir_attached_form_deduped(self, tmp_path):
        # The attached spelling (-I<dir>) is parsed too, so the root is skipped.
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        inc, toks = resolve_inferred_header_roots(
            [umb], [], gcc_option_tokens=(f"-I{root}",)
        )
        assert str(root) not in toks

    def test_deferred_token_dirs_extracts_isystem_paths(self):

        from abicheck.header_utils import deferred_token_dirs

        toks = ["-isystem", "/a/include", "-isystem", "/b/oneapi"]
        assert deferred_token_dirs(toks) == [Path("/a/include"), Path("/b/oneapi")]
        assert deferred_token_dirs([]) == []

    def test_dangling_include_flag_no_operand(self, tmp_path):
        # A bare -I with no following dir (build context present but supplies no
        # parsable dir) still defers the inferred roots via -isystem, no crash.
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        inc, toks = resolve_inferred_header_roots([umb], [], gcc_option_tokens=("-I",))
        assert inc == []
        assert str(root) in toks
        assert toks[toks.index(str(root)) - 1] == "-isystem"

    def test_non_include_options_are_not_build_context(self, tmp_path):
        # -O2/-DNDEBUG add no include dir, so the inferred root stays a plain -I.
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        inc, toks = resolve_inferred_header_roots(
            [umb], [], gcc_options="-O2 -DNDEBUG", gcc_option_tokens=("-Wall",)
        )
        assert root in inc and toks == []

    def test_user_include_deduped(self, tmp_path):
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        nested = root / "oneapi"
        inc, toks = resolve_inferred_header_roots([umb], [nested])
        # nested came from the user -I; only the include root is inferred-added.
        assert nested not in inc and root in inc

    def test_no_inferred_roots_returns_empty(self, tmp_path):
        # A -H file with a nonexistent parent yields no inferred roots → no flags
        # of either kind (and no spurious -isystem even with a build context).
        from abicheck.header_utils import resolve_inferred_header_roots

        ghost = tmp_path / "absent" / "x.h"
        assert resolve_inferred_header_roots(
            [ghost], [], gcc_option_tokens=("-isystem", "/x")
        ) == ([], [])

    def test_malformed_gcc_options_falls_back_to_plain_split(self, tmp_path):
        # An unbalanced quote makes shlex.split raise; we fall back to str.split
        # so an -I in a malformed --gcc-options string is still detected.
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        inc, toks = resolve_inferred_header_roots([umb], [], gcc_options='-I "/broken')
        assert inc == [] and str(root) in toks
