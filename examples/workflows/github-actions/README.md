# Workflow: gate a pull request automatically with GitHub Actions

**Task:** "How do I run this check automatically on every pull request,
without anyone remembering to run it (or writing a CI script myself)?"

This is a Phase 5 slice of the [examples/catalog split]
(../../../docs/contribute/plans/examples-catalog-split.md) — a small,
curated, task-oriented example independent of the 197-case calibration
catalog under `catalog/cases/` (which exists to calibrate detectors, not to
teach the CLI). See that plan's "What is left" section for the rest of this
curated set, not yet built.

## The project

A tiny shared library, `counter`, drops a function between releases:

```text
v1/counter.h   int counter_increment(int value);
               int counter_reset(void);
v2/counter.h   int counter_increment(int value);   -- counter_reset removed
```

## Run it locally first

The GitHub Action runs exactly this comparison — reproduce it locally to
see what it would report:

```bash
cd examples/workflows/github-actions

# Build both releases as shared libraries
python3 build_shared_lib.py -fPIC -g v1/counter.c -o libcounter_v1.so
python3 build_shared_lib.py -fPIC -g v2/counter.c -o libcounter_v2.so

# Compare, giving abicheck each side's public header for the strongest evidence
abicheck compare libcounter_v1.so libcounter_v2.so \
    --header old=v1/counter.h --header new=v2/counter.h
```

```text
Verdict: BREAKING (exit 4)

- func_removed: Public function removed: counter_reset
```

## Wire it into a pull request

The [abicheck GitHub Action](../../../docs/use/github-action.md) runs the
identical comparison as one step, no script required:

```yaml
name: ABI Check

on:
  pull_request:

jobs:
  abi-check:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write  # needed for pr-comment (default true) to post
    steps:
      - uses: actions/checkout@v6

      - name: Build old release
        run: gcc -shared -fPIC -g v1/counter.c -o libcounter_v1.so

      - name: Build new release
        run: gcc -shared -fPIC -g v2/counter.c -o libcounter_v2.so

      - uses: abicheck/abicheck@v0.5.0
        with:
          old-library: libcounter_v1.so
          new-library: libcounter_v2.so
          old-header: v1/counter.h
          new-header: v2/counter.h
          pr-comment: true
```

With `pr-comment: true`, the Action posts the same report shown above as a
comment on the pull request, and fails the check (exit `4`) so the PR can't
merge with the ABI break unreviewed.

## Next steps

- Only have one release's binary and want to catch *hygiene* issues (an
  accidental export, say) rather than compare two releases? See
  [`audit-release`](../audit-release/README.md) — same Action, `mode: scan`
  instead of the default `compare`.
- Need to gate on severity rather than the legacy verdict scheme (e.g. warn
  on additions but fail on breaks)? See
  [Severity & Exit Codes](../../../docs/use/severity.md).
- Want the exhaustive, generated list of every Action input/output straight
  from `action.yml`? See the
  [GitHub Action Inputs/Outputs Reference](../../../docs/reference/github-action-inputs.md).
