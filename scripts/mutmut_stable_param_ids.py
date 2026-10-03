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

"""pytest plugin: parametrize ids that stay the same across in-process sessions.

mutmut 3 runs several pytest sessions in one process: the stats pass records
every test's node id, then the clean run and the per-mutant runs (forked from
that process) select tests by those ids. pytest caches the ids it generated
for a ``parametrize`` mark on the mark itself (``_param_ids_generated``) and,
in the next session, feeds them back through id resolution as if they were
user-given explicit ids -- so any explicit id containing a character pytest
escapes (a backslash, a newline, non-ASCII) is escaped once more per
session: ``x[a\\b]`` is recorded, ``x[a\\\\b]`` is collected. pytest then
reports the recorded id as not found, exits 4, and mutmut aborts the lane
(``BadTestExecutionCommandsException`` in "Running clean tests"; CI run
37077168345, ``tests/test_project_package_archive.py``'s
``refs\\artifacts\\x.json`` id). Auto-generated ids are not affected.

Escaping is applied by ``_resolve_parameter_set_ids``, so the cached value V
is exactly ``escape(raw ids)``, and escaping is invertible. The first time a
later session meets a mark, the plugin hands pytest ``unescape(V)`` (each
entry verified by re-escaping it with pytest's own function) and restores V
once that function is parametrized. Every later session therefore follows
the first session's path step for step -- including pytest's own spelling
for the second function a shared mark reaches, which the first session
already escapes twice -- for every way ``ids`` can be given, a one-shot
generator included. Nothing is changed in the first session. Loaded only by
``[tool.mutmut].pytest_add_cli_args`` (``-p scripts.mutmut_stable_param_ids``;
mutmut puts ``mutants/`` on ``sys.path`` and ``scripts/`` is in
``also_copy``). It changes no test's selection or outcome, only which spelling
a later session gives its id.
"""

from __future__ import annotations

from collections.abc import Callable, Generator, Sequence

import pytest

_escape: Callable[[str, pytest.Config | None], str] | None
try:  # pytest's own escaping, used to verify each inverse exactly
    from _pytest.python import _ascii_escaped_by_config as _escape
except ImportError:  # pragma: no cover - only on an unsupported pytest
    _escape = None

_SEEN = pytest.StashKey[set[int]]()


def unescape_ids(
    ids: Sequence[object],
    escape: Callable[[str, pytest.Config | None], str] | None,
    config: pytest.Config | None,
) -> list[str] | None:
    """The raw ids pytest escaped into *ids*, or None if any entry is not
    exactly invertible (then pytest's own behaviour is left untouched)."""
    if escape is None:
        return None
    raw = []
    for value in ids:
        if not isinstance(value, str) or not value.isascii():
            return None
        try:
            candidate = value.encode("ascii").decode("unicode_escape")
        except UnicodeDecodeError:  # e.g. a lone trailing backslash
            return None
        if escape(candidate, config) != value:
            return None
        raw.append(candidate)
    return raw


@pytest.hookimpl(hookwrapper=True)
def pytest_generate_tests(metafunc: pytest.Metafunc) -> Generator[None]:
    seen = metafunc.config.stash.setdefault(_SEEN, set())
    restore = []
    for mark in metafunc.definition.iter_markers(name="parametrize"):
        origin = getattr(mark, "_param_ids_from", None)
        if origin is None or id(origin) in seen:
            continue
        seen.add(id(origin))
        cached = getattr(origin, "_param_ids_generated", None)
        if cached is None:  # first session: nothing cached, nothing to undo
            continue
        raw = unescape_ids(cached, _escape, metafunc.config)
        if raw is None:
            continue
        # Mark is a frozen dataclass; pytest writes this field the same way
        # (_pytest/python.py, Metafunc.parametrize).
        object.__setattr__(origin, "_param_ids_generated", raw)
        restore.append((origin, cached))
    try:
        yield
    finally:
        for origin, cached in restore:
            object.__setattr__(origin, "_param_ids_generated", cached)
