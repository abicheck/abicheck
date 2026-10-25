---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A header-only default template-argument change reads `NO_CHANGE` at L2 on castxml, and castxml records no default (2026-10-21)

**Status (2026-10-25): PARTIAL — every modelled backend and template family either reports the change or names the gap; clang alias and variable templates are a named gap, not a finding, and the textual scan still misses macro-generated or indirectly-included ones.** Function-template defaults (`template<class T = int> void f();`) are recorded on the clang pattern (schema v64) and read as the same `template_param_default_changed` kind. A clang function template whose default cannot be spelled (for example `sizeof(T)`) now records the defaults as `UNSUPPORTED`, and the assurance rollup counts function-template patterns too, so that case reads `partial` with `template-parameter defaults (a default could not be spelled)` (fixed 2026-10-24; before, it read `complete`). castxml cannot do better than the gap: probed with castxml 0.7.0, its XML has no element for an uninstantiated class, function, alias or variable template, and an instantiation keeps only the spelling the code wrote (`Box<char>`), so no default can be recovered (option of extracting it from castxml rejected). The clang backend models neither alias templates (`template<class T, class U = T> using P = ...`) nor variable templates. Since extractor generation 23 (2026-10-25) a clang run scans its public headers for an alias or variable template with a default template argument and names `template-parameter defaults (the clang backend does not model alias or variable templates)`, so such a change reads `partial` instead of `complete` `NO_CHANGE`. Residual: no finding is reported for it on clang (no model entity carries the default, so an unchanged default reads `partial` too), and the textual scan misses a macro-generated alias or variable template. castxml's header scan names the gap for them as well. `tests/test_template_default_assurance_gap.py` pins both sides. [ADR-085](../adr/085-template-parameter-default-arguments.md) (Accepted) landed D1 and D2: the clang backend records the defaults (schema v62), and a header-only default change is the `API_BREAK` kind `template_param_default_changed` on clang. The castxml side reports the gap but not a finding: castxml never emits the template pattern, so its verdict reads `NO_CHANGE`. The ADR-085 D3 header-text scan (schema v63) names the gap, and the run reads `partial`. The assurance rollup also reports the gap for a pattern that exists with unrecorded defaults (a pre-v62 snapshot or a non-type parameter). The ABI break is caught at L2 whenever the default-substituted type reaches an exported instantiation or a public signature. The one shape L2 does not report is a default change on a template that nothing public names and nothing exports.

Repro (header only, no `.so`):

```cpp
// v1.h                                  // v2.h
template <typename T, typename U = T>    template <typename T, typename U = int>
struct Box { T t; U u; };                struct Box { T t; U u; };
```

`abicheck dump -H v1.h` / `-H v2.h` then `abicheck compare` reports `API_BREAK` (`template_param_default_changed`) with `compile.frontend: clang`, and `NO_CHANGE` with `compile.frontend: castxml`, whose analysis is `partial` with the gap note `template-parameter defaults (castxml cannot record them)` on each side whose header declares a default (ADR-085 D3; for case224 that is the new side only). The L4 source replay reports `public_dependency_role_changed` (risk, `default_template_arg` → `field`), not `default_template_arg_changed`.

Why each backend cannot report it:

- **CastXML** emits no record at all for an uninstantiated class template, so there is no entry to carry a default argument. `tests/test_default_template_arg_l2_matrix.py` pins this.
- **Clang** emits the template pattern (`is_template_pattern`) and its `TemplateTypeParmDecl.defaultArg`. As of ADR-085 D1 the clang backend records it (`RecordType.template_param_defaults_fact`, schema v62), so this shape is no longer a clang gap.

An earlier design, a clang-only `BREAKING` detector, was rejected: a header-only edit has no binary evidence, and castxml could not reproduce the verdict. The shipped design is `API_BREAK`, which is the source-compatibility verdict ADR-084 defines for a header-level break. castxml now reports the same shape as a coverage gap, so neither backend is silent for it. Their verdicts still differ for the same header.

Fix (decided in [ADR-085](../adr/085-template-parameter-default-arguments.md), Accepted): the default is a `Fact` on the template pattern (schema v62). The header-only kind is `TEMPLATE_PARAM_DEFAULT_CHANGED` with verdict `API_BREAK` (not `DEFAULT_TEMPLATE_ARG_CHANGED`, which stays `BREAKING` for exported instantiations). The detector fires only when both sides recorded the default. Decided (ADR-085 Remaining item 1, 2026-10-23): castxml's verdict stays `NO_CHANGE` for this shape and its status reads `partial` with the gap named. Weaker evidence narrows the conclusion and is reported; it does not fabricate a finding.

Index: [Known gaps](../known-gaps.md)
