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

import yaml

_MERGE_TAG = "tag:yaml.org,2002:merge"


def _raw_scalar_for_key(node: yaml.MappingNode, key: str) -> str | None:
    """Raw scalar text of *key* in mapping *node*, resolving YAML merge
    keys (``<<: *anchor`` / ``<<: [*a, *b]``) the way PyYAML itself does:
    a direct (non-merge) key always wins over a merged one, among multiple
    merge sources the first-listed wins for a duplicate key, and among
    multiple *direct* occurrences of the same key the LAST one wins --
    mirroring ``yaml.safe_load()``'s own last-key-wins behavior for a
    mapping with a duplicate key (Codex review, fresh evidence: an
    earlier revision returned on the first direct match, so a duplicate
    ``finding_id:`` entry resolved to a different value here than the
    already-loaded, safe_load-produced mapping this result gets merged
    into -- silently targeting the wrong finding).

    (Also handles ``defaults: &d {finding_id: ...}`` followed by
    ``- <<: *d``, which bypasses a plain direct key/value scan entirely
    since the merge key's own value is a mapping *node reference*, not a
    ``finding_id`` pair in *this* mapping's own ``.value`` list.)
    """
    merged: str | None = None
    direct: str | None = None
    for k, v in node.value:
        if isinstance(k, yaml.ScalarNode) and k.tag == _MERGE_TAG:
            sources = v.value if isinstance(v, yaml.SequenceNode) else [v]
            for source in sources:
                if isinstance(source, yaml.MappingNode) and merged is None:
                    merged = _raw_scalar_for_key(source, key)
        elif (
            isinstance(k, yaml.ScalarNode)
            and k.value == key
            and isinstance(v, yaml.ScalarNode)
        ):
            direct = str(v.value)  # Keep scanning -- a later dup key wins.
    return direct if direct is not None else merged


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
    seq = next(
        (
            v
            for k, v in doc.value
            if isinstance(k, yaml.ScalarNode) and k.value == "suppressions"
        ),
        None,
    )
    if not isinstance(seq, yaml.SequenceNode):
        return {}
    raw_ids: dict[int, str] = {}
    for index, item_node in enumerate(seq.value):
        if not isinstance(item_node, yaml.MappingNode):
            continue
        raw = _raw_scalar_for_key(item_node, "finding_id")
        if raw is not None:
            raw_ids[index] = raw
    return raw_ids


def parse_finding_id(raw: object) -> str | None:
    """Coerce a ``finding_id`` value to ``str``, normalizing an empty
    string to ``None``.

    An explicit ``finding_id: ""`` (or a blank ``finding_id:`` under a
    tag other than YAML's null resolver) would otherwise pass
    ``Suppression.__post_init__``'s ``is not None`` selector check as a
    real, standalone-sufficient selector that can never match any real
    finding -- a rule that loads successfully but is permanently dead
    (Codex review, fresh evidence). ``SuppressionList.load`` passes the
    raw scalar text via :func:`raw_finding_ids_by_index`; this also
    matters for a caller building ``Suppression`` directly with a
    non-``str`` value.
    """
    if raw is None:
        return None
    return str(raw) or None
