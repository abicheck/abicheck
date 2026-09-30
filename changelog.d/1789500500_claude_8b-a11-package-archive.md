### Added

- A `ProjectSnapshot` package directory can now travel as one file: `workflows.storage.pack_project_package`/`unpack_project_package` write and restore a deterministic zip archive (storage-format-v2 A1.1). `compare` accepts such an archive anywhere it accepts a package directory. Unpacking treats the archive as untrusted and refuses path traversal, symlinks, compressed or duplicate members, stray files and oversized archives before writing anything.
