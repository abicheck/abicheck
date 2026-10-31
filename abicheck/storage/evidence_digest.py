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

"""ADR-062 D16's `evidence` digest role, over a snapshot document.

The `publication` digest (`snapshot_encode.snapshot_content_digest`) answers
"is this the exact artifact a release recorded?", so it changes with
`created_at` and with producer list order. The `evidence` digest answers
"is this the same evidence?": the D5 form (`canonical.semantic_digest`) of
the same `snapshot_to_dict()`-shaped document, with two normalizations.

* **Volatile capture metadata is dropped** (`VOLATILE_CAPTURE_FIELDS`): when
  and where the dump ran, and the CI run that ran it. Declared identity
  (`library`, `version`, `git_commit`, `git_tag`) and producer identity
  (`fact_generations`, `dump_provenance`) stay in.
* **Identity-matched record lists are put in a stable order**
  (`UNORDERED_LISTS`): top-level functions/variables/types/enums and the
  ELF export/import tables. The sort is *stable* and keyed by the identity
  `compare` matches on, so records sharing one identity keep their producer
  order -- `AbiSnapshot.index()` is first-wins over duplicates, so their
  order can change a comparison and must stay in the digest. Lists inside a
  record (parameters, fields, bases, enum members) and `elf.needed`
  (`DT_NEEDED`) are semantically ordered and never sorted.

`EVIDENCE_FORM_VERSION` is hashed in. A future change to either list
changes every digest, so a digest stored by an older build never compares
equal to one computed under a different definition -- the safe direction
for every consumer, which only acts on equality.

Pure: imports only `canonical`, and never mutates *document*.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .canonical import semantic_digest

__all__ = [
    "EVIDENCE_FORM_VERSION",
    "UNORDERED_LISTS",
    "VOLATILE_CAPTURE_FIELDS",
    "evidence_digest_of_document",
]

#: Bumped whenever the evidence form below changes meaning.
EVIDENCE_FORM_VERSION = 1

#: Top-level document keys that record the capture, not the evidence.
VOLATILE_CAPTURE_FIELDS = frozenset(
    {"created_at", "source_mtime", "source_mtime_epoch", "source_path", "build_id"}
)


def _text(record: Any, key: str) -> str:
    """*record*'s *key*: a document mapping's entry, or a model object's
    attribute of the same name (so a caller can read identity order off the
    model without serializing it)."""
    if isinstance(record, Mapping):
        value = record.get(key)
    else:
        value = getattr(record, key, None)
    return value if isinstance(value, str) else ""


def _decl_key(record: Any) -> tuple[str, str]:
    return (_text(record, "mangled") or _text(record, "name"), _text(record, "name"))


def _name_key(record: Any) -> tuple[str, ...]:
    return (_text(record, "name"),)


def _symbol_key(record: Any) -> tuple[str, ...]:
    return (_text(record, "name"), _text(record, "version"))


def _import_key(record: Any) -> tuple[str, ...]:
    return (
        _text(record, "name"),
        _text(record, "version"),
        _text(record, "version_soname"),
    )


#: `(container key or None for the document itself, list key) -> identity`.
UNORDERED_LISTS: Mapping[tuple[str | None, str], Callable[[Any], tuple[str, ...]]] = {
    (None, "functions"): _decl_key,
    (None, "variables"): _decl_key,
    (None, "types"): _name_key,
    (None, "enums"): _name_key,
    ("elf", "symbols"): _symbol_key,
    ("elf", "imports"): _import_key,
}


def _stably_sorted(value: Any, key: Callable[[Any], tuple[str, ...]]) -> Any:
    if not isinstance(value, (list, tuple)):
        return value
    return sorted(value, key=key)  # `sorted` is stable


def evidence_digest_of_document(
    document: Mapping[str, Any], *, extensions: Mapping[str, Any] | None = None
) -> str:
    """The `evidence` digest (`sha256:<hex>`) of a `snapshot_to_dict()`-
    shaped *document*. Shallow copies only: *document* is left untouched.

    *extensions*: the ADR-062 D14 carriers the snapshot was loaded with
    (`WireExtensions.evidence_form`), hashed in when non-empty -- D14 puts
    them inside the evidence digest. Absent, the digest is unchanged."""
    form: dict[str, Any] = {
        key: value
        for key, value in document.items()
        if key not in VOLATILE_CAPTURE_FIELDS
    }
    containers: dict[str, dict[str, Any]] = {}
    for (container, name), key in UNORDERED_LISTS.items():
        if container is None:
            if name in form:
                form[name] = _stably_sorted(form[name], key)
            continue
        if not isinstance(form.get(container), Mapping):
            continue
        copy = containers.setdefault(container, dict(form[container]))
        form[container] = copy
        if name in copy:
            copy[name] = _stably_sorted(copy[name], key)
    envelope: dict[str, Any] = {
        "evidence_form_version": EVIDENCE_FORM_VERSION,
        "document": form,
    }
    if extensions:
        envelope["wire_extensions"] = extensions
    return semantic_digest(envelope)
