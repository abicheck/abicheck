---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A header-only default template-argument change reads `NO_CHANGE` at L2 on castxml, and castxml records no default (2026-10-21)

**Status (2026-10-22): PARTIAL — clang closed; castxml reports the gap (ADR-085 D3) but its verdict stays `NO_CHANGE`.** [ADR-085](../adr/085-template-parameter-default-arguments.md) (Accepted) landed D1 and D2: the clang backend records the defaults (schema v62), and a header-only default change is the `API_BREAK` kind `template_param_default_changed` on clang. The castxml side is still open: castxml never emits the template pattern, so it reads `NO_CHANGE` and reports no gap yet. The remaining work is ADR-085 D3's castxml header-text scan, which would turn that `NO_CHANGE` into a coverage gap. Until it lands, a castxml run over such a header is silent on this shape, and the assurance rollup reports the gap only for a pattern that exists with unrecorded defaults (a pre-v62 snapshot or a non-type parameter). The ABI break is caught at L2 whenever the default-substituted type reaches an exported instantiation or a public signature. The one shape L2 does not report is a default change on a template that nothing public names and nothing exports.

Repro (header only, no `.so`):

```cpp
// v1.h                                  // v2.h
template <typename T, typename U = T>    template <typename T, typename U = int>
struct Box { T t; U u; };                struct Box { T t; U u; };
```

`abicheck dump -H v1.h` / `-H v2.h` then `abicheck compare` reports `API_BREAK` (`template_param_default_changed`) with `compile.frontend: clang`, and `NO_CHANGE` with `compile.frontend: castxml`, whose analysis is `partial` with the gap note `template-parameter defaults (castxml cannot record them)` on each side (ADR-085 D3). The L4 source replay reports `public_dependency_role_changed` (risk, `default_template_arg` → `field`), not `default_template_arg_changed`.

Why each backend cannot report it:

- **CastXML** emits no record at all for an uninstantiated class template, so there is no entry to carry a default argument. `tests/test_default_template_arg_l2_matrix.py` pins this.
- **Clang** emits the template pattern (`is_template_pattern`) and its `TemplateTypeParmDecl.defaultArg`. As of ADR-085 D1 the clang backend records it (`RecordType.template_param_defaults_fact`, schema v62), so this shape is no longer a clang gap.

An earlier design, a clang-only `BREAKING` detector, was rejected: a header-only edit has no binary evidence, and castxml could not reproduce the verdict. The shipped design is `API_BREAK`, which is the source-compatibility verdict ADR-084 defines for a header-level break. castxml now reports the same shape as a coverage gap, so neither backend is silent for it. Their verdicts still differ for the same header.

Fix (decided in [ADR-085](../adr/085-template-parameter-default-arguments.md), Accepted): the default is a `Fact` on the template pattern (schema v62). The header-only kind is `TEMPLATE_PARAM_DEFAULT_CHANGED` with verdict `API_BREAK` (not `DEFAULT_TEMPLATE_ARG_CHANGED`, which stays `BREAKING` for exported instantiations). The detector fires only when both sides recorded the default. Still open: whether castxml's verdict should change for this shape, or only its status (ADR-085 Remaining item 1). The coverage gap itself is landed.

Index: [Known gaps](../known-gaps.md)
