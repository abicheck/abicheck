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

"""Raw-scalar YAML helpers for ``suppression.py``, split out to stay under
the file-size cap (CLAUDE.md). No dependency on ``suppression.py`` itself,
so the import direction stays one-way.
"""

from __future__ import annotations

import re

import yaml

_MERGE_TAG = "tag:yaml.org,2002:merge"
_NULL_TAG = "tag:yaml.org,2002:null"


def _raw_scalar_lookup(
    node: yaml.MappingNode, key: str, _visiting: frozenset[int] = frozenset()
) -> tuple[bool, str | None]:
    """``(found, raw_text)`` for *key* in mapping *node*, resolving YAML
    merge keys (``<<: *anchor`` / ``<<: [*a, *b]``) the way PyYAML itself
    does: a direct (non-merge) key always wins over a merged one (null
    included), among multiple merge sources the first-listed wins for a
    duplicate key, among multiple *direct* occurrences of the same key
    the LAST one wins, and a later separate ``<<:`` occurrence overwrites
    an earlier one for a key both define -- all mirroring
    ``yaml.safe_load()``'s own dict-construction semantics (Codex review,
    fresh evidence, several rounds: returning on the first direct match,
    or treating only the first ``<<:``, both disagreed with the already-
    loaded, safe_load-produced mapping this result gets merged into).

    ``found`` is the reason this returns a 2-tuple rather than just
    ``str | None``: a scalar resolving to YAML's null tag (``finding_id:
    null``/``~``/a bare ``finding_id:``) is ``(True, None)``, distinct
    from the key being genuinely absent (``(False, None)``). Collapsing
    both to a bare ``None`` return breaks exactly one case: a LATER merge
    source explicitly nulling out a value an EARLIER source or merge
    provided (``<<: *has_value`` then ``<<: *explicitly_null`` -- real
    ``yaml.safe_load`` resolves this to ``None``, clearing the earlier
    value) previously left the earlier, now-stale value in place, since
    "this source's resolution is None" was indistinguishable from "this
    source doesn't define the key at all, move on" (Codex review, fresh
    evidence).

    (Also handles ``defaults: &d {finding_id: ...}`` followed by
    ``- <<: *d``, which bypasses a plain direct key/value scan entirely
    since the merge key's own value is a mapping *node reference*, not a
    ``finding_id`` pair in *this* mapping's own ``.value`` list.)

    ``_visiting`` guards a self-referential merge (``defaults: &d {<<:
    *d, finding_id: ...}``) -- a legal YAML anchor graph
    ``yaml.safe_load()`` itself resolves without incident (its own merge
    constructor works off an already-partial dict, so merging a node into
    itself is a no-op), but this function walks the raw, uncollapsed Node
    tree, where the same construct is a real reference cycle: without a
    visited-set, re-entering the same ``MappingNode`` recurses forever
    (Codex review, fresh evidence: confirmed ``RecursionError`` on a real
    self-referential anchor). Re-visiting an in-progress node returns
    "not found" for that path -- matching what a partially-constructed
    dict would contribute at that same point during real construction.
    """
    if id(node) in _visiting:
        return False, None
    visiting = _visiting | {id(node)}
    merged_found = False
    merged_value: str | None = None
    direct_found = False
    direct_value: str | None = None
    for k, v in node.value:
        if isinstance(k, yaml.ScalarNode) and k.tag == _MERGE_TAG:
            sources = v.value if isinstance(v, yaml.SequenceNode) else [v]
            for source in sources:
                # First source within THIS occurrence's own sequence wins
                # (YAML merge-sequence spec) -- but a LATER `<<:` occurrence
                # overwrites an earlier one's result, so this inner loop
                # still stops at the first hit while the outer loop keeps
                # scanning across occurrences.
                if isinstance(source, yaml.MappingNode):
                    found, value = _raw_scalar_lookup(source, key, visiting)
                    if found:
                        merged_found = True
                        merged_value = value
                        break
        elif (
            isinstance(k, yaml.ScalarNode)
            and k.value == key
            and isinstance(v, yaml.ScalarNode)
        ):
            # Keep scanning -- a later duplicate key wins.
            direct_found = True
            direct_value = None if v.tag == _NULL_TAG else str(v.value)
    if direct_found:
        return True, direct_value
    return merged_found, merged_value


def raw_finding_ids_by_index(text: str) -> dict[int, str]:
    """``suppressions[]`` index -> raw, unresolved ``finding_id`` scalar
    text, if present (merge keys resolved -- see :func:`_raw_scalar_for_key`).

    Codex review, PR #753 round 2: an unquoted all-octal-digit leading-zero
    scalar (``0123456701234567``) resolves to a *different* int
    (``5744368105847``) under PyYAML's YAML 1.1 resolver -- unrecoverable
    once ``yaml.safe_load`` has already thrown the original digits away, so
    coercing the parsed value with ``str()`` isn't enough. A loader-wide
    int-resolution ban is unsound the other way (this file's own
    ``version: 1`` needs a real int, and resolution has no per-key
    context) -- ``yaml.compose()``'s raw Node tree does: a
    ``ScalarNode.value`` is always the literal written text regardless of
    its resolved tag.
    """
    try:
        doc = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        return {}
    if not isinstance(doc, yaml.MappingNode):
        return {}
    # Last occurrence wins for a duplicate top-level key too -- mirrors
    # yaml.safe_load's own dict construction (Codex review, fresh
    # evidence: an earlier revision picked the FIRST `suppressions:` via
    # next(...), so a document with a duplicate top-level key could raw-
    # extract from a different sequence than the one safe_load actually
    # used, mismatching indices against the effective mapping).
    seq: object = None
    for k, v in doc.value:
        if isinstance(k, yaml.ScalarNode) and k.value == "suppressions":
            seq = v
    if not isinstance(seq, yaml.SequenceNode):
        return {}
    raw_ids: dict[int, str] = {}
    for index, item_node in enumerate(seq.value):
        if not isinstance(item_node, yaml.MappingNode):
            continue
        _found, raw = _raw_scalar_lookup(item_node, "finding_id")
        if raw is not None:
            raw_ids[index] = raw
    return raw_ids


#: report_canonical_finding_id() is always exactly 16 lowercase hex chars
#: (a truncated sha256 hexdigest) -- anything else can never match, so
#: parse_finding_id rejects it outright rather than silently accepting a
#: rule that will never fire (Codex review, fresh evidence).
_FINDING_ID_RE = re.compile(r"[0-9a-f]{16}")


def parse_finding_id(raw: object) -> str | None:
    """Coerce a ``finding_id`` value to ``str``, normalizing an empty
    string to ``None`` and validating the non-empty shape.

    An explicit ``finding_id: ""`` (or a blank ``finding_id:`` under a
    tag other than YAML's null resolver) would otherwise pass
    ``Suppression.__post_init__``'s ``is not None`` selector check as a
    real, standalone-sufficient selector that can never match any real
    finding -- a rule that loads successfully but is permanently dead.
    A non-empty value that isn't a real digest shape (a typo, a copied
    ``finding_id`` from the wrong field) is the identical failure mode,
    caught here with a raised ``ValueError`` instead of silently
    accepted. Both ``SuppressionList.load`` (via
    :func:`raw_finding_ids_by_index`) and ``Suppression.__post_init__``
    (for direct, non-YAML construction) call this, so neither path can
    bypass it.
    """
    if raw is None:
        return None
    value = str(raw) or None
    if value is not None and not _FINDING_ID_RE.fullmatch(value):
        raise ValueError(
            f"finding_id {value!r} is not a valid canonical_finding_id "
            "(expected 16 lowercase hex characters, e.g. copied from a "
            "report's canonical_finding_id field)"
        )
    return value
