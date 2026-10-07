# Agent guide: CLI commands, large files, exit codes

> Moved verbatim out of the root `AGENTS.md` (progressive disclosure: the root file is loaded into every agent session, this one only when its pointer fires). The root `AGENTS.md` remains the primary contract.

## Files that are large — edit carefully

**Don't trust hard-coded line counts — they drift.** The AI-readiness gate is the
source of truth: it WARNs on any file >1500 lines and ERRORs >2000 (hard cap, no
allowlist). To see today's large files, run:

```bash
python scripts/check_ai_readiness.py 2>&1 | grep "exceeds soft limit"
```

That sentence is load-bearing: this paragraph previously named the WARN set as
"`cli.py`, `dumper.py`, and `buildsource/cross_source_checks.py`" long after it had
stopped being true (`cli.py` is now a small registration facade), which is
exactly the drift the "don't trust hard-coded line counts" warning above is
about. As a shape rather than a list: the WARN set is **large — roughly 100
files, about a third of them under `abicheck/`** — and a meaningful number sit
within a few lines of the 2000-line hard cap, so a routine addition to one can
turn an ERROR on. Run the command; don't reason from any count written here.

`architecture/debt.yaml` is the sharper gate for these files and the one you
will actually trip: every one of them carries a `no_growth` baseline
(`python scripts/check_architecture.py`), so growing one is a reviewed
debt-baseline change, not an ordinary edit. ADR-061's definition of done wants
that file empty or holding only accepted exceptions — **the way to shrink an
entry is to move responsibility out to a properly-owned module, never to
trim the file to fit** (`report/render_html.py` is a worked example: it took
~200 lines of formatting out of `html_report.py` by giving them an owner).

When editing any large file, read the specific section you need rather than the
whole file. Several big commands have already been split into sibling
`cli_<name>.py` / `diff_*` modules (see [module-map.md](module-map.md)); prefer extending a
split-out module over growing the parent toward the cap.

### Retiring a command or flag

Ask the maintainer how it leaves, before deleting it. The answer depends on
who still calls it:

- **Delete outright**: the command, its tests, docs and hints all go, and
  the CLI answers with its ordinary "no such command". A changelog fragment
  names the replacement. The `compat` removal took this path.
- **Tombstone for one release**: a stub that exits 64 with a pointer to the
  replacement, removed in the next release. `scan` took this path.

Pre-1.0 either is acceptable and the maintainer picks per case. Once
abicheck promises backward compatibility, a retirement follows its ADR's
deprecation window instead.

### Adding a new top-level command

**First, ask whether it should be a *root* command at all (ADR-043/ADR-054).**
The public root surface is exactly `dump`, `compare`, `deps`,
`aggregate`, `project` — `scan` was retired by ADR-068 Phase 6, which deleted
the command and its scan-only modules, and the ABICC `compat` drop-in was
removed too — and `tests/test_cli_root_surface.py`
pins that set as
an executable contract, so a new root registration fails CI until the test is
updated too. Before adding one, a new root command must clear **every** one of
these (ADR-054's admission bar — the same review that consolidated four
G30/ADR-047 root groups, one added per artifact, back into the single
`project` group below):

1. It answers a stable, user-facing question — not "here is JSON artifact X,
   let me expose the function that reads/writes it."
2. Its operand is a domain object a user already thinks in terms of (a
   binary, a set of reports, a project config) — not an internal pipeline
   transport format (a manifest, a projection of another artifact).
3. It is useful outside one specific CI Action's wire format. A command whose
   whole job is "shape this JSON exactly how `actions/foo`'s one input
   expects it" is a library function that Action/workflow calls directly
   (`python3 -c "from abicheck.x import y; ..."`), not CLI surface — see
   `abicheck/buildsource/baseline_publish.py`'s `derive_baseline_libraries()`
   for the pattern (used by `publish-baseline.yml`/`update-main-baseline.yml`,
   never a CLI command).
4. It doesn't already fit naturally as an option or subcommand of an existing
   durable operation (`--dry-run`, a new `<verb> <noun>` subcommand under an
   existing group) — a second, parallel "preflight" vocabulary next to an
   already-established one (`dump --dry-run`) is exactly the drift ADR-050's
   `plan --dump-manifest` command caused before ADR-054 folded it back into
   `dump --dump-manifest --dry-run`.
5. It has a real, validated usage scenario beyond the PR that introduced it —
   not just "this pipeline stage produces an artifact, so it should be
   inspectable."
6. Landing it means updating `tests/test_cli_root_surface.py`, this file,
   `README.md`, and `docs/reference/cli-reference.md`
   (`python scripts/gen_cli_reference.py`) in the *same* PR — a root surface
   change with only the test updated (or only the code) is how ADR-043's
   "nothing else is registered" invariant drifted from the actual command set
   before (the CLI carried ten root commands while `README.md` still said
   six).

**If it's advanced multi-target/CI-integration surface that fails the "user
already thinks in terms of this operand" bar (#2) on its own** — validating a
project's `.abicheck.yml`, a `build-output.json`, or generating a run-plan —
it almost certainly belongs as a new subcommand of the existing `project`
group (`abicheck/cli_project.py`), not a new root command. `project` exists
precisely to hold this class of operation: `project validate` (one command
over a project config, a build-output directory, or a use-case manifest,
dispatched on the document's own schema — never its filename),
`project plan`, `project history` are all "read one project-integration
artifact, report on it" operations that share one advanced/opt-in namespace
instead of each claiming root. Add `@project_group.command("your-verb")`
there and extend `tests/test_cli_root_surface.py`'s existing assertions
(which don't need to change, since `project`'s subcommand set isn't pinned
the way the root set is) plus a `TestProjectYourVerbCli`-shaped test class.

Once a root command genuinely clears the bar above, pick the right home:

- **Small command (one function, no significant helpers)** — add to `cli.py` directly with `@main.command(...)`.
- **Larger command or command group** — add as a sibling `abicheck/cli_<name>.py` module:
  1. Top of module: `from .cli import main` (and any shared `_helpers`).
  2. Decorate with `@main.command("foo")` or `@main.group("foo")` as usual.
  3. At the bottom of `cli.py`, add `cli_<name>` to the side-effect `from . import (...)` block — that runs after `main` and helpers are defined, registering the new command.
  4. If the new module uses `@click` decorators, add `abicheck.cli_<name>` to the `disallow_untyped_decorators = false` override in `pyproject.toml` (alongside the existing entries).
  5. If `scripts/check_ai_readiness.py` flags a cycle, this is `IMPORT_CYCLE_ALLOWLIST`'s known CLI-registration cluster — see the import-cycle rule in the root `AGENTS.md` "Rules" section before extending it.
  6. **Shared utility flags go through a decorator, not an inline copy.** `-v/--verbose` is `@verbose_option`, the one `-o FORMAT=DESTINATION` export request is `export_options(...)`, language is `lang_option(...)` (all in `cli_options.py`). Every visible option must carry `help=` text and a shared concept must use one canonical primary spelling — both are enforced by `tests/test_cli_contract.py` (`test_no_option_has_empty_help`, `test_shared_concept_canonical_spelling`).
  7. **Moving helpers out of a module that re-exports them?** If you relocate a helper that an existing module re-exports "for API stability / tests" (e.g. the `cli_buildsource` block), preserve the old import path with a lazy module-level `__getattr__` shim that resolves via `importlib.import_module` — a static `from .new_module import …` re-export would re-introduce the import cycle the split was meant to avoid (see the shim at the tail of `cli_buildsource.py`).

## Exit codes

- `compare` command (legacy, with no severity setting in effect): 0 = compatible, 2 = source break, 4 = ABI break
- `compare` command (severity-aware, with `--severity-preset` or a config `severity:` block): 0 = no error-level findings, 1 = error in addition/quality only, 2 = error in potential_breaking, 4 = error in abi_breaking
- `scan` (including `scan --against`) was deleted by ADR-068 Phase 6; its baseline mode is `compare OLD NEW` and its audit mode is `compare --no-baseline NEW`, both covered by the `compare` rows above.
- **Orthogonal contract-coverage axis (ADR-049 Phase 7), on `compare`
  (single-pair and directory/package alike):** under `--contract`, the selected
  domain whose required evidence is incomplete contributes
  **1**, folded with `max` (`contract_coverage_exit.py`). It raises a clean
  `0` to `1` and never lowers a `2`/`4`, and it never rewrites a finding's
  compatibility decision or gate contribution. Without
  `--contract` the evidence-adaptive default domain is one the run's own
  evidence closes, so the contribution is normally `0`. Every consumer that publishes an
  exit status folds it and explains it — the two CLIs and the composite
  Action (`verdict: COVERAGE_INCOMPLETE`). A directory/package
  `compare` (the per-library release fan-out) applies the same flag to
  each library and `max`s every library's own contribution into the
  release's exit code, stated in the release JSON summary under the same
  `contract_coverage_exit_contribution` field. `gate.fail_on_removed_library`'s
  exit `8` is checked ahead of this coverage-only fallback when both could
  apply, so a removed library's own signal is never masked by an unrelated
  coverage gap
- **Orthogonal completeness axis (ADR-065 D6/D7, S2), directory/package
  `compare` only:** a selected, expected member that never reached a
  completed comparison (`not_supplied` with no completeness proof on the
  lacking side, `unsupported`, `failed`) makes `run_outcome.scope` read
  `incomplete` and contributes **`1`** under `scope.on_incomplete: block`
  (`0` under the default `warn`), folded with `max` like the coverage axis;
  a run that completed no comparison at all contributes `1` under either
  setting (`no_comparison_completed`). Exit `8` requires a *proven*
  removal (NEW's inventory proven complete -- a stored `ProjectSnapshot`
  package or bundle-facts document whose capture asserted
  `inventory_complete`; the container type alone proves nothing); an
  unmatched library under an unproven inventory is never a
  removal (`unmatched_old` keeps listing it). Owners:
  `model/scope_acquisition.py` (the record), `policy/scope_completeness.py`
  (the fold), `workflows/release_scope.py` (the release builder, with D9's
  one-candidate narrowing -- applied only when NEW is *named* as a single
  file, never from a discovered one-member directory, so a PR-controlled
  NEW tree cannot narrow its way past `block`), `report/comparison_scope.py`
  (the section)
- `64` = usage error (bad flags/inputs; `cli._EXIT_USAGE_ERROR`) — applies across commands
- Full per-command matrix: `docs/reference/exit-codes.md`

