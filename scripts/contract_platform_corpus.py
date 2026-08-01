#!/usr/bin/env python3
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

"""ADR-049 Phase 6's platform/shape corpus for the contract-mode measurement.

`scripts/measure_contract_shadow.py` originally measured exactly one corpus:
the labelled FP-rate corpus `scripts/check_fp_rate.py` curates. That corpus is
the right *ground truth* -- it already says, per case, whether a finding is
internal noise or a real break -- but it is deliberately built to exercise
public-header scoping, so its snapshots carry no export tables at all. The
consequence was visible in the measurement's own output and recorded honestly
there: the `exports` domain came out **100% unresolved on every case**, which
means the phase's "measured and accepted unresolved rate" was, for that
domain, measuring only the absence of evidence.

Phase 6's Gate names the axes this file supplies:

    ... running case97, pvxs, real-world corpus, ELF/Mach-O/PE, stripped,
    versioned, C/C++, snapshot, package, and downstream renderer/aggregate
    lanes.

Each `Case` here is tagged with a **lane** (:data:`CASE_LANE`) so the
measurement can report unresolved rate *by lane*, not only by domain and
provider state -- which is what turns "exports is 100% unresolved" from a
single opaque number into "exports resolves on every lane that carries an
export table, and is unresolved exactly on the lanes that do not."

**Why a separate corpus rather than more FP-rate cases.** `check_fp_rate.py`'s
corpus is a gate with its own 0/0 FP/FN baselines: every case added there must
be one the *verdict* logic already gets right, and adding platform-shape cases
to it would couple two unrelated gates -- a Mach-O export-table shape has
nothing to say about false-positive rate. These cases carry the same
`internal_noise` label (so the shadow measurement's loss / FP-reduction
counters apply to them identically) without joining that gate.

**What these snapshots are.** Hand-built `AbiSnapshot`s carrying real
platform *metadata shapes* -- an ELF `.dynsym`, a PE export directory, a
Mach-O export trie -- not real compiled binaries. That is deliberate and
sufficient for what is being measured here: the export-surface provider reads
exactly these fields (`export_surface.observed_exports_by_platform`), so a
snapshot carrying them exercises the same provider code a real binary would,
on every host, without a cross-compiler. Real *binaries* are covered one
level up, by `tests/test_scan_compare_parity.py`'s integration lane and the
`examples/` corpus; what cannot be covered without a real toolchain is
recorded as such rather than faked.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from check_fp_rate import Case  # noqa: E402

from abicheck.elf_metadata import ElfMetadata, ElfSymbol  # noqa: E402
from abicheck.macho_metadata import MachoExport, MachoMetadata  # noqa: E402
from abicheck.model import (  # noqa: E402
    AbiSnapshot,
    AccessLevel,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    TypeField,
    Visibility,
)
from abicheck.pe_metadata import PeExport, PeMetadata  # noqa: E402

# Lane names. One per row of Phase 6's Gate list that this corpus can supply
# without a real toolchain; the ones it cannot are named in `UNCOVERED_LANES`
# below rather than silently omitted.
LANE_ELF = "elf"
LANE_PE = "pe"
LANE_MACHO = "macho"
LANE_STRIPPED = "stripped"
LANE_VERSIONED = "versioned"
LANE_C = "c"

#: Lanes Phase 6's Gate names that this corpus deliberately does **not**
#: cover, with the reason -- reported by the measurement so the coverage claim
#: is bounded by what was actually run, not by what the list mentions.
UNCOVERED_LANES: dict[str, str] = {
    "package": (
        "compare rejects --contract-evaluation for directory/package operands "
        "(the same fan-out limitation --contract itself carries), so there is "
        "no contract decision on a package pair to measure"
    ),
    "real_binaries": (
        "needs a compiler; covered by the integration lanes "
        "(tests/test_scan_compare_parity.py, tests/test_abi_examples.py) "
        "rather than by this always-on measurement"
    ),
}


def _fn(
    name: str,
    mangled: str,
    *,
    ret: str = "void",
    params: tuple[tuple[str, str], ...] = (),
    origin: ScopeOrigin = ScopeOrigin.PUBLIC_HEADER,
) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type=ret,
        params=[Param(name=n, type=t) for n, t in params],
        visibility=Visibility.PUBLIC,
        access=AccessLevel.PUBLIC,
        origin=origin,
    )


def _cfg(fields: tuple[tuple[str, str], ...], size_bits: int) -> RecordType:
    return RecordType(
        name="Cfg",
        kind="struct",
        fields=[TypeField(name=n, type=t) for n, t in fields],
        size_bits=size_bits,
        origin=ScopeOrigin.PUBLIC_HEADER,
    )


def _elf(*names: str, versions: dict[str, str] | None = None) -> ElfMetadata:
    versions = versions or {}
    return ElfMetadata(
        soname="libfoo.so.1",
        versions_defined=sorted(set(versions.values())),
        symbols=[ElfSymbol(name=n, version=versions.get(n, "")) for n in names],
    )


def _snap(
    version: str,
    functions: list[Function],
    *,
    types: list[RecordType] | None = None,
    elf: ElfMetadata | None = None,
    pe: PeMetadata | None = None,
    macho: MachoMetadata | None = None,
    from_headers: bool = True,
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so.1",
        version=version,
        from_headers=from_headers,
        functions=functions,
        types=types or [],
        elf=elf,
        pe=pe,
        macho=macho,
    )


# ---------------------------------------------------------------------------
# ELF
# ---------------------------------------------------------------------------


def _elf_exported_break() -> tuple[AbiSnapshot, AbiSnapshot]:
    """A C++ export present in `.dynsym` and in a public header, removed.

    The base case for the `exports` domain: the removed symbol is a real
    export root, so every domain must call it `IN_CONTRACT` -- and, unlike
    every FP-rate case, `exports` can say so from evidence rather than
    reporting unresolved.
    """
    old = _snap(
        "1.0",
        [_fn("pub_a", "_Z5pub_av"), _fn("pub_b", "_Z5pub_bv")],
        elf=_elf("_Z5pub_av", "_Z5pub_bv"),
    )
    new = _snap("2.0", [_fn("pub_a", "_Z5pub_av")], elf=_elf("_Z5pub_av"))
    return old, new


def _elf_type_reached_only_by_unexported() -> tuple[AbiSnapshot, AbiSnapshot]:
    """A public-header type whose layout changes, reached by no export.

    The one case where the two domains are *supposed* to disagree, and the
    `exports` domain's own reason for existing. `Cfg` is declared in a public
    header, so the header provider commits to it and `public` calls the
    layout change `IN_CONTRACT`; but the only signature naming it belongs to
    a function absent from the observed export table, so the export closure
    proves `Cfg` unreachable from any root and `exports` answers
    `PROVEN_OUT_OF_CONTRACT`. Without a case like this the domain's whole
    value proposition -- exclusions header scoping cannot prove -- is
    unmeasured.

    Labelled internal noise: nothing a consumer can link against reaches this
    type, so its layout cannot break one.
    """
    helper = _fn("helper", "_Z6helperP3Cfg", params=(("c", "Cfg*"),))
    old = _snap(
        "1.0",
        [_fn("pub_a", "_Z5pub_av"), helper],
        types=[_cfg((("a", "int"),), 32)],
        elf=_elf("_Z5pub_av"),
    )
    new = _snap(
        "2.0",
        [_fn("pub_a", "_Z5pub_av"), helper],
        types=[_cfg((("a", "int"), ("b", "int")), 64)],
        elf=_elf("_Z5pub_av"),
    )
    return old, new


def _elf_versioned_symbol_break() -> tuple[AbiSnapshot, AbiSnapshot]:
    """A versioned ELF export removed (the `versioned` lane).

    Symbol versioning changes what the export *table* says a name is, so it
    is the one ELF shape whose export roots are not simply the bare names --
    worth measuring separately rather than assuming the unversioned result
    carries over.
    """
    versions = {"_Z5pub_av": "LIBFOO_1.0", "_Z5pub_bv": "LIBFOO_1.0"}
    old = _snap(
        "1.0",
        [_fn("pub_a", "_Z5pub_av"), _fn("pub_b", "_Z5pub_bv")],
        elf=_elf("_Z5pub_av", "_Z5pub_bv", versions=versions),
    )
    new = _snap(
        "2.0",
        [_fn("pub_a", "_Z5pub_av")],
        elf=_elf("_Z5pub_av", versions={"_Z5pub_av": "LIBFOO_1.0"}),
    )
    return old, new


def _elf_extern_c_break() -> tuple[AbiSnapshot, AbiSnapshot]:
    """An unmangled C export removed (the `C` half of the `C/C++` lane).

    Identity resolution for an `extern "C"` entity runs the name-based
    fallback tier rather than the mangled-primary one
    (`finding_identity.resolve_function_identity`), so the domain has to
    match an export root spelled exactly as the declaration is.
    """
    old = _snap(
        "1.0",
        [_fn("c_keep", "c_keep"), _fn("c_drop", "c_drop")],
        elf=_elf("c_keep", "c_drop"),
    )
    new = _snap("2.0", [_fn("c_keep", "c_keep")], elf=_elf("c_keep"))
    return old, new


def _elf_stripped_no_headers() -> tuple[AbiSnapshot, AbiSnapshot]:
    """Exports with no header provenance at all (the `stripped` lane).

    `from_headers=False` and no declared origin: the header provider has
    nothing to search, while the export table is complete. Exactly the case
    where the two domains must diverge -- `public` cannot resolve, `exports`
    can -- and where a provider failure must stay scoped to its own domain
    rather than making the whole run unresolved.
    """
    old = _snap(
        "1.0",
        [
            _fn("pub_a", "_Z5pub_av", origin=ScopeOrigin.UNKNOWN),
            _fn("pub_b", "_Z5pub_bv", origin=ScopeOrigin.UNKNOWN),
        ],
        elf=_elf("_Z5pub_av", "_Z5pub_bv"),
        from_headers=False,
    )
    new = _snap(
        "2.0",
        [_fn("pub_a", "_Z5pub_av", origin=ScopeOrigin.UNKNOWN)],
        elf=_elf("_Z5pub_av"),
        from_headers=False,
    )
    return old, new


# ---------------------------------------------------------------------------
# PE and Mach-O
# ---------------------------------------------------------------------------


def _pe(*names: str) -> PeMetadata:
    return PeMetadata(
        machine="x86_64",
        exports=[PeExport(name=n, ordinal=i + 1) for i, n in enumerate(names)],
    )


def _pe_exported_break() -> tuple[AbiSnapshot, AbiSnapshot]:
    """A PE export-directory entry removed.

    PE names are not Itanium-mangled, and the provider deliberately does not
    run the ELF/Itanium relevance filter over them
    (`export_surface.observed_exports_by_platform`) -- so this lane checks the
    domain against the one table whose names follow a different scheme.
    """
    old = _snap(
        "1.0",
        [_fn("pub_a", "pub_a"), _fn("pub_b", "pub_b")],
        pe=_pe("pub_a", "pub_b"),
    )
    new = _snap("2.0", [_fn("pub_a", "pub_a")], pe=_pe("pub_a"))
    return old, new


def _macho(*names: str) -> MachoMetadata:
    return MachoMetadata(
        cpu_type="arm64",
        install_name="libfoo.1.dylib",
        exports=[MachoExport(name=n) for n in names],
    )


def _macho_exported_break() -> tuple[AbiSnapshot, AbiSnapshot]:
    """A Mach-O export-trie entry removed.

    The third of the three tables the provider reads. Names are stored
    **without** the platform's extra leading underscore: both real readers
    (`macho_metadata`'s export-trie walk and its symbol-table fallback) strip
    it before constructing a `MachoExport`, so a `__Z...` spelling is one no
    real snapshot can contain. An earlier version of this fixture used the
    on-disk spelling and thereby invented a second, impossible export --
    which the measurement duly reported as an extra unresolved finding,
    corrupting the very lane result this case exists to produce (Codex
    review, confirmed against `macho_metadata.py`'s own stripping and by
    re-running the measurement).
    """
    old = _snap(
        "1.0",
        [_fn("pub_a", "_Z5pub_av"), _fn("pub_b", "_Z5pub_bv")],
        macho=_macho("_Z5pub_av", "_Z5pub_bv"),
    )
    new = _snap("2.0", [_fn("pub_a", "_Z5pub_av")], macho=_macho("_Z5pub_av"))
    return old, new


PLATFORM_CORPUS: list[Case] = [
    Case("elf_exported_break", False, _elf_exported_break),
    Case(
        "elf_type_reached_only_by_unexported",
        True,
        _elf_type_reached_only_by_unexported,
    ),
    Case("elf_versioned_symbol_break", False, _elf_versioned_symbol_break),
    Case("elf_extern_c_break", False, _elf_extern_c_break),
    Case("elf_stripped_no_headers", False, _elf_stripped_no_headers),
    Case("pe_exported_break", False, _pe_exported_break),
    Case("macho_exported_break", False, _macho_exported_break),
]

#: Which Gate lane each case supplies. A case can only be in one lane: the
#: lane names a *shape*, and a case that mixed two would make the by-lane
#: unresolved rate unattributable.
CASE_LANE: dict[str, str] = {
    "elf_exported_break": LANE_ELF,
    "elf_type_reached_only_by_unexported": LANE_ELF,
    "elf_versioned_symbol_break": LANE_VERSIONED,
    "elf_extern_c_break": LANE_C,
    "elf_stripped_no_headers": LANE_STRIPPED,
    "pe_exported_break": LANE_PE,
    "macho_exported_break": LANE_MACHO,
}


def lane_of(name: str) -> str:
    """The lane *name* belongs to; ``"fp_corpus"`` for a case from that corpus.

    One function rather than two dicts merged at the call site, so the
    measurement can tag every case it runs without knowing which corpus it
    came from.
    """
    return CASE_LANE.get(name, "fp_corpus")
