### Fixed

- **The time64 detector follows `time_t`'s typedef chain.** Clang's header
  backend records each alias link (`time_t` → `__time_t` → `long` on 32-bit
  without `_TIME_BITS`), so the width of the underlying type was never read
  and a `_TIME_BITS=64` flip reported only `TYPEDEF_BASE_CHANGED`. The
  detector now resolves the chain on each side and buckets the fixed-width
  POSIX names (`__int64_t`, `int64_t`, …) by their own width. GCC's flattened
  spelling already produced `TIME64_ABI_CHANGED` and is unchanged.
