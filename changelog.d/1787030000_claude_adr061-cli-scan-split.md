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
