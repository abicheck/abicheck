### Fixed

- **A failed external-extractor `collect` cleanup could leave a stale,
  un-purged normalized output silently hashed into a published build-source
  pack's content identity.** When purging a failed extractor's outputs
  failed (a locked file, a permissions error), the run only marked the
  extractor `"failed"` and kept going — but `--collection-mode permissive`
  (the default) tolerates a failed extractor by design, and pack writing
  hashes every file under `normalized/` regardless of extractor status, so
  the surviving file was still folded into the pack's published identity as
  if it were valid evidence. A purge failure is a data-integrity risk, not a
  missing-evidence gap permissive mode is meant to tolerate, so it now
  aborts collection unconditionally, in every collection mode.
