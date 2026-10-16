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


"""The manifest-driven dump's structural identity: each translation unit's scope
fields (ADR-050 D1) encoded as one JSON string. The whole-snapshot cache key
folds it in (a content walk cannot see a TU rename or a flipped flag), and the
extraction contract's scope fingerprint uses it too, via
:mod:`abicheck.comparability`'s re-export."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..comparability_fields import _resolved


def manifest_tu_scope_field(dump_manifest: Any) -> str:
    """JSON-serializable, order-preserving encoding of every ``--dump-manifest``
    translation unit's own scope-affecting fields (ADR-050 D1: name, ordered
    ``forced_includes``/``includes`` including ``project_owned``, ``required``,
    ``contributes_to_abi``) -- fed into :func:`compute_extraction_contract`'s
    ``scope_fingerprint`` as its ``"translation_units"`` field whenever a
    manifest-driven dump is being fingerprinted.

    Without this, two manifests declaring the identical ``roots`` (the only
    scope input ``compute_extraction_contract`` otherwise sees for a
    manifest-driven dump) but different TU structure -- a TU renamed, its
    ``includes``/``forced_includes`` reordered, or a ``required``/
    ``contributes_to_abi``/``project_owned`` flag flipped -- would silently
    fingerprint identically: exactly the class of manifest/extraction-contract
    drift ADR-050 exists to catch (D1's own text: "flipping ``contributes_to_abi``
    changes which declarations feed the ABI model... without necessarily
    changing that TU's includes at all").

    Every path is normalized relative to *dump_manifest*'s own ``base_dir``
    (the manifest file's own directory) -- ADR-050's "for the manifest path,
    both fingerprints' roots are simply the manifest file's own directory"
    rule, mirroring ``dumper_contract._manifest_declared_includes``'s
    identical ``os.path.relpath``/``ValueError`` handling (falls back to the
    resolved absolute path only for a genuinely cross-drive Windows path) so
    two checkouts of the same manifest at different mount points still
    normalize identically.

    Computable from the manifest document alone, no compiler invocation --
    genuinely available pre-dump, which is what lets
    :mod:`abicheck.workflows.dump.cache` (G32 Phase E) fold it into the
    whole-snapshot cache key *before* running a live dump.

    Typed ``Any`` rather than ``dump_manifest.DumpManifest`` to avoid a
    module-level import of :mod:`abicheck.dump_manifest` here purely for a
    type hint (this module has no other reason to depend on it); only
    ``.base_dir``/``.translation_units`` are read, structurally.
    """
    # The literal, UN-resolved base_dir string -- dump_manifest.py's own
    # _resolve_path() builds every relative-in-YAML path as exactly
    # `base_dir / raw`, so its str() always carries this exact prefix
    # before any ".." components get collapsed by .resolve() below. This is
    # what lets _rel tell "a relative-declared sibling path (../src)" apart
    # from "a genuinely external absolute path (/usr/include)" -- checking
    # AFTER resolving can't: .resolve() collapses `manifest_dir/../src`
    # down to the same shape as an unrelated absolute path, discarding the
    # one signal that distinguishes them (Codex review, PR #636).
    _base_dir_str = str(dump_manifest.base_dir)
    # A base_dir that already ends in a separator (a filesystem root "/" or
    # a Windows drive root "C:\") must not gain a second one here -- "//" or
    # "C:\\" would never prefix-match any real child path string, so every
    # path under such a root-level checkout would be misclassified as
    # "external" and fingerprinted as an absolute, checkout-depth-dependent
    # path instead of being properly relativized (CodeRabbit review).
    _base_dir_prefix = (
        _base_dir_str if _base_dir_str.endswith(os.sep) else _base_dir_str + os.sep
    )

    def _rel(p: Path) -> str:
        p_str = str(p)
        if p_str != _base_dir_str and not p_str.startswith(_base_dir_prefix):
            # A genuinely external path (declared absolute in the manifest,
            # e.g. `/usr/include`, and not already under this checkout) has
            # no structural relationship to base_dir at all -- relativizing
            # it would climb a `../` distance that depends on how deeply
            # THIS checkout happens to be nested, not on anything about the
            # external path itself, so two otherwise-identical manifests at
            # different checkout depths would spuriously fingerprint
            # differently. Keep it as the resolved absolute path instead,
            # which is already checkout-depth-independent by construction.
            return str(_resolved(p))
        # Lexical normalization only (os.path.normpath), never real
        # filesystem resolution: a relative-declared path whose lexical
        # structure crosses a symlink (e.g. a checkout-local `vendor ->
        # /opt/sdk`, or that symlink's real target being relocated/
        # versioned per checkout) must not have the symlink followed
        # before computing the relative form -- two checkouts declaring
        # the identical `vendor/api.h` must fingerprint identically
        # regardless of what `vendor` actually resolves to on either
        # host. `.resolve()` follows symlinks as well as collapsing
        # `..`/`.`; `os.path.normpath` collapses `..`/`.` purely
        # lexically, with no filesystem access at all (Codex review).
        try:
            return os.path.relpath(
                os.path.normpath(p_str), os.path.normpath(_base_dir_str)
            )
        except ValueError:
            return str(_resolved(p))

    tus = [
        {
            "name": tu.name,
            "forced_includes": [_rel(p) for p in tu.forced_includes],
            "includes": [
                {"path": _rel(inc.path), "project_owned": inc.project_owned}
                for inc in tu.includes
            ],
            "required": tu.required,
            "contributes_to_abi": tu.contributes_to_abi,
        }
        for tu in dump_manifest.translation_units
    ]
    # Sorted by name -- the manifest declares a SET of translation units,
    # identified by their (parse-time-enforced-unique) name, not by list
    # position (ADR-050 D1: "the set of translation units (by name, not by
    # list position)... reordering two independent TU entries... must not
    # change the fingerprint"). Each TU's own internal forced_includes/
    # includes order is preserved above and stays order-sensitive -- only
    # the outer TU-to-TU ordering is canonicalized (Codex review, PR #636).
    tus.sort(key=lambda t: t["name"])
    return json.dumps(tus)
