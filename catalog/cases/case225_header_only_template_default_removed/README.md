# Case 225: Header-Only Template Default Removed

**Category:** Template ABI | **Verdict:** 🟢 NO_CHANGE (castxml) / 🟠 API_BREAK (clang)

## Verdict and consumer impact

v1 declares `template <typename T, typename U = int> struct Box;`. v2 removes the
default. Existing source that writes the one-argument form `Box<char>` stops
compiling. That is a source break, not a binary break, because no symbol depends
on the default.

castxml cannot record defaults. Its validator-lane result is
`NO_CHANGE` with the template-default gap, so it does not see this source break.
clang records the removal and reports `template_param_default_changed` as
`API_BREAK`. Read the castxml result as a known blind spot, not a clean pass.

## Old/new diff

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

Switch `frontend: clang` to see the clang result.

## Expected abicheck finding

castxml (default): `Verdict: NO_CHANGE`, `Assurance: partial` with the
template-default gap.

clang: `Verdict: API_BREAK`, `template_param_default_changed`.

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
