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

"""Shared sanitization for a project config folded into a synthesized,
always-``explicit`` ``--config`` overlay by this repository's own GitHub
Actions Python glue.

Two call sites build one of these overlays today: ``action/run.sh``'s
``_merge_config_overlay_with_discovered_project_config`` (used for the
compile-context and release-topology overlays) and
``actions/check-target/action.yml``'s "Generate assurance-overlay config"
step. Both read a project's ``.abicheck.yml`` (either auto-discovered by
walking up from a directory, or an operator-named explicit file), fold in a
synthesized top-level key, and write the merged result to a *fresh* path
that is then handed to the ``abicheck`` CLI as an EXPLICIT ``--config``.

Every ``explicit_config``/``config_explicit`` trust check inside the engine
(``cli_options.py``'s ``compile.compiler`` gate, ADR-032 D5's ``build.query``
gate, ``compare_bundle_facts.dispatch()``'s pre-``json.loads()`` decode-node
budget) treats "this run was given an explicit ``--config``" as an
operator's own deliberate authorization, regardless of how that file's
*content* was actually assembled. So an auto-discovered base document --
untrusted, PR-controlled content on a ``pull_request`` trigger -- must never
be folded into one of these overlays unchanged: doing so launders it into
that trusted, executable-authorized status the moment any unrelated Action
input triggers the overlay's synthesis (Codex review; see
``action/run.sh``'s own, more detailed docstring on
``_merge_config_overlay_with_discovered_project_config`` for the original
finding this module's checks were first written to close).

:func:`strip_untrusted_execution_keys` implements the part of that rule
shared byte-for-byte by both call sites -- stripping ``build.query``/
``compile.compiler`` outright and capping (never raising)
``resource_limits.max_bundle_facts_decode_nodes``. It deliberately does NOT
also handle ``build.compile_db``: ``action/run.sh``'s own merge keeps a
discovered ``build.compile_db`` when it demonstrably resolves against a
known ``--sources`` root (stripping a perfectly usable path is its own real
cost -- see that function's own docstring), a check neither this module nor
the assurance-overlay call site (which has no ``--sources`` root context at
all) can perform generically; each caller handles that field itself.

Must be called for a base document that is NOT itself an operator-supplied,
explicit ``--config`` (an auto-discovered ``.abicheck.yml``, in both call
sites' own terminology). A caller already holding a genuinely explicit base
document (``action/run.sh``'s own "explicit" merge mode; the assurance
overlay step's own ``BASE_CONFIG``-given branch) must NOT call this --
that document is already the trusted case these same gates exist to allow,
matching ``cli_options.py``'s own ``explicit_config = build_config is not
None`` line.

:func:`validate_base_config` is a third, orthogonal concern from the same
family: a base document -- discovered OR explicit -- loaded via a bare
``yaml.safe_load`` is syntactically valid YAML, but has never been run
through the real strict-schema check (``BuildConfig.from_dict``) the native
CLI's own ``load_build_config``/``discover_project_config`` loading path
always applies before any command sees the parsed config. Left unvalidated,
a structurally invalid document (``build.query: 7``, ``compile.compiler:
[]``, ``build.compile_db: false``) can have its own invalid field silently
STRIPPED by :func:`strip_untrusted_execution_keys` or REPLACED by either
caller's own top-level-key overlay merge before the nested engine ever gets
a chance to reject it -- turning a config a direct ``compare --config
<file>`` invocation would refuse outright into a silently-accepted run.
Must run BEFORE both of the other two functions in this module, for exactly
that reason -- validating a document only after its invalid fields have
already been stripped or overwritten proves nothing about what the operator
actually wrote. Unlike :func:`strip_untrusted_execution_keys`, it applies to
BOTH kinds of base document alike (discovered and explicit) -- an explicit
build-config is trusted to run ``build.query``/``compile.compiler``, but
that is a distinct question from whether it is schema-valid at all, and the
same ``compare --config <file>`` a user could run directly would reject an
explicit config just as loudly as a discovered one.

:func:`rebase_relative_config_paths` is an unrelated, purely-correctness
concern that applies to BOTH kinds of base document alike: relocating the
merged overlay to a fresh path (typically under ``$RUNNER_TEMP``, chosen so
a PR-planted symlink at a predictable in-checkout path can't hijack the
write -- see the assurance-overlay step's own comments) changes what a
config-relative path inside it resolves against. ``compile.include_dirs``
is the one config key resolved against the config file's own project root
(``config_paths.project_root_for_config`` -- confirmed the *only* such key
by grepping every ``buildsource/build_config.py`` field for a
``project_root_for_config`` consumer); left unrebased, a relative entry
silently resolves against the overlay's own scratch location instead of the
real project, dropping headers from extraction with no diagnostic.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .buildsource.build_config import BuildConfig
from .bundle_facts import DEFAULT_MAX_JSON_OBJECT_NODES
from .config_paths import project_root_for_config
from .frontends.cli.commands.compare_bundle_facts_rejections import (
    resolve_max_json_object_nodes_cfg,
)


def validate_base_config(doc: dict[str, object]) -> None:
    """Validate *doc* against the real ``BuildConfig`` schema -- the same
    strict-structure check (``BuildConfig._validate_structure``: unknown
    top-level keys, wrong-typed values) that ``load_build_config``/
    ``discover_project_config`` always apply before the native CLI ever
    sees a parsed config document.

    Raises ``ValueError`` (the identical exception :meth:`BuildConfig.
    from_dict` itself raises for a structurally invalid document) when
    *doc* is invalid; the caller decides how to present that failure (both
    current call sites format it as a GitHub Actions ``::error::``
    annotation and exit non-zero, matching what a direct ``compare
    --config <file>`` invocation against the same document would report).
    Does nothing when *doc* is schema-valid.

    See the module docstring for why this must run before
    :func:`strip_untrusted_execution_keys`/:func:`rebase_relative_config_paths`
    and why it applies to an explicit base document too, not just a
    discovered one.
    """
    BuildConfig.from_dict(doc)


def strip_untrusted_execution_keys(base: dict[str, object]) -> dict[str, object]:
    """Return a COPY of *base* with the fields an auto-discovered project
    config must never be trusted to carry into a synthesized, always-
    explicit ``--config`` overlay stripped or capped.

    Strips ``build.query`` and ``compile.compiler`` outright (both select
    code/an executable to run -- ``cli_options.py``'s ``compile.compiler``
    gate and ADR-032 D5's ``build.query`` gate exist specifically to
    withhold that from anything but an operator's own explicit
    ``--config``), and caps (never strips -- a lower value is never a
    decode-bomb risk) ``resource_limits.max_bundle_facts_decode_nodes`` via
    the identical
    :func:`~abicheck.frontends.cli.commands.compare_bundle_facts_rejections.
    resolve_max_json_object_nodes_cfg` the CLI itself calls, so the two can
    never drift.

    *base* is never mutated -- every changed top-level block is a fresh
    copy, so the caller's own reference is left untouched. Does not touch
    ``build.compile_db`` (see the module docstring for why that field is
    each caller's own responsibility), ``compile.include_dirs``, or any
    other path field -- see :func:`rebase_relative_config_paths` for that
    orthogonal concern.
    """
    base = dict(base)

    build_blk = base.get("build")
    if isinstance(build_blk, dict) and "query" in build_blk:
        build_blk = dict(build_blk)
        del build_blk["query"]
        base["build"] = build_blk
        print(
            "::warning::the discovered .abicheck.yml's build.query was "
            "dropped from this synthesized --config overlay -- an "
            "auto-discovered config is never trusted to run a build-system "
            "query; set build-config explicitly (naming a config you "
            "reviewed) to opt in.",
            file=sys.stderr,
        )

    compile_blk = base.get("compile")
    if isinstance(compile_blk, dict) and "compiler" in compile_blk:
        compile_blk = dict(compile_blk)
        del compile_blk["compiler"]
        base["compile"] = compile_blk
        print(
            "::warning::the discovered .abicheck.yml's compile.compiler was "
            "dropped from this synthesized --config overlay -- an "
            "auto-discovered config is never trusted to select a compiler "
            "executable; set build-config explicitly (naming a config you "
            "reviewed) to opt in.",
            file=sys.stderr,
        )

    resource_limits = base.get("resource_limits")
    if isinstance(resource_limits, dict):
        configured_nodes = resource_limits.get("max_bundle_facts_decode_nodes")
        # CodeRabbit review, fresh evidence: a wrong-typed configured_nodes
        # (a string, list, or bool -- bool is an int subclass, so it's
        # excluded explicitly) used to be coerced to None here before
        # calling resolve_max_json_object_nodes_cfg(), which returns None
        # right back for a None input -- capped_nodes (None) then compares
        # unequal to configured_nodes (the original wrong-typed value), so
        # the "capped" branch fired and OVERWROTE the value with a literal
        # null, printing a "capped to the conservative default" warning
        # that doesn't describe what actually happened. A wrong-typed value
        # is not this function's concern at all (see the module docstring:
        # :func:`validate_base_config` -- called first by every real
        # caller -- is what rejects it, with the same error a direct
        # ``compare --config <file>`` would raise); this function must
        # leave it untouched rather than inventing a replacement.
        if not isinstance(configured_nodes, int) or isinstance(configured_nodes, bool):
            return base
        capped_nodes = resolve_max_json_object_nodes_cfg(
            configured_nodes,
            config_explicit=False,
            default=DEFAULT_MAX_JSON_OBJECT_NODES,
        )
        if capped_nodes != configured_nodes:
            resource_limits = dict(resource_limits)
            resource_limits["max_bundle_facts_decode_nodes"] = capped_nodes
            base["resource_limits"] = resource_limits
            print(
                "::warning::the discovered .abicheck.yml's "
                "resource_limits.max_bundle_facts_decode_nodes was capped to "
                f"the conservative default ({DEFAULT_MAX_JSON_OBJECT_NODES}) "
                "when synthesizing this --config overlay -- an "
                "auto-discovered config is never trusted to raise this "
                "pre-json.loads() decode-bomb budget; set build-config "
                "explicitly (naming a config you reviewed) to opt in.",
                file=sys.stderr,
            )

    return base


def rebase_relative_config_paths(
    base: dict[str, object], *, found_path: Path
) -> dict[str, object]:
    """Return a COPY of *base* with every config-location-relative path
    field rewritten to an absolute path anchored at *found_path*'s own
    project root (``config_paths.project_root_for_config``), so the
    document keeps parsing the same surface after it is relocated to a
    fresh overlay path elsewhere on disk (typically under
    ``$RUNNER_TEMP``).

    ``compile.include_dirs`` is the only such field today (confirmed by
    grepping every ``buildsource/build_config.py`` field for a
    ``project_root_for_config`` consumer -- ``build.compile_db`` is a glob
    resolved against the ``--sources`` root instead, never the config
    file's own location, so it is unaffected by relocation and is handled
    by :func:`strip_untrusted_execution_keys` for an unrelated,
    trust-driven reason).

    Applies regardless of whether *base* is trusted (an operator's own
    explicit ``--config``) or not -- this is a pure correctness concern,
    orthogonal to :func:`strip_untrusted_execution_keys`'s trust-driven
    stripping: an explicit ``--config`` with a relative
    ``compile.include_dirs`` breaks exactly the same way a discovered one
    does the moment its document moves.
    """
    base = dict(base)
    compile_blk = base.get("compile")
    if not isinstance(compile_blk, dict):
        return base
    include_dirs = compile_blk.get("include_dirs")
    if include_dirs is None:
        return base

    root = project_root_for_config(found_path)

    def _abs(p: object) -> object:
        if not isinstance(p, str):
            return p
        pp = Path(p)
        return str(pp) if pp.is_absolute() else str((root / pp).resolve())

    compile_blk = dict(compile_blk)
    compile_blk["include_dirs"] = (
        [_abs(p) for p in include_dirs]
        if isinstance(include_dirs, list)
        else _abs(include_dirs)
    )
    base["compile"] = compile_blk
    return base
