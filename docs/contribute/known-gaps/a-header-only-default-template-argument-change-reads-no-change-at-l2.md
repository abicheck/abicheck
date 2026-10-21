---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A header-only default template-argument change reads `NO_CHANGE` at L2 on both backends, and clang records no default (2026-10-21)

**Status (2026-10-21): OPEN — no detector change yet; the fix direction is decided in [ADR-085](../adr/085-template-parameter-default-arguments.md) (Proposed).** Measured in G28 Phase 5; see the plan's [investigation result](../plans/g28-castxml-clang-l2-parity-hardening.md). On acceptance, a header-only default change becomes an `API_BREAK` kind on clang, and castxml reports it as an explicit coverage gap instead of `NO_CHANGE`. The ABI break is caught at L2 whenever the default-substituted type reaches an exported instantiation or a public signature. The one shape L2 does not report is a default change on a template that nothing public names and nothing exports.

Repro (header only, no `.so`):

```cpp
// v1.h                                  // v2.h
template <typename T, typename U = T>    template <typename T, typename U = int>
struct Box { T t; U u; };                struct Box { T t; U u; };
```

`abicheck dump -H v1.h` / `-H v2.h` then `abicheck compare` reports `NO_CHANGE` with either `compile.frontend: castxml` or `clang`. The L4 source replay reports `public_dependency_role_changed` (risk, `default_template_arg` → `field`), not `default_template_arg_changed`.

Why each backend cannot report it:

- **CastXML** emits no record at all for an uninstantiated class template, so there is no entry to carry a default argument. `tests/test_default_template_arg_l2_matrix.py` pins this.
- **Clang** emits the template pattern (`is_template_pattern`) and its `TemplateTypeParmDecl.defaultArg`, but `dumper_clang.py` does not record template-parameter defaults. The data is available; nothing reads it.

Why no clang-only detector was added: it would return `BREAKING` for a header-only edit that castxml cannot reproduce, so the same input would decide differently by frontend, and the verdict would have no binary evidence. The source-level consequence (a consumer's own `Box<char>` now means a different type) is a behavior change, not an ABI break, and belongs in a risk-level or source-compat kind that does not exist yet.

Fix direction (decided in [ADR-085](../adr/085-template-parameter-default-arguments.md), Proposed): record the default as a `Fact` on the template pattern (schema v61), add a header-only `TEMPLATE_PARAM_DEFAULT_CHANGED` kind with verdict `API_BREAK` (not `DEFAULT_TEMPLATE_ARG_CHANGED`, which stays `BREAKING` for exported instantiations), and have castxml report the fact as UNKNOWN, which is a coverage gap rather than a clean result. The detector fires only when both sides recorded the default.

Index: [Known gaps](../known-gaps.md)
