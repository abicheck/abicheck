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

"""What the release-level public-surface acquisition parses *under*.

The acquisition identity and the reconciliation are
``tests/test_release_public_surface.py``'s subject; this file owns one
narrower claim that turned out to be a distinct, separately-breakable
contract: an AST-affecting input the run resolved must reach the
acquisition's **parse**, not only its key.

Split out of that file (a distinct subject, and that file passed the 1200-
line test maximum) -- the same split ``tests/test_multi_library_assurance.py``
documents for the same two reasons.

Bug class: ``tests/regressions/manifest.py``'s
``release.cartesian_product_contract``, whose own axis
``acquisition_context`` this file covers.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from abicheck.elf_metadata import ElfMetadata, ElfSymbol


@dataclass
class _Member:
    """A bundle member's export evidence, in the compact shape the release
    fan-out really keeps."""

    elf: ElfMetadata | None


def _member(*exports: str) -> _Member:
    return _Member(
        elf=ElfMetadata(
            soname="",
            needed=[],
            symbols=[ElfSymbol(name=n, is_default=True) for n in exports],
            imports=[],
        )
    )


class TestTheResolvedCompileContextReachesTheParse:
    """An AST-affecting input the run resolved must reach the *parse*, not
    only the acquisition key.

    The class, not the one reported input: hashing a compile context into
    the key while parsing without it is the worst of both outcomes --
    differently-configured runs key apart, and every one of them acquires a
    surface that ignores the configuration the member dumps honor. The
    observable consequence is a *lost* finding, not a noisy one: a
    declaration behind ``#ifdef FEATURE`` with ``compile.defines:
    [FEATURE]`` set silently leaves the product's contract, so nothing
    reports its missing export (Codex security review, PR #1328). Reproduced
    end to end before the fix: a two-library release saw 2 obligations
    instead of 3 and exited 0, while the same product as a single member
    reported ``public_not_exported: api_c`` and exited 2.
    """

    #: One entry per AST-affecting `CompileContext` field. A field absent
    #: here is a field this guard does not cover, which is how the next one
    #: would slip through -- `test_every_compile_context_field_is_covered`
    #: fails when the dataclass grows one.
    CONTEXT_FIELDS = {
        "gcc_path": "/opt/toolchain/bin/gcc",
        "gcc_prefix": "aarch64-linux-gnu-",
        "gcc_options": "-DFEATURE",
        "gcc_option_tokens": ("-DFEATURE", "-I/extra"),
        # ADR-074's -D/--define. Its parse effect normally travels as a
        # rendered `-D` token above, but the field is keyed on its own so a
        # context carrying definitions that were never folded into the token
        # tail cannot hash the same as one with no macros.
        "defines": ("FEATURE", "MODE=2"),
        "sysroot": Path("/sysroots/target"),
        "nostdinc": True,
        "frontend": "clang",
        "frontend_context": "device",
    }

    @staticmethod
    def _context(**overrides: object):
        from abicheck.compile_context import CompileContext

        return CompileContext(**overrides)  # type: ignore[arg-type]

    def _acquire_with(
        self, monkeypatch: pytest.MonkeyPatch, context: object, tmp_path: Path
    ):
        """Acquire one surface, returning the kwargs the parser really got."""
        import abicheck.header_only_dump as header_only_dump
        from abicheck.workflows.release_public_surface import (
            reconcile_release_public_surface,
        )

        seen: dict[str, object] = {}

        def _spy(**kwargs: object):
            seen.update(kwargs)
            raise RuntimeError("parse not attempted in this test")

        monkeypatch.setattr(header_only_dump, "build_header_only_snapshot", _spy)
        header = tmp_path / "product.h"
        header.write_text("int api_a(int);\n", encoding="utf-8")
        stage = reconcile_release_public_surface(
            [
                {
                    "library": "libA.so",
                    "verdict": "NO_CHANGE",
                    "_new_bundle_evidence": _member("api_a"),
                },
                {
                    "library": "libB.so",
                    "verdict": "NO_CHANGE",
                    "_new_bundle_evidence": _member("api_b"),
                },
            ],
            expected_members=["libA.so", "libB.so"],
            old_headers=[],
            new_headers=[header],
            old_includes=[],
            new_includes=[],
            lang="c",
            compile_context=context,  # type: ignore[arg-type]
        )
        return seen, stage

    def test_the_context_object_itself_is_handed_to_the_parser(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        context = self._context(**self.CONTEXT_FIELDS)  # type: ignore[arg-type]
        seen, _ = self._acquire_with(monkeypatch, context, tmp_path)
        assert seen["compile"] is context

    def test_the_backend_comes_from_the_resolved_context(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        context = self._context(frontend="clang")
        seen, _ = self._acquire_with(monkeypatch, context, tmp_path)
        assert seen["backend"] == "clang"

    def test_no_context_still_parses_with_the_previous_defaults(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Every pre-existing caller is unchanged: no context means the
        `auto` backend and no compile override, exactly as before."""
        seen, _ = self._acquire_with(monkeypatch, None, tmp_path)
        assert seen["backend"] == "auto"
        assert seen["compile"] is None

    @pytest.mark.parametrize("field,value", sorted(CONTEXT_FIELDS.items()))
    def test_each_field_changes_the_acquisition_key(
        self, field: str, value: object
    ) -> None:
        """The key half of the same invariant, one field at a time: a field
        the key ignores lets two genuinely different acquisitions share one
        surface."""
        from abicheck.workflows.release_public_surface import build_side_identity

        def _identity(context: object) -> str:
            return build_side_identity(
                [Path("/inc")],
                [],
                lang="c",
                exclude_headers=(),
                public_header_dirs=None,
                compile_context=context,  # type: ignore[arg-type]
                depth=None,
                include_dependencies=False,
            ).key()

        assert _identity(self._context(**{field: value})) != _identity(self._context())

    def test_the_oracle_is_not_vacuous(self) -> None:
        """Guards the sweep above: two equal contexts must key *equally*, so
        an identity that simply hashed something random could not pass."""
        from abicheck.workflows.release_public_surface import build_side_identity

        def _identity(context: object) -> str:
            return build_side_identity(
                [Path("/inc")],
                [],
                lang="c",
                exclude_headers=(),
                public_header_dirs=None,
                compile_context=context,  # type: ignore[arg-type]
                depth=None,
                include_dependencies=False,
            ).key()

        assert _identity(self._context()) == _identity(self._context())

    def test_every_compile_context_field_is_covered(self) -> None:
        """A new AST-affecting field must join `CONTEXT_FIELDS`, or this
        guard silently stops covering the thing it exists for."""
        import dataclasses

        from abicheck.compile_context import CompileContext

        assert {f.name for f in dataclasses.fields(CompileContext)} == set(
            self.CONTEXT_FIELDS
        )

    def test_the_key_and_the_parse_agree_on_the_backend(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The sibling defect the same root cause carried: the backend was
        hard-coded `auto` in the key while the parse took its own default,
        so two runs differing only in `compile.frontend` keyed identically.
        One resolver now feeds both."""
        from abicheck.workflows.release_public_surface import (
            build_side_identity,
            resolve_surface_backend,
        )

        context = self._context(frontend="clang")
        seen, _ = self._acquire_with(monkeypatch, context, tmp_path)
        identity = build_side_identity(
            [tmp_path],
            [],
            lang="c",
            exclude_headers=(),
            public_header_dirs=None,
            compile_context=context,  # type: ignore[arg-type]
            depth=None,
            include_dependencies=False,
        )
        assert identity.backend == resolve_surface_backend(context) == seen["backend"]

    def test_a_parser_failure_stays_an_unresolved_surface(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The spy above raises; the stage must still not propagate it."""
        _, stage = self._acquire_with(monkeypatch, self._context(), tmp_path)
        assert stage.new_surface is not None
        assert stage.new_surface.resolvable is False


def _decl(name: str, *, mangled: str = "", public: bool = True, **kw):
    """A header-declared function, as the projection sees one."""
    from abicheck.model.declarations import Function
    from abicheck.model.vocabulary import AccessLevel, ScopeOrigin

    return Function(
        name=name,
        mangled=mangled or name,
        return_type="int",
        params=[],
        access=AccessLevel.PUBLIC,
        origin=ScopeOrigin.PUBLIC_HEADER if public else ScopeOrigin.UNKNOWN,
        is_extern_c=True,
        **kw,
    )


def _header_snapshot(*decls, variables=(), types=(), enums=()):
    from abicheck.model import AbiSnapshot

    return AbiSnapshot(
        library="headers",
        version="1",
        functions=list(decls),
        variables=list(variables),
        types=list(types),
        enums=list(enums),
        from_headers=True,
        header_only=True,
    )


class TestWhatCountsAsAnObligation:
    """`surface_from_snapshot` decides what the product *promises*.

    Exercised directly here, against hand-built snapshots, rather than only
    through a real castxml parse: the projection is what every downstream
    number rests on (how many declarations owe an export, which symbols
    count as documented), and the toolchain-driven path only runs in the
    `integration` lane -- which the coverage lane does not run, so this was
    the largest untested surface in the change.
    """

    def _surface(self, snapshot):
        from abicheck.workflows.release_surface_acquisition import (
            surface_from_snapshot,
        )

        return surface_from_snapshot(snapshot, acquisition_key="k", side="new")

    def test_a_public_declaration_owes_an_export(self) -> None:
        surface = self._surface(_header_snapshot(_decl("api_a")))
        assert [o.symbol for o in surface.obligations] == ["api_a"]
        assert surface.obligations[0].entity == "function"
        assert surface.resolvable is True

    def test_an_unresolvable_origin_is_not_an_empty_surface(self) -> None:
        """With no public-header provenance nothing can be classified, and
        reporting zero obligations would read as "this product promises
        nothing" -- the inversion the whole model refuses."""
        surface = self._surface(_header_snapshot(_decl("api_a", public=False)))
        assert surface.resolvable is False
        assert surface.obligations == ()
        assert "provenance" in (surface.unresolved_reason or "")

    def test_an_inline_declaration_owes_nothing(self) -> None:
        """The obligation predicate is the single-artifact check's own, so a
        declaration that legitimately emits no symbol is not demanded."""
        surface = self._surface(
            _header_snapshot(_decl("api_a"), _decl("helper", is_inline=True))
        )
        assert [o.symbol for o in surface.obligations] == ["api_a"]

    def test_a_declared_symbol_is_documented_even_without_an_obligation(self) -> None:
        """Documented surface and owed surface are different questions: an
        inline declaration documents its symbol without promising it."""
        surface = self._surface(
            _header_snapshot(_decl("api_a"), _decl("helper", is_inline=True))
        )
        assert surface.declared_symbols == frozenset({"api_a", "helper"})

    def test_extern_data_owes_an_export_too(self) -> None:
        from abicheck.model.declarations import Variable
        from abicheck.model.vocabulary import AccessLevel, ScopeOrigin

        surface = self._surface(
            _header_snapshot(
                variables=[
                    Variable(
                        name="api_table",
                        mangled="api_table",
                        type="int",
                        access=AccessLevel.PUBLIC,
                        origin=ScopeOrigin.PUBLIC_HEADER,
                    )
                ]
            )
        )
        assert [(o.symbol, o.entity) for o in surface.obligations] == [
            ("api_table", "variable")
        ]

    def test_a_const_header_constant_owes_nothing(self) -> None:
        from abicheck.model.declarations import Variable
        from abicheck.model.vocabulary import AccessLevel, ScopeOrigin

        surface = self._surface(
            _header_snapshot(
                _decl("api_a"),
                variables=[
                    Variable(
                        name="kLimit",
                        mangled="kLimit",
                        type="int",
                        access=AccessLevel.PUBLIC,
                        origin=ScopeOrigin.PUBLIC_HEADER,
                        is_const=True,
                    )
                ],
            )
        )
        assert [o.symbol for o in surface.obligations] == ["api_a"]

    def test_obligations_are_ordered_deterministically(self) -> None:
        surface = self._surface(
            _header_snapshot(_decl("zeta"), _decl("alpha"), _decl("mid"))
        )
        assert [o.symbol for o in surface.obligations] == ["alpha", "mid", "zeta"]

    def test_the_declaring_headers_are_counted_not_the_declarations(self) -> None:
        """`header_count` answers "how many headers did this parse", so two
        declarations from one header count once."""
        surface = self._surface(
            _header_snapshot(
                _decl("api_a", source_location="/inc/product.h:3"),
                _decl("api_b", source_location="/inc/product.h:4"),
                _decl("api_c", source_location="/inc/extra.h:1"),
            )
        )
        assert surface.header_count == 2

    def test_a_declaration_with_no_location_is_not_counted_as_a_header(self) -> None:
        surface = self._surface(_header_snapshot(_decl("api_a")))
        assert surface.header_count == 0

    def test_public_type_names_ride_the_surface(self) -> None:
        from abicheck.model import RecordType
        from abicheck.model.vocabulary import ScopeOrigin

        surface = self._surface(
            _header_snapshot(
                _decl("api_a"),
                types=[
                    RecordType(
                        name="Cfg", kind="struct", origin=ScopeOrigin.PUBLIC_HEADER
                    ),
                    RecordType(
                        name="Hidden", kind="struct", origin=ScopeOrigin.UNKNOWN
                    ),
                ],
            )
        )
        assert surface.type_names == ("Cfg",)

    def test_the_source_location_rides_the_obligation(self) -> None:
        surface = self._surface(
            _header_snapshot(_decl("api_a", source_location="/inc/product.h:7"))
        )
        assert surface.obligations[0].source_location == "/inc/product.h:7"


class TestAcquisitionFailureIsAFact:
    """An extractor failure is an explicit `FAILED` fact, never an empty
    surface -- so a release cannot read a broken parse as "promises
    nothing"."""

    def _acquire(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, producer):
        import abicheck.header_only_dump as header_only_dump
        from abicheck.model.release_surface import SurfaceAcquisitionIdentity
        from abicheck.workflows.release_surface_acquisition import (
            SurfaceAcquisitionLedger,
            acquire_release_surface,
        )

        monkeypatch.setattr(header_only_dump, "build_header_only_snapshot", producer)
        header = tmp_path / "product.h"
        header.write_text("int api_a(int);\n", encoding="utf-8")
        return acquire_release_surface(
            SurfaceAcquisitionIdentity(header_files=(str(header),)),
            "new",
            ledger=SurfaceAcquisitionLedger(),
            headers=[header],
            includes=[],
            public_headers=[header],
            public_header_dirs=[],
        )

    def test_a_parse_exception_becomes_an_unresolved_surface(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        def _boom(**_: object):
            raise RuntimeError("castxml exploded")

        surface = self._acquire(monkeypatch, tmp_path, _boom)
        assert surface.resolvable is False
        assert "castxml exploded" in (surface.unresolved_reason or "")

    def test_no_header_inputs_is_its_own_stated_reason(self) -> None:
        from abicheck.model.release_surface import SurfaceAcquisitionIdentity
        from abicheck.workflows.release_surface_acquisition import (
            SurfaceAcquisitionLedger,
            acquire_release_surface,
        )

        surface = acquire_release_surface(
            SurfaceAcquisitionIdentity(),
            "new",
            ledger=SurfaceAcquisitionLedger(),
            headers=[],
            includes=[],
            public_headers=[],
            public_header_dirs=[],
        )
        assert surface.resolvable is False
        assert "no public header inputs" in (surface.unresolved_reason or "")

    def test_a_successful_parse_is_projected(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The declaration carries a real `source_location` under the public
        header: `acquire_release_surface` runs provenance before projecting,
        and provenance classifies origin from that location -- so a
        location-less declaration would legitimately come out UNKNOWN (which
        is what the unresolved-surface test above asserts)."""
        header = tmp_path / "product.h"
        snapshot = _header_snapshot(_decl("api_a", source_location=f"{header}:1"))

        def _ok(**_: object):
            return snapshot

        surface = self._acquire(monkeypatch, tmp_path, _ok)
        assert surface.resolvable is True
        assert [o.symbol for o in surface.obligations] == ["api_a"]


class TestTheLedgerRecord:
    """The acquisition ledger is the instrumentation the one-per-side claim
    rests on, so its own record is asserted rather than assumed."""

    def test_it_reports_each_key_once_with_its_reuses(self) -> None:
        from abicheck.model.release_surface import (
            ReleasePublicSurface,
            SurfaceAcquisitionIdentity,
        )
        from abicheck.workflows.release_surface_acquisition import (
            SurfaceAcquisitionLedger,
        )

        ledger = SurfaceAcquisitionLedger()
        identity = SurfaceAcquisitionIdentity(header_dirs=("/inc",))
        made = ReleasePublicSurface(acquisition_key=identity.key(), side="old")
        ledger.acquire(identity, "old", lambda: made)
        ledger.acquire(identity, "new", lambda: made)
        record = ledger.to_dict()
        assert record["acquisitions"] == 1
        assert record["reuses"] == 1
        assert record["keys"] == [
            {"key": identity.key(), "acquisitions": 1, "reuses": 1}
        ]
        assert ledger.get(identity) is made
        assert ledger.acquisitions_by_key() == {identity.key(): 1}

    def test_an_unacquired_identity_is_absent_not_zero(self) -> None:
        from abicheck.model.release_surface import SurfaceAcquisitionIdentity
        from abicheck.workflows.release_surface_acquisition import (
            SurfaceAcquisitionLedger,
        )

        ledger = SurfaceAcquisitionLedger()
        assert ledger.get(SurfaceAcquisitionIdentity(header_dirs=("/inc",))) is None
        assert ledger.to_dict()["keys"] == []
