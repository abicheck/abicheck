### Changed

- **Internal (ADR-061 D5): `cli_scan.py` is off the 2000-line hard cap.** The
  repository's most-edited module sat exactly at the limit, so no change to it
  was possible without first moving responsibility out. `scan`'s CLI input
  parsing (`frontends/cli/scan_inputs.py`) and its `--artifact-set` member
  resolution and rendering (`frontends/cli/scan_artifact_set.py`) now have
  owners inside the `frontends` responsibility package, taking `cli_scan.py`
  from 2000 to 1772 lines. Every moved name is re-exported under its original
  spelling, so existing call sites and `monkeypatch` targets resolve
  unchanged. ABI3 version parsing (`parse_abi3_version`/`format_version`)
  moved out of `stable_abi.py` into a new `model/abi3_version.py`, so the
  frontend can reach the parser without the module's compatibility-policy
  classifiers (`classify`, `is_private_symbol`) becoming `model` alongside
  it; `stable_abi.py` re-exports both names unchanged.
- **Internal (ADR-061 D5): `reporter.py` down from 1998 to 1747 lines.** The
  application-compatibility projection (`appcompat_to_json`/
  `appcompat_to_markdown` plus the five section builders only they use) moved
  to `report/appcompat_report.py` — one whole concern with no other caller,
  answering a different question (one application against one library pair)
  from the library-vs-library report the rest of the module builds.
  `reporter.appcompat_to_json`/`appcompat_to_markdown` still resolve, through
  a lazy module-level `__getattr__` rather than a static re-export, which is
  what keeps the module-level import edge one-way.
- **Internal (ADR-061 D5): `suppression.py` down from 1993 to 1355 lines.** The
  namespace-glob matcher — pattern compilation, the non-backtracking segment
  walk, and the character-level helpers under both — moved to
  `policy/namespace_glob.py`. It is a general string-matching primitive that
  knows nothing about suppressions or findings, and `suppression.py` was its
  only caller. Every name is re-exported unchanged.
