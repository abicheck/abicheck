# Case 226: Header-Only Template Default Unchanged

**Category:** Template ABI | **Verdict:** 🟢 NO_CHANGE (both backends)

## Verdict and consumer impact

Both sides declare `template <typename T, typename U = int> struct Box;`. v2 only
rewords a comment. No default changes, so nothing is reported. This is the
negative control for case223 to case225 and case227.

## Old/new diff

Comment-only change. The default is `int` on both sides.

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

## Expected abicheck finding

`Verdict: NO_CHANGE` on castxml and clang. No template-default finding and no
template-default gap: both backends recorded the default (castxml through its
clang join). Without clang, castxml names the gap instead, because then it has
not checked the defaults.

## Minimum evidence

`min_evidence: L2`.

## Why abicheck catches it

The detector emits nothing for a pair whose recorded defaults match. The test
for this case asserts that `template_param_default_changed` is absent.

## Runtime failure demonstration

None.

## Safe redesign

Not applicable.
