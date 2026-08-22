### Fixed

- **`_umask_derived_mode()` (formerly `_process_umask()`) no longer calls
  `os.umask()` at all, and no longer caches its result.** The previous
  round's fix locked calls to this module's own umask query to avoid
  double-mutating the process-wide umask, but the lock only serialized
  this module's own callers -- it never closed the underlying race with
  *any* concurrent file creation in an unrelated thread of the same
  process, since `os.umask(new)` is POSIX's only getter and briefly
  zeroes the process-wide umask to read it. Separately, caching the result
  for the rest of the process's lifetime meant a later, unrelated
  `os.umask()` call elsewhere in a long-lived embedding was silently
  ignored. Both are now closed the same way: `_umask_derived_mode()`
  learns `base_mode & ~umask` from a real, immediately removed probe file
  or directory created under the caller's own writable directory, which
  picks up the umask exactly the way any ordinary file/directory creation
  does, without ever mutating shared process state -- eliminating the race
  entirely rather than narrowing its window, and staying live rather than
  stale.
- **`pack_product_baseline()`'s dangling-symlink case/Unicode-fold checks
  and the case/Unicode-collision detection tests now correctly skip on a
  case-insensitive or Unicode-normalizing host filesystem** (macOS's
  default APFS, Windows' default NTFS) instead of failing outright, since
  several of these tests' own fixture setup requires creating two
  genuinely distinct files that collide only under the *code's* case/
  Unicode folding -- impossible to set up at all when the *host*
  filesystem already unifies them at the OS level.
- **`test_gnu_sparse_member_declaring_huge_size_is_rejected`** now skips
  cleanly, with the underlying `tar` diagnostic included, when the local
  `tar` binary doesn't accept GNU tar's `--sparse` flag the way the test
  needs (e.g. macOS's default BSD `tar`), instead of failing with an
  opaque `CalledProcessError`.
