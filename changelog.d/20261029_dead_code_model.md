### Removed

- Removed three unreferenced helpers (`_param_from_cli`, `manifest_forced_includes`, `calling_convention_text`) and unread or never-passed parameters across `model/`, `workflows/`, `extract/`, `compare/`, `policy/` and `report/` internals; protocol-fixed parameters are now marked with a reasoned `noqa`. No behaviour change.
