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
"""Which consumer or plugin host a call scoped itself to (ADR-068 D5, ADR-091 Phase 5).

Split out of `evidence.py` to keep that module under its size record. Every
name here is re-exported from `evidence`, so `ev.is_consumer_scoped` and the
rest keep their spellings for `dimensions.py` and the tests.

A call names its consumer through `--used-by`/`--required-symbol` (the old
spellings) or through `--set consumers.required_symbols=[...]` (ADR-091 Phase
5); both read into the same `required_symbols` bucket.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

#: The dials `shared/consumer-scoping.md` documents. A call carrying any of
#: these answers a *different* question from an unscoped comparison of the
#: same pair — "does it break this consumer", not "does it break someone" —
#: and per that doc the two verdicts "legitimately diverge in both
#: directions", so neither is a filtered view of the other. ADR-068 D5 /
#: plan Phase 7h merged the separate `--required-symbols FILE` flag into
#: `--required-symbol @FILE`, so there is no longer a second flag spelling
#: to list here.
CONSUMER_SCOPE_FLAGS = ("--used-by", "--required-symbol")

#: ADR-091 Phase 5 moved the scoping and view inputs out of their own flags
#: and into `--set PATH=VALUE` (config keys). Each key below is recognised as
#: the *same* input its old spelling named, so a call using either spelling
#: grades identically. `consumers.required_symbols` is the equivalent of
#: `--required-symbol`. `consumers.manifests` has no recognised old spelling
#: (`--used-by-manifest` never reached the scope dial), so it is deliberately
#: not scope-recognised either. `output.view` replaced `--view`, which never
#: affected a grade, so it is parsed (Click arity keeps its value out of the
#: operands) but changes nothing. `severity.preset` is the equivalent of
#: `--severity-preset`, so it reports as that flag.
SET_REQUIRED_SYMBOLS_KEY = "consumers.required_symbols"
SET_SEVERITY_PRESET_KEY = "severity.preset"


def _set_overrides(argv: list[str]) -> dict[str, str]:
    """`--set PATH=VALUE` assignments in this argv, as raw `{PATH: VALUE}`.

    Both `--set PATH=VALUE` and `--set=PATH=VALUE` are read. A later repeat
    of the same PATH wins, matching `parse_set_assignments` on the real CLI.
    The value is returned as the raw text the shell passed; see
    `_flow_list_items` for the list shape.
    """
    overrides: dict[str, str] = {}
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--set" and index + 1 < len(argv):
            raw = argv[index + 1]
            index += 2
        elif token.startswith("--set="):
            raw = token[len("--set=") :]
            index += 1
        else:
            index += 1
            continue
        path, sep, value = raw.partition("=")
        if sep and path.strip():
            overrides[path.strip()] = value
    return overrides


def _flow_list_items(raw: str) -> list[str]:
    """The items of a YAML flow list `[a, "b", '@c.txt']`, as plain strings.

    `--set` values are read as YAML flow values (`parse_set_assignments`), so a
    list is written `[...]`. Only the flat, comma-separated shape the scoping
    docs use is handled here (no nested or escaped items). A value that is not
    a bracketed list names no symbols, which matches the CLI refusing it.
    """
    text = raw.strip()
    if not (text.startswith("[") and text.endswith("]")):
        return []
    items: list[str] = []
    for part in text[1:-1].split(","):
        item = part.strip().strip("'\"").strip()
        if item:
            items.append(item)
    return items


def _consumer_scope_set_items(argv: list[str]) -> list[str]:
    """The `consumers.required_symbols` items a `--set` assignment supplies."""
    raw = _set_overrides(argv).get(SET_REQUIRED_SYMBOLS_KEY)
    return [] if raw is None else _flow_list_items(raw)


def is_consumer_scoped(call: dict) -> bool:
    """Whether this call named a consumer/host, narrowing the question asked."""
    argv = call.get("argv", [])
    for token in argv:
        for flag in CONSUMER_SCOPE_FLAGS:
            if token == flag or token.startswith(f"{flag}="):
                return True
    return bool(_consumer_scope_set_items(argv))


#: Compiled-artifact suffixes stripped from a `--used-by` operand's basename,
#: on top of the plain basename match — a versioned SONAME (`renderer.so.2`)
#: strips every trailing `.N` segment along with the leading `.so`.
_BINARY_SUFFIX_RE = re.compile(r"\.(so(\.\d+)*|dll|dylib|exe)$", re.IGNORECASE)


def _stripped_binary_names(name: str) -> set[str]:
    """`name` with a trailing compiled-artifact suffix and/or a leading
    conventional `lib` prefix removed, every combination that applies.

    `--used-by` names a real path to the *built* consumer
    (`references/abicheck-adapter.md`'s own worked example: `--used-by
    path/to/consumer-binary`), and a real Unix shared-library build
    conventionally gives that path both a platform suffix (`renderer.so`,
    `renderer.so.2`, `renderer.dll`) and, for the plain-`.so` case
    specifically, a leading `lib` prefix (`librenderer.so`) — while a
    scenario's `invocation.used_by` declares the bare logical name
    (`renderer`) the fixture calls it. The plain-basename match already
    handles a literal, suffix-free operand; this closes the far more common
    case of an actual compiled artifact, in either shape. `lib` is stripped
    only when a recognized suffix was also present — `libwidget` (a real,
    unrelated consumer literally named that) must not lose its `lib` on the
    strength of a prefix alone. Returns an empty set when nothing matched,
    so a caller adds only genuinely new candidates.
    """
    match = _BINARY_SUFFIX_RE.search(name)
    if not match:
        return set()
    suffix_stripped = name[: match.start()]
    if not suffix_stripped:
        return set()
    candidates = {suffix_stripped}
    if suffix_stripped.startswith("lib") and len(suffix_stripped) > len("lib"):
        candidates.add(suffix_stripped[len("lib") :])
    return candidates


def _required_symbols_file_targets(call: dict, value: str) -> list[str]:
    """The symbol names listed in a `--required-symbol @FILE` argument.

    ADR-068 D5 / plan Phase 7h: the separate `--required-symbols FILE` flag
    is gone -- `@FILE` is now one of `--required-symbol`'s own repeatable
    values, so `value` here already has its leading `@` stripped by the
    caller.

    Best-effort, silently empty on any failure — matching the
    false-negative-over-false-positive default this module uses throughout.
    `value` is resolved against the call's own recorded `cwd` (the shim
    stamps this from `Path.cwd()` at call time — the workspace directory
    under the run directory, which persists on disk after the run, unlike
    the process's own transient cwd), not against the run directory itself:
    a relative `@FILE` operand is relative to where the agent ran the
    command, which need not be the workspace root.
    """
    cwd = call.get("cwd")
    if not cwd:
        return []
    path = (Path(cwd) / value).resolve()
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


class ScopeTargets(NamedTuple):
    """Targets this call declared, kept apart by which dial produced them.

    `--used-by` and `--required-symbol(s)` are two distinct scoping
    mechanisms with different meanings (`abicheck compare --help`:
    "narrow the compatibility question to a specific consumer" vs. "...to
    a specific required symbol") — a call scoped to a *consumer* answers a
    different question from one scoped to a *symbol*, even when the two
    happen to share a spelling. Merging them into one set let a
    `--required-symbol analytics-daemon` call (a coincidental name
    collision, not an actual consumer analysis) satisfy a scenario's
    declared `used_by: [analytics-daemon]` requirement (Codex review,
    PR #808).
    """

    used_by: frozenset[str]
    required_symbols: frozenset[str]


def consumer_scope_targets_by_kind(call: dict) -> ScopeTargets:
    """The consumer/symbol identities this call passed, split by dial.

    `--used-by`/`--required-symbol`, each repeatable and each carrying its
    value as a plain string operand, are recovered directly from argv.
    `--required-symbol @FILE` names a *file*, not the symbols themselves —
    its contents are read (see `_required_symbols_file_targets`) and each
    listed symbol contributes as its own `required_symbols` target; a file
    that can't be read (workspace already gone, unreadable path)
    contributes nothing rather than fabricating a match. (ADR-068 D5 /
    plan Phase 7h merged the separate `--required-symbols FILE` flag into
    this `@FILE` spelling.)

    `--used-by` takes a real path to the consumer binary, not a bare logical
    name — while a scenario's `invocation.used_by` declares the logical name
    (`renderer`) the fixture calls it. Matching the literal operand alone
    would hard-fail every correctly-scoped run, so each raw value
    contributes itself, its path basename, and (see `_stripped_binary_names`)
    that basename with its compiled-artifact suffix and/or conventional
    `lib` prefix stripped — `--required-symbol`'s operand is already a bare
    symbol name with no path structure, suffix, or prefix, so all of these
    transforms are no-ops there.
    """
    argv = call.get("argv", [])
    used_by: set[str] = set()
    required_symbols: set[str] = set()

    def _add(bucket: set[str], value: str) -> None:
        bucket.add(value)
        basename = Path(value).name
        bucket.add(basename)
        bucket.update(_stripped_binary_names(basename))

    def _add_required_symbols_file(value: str) -> None:
        for symbol in _required_symbols_file_targets(call, value):
            _add(required_symbols, symbol)

    def _add_required_symbol(value: str) -> None:
        # ADR-068 D5 / plan Phase 7h: '@FILE' is now one of
        # --required-symbol's own repeatable values, replacing the
        # separate --required-symbols FILE flag.
        if value.startswith("@"):
            _add_required_symbols_file(value[1:])
        else:
            _add(required_symbols, value)

    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--used-by" and i + 1 < len(argv):
            _add(used_by, argv[i + 1])
            i += 2
            continue
        if token == "--required-symbol" and i + 1 < len(argv):
            _add_required_symbol(argv[i + 1])
            i += 2
            continue
        if token.startswith("--used-by="):
            _add(used_by, token[len("--used-by=") :])
        elif token.startswith("--required-symbol="):
            _add_required_symbol(token[len("--required-symbol=") :])
        i += 1
    # `--set consumers.required_symbols=[...]` is the same dial as
    # `--required-symbol`, one item per list entry, `@FILE` included.
    for item in _consumer_scope_set_items(argv):
        _add_required_symbol(item)
    return ScopeTargets(frozenset(used_by), frozenset(required_symbols))


def consumer_scope_targets(call: dict) -> frozenset[str]:
    """The union of `consumer_scope_targets_by_kind(call)`'s two buckets.

    Kept for callers that only need "was any declared target touched at
    all" and don't need to distinguish which scoping mechanism produced
    it — `is_consumer_scoped`-adjacent checks, and this module's own
    tests. A check that must honor a *specific* declared `used_by` or
    `required_symbols` target should call `consumer_scope_targets_by_kind`
    directly instead, the way `dimensions.py`'s declared-target check
    does — merging back into one set here is exactly the collapse that
    let a same-spelled symbol satisfy an unrelated consumer requirement.
    """
    by_kind = consumer_scope_targets_by_kind(call)
    return by_kind.used_by | by_kind.required_symbols
