<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`pass_through_flags` hashed a path-valued operand's raw checkout-root-
  dependent string** (Codex review, PR #624 — a gap in this same PR's own
  `pass_through_flags` field, landed and caught within the review round):
  a forced-include flag like `-include /checkout-old/force.h` names a real
  file, but the field hashed the operand as opaque literal text, so
  byte-identical forced-include content extracted from `/checkout-old/...`
  vs. `/checkout-new/...` fingerprinted differently — exactly the class of
  noise this whole algorithm strips everywhere else. `pass_through_flags`
  now accepts `Sequence[str | Path]`: a `Path` element is content-hashed
  (like the unattributed system/toolchain bucket — there's no principled
  root to normalize a forced-include path against, since it need not fall
  under any declared header or `-I` directory), while a bare `str` element
  stays opaque literal text. A `"path:"`/`"str:"` tag prevents the two
  categories from colliding.
