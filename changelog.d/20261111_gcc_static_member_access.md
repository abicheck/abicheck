### Fixed

- **native GCC header backend: static data member access** — a static data
  member moved under `private:`/`protected:` (or back) is now reported as
  `var_access_changed`/`var_access_widened` on snapshots from the native GCC
  backend. The backend always recorded the access, but the detector only read
  castxml and clang snapshots.
