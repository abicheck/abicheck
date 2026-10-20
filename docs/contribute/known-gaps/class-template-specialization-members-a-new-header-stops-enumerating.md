---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Class-template specialization members a new header stops enumerating (partly fixed)

Index: [Known gaps](../known-gaps.md)

**Status (2026-10-20): PARTIAL.** A template instance whose primary template is still declared exactly once in the new header graph is no longer reported as `func_removed` or `inline_function_removed` for its mangled symbol. The export change is reported as `func_visibility_changed` (BREAKING), and nothing is reported when the new binary still exports the symbol or the old one never did (`compare/template_instance_absence.py`, called from `diff_symbols._match_old_function`). Still open: the implicit members of a class-template specialization that the new header no longer instantiates, and the specialization's own `type_removed`.

## Why the removal was wrong

castxml records a template instantiation only while some header translation unit still instantiates it, or an `extern template` names it. When the new header stops using a template, the new snapshot holds no record for the instantiation. The header-depth removal path read that absence as a removal, so the same declaration was reported as `func_removed` (BREAKING) or `inline_function_removed` (API_BREAK) depending on whether the old build exported it. The primary template was still declared, and the new binary often still exported the symbol.

The reproduction is a free function template, a member function template, and a class-template member, each built against a new library in three ways (`-fvisibility-inlines-hidden`, the template's last caller removed, and `-fvisibility=hidden`), with and without the new header instantiating it. Before the fix, every no-instantiation cell of the three template kinds reported a declaration removal, including the cell where `readelf` still shows the symbol exported and unchanged.

## What the fix decides

For an old instantiation whose primary template is declared once in the new header graph (`decl://<qualified name>#…` nodes of `surface_graph`):

| New export table (`readelf` oracle) | Old build exported it | Finding |
|---|---|---|
| still exported | any | none |
| proven absent | yes | `func_visibility_changed` |
| proven absent | no | none (no symbol to bind to; the declaration remains) |
| proven absent | unknown | the existing path decides |
| unknown (no table read) | any | none from this rule; the existing path decides |

The rule does not apply when the primary is undeclared or declared more than once (an overload set cannot tell which declaration the instance belonged to), for an export-table-only old record, or to any name that is not a named template member (operators, constructors, substitutions, synthetic keys). An `extern template` in the new header never downgrades an export loss: the export loss is reported as `func_visibility_changed`, which is BREAKING, whether the rule or the existing enumerated path produces it.

Verified by `tests/test_template_export_loss_declaration_presence.py` (38 integration tests: 36 matrix cells built with g++ and castxml and judged by `readelf` and the header text, plus 2 controls) and `tests/test_template_export_loss_declaration_presence_unit.py` (the same invariants on constructed snapshots). With the rule disabled, the integration run fails nine cells, the exact no-instantiation template cells.

## What remains

1. **Implicit members of a class-template specialization.** A specialization the new header stops instantiating keeps its implicit constructors, destructor and `operator=` as `inline_function_removed` (API_BREAK). The constructors and destructor are synthetic keys (`__abicheck_ctor__ns::Box<double>()`, `~ns::Box<double>`) with no mangled name. The `operator=` has a mangled name whose stem is `ns::Box::{op:aS}`, which has no header spelling this rule matches. Closing this needs the class template's own declaration as the owner, and an operator-name map from the Itanium codes.
2. **`type_removed` for the specialization.** The type detector reports `Box<double>` as removed. That is the same root cause one level up, in the type owners (`diff_types*`), not in `diff_symbols.py`.

The integration matrix asserts only the mangled symbol, so both rows stay visible in the raw report until a fix lands; a fix for them should add its own assertion.

## Tried and rejected

- **Export presence alone as the removal test.** Treating "the new binary still exports the same name" as "not removed" with no declaration gate silences a plain function the header dropped while the binary still exports it (`ns::bar`). That is a real API removal, so it reports `func_removed` today. The gate on the primary template's declaration is what keeps this case. Pinned by `test_plain_function_dropped_from_header_stays_a_removal`.
- **Demoting a WEAK export loss to RISK by default.** `case217` pins BREAKING without a policy. A default change needs an ADR; the binding policy already expresses the demotion. Not changed here.
- **Demoting on a build fact.** The reverted attempts in [Linkage-blind removal](linkage-blind-removal-attempted-twice-reverted-twice-the.md) apply here too. The rule uses the new export table and the header graph, not a property of the library's build.
