### Changed

- **The service-layer unit tests live with the modules that own them** — the 5,600-line `tests/test_service_unit.py` is split into owner-named test modules (`test_input_resolution_unit.py`, `test_dump_native_run_dump.py`, `test_service_compare_pipeline_unit.py`, and so on), and `plan.py`'s regression-test pointer names the new home. No test was added, removed or skipped, and the product behaviour is unchanged.
