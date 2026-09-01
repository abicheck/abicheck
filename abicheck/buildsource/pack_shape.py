# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

""":func:`is_pack_dir` — "is this a classic ``BuildSourcePack`` directory".

ADR-061 Phase 3 gave this predicate a home of its own. It has zero
first-party dependencies but lived in ``inline.py`` (a WARN-oversized
module), so every engine-side consumer had to import ``inline`` — and its
whole dependency stack — for a filesystem check.

**The Flow-2 sibling deliberately lives elsewhere**, in ``inputs_pack.py``
next to :func:`~abicheck.buildsource.inputs_pack.is_inputs_pack`, and this
module must not grow a reference to it. A first attempt did put both here,
and the ``import-cycle-growth`` gate rejected it: ``inline`` imports this
module, so any edge from here to ``inputs_pack`` (which imports ``inline``)
closes ``inline -> pack_shape -> inputs_pack -> inline``. A function-local
import does not help — the gate reads the AST, not the call graph, and it is
right to: the cycle is real at runtime either way. That cycle is exactly what
the three private copies of the inputs-pack guard existed to dodge, so
merging the pair back into one module would reintroduce it.

This module therefore imports nothing first-party, at any scope, and any
layer may depend on it. :func:`~abicheck.buildsource.inputs_pack.
is_any_pack_dir` is the combined "either shape" predicate.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path


def is_pack_dir(path: Path | None) -> bool:
    """True when *path* is a real ``BuildSourcePack`` directory.

    Validates the manifest *content*, not just its presence: a raw source checkout
    or build dir that merely contains a top-level ``manifest.json`` must not be
    mistaken for a pack — ``BuildSourcePack.load`` would otherwise accept it with
    sparse defaults and silently drop the real L3-L5 evidence the caller meant to
    collect. Requires the BuildSourcePack version marker
    (``build_source_pack_version`` / legacy ``evidence_pack_version``).
    """
    if path is None or not path.is_dir():
        return False
    manifest = path / "manifest.json"
    if not manifest.is_file():
        return False
    try:
        with manifest.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError:
        return False
    except ValueError:
        # Present but unparseable: keep treating it as a (corrupt) pack so the
        # downstream load raises a loud error rather than silently collecting —
        # a corrupt `collect` output must never be ignored.
        return True
    # Valid JSON *without* the BuildSourcePack marker is a non-pack file (e.g. a
    # stray project manifest.json in a raw checkout) — collect from the tree, do
    # not mis-load it as an empty pack.
    return isinstance(data, dict) and (
        "build_source_pack_version" in data or "evidence_pack_version" in data
    )


def purge_external_outputs(pack_root: Path, manifest: object) -> bool:
    """Remove a failed external extractor's normalized outputs from the pack.

    A failed/skipped extractor must be isolated from the collected pack: its
    normalized output files (and its ``normalized/<name>/`` subtree) would
    otherwise be hashed into ``BuildSourcePack`` ``manifest.artifacts`` and the
    content hash, so an invalid output would change pack identity and publish a
    digest for evidence that was never folded (Codex P2). Raw artifacts under
    ``raw/`` are *not* removed — they are provenance-only, never hashed, and are
    what audit mode preserves for debugging. Takes *manifest* duck-typed
    (``name``/``outputs`` attributes) rather than a typed import, so this
    dependency-free leaf stays importable from any layer.

    Returns ``True`` when every declared output and the ``normalized/<name>/``
    subtree were confirmed absent afterward (already-missing counts as
    removed), ``False`` when a real removal failure occurred -- a locked file,
    a permissions error, a read-only remount. Previously swallowed either way
    with no signal at all (CodeRabbit review): a caller that only checks
    ``record.status`` had no way to learn a stale, un-purged file might still
    be sitting under *pack_root* for a later hashing pass to pick up as if it
    were valid, current-run evidence -- exactly the corruption this
    function's own first paragraph exists to prevent. Callers should record a
    ``False`` result somewhere a human/CI will see it (see
    ``cli_buildsource_helpers.py``'s three call sites); this leaf itself
    stays a pure filesystem primitive with no diagnostics/ledger shape of its
    own to append to.
    """
    name = getattr(manifest, "name", "")
    fully_removed = True
    for output in getattr(manifest, "outputs", []):
        path = pack_root / output.path
        try:
            path.unlink()
        except FileNotFoundError:
            pass  # already absent -- not a removal failure
        except OSError:
            fully_removed = False
    norm_dir = pack_root / "normalized" / name
    if norm_dir.is_dir():
        shutil.rmtree(norm_dir, ignore_errors=True)
        if norm_dir.is_dir():
            fully_removed = False
    return fully_removed
