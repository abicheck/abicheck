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
