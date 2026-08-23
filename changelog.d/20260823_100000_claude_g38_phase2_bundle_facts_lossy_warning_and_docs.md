### Fixed

- **`--bundle-facts-out` now warns when a stranded library can't be fully
  resolved, instead of silently persisting a lossy entry (Codex/CodeRabbit
  review, fresh evidence).** The stranded-library fallback already degrades
  to a bare `ElfMetadata`-only snapshot (no functions/types/headers) when
  the real, full resolve itself raises — a deliberate choice to avoid
  aborting the whole `--bundle-facts-out` write over one stranded library,
  the same "only promise what was actually captured" principle
  `build_bundle_snapshot()` already applies. But that degrade previously
  happened silently, leaving a user comparing the stored baseline later
  with no way to know that one library's entry is incomplete. The fallback
  now prints a warning naming the affected library when this happens.
- **Corrected two multi-binary bundle-analysis doc claims that
  contradicted the code and the page's own later sections (Codex review,
  fresh evidence).** The scoping-vs-policy terminology note stated
  scoping was "the only thing that filters findings", overlooking that
  suppression is a separate, third filtering mechanism the page itself
  relies on a few sections later. And a bolded claim that policy
  reclassification "does not reach bundle findings at all, not even the
  three named built-in profiles" directly contradicted the very next
  paragraph (and `BundleDiffResult.bundle_verdict`'s real implementation),
  which correctly explains that a built-in profile name *does* reach
  bundle-level classification — only a custom YAML `PolicyFile` document
  does not. Both corrected to match the code.
