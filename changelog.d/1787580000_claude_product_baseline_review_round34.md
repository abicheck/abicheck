### Fixed

- **`abicheck.product_baseline.compare_product_directories`**: the
  canonical (SONAME-major-stripped) fallback pairing grouped candidates
  by a bare, global canonical name -- two independent libraries sharing
  a basename in different directories (an ordinary plugin-host layout,
  e.g. `plugins/a/libfoo.so.1` and `plugins/b/libfoo.so.1`) that each
  independently bumped their own SONAME major landed in one shared,
  cross-directory bucket. That made the bucket ambiguous (more than one
  candidate) on both sides, so *neither* pair was ever compared, even
  though each pairing is individually unambiguous once directory is
  taken into account -- silently losing every symbol/type change for
  both libraries, and risking a false-green product verdict. The
  canonical fallback is now scoped per (relative directory, canonical
  name) rather than canonical name alone.
- **`abicheck.product_baseline._resolve_under_root`**: an untrusted
  archive containing a self-referential symlink and declaring a library
  or header root beneath it made `Path.resolve()` raise `RuntimeError`
  instead of returning a value -- `unpack_product_baseline()` cleaned up
  its staging directory but re-raised that raw exception, bypassing its
  own documented `SnapshotError`-only contract and surfacing an
  unhandled traceback to any caller that only handles corrupt archives
  through `SnapshotError`. A symlink loop is now caught in this shared
  containment primitive and treated the same as any other unresolvable
  or escaping path.
