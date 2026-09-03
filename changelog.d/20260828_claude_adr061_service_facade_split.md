### Changed

- Split `abicheck/service.py`'s native-binary dump orchestration
  (`run_dump`/`_run_dump_uncached`/`_finish_native_snapshot`/
  `_apply_native_provenance`/`_emit`/`_dump_elf`/`_extract_pdb_debug`/
  `_dump_pe`/`_dump_macho`, plus the `_HEADER_GRAPH_ENABLED`/
  `_HEADER_GRAPH_INCLUDES_ENABLED` module constants they read) out into two
  new sibling leaf modules, `service_dump_native.py` (the shared orchestration
  and the ELF tail) and `service_dump_native_pe.py` (the PE/Mach-O tail plus
  `_extract_pdb_debug`, split into its own file purely because the first file
  alone was still over the AI-readiness 800-line production cap and a
  genuinely new file gets no `architecture/debt.yaml` baseline to grow into
  the way `service.py` itself does) — the same "extract to a `service_<name>`
  leaf, re-export verbatim" pattern `service_metadata_attach`/
  `service_header_graph_attach`/`service_header_scoped`/`service_render`/
  `service_scan`/`service_compare_pipeline`/`service_dump_pipeline` already
  established, applied to the one large block `service.py` had never gone
  through it for. `service.py` drops from 1763 to 872 lines (both new modules
  registered in `architecture/modules.yaml`'s `frozen_root_families.service_`
  and `legacy_root_modules` lists; `architecture/debt.yaml`'s `service.py`
  entry's `baseline_lines` lowered from 1763 to 873 to match); every public
  and private name involved is re-exported unchanged, so
  `from abicheck.service import run_dump` (and the many `_dump_elf`/
  `_dump_pe`/`_dump_macho`/`_run_dump_uncached`/`_attach_header_graph`
  test-patch targets) keep resolving — behavior is bit-for-bit unchanged.
  `service_dump_native` joins the pre-existing, already-baselined
  `IMPORT_CYCLE_ALLOWLIST` SCC in `scripts/check_ai_readiness.py` on exactly
  the terms `service_header_graph_attach`/`service_compare_pipeline`/
  `service_dump_pipeline` were each signed off under before it: a split of an
  already-member module (`service`), carrying edges (`service_scan`,
  `service_header_graph_attach`) `service.py` itself already had, not a new
  dependency direction — see that allowlist entry's own comment for the full
  reasoning, and the PR body for why this is a judgment call worth a second
  look rather than a routine allowlist edit.

  **Test-patch fix, not just a move**: ~65 existing test-patch call sites
  across `tests/test_service_unit.py`/
  `tests/test_service_clang_layout_renumbering.py`/
  `tests/test_lambda_identity_ordinal.py`/`tests/test_pdb_provenance.py`
  were patching `_dump_elf`/`_dump_pe`/`_dump_macho`/`_run_dump_uncached`/
  `_attach_header_graph`/`attach_clang_layout`/`expand_header_inputs`/
  `_extract_pdb_debug`/`_try_header_scoped_dump` to observe `run_dump()`'s
  (or, for the last two, `_dump_pe()`'s own) behavior — since the call these
  tests were actually influencing now resolves against
  `service_dump_native.py`'s or `service_dump_native_pe.py`'s own module
  globals (ordinary Python import-binding semantics, the same "test-patch
  gotcha" `workflows/extraction.py`'s own docstring already documents for
  the `workflows` facade), every one of those patch targets was rewritten to
  name the module the call actually resolves in. Two rounds of this: the
  first pass caught every single-line `patch("abicheck.service.<name>", ...)`
  call by mechanical search; a second pass, prompted by actually running the
  affected test files rather than trusting the mechanical search alone,
  caught four more shapes it missed — a multi-line `patch(\n    "abicheck.
  service.<name>", ...\n)` call whose string spans two lines (the naive
  single-line search silently skipped these), `_try_attach_numpy_capi_
  surface`/`_try_attach_python_api_surface`/`_try_attach_python_ext_
  metadata`/`_try_attach_sycl_metadata` (from `service_metadata_attach.py`)
  being dropped from `service.py`'s own re-exports entirely rather than
  merely needing a retargeted patch — several test files import these four
  directly from `abicheck.service` for standalone unit testing, unrelated to
  `run_dump`, and that import broke outright until they were re-added
  alongside (not instead of) `service_dump_native.py`'s own identical import
  from the same source — `attach_clang_layout` (patched via
  `abicheck.service.attach_clang_layout`, a name `service.py` no longer even
  defines post-split) needing the same retarget as the `_dump_*` family, and
  one test (`test_pdb_provenance.py`) patching `_extract_pdb_debug`/
  `_try_header_scoped_dump` via a live module-object reference
  (`monkeypatch.setattr(service, "_extract_pdb_debug", ...)`) rather than a
  string target, which needed retargeting at the object level
  (`service_dump_native_pe`) the same way. A bare `from abicheck.service
  import _dump_elf` (calling the function directly, not patching it) needed
  no change, since `service.py`'s re-export still makes that import resolve.
  This two-round history is itself the reason the verification section below
  runs every test file that references any of these names directly, not
  only the ones a first pass happened to catch.

  Only this one block moved in this pass — `resolve_input`/`sniff_text_format`/
  format-detection, `compare_snapshots`/policy-and-suppression loading, and
  the several existing tail-of-file re-export blocks stay in `service.py`
  for now, since none of them cleared the same "large, cleanly-delineated,
  independently-verifiable" bar without risking a half-migration; see the PR
  body for the fuller investigation and what was deliberately left as
  follow-up work.

  **Post-review fix: both new files gained the `workflows` layer
  classification their own siblings already carry.** A Codex review finding
  correctly pointed out that `run_dump`'s own dump-orchestration role
  matches `AGENTS.md`'s task-routing table entry for `workflows/`
  ("Coordinate dump, compare, scan, release, aggregate, project, or
  dependency behavior") — but its proposed remedy (implement it fresh under
  `abicheck/workflows/`) would have been inconsistent with how every other
  `service_*` sibling in this exact cluster was migrated: `service.py`,
  `service_compare_pipeline.py`, `service_dump_pipeline.py`,
  `service_input_resolution.py`, and `service_compare_evidence.py` are
  *already* classified `workflows` in `architecture/modules.yaml`'s
  `legacy_paths` — logically owned by that layer while still physically
  flat, exactly the virtual-classification-before-physical-move pattern
  this same ADR-061 pass already used for the `policy`/`extract` layers.
  The real gap the review surfaced: `service_dump_native.py`/
  `service_dump_native_pe.py`, split out of an already-`workflows`-
  classified file and doing the identical kind of dump-coordination work,
  were left with no `legacy_paths` entry in either layer at all — an
  inconsistency with their own immediate precedent, not a design
  disagreement. Fixed by adding both to `workflows`'s `legacy_paths`,
  matching their siblings exactly. `python scripts/check_architecture.py`
  stays 0 errors (workflows' `may_import` already covers everything
  these two files import, since they inherited `service.py`'s own import
  set unchanged). Physically moving this whole `service_*` dump-
  orchestration cluster into `abicheck/workflows/` remains a real,
  separate follow-up — the same one the module map's `service_dump_
  pipeline.py`/`service_compare_pipeline.py` entries already imply — not
  something this PR's own scope extends to.
