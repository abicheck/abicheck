# Explicit function and variable instances: removal is castxml-blind and class-scope-ambiguous

**Status (2026-10-25): NARROWED.** A function or static data member that an
old header spelled explicitly (`extern template`, `template` instantiation,
explicit specialization) and the new header dropped is `func_removed` /
`var_removed` on the clang header backend (`abicheck/extract/headers/clang/explicit_instantiation.py`,
schema v65). It was `func_visibility_changed` ("changed to hidden") before.

Still open:

- **castxml never records the declaration kind.** Its instance of an
  `extern template` sits on the primary template's line, so it cannot be told
  from an implicit instance. Its side stays `NOT_COLLECTED` and the export-loss
  path keeps `func_visibility_changed`. On the gcc/castxml validator lane,
  `case85` therefore stays `KINDS_MISMATCH`. This is reported, not masked: without
  `ABICHECK_STRICT_KINDS` it is a report line, and `ground_truth.json` is unchanged.
  Closing it needs castxml to expose the kind, or a per-frontend `known_gap` with
  an observed value per key (a schema change left for a follow-up).
- **Two instances of one class template in one translation unit share a
  scope.** The text dump does not spell a specialization's arguments, so
  `Box<float>` and `Box<int>` both key as `Box`. A member declared by both reads
  `UNSUPPORTED`, never a guessed kind.
- **Cost.** A translation unit whose headers spell an explicit form pays one
  extra clang text pass: about 20 ms on a small header, and about 85% of the
  JSON pass on a 3600-line header with 400 class templates. The pass is not
  cached, so a warm and a cold dump read the same facts.

`false-positive` — On castxml, an explicitly dropped instance keeps the
`func_visibility_changed` wording even though its declaration is gone.
