#!/usr/bin/env python3
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
"""Build a baseline-set manifest.json from a directory of per-library
.abicheck.json snapshots that actions/baseline/run.sh just dumped.

Reads each snapshot's raw JSON directly (not through abicheck's AbiSnapshot
model) so this script has no dependency on abicheck's internal schema beyond
a handful of top-level, long-stable keys -- the same defensive-.get()
philosophy abicheck/buildsource/CLAUDE.md documents for its own dataclasses.

A baseline-set is *not* self-describing from a version number alone (see
docs/user-guide/baseline-management.md#baseline-identity-is-more-than-a-version-number):
this manifest records the profile string the caller supplies, plus each
snapshot's own schema_version and (when build-source evidence is embedded)
fact_set identity, so a mismatch against a previous manifest is a structured
comparison instead of a human guessing from a filename.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

# Keys that vary between two dumps/replays of otherwise ABI-identical
# content -- timestamps, source-file mtimes, and wall-clock/cache-state
# counters -- so a stable content hash must strip all of them, not hash raw
# file bytes. This started as a single `created_at` pop and grew by three
# separate review rounds (top-level created_at, then the nested
# build_source.manifest.created_at, then this fuller set) as each left
# something volatile behind; kept as one explicit list instead of another
# one-off pop so the next volatile field lands here too (Codex review).
_VOLATILE_TOP_LEVEL_KEYS = ("created_at", "source_mtime", "source_mtime_epoch")
_VOLATILE_BUILD_SOURCE_MANIFEST_KEYS = ("created_at",)
# BuildSourceRef.path_hint (abicheck/buildsource/model.py) -- the out-of-band
# pack's on-disk location, documented on the field itself as "advisory only".
# cli_buildsource.py's embed_build_source() defaults it to the --sources/
# --build-info operand path when the caller doesn't pass a name hint, so the
# same ABI/source facts dumped from a relative abicheck_inputs/ vs an
# absolute restored pack path get a different path_hint and therefore a
# different per-artifact sha256/content-digest, even though nothing semantic
# changed (Codex review). content_hash/coverage_summary, the ref's other two
# fields, are real content identity and stay.
_VOLATILE_BUILD_SOURCE_PACK_KEYS = ("path_hint",)
# Populated by abicheck/buildsource/source_replay.py's replay producer (and
# inline.py's cache bookkeeping) -- wall-clock durations and cache hit/miss
# counts that depend on the runner's cache warmth and load, not on the
# semantic source-fact content itself.
_VOLATILE_COVERAGE_KEYS = (
    "cache_lookup_s",
    "extract_s",
    "link_s",
    "elapsed_s",
    "cache_misses",
    "cache_hits",
    # The replay producer's own parallelism setting (CPU count or
    # ABICHECK_L4_JOBS), not a property of the extracted content -- the
    # same ABI/source facts replayed on a differently-sized runner would
    # otherwise still churn the digest (Codex review).
    "extractor_jobs",
)
# LayerCoverage rows (abicheck/buildsource/model.py) embedded at
# build_source.manifest.coverage: build_inline_coverage() (buildsource/
# inline.py) copies the same cache/timing state into each row's "detail"
# (a free-form human string that can embed "cache X/Y hit (Z%)" and
# "N.NNs") and "elapsed_s" -- a *third* place the same volatile info
# leaks into, after source_abi.coverage and build_source.manifest itself.
# "layer"/"status"/"confidence" are the semantic identity; "detail" is by
# its own docstring presentational, so both are dropped rather than parsed.
_VOLATILE_MANIFEST_COVERAGE_ROW_KEYS = ("detail", "elapsed_s")
# ExtractorRecord rows (abicheck/buildsource/model.py) embedded at
# build_source.manifest.extractors: inline_graph_fold.py appends
# "N.NNs, jobs=M" (last_elapsed_s/last_jobs) to a row's "detail", and
# started_at/finished_at are ISO 8601 wall-clock bounds -- runner
# load/CPU count and collection time, not source-fact content, so a
# fourth leak of the same volatile-info-in-a-row-field shape. name/
# version/status/inputs/artifacts/command/command_hash/capabilities/
# diagnostics are the semantic identity and stay.
_VOLATILE_MANIFEST_EXTRACTOR_ROW_KEYS = ("detail", "started_at", "finished_at")


def _strip_row_keys(rows: Any, volatile_keys: tuple[str, ...]) -> Any:
    if not isinstance(rows, list):
        return rows
    return [
        {k: v for k, v in row.items() if k not in volatile_keys}
        if isinstance(row, dict)
        else row
        for row in rows
    ]


def _strip_volatile_fields(raw: dict[str, Any]) -> dict[str, Any]:
    stable = dict(raw)
    for key in _VOLATILE_TOP_LEVEL_KEYS:
        stable.pop(key, None)

    build_source_pack = stable.get("build_source_pack")
    if isinstance(build_source_pack, dict):
        build_source_pack = dict(build_source_pack)
        for key in _VOLATILE_BUILD_SOURCE_PACK_KEYS:
            build_source_pack.pop(key, None)
        stable["build_source_pack"] = build_source_pack

    build_source = stable.get("build_source")
    if isinstance(build_source, dict):
        build_source = dict(build_source)

        manifest = build_source.get("manifest")
        if isinstance(manifest, dict):
            manifest = dict(manifest)
            for key in _VOLATILE_BUILD_SOURCE_MANIFEST_KEYS:
                manifest.pop(key, None)
            if "coverage" in manifest:
                manifest["coverage"] = _strip_row_keys(
                    manifest["coverage"], _VOLATILE_MANIFEST_COVERAGE_ROW_KEYS
                )
            if "extractors" in manifest:
                manifest["extractors"] = _strip_row_keys(
                    manifest["extractors"], _VOLATILE_MANIFEST_EXTRACTOR_ROW_KEYS
                )
            build_source["manifest"] = manifest

        source_abi = build_source.get("source_abi")
        if isinstance(source_abi, dict):
            source_abi = dict(source_abi)
            coverage = source_abi.get("coverage")
            if isinstance(coverage, dict):
                coverage = dict(coverage)
                for key in _VOLATILE_COVERAGE_KEYS:
                    coverage.pop(key, None)
                source_abi["coverage"] = coverage
            build_source["source_abi"] = source_abi

        stable["build_source"] = build_source

    return stable


def _read_snapshot_meta(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        raw = json.load(f)
    # Hash the snapshot with volatile fields removed, not the raw file
    # bytes: dumper.py/collect-facts stamp several fields fresh on every run
    # (absent SOURCE_DATE_EPOCH) even when the actual ABI/source-fact
    # content is identical -- see _strip_volatile_fields above.
    stable = _strip_volatile_fields(raw)
    sha256 = hashlib.sha256(
        json.dumps(stable, sort_keys=True).encode("utf-8")
    ).hexdigest()
    fact_set = None
    build_source = raw.get("build_source")
    if isinstance(build_source, dict):
        source_abi = build_source.get("source_abi")
        if isinstance(source_abi, dict):
            # SourceAbiSurface.to_dict() (abicheck/buildsource/source_abi.py)
            # has no top-level "fact_set" key -- the rolled-up identity is
            # written to surface.coverage["fact_set"] by
            # source_link.link_source_abi() (abicheck/buildsource/
            # source_link.py). Reading source_abi["fact_set"] directly always
            # returned None for a real dump --sources/--build-info baseline,
            # silently disabling the freshness recipe-identity check this
            # manifest exists to provide (Codex review).
            coverage = source_abi.get("coverage")
            if isinstance(coverage, dict):
                fact_set = coverage.get("fact_set")
    return {
        "schema_version": raw.get("schema_version"),
        "library": raw.get("library"),
        "version": raw.get("version"),
        "git_commit": raw.get("git_commit"),
        "git_tag": raw.get("git_tag"),
        "created_at": raw.get("created_at"),
        "build_id": raw.get("build_id"),
        "fact_set": fact_set,
        "sha256": sha256,
        # cli_dump_helpers.fold_dump_provenance_into_json's requested_depth/
        # effective_depth/degraded/frontend/source_scope block -- absent (None)
        # for a snapshot dumped without --depth (audit finding: the baseline
        # manifest recorded profile/schema/fact-set but never the actual depth
        # contract each library's dump satisfied).
        "dump_provenance": raw.get("dump_provenance"),
    }


def build_manifest(
    output_dir: Path,
    project_ref: str,
    profile: str,
    entries: list[dict[str, str]],
    previous_manifest_path: Path | None,
) -> dict[str, Any]:
    artifacts = []
    schema_versions: set[int] = set()
    # The full source-fact recipe identity, not just (name, version): two
    # snapshots can share fact_set.version while a producer/compiler upgrade
    # (e.g. a new Clang plugin build, or a different loading Clang) silently
    # changed the opaque body/template hash recipe underneath it -- see
    # abicheck/buildsource/fact_set.py's own producer/producer_version/
    # compiler_version comparability rules, which this mirrors so a refresh
    # is flagged for the same reasons.
    fact_set_ids: set[tuple[str, int, str, str, str, str]] = set()
    fact_set_present = 0
    fact_set_absent = 0
    for entry in entries:
        name = entry["name"]
        snap_path = output_dir / f"{name}.abicheck.json"
        if not snap_path.is_file():
            raise SystemExit(
                f"expected a dumped snapshot for library '{name}' at "
                f"{snap_path}, but it does not exist -- the dump step for "
                f"this library must have failed silently."
            )
        meta = _read_snapshot_meta(snap_path)
        # A missing schema_version is not a legitimate "unknown" state to
        # silently tolerate -- every real `abicheck dump` snapshot carries
        # one, so its absence means this snapshot is malformed/truncated,
        # and letting it through would publish a manifest whose
        # snapshot_schema silently lost that information (CodeRabbit review).
        if meta["schema_version"] is None:
            raise SystemExit(
                f"snapshot for library {name!r} is missing schema_version "
                f"-- the dump step for this library must have produced a "
                f"malformed snapshot."
            )
        schema_versions.add(int(meta["schema_version"]))
        fact_set = meta["fact_set"]
        if fact_set is None:
            # No build_source/source_abi/coverage.fact_set at all -- this
            # library was legitimately dumped without --build-info/--sources.
            fact_set_absent += 1
        elif (
            isinstance(fact_set, dict)
            and fact_set.get("name")
            and fact_set.get("version") is not None
        ):
            fact_set_present += 1
            fact_set_ids.add(
                (
                    str(fact_set["name"]),
                    int(fact_set["version"]),
                    str(fact_set.get("compiler_family") or ""),
                    str(fact_set.get("producer") or ""),
                    str(fact_set.get("producer_version") or ""),
                    str(fact_set.get("compiler_version") or ""),
                )
            )
        else:
            # A non-None fact_set that isn't a well-formed identity is
            # corrupted evidence, not "no evidence" -- collapsing it into
            # fact_set_absent (as before) silently published a lossy
            # baseline identity instead of surfacing the corruption
            # (CodeRabbit review).
            raise SystemExit(
                f"snapshot for library {name!r} has a malformed fact_set "
                f"identity {fact_set!r} -- expected a dict with at least "
                f"'name' and 'version' keys."
            )
        artifacts.append(
            {
                "library": name,
                "artifact": entry.get("artifact", ""),
                "snapshot": snap_path.name,
                "sha256": meta["sha256"],
                "git_commit": meta["git_commit"],
                "git_tag": meta["git_tag"],
                "created_at": meta["created_at"],
                "build_id": meta["build_id"],
                "dump_provenance": meta["dump_provenance"],
            }
        )

    # Every check below is a self-consistency invariant of one baseline-set
    # run (all libraries dumped in the same job, by the same installed
    # abicheck, against the same shared --build-info pack per action.yml's
    # contract) -- a violation means the invariant broke, not that there is
    # a legitimate "mixed" state to represent, so this fails loudly rather
    # than publishing a manifest whose identity silently dropped information
    # a later comparison could have used to detect drift (CodeRabbit review).
    if len(schema_versions) > 1:
        raise SystemExit(
            f"baseline-set snapshots disagree on schema_version "
            f"{sorted(schema_versions)} -- they were dumped by different "
            f"abicheck versions in the same run, which should never happen."
        )
    if fact_set_present and fact_set_absent:
        raise SystemExit(
            f"baseline-set snapshots disagree on whether source-fact "
            f"evidence is present: {fact_set_present} carry a fact_set "
            f"identity, {fact_set_absent} do not -- each library should "
            f"share the one build-info pack passed to every dump call "
            f"(pass the same --build-info/--sources to every library, or "
            f"none)."
        )
    if len(fact_set_ids) > 1:
        raise SystemExit(
            f"baseline-set snapshots disagree on fact_set identity "
            f"{sorted(fact_set_ids)} -- each library should share the one "
            f"build-info pack passed to every dump call."
        )

    fact_set_out = None
    if len(fact_set_ids) == 1:
        (
            name,
            version,
            compiler_family,
            producer,
            producer_version,
            compiler_version,
        ) = next(iter(fact_set_ids))
        fact_set_out = {"name": name, "version": version}
        # Only recorded when present, so a fact_set with no producer identity
        # (a pre-C.8 producer, or a hand-written one) keeps the same
        # {"name", "version"}-only shape as before.
        if compiler_family:
            fact_set_out["compiler_family"] = compiler_family
        if producer:
            fact_set_out["producer"] = producer
        if producer_version:
            fact_set_out["producer_version"] = producer_version
        if compiler_version:
            fact_set_out["compiler_version"] = compiler_version

    manifest: dict[str, Any] = {
        "manifest_version": 1,
        "project_ref": project_ref,
        "profile": profile,
        "snapshot_schema": max(schema_versions) if schema_versions else None,
        "fact_set": fact_set_out,
        "artifacts": artifacts,
    }
    manifest["freshness"] = _compute_freshness(manifest, previous_manifest_path)
    return manifest


def _compute_freshness(
    manifest: dict[str, Any], previous_manifest_path: Path | None
) -> dict[str, Any]:
    """Compare against a previous manifest (if given) and report what
    changed -- the structured input to an Action's refresh-required output.
    Absent a previous manifest, freshness cannot be assessed either way."""
    if previous_manifest_path is None:
        return {"refresh_required": False, "reasons": []}
    if not previous_manifest_path.is_file():
        # Omitting --previous-manifest entirely is the documented way to say
        # "no previous baseline" (action.yml); a caller that *did* pass one
        # pointing at a path that doesn't exist is a broken workflow (a typo,
        # or an artifact download that silently failed) -- silently treating
        # it the same as "omitted" would report refresh-required=false as if
        # the comparison had actually run and found nothing stale (CodeRabbit
        # review).
        raise SystemExit(
            f"--previous-manifest was given as {previous_manifest_path} but "
            "that file does not exist -- omit the flag entirely for 'no "
            "previous baseline', don't point it at a missing path."
        )

    with previous_manifest_path.open(encoding="utf-8") as f:
        previous = json.load(f)

    reasons = []
    if previous.get("profile") != manifest["profile"]:
        # profile is the platform/compiler build-profile identity the action
        # itself records (e.g. linux-x86_64-gcc vs linux-x86_64-clang) -- a
        # previous-manifest from a different profile is not a stale copy of
        # this one, it is a baseline for a different target entirely, and
        # comparing schema/fact_set/library-set alone can't tell the two
        # apart (Codex review).
        reasons.append(
            f"profile {previous.get('profile')!r} -> {manifest['profile']!r}"
        )
    if previous.get("snapshot_schema") != manifest["snapshot_schema"]:
        reasons.append(
            f"snapshot_schema {previous.get('snapshot_schema')} -> {manifest['snapshot_schema']}"
        )
    if previous.get("fact_set") != manifest["fact_set"]:
        reasons.append(f"fact_set {previous.get('fact_set')} -> {manifest['fact_set']}")

    prev_libs = {a["library"] for a in previous.get("artifacts", [])}
    cur_libs = {a["library"] for a in manifest["artifacts"]}
    removed = prev_libs - cur_libs
    added = cur_libs - prev_libs
    if removed:
        reasons.append(f"libraries removed: {sorted(removed)}")
    if added:
        reasons.append(f"libraries added: {sorted(added)}")

    return {"refresh_required": bool(reasons), "reasons": reasons}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--project-ref", default="")
    parser.add_argument("--profile", default="")
    parser.add_argument(
        "--libraries",
        required=True,
        help='JSON array of {"name": ..., "artifact": ...} entries, one per library.',
    )
    parser.add_argument("--previous-manifest", default=None, type=Path)
    parser.add_argument("--manifest-out", required=True, type=Path)
    args = parser.parse_args(argv)

    entries = json.loads(args.libraries)
    manifest = build_manifest(
        args.output_dir, args.project_ref, args.profile, entries, args.previous_manifest
    )
    args.manifest_out.write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    # Only library name + snapshot sha256, sorted by library -- matches
    # action.yml's documented contract ("library names + per-file digests").
    # Hashing the full artifact list (as before) pulled in created_at, which
    # dumper.py auto-stamps fresh on every dump call, so the digest changed
    # on every run even when every snapshot's actual content was identical,
    # defeating its purpose as a "did anything really change" signal
    # (CodeRabbit review). Sorted by library so digest is independent of
    # entry/matrix order too.
    content_digest = hashlib.sha256(
        json.dumps(
            sorted(
                (
                    {"library": a["library"], "sha256": a["sha256"]}
                    for a in manifest["artifacts"]
                ),
                key=lambda a: a["library"],
            ),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    # key=value lines on stdout -- the caller (run.sh) forwards these to
    # GITHUB_OUTPUT rather than this script writing there directly, so it
    # stays testable as a plain function/CLI with no Action-runner dependency.
    print(f"library-count={len(manifest['artifacts'])}")
    print(f"content-digest={content_digest}")
    print(
        f"refresh-required={'true' if manifest['freshness']['refresh_required'] else 'false'}"
    )
    print(f"refresh-reasons={'; '.join(manifest['freshness']['reasons'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
