<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Removed

- **`abicheck/cli_compare_release_matrix.py` is retired** — its release output finalization and early suppression validation moved into their frontend owners (`frontends/cli/release_summary.py`, `frontends/cli/release_compare_request.py`), and its re-exports of input-resolution and policy names were dropped. Import those names from their owning modules; the observable CLI behavior, exit codes and report output are unchanged.

### Documentation

- **Stale references to the deleted `cli_compare_release_helpers` module now name the real owners** of each helper (`frontends/cli/release_exit.py`, `workflows/release_bundle.py`, `workflows/release_inputs.py`, `policy/release_outcome.py`, `report/render_release_report.py`, and others), so code comments and docstrings point at code that exists.
