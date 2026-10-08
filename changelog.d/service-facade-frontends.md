### Removed

- **`abicheck.service` no longer re-exports the private `_try_attach_*` metadata helpers** — `_try_attach_sycl_metadata`, `_try_attach_python_ext_metadata`, `_try_attach_numpy_capi_surface` and `_try_attach_python_api_surface` were undocumented, test-only aliases; import them from their owner `abicheck.service_metadata_attach`. `abicheck/service.py` is now classified `frontends` (ADR-061), which deletes the last `workflows -> frontends` dependency exception (`service.py -> service_render`). Every name documented in `docs/use/python-api.md` is unchanged.
