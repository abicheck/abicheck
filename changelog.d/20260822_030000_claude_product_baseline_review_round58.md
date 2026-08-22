### Fixed

- **`unpack_product_baseline()` no longer re-hashes the same physical content
  once per declared library alias.** A hardlink alias gets its own
  `LibraryEntry` (unlike a symlink alias), so an untrusted archive declaring
  many entries hardlinked/aliased to one physical payload previously had
  its verification loop hash that payload's full content independently for
  every declared entry -- turning N declared aliases into N full reads of
  the same bytes, a cumulative-hashing CPU cost an attacker controls
  directly via manifest size (10,000 aliases to a 100 MiB payload could
  force roughly 1 TiB of hashing from about 100 MiB of extracted data).
  The verification loop now caches each computed SHA-256 digest by the
  resolved `(dev, ino)` identity, so a shared payload is hashed exactly
  once regardless of how many declared entries alias it.
