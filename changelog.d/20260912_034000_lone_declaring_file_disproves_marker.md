### Fixed

- A declaration whose one recorded declaring file names none of its closure
  markers no longer reports `declaration_moved` from a differing marker on
  the other side: that marker describes a nested template argument, not the
  declaration, so the pair keeps the evidence-conflict outcome.
