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

"""The one truthy/falsey parser every ``ABICHECK_*`` boolean knob reads.

Before this module each knob hand-rolled its own comparison, and the set of
tokens each accepted had drifted apart with nothing anywhere to notice:
``ABICHECK_COLLECT_COMDAT`` accepted ``on`` nowhere (``{"1","true","yes"}``)
while ``ABICHECK_ALLOW_AST_FALLBACK`` did; ``ABICHECK_PARALLEL_EXTRACTION``
recognized ``0``/``false``/``no`` but not ``off`` while the neighbouring
``ABICHECK_PREPROCESSOR_SCAN`` recognized all four;
``ABICHECK_CLANG_PRUNE_DEPENDENCY_DECLS`` accepted the single literal ``1``;
and ``ABICHECK_CC_DISABLE`` was read as *any non-empty value*, so
``ABICHECK_CC_DISABLE=0`` — the one spelling a user reaches for to say "no,
don't disable it" — silently turned source-fact capture off (external CLI
audit, ``one-comparison-product.md`` Phase 7l). That is one defect in a
class, so the fix is the class: every variable below resolves through
:func:`env_flag`, and a variable is a boolean knob here only by being
*registered* in :data:`BOOLEAN_ENV_FLAGS` with the default it carries when
unset.

The rules, stated once:

* **Unset, empty, or whitespace → the registered default.** A variable that
  is not saying anything cannot be read as saying either thing.
* ``1``/``true``/``yes``/``on`` → ``True``; ``0``/``false``/``no``/``off`` →
  ``False``, case- and surrounding-whitespace-insensitive. The same ten
  tokens on every variable, whichever way its default points — an opt-in
  knob accepts ``off`` and an opt-out knob accepts ``on``, and neither is a
  no-op the user has to discover empirically.
* **An unrecognized value → the registered default**, never the opposite of
  it. This is deliberately the same answer as "unset": a value this parser
  cannot interpret is not evidence for flipping a knob, and the failure the
  class is named after is exactly a knob flipped by a value nobody meant as
  a flip. It is also what every pre-existing reader already did (each
  tested membership of its *own* polarity's token set and fell through to
  its default), so unifying on it changes no behaviour beyond the token
  sets themselves and the ``ABICHECK_CC_DISABLE`` bug.

**Why ``extract/`` owns this.** Every knob in the registry below governs
how evidence is captured -- whether the AST frontend may fall back, whether
an unsupported castxml is allowed, whether the system-include probe runs,
whether the streaming pruner engages, whether COMDAT groups and the
preprocessor pre-scan are collected, whether the two sides extract
concurrently, and whether the ``abicheck-cc`` wrapper captures at all. That
is one coherent responsibility, and it is this package's
(``AGENTS.md``: "read a binary, debug, header, build, or source fact").
Every reader is itself classified ``extract``, except the compare
pipeline's concurrency check, and ``workflows -> extract`` is a permitted
direction.

It was briefly placed in ``model/`` on the reasoning that every layer may
import that ring. That was wrong on the contract, not merely on taste:
``model/`` owns *shapes* and answers "what is this fact", never "how was it
produced" (``abicheck/model/AGENTS.md``), while this module reads the live
process environment and resolves a runtime input. Putting behavior there
would have made the innermost ring a general-purpose utility layer and
invited configuration resolution at arbitrary call sites (Codex review,
PR #1278).
"""

from __future__ import annotations

from collections.abc import Mapping

#: Tokens that mean "on", whatever the variable's own default is.
TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})

#: Tokens that mean "off", whatever the variable's own default is.
FALSE_TOKENS = frozenset({"0", "false", "no", "off"})

#: Every ``ABICHECK_*`` variable that is a boolean knob, and the value it
#: carries when unset. Registration is what *makes* a variable one: a name
#: not listed here is rejected by :func:`env_flag` rather than silently
#: defaulting, so a new knob cannot be added with a fresh hand-rolled
#: comparison and no entry (`tests/test_env_flags.py` asserts both the
#: value domain over this whole registry and that no reader under
#: ``abicheck/`` still parses one of these by hand).
BOOLEAN_ENV_FLAGS: dict[str, bool] = {
    # ── Opt-in: off unless asked for ────────────────────────────────────
    "ABICHECK_ALLOW_AST_FALLBACK": False,
    "ABICHECK_ALLOW_UNSUPPORTED_CASTXML": False,
    "ABICHECK_CC_DISABLE": False,
    "ABICHECK_CLANG_PRUNE_DEPENDENCY_DECLS": False,
    "ABICHECK_COLLECT_COMDAT": False,
    # ── Opt-out: on unless switched off ─────────────────────────────────
    "ABICHECK_AUTO_SYSTEM_INCLUDES": True,
    "ABICHECK_PARALLEL_EXTRACTION": True,
    "ABICHECK_PREPROCESSOR_SCAN": True,
}


def parse_env_flag(raw: str | None, *, default: bool) -> bool:
    """Resolve one raw environment value against *default*.

    Separate from :func:`env_flag` so the rule can be exercised (and
    reasoned about) without an environment or a registry entry.
    """
    if raw is None:
        return default
    token = raw.strip().lower()
    if token in TRUE_TOKENS:
        return True
    if token in FALSE_TOKENS:
        return False
    return default


def env_flag(name: str, environ: Mapping[str, str] | None = None) -> bool:
    """Whether the registered boolean knob *name* is on in *environ*.

    *environ* defaults to the live process environment; the ``abicheck-cc``
    wrapper passes its own already-captured mapping so its pass-through
    behaviour stays testable without mutating ``os.environ``.
    """
    if name not in BOOLEAN_ENV_FLAGS:
        raise KeyError(
            f"{name!r} is not a registered ABICHECK_* boolean knob -- add it to "
            "abicheck.env_flags.BOOLEAN_ENV_FLAGS with the default it carries "
            "when unset, rather than parsing it by hand at the call site"
        )
    if environ is None:
        import os

        environ = os.environ
    return parse_env_flag(environ.get(name), default=BOOLEAN_ENV_FLAGS[name])
