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

"""Already-read input sources for compatibility-evaluation configuration.

:class:`SuppressionSource` and :class:`PublicSymbolsList` carry a file a front
end has already read, together with the digest of the bytes that were parsed,
so a receipt describes exactly the content that scored the run (the single-read
rule). :func:`collect_force_public_symbols` merges inline ``--public-symbol``
values with a ``--public-symbols-list`` file the way the live comparison does.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SuppressionSource:
    """A selected ``--suppress`` file, already read and digested.

    Kept separate from :class:`ExplicitCompatibilityInputs` so the resolver
    itself stays I/O-free: :meth:`from_file` is the one place that touches
    the filesystem, and a caller that already loaded the rule file (every
    real front end does, well before configuration is resolved) can build
    this from what it has.
    """

    path: str | None
    sha256: str
    rules: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "rules", tuple(self.rules))

    @classmethod
    def from_loaded(
        cls, suppression: Any, path: Any = None
    ) -> SuppressionSource | None:
        """Build one from a list a front end already loaded. ``None`` passes through.

        The single-read rule: built from the list that really scored the run,
        never a re-read of *path*, so the digest cannot describe content that
        did not.

        **The digest falls back to the rules' own content** when the list
        carries no ``source_sha256``. That is not a rare case: a
        programmatically constructed or merged
        :class:`~abicheck.suppression.SuppressionList` has none, and every
        front end that accepts one could hand it over. Taking ``""`` there --
        which each of them previously did, by spelling this
        ``getattr(...) or ""`` inline -- made :class:`SuppressionConfig`
        reject the input and fail an otherwise-valid run (Codex review, found
        on the scan path; the MCP path had it too). ``contract_context.
        suppression_config_for`` already derived the fallback correctly, so
        this is that rule shared rather than a fourth transcription of it:
        the rules are persisted verbatim beside the digest, so a digest over
        them authenticates exactly what a replay would re-read.
        """
        if suppression is None:
            return None
        from ..contract_evidence_collect import content_digest

        rules = tuple(suppression.rule_identities())
        return cls(
            path=str(path) if path is not None else None,
            sha256=getattr(suppression, "source_sha256", None)
            or content_digest(list(rules)),
            rules=rules,
        )

    @classmethod
    def from_file(
        cls, path: str | Path, *, require_justification: bool = False
    ) -> SuppressionSource:
        """Read, digest, and summarize a real suppression file.

        ``sha256`` is the digest ``SuppressionList.load`` captured over the
        raw bytes it parsed (exact replay, ADR-049 D6); ``rules`` is
        :meth:`~abicheck.suppression.SuppressionList.rule_identities`. One
        read produces both, so they always describe the same content.
        *require_justification* is forwarded to the loader so a front end
        running with ``--require-justification`` gets the same hard error
        here that it gets from its own load.
        """
        from ..suppression import SuppressionList

        file_path = Path(path)
        loaded = SuppressionList.load(
            file_path, require_justification=require_justification
        )
        # The digest comes from the same read that produced these rules --
        # digesting the path separately could pair one content's hash with
        # another's rules if the file changed in between (Codex review).
        return cls(
            path=str(file_path),
            sha256=loaded.source_sha256 or "",
            rules=loaded.rule_identities(),
        )


@dataclass(frozen=True)
class PublicSymbolsList:
    """A selected ``--public-symbols-list`` file, already read.

    Kept apart from the inline ``--public-symbol`` values rather than merged
    into them: flattening the two lost which symbols came from the file, so
    the receipt for ``surface.explicit_scope`` named only ``--public-symbol``
    and no path — even for a list-file-only invocation, which a replay then
    had no way to reproduce (Codex review).
    """

    path: str | None
    items: tuple[str, ...] = ()
    #: The digest of the file's raw bytes. The items alone cannot stand in for
    #: it: reading drops comments and blank lines, and the union that forms
    #: ``surface.explicit_scope`` sorts and deduplicates, so a real edit to
    #: this file can leave the item list identical -- exactly the drift
    #: :class:`~abicheck.compatibility_evaluation_config.DigestedItems` says a
    #: digest exists to catch (Codex review, fresh evidence).
    sha256: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))

    @classmethod
    def from_file(cls, path: str | Path) -> PublicSymbolsList:
        items, digest = read_symbols_list_with_digest(path)
        return cls(path=str(path), items=items, sha256=digest)


def _normalized_symbols(values: Iterable[str]) -> tuple[str, ...]:
    """Inline symbol values, normalized the way the live run normalizes them.

    :func:`collect_force_public_symbols` -- what the real comparison forces --
    strips every value and drops blanks, so keeping ``--public-symbol ' foo '``
    verbatim here resolved ``surface.explicit_scope`` to ``" foo "`` for a run
    that actually forces ``"foo"``: an effective configuration, and a digest,
    that misrepresent the run (Codex review, fresh evidence). Applied in the
    input models rather than in one adapter so every front end -- and a caller
    constructing the dataclass directly -- normalizes identically. The
    list-file reader (:func:`read_symbols_list`) already does the same.
    """
    return tuple(stripped for value in values if (stripped := value.strip()))


def collect_force_public_symbols(
    public_symbols: tuple[str, ...],
    symbols_list: Path | None,
    *,
    already_read: PublicSymbolsList | None = None,
) -> set[str]:
    """Merge ``--public-symbol`` values with a ``--public-symbols-list`` file.

    The list file is one symbol per line; blank lines and ``#`` comments are
    ignored (à la abi-compliance-checker ``-symbols-list``). Inline trailing
    comments are not stripped — a ``#`` must start the line to be a comment.

    Lives here rather than in the CLI layer, so this module can build ``surface.explicit_scope`` from *both* sources without
    importing a CLI-layer module. Reading only the inline tuple made a
    list-file-only invocation resolve to no explicit scope at all, and a
    mixed one silently drop every symbol the file supplied (Codex review).

    *already_read* lets a caller that will *also* build a receipt from this
    file supply the one read both need. Reading it twice pairs the receipt's
    digest with content that may not be what scored the run, and lets a file
    deleted mid-run fail an otherwise-finished comparison during receipt
    generation (Codex review, fresh evidence) -- the same single-read rule
    the policy, suppression, project-config, and required-symbol sources
    already follow.
    """
    out: set[str] = {s.strip() for s in public_symbols if s.strip()}
    if already_read is not None:
        out.update(already_read.items)
    elif symbols_list is not None:
        out.update(read_symbols_list(symbols_list))
    return out


def read_symbols_list(path: str | Path) -> tuple[str, ...]:
    """The symbols named by a ``--public-symbols-list`` file, in file order."""
    return _parse_symbols_list(Path(path).read_bytes())


def read_symbols_list_with_digest(path: str | Path) -> tuple[tuple[str, ...], str]:
    """The symbols a list file names, plus the digest of the bytes they were
    parsed from.

    One read produces both, the same rule ``PolicyFile.load``/
    ``SuppressionList.load`` follow: digesting the path in a separate read
    could pair one content's hash with another's items. The digest is over
    the raw bytes, so a line-ending-only change is drift, not a no-op.
    """
    raw = Path(path).read_bytes()
    return _parse_symbols_list(raw), hashlib.sha256(raw).hexdigest()


def _parse_symbols_list(raw: bytes) -> tuple[str, ...]:
    return tuple(
        line
        for source in raw.decode("utf-8").splitlines()
        if (line := source.strip()) and not line.startswith("#")
    )
