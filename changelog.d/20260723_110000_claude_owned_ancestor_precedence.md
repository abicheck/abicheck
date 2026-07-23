<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **A project-owned ancestor `-I` directory could be overridden by a
  deeper, non-owned nested `-I` directory** (Codex review, PR #624):
  `_attribute_file`'s longest-prefix match picked whichever declared `-I`
  directory was deepest, regardless of ownership. With `--header
  old/include/foo.h --include old --include old/generated`, `old` is
  project-owned (an ancestor of the declared header) but `old/generated`
  is not (it isn't itself an ancestor of any declared header); a file under
  `old/generated` was attributed to that deeper, non-owned slot and
  content-hashed, even though the broader owned `old` root already claims
  everything beneath it per this algorithm's own "every file under it,
  named or not, is excluded" rule. An ordinary internal support-header edit
  under such a nested directory could therefore spuriously raise
  `ProfileMismatchError`. Owned matches now win outright over non-owned
  ones; longest-prefix only breaks ties within the same ownership class
  (immaterial for owned matches, since an owned slot's token never depends
  on which specific owned directory attributed a file to it). This is the
  same class of fix as the prior "implicit header-parent ownership"
  precedence fix, extended to explicit owned `-I` ancestors.
