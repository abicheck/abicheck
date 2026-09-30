### Fixed

- **An anonymous-type/lambda location under a checkout path containing a
  space is stripped again.** `canonicalize_type_name`'s location strip matched
  the path with `\S+`, so `(lambda at /home/dev/my projects/x/api.h:4:37)` kept
  the absolute path and leaked the checkout root into canonical type
  spellings and function identity keys. It now matches the path non-greedily
  and is anchored on the `(lambda` / `(unnamed <kind>` / `(anonymous <kind>`
  marker, like its sibling discriminator regex. The clang initializer
  fingerprint's location strip (`dumper_clang_expr`) likewise no longer stops
  at a `)` inside the path.
