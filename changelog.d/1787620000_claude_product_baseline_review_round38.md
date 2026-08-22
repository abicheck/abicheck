### Fixed

- **`abicheck.product_baseline.pack_product_baseline`**: the Windows
  drive-absolute/UNC symlink-target check added in an earlier fragment
  only used `PureWindowsPath.is_absolute()`, which requires both a
  drive *and* a root to be true -- so a current-drive-rooted target
  (`\outside\foo.dll`, no drive letter) or a drive-relative target
  (`C:outside\foo.dll`, no root) both reported `is_absolute() == False`
  and packed cleanly, even though both are anchored to Windows-specific
  per-drive state rather than a portable in-tree path. Any target with
  a nonempty Windows drive or root is now rejected, not just one where
  both are present.
