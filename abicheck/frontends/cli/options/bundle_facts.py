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

"""``--bundle-facts-library-manifest`` (G38 Phase 17): a small, standalone
leaf module for one Click option.

Declared here rather than inline on ``compare_cmd`` (``frontends/cli/
commands/compare.py``, which sits at the architecture 800-line production
cap with no headroom for another inline ``@click.option``) or folded into
``frontends/cli/options/release.py`` (that decorator family is about the
general directory/package release fan-out; this flag is specific to
``--old-bundle-facts``, a narrower, separate surface). Applying a Click
option declared in a sibling module costs the applying command exactly one
decorator line, regardless of how large the option's own declaration is --
the same reason ``secondary_output_options``/``release_options`` already
live outside ``compare.py`` itself.
"""

from __future__ import annotations

from pathlib import Path

import click

#: A YAML/JSON manifest of per-library header/include/compile-context
#: overrides for ``compare --old-bundle-facts``, parsed by
#: :func:`abicheck.workflows.bundle_facts_library_overrides.parse_bundle_facts_library_overrides`.
#: Meaningless without ``--old-bundle-facts`` -- rejected explicitly by
#: :func:`reject_bundle_facts_manifest_without_old_bundle_facts` below,
#: rather than silently ignored, since a live directory/package
#: comparison's own per-library needs are unrelated to this
#: stored-facts-specific gap.
bundle_facts_manifest_options = click.option(
    "--bundle-facts-library-manifest",
    "bundle_facts_library_manifest",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="With --old-bundle-facts: a YAML/JSON manifest giving one or more "
    "libraries their own header root, include path, or compile context, "
    "instead of the uniform --header/--include/compile-context flags -- "
    "for a bundle whose libraries don't share one toolchain (e.g. a "
    "plain-C++ library alongside a -fsycl/icpx one). Shaped "
    "{library_name: {headers: [...], includes: [...], gcc_path: ..., "
    "gcc_options: [...], sysroot: ..., ...}}; a library not named in the "
    "manifest keeps the uniform fallback.",
)


def reject_bundle_facts_manifest_without_old_bundle_facts(
    kwargs: dict[str, object],
) -> None:
    """Pop and reject ``--bundle-facts-library-manifest`` on the ordinary
    (non-``--old-bundle-facts``) ``compare`` dispatch path.

    Called from ``compare_cmd`` right before it forwards *kwargs* to
    ``run_compare``, mirroring ``_reject_bundle_facts_out_for_single_pair``'s
    precedent for the sibling producer-side flag (``cli_compare_options.py``).
    """
    if kwargs.pop("bundle_facts_library_manifest", None) is not None:
        raise click.UsageError(
            "--bundle-facts-library-manifest is only supported together "
            "with --old-bundle-facts."
        )
