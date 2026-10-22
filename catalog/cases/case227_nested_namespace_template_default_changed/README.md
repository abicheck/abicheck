# Case 227: Nested-Namespace Template Default Changed

**Category:** Template ABI | **Verdict:** 🟡 COMPATIBLE_WITH_RISK (castxml) / 🟠 API_BREAK (clang)

## Verdict and consumer impact

The case223 change, placed in the nested namespace `mylib::detail`. The default
for `U` changes from `T` to `long`. Qualified-name pairing must match the
pattern under the nested scope, otherwise a real default change under
`detail::` would be lost.

Results match case223: the castxml lane reports the risk
`public_dependency_role_changed`, with the template-default gap. clang
reports `template_param_default_changed` as `API_BREAK`.

## Old/new diff

## Old/new diff

| v1.h | v2.h |
|------|------|
| `namespace mylib::detail { template <typename T, typename U = T> struct Box; }` | `namespace mylib::detail { template <typename T, typename U = long> struct Box; }` |

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

castxml (default): `Verdict: COMPATIBLE_WITH_RISK` with
`public_dependency_role_changed` (`mylib::detail::Box` still depends on `T`, whose
role changes from `default_template_arg` to `field`), `Assurance: partial` with the
template-default gap, and the same unconfirmed-compatibility warning as case223.

clang: `Verdict: API_BREAK`, `template_param_default_changed`, named
`mylib::detail::Box`.

## Minimum evidence

`min_evidence: L2`.

## Why abicheck catches it

Pattern records are keyed by qualified name (`mylib::detail::Box`), so the pairing
is the same as at the top level.

## Runtime failure demonstration

None, as in case223.

## Safe redesign

As in case223: add an alias rather than change the default in place.
