### Fixed

- A closure/anonymous-tag marker that changes *kind* while naming the same
  declaring file is reported as `declaration_renamed` again, rather than
  additionally asserting a move the identity shows no evidence for.
