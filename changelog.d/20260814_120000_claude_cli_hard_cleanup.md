### Removed

- **`compare --verify-runtime` and its inert execution probe are gone.** The
  flag had already been reduced to a documented safety no-op — it never ran
  anything and always reported `attempted=False` — so a caller passing it got
  a flag that silently did nothing. Removed outright along with
  `abicheck.runtime_probe`, the `consumer_runtime_load_failed` `ChangeKind`
  nothing could produce anymore, and the `verify-runtime` inputs on the
  composite Action, `actions/check-target`, and the `check-single`/
  `check-project` reusable workflows. Use the static `--used-by` scanner for
  undefined-symbol corroboration; it answers the same question from the
  binaries' own import/export tables and never executes an analyzed artifact.
  The `runtime_proven` evidence-level vocabulary stays in the report schema so
  an already-published report still reads back correctly.
