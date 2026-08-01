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

"""ADR-049's shared CLI option decorators (contract evaluation, packs).

A leaf under :mod:`abicheck.cli_options`, which re-exports both names so
existing import paths are unchanged. Split out for the ordinary reason this
repo splits modules: `cli.py` reached its 2000-line hard limit when `--pack`
was added, moving these 41 lines of option definitions into `cli_options.py`
pushed *that* file over the same limit, and shrinking their help text to buy
space would have cut the user-facing documentation of a feature to make room
for its own code.

Both decorators are shared on purpose rather than copied per command:
`tests/test_cli_contract.py` pins that one concept uses one canonical
spelling, and for `--pack` the resolver already records that exact option
name as the selector (`resolve_selected_packs`'s own default), so a second
spelling would make a receipt name an option that does not exist.

Imports nothing from any `cli*` module, so registering these on a command
cannot pull a new member into the CLI-registration import cycle the
`import-cycle-growth` gate guards.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

import click

#: Same shape `cli_options` uses for its own decorators: Click's own `FC`
#: type var is invariant over the decorated object, so a plain callable
#: alias keeps these composable with every other option decorator.
F = TypeVar("F", bound=Callable[..., Any])


#: ADR-049's three contract-evaluation options, as one decorator. Extracted
#: from ``cli.py`` when adding ``--pack`` pushed that file past the
#: 2000-line hard limit: 41 lines of option definitions for one cohesive
#: concept is exactly what this module holds, and the alternative --
#: trimming their help text to buy space -- would have shrunk the
#: user-facing documentation of a feature to make room for its own code.
def contract_options(f: F) -> F:
    """Attach ``--contract-evaluation`` / ``--contract`` / ``--audit-suppressions``."""
    f = click.option("--audit-suppressions", "audit_suppressions", is_flag=True, default=False,
                  help="Audit the --suppress rule file against this run's findings: which "
                       "rules matched nothing (stale), matched a BREAKING change (high "
                       "risk), are expired, or expire soon. Requires --suppress. Adds a "
                       "suppression_audit key in --format json, a '## Suppression Audit' "
                       "section in markdown/review. Advisory only.")(f)
    f = click.option("--contract", "contract_mode",
                  type=click.Choice(["public", "exports", "all"]), default=None,
                  help="Which evidence domain --contract-evaluation judges each finding "
                       "against (ADR-049 Phase 6). 'public': the header-derived declared "
                       "surface. 'exports': the binary's own export table (ELF .dynsym / "
                       "PE export directory / Mach-O export trie) plus the raw type "
                       "closure reachable from it -- a private-header type reached from a "
                       "real export is inside this contract, an unexported public-header "
                       "declaration is not. 'all': every entity, no root or closure "
                       "evidence required. Omitted, the domain follows "
                       "--scope-public-headers/--no-scope-public-headers as before; an "
                       "explicit value outranks those. Requires --contract-evaluation, "
                       "and is advisory exactly like it: selecting a domain never "
                       "changes verdict, exit_code, or which findings appear.")(f)
    f = click.option("--contract-evaluation", "contract_evaluation", is_flag=True, default=False,
                  help="ADR-049 Phase 3's shadow contract evaluator (non-authoritative; "
                       "pick its evidence domain with --contract). Stamps each finding in "
                       "the report with a "
                       "contract_relevance (IN_CONTRACT/PROVEN_OUT_OF_CONTRACT/"
                       "UNKNOWN_UNPROVEN/UNKNOWN_UNRESOLVED/NOT_APPLICABLE), "
                       "contract_reason_code, and -- when resolved -- contract_assurance "
                       "field, reflecting whether the finding falls inside the library's "
                       "declared public contract. Rendered per finding in --format json/"
                       "markdown; --format review's compact digest does not (its "
                       "top-impacted-symbols list predates this field), except for the "
                       "--used-by/--required-symbol scoped-gate appendix, which renders it "
                       "under json/markdown/review alike. Not yet rendered in sarif/junit/"
                       "html. --format json additionally carries contract_evidence_refs "
                       "per finding (which evidence records its decision rests on) and a "
                       "top-level contract_context block (the observed provider evidence, "
                       "the resolved evaluation context, and the decision receipt), so a "
                       "decision can be replayed or re-evaluated later without re-reading "
                       "the binaries. Advisory only: it never changes verdict, exit_code, "
                       "or which findings appear. Default off; the report is unchanged "
                       "unless this is set.")(f)
    return f
