### Fixed

- **A hidden friend befriended by more than one class no longer loses a
  public owner to a later-visited private one.** `Function.hidden_friend_owner`
  holds only a single owner; when the same free function id was befriended
  by both a public and a private/system class, whichever was visited last
  in the castxml XML silently won. A public owner now always wins and,
  once recorded, is never displaced by a later private one.
- **The C++20 dialect detector no longer misdetects a member, pointer-member,
  or qualified call to a pre-C++20 function named "requires"**
  (`x.requires(1);`, `x->requires(1);`, `ns::requires(1);`). "requires" the
  C++20 keyword is never looked up via `.`/`->`/`::`, so these forms can
  only be the pre-C++20 identifier usage.
