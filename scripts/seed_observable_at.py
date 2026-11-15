#!/usr/bin/env python3
"""Seed ``observable_at`` on the ChangeKind taxonomy (ADR-093, WS-1 slice 3).

``DECISIONS`` is the reviewable record of how each kind's observability was
derived. One row per kind: ``kind  CODE  # note``. A kind with no row, or with
code ``U``, stays ``UNCLASSIFIED``: when the evidence is ambiguous or absent
the sentinel is the answer, because a wrong value creates a false-clean
obligation that no run can discharge.

Evidence collected per kind (``--table`` renders it as Markdown):

* abiforge operators whose ``expected`` or ``any_of`` names the kind, with the
  operator's ``min_evidence`` (``fn`` = computed per config);
* the designed evidence tier of ``scripts/evidence_tiers.py``
  (``EVIDENCE_TIER_BY_KIND``, a chain assumption: a hint, not a rule);
* the modules outside the taxonomy that emit ``ChangeKind.<NAME>``.

Rules applied (plan ``abicheck-abiforge-completeness.md`` §1.2, §2):

* pure export-table facts (symbols, versions, SONAME, ELF/Mach-O/PE header
  facts) -> ``B``;
* layout or type facts that both DWARF and the public header carry ->
  ``{B,D}, {B,H}``;
* compiler-flag facts that headers never carry -> ``{B,D}, {B,BD}``;
* declaration-only facts (defaults, ``explicit``, ``noexcept``, deprecation,
  access, qualifiers, inline or template bodies, macros) -> ``{B,H}``;
* facts that need the source (``S``) or the graph (``G``) -> ``L4`` / ``L5``;
* platform-specific facts carry an ``ObservabilityCondition``.

Apply the decisions to the taxonomy modules (formatting afterwards with
``ruff format``)::

    python scripts/seed_observable_at.py --apply
    python scripts/seed_observable_at.py --baseline   # rewrite the U baseline
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CATALOG = REPO / "abicheck" / "model" / "change_catalog"
BASELINE = REPO / "tests" / "data" / "change_kind_unclassified.json"
# architecture/modules.yaml `limits.production`.
PRODUCTION_LINE_CAP = 800

# Ordered form codes. ``form`` is (alternatives, rationale, platforms, const).
# ``const`` is the module-level name the taxonomy file defines and reuses.
FORMS: dict[str, tuple[tuple[tuple[str, ...], ...], str, tuple[str, ...], str]] = {
    "B": ((("B",),), "", (), "_OBS_B"),
    "B:ELF": ((("B",),), "", ("elf",), "_OBS_B_ELF"),
    "B:MACHO": ((("B",),), "", ("macho",), "_OBS_B_MACHO"),
    "B:PE": ((("B",),), "", ("pe",), "_OBS_B_PE"),
    "B:ELF+MACHO": ((("B",),), "", ("elf", "macho"), "_OBS_B_ELF_MACHO"),
    "BH": (
        (("B", "H"),),
        "declared in the public header and present in the export table",
        (),
        "_OBS_BH",
    ),
    "H": (
        (("B", "H"),),
        "declaration-only fact: visible in the public header",
        (),
        "_OBS_H",
    ),
    "DH": (
        (("B", "D"), ("B", "H")),
        "layout or type fact: DWARF and the public header both carry it",
        (),
        "_OBS_DH",
    ),
    "D1": (
        (("B", "D"),),
        "underlying size set by the compiler: only DWARF records it",
        (),
        "_OBS_D",
    ),
    "DBD": (
        (("B", "D"), ("B", "BD")),
        "compiler-flag fact: the header never carries it; DWARF and build data do",
        (),
        "_OBS_DBD",
    ),
    "BHSG": (
        (("B", "H", "S", "G"),),
        "call edges across translation units: needs sources and the graph",
        (),
        "_OBS_BHSG",
    ),
    "L4": (
        (("B", "H", "BD", "S"),),
        "needs the source translation unit (S)",
        (),
        "_OBS_L4",
    ),
    "L5": (
        (("B", "H", "BD", "S", "G"),),
        "needs the source graph (G)",
        (),
        "_OBS_L5",
    ),
}

# kind  CODE  [# note].  Kinds absent here (or coded U) stay UNCLASSIFIED.
DECISIONS_TEXT = """
# -- symbols: export-table facts (B) --------------------------------------
func_added                       B
func_added_elf_only              B:ELF
func_export_added                B
func_language_linkage_changed    B      # mangled names change
func_likely_renamed              B
func_removed                     B
func_removed_elf_only            B:ELF
func_visibility_changed          B
var_added                        B
var_added_elf_only               B:ELF
var_export_added                 B
var_removed                      B
var_removed_elf_only             B:ELF
var_visibility_changed           B
symbol_renamed_batch             B
symbol_size_changed              B:ELF  # st_size in .dynsym; no PE/Mach-O analogue
symbol_size_changed_const_object B:ELF
symbol_size_changed_internal     B:ELF
# -- symbols: layout / type facts in DWARF and the header (DH) -------------
anon_field_changed               DH
calling_convention_changed       DH
func_params_changed              DH
func_return_changed              DH
func_return_covariant_narrowed   DH
func_variadic_added              DH
func_variadic_removed            DH
func_virtual_added               DH
func_virtual_removed             DH
param_pointer_level_changed      DH
return_pointer_level_changed     DH
var_alignment_changed            DH   # plan A2.1
var_became_const                 DH
var_lost_const                   DH
var_type_changed                 DH
# -- symbols: declaration-only facts (H) -----------------------------------
constant_added                   H
constant_changed                 H
constant_removed                 H
ctor_explicit_added              H
ctor_explicit_removed            H
ctor_overload_ambiguity_risk     H
field_access_changed             H
func_contract_attribute_added    H
func_contract_attribute_removed  H
func_deleted                     H
func_deprecated_added            H
func_deprecated_removed          H
func_exception_spec_changed      H
func_lost_final                  H
func_lost_inline                 H
func_became_final                H
func_noexcept_added              H
func_noexcept_removed            H
func_override_specifier_added    H
func_override_specifier_removed  H
func_virtual_noexcept_added      H
hidden_friend_added              H
hidden_friend_removed            H
method_access_changed            H
overload_ambiguity_introduced    H
param_became_va_list             H
param_default_value_changed      H   # plan A2.4
param_default_value_removed      H
param_lost_va_list               H
param_pointee_qualifier_added    H
param_pointee_qualifier_changed  H
param_renamed                    H
param_restrict_added             H
param_restrict_changed           H
func_return_pointee_qualifier_added    H
func_return_pointee_qualifier_changed  H
func_return_pointee_qualifier_removed  H
public_api_exposes_stl_by_value  H
var_access_changed               H
var_access_widened               H
var_deprecated_added             H
var_deprecated_removed           H
# -- symbols: needs the source graph (L5) ---------------------------------
internal_symbol_required_by_public_api L5
# -- types: layout / type facts (DH) ---------------------------------------
atomic_qualifier_changed         DH
base_class_offset_changed        DH
base_class_position_changed      DH
base_class_virtual_changed       DH
enum_last_member_value_changed   DH
enum_member_added               DH
enum_member_removed              DH
enum_member_value_changed        DH
field_bitfield_changed           DH
field_became_const               DH
field_became_volatile            DH
field_lost_const                 DH
field_lost_volatile              DH
flexible_array_member_changed    DH
func_cv_changed                  DH
func_pure_virtual_added          DH
func_ref_qual_changed            DH
standard_layout_lost             DH
struct_alignment_changed         DH
struct_field_offset_changed      DH
struct_field_removed             DH
struct_field_type_changed        DH
struct_packing_changed           DH   # plan A2.2: {B,D},{B,H}
struct_size_changed              DH
trivially_copyable_lost          DH   # plan A2.4: VALUE_ABI_TRAIT duplicate
type_alignment_changed           DH
type_base_changed                DH
type_field_added                 DH
type_field_added_compatible      DH
type_field_offset_changed        DH
type_field_removed               DH
type_field_type_changed          DH
type_kind_changed                DH
type_size_changed                DH
typedef_base_changed             DH
union_field_added               DH
union_field_removed              DH
union_field_type_changed         DH
virtual_base_offset_changed      DH
virtual_method_added             DH
vptr_introduced                  DH
# -- types: declaration-only facts (H) -------------------------------------
base_class_access_narrowed       H
default_template_arg_changed      H
enum_became_scoped               H
enum_deprecated_added            H
enum_deprecated_removed          H
enum_lost_scoped                 H
enum_member_renamed              H
experimental_graduated           H
experimental_removed_without_replacement H
field_became_mutable             H
field_default_initializer_changed H
field_default_initializer_removed H
field_deprecated_added           H
field_deprecated_removed         H
field_lost_mutable               H
field_renamed                    H
func_static_changed              H
inline_body_references_renamed_member H
inline_namespace_version_bumped  H
mandatory_template_param_added   H
polymorphic_type_non_virtual_dtor H
removed_const_overload           H
source_level_kind_changed        H
std_reexport_removed             H
template_param_default_changed   H
type_became_abstract             H
type_became_final                H
type_became_opaque               H
type_deprecated_added            H
type_deprecated_removed          H
type_lost_abstract               H
type_lost_final                  H
type_removed                     H
typedef_removed                  H
unspecified_return_now_named     H
# -- types: binary facts (B) ----------------------------------------------
rtti_inheritance_changed         B
type_visibility_changed          B
type_vtable_changed              B:ELF+MACHO  # plan §8: _ZTV sizes
vtable_slot_count_changed        B:ELF+MACHO
vtable_thunk_offset_changed      B:ELF+MACHO
vtable_thunk_set_changed         B:ELF+MACHO
vtt_slot_count_changed           B:ELF+MACHO
# -- platform: ELF / Mach-O / PE header and dynamic facts (B) --------------
abi_tag_changed                  B:ELF
allocator_replacement_added      B:ELF
allocator_replacement_removed    B:ELF
bind_now_disabled                B:ELF
branch_protection_improved       B:ELF
branch_protection_weakened       B:ELF
cet_protection_improved          B:ELF
cet_protection_weakened          B:ELF
dt_relr_introduced               B:ELF
dt_relr_removed                  B:ELF
dynamic_loading_flags_changed    B:ELF
elf_abi_flags_changed            B:ELF
elf_class_changed                B:ELF
elf_endianness_changed           B:ELF
elf_init_fini_changed            B:ELF
elf_machine_changed              B:ELF
elf_osabi_changed                B:ELF
executable_stack                 B:ELF
executable_stack_removed         B:ELF
fortify_source_weakened          B:ELF
func_visibility_protected_changed B:ELF
glibcxx_dual_abi_changed         B
glibcxx_dual_abi_flip_detected   B
hash_style_removed               B:ELF
ifunc_introduced                 B:ELF
ifunc_removed                    B:ELF
imported_symbol_added            B
imported_symbol_removed          B
imported_symbol_unprovided       B
interpreter_changed              B:ELF
macho_cpu_type_changed           B:MACHO
macho_filetype_changed          B:MACHO
macho_linkage_flags_changed     B:MACHO
macho_reexport_changed          B:MACHO
needed_added                     B
needed_order_changed             B
needed_removed                   B
os_deployment_floor_raised       B:MACHO
pe_forwarder_changed             B:PE
pe_hardening_improved            B:PE
pe_hardening_weakened            B:PE
pe_import_load_mode_changed      B:PE
pe_machine_changed               B:PE
pe_ordinal_retargeted            B:PE
pie_disabled                     B:ELF
protected_visibility_changed     B:ELF
relro_weakened                   B:ELF
rpath_changed                    B:ELF
rpath_type_changed               B:ELF
runpath_changed                  B:ELF
soname_changed                   B:ELF+MACHO
soname_missing                   B:ELF
stack_canary_removed             B:ELF
static_tls_introduced            B:ELF
static_tls_removed               B:ELF
symbol_binding_became_unique     B:ELF
symbol_binding_changed           B:ELF
symbol_binding_lost_unique       B:ELF
symbol_binding_strengthened      B:ELF
symbol_elf_visibility_changed    B:ELF
symbol_leaked_from_dependency_changed B:ELF
symbol_moved_version_node        B:ELF
symbol_type_changed              B:ELF
symbol_version_alias_changed     B:ELF
symbol_version_defined_added     B:ELF
symbol_version_defined_removed   B:ELF
symbol_version_node_removed      B:ELF
symbol_version_required_added    B:ELF
symbol_version_required_added_compat B:ELF
symbol_version_required_removed  B:ELF
symbolic_binding_mode_changed    B:ELF
text_relocation_introduced       B:ELF
text_relocation_removed          B:ELF
tls_var_size_changed             B:ELF
unversioned_exported_symbol      B:ELF
runtime_floor_raised             B:ELF
version_script_missing           B:ELF
versioned_symbol_scheme_detected B:ELF
vtable_symbol_identity_changed   B:ELF+MACHO
writable_executable_segment      B:ELF
x86_isa_baseline_raised          B:ELF
# -- platform: compiler-flag facts (DBD) and layout (DH / D1) -------------
bit_int_width_changed            DH
char8t_migration                 DH
char_signedness_changed          DBD  # plan A3
enum_size_flag_changed           DBD
enum_underlying_size_changed     D1   # header carries it only when explicit
exceptions_mode_changed          DBD
float_abi_changed                DBD
integer_model_changed            DBD
long_double_abi_changed          DBD  # plan A3
lto_mode_changed                 DBD
struct_packing_mode_changed      DBD
struct_return_convention_changed DH   # plan: STRUCT_RETURN_CONVENTION
stdlib_debug_mode_changed        DBD
target_isa_changed               DBD  # plan A3
threadsafe_statics_mode_changed  DBD
time64_abi_changed               DBD
tls_model_changed                DBD
toolchain_flag_drift             DBD  # plan A3
value_abi_trait_changed          DH   # plan A2.4
vector_abi_changed               DBD  # plan A3
whole_program_vtables_mode_changed DBD
wchar_model_changed              DBD  # plan A3
abi_relevant_build_flag_changed  DBD
# -- build / source: declaration-only (H), header and export (BH) ---------
concept_constraint_changed       H
concept_tightened               H
constexpr_value_changed          H
default_argument_changed         H    # plan A2.4
exported_not_public              BH
header_only_function_params_changed H
inline_body_changed              H    # plan A5
inline_function_removed          H    # plan A2.4 / A5
internal_template_leaks_via_public_api H
internal_type_leaks_via_public_api H
private_header_leak              H
public_macro_removed             H    # plan A5
public_macro_value_changed       H
public_not_exported              BH
public_surface_grew              H
public_surface_shrank            H
public_typedef_removed           H
public_typedef_target_changed    H
rtti_for_internal_type           BH
source_decl_binary_symbol_mismatch BH
template_body_changed            H    # plan A5
uninstantiated_template_removed  H    # plan A2.4
# -- build / source: binary (B) -------------------------------------------
consumer_required_symbol_removed B
runtime_symbol_provider_changed  B
runtime_weak_resolution_changed  B
# -- build / source: needs sources or the graph (L4, L5, BHSG) -----------
call_graph_public_entry_reachability_changed BHSG
public_api_internal_dependency_added L5
public_api_internal_type_renamed L5
public_dependency_role_changed   L5
public_dependency_role_unknown   L5
public_to_internal_dependency    L5
public_virtual_dispatch_set_changed L5
source_surface_dso_mismatch      L4
target_dependency_added          L5
"""

# Kinds that stay UNCLASSIFIED on purpose, with the reason. Kept here so the
# reviewer sees what was considered and not classified (not used by --apply).
UNCLASSIFIED_REASONS: dict[str, str] = {
    "func_virtual_became_pure": "API (B,H) or ABI (B,H,S) form: the kind does not say which",
    "func_became_inline": "export-table effect depends on the inline emission in the TU",
    "func_deleted_dwarf": "a DWARF-derived deletion: its positive evidence is unclear",
    "python_api_*": "Python binding surface: no evidence source named in the taxonomy",
    "python_abi3_*": "stable-ABI floor: depends on the producer's module init symbols",
    "bundle_*": "multi-library bundle facts: a pipeline fact, not one library's",
    "numpy_*": "NumPy C-API consumption: a producer-side policy fact",
    "wheel_*": "wheel packaging facts: outside the ELF/PE/Mach-O header set",
    "sycl_*": "SYCL plugin facts: runtime deployment, not a library surface",
    "declaration_*": "graph reconciliation identity: a pipeline fact (ADR-031)",
    "suppression_*": "policy disposition facts (ADR-067), not observation facts",
    "kabi_*": "kernel ABI symbol metadata: not covered by the ELF/PE/Mach-O sets",
}

FORM_CODES = frozenset(FORMS)


@dataclass(frozen=True, slots=True)
class Decision:
    kind: str
    code: str
    note: str


def parse_decisions(text: str = DECISIONS_TEXT) -> dict[str, Decision]:
    """Parse ``kind CODE [# note]`` rows; reject unknown codes and duplicates."""
    out: dict[str, Decision] = {}
    for raw in text.splitlines():
        line, _, note = raw.partition("#")
        parts = line.split()
        if not parts:
            continue
        if len(parts) != 2:
            raise ValueError(f"malformed decision row: {raw!r}")
        kind, code = parts
        if code == "U":
            continue
        if code not in FORM_CODES:
            raise ValueError(f"{kind}: unknown form code {code!r}")
        if kind in out:
            raise ValueError(f"{kind}: decided twice")
        out[kind] = Decision(kind, code, note.strip())
    return out


DECISIONS: dict[str, Decision] = parse_decisions()


# ── evidence ────────────────────────────────────────────────────────────────


def taxonomy_files() -> list[Path]:
    """Files under the change catalog that hold ``_E(...)`` entry rows."""
    found = []
    for path in sorted(CATALOG.glob("*.py")):
        if re.search(r"^    _E\($", path.read_text(), re.M):
            found.append(path)
    return found


def taxonomy_owner() -> dict[str, str]:
    owner: dict[str, str] = {}
    for path in taxonomy_files():
        for m in re.finditer(r'_E\(\s*\n\s*"([a-z0-9_]+)"', path.read_text()):
            owner[m.group(1)] = path.name
    return owner


def operator_evidence() -> dict[str, list[tuple[str, str]]]:
    """Kind -> [(operator name, min_evidence)] over abiforge's operators."""
    sys.path.insert(0, str(REPO))
    from abiforge.operators import OPERATORS

    out: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for op in OPERATORS:
        kinds = set(op.expected)
        for group in op.any_of:
            kinds |= set(group)
        me = op.min_evidence if isinstance(op.min_evidence, str) else "fn"
        for kind in kinds:
            out[kind.lower()].append((op.name, me))
    return out


def designed_tiers() -> dict[str, str]:
    sys.path.insert(0, str(REPO / "scripts"))
    import evidence_tiers

    return dict(evidence_tiers.EVIDENCE_TIER_BY_KIND)


def detector_modules() -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for path in glob.glob(str(REPO / "abicheck" / "**" / "*.py"), recursive=True):
        rel = Path(path).relative_to(REPO / "abicheck").as_posix()
        if rel.startswith("model/change_catalog/") or "/tests/" in rel:
            continue
        for m in re.finditer(r"ChangeKind\.([A-Z0-9_]+)\b", Path(path).read_text()):
            out[m.group(1).lower()].add(rel)
    return out


# ── rendering ───────────────────────────────────────────────────────────────


def _spell(alts: tuple[tuple[str, ...], ...]) -> str:
    return ", ".join("{" + ",".join(a) + "}" for a in alts)


def markdown_table() -> str:
    """Kind | taxonomy | verdict | decision | rationale | evidence."""
    from abicheck.model.change_catalog.assembled import REGISTRY

    owner = taxonomy_owner()
    ops = operator_evidence()
    tiers = designed_tiers()
    dets = detector_modules()
    rows = [
        "| Kind | Taxonomy | Verdict | Decision | Rationale | Operators (min_evidence) "
        "| Designed tier | Detectors |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for kind, entry in REGISTRY.entries.items():
        dec = DECISIONS.get(kind)
        if dec is None:
            decision, rationale = "UNCLASSIFIED", ""
        else:
            alts, rationale, _platforms, _const = FORMS[dec.code]
            decision = _spell(alts)
            if dec.code.startswith("B:"):
                decision += " on " + dec.code[2:]
            rationale = dec.note or rationale
        mins = Counter(m for _, m in ops.get(kind, []))
        op_txt = ", ".join(f"{m}:{n}" for m, n in sorted(mins.items())) if mins else "-"
        det = ", ".join(sorted(dets.get(kind, set()))[:2]) or "-"
        rows.append(
            f"| `{kind}` | {owner.get(kind, '?')} | {entry.default_verdict.name} "
            f"| {decision} | {rationale} | {op_txt} | {tiers.get(kind, '-')} | {det} |"
        )
    return "\n".join(rows) + "\n"


def _entry_spans(lines: list[str]) -> list[tuple[str, int, int]]:
    """``(kind, start, end)`` for each ``_E(`` entry; ``end`` is its ``),`` line."""
    spans = []
    i = 0
    while i < len(lines):
        if lines[i] == "    _E(\n":
            kind = re.match(r'\s*"([a-z0-9_]+)",', lines[i + 1]).group(1)  # type: ignore[union-attr]
            j = i + 1
            while lines[j] != "    ),\n":
                j += 1
            spans.append((kind, i, j))
            i = j
        i += 1
    return spans


def _constants_block(codes: set[str]) -> list[str]:
    """Module-level ``observable`` constants for the forms used in one file."""
    block = []
    for code in sorted(codes, key=list(FORMS).index):
        alts, rationale, platforms, const = FORMS[code]
        parts = [
            "("
            + ", ".join(f'"{s}"' for s in alt)
            + ("," if len(alt) == 1 else "")
            + ")"
            for alt in alts
        ]
        if rationale:
            parts.append(f'rationale="{rationale}"')
        if platforms:
            plat = ", ".join(f"Platform.{p.upper()}" for p in platforms)
            parts.append(
                f"condition=ObservabilityCondition(platform=frozenset({{{plat}}}))"
            )
        block.append(f"{const} = observable({', '.join(parts)})")
    return block


def _ruff_format(path: Path, text: str) -> str:
    """Format *text* as ``ruff format`` would, keyed on *path*'s name."""
    proc = subprocess.run(
        ["ruff", "format", "--stdin-filename", str(path), "-"],
        input=text,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def apply_decisions(dry_run: bool) -> dict[str, int]:
    """Rewrite the taxonomy modules so each decided entry has an ``observable_at``."""
    owner = taxonomy_owner()
    by_file: dict[str, list[Decision]] = defaultdict(list)
    for dec in DECISIONS.values():
        if dec.kind not in owner:
            raise SystemExit(f"{dec.kind}: not a ChangeKind taxonomy entry")
        by_file[owner[dec.kind]].append(dec)

    counts: dict[str, int] = {}
    for name, decs in sorted(by_file.items()):
        path = CATALOG / name
        lines = path.read_text().splitlines(keepends=True)
        spans = {k: (s, e) for k, s, e in _entry_spans(lines)}
        codes = {d.code for d in decs}
        # Insert from the bottom up so earlier indices stay valid.
        for dec in sorted(decs, key=lambda d: -spans[d.kind][1]):
            _, end = spans[dec.kind]
            const = FORMS[dec.code][3]
            lines.insert(end, f"        observable_at=({const},),\n")
        # Constants go before the list definition, the import before registry.
        head = next(
            i
            for i, ln in enumerate(lines)
            if re.match(r"[A-Z_0-9]+: list\[ChangeKindMeta\] = \[", ln)
        )
        lines[head:head] = [ln + "\n" for ln in _constants_block(codes)] + ["\n"]
        imp = next(
            i for i, ln in enumerate(lines) if ln.startswith("from .registry import")
        )
        used = {"observable"}
        if any(FORMS[c][2] for c in codes):
            used |= {"ObservabilityCondition", "Platform"}
        lines.insert(imp, f"from .observability import {', '.join(sorted(used))}\n")
        # A file that would exceed the production cap is left untouched and its
        # kinds stay UNCLASSIFIED: splitting a module is a separate decision.
        # The cap is checked on the file as ruff formats it, not the raw text.
        text = _ruff_format(path, "".join(lines))
        if text.count("\n") > PRODUCTION_LINE_CAP:
            counts[name] = -len(decs)
            continue
        counts[name] = len(decs)
        if not dry_run:
            path.write_text(text)
    return counts


def write_baseline() -> int:
    from abicheck.model.change_catalog.assembled import REGISTRY
    from abicheck.model.change_catalog.observability import UNCLASSIFIED

    unclassified = sorted(
        k for k, e in REGISTRY.entries.items() if e.observable_at is UNCLASSIFIED
    )
    data = json.loads(BASELINE.read_text())
    data["unclassified"] = unclassified
    BASELINE.write_text(json.dumps(data, indent=2) + "\n")
    return len(unclassified)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--table", action="store_true", help="print the evidence table")
    group.add_argument("--apply", action="store_true", help="write observable_at lines")
    group.add_argument("--baseline", action="store_true", help="rewrite the U baseline")
    group.add_argument("--dry-run", action="store_true", help="count, write nothing")
    args = parser.parse_args(argv)
    if args.table:
        sys.stdout.write(markdown_table())
    elif args.apply or args.dry_run:
        counts = apply_decisions(dry_run=args.dry_run)
        for name, n in counts.items():
            print(f"{name}: {n} observable_at lines" if n >= 0 else f"{name}: blocked")
        blocked = {n: -c for n, c in counts.items() if c < 0}
        applied = sum(c for c in counts.values() if c > 0)
        for name, n in blocked.items():
            print(f"{name}: {n} decisions NOT applied (file would exceed the cap)")
        print(f"applied: {applied} (run ruff format afterwards)")
    else:
        print(f"UNCLASSIFIED after baseline: {write_baseline()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
