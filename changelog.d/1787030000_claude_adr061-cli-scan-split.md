### Changed

- **Internal (ADR-061 D5): `cli_scan.py` is off the 2000-line hard cap.** The
  repository's most-edited module sat exactly at the limit, so no change to it
  was possible without first moving responsibility out. `scan`'s CLI input
  parsing (`frontends/cli/scan_inputs.py`) and its `--artifact-set` member
  resolution and rendering (`frontends/cli/scan_artifact_set.py`) now have
  owners inside the `frontends` responsibility package, taking `cli_scan.py`
  from 2000 to 1720 lines. Every moved name is re-exported under its original
  spelling, so existing call sites and `monkeypatch` targets resolve
  unchanged. `stable_abi.py` is classified `model` (a 209-line leaf importing
  only the already-`model` `stable_abi_data.py`), which is what let the ABI3
  floor parser move with the rest.
- **Internal (ADR-061 D5): `reporter.py` down from 1998 to 1747 lines.** The
  application-compatibility projection (`appcompat_to_json`/
  `appcompat_to_markdown` plus the five section builders only they use) moved
  to `report/appcompat_report.py` — one whole concern with no other caller,
  answering a different question (one application against one library pair)
  from the library-vs-library report the rest of the module builds.
  `reporter.appcompat_to_json`/`appcompat_to_markdown` still resolve, through
  a lazy module-level `__getattr__` rather than a static re-export, which is
  what keeps the module-level import edge one-way.
