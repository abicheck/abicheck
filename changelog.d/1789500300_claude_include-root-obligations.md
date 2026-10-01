### Fixed

- **A header reached only through `#include` no longer counts as a confirmed
  missing export.** With `-H include/pvxs/iochooks.h -I include`, a sibling
  library's `version.h` was treated as this library's public surface, and
  each symbol it declared was reported as a high-confidence
  `public_not_exported`. Each `-H` file is now recorded as an ownership
  target root, like a `-H` directory already was. A declaration outside every
  root is still reported, but at low confidence and described as "not
  established", because the run cannot tell whether this library owes that
  export. Declarations in headers named by `-H`, or under a `-H` directory,
  are judged exactly as before.
