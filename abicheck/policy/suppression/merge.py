# Copyright 2026 Nikolay Petrov
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

"""The one owner of "several ``--suppress`` files make one rule set".

``--suppress`` is repeatable: a build-variant matrix typically passes one
shared exceptions file plus one per-variant file. Every consumer (two-sided
``compare``, release/directory fan-out, ``--no-baseline``, dry-run previews,
the typed API) loads its suppression input through
:func:`load_suppression_files`, so the merge rules live here once:

* **Order.** Rules keep command-line file order, then in-file order. Matching
  is first-match, so an earlier file's rule wins a finding; a later rule that
  also matches is recorded as *shadowed* (ADR-067 D3), never dropped.
* **Provenance.** Every rule keeps the file it came from and its 0-based
  position *in that file* (:func:`rule_origin`), so the disposition audit, the
  ``unused_rules`` gate and strict-expiry errors all name the right document.
* **Duplicates.** An identical rule in two files is two configured rules: both
  are reported, both count matches in the suppression audit, and only the
  first one is the disposing rule. The same path given twice is read once.
* **One file** is exactly :meth:`SuppressionList.load` -- no wrapper type, so
  single-file output is unchanged.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, TypeAlias, cast

from .rules import Suppression, SuppressionList

#: What a front end hands the loaders: one path, several, or nothing.
SuppressPaths: TypeAlias = Path | tuple[Path, ...]


def suppress_paths(value: object) -> tuple[Path, ...]:
    """*value* (``None``, one path, or an iterable of paths) as a tuple.

    Repeats of the same path are dropped (first occurrence kept): reading one
    document twice would only manufacture shadowed duplicates of itself.
    """
    if value is None:
        return ()
    items = (value,) if isinstance(value, (str, Path)) else cast("Iterable[Any]", value)
    return tuple(dict.fromkeys(Path(item) for item in items))


def normalize_suppress_option(value: object) -> SuppressPaths | None:
    """Click callback shape: ``None`` for none, a bare ``Path`` for one.

    Keeping the one-file value a plain ``Path`` is what keeps every one-file
    invocation byte-identical to the single-valued option it replaced.
    """
    paths = suppress_paths(value)
    if not paths:
        return None
    return paths[0] if len(paths) == 1 else paths


def suppress_label(value: object) -> str:
    """Human label for *value*: the path, or ``a.yml, b.yml`` for several."""
    return ", ".join(str(p) for p in suppress_paths(value))


class MergedSuppressionList(SuppressionList):
    """Rules of several loaded files, in order, each keeping its origin.

    ``source_path`` is ``None`` (no single document holds these rules; use
    :func:`rule_origin`), and ``source_sha256`` digests the ordered
    ``(path, file digest)`` pairs, so changing any file, or their order,
    changes the digest the effective-config receipt records.
    """

    def __init__(self, parts: Sequence[SuppressionList]) -> None:
        digest = hashlib.sha256(
            "".join(f"{p.source_path}\0{p.source_sha256}\n" for p in parts).encode()
        ).hexdigest()
        super().__init__(
            [rule for part in parts for rule in part.rules], source_sha256=digest
        )
        self.parts = tuple(parts)
        self.source_paths = tuple(p.source_path for p in parts)
        self.rule_origins: tuple[tuple[str | None, int], ...] = tuple(
            (p.source_path, i) for p in parts for i in range(len(p))
        )


def load_suppression_files(
    value: object, *, require_justification: bool = False
) -> SuppressionList | None:
    """Load and merge every ``--suppress`` file in *value* (``None`` if none).

    Errors are :meth:`SuppressionList.load`'s own; with several files a
    ``ValueError`` is prefixed with the offending path, since the loader's
    own message names only the entry index.
    """
    paths = suppress_paths(value)
    if not paths:
        return None
    if len(paths) == 1:
        return SuppressionList.load(
            paths[0], require_justification=require_justification
        )
    parts: list[SuppressionList] = []
    for path in paths:
        try:
            parts.append(
                SuppressionList.load(path, require_justification=require_justification)
            )
        except ValueError as e:
            raise ValueError(f"{path}: {e}") from e
    return MergedSuppressionList(parts)


def rule_origin(suppression: object, index: int) -> tuple[str | None, int]:
    """``(source file, index in that file)`` of rule *index* of *suppression*."""
    origins = getattr(suppression, "rule_origins", None)
    if origins is not None:
        return cast("tuple[str | None, int]", origins[index])
    source = getattr(suppression, "source_path", None)
    return (None if source is None else str(source), index)


def expired_by_file(
    suppression: SuppressionList,
) -> list[tuple[str | None, list[tuple[int, Suppression]]]]:
    """Expired rules grouped by their file, with in-file indexes, file order kept."""
    grouped: dict[str | None, list[tuple[int, Suppression]]] = {}
    for index, rule in suppression.check_expired_strict():
        source, local = rule_origin(suppression, index)
        grouped.setdefault(source, []).append((local, rule))
    return list(grouped.items())
