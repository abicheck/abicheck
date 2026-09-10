### Fixed

- **`compare --dry-run` accepted a malformed `.abicheck.yml` `policy.overrides`
  block that the real comparison rejects.** A discovered project config's
  `policy.overrides` (e.g. an unknown `ChangeKind` slug) was only
  semantically validated once folded into a `PolicyFile` -- for both the
  scalar path and the directory/package fan-out, that fold happens after
  the `--dry-run` emit (which exits first), so an invalid invocation
  reported a clean exit-0 dry run instead of the exit-64 usage error the
  identical non-dry-run invocation produces. Fixed by validating the
  project config's overrides ahead of every `--dry-run` exit, reusing the
  same canonical validator (and its `click.BadParameter` translation) the
  real run already trusts, for both operand shapes.
- **A GNU `__attribute__((abi_tag(...)))`-tagged internal class's
  vtable/RTTI churn was never demoted during public-surface classification.**
  An ABI-tagged class's mangled special name (e.g. `_ZTV1CB3tag`) carries
  its tag directly on the owner's bare name, but CastXML/clang model the
  record itself under its plain, untagged name -- so the surface-candidate
  set built from the mangled name could never match the model's own record,
  and the finding was conservatively kept in-surface. Fixed by stripping
  `[abi:tag]` markers from every candidate `itanium_special_name_owner_
  identifiers` produces (including one embedded in a template argument),
  while the identity-oriented `itanium_special_name_owner_scope_components`
  parser keeps the tag intact, as identity resolution requires.
- **A typed `run_compare_request()` call using `CompareRequest.project_
  policy_overrides` recorded that override's provenance as `API_REQUEST`
  instead of the `PROJECT_CONFIG` tier ADR-049 D7 actually places it at.**
  The value correctly scored the comparison (folded into the `PolicyFile`
  before classification), but the separately-built gate receipt reused
  that already-folded file, which made the resolver read the fold's own
  output as the file's *explicit* content. Fixed by threading the request's
  project overrides through the canonical resolver's existing `project=`
  input (the same channel the native `compare` CLI and the directory/
  package release fan-out already use for their own `.abicheck.yml`
  provenance), keeping the pre-fold `PolicyFile` out of the receipt call
  so the two no longer disagree.
