# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Bug classes about abicheck's own delivery surface, not its ABI analysis.

The CLI a user types, the CI that ships it, and the developer tooling
around both -- as opposed to every other entry in this registry, which is
about the ABI/API analysis abicheck *performs*. The distinction is real
rather than a convenience: a defect here is found by running the tool's own
interfaces (a `compare` invocation, a workflow job, a path key on another
platform), never by comparing two libraries.

Split out of ``manifest.py`` when that module hit its
`architecture/debt.yaml` ``no_growth`` baseline (Codex review, PR #1184).
``manifest.py`` keeps the registry's behavior -- ``get``/``all_ids``, the
id-uniqueness and seed-test-existence checks -- and becomes an assembly
point over per-axis entry lists, the same shape ``abicheck/
change_registry.py`` already has over ``model/change_catalog/``'s taxonomy
modules. Adding a class here needs no edit to ``manifest.py`` at all.
"""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap

__all__ = ["TOOL_SURFACE_BUG_CLASSES"]

#: Registered in this order, appended after ``manifest.py``'s own list.
TOOL_SURFACE_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="trust_boundary.shell_workflow_injection",
        invariant=(
            "Every scalar input to a shell script or composite-Action "
            "step arrives as exactly one argument with exactly the same "
            "bytes, and untrusted data cannot create additional commands, "
            "$GITHUB_OUTPUT records, paths, or side effects."
        ),
        fixed_by=(705, 758, 836, 919, 1165),
        seed_tests=(
            "tests/test_reusable_workflow_execution.py",
            "tests/test_check_project_workflow_execution.py",
            "tests/test_action_run_sh_helpers.py",
            # PR #1165: `action/validate-inputs.sh`'s own annotation
            # emitters. Every message there interpolates a
            # workflow-controlled INPUT_* value into a line-delimited
            # GitHub annotation, so a value carrying a newline forged an
            # `::error::`/`::set-output::` command of its own -- a live
            # defect on the pre-existing `build-info` site, not only on
            # the tombstones the same PR added. Fixed once in the shared
            # `_warn`/`_fail` helpers.
            "tests/test_action_validate_inputs.py",
        ),
        public_surfaces=("github-action",),
        axes={
            "adversarial-shape": (
                "path-traversal",
                "shell-metacharacters",
                "command-substitution",
                "spaces",
                "tab",
                "leading-dash-flag-shaped",
                "multiple-flags-shaped",
                "quotes",
                "redirects",
                "newline-record-injection",
                "non-ascii",
                "empty-string",
            )
        },
        known_gaps=(
            KnownGap(
                description=(
                    "`action/run.sh` emits its own annotations with "
                    'inline `echo "::error::..."` calls rather than '
                    "through a shared helper, and several interpolate an "
                    "INPUT_* value the same way validate-inputs.sh did. "
                    "PR #1165 fixed validate-inputs.sh at its two "
                    "emitters (covering every site in that file at once) "
                    "but deliberately did not widen into run.sh's ~20 "
                    "inline sites, which need their own pass and their "
                    "own executing corpus."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
            KnownGap(
                description=(
                    "The hostile-input execution corpus (shared via "
                    "`_workflow_exec.HOSTILE_SCALAR_CORPUS`, Phase 8) now "
                    "covers two independently-maintained real sanitizer "
                    "copies (`check-single.yml`/`check-project.yml`) plus "
                    "`action/run.sh`'s word-splitting-sensitive `add_flag`/"
                    "`add_sided_flag` helpers — not every scalar input "
                    "across the repository's other shell scripts and "
                    "composite-action steps (e.g. the other workflows' "
                    "`run:` steps enumerated in the plan's own target-"
                    "script inventory), which is still the full scope "
                    "Phase 8's invariant is stated over."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md#phase-8",
            ),
        ),
    ),
    BugClass(
        id="ci.unrelated_apt_source_gates_the_job",
        invariant=(
            "A CI lane never fails because of a package repository none "
            "of its packages come from. `apt-get update` fails as a whole "
            "when any configured source fails -- including the "
            "third-party vendor repositories pre-baked into GitHub's "
            "runner images -- so its exit status must never gate a step; "
            "`apt-get install` is the gate, and a post-install check "
            "confirms the packages are actually present so relaxing the "
            "first gate cannot turn into a silent false success."
        ),
        fixed_by=(1182,),
        seed_tests=("tests/test_apt_install_hardening.py",),
        known_gaps=(
            KnownGap(
                description=(
                    "The workflow-wide half of the invariant is "
                    "structural (parsed YAML), not executed: a step's own "
                    "wiring needs a real GitHub Actions runner. The "
                    "script's behaviour is executed against a simulated "
                    "apt; that a given lane calls the script is asserted "
                    "over the workflow files only. A lane could still "
                    "install packages through a mechanism this scan does "
                    "not model (a Makefile, a setup script it invokes)."
                ),
                reference="tests/test_apt_install_hardening.py",
            ),
        ),
    ),
    BugClass(
        id="tooling.platform_dependent_path_key",
        invariant=(
            "A repo-relative path computed to serve as a lookup/comparison "
            "key -- an allowlist entry, a cache key, anything compared "
            "against a hand-written or previously-persisted forward-slash "
            "string -- must render with `.as_posix()`, never bare "
            "`str(Path)`. `str()` of a relative `Path` renders with the "
            "*host's native* separator (backslash on Windows), so a key "
            "computed that way silently fails to match every "
            "forward-slash-spelled entry on Windows alone, in both "
            "directions: an already-reviewed, allowlisted call site reads "
            "as a fresh, unreviewed violation, and a genuinely live "
            "allowlist entry reads as stale. Distinct from the "
            "canonical-identity `environment_taint` shape this registry "
            "otherwise tracks -- this is a path-*rendering* bug, not an "
            "identity or environment-capture one, and it fails "
            "deterministically on every Windows run rather than "
            "intermittently."
        ),
        fixed_by=(995, 1004),
        seed_tests=("tests/test_fact_bridged_replace_guard.py",),
        known_gaps=(
            KnownGap(
                description=(
                    "This class's fix and seed test are scoped to the one "
                    "file CI actually reported as broken "
                    "(`tests/test_fact_bridged_replace_guard.py`). No "
                    "repo-wide audit was run against the other AST-scan "
                    "gates that compute a similar path-shaped lookup key "
                    "(`scripts/check_ai_readiness.py`, "
                    "`scripts/fact_detector_misuse.py`/`fact_field_"
                    "readers.py` and their siblings) to confirm none of "
                    "them share the identical `str(path.relative_to(...))` "
                    "spelling -- a real, separate follow-up this class "
                    "does not yet close."
                ),
                reference="https://github.com/abicheck/abicheck/pull/1004",
            ),
        ),
    ),
    BugClass(
        id="cli_surface.retired_spelling_in_remediation",
        invariant=(
            "Remediation a tool prints must actually work when run: every "
            "`--flag` token in a user-facing message must be a live option "
            "of the command being advised, AND any concrete example it "
            "offers must resolve the condition it is offered for -- so "
            "following the tool's own advice can never itself be a usage "
            "error. Flag existence alone is not the invariant: a live flag "
            "aimed at the wrong operand fails just as hard."
        ),
        fixed_by=(1184,),
        seed_tests=("tests/test_cli_compare_release_project_snapshot_package.py",),
        public_surfaces=("cli",),
        axes={
            "error_path": (
                "ambiguous-variant",
                "unknown-variant-id",
                "empty-variant-id",
                "unknown-both-sides-id",
                "zero-variants-declared",
            ),
            "ambiguous_side": ("old", "new"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The seed test applies the oracle only to the variant "
                    "family's own error paths. A repo-wide AST sweep run "
                    "while fixing this (non-docstring string literals under "
                    "`abicheck/` containing any spelling in "
                    "`scripts/retired_surfaces.py`'s RETIRED_SURFACES) "
                    "reports 44 hits across 25 modules, and several look "
                    "like the same defect already sitting in the tree -- "
                    "`pdb_utils.py`'s 'use --pdb-path to override', "
                    "`reporter_markdown.py`'s 'Unknown --show-only token', "
                    "`dumper.py`'s '--dwarf-only requested but ...'. They "
                    "are NOT mechanically decidable: a flag retired from "
                    "`compare`/`dump` can still be live on `scan` (which "
                    "kept the whole compile-context and debug-resolution "
                    "families), and `cli_compare_release.py`'s unregistered "
                    "release engine legitimately still *defines* several of "
                    "them, so a sweep-turned-gate would need ~25 "
                    "hand-judged allowlist entries -- the shape AGENTS.md "
                    "warns is itself a smell. Each site needs reading "
                    "against the command that emits it. Deliberately not "
                    "attempted in PR #1184: it is a separate change from "
                    "the CLI option audit, and a hasty sweep would ship a "
                    "large unreviewed allowlist rather than close the class."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
)
