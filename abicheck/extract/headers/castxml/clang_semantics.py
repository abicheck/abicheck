# SPDX-License-Identifier: Apache-2.0
"""What a castxml root carries from its clang join, and how the parser reads it.

A leaf of :mod:`.clang_semantics_join` (which runs the clang parse): it does
not import the clang backend, so ``extract/header_ast_fields.py`` can read a
castxml root's concept and template-default tables without an import cycle through the backend
protocol. See the join module's docstring for what the facts are.
"""

from __future__ import annotations

import json
from typing import Any
from xml.etree.ElementTree import Element

from ....model.template_default_table import TemplateDefaults
from ..clang.concept_constraints import public_concept_constraints
from ..clang.template_default_entries import (
    TemplateDefaultEntry,
    public_template_defaults,
)

__all__ = [
    "SEMANTICS_ATTR",
    "CastxmlClangSemantics",
    "castxml_concept_constraints",
    "castxml_template_defaults",
    "load_clang_semantics",
    "valid_template_default_entries",
]

#: Root attribute carrying the joined facts. The parsed tree itself is never written.
SEMANTICS_ATTR = "abicheck-clang-semantics"


class CastxmlClangSemantics:
    """The joined facts as the castxml parser reads them."""

    __slots__ = ("concepts", "mention_files", "noexcept", "template_defaults")

    def __init__(
        self,
        noexcept: dict[str, bool] | None = None,
        mention_files: frozenset[str] = frozenset(),
        concepts: list[tuple[str, str, str]] | None = None,
        template_defaults: list[TemplateDefaultEntry] | None = None,
    ) -> None:
        self.noexcept = noexcept
        self.mention_files = mention_files
        self.concepts = concepts
        self.template_defaults = template_defaults

    def function_noexcept(
        self,
        names: tuple[str, ...],
        file_name: str,
        castxml_value: bool,
        *,
        artificial: bool = False,
    ) -> bool | None:
        """``Function.is_noexcept`` for one castxml function, looked up by its
        candidate *names* (castxml's guessed mangling, the final one, then a
        constructor/destructor's :func:`special_member_key`): clang's reading
        when the join has one, ``None`` (unknown) when the declaring file can
        spell a specification the join did not read, else castxml's value.

        A compiler-generated (``artificial``) member keeps castxml's value: its
        specification is deduced from the members, not spelled in the header,
        and the clang backend does not record one either."""
        if artificial:
            return castxml_value
        if self.noexcept is not None:
            for name in names:
                if name and name in self.noexcept:
                    return self.noexcept[name]
        if file_name in self.mention_files:
            return None
        return castxml_value


def load_clang_semantics(root: Element) -> CastxmlClangSemantics:
    """The facts :func:`join_clang_semantics` carried on *root* (none when absent)."""
    raw = root.get(SEMANTICS_ATTR)
    if not raw:
        return CastxmlClangSemantics()
    loaded = json.loads(raw)
    noexcept = loaded.get("noexcept")
    concepts = loaded.get("concepts")
    return CastxmlClangSemantics(
        noexcept=None
        if noexcept is None
        else {str(k): bool(v) for k, v in noexcept.items()},
        mention_files=frozenset(str(f) for f in loaded.get("mention_files") or ()),
        concepts=None
        if concepts is None
        else [(str(e[0]), str(e[1]), str(e[2])) for e in concepts],
        template_defaults=valid_template_default_entries(
            loaded.get("template_defaults")
        ),
    )


def valid_template_default_entries(raw: Any) -> list[TemplateDefaultEntry] | None:
    """The ``template_defaults`` entries of a join sidecar or root attribute,
    or ``None`` when absent or malformed (not modelled)."""
    if not isinstance(raw, list):
        return None
    out: list[TemplateDefaultEntry] = []
    for entry in raw:
        if not (isinstance(entry, list) and len(entry) == 3):
            return None
        key, defaults, header = entry
        if not (isinstance(key, str) and isinstance(header, str)):
            return None
        if defaults is None:
            out.append((key, None, header))
            continue
        if not isinstance(defaults, list) or not all(
            d is None or isinstance(d, str) for d in defaults
        ):
            return None
        out.append((key, tuple(defaults), header))
    return out


def castxml_concept_constraints(
    root: object,
    pub_header_segs: list[tuple[str, ...]],
    pub_dir_segs: list[tuple[str, ...]],
    have_public_set: bool,
) -> dict[str, str] | None:
    """The public headers' concept/constraint table a castxml *root* carries
    from its clang join, filtered by the parse's public set; ``None`` when no
    join read them or no public set is declared (not modelled)."""
    if not isinstance(root, Element) or not have_public_set:
        return None
    entries = load_clang_semantics(root).concepts
    if entries is None:
        return None
    return public_concept_constraints(entries, pub_header_segs, pub_dir_segs)


def castxml_template_defaults(
    root: object,
    pub_header_segs: list[tuple[str, ...]],
    pub_dir_segs: list[tuple[str, ...]],
    have_public_set: bool,
) -> dict[str, TemplateDefaults] | None:
    """The public headers' template-default table a castxml *root* carries
    from its clang join; ``None`` when no join read it or no public set is
    declared (not modelled)."""
    if not isinstance(root, Element) or not have_public_set:
        return None
    entries = load_clang_semantics(root).template_defaults
    if entries is None:
        return None
    return public_template_defaults(entries, pub_header_segs, pub_dir_segs)
