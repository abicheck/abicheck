# Case 227: Nested-Namespace Template Default Changed

**Category:** Template ABI | **Verdict:** 🟢 NO_CHANGE (castxml) / 🟠 API_BREAK (clang)

## Verdict and consumer impact

The case223 change, placed in the nested namespace `mylib::detail`. The default
for `U` changes from `int` to `long`. The qualified-name pairing must match the
pattern under the nested scope, otherwise a real default change under `detail::`
would be lost.

The castxml lane reads `NO_CHANGE` with the template-default gap. The clang
backend reports `template_param_default_changed` as `API_BREAK`.

## Old/new diff

| v1.h | v2.h |
|------|------|
| `namespace mylib::detail { template <typename T, typename U = int> struct Box; }` | `namespace mylib::detail { template <typename T, typename U = long> struct Box; }` |

## abicheck command

Use the same commands as case223, with `v1.h` and `v2.h` from this case.

## Expected abicheck finding

castxml (default): `Verdict: NO_CHANGE`, `Assurance: partial` with the
template-default gap.

clang: `Verdict: API_BREAK`, `template_param_default_changed`, named
`mylib::detail::Box`.

## Minimum evidence

`min_evidence: L2`, the public header.

## Why abicheck catches it

Pattern records are keyed by qualified name (`mylib::detail::Box`), so the
pairing is the same as at the top level.

## Runtime failure demonstration

None, as in case223.

## Safe redesign

As in case223: add an alias rather than change the default in place.
