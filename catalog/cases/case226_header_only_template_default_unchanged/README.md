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

`Verdict: NO_CHANGE` on castxml and clang. No template-default finding. castxml
still reports the template-default gap, because it cannot record the default. A
clean-looking NO_CHANGE does not mean the defaults were checked there.

## Minimum evidence

`min_evidence: L2`.

## Why abicheck catches it

The detector emits nothing for a pair whose recorded defaults match. The test
for this case asserts that `template_param_default_changed` is absent.

## Runtime failure demonstration

None.

## Safe redesign

Not applicable.
