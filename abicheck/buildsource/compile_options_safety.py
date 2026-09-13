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

"""``compile.options`` plugin-loading rejection (CodeRabbit review, PR #1146,
finding #2). Split out of :mod:`build_config` (rather than added inline) to
keep that module under its architecture line-count cap -- a leaf module with
no dependency on :class:`~abicheck.buildsource.build_config.BuildConfig`
itself, imported from there.

Compiler/frontend flags that load arbitrary native code into the compiler
process (a Clang/GCC plugin, or an ``-Xclang``-smuggled equivalent) have no
legitimate use case for header-ABI extraction, so they are rejected outright
in ``compile.options`` regardless of whether the surrounding
``.abicheck.yml`` is trusted (an explicit ``--config``) or auto-discovered
-- unlike ``compile.compiler`` (see ``cli_options.merge_compile_config``),
this isn't a trust-tier question: nothing downstream needs the ability at
all. ``build_config._safe_compile_atom`` already rejects whitespace inside
ONE list item (blocking a single ``"-Xclang -load evil.so"`` atom), but a
YAML list happily carries the equivalent as separate, individually
whitespace-free items (``[-Xclang, -load, evil.so]``) that reassemble into
the same argv once appended to the compiler invocation -- :func:`
reject_plugin_loading_options` scans the whole already-tokenized list, not
each token in isolation.
"""

from __future__ import annotations

_PLUGIN_LOADING_BARE_OPTIONS = frozenset(
    {
        "-load",
        "-plugin",
        "-add-plugin",
        "-plugin-arg",
    }
)
_PLUGIN_LOADING_PREFIXES = (
    "-fplugin=",
    "-fplugin-arg-",
    "-fpass-plugin=",
)


def reject_plugin_loading_options(tokens: list[str]) -> None:
    """Raise ``ValueError`` if ``tokens`` (``compile.options``) load a plugin.

    Catches a self-contained flag (``-fplugin=./evil.so``), the
    ``-Xclang``-prefixed two-token form Clang uses to pass an otherwise
    frontend-only flag through the driver (``-Xclang -load -Xclang
    ./evil.so``, and its documented ``-Xclang=<arg>`` joined alias) -- the
    two-token form only smuggles a plugin when the token *following*
    ``-Xclang`` is itself one of the plugin-loading options, so a
    legitimate, unrelated ``-Xclang <frontend-flag>`` pair is left alone --
    and, unconditionally, Clang's own ``--config``/``--config=<file>``
    configuration-file mechanism and the ``@<file>`` response-file
    convention, both of which indirect into a separate file this scan
    cannot see the contents of.
    """
    for index, token in enumerate(tokens):
        if (
            token.startswith(_PLUGIN_LOADING_PREFIXES)
            or token in _PLUGIN_LOADING_BARE_OPTIONS
        ):
            raise ValueError(
                f"compile.options: plugin-loading flag {token!r} is not permitted "
                "(loading arbitrary native code into the compiler is not a "
                "supported use case for header-ABI extraction)"
            )
        if token == "-Xclang" and index + 1 < len(tokens):
            nxt = tokens[index + 1]
            if nxt in _PLUGIN_LOADING_BARE_OPTIONS or nxt.startswith(
                _PLUGIN_LOADING_PREFIXES
            ):
                raise ValueError(
                    f"compile.options: plugin-loading flag {nxt!r} (via -Xclang) "
                    "is not permitted (loading arbitrary native code into the "
                    "compiler is not a supported use case for header-ABI "
                    "extraction)"
                )
        elif token.startswith("-Xclang="):
            # Clang documents `-Xclang=<arg>` (`--help-hidden`) as an alias
            # for the separate `-Xclang <arg>` form above -- the two-token
            # check alone leaves this joined spelling free to smuggle the
            # identical plugin-loading argument straight past it.
            joined = token[len("-Xclang=") :]
            if joined in _PLUGIN_LOADING_BARE_OPTIONS or joined.startswith(
                _PLUGIN_LOADING_PREFIXES
            ):
                raise ValueError(
                    f"compile.options: plugin-loading flag {joined!r} (via "
                    "-Xclang=) is not permitted (loading arbitrary native "
                    "code into the compiler is not a supported use case for "
                    "header-ABI extraction)"
                )
        elif (
            token == "--config"
            or token.startswith("--config=")
            or token.startswith("@")
        ):
            # Both Clang's own `--config <file>`/`--config=<file>`
            # configuration-file mechanism (`clang --help-hidden`) and the
            # GCC/Clang-common `@<file>` response-file convention let an
            # otherwise-scanned argv token indirect into a SEPARATE file
            # whose own contents are never scanned by this function --
            # verified locally against the real Clang contract (`--config=
            # <file>` genuinely loads a named compiler plugin from a config
            # file's own `-fplugin=...` line). A sibling file is exactly as
            # reachable to a PR-controlled `.abicheck.yml` as the config
            # itself, so this is not a narrower attack surface than the
            # inline forms above -- rejected unconditionally, the same as
            # every other plugin-loading vector here (Codex review, fresh
            # evidence -- real finding on PR #1154).
            raise ValueError(
                f"compile.options: {token!r} may indirect to a plugin-loading "
                "flag in a separate file (Clang's --config/--config=<file> "
                "mechanism and the @<file> response-file convention both "
                "point at a file whose contents this check cannot scan) and "
                "is not permitted; header-ABI extraction has no legitimate "
                "use for either"
            )
