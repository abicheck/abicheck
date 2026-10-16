# case217 — WEAK export loss demoted to a risk by a `binding: weak` rule

## Verdict and consumer impact

NEW drops three WEAK exports that OLD had: a header-declared `inline` function
(`ns::hdr_inline`), a WEAK export no public header declares
(`ns::weak_undeclared`), and the `ns::detail::P<float>` constructor from an
internal template namespace. Without a policy, abicheck reports them as
BREAKING (`func_visibility_changed`, `func_removed_elf_only`) and exits 4.

These are copies the consumer's own translation units emit as well, so the
loss is a risk, not a hard break. The policy in `policy.yaml` says so with a
`binding: weak` selector scoped to the library's namespace. With that policy
abicheck reports `COMPATIBLE_WITH_RISK` and exits 0.

This is the case that the `binding:` selector did not reach before PRs #1629
and #1630: the findings carried no `symbol_binding`, so the rule never matched.

## Old/new diff

| | v1 (OLD) | v2 (NEW) |
|---|---|---|
| `hdr_inline()` | called from `Pub::g()`, emitted WEAK | `Pub::g()` no longer calls it, not emitted |
| `weak_undeclared()` | `__attribute__((weak))`, not in any header | not defined |
| `detail::P<float>` ctor | instantiated by `Pub::q()` | `Pub::q()` returns `nullptr`, not instantiated |
| `strong_fn()` | GLOBAL, declared | GLOBAL, unchanged |

## abicheck command

Dump both sides from headers (L2), delete the OLD binary, then compare with the
policy:

```bash
abicheck dump libv1.so -H v1.h -o v1.json
abicheck dump libv2.so -H v2.h -o v2.json
rm libv1.so
abicheck compare v1.json v2.json --policy policy.yaml
```

## Expected abicheck finding

- Verdict: `COMPATIBLE_WITH_RISK`, exit code `0`.
- Without `--policy`: `BREAKING`, exit code `4`.
- Kinds (both runs): `func_visibility_changed` on `hdr_inline`,
  `func_removed_elf_only` on `weak_undeclared` and the `P<float>` constructor.
  Each carries `symbol_binding: "weak"`. Under the policy each is reclassified
  to `risk`.

## Minimum evidence

`min_evidence: L2`. Headers are needed to tell a header-declared inline
function (`hdr_inline`) from a symbol no header declares. Without `-H` the
run also reports `visibility_leak` and `public_surface_shrank`, which the
binding rule does not cover, so the verdict stays `BREAKING` there.

## Why abicheck catches it

The ELF `.dynsym` tables of both sides give the removals and their WEAK
binding (`elf_metadata.py`). The header AST (castxml) tells the detector that
`hdr_inline` is still declared, so the removal is classified as a visibility
change instead of an API removal. `compare/undeclared_exports.py` and the
template detectors stamp the old-side binding on each finding
(`Change.symbol_binding`); the `binding:` selector matches on that field
(`policy/selectors.py`, `EXPORT_LOSS_KINDS`).

## Sibling

`case218_export_loss_strong_binding_stays_breaking` uses the same `policy.yaml`
and the same WEAK losses, plus the removal of a GLOBAL export. Its verdict stays
`BREAKING`, which shows the selector does not demote strong exports.
