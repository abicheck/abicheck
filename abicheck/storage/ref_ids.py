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

"""Cross-platform ref-id path safety — split out of `package.py` (that
module's own 800-line production cap) purely to make room: this is
self-contained filename-safety validation with no dependency on the
`PackageManifest`/`VariantRef`/`ArtifactRef` object model itself, so it
moves cleanly to its own leaf.

`safe_ref_id`/`reject_filesystem_collisions` are `package.py`'s own
`_safe_ref_id`/`_reject_filesystem_collisions` under their original names,
unchanged in behavior — see the module map's `package.py` entry for what
they guard against (a variant/artifact id becoming a literal, cross-platform
filename component).

`resolve_ref_ids` (ADR-063 Track C 8B) is new: `import_bundle_facts`/
`import_baseline_set` key a real document by an arbitrary caller-chosen
string (a library name) they don't get to pick a safer `artifact_id` for,
unlike `import_v1.import_legacy_snapshot`'s own caller-supplied
`artifact_id` -- nothing upstream of those two adapters has already
ensured the key is ref-id-safe."""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Sequence

from .guards import identity_text as _identity_text

__all__ = [
    "REF_SUFFIX",
    "reject_filesystem_collisions",
    "resolve_ref_ids",
    "safe_ref_id",
]

#: Windows' reserved device names -- forbidden as a path component's stem
#: regardless of case or of what follows (`CON`, `con.json`, and `Con` are
#: all refused the same way a real Windows filesystem refuses them), so a
#: writer fanning this manifest out to `refs/variants/<id>.json` never hits
#: a name the target filesystem cannot create.
#: Superscript digits 0-9, index-aligned (`_SUPERSCRIPT_DIGITS[1]` is `"¹"`).
#: Windows treats `COM¹`/`LPT¹` and friends as reserved device names too --
#: a real, documented bypass of the plain-ASCII-digit restriction that a
#: Windows filesystem update closed by rejecting these spellings as well.
_SUPERSCRIPT_DIGITS = "⁰¹²³⁴⁵⁶⁷⁸⁹"

_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
    | {f"COM{_SUPERSCRIPT_DIGITS[i]}" for i in range(1, 4)}
    | {f"LPT{_SUPERSCRIPT_DIGITS[i]}" for i in range(1, 4)}
)

#: Characters no Windows filesystem accepts in a path component, beyond the
#: separators `safe_ref_id` already rejects on every platform.
_WINDOWS_FORBIDDEN_CHARS = frozenset('<>:"|?*')

#: The literal suffix `package.variant_ref_relpath`/`artifact_ref_relpath`
#: append to a ref id to form its ref document's filename.
REF_SUFFIX = ".json"

#: The common filesystem path-component limit (ext4, NTFS, APFS, ...) an id
#: plus `REF_SUFFIX` must fit under. Measured in encoded bytes, not
#: characters: POSIX filesystems count bytes, and the UTF-8 encoding of a
#: non-ASCII id can be several bytes per character, so a character count
#: alone would accept an id whose actual on-disk name is longer than this
#: limit.
_MAX_REF_COMPONENT_BYTES = 255


def safe_ref_id(value: str, field_name: str) -> str:
    """A ref id, made safe to use as a bare, cross-platform filename component.

    A variant or artifact id becomes a literal path segment
    (`refs/variants/<variant-id>.json`), so a value containing a path
    separator or a `..` component could let a written package escape its own
    directory. Checked once here so every current and future path helper
    inherits the rule rather than each re-deriving it.

    The check is Windows-shaped, not just POSIX-shaped, even though nothing
    here runs on Windows yet: a package is meant to be written on one
    platform and read on another, so an id only POSIX would accept (a
    Windows reserved device name, a trailing dot or space, `:`/`*`/`?`/...)
    would make a manifest that validates here fail once a real writer tries
    to place it on a different filesystem. Refusing it at the one place
    every id passes through means a future writer never has to guess which
    ids are actually portable.
    """
    _identity_text(value, field_name)
    if (
        not value
        or "/" in value
        or "\\" in value
        or value in (".", "..")
        or any(ord(char) < 0x20 for char in value)
        or any(char in _WINDOWS_FORBIDDEN_CHARS for char in value)
        or value[-1] in (".", " ")
        or value.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES
        # The id never appears on disk alone -- it is always rendered as
        # `<id>.json` -- so what must fit under the filesystem's component
        # limit is the id *plus* that suffix, not the id by itself. Checked
        # against the UTF-8 encoding, since that is what actually reaches
        # the filesystem's own byte-counted limit.
        or len(value.encode("utf-8")) + len(REF_SUFFIX) > _MAX_REF_COMPONENT_BYTES
    ):
        raise ValueError(
            f"{field_name} must be a non-empty, cross-platform-path-safe "
            f"identifier no more than "
            f"{_MAX_REF_COMPONENT_BYTES - len(REF_SUFFIX)} UTF-8 bytes long, "
            f"got {value!r}"
        )
    return value


def reject_filesystem_collisions(ids: list[str], record_kind: str) -> None:
    """Two ids a real filesystem would treat as the same path, refused early.

    Two ways two *distinct* Python strings still name one file:

    * **Case** -- `Foo`/`foo` are distinct strings but one file on a
      case-insensitive filesystem (the default on Windows and on macOS's
      usual volume format), so `variant_ref_relpath`/`artifact_ref_relpath`
      would write both as the same path, the second silently overwriting
      the first.
    * **Unicode normalization** -- `"é"` (`é`, one code point) and
      `"é"` (`e` + a combining acute accent) render identically and
      are canonically equivalent text, but compare unequal, and unequal
      under `casefold()` alone too. A normalization-insensitive filesystem
      (macOS's default APFS/HFS+ configuration) treats them as the same
      path component for exactly the reason a case-insensitive one treats
      `Foo`/`foo` as the same one.

    Folded via Unicode's own canonical-caseless-matching construction
    (`NFD(casefold(NFD(x)))`, Unicode Standard D145/D146) rather than a single
    normalize-then-casefold pass: `casefold()` is not guaranteed to preserve
    normalization, so two ids that are themselves already NFC/NFD and
    genuinely distinct can still fold to differently-normalized strings that
    a normalization-insensitive filesystem would treat as one path
    component. Renormalizing after casefolding is what closes that gap --
    either kind of collision (case, or Unicode normalization, or both at
    once) is caught here, at the one place every id is collected, rather
    than only once a real writer target reproduces it.
    """
    seen: dict[str, str] = {}
    for ref_id in ids:
        folded = unicodedata.normalize(
            "NFD", unicodedata.normalize("NFD", ref_id).casefold()
        )
        collision = seen.get(folded)
        if collision is not None and collision != ref_id:
            raise ValueError(
                f"{record_kind} {ref_id!r} and {collision!r} would collide on "
                "a case-insensitive or normalization-insensitive filesystem"
            )
        seen[folded] = ref_id


def _opaque_ref_id(name: str, prefix: str) -> str:
    """A deterministic id derived from *name* guaranteed to pass
    `safe_ref_id` and never collide (up to sha256) with another name's own
    opaque id -- lowercase hex is already `safe_ref_id`-safe and
    case/normalization-collision-free by construction."""
    digest = hashlib.sha256(name.encode("utf-8", errors="surrogatepass")).hexdigest()
    return f"{prefix}-{digest[:16]}"


def resolve_ref_ids(names: Sequence[str], *, opaque_prefix: str) -> dict[str, str]:
    """Map each of *names* to a ref-id-safe, collision-free identifier --
    preferring the literal name (for on-disk readability, e.g. in
    `refs/artifacts/<id>.json`) when every name in *names* already passes
    `safe_ref_id` and none collides with another on a case-insensitive or
    normalization-insensitive filesystem (`reject_filesystem_collisions`);
    falling back to a deterministic, opaque, sha256-derived id (see
    `_opaque_ref_id`) for *every* name otherwise.

    A single unsafe or colliding name falls the whole set back to opaque
    ids, not just the offending one(s) -- deciding which of several
    colliding names keeps its literal spelling would be an arbitrary
    choice this function has no basis to make, and a set mixing opaque and
    literal ids is more confusing to a reader of the resulting package than
    a uniformly opaque one (Codex review: a real `BundleFacts`/baseline-set
    document may legitimately name two libraries differing only by case --
    `libFoo.so`/`libfoo.so` -- which this package's own cross-platform
    ref-id safety would otherwise reject outright, even though the
    canonical `BundleFacts` reader treats them as two distinct, valid
    entries).

    Raises nothing itself -- a name this function cannot make safe always
    has a working opaque fallback.
    """
    safe_names: list[str] = []
    for name in names:
        try:
            safe_ref_id(name, "name")
        except ValueError:
            return {n: _opaque_ref_id(n, opaque_prefix) for n in names}
        safe_names.append(name)
    try:
        reject_filesystem_collisions(safe_names, "name")
    except ValueError:
        return {n: _opaque_ref_id(n, opaque_prefix) for n in names}
    return {n: n for n in names}
