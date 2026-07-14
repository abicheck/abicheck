# Case 122 — Uninstantiated Template Signature Change

**Ground truth:** ⚠️ COMPATIBLE_WITH_RISK (`template_body_changed`).
Object/DWARF/header lanes return NO_CHANGE, but that is an L0–L2 missed
detection; L4 source-ABI replay proves the one canonical verdict.

## What changes

| Version | Declaration |
|---------|-------------|
| v1 | `template <typename T> T clamp(T value, int lo, int hi);` |
| v2 | `template <typename T> T clamp(T value, long lo, long hi);` |

The function template's parameter types change (`int` → `long`). The library
**instantiates nothing** — it ships the template header-only and exports only an
ordinary `library_version()` function.

## What breaks

A consumer writing `clamp<int>(x, a, b)` resolves the call against the new
parameter types and emits a *different* mangled symbol on its own side; overload
resolution and deduction can also change. No shipped binary instantiates the
template today, so nothing that currently links is broken — a consumer only
picks up the new signature the next time it recompiles against the updated
headers. That is why the canonical verdict is COMPATIBLE_WITH_RISK
(`template_body_changed`) rather than API_BREAK: a source-visible risk for
future consumers, not a proven break for any consumer that exists yet.

## Why this case exists — the limit of *artifact* analysis

This change is invisible to abicheck's artifact-comparison modes:

- **Object / DWARF mode** — the library binary is byte-identical (no
  instantiation is emitted), so there is nothing to compare.
- **Header / castxml mode** — castxml does **not** emit uninstantiated template
  declarations into its AST output, so the signature change is not modelled.

This is the boundary of comparing *built artifacts*: code that never becomes a
symbol (uninstantiated templates, never-included inline code) leaves no trace a
binary or castxml comparison can observe.

It is **not**, however, invisible to abicheck as a whole. The L4 source-ABI
replay layer (`--sources`, ADR-030) walks the real clang AST — which *does*
emit uninstantiated `FunctionTemplateDecl` nodes — and hashes each template's
subtree, including its parameter types. That hash changes here, and the L4
diff (`abicheck/buildsource/source_diff.py`) reports it as
`TEMPLATE_BODY_CHANGED` (a RISK finding: "uninstantiated public template body
changed — invisible to artifact comparison, consumers pick up the new body on
recompile"). A pure source-AST tool that diffs the headers directly *can* see
this class of change — L4 is abicheck's own answer to that gap, opt-in because
it needs a real compile context (compile flags, include paths) rather than
just the two binaries. See
[Limitations → Source-only changes](../../docs/concepts/limitations.md) and
[Evidence and detectability](../../docs/concepts/evidence-and-detectability.md).

## Reproduce manually
```bash
g++ -shared -fPIC -g v1.cpp -o libtpl_v1.so
g++ -shared -fPIC -g v2.cpp -o libtpl_v2.so

# Default (object/header) comparison — documented gap, NO_CHANGE:
abicheck compare libtpl_v1.so libtpl_v2.so \
    --header old=v1.h --header new=v2.h   # → NO_CHANGE
```

Seeing the L4 finding takes more than adding `--sources`: it needs (1) L3
compile-unit evidence (a `compile_commands.json` — a real project already has
one; here we write a one-line entry by hand) explicitly paired with `-H` so
abicheck knows *which* header is public (a bare `--sources` tree has no build
metadata declaring that, so the template is never scoped as reachable), and
(2) `--no-scope-public-headers` on `compare` — the default L2 header backend
(castxml/clang AST) does not model templates at all (see above), so the
default public-surface scoping can't recognize `clamp` as public and silently
drops the L4 finding as "internal". Verified end to end:
```bash
cat > v1.compile_commands.json <<EOF
[{"directory": "$PWD", "command": "c++ -std=c++17 -c v1.cpp -o v1.o", "file": "$PWD/v1.cpp"}]
EOF
cat > v2.compile_commands.json <<EOF
[{"directory": "$PWD", "command": "c++ -std=c++17 -c v2.cpp -o v2.o", "file": "$PWD/v2.cpp"}]
EOF

abicheck collect -H v1.h --compile-db v1.compile_commands.json \
    --source-abi --source-abi-scope headers-only -o v1.evidence
abicheck collect -H v2.h --compile-db v2.compile_commands.json \
    --source-abi --source-abi-scope headers-only -o v2.evidence

abicheck dump libtpl_v1.so -H v1.h -p v1.compile_commands.json --build-info v1.evidence --ast-frontend clang -o v1.abi.json
abicheck dump libtpl_v2.so -H v2.h -p v2.compile_commands.json --build-info v2.evidence --ast-frontend clang -o v2.abi.json

abicheck compare v1.abi.json v2.abi.json --no-scope-public-headers
    # → COMPATIBLE_WITH_RISK, template_body_changed on clamp
```
(`--ast-frontend clang` is only needed on a castxml-less host; drop it if
castxml is installed. `-p`/`--compile-db` on `dump` matters too: without it,
the L2 header AST is parsed without the build's flags and `compare` also
reports an unrelated `header_parse_context_drift` risk finding alongside
`template_body_changed`.)

## How to mitigate
For ABI-sensitive templates, ship **explicit instantiations**
(`template class Foo<int>;`) so the instantiation becomes a real symbol abicheck
can track (see `case17_template_abi`), or guard the public template API with a
source-level (header-diff) check in addition to the binary comparison.
