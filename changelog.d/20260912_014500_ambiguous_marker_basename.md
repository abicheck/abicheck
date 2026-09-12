### Fixed

- A declaration whose recorded declaring file is named by more than one of
  its closure markers no longer reports `declaration_moved` when one of them
  changes: with several candidates there is no way to tell which marker is
  the declaration's, so the move claim is withheld.
