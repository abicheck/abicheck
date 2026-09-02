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

"""File classification pipeline for compare-release input discovery.

When ``compare-release`` is given a plain directory, it needs to decide which
files inside are *ABI inputs* (ELF binaries, ABI snapshots, Perl dumps) and
which are incidental data files (SBOMs, templates, test fixtures, …).

This module implements a composable **classifier pipeline** that answers that
question cleanly without accumulating ad-hoc conditionals in ``cli.py``.

Architecture
------------
- :class:`FileClassifier` — abstract base; each subclass handles one file kind.
- :data:`_PIPELINE` — ordered list of classifiers, first non-``None`` wins.
- :func:`is_supported_compare_input` — public entry point for the pipeline.

Extending
---------
To add support for a new ABI snapshot format (e.g. libabigail JSON v2):
1. Add a fingerprint tuple to :attr:`AbiJsonClassifier.FINGERPRINTS`, *or*
2. Create a new :class:`FileClassifier` subclass and append it to
   :data:`_PIPELINE` *before* :class:`FallbackSniffClassifier`.

Zero changes to ``cli.py`` are needed for either extension path.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)


def _top_level_json_keys(text: str) -> set[str]:
    """Best-effort top-level (depth-1) object key names appearing in *text*
    -- a possibly-truncated prefix of a JSON document's UTF-8 text (the
    fingerprint probe window this feeds may cut a real document off
    mid-value, so this never requires *text* to be complete, valid JSON).

    Tracks string/escape state and object/array nesting depth precisely
    enough to distinguish a genuinely top-level key from one nested inside
    a value or array (Codex review, fresh evidence: a plain substring/regex
    search for a key name matches at ANY nesting depth -- e.g.
    ``{"schema_version": 1, "metadata": {"sections": {}}}`` has a nested
    "sections", not a top-level one, and must not be mistaken for the
    sectioned envelope shape). Truncation just stops the scan early
    (whatever top-level keys were already seen are still returned); it
    never raises.
    """
    keys: set[str] = set()
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            key_depth = depth  # depth *before* this string, i.e. its own nesting level
            str_start = i + 1
            i += 1
            while i < n:
                c = text[i]
                if c == "\\":
                    i += 2  # skip the escape sequence's second character too
                    continue
                if c == '"':
                    break
                i += 1
            else:
                break  # truncated mid-string -- nothing more to learn
            str_end = i
            i += 1  # past the closing quote
            if key_depth == 1:
                j = i
                while j < n and text[j] in " \t\r\n":
                    j += 1
                if j < n and text[j] == ":":
                    keys.add(text[str_start:str_end])
            continue
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        i += 1
    return keys


def _fingerprint_matches(
    matcher: re.Pattern[str] | Callable[[str], bool], text: str
) -> bool:
    """Run one `AbiJsonClassifier.FINGERPRINTS` entry's matcher against
    *text* -- a compiled regex is `.search()`ed, a plain predicate is
    called directly. The one place every fingerprint consumer dispatches
    through, so the registry's two matcher shapes can't independently
    drift between call sites."""
    if isinstance(matcher, re.Pattern):
        return matcher.search(text) is not None
    return matcher(text)


def _is_sectioned_document_fingerprint(head: str) -> bool:
    """Whether *head* (a JSON document's probe-window prefix) carries both
    of `storage.sectioned_document`'s own top-level markers -- "schema_version"
    and "sections" -- as genuinely TOP-LEVEL keys, not merely present
    somewhere at any nesting depth (see `_top_level_json_keys`'s own
    docstring for why that distinction matters)."""
    top_level = _top_level_json_keys(head)
    return "schema_version" in top_level and "sections" in top_level


# ---------------------------------------------------------------------------
# Helpers shared with cli.py
# ---------------------------------------------------------------------------


def _detect_binary_format(path: Path) -> str | None:
    """Delegate to the existing binary-format detector (ELF/PE/Mach-O)."""
    from .binary_utils import detect_binary_format

    return detect_binary_format(path)


def _looks_like_perl_dump(head: str) -> bool:
    """Return True if the text looks like an ABICC Perl dump."""
    from .compat.abicc_dump_import import looks_like_perl_dump

    return looks_like_perl_dump(head)


_SNIFF_BYTES = 256  # same constant as cli.py


def _sniff_head(path: Path) -> str:
    """Read a small prefix and return it decoded (lstrip'd), or '' on error."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(_SNIFF_BYTES)
        return raw.decode("utf-8", errors="replace").lstrip()
    except OSError as exc:
        logger.warning("classify: cannot read %s: %s", path, exc)
        return ""


# ---------------------------------------------------------------------------
# Base classifier
# ---------------------------------------------------------------------------


class FileClassifier(ABC):
    """Single-responsibility file classifier.

    Returns:
        ``True``  — file is accepted as a compare-release input.
        ``False`` — file is explicitly rejected (skip remaining classifiers).
        ``None``  — classifier does not apply; pass to the next one.
    """

    @abstractmethod
    def accepts(self, path: Path) -> bool | None: ...


# ---------------------------------------------------------------------------
# Concrete classifiers
# ---------------------------------------------------------------------------


class BinaryExtensionClassifier(FileClassifier):
    """Fast accept based on known binary file extensions.

    Uses a regex for ``.so`` to enforce an extension boundary and avoid
    false positives from substrings like ``some``, ``solution``, ``resolve``.
    """

    _SO_RE: re.Pattern[str] = re.compile(r"\.so(?:\.|$)")
    _BINARY_EXTS: frozenset[str] = frozenset({".dll", ".dylib", ".pyd"})

    def accepts(self, path: Path) -> bool | None:
        lower = path.name.lower()
        if self._SO_RE.search(lower):
            return True
        if any(lower.endswith(ext) for ext in self._BINARY_EXTS):
            return True
        return None


class MagicByteClassifier(FileClassifier):
    """Accept files whose magic bytes identify them as ELF / PE / Mach-O binaries.

    This catches binaries with unusual or missing extensions (e.g. ``.node``,
    no extension) that the extension classifier would miss.
    """

    def accepts(self, path: Path) -> bool | None:
        fmt = _detect_binary_format(path)
        return True if fmt is not None else None


class AbiJsonClassifier(FileClassifier):
    """Accept ``.json`` files that contain a recognised ABI snapshot fingerprint.

    **Fingerprint registry** — add new ABI formats here, not in ``cli.py``::

        FINGERPRINTS = [
            ("abicheck/abicc-v1", re.compile(r'(^|[,{])\\s*"library"\\s*:', re.MULTILINE)),
            # ("libabigail-json-v2", re.compile(r'"abi-corpus"')),
        ]

    Detection reads only the first :data:`_JSON_PROBE_BYTES` bytes of the file,
    which is sufficient for all known ABI snapshot formats (top-level keys
    appear within the first 4 KiB in every generator we support).
    If a future generator emits a large preamble, increase this constant.

    Note: current fingerprint checks for a ``"library":`` key. This is a
    practical heuristic for known abicheck/abicc snapshots, but arbitrary JSON
    files that happen to include the same key could be false positives. If that
    appears in the wild, add stricter co-occurrence fingerprints (e.g.
    ``"functions":`` plus schema version marker).
    """

    _JSON_PROBE_BYTES: int = 4096

    # Registry: (label, matcher). A matcher is either a compiled regex
    # (searched in the first _JSON_PROBE_BYTES bytes, decoded UTF-8) or a
    # plain `Callable[[str], bool]` predicate over that same text -- for a
    # fingerprint that needs more than a regex can express (e.g. genuinely
    # top-level JSON key detection, which needs depth tracking, not just
    # pattern matching; see `_is_sectioned_document_fingerprint`).
    FINGERPRINTS: list[tuple[str, re.Pattern[str] | Callable[[str], bool]]] = [
        (
            "abicheck/abicc-v1",
            # Matches "library": as a JSON key (preceded by start-of-string,
            # comma, or opening brace) — NOT "library" as a value like
            # {"type": "library"} which appears in CycloneDX SBOMs.
            re.compile(r'(^|[,{])\s*"library"\s*:', re.MULTILINE),
        ),
        (
            # ADR-063 Phase 8: the single-file sectioned document
            # (storage.sectioned_document) buries "library" inside the
            # "declarations" section's payload, well past _JSON_PROBE_BYTES
            # for a real snapshot -- so it needs its own fingerprint.
            # A top-level "sections" key alone (an earlier version of this
            # fingerprint) is still too generic -- an unrelated document
            # (OpenAPI, a docs config, ...) can carry its own top-level
            # "sections" object (Codex/CodeRabbit review, fresh evidence,
            # second round; the round before that found the same problem
            # with the nested per-SectionDTO "section_kind" key). A THIRD
            # round then found that requiring the exact adjacency
            # `to_sectioned_document()` happens to write ("schema_version"
            # immediately followed by "sections") breaks the moment a
            # document is reserialized with reordered keys -- e.g.
            # `json.dumps(..., sort_keys=True)`, or any JSON formatter.
            # A FOURTH round then found that requiring the two keys
            # independently, via a plain substring/regex search, matches
            # them at ANY nesting depth -- e.g.
            # `{"schema_version": 1, "metadata": {"sections": {}}}` has a
            # nested "sections", not a top-level one, but still matched.
            # `_is_sectioned_document_fingerprint` closes that: it tracks
            # JSON nesting depth (via `_top_level_json_keys`) so only a
            # genuinely top-level "schema_version" and "sections" count,
            # while staying tolerant of reordering and truncation the same
            # way the round-3 fix was. Deliberately does NOT also require
            # "section_schema_versions": unlike the other two keys,
            # `to_sectioned_document()` writes it dict-order *after* the
            # (size-variable) "sections" payload, so on a real,
            # content-heavy snapshot it can itself fall past the probe
            # window -- requiring it would reintroduce the same kind of
            # false negative the round-3 fix closed.
            "abicheck/sectioned-v1",
            _is_sectioned_document_fingerprint,
        ),
        # Future formats — uncomment or add new entries here:
        # ("libabigail-json-v2", re.compile(r'"abi-corpus"\s*:', re.MULTILINE)),
    ]

    def accepts(self, path: Path) -> bool | None:
        if path.suffix.lower() != ".json":
            return None
        # ADR-059 (Codex review): `dump --compression gzip|zstd -o out.json`
        # explicitly permits writing compressed bytes under a neutral
        # `.json` name -- resolve_write_compression() only rejects a
        # *canonical* suffix (.json.gz/.json.zst) that contradicts an
        # explicit --compression, not a plain .json one. Reading those raw
        # compressed bytes as text here would never match the fingerprint
        # regex, returning False (not None) and ending the "first
        # non-None wins" pipeline before CompressedAbiJsonClassifier ever
        # gets a chance to decode and inspect it -- so abstain on a
        # recognizably compressed file and let that classifier handle it.
        try:
            from .snapshot_io import SnapshotCompression, detect_snapshot_compression

            if detect_snapshot_compression(path) is not SnapshotCompression.NONE:
                return None
        except Exception:
            pass  # fall through to the plain-text read below
        try:
            with open(path, "rb") as fh:
                head = fh.read(self._JSON_PROBE_BYTES).decode("utf-8", errors="replace")
        except OSError as exc:
            logger.warning("classify: cannot read JSON candidate %s: %s", path, exc)
            return False
        return any(_fingerprint_matches(pat, head) for _, pat in self.FINGERPRINTS)


class CompressedAbiJsonClassifier(FileClassifier):
    """Accept gzip/zstd-compressed ABI snapshots (ADR-059).

    Mirrors :class:`AbiJsonClassifier`'s fingerprint check, but reads a
    bounded *decoded* prefix (:func:`abicheck.snapshot_io.
    bounded_decoded_prefix`) instead of the raw file bytes, so a directory
    of ``*.abicheck.json.gz``/``*.abicheck.json.zst`` release assets is
    discovered the same way a plain ``.json`` one is — never a full
    decompression just to classify the file, and never misrouting an
    unrelated compressed archive (a baseline-set ``.tar.zst``, which does
    not decode to a leading ``{`` fingerprint match).
    """

    def accepts(self, path: Path) -> bool | None:
        from .snapshot_io import (
            SnapshotCompression,
            bounded_decoded_prefix,
            detect_snapshot_compression,
        )

        try:
            compression = detect_snapshot_compression(path)
        except Exception:
            return None
        if compression is SnapshotCompression.NONE:
            return None  # not a compressed file at all; let other classifiers try
        prefix = bounded_decoded_prefix(path, AbiJsonClassifier._JSON_PROBE_BYTES)
        if prefix is None:
            return (
                False  # recognizably compressed but undecodable -- reject, don't guess
            )
        head = prefix.decode("utf-8", errors="replace")
        return any(
            _fingerprint_matches(pat, head) for _, pat in AbiJsonClassifier.FINGERPRINTS
        )


class PerlDumpClassifier(FileClassifier):
    """Accept ``.pl`` / ``.pm`` files that look like ABICC Perl dumps."""

    _PERL_EXTS: frozenset[str] = frozenset({".pl", ".pm"})

    def accepts(self, path: Path) -> bool | None:
        if path.suffix.lower() not in self._PERL_EXTS:
            return None
        head = _sniff_head(path)
        return bool(_looks_like_perl_dump(head))


class FallbackSniffClassifier(FileClassifier):
    """Last-resort: files with arbitrary extensions that *sniff* as JSON/Perl.

    Applies the same ABI-marker validation as :class:`AbiJsonClassifier` /
    :class:`PerlDumpClassifier` so that incidental JSON-like files (Jinja
    templates starting with ``{%``, etc.) are still rejected.
    """

    def accepts(self, path: Path) -> bool | None:
        head = _sniff_head(path)
        if _looks_like_perl_dump(head):
            return True
        if head.startswith("{"):
            # Looks like JSON on a non-.json extension — apply ABI marker check.
            try:
                with open(path, "rb") as fh:
                    probe = fh.read(AbiJsonClassifier._JSON_PROBE_BYTES).decode(
                        "utf-8", errors="replace"
                    )
            except OSError as exc:
                logger.warning(
                    "classify: cannot read fallback JSON candidate %s: %s", path, exc
                )
                return False
            return any(
                _fingerprint_matches(pat, probe)
                for _, pat in AbiJsonClassifier.FINGERPRINTS
            )
        return False


# ---------------------------------------------------------------------------
# Pipeline (ordered — first non-None result wins)
# ---------------------------------------------------------------------------

_PIPELINE: list[FileClassifier] = [
    BinaryExtensionClassifier(),
    MagicByteClassifier(),
    AbiJsonClassifier(),
    CompressedAbiJsonClassifier(),
    PerlDumpClassifier(),
    FallbackSniffClassifier(),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_supported_compare_input(path: Path) -> bool:
    """Return ``True`` if *path* is a valid compare-release ABI input.

    Runs each classifier in :data:`_PIPELINE` in order; returns on the first
    non-``None`` result.  Returns ``False`` if all classifiers abstain.

    Args:
        path: Absolute or relative path to the candidate file.

    Returns:
        ``True`` if the file should be included in a compare-release run,
        ``False`` otherwise.
    """
    if not path.is_file():
        return False
    for classifier in _PIPELINE:
        result = classifier.accepts(path)
        if result is not None:
            return result
    return False
