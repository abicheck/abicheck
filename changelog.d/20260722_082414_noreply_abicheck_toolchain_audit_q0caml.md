### Fixed

- **A hidden friend added or removed together with its private/system owner
  class is now correctly demoted out of the public surface.** A hidden
  friend's owner class is most often introduced or deleted *alongside* the
  friend itself, so the owner legitimately exists in only one of the two
  snapshots' `all_types`. `surface.py`'s owner-provenance check previously
  required *both* snapshots to independently agree the owner was a
  system/private header — the side missing the owner defaulted to
  `ScopeOrigin.UNKNOWN`, which silently starved that agreement check, keeping
  the finding in-surface (a false API/BREAKING result) even when the one side
  that has the owner is confidently non-public. New
  `_hidden_friend_owner_reason()` classifies from whichever side(s) actually
  contain the owner, still requiring agreement only when the type persists on
  both sides.
