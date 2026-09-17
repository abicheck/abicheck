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

"""The release-level *public surface* -- one product contract, many binary
providers.

A multi-library release has **one** public API contract (its installed
headers) backed by **several** binary providers (its DSOs). Before this
module the release fan-out modelled it as a Cartesian product instead: each
member was compared against the *complete* product header surface
independently, so every declaration another member provides was reported
missing from this one. On a 28-library Intel MKL release that produced
787,833 ``public_not_exported`` findings, 218,689 ``exported_not_public``
findings, a 1.6 GB JSON report and 26.5 GB peak RSS -- none of it a real
compatibility signal (a ScaLAPACK declaration such as ``BDLAAPP`` was
demanded from ``libmkl_rt`` although a sibling MKL library exports it).

The types here are the shape of the corrected model, and nothing more:

* :class:`SurfaceAcquisitionIdentity` -- every input that can change the
  header AST, folded into one stable key. Two members whose acquisition
  request is identical share one acquired surface; two members whose
  request genuinely differs get their own, keyed rather than forced onto
  one snapshot.
* :class:`PublicObligation` -- one public declaration that promises a
  dynamic symbol, with the evidence (declared name, source location) a
  finding needs.
* :class:`ReleasePublicSurface` -- the acquired surface itself: the
  obligations, the symbols the headers declare, and whether the
  acquisition resolved at all.

Deliberately inert value types (``model`` may import nothing): acquisition
lives in ``workflows.release_surface_acquisition``, the export index in
``compare.bundle_export_index``, the reconciliation in
``policy.release_contract_reconciliation``, and the report section in
``report.release_public_surface``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace

#: Bumped whenever :meth:`SurfaceAcquisitionIdentity.key` changes what it
#: folds in or how. Part of the key itself, so an abicheck upgrade that
#: widens the identity can never reuse a surface acquired under the
#: narrower one -- the same discipline ``dumper_ast_config._cache_key``'s
#: own ``_CLANG_CACHE_SCHEMA_VERSION`` applies to the AST cache.
SURFACE_ACQUISITION_IDENTITY_VERSION = 1


@dataclass(frozen=True)
class SurfaceAcquisitionIdentity:
    """Everything about a header-acquisition request that can change the AST.

    Two release members asking for the *same* public surface must share one
    acquisition; two members whose request genuinely differs (a different
    ``--lang``, a different sysroot, a different exclusion set) must not be
    forced onto one snapshot. That distinction is this type: reuse is keyed
    on :meth:`key`, never assumed from "same release".

    Every field is a plain, already-normalized value so the key is stable
    across processes and platforms -- paths are resolved strings, sequences
    are ordered tuples, and nothing here holds a live object. An input this
    type does *not* carry cannot influence reuse, so adding a new
    AST-affecting dump input means adding it here and bumping
    :data:`SURFACE_ACQUISITION_IDENTITY_VERSION`.
    """

    #: Public header *files* named explicitly (``-H file``).
    header_files: tuple[str, ...] = ()
    #: Public header *roots* (``-H dir``), which also drive provenance.
    header_dirs: tuple[str, ...] = ()
    #: ``--exclude-header`` rules. Part of the identity because two sides
    #: narrowed differently are not the same surface at all (and a pair so
    #: narrowed is refused outright downstream).
    exclude_headers: tuple[str, ...] = ()
    #: ``-I`` include search paths.
    includes: tuple[str, ...] = ()
    #: ``scope.public_header_dirs`` / ``--public-header-dir``.
    public_header_dirs: tuple[str, ...] = ()
    #: The resolved language request and whether the caller stated it (the
    #: same pair ``ResolvedDumpRequest`` carries: an *inferred* "c++" and an
    #: explicit ``--lang c++`` are different requests).
    lang: str = ""
    lang_explicit: bool = False
    #: Header-AST backend (``castxml``/``clang``/``auto``/``hybrid``).
    backend: str = "auto"
    #: ``host`` or ``device`` (DPC++ multi-context evidence).
    frontend_context: str = "host"
    #: The compile context's own AST-affecting values, as sorted pairs:
    #: compiler, gcc path/prefix, option string and tokens, sysroot,
    #: ``-nostdinc``, and any defines the caller resolved.
    compile_options: tuple[tuple[str, str], ...] = ()
    #: The requested evidence depth, which decides what a dump collects.
    depth: str | None = None
    #: Whether toolchain/system declarations are kept (``dumper_scoping``).
    include_dependencies: bool = False
    #: Digest of the build configuration in effect, when one was resolved.
    build_config_digest: str | None = None

    def key(self) -> str:
        """A stable content key for this acquisition request."""
        payload = {
            "version": SURFACE_ACQUISITION_IDENTITY_VERSION,
            "header_files": list(self.header_files),
            "header_dirs": list(self.header_dirs),
            "exclude_headers": list(self.exclude_headers),
            "includes": list(self.includes),
            "public_header_dirs": list(self.public_header_dirs),
            "lang": self.lang,
            "lang_explicit": self.lang_explicit,
            "backend": self.backend,
            "frontend_context": self.frontend_context,
            "compile_options": [list(pair) for pair in self.compile_options],
            "depth": self.depth,
            "include_dependencies": self.include_dependencies,
            "build_config_digest": self.build_config_digest,
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def has_headers(self) -> bool:
        """Whether this request names any header input at all."""
        return bool(self.header_files or self.header_dirs)


@dataclass(frozen=True)
class PublicObligation:
    """One public declaration that promises a dynamic symbol.

    The unit the release-level contract is reconciled in: an obligation is
    satisfied when *any* bundle member exports :attr:`symbol`, and produces
    exactly one release-level finding when no member does. It carries the
    declaration evidence (:attr:`name`, :attr:`source_location`) rather than
    a library name, because a declaration absent everywhere has no owning
    library to name -- inventing one is the defect this model removes.
    """

    #: The expected export spelling (the declaration's mangled name).
    symbol: str
    #: The declared name, as the header spells it.
    name: str
    #: ``"function"`` or ``"variable"``.
    entity: str
    #: ``file:line`` of the declaration, when the parse recorded one.
    source_location: str | None = None

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "symbol": self.symbol,
            "name": self.name,
            "entity": self.entity,
        }
        if self.source_location:
            out["source_location"] = self.source_location
        return out


@dataclass(frozen=True)
class ReleasePublicSurface:
    """One release side's public contract, acquired once.

    :attr:`resolvable` is the honesty gate: an acquisition that produced no
    header evidence, or whose declarations carry no resolvable origin, is
    ``False`` with an :attr:`unresolved_reason` -- never an *empty* surface,
    which would read as "this product promises nothing" and silently turn
    every export into undocumented surface.
    """

    #: :meth:`SurfaceAcquisitionIdentity.key` of the request that produced it.
    acquisition_key: str
    #: ``"old"`` or ``"new"`` -- which release side this surface describes.
    side: str
    #: Declarations promising a dynamic symbol, ordered by symbol.
    obligations: tuple[PublicObligation, ...] = ()
    #: Every symbol spelling a public header declares (documented surface),
    #: which is what an undocumented export is judged against.
    declared_symbols: frozenset[str] = frozenset()
    #: How many header files the acquisition actually parsed.
    header_count: int = 0
    #: Public type/enum/typedef names the surface carries, for traceability
    #: of the type closure the contract depends on.
    type_names: tuple[str, ...] = ()
    resolvable: bool = True
    unresolved_reason: str | None = None

    def for_side(self, side: str) -> ReleasePublicSurface:
        """This same surface, labelled for *side*.

        Two sides whose acquisition request is byte-identical (the common
        case: one shared header tree passed to both) share one acquisition,
        so the reused surface arrives labelled with whichever side asked
        first. Relabelling is honest precisely because the *identity* is
        equal: same key, same evidence -- only the side asking differs.
        Never used to move a surface between two genuinely different
        acquisition keys, which the ledger cannot produce.
        """
        return self if self.side == side else replace(self, side=side)

    def obligation_symbols(self) -> frozenset[str]:
        return frozenset(o.symbol for o in self.obligations)

    def to_dict(self) -> dict[str, object]:
        """Deterministic projection for a report/baseline document."""
        out: dict[str, object] = {
            "acquisition_key": self.acquisition_key,
            "side": self.side,
            "header_count": self.header_count,
            "obligations": [o.to_dict() for o in self.obligations],
            "declared_symbols": sorted(self.declared_symbols),
            "type_names": list(self.type_names),
            "resolvable": self.resolvable,
        }
        if self.unresolved_reason is not None:
            out["unresolved_reason"] = self.unresolved_reason
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> ReleasePublicSurface:
        """Rebuild a surface from :meth:`to_dict` (baseline loading)."""
        raw_obligations = data.get("obligations") or ()
        obligations: list[PublicObligation] = []
        if isinstance(raw_obligations, Iterable) and not isinstance(
            raw_obligations, (str, bytes)
        ):
            for item in raw_obligations:
                if not isinstance(item, Mapping):
                    continue
                obligations.append(
                    PublicObligation(
                        symbol=str(item.get("symbol", "")),
                        name=str(item.get("name", "")),
                        entity=str(item.get("entity", "function")),
                        source_location=(
                            str(item["source_location"])
                            if item.get("source_location")
                            else None
                        ),
                    )
                )
        declared = data.get("declared_symbols") or ()
        type_names = data.get("type_names") or ()
        reason = data.get("unresolved_reason")
        return cls(
            acquisition_key=str(data.get("acquisition_key", "")),
            side=str(data.get("side", "")),
            obligations=tuple(sorted(obligations, key=lambda o: (o.symbol, o.name))),
            declared_symbols=frozenset(str(s) for s in _as_sequence(declared)),
            header_count=_as_int(data.get("header_count", 0)),
            type_names=tuple(str(t) for t in _as_sequence(type_names)),
            resolvable=bool(data.get("resolvable", True)),
            unresolved_reason=str(reason) if reason else None,
        )


def _as_int(value: object) -> int:
    """*value* as a count, or 0 for anything a document could hold instead."""
    try:
        return int(value)  # type: ignore[call-overload,no-any-return]
    except (TypeError, ValueError):
        return 0


def _as_sequence(value: object) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        return ()
    return list(value)


def unresolved_surface(
    *, acquisition_key: str, side: str, reason: str
) -> ReleasePublicSurface:
    """A surface that could not be acquired -- an explicit ``FAILED`` fact.

    Never an empty resolvable surface: "no evidence" and "promises nothing"
    are different states with different outcomes, and conflating them is
    what turns a failed extractor into a clean compatibility claim.
    """
    return ReleasePublicSurface(
        acquisition_key=acquisition_key,
        side=side,
        resolvable=False,
        unresolved_reason=reason,
    )
