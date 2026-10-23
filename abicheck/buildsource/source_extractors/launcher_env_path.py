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
"""``PATH``-entry resolution for an ``env``-prefixed compile driver (ADR-030 D2).

Split out of :mod:`.launcher_env` to keep both modules under the line cap.
:func:`_apply_env_context` folds an ``env -C DIR`` chdir and an ``env PATH=...``
assignment into the driver token :func:`~.compile_invocation.strip_launchers`
returns: a relative driver is joined onto the effective directory, and a bare
driver name is resolved through the env-supplied ``PATH`` when it is found there.
Pure: nothing here shells out.
"""

from __future__ import annotations

import re
import shutil

from ._argv_shortopts import (
    is_absolute_path_token,
    join_path_token,
)

#: Matches a Windows drive-letter-and-separator sequence (``C:\`` / ``C:/``)
#: at the START of the string it is tested against -- used by
#: :func:`_split_posix_path_value` to recognize the ONE legitimate reason a
#: literal ``:`` inside a POSIX-style ``:``-joined PATH value must NOT be
#: treated as a separator: a single ``PATH`` entry that is itself an
#: absolute Windows path.
_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _split_posix_path_value(value: str) -> list[str]:
    """Split *value* on a LITERAL ``:`` -- POSIX ``env``'s own, fixed
    ``PATH`` entry separator, regardless of the analyzing host's own
    ``os.pathsep`` (Codex review, live Windows CI signal, fresh evidence;
    see :func:`_resolve_env_path_entries`'s own docstring for why
    host-native ``os.pathsep`` is wrong here at all).

    **Does NOT split a Windows drive-letter colon** (``C:\\...``/
    ``C:/...``): a genuine POSIX-recorded multi-entry value never contains
    one (POSIX has no drive letters), but this module's own tests --
    and, plausibly, real evidence gathered by an `env` invocation captured
    while a build itself ran under Windows -- legitimately use a single
    Windows-absolute-path ``PATH`` entry, which a blind "split on every
    ``:``" would otherwise cut in two (``C:\\Users\\x`` ->
    ``["C", "\\Users\\x"]``). Detected the same way ``is_absolute_path_token``
    already recognizes a Windows-absolute token (a single ASCII letter
    immediately followed by ``:`` and a path separator) -- checked
    positionally against each candidate colon (exactly one preceding,
    unsplit character that is itself a letter), not merely "the string
    somewhere contains a drive-letter pattern", so a colon anywhere else
    in the same value still splits normally.
    """
    parts: list[str] = []
    current: list[str] = []
    n = len(value)
    for i, ch in enumerate(value):
        is_drive_colon = (
            ch == ":"
            and len(current) == 1
            and current[0].isalpha()
            and i + 1 < n
            and value[i + 1] in "\\/"
        )
        if ch == ":" and not is_drive_colon:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def _resolve_env_path_entries(
    env_path: str, chdir: str | None, directory: str | None
) -> list[str]:
    """Compose each *relative* entry of an ``env``-supplied ``PATH``
    override against the directory GNU ``env`` actually executes the
    command from, before it is handed to :func:`shutil.which` (Codex
    review, ``_argv.py:576``, Finding 3, fresh evidence).

    GNU ``env`` applies ``-C DIR`` (chdir) *before* it ``execvp()``s the
    command, and ``execvp`` searches a relative ``PATH`` entry relative to
    the process's *current* (i.e. already-chdir'ed) working directory --
    not abicheck's own CWD. For ``env -C build PATH=../tool clang-cl ...``
    recorded for a compile unit whose own ``directory`` is ``/work``, GNU
    ``env`` searches ``/work/build/../tool`` (== ``/work/tool``), never
    ``<abicheck's own CWD>/../tool``. Without this composition,
    :func:`_apply_env_context`'s ``shutil.which(token, path=env_path)`` call
    resolved a relative entry against abicheck's own process CWD instead --
    leaving the driver bare (not found) or, worse, silently resolving to a
    *different* executable that happens to exist at the wrong, un-chdir'ed
    location.

    *directory* is only available to callers that already have the full
    :class:`~abicheck.buildsource.build_evidence.CompileUnit` in scope
    (:func:`pick_compiler_binary` here) -- ``None`` for every other,
    directory-agnostic caller of :func:`strip_launchers`, which leaves an
    entry composed against *chdir* alone (relative to an unknown base, the
    pre-existing best-effort behavior) rather than fabricating a directory
    that was never supplied.

    An already-absolute entry is passed through unchanged -- unambiguous
    regardless of *chdir*/*directory*. Uses :func:`join_path_token`, not
    host-native ``os.path.join``, for the same cross-OS-evidence reasons as
    every other join in this module.

    **An EMPTY entry means the effective current directory, per POSIX/GNU
    ``PATH`` semantics** (Codex review, ``_argv.py:708``, Finding 3, fresh
    evidence): ``PATH=:`` or ``PATH=/a::/b`` both contain a component
    between two colons (or a leading/trailing colon) that names no
    directory at all -- POSIX and GNU coreutils both document this as
    equivalent to ``.``, the shell's/process's own current directory (here,
    *base* -- ``directory`` composed with any ``-C``/``--chdir``, exactly
    the value a genuine ``.`` entry already resolves against). The previous
    behavior left an empty component unchanged, so it fell through to
    :func:`_apply_env_context`'s ``shutil.which(token, path=env_path)`` with
    an empty ``PATH`` entry -- which ``shutil.which`` resolves against
    ITS OWN caller's (abicheck's) process CWD, not the compile unit's
    effective directory, the identical wrong-CWD failure mode this whole
    function exists to prevent for a genuinely relative entry.

    **Splits on a LITERAL ``:``, never host-native ``os.pathsep`` (live
    Windows CI signal, fresh evidence).** A recorded ``env``-supplied
    ``PATH=`` value is POSIX ``env``'s own evidence text -- colon-separated
    by that utility's OWN, fixed convention, regardless of which host OS
    later ANALYZES this evidence. ``os.pathsep`` is host-native (``;`` on
    Windows), so splitting on it there left a colon-separated evidence
    string as one single, unsplit, bogus entry -- confirmed empirically: a
    Windows CI run split ``":"`` on ``";"`` (no match) into ``[":"]``, one
    literal-colon "entry", corrupting every downstream join. Returns a
    LIST of composed directory candidates now (not a ``:``-joined string)
    for the identical reason one level up: :func:`_apply_env_context`
    used to hand the joined string straight to a single
    ``shutil.which(token, path=...)`` call, which ALSO splits its own
    ``path`` argument on host-native ``os.pathsep`` internally -- so even a
    correctly ``:``-joined POSIX-style string was mis-split again by
    ``shutil.which`` itself on Windows (its own drive-letter colon,
    ``C:\\...``, made this doubly ambiguous: splitting on a literal ``:``
    there would also wrongly cut a drive letter in two). Returning a list
    and letting the caller search each directory with its own
    single-directory ``shutil.which`` call sidesteps ANY pathsep
    representation question entirely -- a lone directory string handed to
    ``shutil.which`` is never split at all, regardless of what characters
    (``:`` or otherwise) it happens to contain.
    """
    if not directory:
        return _split_posix_path_value(env_path)
    base = directory
    if chdir:
        base = (
            join_path_token(directory, chdir)
            if not is_absolute_path_token(chdir)
            else chdir
        )
    entries = _split_posix_path_value(env_path)
    return [
        entry
        if is_absolute_path_token(entry)
        else base
        if not entry
        else join_path_token(base, entry)
        for entry in entries
    ]


def _apply_env_context(
    token: str,
    chdir: str | None,
    env_path: str | None,
    directory: str | None = None,
) -> str:
    """Fold a leading ``env -C DIR``/``env PATH=...`` prefix's effect into
    *token*, the driver argv token :func:`strip_launchers` is about to
    return (P2 review round 19, both findings, fresh evidence).

    Chosen deliberately as the ONE place both effects are resolved, rather
    than threading ``chdir``/``env_path`` as new fields through every one of
    :func:`strip_launchers`'s several call sites
    (``header_compile_context._derived_gcc_path``, ``adapters.base``'s
    driver-position scan, ``cc_wrapper.py``, ``include_graph.py``,
    ``build_context.py``): folding the effect into the token itself here
    means every existing caller gets the corrected token for free, with no
    signature change and no new field for a caller that doesn't care about
    ``env`` to thread through unused.

    **Chdir** (``-C DIR``): only meaningful for a *relative, separator-
    bearing* token -- a bare PATH name (``clang-cl``, matched by
    :func:`is_msvc_mode` and friends via basename lookup) is looked up on
    ``PATH``, never relative to any directory, and an already-absolute
    token needs no adjustment. For a relative, path-shaped token, DIR is
    folded onto it via a plain lexical join+normalize (``os.path.join`` +
    ``os.path.normpath``, matching ``header_compile_context.
    _resolve_driver_token``'s own lexical, symlink-blind convention for
    every other redacted/relative ``CompileUnit`` path field) -- the result
    is still a *relative* path (DIR is not itself resolved against
    anything here), so the existing downstream join against
    ``cu.directory`` in ``_resolve_driver_token`` composes correctly
    without needing to know an ``env -C`` prefix was ever involved: joining
    ``build`` onto ``../llvm/bin/clang-cl`` and normalizing collapses to
    ``llvm/bin/clang-cl``, which ``_resolve_driver_token`` then joins onto
    ``cu.directory`` exactly as it already does for any other relative
    driver token.

    **PATH** (``PATH=...``): only meaningful for a *bare* token (no path
    separator at all) -- a path-shaped token is unambiguous regardless of
    which ``PATH`` would have been searched. Resolved immediately via a
    ``shutil.which(token, path=<one directory>)`` call PER composed
    candidate directory (see :func:`_resolve_env_path_entries`'s own
    docstring for why one call per directory, not one call with a
    pathsep-joined string, is what a Windows-analysis-host correctness fix
    required) into an absolute path when found, so no downstream consumer
    needs to know the env override existed at all; left unchanged (the
    pre-fix, bare-name behavior) when the name isn't found in any
    candidate directory either -- the same conservative, no-worse-than-
    before fallback every other best-effort resolution in this module
    already uses.

    A token that is itself a flag (starts with ``-``) -- meaning
    :func:`strip_launchers` found no real driver token at all -- is left
    completely untouched; neither effect applies to a non-existent driver.

    **Host-independent absolute/join grammar (Codex review, "Recognize
    foreign absolute driver paths", fresh evidence).** The chdir branch
    below used to check ``os.path.isabs(token)`` and join with plain
    ``os.path.join``/``os.path.normpath`` -- both host-native. Evidence
    collected on one OS and analyzed on another (a POSIX build's compile
    database inspected from a Windows CI runner, or the reverse) means
    *token* is not always spelled in the host's own grammar: a POSIX
    absolute token like ``/opt/llvm/bin/clang-cl`` reads as *relative* to
    ``os.path.isabs`` on Windows, so it was wrongly re-joined onto *chdir*
    a second time, and a relative token joined via host-native
    ``os.path.join`` on Windows produced a backslash-separated result even
    when *chdir*/*token* were both recorded in POSIX form. Delegates to
    :func:`is_absolute_path_token`/:func:`join_path_token`, which check and
    compose using the token's own separator grammar instead of the host's.
    """
    if not token or token.startswith("-"):
        return token
    if "/" in token or "\\" in token:
        if chdir and not is_absolute_path_token(token):
            return join_path_token(chdir, token)
        return token
    if env_path:
        for candidate_dir in _resolve_env_path_entries(env_path, chdir, directory):
            resolved = shutil.which(token, path=candidate_dir)
            if resolved:
                return resolved
    return token
