# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Human-readable observations and the cross-library dependency inventory."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def evidence_lines(evidence: object, contract_reason: object = None) -> list[str]:
    if not isinstance(evidence, dict):
        return []
    lines = []
    for export in evidence.get("removed_exports", []):
        version = export["version"]
        label = export["name"]
        if version:
            label += ("@@" if export["default"] else "@") + version
        lines.append(
            f"{export['library']} lost {label} (old definition: {export['binding']})."
        )
    if header := evidence.get("declaration_header"):
        lines.append(f"Declaration in {header}.")
    if evidence.get("removed_exports"):
        if contract_reason == "export_root_membership":
            lines.append("The selected export contract covers this exact name.")
        else:
            lines.append("The external-export guarantee depends on the project policy.")
    if template_header := evidence.get("owner_template_header"):
        lines.append(f"The owning template is declared in {template_header}.")
    if "client_observations" in evidence:
        clients = evidence["client_observations"]
        if not clients:
            lines.append("Clients were not checked.")
        for client in clients:
            path = client["artifact"]
            if client["artifact_role"] != "unspecified":
                path += f" ({client['artifact_role']})"
            matches = client["direct_name_matches"]
            if matches is None:
                lines.append(f"{path}: direct ELF imports unavailable.")
                continue
            lines.append(f"{path}: {len(matches)} direct name matches.")
            for match in matches:
                version = match["version"] or "unversioned"
                lines.append(
                    f"{match['name']} ({version}, {match['binding']} reference, version provider: {match['provider'] or 'unspecified'})."
                )
            resolution = client["resolution"]
            if resolution["status"] == "tested":
                for side in ("old", "new"):
                    trial = resolution[side]
                    lines.append(
                        f"{side} control link: exit {trial['exit_code']}. {trial['diagnostic']}".rstrip()
                    )
            elif resolution["status"] == "modeled":
                for trial in resolution["bindings"]:
                    for side in ("old", "new"):
                        target = trial.get(side) or {}
                        status = target.get("status", "unknown")
                        provider = target.get("provider") or "no provider identified"
                        lines.append(
                            f"{side} modeled resolution: {status} ({provider})."
                        )
            elif matches:
                lines.append("Link/load resolution was not tested.")
    if declarations := evidence.get("enum_declarations"):
        for side, decl in declarations.items():
            header = decl["header"] or decl["location"] or "unknown header"
            lines.append(
                f"{side}: {decl['name']} in {header} (owner: {decl['owner']})."
            )
            for path in decl["public_field_paths"]:
                lines.append("Public field: " + " → ".join(path))
            for path in decl["public_inline_arguments"]:
                lines.append(
                    "Public inline argument to exported function: " + " → ".join(path)
                )
        if not any(
            d["public_field_paths"]
            or d["public_inline_arguments"]
            or d["public_target"]
            for d in declarations.values()
        ):
            lines.append(
                "The available declarations do not establish a product ABI dependency on this value."
            )
    return lines


def grouped_export_variants(
    findings: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Keep the exact names behind a shared demangled C++ spelling."""
    groups: dict[tuple[str, str], dict[str, str]] = {}
    for finding in findings:
        spelling = finding.get("demangled_symbol")
        if not spelling:
            continue
        for export in (finding.get("review_evidence") or {}).get("removed_exports", ()):
            key = (export["library"], spelling)
            groups.setdefault(key, {})[export["name"]] = finding["finding_id"]
    return [
        {
            "library": library,
            "demangled_symbol": name,
            "variants": [
                {"name": symbol, "finding_id": fid}
                for symbol, fid in sorted(variants.items())
            ],
        }
        for (library, name), variants in sorted(groups.items())
        if len(variants) > 1
    ]


def dependency_changes(libraries: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    for library in libraries:
        for finding in library.get("findings", []):
            evidence = finding.get("review_evidence") or {}
            sides = evidence.get("enum_declarations")
            if not sides or not all(
                d["owner"].startswith("dependency:") for d in sides.values()
            ):
                continue
            identity = tuple(
                (d["owner"], d["header"], d["name"])
                for d in (sides["old"], sides["new"])
            )
            key = (
                identity,
                finding["symbol"],
                finding["old_value"],
                finding["new_value"],
            )
            entry = groups.setdefault(
                key,
                {
                    "symbol": finding["symbol"],
                    "old_value": finding["old_value"],
                    "new_value": finding["new_value"],
                    "declarations": sides,
                    "libraries": [],
                    "observations": [],
                },
            )
            if library["library"] not in entry["libraries"]:
                entry["libraries"].append(library["library"])
                entry["observations"].append(
                    {
                        "library": library["library"],
                        "finding_id": finding.get("finding_id"),
                        "declarations": sides,
                    }
                )
    return list(groups.values())


def attach_deployment_observations(
    libraries: Iterable[Mapping[str, Any]], binding: Mapping[str, Any] | None
) -> None:
    """Join the existing deployment resolver's evidence to each ELF loss."""
    if not binding:
        return
    for client in (binding, *binding.get("additional_consumers", ())):
        references = client.get("direct_references")
        by_name: dict[str, list[dict[str, Any]]] = {}
        for ref in references or ():
            by_name.setdefault(ref["name"], []).append(ref)
        resolutions: dict[str, list[dict[str, Any]]] = {}
        for item in client["imports"]:
            resolutions.setdefault(item["symbol"], []).append(item)
        for library in libraries:
            for finding in library.get("findings", ()):
                evidence = finding.get("review_evidence") or {}
                if not evidence.get("removed_exports"):
                    continue
                name = finding["symbol"]
                matches = by_name.get(name, [])
                evidence["client_check_status"] = "see_artifacts"
                evidence["client_observations"].append(
                    {
                        "artifact": client["client"],
                        "artifact_role": "unspecified",
                        "status": "checked"
                        if references is not None
                        else "unavailable",
                        "match_count": len(matches) if references is not None else None,
                        "direct_name_matches": matches
                        if references is not None
                        else None,
                        "resolution": {
                            "status": "modeled",
                            "bindings": resolutions[name],
                        }
                        if name in resolutions
                        else {"status": "not_tested"},
                    }
                )
