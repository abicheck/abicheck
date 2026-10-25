### Fixed

- Retired `cli_compare_release_pairwise` is now guarded alongside `cli_compare_release_helpers` and `cli_compare_release_matrix`: any live reference that does not label the module `retired` fails `tests/test_release_retired_module_names.py`. The release fan-out's history comments in `workflows/release_members.py` and six test modules now say so. No runtime behavior changes.
