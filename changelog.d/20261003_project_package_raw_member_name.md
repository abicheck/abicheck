### Fixed

- `unpack_project_package` now validates each member's name as stored in the
  archive (`ZipInfo.orig_filename`). On Windows `zipfile` rewrites `\` to `/`
  in `filename`, so a member like `refs\artifacts\x.json` was accepted there
  while every other platform refused it; any rewritten name is now refused.
