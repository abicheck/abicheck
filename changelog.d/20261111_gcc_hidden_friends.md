### Fixed

- **native GCC header backend: hidden friends** — the GCC plugin now emits the
  functions each class befriends, and the projection marks them as hidden
  friends with their owning class, as castxml does. Adding or removing an
  in-class `friend` function (found only by ADL) is now reported as
  `hidden_friend_added`/`hidden_friend_removed` with the gcc header backend
  instead of NO_CHANGE. Plugin protocol schema 4 (rebuild the plugin).
  `EXTRACTOR_GENERATION` 57, snapshot cache salt v51.
