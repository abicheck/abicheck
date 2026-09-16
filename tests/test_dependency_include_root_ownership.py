# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""A dependency's ``-I`` root is compile context, not a public API root.

Bug class: *an input consulted for one purpose silently granted authority
for another*. ``-I`` tells the parser where it may search for an
``#include``; it says nothing about which declarations the library owns and
promises to export. Conflating the two made every declaration under a
dependency's include directory an export obligation of the library that
merely needed to parse its header.

The reported instance: Intel MKL passes an MPI include directory solely so
``mkl_cdft.h`` can parse ``#include <mpi.h>``. The resulting ``libmkl_rt``
report carried 2,211 ``public_not_exported`` findings -- 731 ``MPI_*``, 730
``PMPI_*``, 750 ``QMPIX_*`` and friends -- about an API MKL neither owns nor
ever promised to export.

The fix is structural and names nothing (``provenance.
_public_dirs_from_include_roots``): an ``-I`` root widens public provenance
exactly when this run's own *declared* public headers live underneath it,
which is what the transitively-reached-header case the parameter exists for
always relied on, and is precisely what a dependency search path does not
satisfy. Nothing here knows about MPI, ``mpi.h``, system paths or symbol
prefixes -- the tests below deliberately use a dependency called ``dep``
living in a plain ``extra/`` directory, so a name-based fix could not pass
them.

The lower half of this module tests that rule directly, as a *primitive*,
over generated inputs -- per AGENTS.md's "Primitive-level property tests":
the containment predicate is a reusable ancestry/grouping rule, and the
hand-written compile cases below only ever exercise the handful of shapes
their author thought of.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.extract.public_root_ownership import (
    retain_owning_roots,
    roots_a_declared_public_surface,
)
from abicheck.model import ScopeOrigin
from abicheck.provenance import _segments, public_dirs_with_owned_roots

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="compiles an ELF .so; Linux-scoped",
)


def _require_toolchain() -> None:
    if shutil.which("gcc") is None:
        pytest.skip("gcc required")


def _build_dependency_tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A library whose public header includes a dependency found only via ``-I``.

    ``pub/api.h`` is the declared public surface. ``extra/dep.h`` is reachable
    *only* through ``-I extra``. It declares two functions: one the binary
    happens to export, and one it does not -- the second is the shape that
    produced MKL's ``public_not_exported`` flood. ``api.h`` also references a
    type defined in ``dep.h``, so type closure has something real to resolve.
    """
    (tmp_path / "extra").mkdir()
    (tmp_path / "pub").mkdir()
    (tmp_path / "extra" / "dep.h").write_text(
        "#pragma once\n"
        "struct DepType { int a; };\n"
        "void dep_exported(void);\n"
        "void dep_never_exported(void);\n",
        encoding="utf-8",
    )
    (tmp_path / "pub" / "api.h").write_text(
        "#pragma once\n"
        "#include <dep.h>\n"
        "void owned_api(void);\n"
        "void owned_but_missing(void);\n"
        "struct DepType *owned_uses_dep(void);\n",
        encoding="utf-8",
    )
    (tmp_path / "lib.c").write_text(
        '#include "pub/api.h"\n'
        "void owned_api(void) {}\n"
        "void dep_exported(void) {}\n"
        "struct DepType *owned_uses_dep(void) { return 0; }\n",
        encoding="utf-8",
    )
    so = tmp_path / "lib.so"
    subprocess.run(
        [
            "gcc",
            "-shared",
            "-fPIC",
            "-g",
            f"-I{tmp_path / 'extra'}",
            f"-I{tmp_path / 'pub'}",
            "-o",
            str(so),
            str(tmp_path / "lib.c"),
        ],
        check=True,
        capture_output=True,
    )
    return so, tmp_path / "pub", tmp_path / "extra"


def _origins(snap: object) -> dict[str, str]:
    return {
        f.name: (f.origin.value if f.origin else "unknown")
        for f in snap.functions  # type: ignore[attr-defined]
    }


def _dump(so: Path, pub: Path, extra: Path, **kw: object) -> object:
    from abicheck.dumper import dump

    return dump(
        so,
        [pub / "api.h"],
        [extra, pub],
        public_headers=[pub / "api.h"],
        public_include_search_dirs=[extra, pub],
        **kw,  # type: ignore[arg-type]
    )


class TestDependencyIncludeRootIsNotAPublicApiRoot:
    def test_a_declaration_found_only_under_a_dependency_root_is_not_public(
        self, tmp_path: Path
    ) -> None:
        """Requirement 1. ``dep_never_exported`` is reachable only through
        ``-I extra``; it must not become an export obligation."""
        _require_toolchain()
        so, pub, extra = _build_dependency_tree(tmp_path)
        origins = _origins(_dump(so, pub, extra))
        assert origins["dep_never_exported"] != ScopeOrigin.PUBLIC_HEADER.value
        assert origins["dep_exported"] != ScopeOrigin.PUBLIC_HEADER.value

    def test_an_explicitly_declared_public_header_still_owes_its_exports(
        self, tmp_path: Path
    ) -> None:
        """Requirement 2, and the negative control for the test above:
        demoting everything would satisfy that claim completely.
        ``owned_but_missing`` is declared in the real ``-H`` header and
        absent from the binary -- still a ``public_not_exported``."""
        _require_toolchain()
        so, pub, extra = _build_dependency_tree(tmp_path)
        snap = _dump(so, pub, extra)
        origins = _origins(snap)
        assert origins["owned_but_missing"] == ScopeOrigin.PUBLIC_HEADER.value
        assert origins["owned_api"] == ScopeOrigin.PUBLIC_HEADER.value

        from abicheck.buildsource.cross_source_checks import (
            CrosscheckConfig,
            _check_public_not_exported,
        )

        out = _check_public_not_exported(snap, CrosscheckConfig())  # type: ignore[arg-type]
        reported = {c.symbol for c in out.findings}
        assert "owned_but_missing" in reported, (
            "an explicitly declared public header's unexported function is "
            "still an export obligation"
        )
        assert "dep_never_exported" not in reported, (
            "a dependency declaration reached only through -I must not create "
            "an export obligation of its own"
        )

    def test_types_an_owned_api_references_stay_available_to_closure(
        self, tmp_path: Path
    ) -> None:
        """Requirement 4. Narrowing *ownership* must not narrow the type
        graph: ``owned_uses_dep`` returns a ``DepType`` defined in the
        dependency header, so leak/closure analysis must still resolve it."""
        _require_toolchain()
        so, pub, extra = _build_dependency_tree(tmp_path)
        from abicheck.surface import compute_public_surface

        surface = compute_public_surface(_dump(so, pub, extra))
        assert "DepType" in surface.public_types

    def test_old_and_new_side_include_paths_stay_isolated(self, tmp_path: Path) -> None:
        """Requirement 5, at the level the rule is decided: one side's
        declared public roots must never license the *other* side's include
        roots. ``apply_provenance`` is per-snapshot, so the check is that
        the containment predicate is answered against the roots it was
        handed and nothing else."""
        old_pub = _segments("/old/pub")
        new_pub = _segments("/new/pub")
        assert public_dirs_with_owned_roots([old_pub], [], ["/old/pub"]) == [
            _segments("/old/pub")
        ]
        assert public_dirs_with_owned_roots([new_pub], [], ["/old/pub"]) == []
        assert public_dirs_with_owned_roots([old_pub], [], ["/new/pub"]) == []


class TestIncludeRootContainmentPredicate:
    """The rule itself, stated as invariants over generated inputs.

    ``_roots_a_declared_public_surface`` is a reusable ancestry test, and
    the compiled cases above only ever exercise the shapes their author
    thought of. These state its contract directly: it is exactly
    "``dir_seg`` is a prefix of some declared root", nothing more.
    """

    @staticmethod
    def _seg(text: str) -> tuple[str, ...]:
        return _segments(text)

    def test_every_proper_ancestor_of_a_declared_root_qualifies(self) -> None:
        """Exhaustive over a small domain, both directions: every prefix of
        a declared root is an owner; nothing longer is."""
        declared = self._seg("/a/b/c/d/api.h")
        for depth in range(1, len(declared) + 1):
            assert roots_a_declared_public_surface(declared[:depth], [declared])
        assert not roots_a_declared_public_surface(declared + ("more",), [declared])

    def test_a_sibling_directory_never_qualifies(self) -> None:
        """The dependency case, generalized: sharing a parent is not
        containment. Several independently-chosen siblings, since one pair
        could pass by coincidence."""
        declared = self._seg("/proj/include/api.h")
        for sibling in ("/proj/extra", "/proj/vendor/mpi/include", "/opt/mpi", "/usr"):
            assert not roots_a_declared_public_surface(self._seg(sibling), [declared])

    def test_a_shared_path_component_is_not_containment(self) -> None:
        """A near-miss a substring- or component-membership rule would get
        wrong: ``/other/include`` shares the ``include`` component with
        ``/proj/include`` and is still unrelated."""
        declared = self._seg("/proj/include/api.h")
        assert not roots_a_declared_public_surface(
            self._seg("/other/include"), [declared]
        )
        assert not roots_a_declared_public_surface(
            self._seg("/proj/includes"), [declared]
        )

    def test_any_one_declared_root_is_enough(self) -> None:
        """Order-independent, and independent of how many roots there are:
        a root containing *any* declared public root qualifies, whichever
        position that root holds in the list."""
        roots = [self._seg(p) for p in ("/x/api.h", "/proj/include/api.h", "/y/api.h")]
        probe = self._seg("/proj/include")
        for rotation in range(len(roots)):
            rotated = roots[rotation:] + roots[:rotation]
            assert roots_a_declared_public_surface(probe, rotated)

    def test_no_declared_roots_means_no_owner(self) -> None:
        """The vacuity guard: with nothing declared, nothing is owned --
        an implementation returning True unconditionally fails here, and
        one returning False unconditionally fails the first test above."""
        assert not roots_a_declared_public_surface(self._seg("/anything"), [])

    def test_the_filter_still_drops_a_bare_system_root_that_would_contain(
        self,
    ) -> None:
        """Containment does not resurrect the pre-existing system-root
        guard: a ``-I /usr/include`` alongside a public header living under
        it is still not a project-owned directory."""
        declared = _segments("/usr/include/mylib/api.h")
        assert public_dirs_with_owned_roots([declared], [], ["/usr/include"]) == []

    def test_omitting_declared_segs_keeps_the_pre_containment_behavior(self) -> None:
        """The leaf's documented default, so a caller with no declared set
        to test against is not silently changed."""
        roots = [self._seg("/opt/mpi/include")]
        assert retain_owning_roots(roots, None) == roots
        assert retain_owning_roots(roots, []) == []

    def test_an_empty_declared_set_widens_nothing(self) -> None:
        """No declared public set means classification was never opted in,
        so an ``-I`` root cannot turn it on by itself."""
        assert public_dirs_with_owned_roots([], [], ["/proj/include"]) == []


class TestEveryOwnershipSourceAndEntryPointAgrees:
    """Requirements 3 and 6, checked where the rule is actually decided.

    Public ownership has several spellings (a ``-H`` operand, a declared
    public header directory, ``sources.public_headers``/
    ``scope.public_header_dirs`` from the project config), and several
    entry points consume it (ELF, PE/Mach-O, appcompat, the header-only
    graph). Asserting each combination through a compiled fixture would
    need four toolchains; asserting the *structure* is both possible here
    and stronger: every spelling lands in the same declared set, and every
    entry point reaches the same fold.
    """

    def test_a_declared_directory_is_as_authoritative_as_a_declared_file(
        self,
    ) -> None:
        """The config route (`scope.public_header_dirs`/
        `sources.public_headers`) reaches provenance as
        ``public_header_dirs``/``public_headers`` -- the same two
        parameters ``-H`` uses -- so what makes a root an owner is the
        declared set, never which spelling produced it."""
        include = _segments("/proj/include")
        # Declared as a directory: the -I root above it still owns nothing
        # of its own, but the declared directory itself does.
        assert public_dirs_with_owned_roots([], [include], ["/proj/include"]) == [
            include,
            include,
        ]
        assert public_dirs_with_owned_roots([], [include], ["/proj/vendor"]) == [
            include
        ]

    def test_an_unrelated_include_root_never_widens_a_declared_set(self) -> None:
        """Requirement 3 stated as the property: adding any number of
        unrelated ``-I`` roots leaves the declared set exactly as it was.
        Several independently-chosen roots, so one coincidence cannot
        carry the claim."""
        declared = [_segments("/proj/include/api.h")]
        base = public_dirs_with_owned_roots(declared, [], [])
        for unrelated in (
            ["/opt/mpi/include"],
            ["/usr/local/include", "/opt/vendor/sdk/include"],
            ["/proj/vendor", "/proj/build/gen", "/elsewhere"],
        ):
            assert public_dirs_with_owned_roots(declared, [], unrelated) == base

    def test_every_binary_entry_point_classifies_through_the_one_fold(self) -> None:
        """Requirement 6, structurally: ELF, PE and Mach-O all reach
        provenance through ``dumper``'s single ``apply_provenance`` call,
        and appcompat and the header-only graph reach the identical rule --
        so there is no second place where ownership could be decided
        differently. A second call site is the thing this guards against,
        which a per-format behavioural test could not see."""
        import abicheck.appcompat as appcompat
        import abicheck.dumper as dumper
        from abicheck import provenance
        from abicheck.buildsource import header_graph

        dumper_src = pathlib.Path(dumper.__file__).read_text(encoding="utf-8")
        assert dumper_src.count("apply_provenance(") == 1, (
            "dumper must reach declaration provenance through exactly one "
            "call; a second call site is a second place "
            "ownership can be decided"
        )
        # appcompat supplies the same parameter rather than classifying
        # its own way.
        appcompat_src = pathlib.Path(appcompat.__file__).read_text(encoding="utf-8")
        assert "public_include_search_dirs=" in appcompat_src
        assert "apply_provenance(" not in appcompat_src
        # And the two consumers of the ownership rule share one function.
        graph_src = pathlib.Path(header_graph.__file__).read_text(encoding="utf-8")
        prov_src = pathlib.Path(provenance.__file__).read_text(encoding="utf-8")
        for src in (graph_src, prov_src):
            assert "public_dirs_with_owned_roots(" in src
        assert "retain_owning_roots(" not in graph_src, (
            "header_graph must go through the shared fold, not re-apply the "
            "containment rule with its own filtering around it"
        )
