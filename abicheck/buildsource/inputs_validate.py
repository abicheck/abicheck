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

"""Pre-merge validation for a Flow-2 ``abicheck_inputs/`` pack (ADR-038 C.8, #28).

A build-emitted pack (the ``abicheck-cc`` wrapper, the Clang facts plugin, or a
hand-written producer) can silently look complete while its evidence is
partial, duplicated, or produced under an incompatible fact-set version. This
module runs the checks *before* an authoritative merge/dump so those problems
are caught at pack-production time (e.g. in the build job that drops the pack)
rather than surfacing as a confusing missing-finding in a much later compare.

Pure: reads the pack's files (the same ``ingest_inputs_pack``/
``read_source_facts`` machinery used to fold the pack), never runs a tool.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .fact_set import incomplete_families, rollup_coverage, rollup_fact_set
from .inputs_pack import (
    INPUTS_KIND,
    InputsManifest,
    is_inputs_pack,
    load_inputs_manifest,
    read_source_facts,
)
from .source_abi import SOURCE_ABI_FACT_SET_VERSION, SourceAbiTu
from .source_link import link_source_abi


@dataclass
class InputsValidationReport:
    """Result of validating one ``abicheck_inputs/`` pack."""

    root: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tu_count: int = 0
    duplicate_tu_ids: list[str] = field(default_factory=list)
    incomplete_families: list[str] = field(default_factory=list)
    fact_set: dict[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, object]:
        return {
            "root": self.root,
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "tu_count": self.tu_count,
            "duplicate_tu_ids": list(self.duplicate_tu_ids),
            "incomplete_families": list(self.incomplete_families),
            "fact_set": dict(self.fact_set),
        }


def _duplicate_tu_ids(tus: list[SourceAbiTu]) -> list[str]:
    seen: set[str] = set()
    dupes: set[str] = set()
    for tu in tus:
        if not tu.tu_id:
            continue
        if tu.tu_id in seen:
            dupes.add(tu.tu_id)
        seen.add(tu.tu_id)
    return sorted(dupes)


def validate_inputs_pack(root: Path | str) -> InputsValidationReport:
    """Validate one ``abicheck_inputs/`` pack directory; never raises for a
    structurally-readable pack — problems are reported, not thrown. Raises
    ``FileNotFoundError``/``ValueError`` only when *root* is not a readable
    Flow-2 pack at all (no manifest, or a manifest declaring a different
    ``kind``), matching :func:`~.inputs_pack.load_inputs_manifest`.
    """
    root = Path(root)
    report = InputsValidationReport(root=str(root))

    if not is_inputs_pack(root):
        # Raise the same way load_inputs_manifest would, rather than a soft
        # error, so a caller pointed at the wrong directory gets a hard usage
        # failure (ADR-038 #28's "manifest validity" check) instead of a
        # misleadingly "clean" empty report.
        manifest_path = root / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(
                f"No abicheck_inputs manifest at {manifest_path}. Expected a "
                f"Flow-2 pack with a manifest.json declaring kind: {INPUTS_KIND}."
            )
        raise ValueError(
            f"{manifest_path} does not declare kind: {INPUTS_KIND} — not a "
            "Flow-2 abicheck_inputs pack."
        )

    manifest: InputsManifest = load_inputs_manifest(root)
    diagnostics: list[str] = []
    tus = read_source_facts(root, manifest, diagnostics=diagnostics)
    report.tu_count = len(tus)
    report.warnings.extend(f"source_facts: {d}" for d in diagnostics)

    if report.tu_count == 0:
        report.warnings.append(
            "pack contains zero readable TU records (source_facts/*.jsonl is "
            "empty or unreadable) — no L4 evidence will be folded from it."
        )

    dupes = _duplicate_tu_ids(tus)
    report.duplicate_tu_ids = dupes
    if dupes:
        report.errors.append(
            f"{len(dupes)} duplicate tu_id(s) across source-fact files (a race-free "
            f"per-TU filename should make this impossible): {', '.join(dupes)}"
        )

    fact_set = manifest.fact_set or rollup_fact_set(tus)
    report.fact_set = fact_set
    if not fact_set:
        report.warnings.append(
            "no fact_set identity found (manifest nor any TU record) — this "
            "pack predates ADR-038 C.8 coverage/fact-set reporting, or mixes "
            "producers inconsistently."
        )
    else:
        version = fact_set.get("version")
        if version != SOURCE_ABI_FACT_SET_VERSION:
            report.errors.append(
                f"pack fact_set version is {version!r}; this abicheck build "
                f"expects {SOURCE_ABI_FACT_SET_VERSION!r} — mandatory fact "
                "families may differ from what downstream comparison assumes."
            )

    coverage = rollup_coverage(tus)
    incomplete = incomplete_families(coverage)
    report.incomplete_families = incomplete
    if incomplete:
        report.warnings.append(
            "mandatory fact families reported partial/failed coverage in at "
            f"least one TU: {', '.join(incomplete)} — do not read their "
            "absence from findings as proof nothing changed."
        )

    if tus:
        exports = sorted(set(manifest.exported_symbols))
        surface = link_source_abi(
            tus, exported_symbols=exports, library=manifest.library
        )
        if not surface.reachable_declarations and not surface.reachable_types:
            report.warnings.append(
                "linked surface has an empty public surface (no reachable "
                "declarations or types) — check public-header-roots scoping."
            )

    return report
