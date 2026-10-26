### Fixed

- **A corrupt or truncated package archive is reported as corrupt, not as an unrecognized format** --
  a `.whl`/`.zip`/`.jar`-family file (or a zip-magic file) whose central directory cannot be read,
  and a tar-family file no tar reader accepts, used to be treated as "not mine" by every extractor.
  Release input resolution then said `Unrecognized package format`, which misdiagnosed the cause.
  It now fails with `... is a corrupt or truncated archive: ...` naming the file, under the same
  exit code as before (`1`, `ReleaseOperandContentError`). Non-archive files keep the unrecognized-format
  message, and extractor `detect()` keeps its boolean contract.
