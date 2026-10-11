---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Closure-parameterized non-ctor functions still churn (func_params_changed) on lambda line shifts; '?' ctor param unexplained

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** Type-level marker fix, ctor/dtor binary-evidence fix and L5 declaration_renamed fix (model.graph_identity.closure_location_free_identity) are recorded as landed. Still open: non-ctor func_params_changed/template_param_type_changed on closure-parameterized symbols, the mangled-only surface filter gap, and residual (2), the literal '?' ctor parameter.

`name_classification._ANONYMOUS_TYPE_MARKERS` did not
recognize clang's own closure spelling (`(lambda at <path>:<line>:<col>)`,
or the `(lambda:<file>:<line>:<col>)` form
`strip_anonymous_type_location` normalizes it to), so a template
instantiated over a closure —
`raii_guard<(lambda:task_group.h:522:26)>` — was treated as ordinary ABI
surface. That half is fixed: the marker list now covers it, and an
unrelated edit that merely shifts the lambda's line no longer produces a
`type_removed`/`type_added` pair. **Two residuals were reproduced and are
not closed.**

(1) *Function-level findings on closure-parameterized symbols.* A
destructor of that instantiation is still reported as a BREAKING
`func_removed` (plus `func_added` for the shifted spelling), and a public
function taking the closure-parameterized type by value still produces
`func_params_changed`/`template_param_type_changed`, because the
*mangled symbol* and the *parameter type spelling* both embed the
closure's source coordinates. Reproduced directly through `compare()`
with the two spellings differing only in the line number.
`is_non_abi_surface_type` is a *type*-name predicate and is not consulted
on either path, so the marker fix cannot reach them. **Demoting the
removal is not the fix, and the reason matters**: this codebase already
states the correct reading in `change_registry`'s own
`unnamed_type_in_public_abi` entry ("the Itanium mangling of unnamed
types is per-translation-unit and compiler-ordering dependent ... so
exporting one is an ABI time bomb: a rebuilt consumer can fail to resolve
the symbol"). A consumer *already linked* against the old numbering
really does fail at load when the numbering shifts — so the removal is a
genuine break, and softening it would hide a real one. That is the same
direction of error the linkage-blind-removal entry above was reverted
twice for. Nor is "strip `:<line>:<col>` before comparing two spellings"
a free win: `strip_anonymous_type_location`'s own docstring records why
the coordinates are kept (two unrelated lambdas in one header collapse to
one key, silently overwriting an entry in every name-keyed map that
consumes the spelling), and this pass has no real oneTBB snapshot to
check a narrower "normalize only for pairwise spelling comparison, never
for keys" variant against. What a correct fix needs is the annotation
route ADR-style precedent already establishes elsewhere in this file
("Annotate; never remove"): correlate such a finding with the existing
`unnamed_type_in_public_abi` RISK signal so a reader can see *why* the
symbol churned, rather than removing it from `changes` or lowering its
verdict. That is a new correlation pass with its own identity question
(which finding covers which), not a marker-list edit.

**Update: the ctor/dtor half of (1) is now closed via binary evidence,
not annotation.** A later report against real oneTBB 2021.13.0 →
2022.3.0 binaries reproduced exactly this shape at scale:
`demote_lambda_closure_unexported_findings` (`diff_templates.py`) had
already been added to demote a `FUNC_PARAMS_CHANGED`/
`TEMPLATE_PARAM_TYPE_CHANGED`/`TEMPLATE_RETURN_TYPE_CHANGED` finding
whose reported symbol is confirmed absent from BOTH sides' real ELF
exported symbol table (never escalates; fails closed when no ELF
evidence exists on either side) — but it deliberately excluded every
castxml-synthesized ctor/dtor key
(`dumper_castxml.is_synthetic_ctor_key`/`is_synthetic_dtor_key`), on the
correct-as-far-as-it-went grounds that such a key is *never* itself a
real exported symbol (castxml synthesizes it specifically because it
could not produce one), so a membership check against the key text
would always read "confirmed absent" — vacuously, not because anything
was actually verified. That exclusion left every destructor/constructor
of a closure-parameterized instantiation permanently un-demotable,
which is exactly the residual (1) describes and exactly what the oneTBB
report reproduced: 5 breaking `func_removed` findings, all on synthetic
ctor/dtor keys naming
`tbb::detail::raii_guard<(lambda:task_group.h:522:26)>`/
`try_call_proxy<...>`/`task_arena_function<...>`/
`delegated_function<...>`, paired 1:1 with 5 compatible `func_added`
findings differing only in the lambda's line number.

Fixed by asking the binary a question the synthetic key's *text* can
answer honestly, even though the key itself is not a real symbol: is
the owning class/class-template exported under ANY instantiation at
all, on either side?
`finding_identity_ctor_dtor.synthetic_ctor_dtor_template_base_name`
recovers the owning scope from the key (that same module's own
`synthetic_ctor_scope` for a ctor key's
depth-aware `scope(params)` split; a plain prefix strip for a dtor key),
reduces it to the bare, template-argument-stripped template name via
`type_reachability._bare_type_name` plus a top-level `<` scan
(`raii_guard<(lambda:...)>` → `raii_guard`), and
`finding_identity_ctor_dtor.itanium_source_name_token` renders that name
as its Itanium `<source-name>` encoding (`"raii_guard"` → `"10raii_guard"`,
keyed on the identifier's encoded UTF-8 **byte** length rather than its
Python character count, so a non-ASCII class name still produces the
correct token) — a literal substring every real mangled symbol naming
that class as a scope component must contain (Itanium C++ ABI §5.1.1),
checked directly against the raw exported names with no external
demangler invoked, the same "structural, not textual" preference
`diff_cxx_rules.itanium_scope_components` already established for this
codebase. Both helpers live in `finding_identity_ctor_dtor.py`, not
`diff_templates.py` itself (which only keeps the classification/
modulation entry point, `demote_lambda_closure_unexported_findings`):
`diff_templates.py` is one of ADR-061's `debt.yaml`-tracked
no-growth-baselined legacy files, `finding_identity_ctor_dtor.py` already
owns every other castxml synthetic-ctor/dtor-key helper and had headroom
under the flat 800-line production limit, and a brand-new flat `diff_*`
sibling module is explicitly rejected by `scripts/check_architecture.py`'s
`frozen_root_families` list (confirmed by trying it — `[frozen-root-family]`
and `[root-module]` findings) — while a real `policy/` package migration
for just this one function was investigated and found to cascade into
`unclassified-import` findings for every one of its ~8 currently-flat,
not-yet-layer-classified dependencies (`checker_policy`, `diff_symbols`,
`dumper_castxml`, `elf_symbol_filter`, `type_reachability`,
`name_classification`, ...), which is its own separate, much larger
ADR-061 migration slice, not a follow-up to this fix. A template with
zero exported members under any instantiation on either side is demoted
(`Verdict.COMPATIBLE_WITH_RISK`,
`modulation_rule="lambda_closure_never_exported"`, same ADR-025 hook,
never removed); a template that *does* export some other instantiation
is left exactly as severe as the detector made it, since this check
cannot rule out that the specific closure-parameterized instantiation
was the one a consumer actually linked against.

Deliberately narrower than reconstructing the *exact* per-instantiation
mangling: the real Itanium mangling of a closure-type template argument
uses the compiler's own unnamed-type encoding (`Ul<parameter-types>E_`),
never castxml's `(lambda:file:line:col)` spelling, so there is no way to
derive the precise mangled substring for one specific instantiation from
the snapshot text alone — checking the template's own name is the safe,
strictly more conservative substitute this function's whole design
(fail closed, never escalate) already calls for. See
`tests/test_lambda_closure_function_demotion.py`'s
`TestSyntheticCtorDtorKeysDemotedWhenTemplateNeverExported`/
`TestSyntheticCtorDtorKeysNotDemotedWhenTemplateIsExported` for both
directions, verified against the exact class names from the oneTBB
report.

**A review round on the same fix found a real gap in the substring
search itself, not in the demotion logic around it.** Six `std::` names
(`allocator`, `basic_string`, `basic_istream`, `basic_ostream`,
`basic_iostream`) carry a *fixed, mandatory* Itanium ABI substitution
(`Sa`/`Sb`/`Si`/`So`/`Sd`, C++ Itanium ABI §5.1.2) — the mangler always
emits the abbreviation instead of the literal source-name, even on the
first occurrence in a symbol. A real `std::allocator<int>::allocator()`
mangles to `_ZNSaIiEC1Ev`, never to anything containing the literal
substring `"9allocator"` — so a synthetic `std::allocator<(lambda:...)>`
finding's literal-token search would read that class as "never
exported" regardless of the truth, silently demoting a genuine removal.
Fixed with `finding_identity_ctor_dtor.itanium_standard_substitution_
token`, checked alongside the literal token whenever the owning class's
qualified name is exactly one of the six; see
`tests/test_lambda_closure_function_demotion.py`'s
`TestStdAllocatorSyntheticKeyNotFalselyDemoted` for the exact
counterexample from review, reproduced and fixed.

Two related residuals from the same report. The compatible `func_added`
churn is ordinary add/remove pairing (not itself a bug), left as-is.

~~The risk `declaration_renamed` findings elsewhere in the same report (7
further ones) is pure noise from the lambda's identity embedding its
source `line:col` rather than an ordinal position — closing that needs
changing how a closure is *identified*, a materially larger change to
`name_classification`/the castxml and clang backends' own closure naming
than this fix's binary-evidence check, and is not attempted here.~~
**Update: fixed, narrower than the originally-scoped "change how a
closure is identified" (fresh report, real oneTBB 2021.12.0 -> 2023.0.0,
139 `declaration_renamed` L5 findings against oneDNN's 3 for a comparable
corpus size).** Tracing a representative sample confirmed the same
mechanism this entry already names: `buildsource.graph_reconcile`'s
structural-context tier correctly PAIRS an unrelated-edit-shifted
closure with its own unchanged predecessor (that matching evidence never
reads the coordinate text at all), but `_classify_outcome` then compared
the two nodes' *literal* qualified-name spelling — which embeds the
closure's own `:line:col` — to decide "renamed", so every closure an
unrelated header edit merely shifted read as a rename. Rather than the
originally-scoped identity-naming rewrite (which risks the exact
same-header-collision trade-off `strip_anonymous_type_location`'s own
docstring documents for *matching*), the actual fix is narrower and
provably safe: `model.graph_identity.closure_location_free_identity`
drops a closure/anonymous-tag marker's basename+coordinates entirely (not
merely the checkout-root prefix `_normalize_graph_identity` already
strips) for the *outcome-classification* comparison only, never for
matching/merging — the pair was already established by tier evidence that
never consults this text, so no new merge rides on it, and a genuine
cross-file move is still caught by the separate, coordinate-text-blind
`old_file`/`new_file` check right below it (reclassifying it
`declaration_moved` instead of the misleading `declaration_renamed`,
never hiding the file change). See
`tests/test_graph_reconcile_closure_rename.py` — both the oneTBB-shaped
regression cases and a standalone property-test class for the new
primitive (idempotence, location/basename invariance, parenthesized/bare
spelling agreement, marker-kind non-collapse, never-raises on arbitrary
text) per this file's own "Primitive-level property tests" guidance,
since this is a reusable identity-comparison helper, not a one-off
patch. The separate, still-open gap about the L5 graph's own node *ids*
not sharing the flat snapshot's ordinal renumbering (this file's own
"The L5 source graph's own node identities are never renumbered..."
entry, elsewhere in this file) is unaffected by this fix — it is about
node-id/flat-field spelling agreement, not the rename-vs-not-renamed
classification this fix corrects.

And a public-surface filter gap (ELF-only, mangled-only symbols
such as `std::once_flag::_Prepare_execution<lambda>`'s internal guard
thunks, which `surface.py` cannot scope-classify because it never
demangles) is a separate detector, not this one, and is likewise not
addressed here.

(2) *A constructor key rendering a literal `?` parameter.* Traced to two
legitimate "type not recoverable" sentinels rather than to a formatting
bug: `dwarf_snapshot._process_param` returns `Param(type="?")` for a
`DW_TAG_formal_parameter` carrying no `DW_AT_type`, and
`dumper_castxml._type_name` returns `"?"` when a referenced type id does
not resolve in the XML (a closure class declared inside a function body
is exactly the shape that goes unemitted). Both are honest unknowns, and
which one produced the reported key cannot be determined without the
original snapshot, which this pass does not have. Guessing a
substitution here would replace a visible unknown with a fabricated
spelling, which is strictly worse; closing it needs the real artifact (or
a live castxml/DWARF repro of a closure-parameterized ctor) first.
