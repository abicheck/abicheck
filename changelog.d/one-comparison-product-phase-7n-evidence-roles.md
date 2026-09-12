### Changed

- **One CLI input per evidence *role*, so a transport difference is no longer
  a flag difference** (one-comparison-product.md Phase 7n). `--debug-root`
  merged into `--debug-info` — a detached debug file, a directory of them,
  and a debug package are three transports of one role; `--devel-pkg` merged
  into `-H/--header` — a development package is a carrier of header evidence;
  and `--probe-matrix` merged into `--build-info` — probe observations and
  compile context stay distinct internally, may be supplied together for one
  side, and are still told apart in the report. `compare`'s visible option
  count drops from 47 to 44. Every transport keeps the destination, side
  scoping (`old=`/`new=`), schema and identity validation it had as a flag of
  its own. `dump --debug-root` is the same rename to `--debug-info`, minus
  the package transport: `dump`'s operand is one binary with no
  package-extraction stage, so a debug package there is a usage error naming
  `compare --debug-info` rather than a silently ignored value.
- **`--debug-info` resolves a debug artifact named directly.** A detached
  `.debug` sidecar, a `.dwp`, or a `.pdb` given as a value is used as the
  artifact itself rather than searched inside as a directory, and outranks
  the resolver chain's other strategies — that is what naming it means. A
  sidecar whose GNU build-id contradicts the binary's is refused rather than
  used to describe a different build; a build-id missing on either side
  proves nothing and is accepted, so absent evidence never manufactures a
  mismatch.
- **Package formats are detected from content, not filenames.** Every
  extractor in `abicheck.package` (RPM, Deb, tar and its compressed forms,
  wheel, conda) now recognises its own format from magic bytes and container
  members, so a package staged under a name with no conventional suffix is
  handled as one — by `is_package`, by `detect_extractor`, and therefore by
  the whole extraction pipeline behind the merged inputs above.
- Probe-matrix snapshots written by `abicheck.workflows.findings` now carry a
  `schema: abicheck.probe-matrix/v1` discriminator. Snapshots captured before
  it existed stay classifiable through their long-standing required-key
  contract, and the key is ignored on load, so both directions of the round
  trip are unaffected.

### Removed

- `compare --debug-root`, `compare --devel-pkg`, `compare --probe-matrix` and
  `dump --debug-root` are gone, with no hidden alias: each exits `64`. See the
  Changed entries above for the input that carries each one's capability.
