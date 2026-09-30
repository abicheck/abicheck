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

"""ADR-062 D9 / storage-format-v2 A1.6: variant pairing for the stored
release comparison.

Reads each stored package's ``VariantRef`` set into
:class:`~abicheck.model.variant_pairing.VariantView` values and pairs them
(``compare.variant_pairing``). Called once per release plan
(``workflows.release_request``) when **both** operands are stored packages;
the result rides on the scope plan into the ADR-065 acquisition record and
out as ``comparison_scope.variant_pairing`` -- the report section that
already owns "what was selected, what was compared, what is absent".

Report-only by design: pairing never changes which libraries are compared
(``--variant old=/new=`` still selects that), a verdict, or an exit code.
"""

from __future__ import annotations

from pathlib import Path

from ..compare.variant_pairing import pair_variant_views
from ..model.variant_pairing import VariantPairing, VariantView
from .bundle_variants_capture import BUNDLE_VARIANT_SPEC_SECTION_KIND

__all__ = ["package_variant_views", "release_variant_pairing"]


def _required_flag(root: Path, ref: object) -> bool | None:
    from ..project_snapshot_store import DirectoryObjectStore

    sections = getattr(ref, "sections", {})
    spec_ref = sections.get(BUNDLE_VARIANT_SPEC_SECTION_KIND)
    if spec_ref is None or spec_ref.kind != BUNDLE_VARIANT_SPEC_SECTION_KIND:
        return None
    payload = DirectoryObjectStore(root).get(spec_ref.digest)
    required = payload.get("required") if isinstance(payload, dict) else None
    return required if isinstance(required, bool) else None


def package_variant_views(root: Path) -> list[VariantView]:
    """Every variant a stored package at *root* carries, as a view."""
    from ..project_snapshot_store import read_manifest_summary, read_variant_ref

    views = []
    for variant_id in read_manifest_summary(root).variant_ids:
        ref = read_variant_ref(root, variant_id)
        views.append(
            VariantView(
                variant_id=ref.variant_id,
                declared=dict(ref.declared),
                captured=dict(ref.captured),
                required=_required_flag(root, ref),
            )
        )
    return views


def _compared(views: list[VariantView], selected: str | None) -> str | None:
    if selected is not None:
        return selected
    return views[0].variant_id if len(views) == 1 else None


def release_variant_pairing(
    old_dir: Path,
    new_dir: Path,
    *,
    old_variant: str | None = None,
    new_variant: str | None = None,
) -> VariantPairing | None:
    """The pairing of two stored package operands, or ``None`` when either
    side is not a stored package (a live side has no ``VariantRef`` to
    pair). A package that cannot be read raises -- the release plan already
    read both packages successfully by the time this runs."""
    from .storage import is_project_snapshot_package_dir

    if not all(
        p.is_dir() and is_project_snapshot_package_dir(p) for p in (old_dir, new_dir)
    ):
        return None
    old_views, new_views = (
        package_variant_views(old_dir),
        package_variant_views(new_dir),
    )
    return pair_variant_views(
        old_views,
        new_views,
        compared_old=_compared(old_views, old_variant),
        compared_new=_compared(new_views, new_variant),
    )
