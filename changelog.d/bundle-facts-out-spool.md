### Changed

- **`--bundle-facts-out` no longer holds every OLD member in memory** — a
  release `compare --bundle-facts-out` (and `dump RELEASE
  --bundle-facts-out`) now writes each member's OLD snapshot to a temporary
  on-disk store as soon as its comparison finishes, and writes the document
  from that store one member at a time. On a measured six-member C++ release
  the parent's peak RSS falls from 914 MiB to 370 MiB for the JSON
  document (the same as a run without `--bundle-facts-out`), and from
  2305 MiB to 517 MiB for the archive form. The bytes written are unchanged. The store needs temporary disk
  space about the size of the uncompressed JSON document, and is removed
  when the run ends.
- **`--bundle-facts-out FILE.zip` writes the zip archive form** — a `.zip`
  name now selects the content-addressed archive described by
  `bundle_facts_archive_manifest.schema.json`, as that schema already said.
  Before, it wrote the JSON document under a `.zip` name. Every other name
  writes the JSON document, as before.
