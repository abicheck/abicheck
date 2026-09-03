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

"""ADR-040 Lever 3: ``compare``'s named run profiles -- the data half.

Split out of :mod:`abicheck.cli_options` when that module reached the
2000-line hard cap: the profile table, the ``--profile`` option declared from
it, and the key its injection is recorded under are what a reader looking for
"what does ``ci-gate`` mean" wants, and nothing else in ``cli_options``
reads them.

Deliberately a **leaf**: it imports only ``click``, restating the one-line
``F`` TypeVar rather than importing it back from its former home (exactly as
:mod:`abicheck.cli_help` already restates it), so the split adds no edge to
the CLI-registration import cycle. That is also why
``apply_compare_profile``/``_profile_targets_set_input`` stayed behind --
they reach ``cli_resolve``, which would have pulled this module straight into
that cycle. :mod:`abicheck.cli_options` re-exports every name here, so each
existing caller -- and each existing test importing them from there -- is
unaffected.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

import click

F = TypeVar("F", bound=Callable[..., object])

#: ADR-040 Lever 3 — named run profiles for ``compare``. Each maps a profile
#: name to a bundle of ``{option-dest: value}`` defaults for the documented
#: workflows. A profile is a *default layer*: an explicitly-passed flag always
#: wins (see :func:`apply_compare_profile`), mirroring the config < CLI rule and
#: the way ``--severity-preset`` collapses four severity flags into one token.
#: Values are in each option's *resolved* form (``depth`` uses the canonical
#: ``USER_DEPTHS`` rungs, ``fmt``/``exit_code_scheme`` the ``Choice`` strings,
#: booleans as ``bool``) so they can be injected without re-running conversion.
COMPARE_PROFILES: dict[str, dict[str, object]] = {
    # CI gate: fast header-depth check, compact review digest, severity-aware
    # exit codes — the "block the PR" workflow. (Public-surface scoping is the
    # default, so the profile does not restate it — a project's .abicheck.yml
    # scope choice stays authoritative.)
    "ci-gate": {
        "depth": "headers",
        "fmt": "review",
        "exit_code_scheme": "severity",
    },
    # Release cut: deepest evidence, full Markdown report with a semver/SONAME
    # recommendation appended — the "should I bump?" flow. Named "release-cut"
    # rather than bare "release" (CLI audit finding): compare's directory/
    # package fan-out mode is *also* informally branded "release" throughout
    # (compare_release_cmd, release_options, the "Release (directory/package
    # inputs)" help panel) -- an unrelated concept that shares only the word.
    # `_profile_targets_set_input` already rejects `--profile` on a directory/
    # package operand with a clear error, so the collision was never a live
    # bug, but a user skimming --help could reasonably (and wrongly) assume
    # the two are related.
    "release-cut": {
        "depth": "source",
        "fmt": "markdown",
        # No "recommend": True key any more (CLI cleanup phase two, PR 1) --
        # markdown output always includes the release recommendation now,
        # so this profile no longer needs to ask for it.
    },
    # Quick look: symbols-only, one-line summary — the "just tell me" flow.
    # "fmt": "oneline" (not "stat": True -- CLI cleanup phase two, PR 1: the
    # boolean --stat flag is gone) is service_render.ONELINE_FORMAT, an
    # internal-only value never offered on the public --format Choice list,
    # so it is reachable only through this profile injecting it. An explicit
    # --format on the command line still wins over the profile (the usual
    # "explicit flag > profile" precedence, apply_compare_profile), so
    # `--profile quick --format json` now genuinely returns full JSON rather
    # than the old --stat boolean's json-shaped summary -- a deliberate
    # behaviour refinement, not an oversight: the profile's one-line default
    # only applies when the user didn't ask for a specific format.
    "quick": {
        "depth": "binary",
        "fmt": "oneline",
    },
}


def profile_option(func: F) -> F:
    """The ``--profile`` option (ADR-040 Lever 3): one token for a workflow.

    Kept here so ``cli.py`` stays under its size cap and any future front-end
    shares one spelling/help. The value is validated against
    :data:`COMPARE_PROFILES`; application (default-layering under explicit flags)
    happens in :func:`apply_compare_profile`.
    """
    func = click.option(
        "--profile",
        "profile",
        type=click.Choice(list(COMPARE_PROFILES), case_sensitive=True),
        default=None,
        help="Run-profile preset bundling workflow defaults (ADR-040): "
        "'ci-gate' (headers depth, review digest, severity exit codes), "
        "'release-cut' (source depth, recommendation, Markdown -- the "
        "'should I bump semver?' flow; distinct from directory/package "
        "'release' comparisons, which this profile does not apply to), "
        "'quick' (symbols-only, one-line summary). Explicit flags override "
        "the profile; single-pair compares only (configure release defaults "
        "in .abicheck.yml).",
    )(func)
    return func


#: ``click.Context.meta`` key under which :func:`apply_compare_profile`
#: records the selected profile and the values it injected, for the ADR-049
#: D7 receipt. ``meta`` (not ``obj``/a parameter) because the injection
#: happens in the CLI wrapper while the consumer is several call layers down,
#: and ``meta`` is click's own documented place for exactly that.
RUN_PROFILE_META_KEY = "abicheck.run_profile"
