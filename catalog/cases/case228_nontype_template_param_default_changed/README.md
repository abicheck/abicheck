# Case 228: Non-Type Template Parameter Default Changed

**Category:** Template ABI | **Verdict:** 🟠 API_BREAK (both backends)

## Verdict and consumer impact

`template <typename T, int N = 4> struct Buf;` becomes `N = 8`. The default is
non-type. A consumer that writes `Buf<char>` gets an array of a different size, so
the change is a real source-level change. It does not appear in any symbol.

The clang AST spells non-type defaults, and both header backends read them from
it, so the change reads as `TEMPLATE_PARAM_DEFAULT_CHANGED`, `API_BREAK`, on
castxml and clang. castxml writes no record for an uninstantiated template, so the castxml backend reads the defaults from the clang AST of the same header (its clang join), as the clang backend does. Without clang, a castxml run reads `NO_CHANGE` with run status partial and the gap `template-parameter defaults (castxml cannot record them)`.

## Old/new diff

| v1.h | v2.h |
|------|------|
| `template <typename T, int N = 4> struct Buf;` | `template <typename T, int N = 8> struct Buf;` |

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

`min_evidence: L2`.

## Why abicheck catches it

The non-type defaults `4` and `8` are both recorded (`AbiSnapshot.cxx_template_defaults`
on both backends, and `template_param_defaults_fact` on clang's pattern record),
so the two sides differ and the change is reported. A side whose defaults were
not recorded never compares equal to a recorded side.

## Runtime failure demonstration

None.

## Safe redesign

Do not change a non-type default in a public header. Add a separately named
alias with the new size.
