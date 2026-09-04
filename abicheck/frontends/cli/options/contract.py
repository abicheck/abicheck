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

"""ADR-049's shared contract-evaluation CLI options.

A leaf under :mod:`abicheck.cli_options`, which re-exports the name so
existing import paths are unchanged.

The split happened while ``--pack`` first pushed ``cli.py`` past its
2000-line hard limit, and moving these option definitions into
``cli_options.py`` pushed *that* file over the same limit. ``--pack`` was
then reverted before merge (it configured nothing; see
:mod:`abicheck.pack_application`) and has since come back for real, so the
original reason applies again as written -- but the split would be right
either way: option definitions for one cohesive concept is what this module
is for, and the alternative both times, trimming their help text to buy
space, would shrink a feature's user-facing documentation to make room for
its own code.

One decorator rather than a copy per command: `tests/test_cli_contract.py`
pins that a shared concept uses one canonical spelling.

Imports nothing from any `cli*` module, so registering these on a command
cannot pull a new member into the CLI-registration import cycle the
`import-cycle-growth` gate guards.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import click

#: Same shape `cli_options` uses for its own decorators: Click's own `FC`
#: type var is invariant over the decorated object, so a plain callable
#: alias keeps these composable with every other option decorator.
F = TypeVar("F", bound=Callable[..., Any])


#: ADR-049 D8's pack selector. Separate from :func:`contract_options` because
#: the two answer different questions and are not registered on the same set
#: of commands: ``--pack`` configures the run, while the contract options ask
#: for a decision about each finding. Both change verdicts and exit codes --
#: the contract options since ADR-049 Phase 7 made relevance authoritative.
def pack_option(f: F) -> F:
    """Attach the repeatable ``--pack`` manifest selector."""
    # Assigned rather than returned inline, matching `contract_options` below:
    # the click decorator is untyped, so returning its result directly is an
    # `Any` return from a function declared to return `F` (mypy no-any-return).
    f = click.option(
        "--pack",
        "pack_paths",
        multiple=True,
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        help="Select an ADR-049 D8 pack manifest (repeatable). A pack is a "
        "small versioned YAML document (id/version/kind/assignments) "
        "carrying one reusable piece of configuration. 'kind: policy' "
        "assigns ChangeKind slugs to break/warn/risk/ignore, exactly as "
        "--policy's overrides do; 'kind: contract' assigns "
        "surface.internal_namespaces and contract.unresolved (the "
        "latter needs --contract, which is what computes "
        "the coverage it configures); 'kind: gate' assigns "
        "gate.exit_code_scheme and gate.severity.<category>. Composition "
        "is D8's: an explicitly stated value (--policy, "
        "--exit-code-scheme, --severity-preset, a --profile, or .abicheck.yml) "
        "always outranks a pack, and two selected packs assigning "
        "different values to the same field are a usage error unless "
        "something else already states it. A manifest assigning a field "
        "this build resolves but does not yet apply is rejected rather "
        "than silently recorded. On a directory/package (release) "
        "comparison, a 'kind: policy'/'kind: contract'/'kind: gate' pack's "
        "policy.overrides/surface.internal_namespaces/contract.unresolved/"
        "gate.exit_code_scheme/gate.severity.<category> all apply to every "
        "library uniformly (folded into the release's own resolved "
        "GateOptions); contract.unresolved still needs --contract on that "
        "release comparison, same as everywhere else. On `scan` "
        "this requires --against (a pack's only application there is "
        "the baseline comparison), and a 'kind: gate' pack's gate.exit_"
        "code_scheme/gate.severity.<category> apply to the baseline "
        "comparison's exit code the same way --severity-preset/"
        "--exit-code-scheme given directly already do.",
    )(f)
    return f


#: ADR-049's contract-evaluation options, as one decorator -- see this
#: module's docstring for why they live here rather than in ``cli.py``.
def contract_options(f: F) -> F:
    """Attach ``--contract`` / ``--audit-suppressions``."""
    f = click.option(
        "--audit-suppressions",
        "audit_suppressions",
        is_flag=True,
        default=False,
        help="Audit the --suppress rule file against this run's findings: which "
        "rules matched nothing (stale), matched a BREAKING change (high "
        "risk), are expired, or expire soon. Requires --suppress. Adds a "
        "suppression_audit key in --format json, a '## Suppression Audit' "
        "section in markdown/review. Advisory only.",
    )(f)
    f = click.option(
        "--contract",
        "contract_mode",
        type=click.Choice(["public", "exports", "all", "auto"]),
        default=None,
        help="Which evidence domain each finding is judged against "
        "(ADR-049 Phase 6), and the flag that turns the contract "
        "evaluator on -- omit it and nothing about the run changes. "
        "'public': the header-derived declared surface. 'exports': the "
        "binary's own export table (ELF .dynsym / PE export directory / "
        "Mach-O export trie) plus the raw type closure reachable from it "
        "-- a private-header type reached from a real export is inside "
        "this contract, an unexported public-header declaration is not. "
        "'all': every entity, no root or closure evidence required. "
        "'auto': evaluate, but let the domain be chosen by the D7 "
        "precedence chain below an explicit CLI value -- the "
        "--scope-public-headers/--no-scope-public-headers legacy alias, "
        "then .abicheck.yml. "
        "Each finding is stamped with a contract_relevance (IN_CONTRACT/"
        "PROVEN_OUT_OF_CONTRACT/UNKNOWN_UNPROVEN/UNKNOWN_UNRESOLVED/"
        "NOT_APPLICABLE), a contract_reason_code and -- when resolved -- "
        "a contract_assurance, rendered per finding in --format json/"
        "markdown, in sarif/junit properties, and as an html badge; "
        "--format review's compact digest renders it only in the "
        "--used-by/--required-symbol scoped-gate appendix. --format json "
        "additionally carries contract_evidence_refs per finding (which "
        "evidence records the decision rests on) and a top-level "
        "contract_context block (observed provider evidence, resolved "
        "evaluation context, decision receipt), so a decision can be "
        "replayed or re-evaluated later without re-reading the binaries. "
        "**The decisions are authoritative** (ADR-049 Phase 7): relevance "
        "is classified before compatibility policy, and policy scores only "
        "IN_CONTRACT/NOT_APPLICABLE findings -- so this changes verdicts "
        "and exit codes. Nothing is hidden: an excluded finding stays in "
        "the report with the relevance and reason that explain why it did "
        "not gate. Uncertainty is not treated as compatible either -- if "
        "the selected domain's required evidence is incomplete, the "
        "orthogonal contract-coverage ledger contributes exit 1, folded "
        "with max so it never lowers an ABI break's 2/4. Set "
        "contract.unresolved=warn (e.g. via a `kind: contract` --pack) to "
        "accept incomplete coverage: that zeroes the contribution while "
        "still reporting every failure. The coverage floor itself applies "
        "to a directory/package (release) comparison too -- each library's "
        "own floor is max()-folded into the release's exit code the same "
        "way, and a pack-supplied contract.unresolved applies there too "
        "(see --pack's own help).",
    )(f)
    return f
