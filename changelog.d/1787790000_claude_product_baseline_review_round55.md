### Fixed

- **`abicheck.product_baseline`**: `os.umask()` is the only portable way
  to query the process umask, but it only works by briefly mutating it
  (zeroing it, then restoring the old value) -- a file another thread
  creates in that window could come out more permissive than intended.
  Both call sites (packing an output archive, publishing an unpack
  destination) now share one cached `_process_umask()` helper, so the
  race window opens at most once per process instead of once per call.
- **`abicheck.product_baseline.pack_product_baseline`**: a relative
  symlink target dangling on the (case-sensitive) packing host but
  matching a sibling file once case/Unicode-folded (e.g. `libfoo.so ->
  payload` alongside a real `Payload`) packed with no `LibraryEntry`
  recorded, since it doesn't resolve here -- but on a case-insensitive
  unpacking host (Windows/macOS) it resolves live, and `unpack_product_
  baseline`'s own discovery walk then finds it as an undeclared library
  and rejects an honestly-packed archive. Such a symlink is now rejected
  at pack time, applying the same case/Unicode-fold reasoning the
  existing member-collision check already uses.
