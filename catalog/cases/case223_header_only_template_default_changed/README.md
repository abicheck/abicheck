# Case 223: Header-Only Template Default Changed

**Category:** Template ABI | **Verdict:** 🟢 NO_CHANGE (castxml) / 🟠 API_BREAK (clang)

## Verdict and consumer impact

`mylib::Box<T, U = int>` is a header-only class template. Its default for `U`
changes from `int` to `long`. Nothing exports `Box`, and no public signature
names it, so no symbol changes. A consumer that writes `mylib::Box<char>` still
compiles against v2, but it now selects `Box<char, long>` instead of
`Box<char, int>`. The change is source-level, not binary.

On the default castxml lane the run reads `NO_CHANGE`, with run status partial
and the template-default assurance gap: castxml cannot record template parameter
defaults, so it cannot see this change. The clang backend records the defaults
and reports `template_param_default_changed` as `API_BREAK`.

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

Switch `frontend: clang` to see the clang result.

## Expected abicheck finding

castxml (default):

```text
Verdict: NO_CHANGE
Assurance: partial
  gap: template-parameter defaults (castxml cannot record them)
```

clang:

```text
Verdict: API_BREAK
- template_param_default_changed: class template Box has a different
  default for parameter 1 (int -> long)
```

## Minimum evidence

`min_evidence: L2`, the public header.

## Why abicheck catches it

The clang dumper records `TemplateTypeParmDecl.defaultArg` for class templates.
`compare/template_param_defaults.py` pairs the pattern by qualified name and
compares the recorded defaults. castxml writes no record for an uninstantiated
template, so its snapshot stays UNKNOWN and the run reports the gap.

## Runtime failure demonstration

None. Nothing is exported, so no binary changes. The break is at the source
level, and a consumer rebuilds.

## Safe redesign

Do not change a default in a public header without a deprecation window. Add a
new template alias, for example `template <typename T> using BoxLong = Box<T, long>;`,
and leave `Box`'s default unchanged.
