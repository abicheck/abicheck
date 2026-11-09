# Case 225: Header-Only Template Default Removed

**Category:** Template ABI | **Verdict:** 🟠 API_BREAK (both backends)

## Verdict and consumer impact

v1 declares `template <typename T, typename U = int> struct Box;`. v2 removes the
default. Existing source that writes the one-argument form `Box<char>` stops
compiling. That is a source break, not a binary break, because no symbol depends
on the default.

Both header backends record the removal and report
`template_param_default_changed` as `API_BREAK`. castxml writes no record for an uninstantiated template, so the castxml backend reads the defaults from the clang AST of the same header (its clang join), as the clang backend does. Without clang, a castxml run reads `NO_CHANGE` with run status partial and the gap `template-parameter defaults (castxml cannot record them)`.

## Old/new diff

| v1.h | v2.h |
|------|------|
| `template <typename T, typename U = int> struct Box;` | `template <typename T, typename U> struct Box;` |

## abicheck command

```bash
cat > .abicheck.yml <<'EOF'
compile:
  frontend: castxml
  lang: c++
EOF
abicheck dump -H v1.h --config .abicheck.yml --depth headers -o old.json
abicheck dump -H v2.h --config .abicheck.yml --depth headers -o new.json
abicheck compare old.json new.json
```

`frontend: clang` gives the same result.

## Expected abicheck finding

castxml (default) and clang: `Verdict: API_BREAK`, `template_param_default_changed`.

## Minimum evidence

`min_evidence: L2`, the public header.

## Why abicheck catches it

The recorded default for `U` goes from `int` to `None`. That is a spelling
change, detected the same way as case224.

## Runtime failure demonstration

None. Compilation fails for consumers that use the defaulted form.

## Safe redesign

Keep the default. If it must go, deprecate it first and provide an explicit
alias.
