### Fixed

- **`tests/test_learning_ladder.py` failed every Windows CI lane** with
  `UnicodeEncodeError: 'charmap' codec can't encode character '←'`
  (19 tests). Its footer helper wrote a `←`/`→`/`·`-bearing string with
  `Path.write_text()` and no `encoding=`, so the write used the platform's
  locale encoding — cp1252 on Windows — while the same file already passed
  `encoding="utf-8"` on its other writes. The three affected read/write
  sites now do too. Reproducible off Windows by running that module under
  an ASCII locale (`LC_ALL=C PYTHONUTF8=0`), which fails identically
  before the fix and passes after.
