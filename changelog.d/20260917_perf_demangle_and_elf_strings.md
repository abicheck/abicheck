### Performance

- **`demangle_batch` no longer does the same native work twice.** Two
  independent redundancies, both measured on a real oneDAL build: (1) the
  batch was filtered against the process cache but never deduplicated, and
  neither resolution phase re-consults the cache it is populating, so one
  symbol passed N times in a call reached `cxxfilt`/`c++filt` N times —
  4,000 real oneDAL names submitted 24,000 times took 0.184s and now take
  0.034s (**5.4x**) for an identical mapping; (2) both caches cleared
  themselves *wholesale* at their 65,536-entry bound, so recording one
  symbol at the limit discarded every resolved name. `libonedal_core.so.1`
  carries 91,510 unique mangled exports — genuinely past that bound — where
  the old policy retained 25,974 entries and had to re-resolve 65,536 names
  on a second pass; oldest-entry eviction retains 65,536 and re-resolves
  25,974.
- **ELF symbol names are resolved from one buffered read of the string
  table** (`extract/elf_string_table.py`) instead of a stream seek plus a
  chunked read per name. On `libonedal_core.so.1` (14.5 MB `.dynstr`,
  92,995 symbols) name resolution drops from 190ms to 56ms (**3.4x**) with
  byte-identical strings; end-to-end `parse_elf_metadata` over three oneDAL
  libraries improves 8.21s → 7.65s (**1.07x**), since construct-based
  parsing of the symbol *entries* dominates and is untouched. The buffer is
  scoped to one walk of one section and dropped at exit — deliberately not
  a process-lifetime cache keyed by path — and a table above a size cap
  stays on the unbuffered path.
