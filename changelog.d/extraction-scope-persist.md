### Added

- **Snapshots record who owns each declaration** (ADR-075, snapshot schema
  v52). Every header dump now stores `extraction_scope`: the ownership rules
  its declarations were classified under (`scope.public_header_dirs`, `-H`
  directories, `scope.dependencies`, `private_headers`, `private_namespaces`,
  roots relative to the project root), a fingerprint, and each declaration's
  owner (`target`/`dependency:<name>`/`toolchain`/`unresolved`), contract
  (`public`/`private`/`external`/`unresolved`) and deciding rule, as an
  interned table. A pre-v52 snapshot loads with its ownership *unknown*, never
  guessed. Classification only: no declaration is kept or dropped because of
  these keys.
- **`scope.dependency_evidence`** is a recognised `.abicheck.yml` key. Only
  `full` (today's behaviour) is accepted; `referenced` is rejected until
  retention by reference lands.
- **Comparability on the extraction scope**: two snapshots that kept
  dependency declarations differently (or were narrowed by different
  prefilters) are refused as a scope mismatch. Differing ownership rules alone
  are compared, with a coverage warning naming the declarations that moved
  owner or contract, and a pre-v52 baseline is compared with a note. The rules'
  fingerprint joins the configuration digest as `surface.ownership` (report
  schema 5.4).
