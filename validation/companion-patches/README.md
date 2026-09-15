# External integration companion patches

`svs-pr387-report-exports.patch` was prepared against intel/ScalableVectorSearch
PR 387 head `b22c521ef415a147e791e9aee03221cb8eaa5381` on 2026-09-14. It is not
applied or published upstream. Apply from an SVS checkout at that revision:

```bash
git apply /path/to/abicheck/validation/companion-patches/svs-pr387-report-exports.patch
```

The patch performs one comparison, writes bounded review plus detailed Markdown
and JSON artifacts, preserves abicheck's exit status without `tee`, places only
the bounded review in `$GITHUB_STEP_SUMMARY`, and uploads successfully-written
report files even after a completed breaking comparison.
