# Case 223: Header-Only Template Default Changed

**Category:** Template ABI | **Verdict:** 🟠 API_BREAK (both backends)

## Verdict and consumer impact

`mylib::Box<T, U = int>` is a header-only class template. Its default for `U`
changes from `int` to `long`. Nothing exports `Box`, and no public signature
names it, so no symbol changes. A consumer that writes `mylib::Box<char>` still
compiles against v2, but it now selects `Box<char, long>` instead of
`Box<char, int>`. The change is source-level, not binary.

Both header backends report `template_param_default_changed` as `API_BREAK`.
castxml writes no record for an uninstantiated template, so the castxml backend reads the defaults from the clang AST of the same header (its clang join), as the clang backend does. Without clang, a castxml run reads `NO_CHANGE` with run status partial and the gap `template-parameter defaults (castxml cannot record them)`.

## Old/new diff

| v1.h | v2.h |
|------|------|
| `template <typename T, typename U = int> struct Box;` | `template <typename T, typename U = long> struct Box;` |

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

castxml (default) and clang:

```text
Verdict: API_BREAK
- template_param_default_changed: class template Box has a different
  default for parameter 1 (int -> long)
```

## Minimum evidence

`min_evidence: L2`, the public header.

## Why abicheck catches it

Both backends record each template's defaults from the clang AST
(`AbiSnapshot.cxx_template_defaults`); the clang dumper also records them on the
class-template pattern. The template is paired by qualified name and the
recorded defaults are compared (`compare/template_default_table.py`,
`compare/template_param_defaults.py`). castxml writes no record for an
uninstantiated template, so it reads the defaults through its clang join.

## Runtime failure demonstration

None. Nothing is exported, so no binary changes. The break is at the source
level, and a consumer rebuilds.

## Safe redesign

Do not change a default in a public header without a deprecation window. Add a
new template alias, for example `template <typename T> using BoxLong = Box<T, long>;`,
and leave `Box`'s default unchanged.
