### Fixed

- **`abicheck.product_baseline.pack_product_baseline`**: the self-referential
  symlink-loop rejection added in an earlier fragment relied on
  `Path.resolve()` raising `RuntimeError` for a loop -- true on Python
  <3.13, but Python 3.13 rewrote `Path.resolve()` to delegate to
  `os.path.realpath()`, which in its default non-strict mode silently
  returns the unresolved path for a symlink loop instead of raising.
  On 3.13+, `pack_product_baseline()` therefore packed a self-referential
  symlink cleanly with no error at all (caught by CI on the
  `ubuntu-latest, 3.14` and `macos-latest, 3.13` unit-test lanes).
  Symlink-loop detection is now done via a raw `stat()` call, whose
  `OSError(ELOOP)` reflects the kernel's own errno rather than pathlib's
  resolution behavior, and is consistent across every Python version --
  verified directly against both 3.11 and 3.13.
