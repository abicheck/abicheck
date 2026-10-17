# case218 — a GLOBAL export loss stays BREAKING under a `binding: weak` rule

## Verdict and consumer impact

This is the sibling of `case217_export_loss_weak_binding_demoted`. It uses the
same `policy.yaml` and the same three WEAK losses, and NEW also drops
`ns::strong_fn`, a GLOBAL export declared in the public header. A consumer that
calls `strong_fn` fails to link or bind against NEW, so this is a real break.

The `binding: weak` rule matches only WEAK findings. The `strong_fn` finding
carries `symbol_binding: "global"`, so the rule does not match it and the run
stays `BREAKING`, exit code `4`. The WEAK losses are still demoted to a risk;
the verdict is set by the strong removal alone.

## Old/new diff

| | v1 (OLD) | v2 (NEW) |
|---|---|---|
| `hdr_inline()` | called from `Pub::g()`, emitted WEAK | not emitted (as in case217) |
| `weak_undeclared()` | `__attribute__((weak))`, not in any header | not defined |
| `detail::P<float>` ctor | instantiated by `Pub::q()` | not instantiated |
| `strong_fn()` | GLOBAL, declared and defined | declared in `v2.h`, **not defined** |

## abicheck command

```bash
abicheck dump libv1.so -H v1.h -o v1.json
abicheck dump libv2.so -H v2.h -o v2.json
rm libv1.so
abicheck compare v1.json v2.json --policy policy.yaml
```

## Expected abicheck finding

- Verdict: `BREAKING`, exit code `4`.
- The WEAK findings (`func_visibility_changed` on `hdr_inline`,
  `func_removed_elf_only` on `weak_undeclared` and the `P<float>` constructor)
  carry `symbol_binding: "weak"` and are reclassified to `risk`.
- `func_visibility_changed` on `strong_fn` carries `symbol_binding: "global"`
  and is not reclassified, so it keeps the run at `BREAKING`.

## Minimum evidence

`min_evidence: L2` (public headers), as in case217.

## Why abicheck catches it

Same mechanism as case217: the ELF `.dynsym` gives each finding its old-side
binding, and the `binding:` selector compares that value
(`policy/selectors.py`). Because the selector compares the value, a GLOBAL
finding is never matched by a rule written for WEAK ones.
