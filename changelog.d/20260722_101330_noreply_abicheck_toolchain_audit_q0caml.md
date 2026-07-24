### Fixed

- **The CastXML version gate no longer accepts a release-candidate build as
  equivalent to the final release it precedes.** The documented CastXML
  version format allows an optional `-rc<n>` pre-release id before the git
  suffix (e.g. `0.7.0-rc1-gabc`). The git-suffix-tolerant parser converted
  the *first* hyphen to a PEP 440 local-version separator, which folded
  `-rc1` into the opaque local-version string and erased its pre-release
  meaning — making the build compare as `>=` final `0.7.0` instead of below
  it. Now only the *last* hyphen is converted, leaving an earlier
  hyphen-separated pre-release segment intact for PEP 440 to parse
  natively.
