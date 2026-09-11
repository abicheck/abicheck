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

"""The ``compile:`` block per-field merge rule set for
``abicheck.action_config_overlay.apply_sources_root_config_blocks``.

Split out of ``action_config_overlay.py`` (ADR-061's new-file line ceiling)
rather than trimmed to fit -- see that module's own docstring for the full
trust-boundary context this rule set lives inside; this file holds only the
``compile:``-specific per-field precedence, reproducing
``cli_options.merge_compile_config``'s own two-stage (checkout, then
sources-root) fold over the raw ``compile:`` mapping rather than a real
``CompileContext`` round-trip (see :func:`merge_compile_block`'s own
docstring for why).
"""

from __future__ import annotations

from .cli_options import merge_compile_std_fields

#: ``compile:`` sub-keys that ``cli_options.merge_compile_config`` never
#: reads from a *second*, ``sources=``-supplied document at all -- each is
#: consumed exactly once, from the single already-resolved project config
#: (``resolved_cfg``/``project_cfg`` in ``cli_compare_helpers.py``/
#: ``frontends/cli/dump_debug_config.py``, and ``apply_compile_config_env_
#: toggles``'s own single ``bc`` parameter), the same one-document-only
#: shape ``source:``/``debug:`` have (see the module-level note on
#: ``_COMPILE_SOURCES_ONLY_KEYS`` below). Promoting one of these three from
#: a sources-root document would grant it an effect the real per-side
#: ``compare``/``dump`` pipeline never gives it once an explicit
#: ``--build-config`` (this overlay) is in play, so they are excluded from
#: promotion entirely -- the checkout document's own value (if any) is all
#: that ever applies.
_COMPILE_CHECKOUT_ONLY_KEYS = frozenset(
    {"ast_frontend_fallback", "allow_unsupported_castxml", "lang"}
)

#: ``compile:`` sub-keys for which ``merge_compile_config`` lets the
#: *later*-folded document's own value win outright once it sets one,
#: rather than the earlier one persisting -- the reverse of every other
#: scalar field below. ``frontend_context``: ``bc.compile_frontend_context
#: or cli_ctx.frontend_context`` -- the current stage's own value wins
#: whenever it sets one; only an *unset* current-stage value falls back to
#: the prior stage's. Since the real pipeline always folds the checkout
#: document first and a per-side ``--sources`` document second, "current
#: stage" here is the sources-root document -- so for this key
#: specifically the sources-root document's own value should win over the
#: checkout document's when both set one.
_COMPILE_SOURCES_WINS_KEYS = frozenset({"frontend_context"})

#: ``compile:`` sub-keys ``merge_compile_config`` effectively combines with
#: OR semantics across the two folded documents -- an already-``True``
#: value from either side always survives, never just "whichever document
#: was folded last" (Codex review, PR #1222 fifth round: classifying
#: ``nostdinc`` alongside ``frontend_context`` above made a sources-root
#: ``compile.nostdinc: false`` silently clear a checkout ``compile.
#: nostdinc: true``). ``nostdinc``'s real two-call shape is
#: ``cli_compare_helpers.py``'s ``nostdinc_explicit=_nostdinc_explicit or
#: compile_context.nostdinc`` feeding ``cli_options.merge_compile_config``'s
#: ``nostdinc = cli_ctx.nostdinc if nostdinc_explicit else bool(bc.
#: compile_nostdinc)`` for the second (sources-root) fold, where
#: ``cli_ctx`` is the already-resolved checkout-stage context and ``bc`` is
#: the sources-root document. Enumerating all four checkout/sources
#: booleans against that exact expression: (F, F) -> explicit=F ->
#: bc.compile_nostdinc=F; (F, T) -> explicit=F -> bc.compile_nostdinc=T;
#: (T, F) -> explicit=T (forced by ``compile_context.nostdinc``) ->
#: cli_ctx.nostdinc=T (the checkout value, sources' own ``False`` is never
#: consulted); (T, T) -> explicit=T -> cli_ctx.nostdinc=T. Every row equals
#: ``checkout or sources`` -- true wins from either document, unlike
#: ``frontend_context``'s "later document, if it sets one, replaces the
#: earlier" precedence above.
_COMPILE_OR_KEYS = frozenset({"nostdinc"})

#: ``compile:`` sub-keys whose value is a *semantic default sentinel*, not a
#: real "unset" marker -- ``merge_compile_config`` checks for the literal
#: string ``"auto"`` explicitly (Codex review, PR #1222 sixth round: a
#: checkout ``compile.frontend: auto`` was treated as "checkout already set
#: this key" by mere key presence, permanently blocking a sources-root
#: ``compile.frontend: clang``/``castxml`` from ever applying -- the real
#: pipeline treats a checkout-side ``"auto"`` exactly like an absent key).
#: ``frontend``'s real two-call shape is ``cli_options.merge_compile_
#: config``'s ``frontend = cli_ctx.frontend if (frontend_explicit or cli_ctx.
#: frontend != "auto") else (bc.compile_frontend or "auto")``, where
#: ``frontend_explicit`` is always ``False`` for a config-only (no
#: ``--ast-frontend`` on the command line) fold, ``cli_ctx`` is the
#: already-resolved checkout-stage context, and ``bc`` is the document being
#: folded on top. Enumerating checkout/sources against that exact
#: expression, for the second (sources-root) fold: checkout-stage result is
#: ``"auto"`` (whether the checkout document set ``frontend: auto``
#: explicitly, set nothing, or set a value that itself resolved to
#: ``"auto"``) -> ``cli_ctx.frontend != "auto"`` is ``False`` -> the
#: sources-root value wins outright, defaulting to ``"auto"`` when sources
#: sets nothing either. Checkout-stage result is any concrete value (e.g.
#: ``"clang"``) -> ``cli_ctx.frontend != "auto"`` is ``True`` -> the
#: checkout value wins outright, and the sources-root document's own value
#: (concrete or ``"auto"``) is never even consulted. So the full rule is:
#: a checkout value of exactly ``"auto"`` (or an absent key, which resolves
#: to the same ``"auto"`` through the ordinary "key not in checkout_blk"
#: branch below) never blocks the sources-root value; any other checkout
#: value always wins over the sources-root value, concrete or not.
#:
#: The sentinel check itself must match the real pipeline's own case
#: normalization (Codex review, fresh evidence, PR #1222 seventh round): a
#: real ``.abicheck.yml`` is parsed by ``build_config.BuildConfig.from_dict``,
#: which folds ``compile.frontend`` through ``_lowered()`` (a plain
#: ``str.lower()``) *before* validating it against the ``("auto", "castxml",
#: "clang", "hybrid")`` choice set -- matching the CLI's own
#: ``click.Choice(AST_FRONTENDS, case_sensitive=False)``. So a checkout
#: document spelling ``compile.frontend: AUTO``/``Auto``/etc. resolves to the
#: identical semantic-default sentinel a real run would see, and the overlay
#: merge below must fold the checkout value through the same ``.lower()``
#: before comparing it to the literal ``"auto"`` string -- a raw, case-
#: sensitive comparison would treat ``AUTO`` as a concrete checkout choice
#: and wrongly block a sources-root ``clang``/``castxml`` from ever applying.
_COMPILE_AUTO_DEFAULT_KEYS = frozenset({"frontend"})

#: Empty-checkout-string-counts-as-unset sibling of
#: ``_COMPILE_AUTO_DEFAULT_KEYS`` (Codex review, PR #1222, "Treat an empty
#: sysroot as unset during overlay merge"): the real ``merge_compile_
#: config`` gates ``sysroot``/``compiler`` on ``bc.compile_sysroot``/
#: ``bc.compile_compiler`` truthiness, not ``is not None``, so an empty
#: checkout string must not block a sources-root value either.
_COMPILE_EMPTY_STRING_UNSET_KEYS = frozenset({"sysroot", "compiler"})


def _lower_config_str(value: object) -> object:
    """Lowercase *value* the same way ``build_config._lowered()`` normalizes
    a real ``.abicheck.yml`` enum-ish scalar before validating it, so an
    auto-sentinel comparison here matches the real pipeline's case-
    insensitivity. Deliberately tolerant of a schema-invalid non-string
    value (returned unchanged) -- this module only merges, it does not
    re-validate (see :func:`merge_compile_block`'s own docstring)."""
    return value.lower() if isinstance(value, str) else value


#: ``compile:`` list-valued sub-keys ``merge_compile_config`` always
#: CONCATENATES across the two folded documents, never a plain override in
#: either direction. ``include_dirs`` appends sources-root's entries after
#: checkout's own (a pure ordering nicety -- both paths are honored
#: regardless). ``defines``/``options`` synthesize argv tokens and prepend
#: the CURRENT stage's tokens ahead of the prior stage's already-resolved
#: ones (``gcc_option_tokens = tuple(config_tokens) + gcc_option_tokens``),
#: so checkout's own tokens end up LAST and win a same-flag conflict (a
#: compiler honors a repeated flag's final occurrence) -- reproduced here
#: as data (sources-root entries first, checkout appended after) rather
#: than raw argv tokens, so these stay legible document keys instead of
#: ``compile.options`` token soup. Correct only when at most one document
#: sets any of ``std``/``defines``/``options`` -- see
#: :func:`cli_options.merge_compile_std_fields` (PR #1222).
_COMPILE_SOURCES_FIRST_LIST_KEYS = frozenset({"defines", "options"})
_COMPILE_CHECKOUT_FIRST_LIST_KEYS = frozenset({"include_dirs"})


def merge_compile_block(
    checkout_blk: dict[str, object], sources_blk: dict[str, object]
) -> dict[str, object]:
    """Merge a checkout-root and a sources-root ``compile:`` block the way
    ``cli_options.merge_compile_config`` folds a ``sources=``-supplied
    document on top of an already-resolved (CLI + checkout-config)
    ``CompileContext`` -- see that function's own precedence for each
    field, and the key-bucket constants above for the exact rule
    transcribed for each.

    Deliberately expressed over the raw ``compile:`` mapping rather than by
    round-tripping through a real ``CompileContext``: that dataclass fuses
    ``std``/``defines``/``options`` into opaque compiler-argv tokens for
    feeding a header-AST subprocess, which cannot be losslessly reconstructed
    back into ``compile.std``/``compile.defines``/``compile.options``
    document keys (this overlay must keep them as such -- the nested "Run
    analysis" invocation's own ``load_build_config`` re-parses this
    synthesized document exactly like any other project config).

    For every scalar key not covered by one of the buckets above
    (NOT ``std``, which only looks like one; see :func:`cli_options.
    merge_compile_std_fields`) ``merge_compile_config`` only consults the
    later-folded (sources-root) document's value when the earlier-folded
    (checkout) one left the field unset/default -- an explicit checkout
    value blocks the sources-root one from applying at all. ``frontend``'s
    own semantic-default value ``"auto"`` counts as unset too (see
    :data:`_COMPILE_AUTO_DEFAULT_KEYS`), and so does an empty-string
    ``sysroot``/``compiler`` checkout value (see
    :data:`_COMPILE_EMPTY_STRING_UNSET_KEYS`).
    """

    def _as_list(value: object) -> list[object]:
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            return list(value)
        # A schema-invalid non-list value here (already rejected by
        # validate_base_config for a real document) -- treated as a single
        # element rather than raised, matching this module's own "this
        # function only merges, it does not re-validate" scope.
        return [value]

    merged: dict[str, object] = dict(checkout_blk)
    std_fold = merge_compile_std_fields(checkout_blk, sources_blk)
    combined_std_keys = frozenset({"std", "defines", "options"} if std_fold else ())
    if std_fold is not None:
        for k in ("std", "defines", "options"):
            merged.pop(k, None)
        if std_fold["options"]:
            merged["options"] = std_fold["options"]

    for key in _COMPILE_SOURCES_FIRST_LIST_KEYS:
        if key in combined_std_keys:
            continue
        combined = _as_list(sources_blk.get(key)) + _as_list(checkout_blk.get(key))
        if combined:
            merged[key] = combined
    for key in _COMPILE_CHECKOUT_FIRST_LIST_KEYS:
        combined = _as_list(checkout_blk.get(key)) + _as_list(sources_blk.get(key))
        if combined:
            merged[key] = combined
    for key, value in sources_blk.items():
        if (
            key in _COMPILE_SOURCES_FIRST_LIST_KEYS
            or key in _COMPILE_CHECKOUT_FIRST_LIST_KEYS
            or key in _COMPILE_CHECKOUT_ONLY_KEYS
            or key in combined_std_keys
        ):
            continue
        if key in _COMPILE_OR_KEYS:
            merged[key] = bool(checkout_blk.get(key)) or bool(value)
        elif key in _COMPILE_SOURCES_WINS_KEYS:
            # ``bc.compile_frontend_context or cli_ctx.frontend_context`` --
            # a falsy sources-root value (``null``/``""``) is "unset" for
            # this fold and must fall back to the checkout value, not
            # overwrite it (CodeRabbit review, PR #1222).
            if value:
                merged[key] = value
        elif (
            key not in checkout_blk
            or (
                key in _COMPILE_AUTO_DEFAULT_KEYS
                and _lower_config_str(checkout_blk.get(key)) == "auto"
            )
            or (key in _COMPILE_EMPTY_STRING_UNSET_KEYS and not checkout_blk.get(key))
        ):
            merged[key] = value
        # else: checkout already set this non-default, non-"sources wins"
        # key -- checkout's own value stays (frontend/sysroot/compiler;
        # ``std`` only when not folded above).
    return merged
