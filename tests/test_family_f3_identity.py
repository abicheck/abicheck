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

"""Harness H3 -- environment-metamorphic identity suite (defect family F3,
``docs/contribute/plans/defect-family-harnesses.md``; extends Phase 4 of
``bug-class-regression-testing.md`` / manifest class
``identity.environment_taint``).

The transform catalogue and every oracle live in
``tests/_family_f3_catalogue.py``; this module is the matrix over it:

* string level: every registered identity probe x every path transform
  (relocation, symlinked root, root with a space, ``./``, ``..``, Windows
  separators, relative spelling), plus a negative control per cell;
* codec level: Mach-O ``_``, x86 PE cdecl/stdcall/fastcall/vectorcall,
  Itanium C1/C2/C3 and D0/D1/D2 -- join + distinctness;
* snapshot level (default lane): the production load path is the
  normalizer, oracle is ``NO_CHANGE`` + zero findings + equal identity keys
  + equal report finding ids for a real change pair;
* ``PYTHONHASHSEED`` via subprocess;
* real castxml dumps (``integration``): relocation, symlinked root,
  ``..`` header spelling, reversed header order;
* completeness: every identity-producing function in ``abicheck/`` is in
  ``COVERAGE`` (shrink-only: a stale row fails);
* seeded mutants: historical bugs monkeypatched back in must be reported.

Existing instances this catalogue generalizes (not duplicated here):
``test_identity_taint_end_to_end.py`` (fixture reused for the castxml
cells), ``test_header_graph_hash_seed_determinism.py`` (graph order under
hash seeds), ``test_finding_identity_properties.py``.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
from _mutmut_names import canonical_def_name

from tests import _family_f3_catalogue as cat

REPO = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Known real bugs this harness found (strict xfail -- a fix flips them red).
# Currently none.
# ---------------------------------------------------------------------------

# Formerly: the comparison-only anonymous-type location strip could not
# match a checkout path containing a space (relocate_checkout_with_space
# cells), and the vectorcall decoder stripped a leading "_" that is part of
# the real name. Both are fixed; the cells now run as regular tests.
_XFAIL_STRING: dict[tuple[str, str], str] = {}
_XFAIL_PE: dict[tuple[str, str], str] = {}


def _cell(values: tuple, reasons: dict, key: tuple) -> object:
    reason = reasons.get(key)
    marks = (
        [pytest.mark.xfail(strict=True, raises=AssertionError, reason=reason)]
        if reason
        else []
    )
    return pytest.param(
        *values, marks=marks, id="-".join(str(v).rsplit(":", 1)[-1] for v in values)
    )


# ---------------------------------------------------------------------------
# must_not_change x string-level probes
# ---------------------------------------------------------------------------

_STRING_CELLS = [
    _cell((p.qualname, t.name), _XFAIL_STRING, (p.qualname, t.name))
    for p in cat.STRING_PROBES
    for t in cat.PATH_TRANSFORMS
]


@pytest.mark.parametrize(("probe_name", "transform_name"), _STRING_CELLS)
def test_identity_key_is_invariant_under_path_transform(
    probe_name: str, transform_name: str
) -> None:
    probe = cat.PROBES_BY_NAME[probe_name]
    transform = cat.TRANSFORMS_BY_NAME[transform_name]
    # Vacuity guard: the transform really changed the input spelling.
    assert transform.fn(cat.BASE_HEADER) != cat.BASE_HEADER
    assert cat.string_violations(probe, transform) == []


@pytest.mark.parametrize(
    ("probe_name", "transform_name"),
    [(p.qualname, t.name) for p in cat.STRING_PROBES for t in cat.PATH_TRANSFORMS],
)
def test_distinct_entities_stay_distinct_under_path_transform(
    probe_name: str, transform_name: str
) -> None:
    probe = cat.PROBES_BY_NAME[probe_name]
    assert cat.string_collisions(probe, cat.TRANSFORMS_BY_NAME[transform_name]) == []


@pytest.mark.parametrize(
    ("probe_name", "transform_name"),
    [(p, t.name) for p in sorted(cat._path_probes()) for t in cat.PATH_TRANSFORMS],
)
def test_declaring_path_identity_is_root_relative(
    probe_name: str, transform_name: str
) -> None:
    """Phase 3: a path reaches identity only as a ``RootRelativePath``."""
    assert cat.path_violations(probe_name, cat.TRANSFORMS_BY_NAME[transform_name]) == []


def test_path_transforms_are_all_distinct_spellings() -> None:
    spellings = {t.fn(cat.BASE_HEADER) for t in cat.PATH_TRANSFORMS}
    assert len(spellings) == len(cat.PATH_TRANSFORMS)
    assert all(t.kind == "must_not_change" and t.bugs for t in cat.PATH_TRANSFORMS)


# ---------------------------------------------------------------------------
# codec: decoration schemes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("decoder", sorted(cat.macho_decoders()))
def test_macho_underscore_codec_joins_and_separates(decoder: str) -> None:
    assert cat.macho_violations(decoder) == []


def test_macho_encode_is_injective_over_corpus() -> None:
    names = [n for n, _p, _c in cat._macho_corpus()]
    assert len({cat.macho_encode(n) for n in names}) == len(set(names))


_PE_CELLS = [
    _cell((d, s), _XFAIL_PE, (d, s))
    for d in sorted(cat.pe_decoders())
    for s in cat.PE_SCHEMES
]


@pytest.mark.parametrize(("decoder", "scheme"), _PE_CELLS)
def test_x86_pe_decoration_codec_joins_and_separates(decoder: str, scheme: str) -> None:
    assert cat.pe_violations(decoder, scheme) == []


def test_non_x86_pe_leading_underscore_is_part_of_the_name() -> None:
    assert cat.pe_x64_collisions() == []


@pytest.mark.parametrize("scheme", ["cdecl", "stdcall", "fastcall"])
def test_the_two_pe_decoders_agree_on_x86(scheme: str) -> None:
    decs = cat.pe_decoders()
    enc = cat.PE_SCHEMES[scheme]
    for name in cat.PE_C_NAMES:
        spellings = {d(enc(name, 12)) for d in decs.values()}
        assert spellings == {name}


@pytest.mark.parametrize("decoder", sorted(cat.elf_version_decoders()))
def test_elf_version_codec_joins_and_separates(decoder: str) -> None:
    assert cat.elf_version_violations(decoder) == []


def test_itanium_ctor_dtor_variant_codec_joins_and_separates() -> None:
    assert cat.ctor_dtor_violations() == []
    all_syms = [s for fam in cat.CTOR_FAMILIES.values() for s in fam]
    assert len(all_syms) == len(set(all_syms))  # corpus sanity


def test_entity_id_constructors_keep_similar_shapes_distinct() -> None:
    assert cat.entity_id_collisions() == []


# ---------------------------------------------------------------------------
# Snapshot level (default lane): load path + compare
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def base_pair() -> tuple:
    return cat.build_snapshot(), cat.build_snapshot(changed=True, version="2")


@pytest.mark.parametrize("transform_name", cat.SNAPSHOT_TRANSFORMS)
def test_snapshot_transform_is_no_change(base_pair: tuple, transform_name: str) -> None:
    base, _changed = base_pair
    a = cat.load(cat.snapshot_transform(base, "identity"))
    b = cat.load(cat.snapshot_transform(base, transform_name))
    result = cat.compare(a, b)
    assert result.changes == []
    assert result.verdict.name == "NO_CHANGE"
    assert cat.snapshot_identity_keys(a) == cat.snapshot_identity_keys(b)
    # Negative control: every entity keeps its own identity after the transform.
    assert len(set(cat.snapshot_identity_keys(b))) == cat.entity_count(b)


@pytest.mark.parametrize("transform_name", cat.SNAPSHOT_TRANSFORMS)
def test_report_finding_ids_are_invariant(
    base_pair: tuple, transform_name: str
) -> None:
    base, changed = base_pair
    ref = cat.compare(
        cat.load(cat.snapshot_transform(base, "identity")),
        cat.load(cat.snapshot_transform(changed, "identity")),
    )
    got = cat.compare(
        cat.load(cat.snapshot_transform(base, transform_name)),
        cat.load(cat.snapshot_transform(changed, transform_name)),
    )
    ref_ids, got_ids = cat.finding_ids(ref), cat.finding_ids(got)
    assert ref_ids, "vacuity guard: the change pair must produce findings"
    assert sorted(got_ids) == sorted(ref_ids)
    if transform_name != "reverse_collection_order":
        # A pure spelling change must not even reorder emission. (Emission
        # order under reversed *input* order is not asserted: reports sort.)
        assert got_ids == ref_ids


def _order_violations(runs: int = 4) -> list[str]:
    base, changed = cat.build_snapshot(), cat.build_snapshot(changed=True, version="2")
    seqs = []
    for _ in range(runs):
        result = cat.compare(
            cat.load(cat.snapshot_transform(base, "identity")),
            cat.load(cat.snapshot_transform(changed, "identity")),
        )
        seqs.append(cat.finding_ids(result))
    return [f"run {i}: {s}" for i, s in enumerate(seqs) if s != seqs[0]]


def test_emitted_finding_order_is_repeatable() -> None:
    assert _order_violations() == []


# ---------------------------------------------------------------------------
# PYTHONHASHSEED (subprocess)
# ---------------------------------------------------------------------------


def _fingerprint_under_seed(seed: str) -> str:
    env = {**os.environ, "PYTHONHASHSEED": seed}
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from tests._family_f3_catalogue import hashseed_fingerprint as f; print(f())",
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return proc.stdout.strip()


def test_identity_fingerprint_is_hash_seed_independent() -> None:
    prints = {seed: _fingerprint_under_seed(seed) for seed in ("0", "1", "4242")}
    assert all(prints.values())
    assert '"finding_ids": []' not in prints["0"]  # vacuity guard
    assert len(set(prints.values())) == 1, prints


# ---------------------------------------------------------------------------
# Real castxml dumps (integration)
# ---------------------------------------------------------------------------

_CASTXML_FALLBACK = Path(
    "/root/.cache/abicheck-castxml-conda/castxml=0.7.0=hde8d07d_0-pinset1/bin"
)

_EXTRA_HEADER = """
#pragma once
namespace other {
struct Point { int v; };
int add(int a, int b) noexcept;
int add(double a, double b) noexcept;
inline int uses_other_guard() { lib::Guard g([]() { return 17; }); return g.run(); }
}
"""
_EXTRA_SOURCE = """
#include "api.h"
#include "extra.h"
namespace other {
int add(int a, int b) noexcept { return a - b; }
int add(double a, double b) noexcept { return int(a - b); }
int touch_other() { return uses_other_guard(); }
}
"""


def _require_castxml(monkeypatch: pytest.MonkeyPatch) -> None:
    if not sys.platform.startswith("linux"):
        pytest.skip("real g++ ELF dump is Linux-scoped")
    if shutil.which("castxml") is None and (_CASTXML_FALLBACK / "castxml").exists():
        monkeypatch.setenv(
            "PATH", f"{_CASTXML_FALLBACK}{os.pathsep}{os.environ['PATH']}"
        )
    if shutil.which("castxml") is None or shutil.which("g++") is None:
        pytest.skip("castxml and g++ are required")


def _build_tree(root: Path) -> tuple[Path, Path, Path]:
    from tests.test_identity_taint_end_to_end import _HEADER, _SOURCE

    root.mkdir(parents=True, exist_ok=True)
    (root / "api.h").write_text(_HEADER)
    (root / "extra.h").write_text(_EXTRA_HEADER)
    (root / "api.cpp").write_text(_SOURCE)
    (root / "extra.cpp").write_text(_EXTRA_SOURCE)
    so = root / "libapi.so"
    subprocess.run(
        [
            "g++",
            "-shared",
            "-fPIC",
            "-o",
            str(so),
            str(root / "api.cpp"),
            str(root / "extra.cpp"),
            f"-I{root}",
        ],
        check=True,
        capture_output=True,
    )
    return so, root / "api.h", root / "extra.h"


def _dump(so: Path, headers: list[Path]):
    from abicheck.dumper import dump

    return dump(so, headers, header_backend="castxml", public_headers=headers)


def _assert_same_identity(a, b) -> None:
    from abicheck.checker import Verdict, compare

    ids_a = Counter(f.entity_id for f in a.declarations.functions)
    ids_b = Counter(f.entity_id for f in b.declarations.functions)
    assert ids_a == ids_b
    # Negative control: overloads, same-leaf-other-namespace and the three
    # distinct lambda closures stay distinct entities.
    assert all(n == 1 for n in ids_b.values()), [k for k, n in ids_b.items() if n > 1]
    assert sorted(t.name for t in a.declarations.types) == sorted(
        t.name for t in b.declarations.types
    )
    assert sum("(lambda" in t.name for t in b.declarations.types) >= 2
    result = compare(a, b, cross_source_checks=False)
    assert result.changes == []
    assert result.verdict is Verdict.NO_CHANGE


@pytest.fixture
def castxml_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _require_castxml(monkeypatch)
    so, api, extra = _build_tree(tmp_path / "checkout_a")
    return so, api, extra, _dump(so, [api, extra])


@pytest.mark.integration
def test_castxml_relocated_checkout_is_no_change(castxml_base, tmp_path: Path) -> None:
    *_ignored, base = castxml_base
    so, api, extra = _build_tree(tmp_path / "an/unrelated/deeper/checkout_b")
    _assert_same_identity(base, _dump(so, [api, extra]))


@pytest.mark.integration
def test_castxml_symlinked_root_is_no_change(castxml_base, tmp_path: Path) -> None:
    so, api, extra, base = castxml_base
    link = tmp_path / "link_to_checkout"
    link.symlink_to(api.parent, target_is_directory=True)
    _assert_same_identity(
        base, _dump(link / so.name, [link / "api.h", link / "extra.h"])
    )


@pytest.mark.integration
def test_castxml_redundant_path_segments_are_no_change(castxml_base) -> None:
    so, api, extra, base = castxml_base
    d = api.parent
    dotted = [d / "." / "api.h", d / ".." / d.name / "extra.h"]
    _assert_same_identity(base, _dump(d / ".." / d.name / so.name, dotted))


@pytest.mark.integration
def test_castxml_reversed_header_order_keeps_identity_and_refuses_explicitly(
    castxml_base,
) -> None:
    """Header *order* is part of the parse context (a header's macros change
    how every later one parses -- ``comparability_sequences.
    _header_sequence_is_additive_reorder_free``), so it is not a
    ``must_not_change`` transform for the *verdict*: the comparison is
    refused as not comparable. What must not change is entity identity, and
    a refusal must never surface as a manufactured finding."""
    from abicheck.checker import compare
    from abicheck.errors import ProfileMismatchError

    so, api, extra, base = castxml_base
    reordered = _dump(so, [extra, api])
    ids_a = Counter(f.entity_id for f in base.declarations.functions)
    ids_b = Counter(f.entity_id for f in reordered.declarations.functions)
    assert ids_a == ids_b
    assert all(n == 1 for n in ids_b.values())
    with pytest.raises(ProfileMismatchError, match="header_sequence"):
        compare(base, reordered, cross_source_checks=False)


# ---------------------------------------------------------------------------
# Completeness: every identity-producing function has a coverage row
# ---------------------------------------------------------------------------

_IDENTITY_NAME_RE = re.compile(
    r"^(entity_id_for_\w+|resolve_\w*identity|[a-z]\w*_identity|[a-z]\w*decoration\w*|"
    r"checkout_stable_spelling|strip_anonymous_type_location|canonicalize_type_name|signature_key)$"
)

#: Codec decoders registered by the catalogue whose names the regex above
#: cannot see.
_EXTRA_REGISTERED = {
    "abicheck.extract.export_symbol_identity:msvc_export_function",
    "abicheck.model.special_member_identity:special_member_variant_aliases",
    "abicheck.model.name_decoration.macho:decode_itanium",
    "abicheck.model.name_decoration.pe_x86:decode_c_name",
    "abicheck.model.name_decoration.elf_version:unversioned_name",
    "abicheck.model.symbol_leaf:symbol_leaf_identifier",
}

_U = "UNCOVERED: "
COVERAGE: dict[str, str] = {
    # -- exercised --------------------------------------------------------
    "abicheck.name_classification:strip_anonymous_type_location": "string probe x path transforms",
    "abicheck.name_classification:canonicalize_type_name": "string probe x path transforms",
    "abicheck.model.graph_identity:checkout_stable_spelling": "string probe x path transforms",
    "abicheck.model.graph_identity:closure_location_free_identity": "string probe x path transforms",
    "abicheck.model.graph_entity_identity:signature_key": "string probe x path transforms; mutant M1",
    "abicheck.model.graph_entity_identity:type_identity": "string probe x path transforms",
    "abicheck.model.graph_entity_identity:unresolved_identity": "string probe x path transforms",
    "abicheck.model.graph_entity_identity:declaration_identity": "string probe + Mach-O codec; mutant M2",
    "abicheck.model.name_decoration.pe_x86:decode_c_name": "x86 PE codec; mutant M5",
    "abicheck.model.export_index:pe_decoration_aliases": "x86 PE codec (the join's alias table)",
    "abicheck.model.name_decoration.elf_version:unversioned_name": "ELF version codec",
    "abicheck.model.symbol_leaf:symbol_leaf_identifier": "ELF version codec",
    "abicheck.model.source_graph:function_decl_identity": "string probe x path transforms",
    "abicheck.model.name_decoration.macho:decode_itanium": "Mach-O codec; mutant M2",
    "abicheck.extract.headers.clang.context:strip_darwin_itanium_decoration": "Mach-O codec",
    "abicheck.extract.export_symbol_identity:msvc_export_function": "x86 PE codec",
    "abicheck.model.special_member_identity:special_member_variant_aliases": "ctor/dtor codec; mutant M6",
    "abicheck.finding_identity:resolve_function_identity": "string probe + snapshot level",
    "abicheck.finding_identity:resolve_variable_identity": "snapshot level identity keys",
    "abicheck.finding_identity:resolve_change_identity": "snapshot level report finding ids (via report_canonical_finding_id); path probe",
    "abicheck.model.identity:entity_id_for_function": "negative control + castxml dumps; mutant M4",
    "abicheck.model.identity:entity_id_for_variable": "negative control",
    "abicheck.model.identity:entity_id_for_type": "negative control",
    "abicheck.model.identity:entity_id_for_enum": "negative control",
    "abicheck.model.identity:entity_id_for_typedef": "negative control",
    "abicheck.model.identity:entity_id_for_constant": "negative control",
    # -- path probes (Phase 3: identity takes a RootRelativePath) ---------
    "abicheck.model.entity_identity:source_relative_identity": "path probe x path transforms",
    "abicheck.model.entity_identity:resolve_canonical_identity": "path probe (graph def_file) x path transforms",
    "abicheck.finding_identity:resolve_symbol_identity": "path probe x path transforms",
    "abicheck.workflows.aggregate.reconcile:resolve_report_change_identity": "path probe (report entry) x path transforms",
    # -- environment cells (tests/test_family_f3_identity_cells.py) -------
    "abicheck.model.identity_tiers:snapshot_local_identity": "cell: decoded spelling joins",
    "abicheck.model.extraction_scope:extraction_scope_identity": "cell: rule order / rule set",
    "abicheck.model.extraction_scope:snapshot_scope_identity": "cell: rule order / rule set",
    "abicheck.model.header_exclusion_record:canonical_exclusion_identity": "cell: order / duplicates",
    "abicheck.model.header_exclusion_record:comparison_exclusion_identity": "cell: sides",
    "abicheck.model.header_exclusion_record:release_exclusion_identity": "cell: member order",
    "abicheck.policy.rule_identity:rule_identity": "cell: prose / separator forgery",
    "abicheck.compatibility_evaluation_frontend:builtin_policy_identity": "cell: PYTHONHASHSEED",
    "abicheck.compatibility_evaluation_frontend:severity_preset_identity": "cell: PYTHONHASHSEED / alias",
    "abicheck.frontends.action.library_selection:read_elf_identity": "cell: path with space / symlinked root",
    "abicheck.workflows.release_public_surface:build_side_identity": "cell: ./ .. symlinked root",
    "abicheck.bundle:stored_capture_identity": "cell: relocated package",
    "abicheck.workflows.aggregate.reconcile:resolve_cross_abi_identity": "cell: Itanium / Mach-O / MSVC spelling",
    "abicheck.compare.template_surface:alias_identity": "cell: signature / ABI tag",
    "abicheck.compare.template_surface:cpo_identity": "cell: function vs variable",
}


def _enumerate_identity_functions() -> set[str]:
    found = set()
    for path in sorted((REPO / "abicheck").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module = ".".join(path.relative_to(REPO).with_suffix("").parts)
        for node in tree.body:
            name = (
                canonical_def_name(node.name)
                if isinstance(node, ast.FunctionDef)
                else None
            )
            if name is not None and _IDENTITY_NAME_RE.match(name):
                found.add(f"{module}:{name}")
    return found


def test_every_identity_function_has_a_coverage_row() -> None:
    enumerated = _enumerate_identity_functions() | _EXTRA_REGISTERED
    missing = sorted(enumerated - COVERAGE.keys())
    stale = sorted(COVERAGE.keys() - enumerated)
    assert missing == [], (
        f"add a COVERAGE row (exercised-by or UNCOVERED: reason): {missing}"
    )
    assert stale == [], f"stale COVERAGE rows (function gone/renamed): {stale}"


def test_covered_rows_are_really_registered_in_the_catalogue() -> None:
    registered = (
        set(cat.PROBES_BY_NAME)
        | set(cat.macho_decoders())
        | set(cat.pe_decoders())
        | set(cat.elf_version_decoders())
        | set(cat._path_probes())
        | set(cat.ENVIRONMENT_CELLS)
        | {
            "abicheck.model.special_member_identity:special_member_variant_aliases",
            "abicheck.finding_identity:resolve_variable_identity",
            "abicheck.finding_identity:resolve_change_identity",
        }
        | {
            f"abicheck.model.identity:entity_id_for_{k}"
            for k in ("function", "variable", "type", "enum", "typedef", "constant")
        }
    )
    covered = {k for k, v in COVERAGE.items() if not v.startswith(_U)}
    assert covered == registered
    # Design-hardening Phase 3 exit: no identity function is left uncovered.
    assert [k for k, v in COVERAGE.items() if v.startswith(_U)] == []


# ---------------------------------------------------------------------------
# Seeded-mutant self-check: historical bugs put back must be reported
# ---------------------------------------------------------------------------


def test_mutant_m1_checkout_path_in_signature_key_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-#1355/#1383: the raw ``(lambda at /abs/...)`` spelling hashed."""
    probe = cat.PROBES_BY_NAME["abicheck.model.graph_entity_identity:signature_key"]
    relocate = cat.TRANSFORMS_BY_NAME["relocate_checkout"]
    assert cat.string_violations(probe, relocate) == []
    monkeypatch.setattr(cat.gei_mod, "checkout_stable_spelling", lambda s: s)
    assert cat.string_violations(probe, relocate) != []


def test_mutant_m2_dropped_macho_underscore_strip_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-#1140/#1156: Mach-O ``__Z`` never normalized."""
    dec = "abicheck.model.graph_entity_identity:declaration_identity"
    monkeypatch.setattr(cat.macho_codec, "decode_itanium", lambda s: s)
    assert any(v.startswith("join:") for v in cat.macho_violations(dec))


def test_mutant_m3_hash_order_dependent_emission_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#1359's shape: output order following per-process hash/set order."""
    real = cat.checker_mod.compare
    calls = iter(range(100))

    def salted(*args, **kwargs):
        result = real(*args, **kwargs)
        salt = next(calls)
        result.changes.sort(key=lambda c: hash((salt, str(c.symbol), c.kind.value)))
        return result

    monkeypatch.setattr(cat.checker_mod, "compare", salted)
    assert _order_violations(runs=6) != []


def test_mutant_m4_overload_collapse_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """#1148-#1155's shape: an identity key missing its discriminator."""
    real = cat.ident_mod.entity_id_for_function
    monkeypatch.setattr(
        cat.ident_mod,
        "entity_id_for_function",
        lambda scope, leaf, **_kw: real(scope, leaf, is_extern_c=True),
    )
    assert any("overloads" in v for v in cat.entity_id_collisions())


def test_mutant_m5_stdcall_decoration_not_decoded_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-#1367: x86 ``_f@N`` left decorated."""
    monkeypatch.setattr(cat.pe_codec, "_STDCALL_RE", re.compile(r"(?!x)x"))
    for decoder in cat.pe_decoders():
        assert cat.pe_violations(decoder, "stdcall") != [], decoder


def test_mutant_m6_missing_ctor_variant_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-#1370: a ctor variant (C3) not joined to its family."""
    from abicheck.model.name_decoration import itanium_structors

    monkeypatch.setattr(itanium_structors, "CTOR_VARIANTS", ("C1", "C2"))
    assert cat.ctor_dtor_violations() != []


def test_mutant_m7_checkout_prefix_kept_in_path_identity_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 3: the root-relative anchor lost, so a declaring path keeps its
    checkout prefix (the shape every F3 path fix had to close)."""
    import abicheck.model.root_relative_path as rrp

    monkeypatch.setattr(rrp, "PROJECT_LAYOUT_MARKERS", frozenset())
    relocate = cat.TRANSFORMS_BY_NAME["relocate_checkout"]
    assert any(cat.path_violations(p, relocate) for p in cat._path_probes())
