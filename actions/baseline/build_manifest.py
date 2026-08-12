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
docs/use/baseline-management.md#baseline-identity-is-more-than-a-version-number):
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

# Volatile-field stripping + the per-artifact content-hash algorithm now
# live in abicheck.buildsource.baseline_set (G30 P1.2) -- the ONE place both
# this producer and resolve-baseline's digest-verification check compute a
# snapshot's stable content hash, so the two can never silently drift apart
# and disagree on what "unchanged content" means. This script still has no
# dependency on abicheck's AbiSnapshot/schema internals beyond that one pure
# utility function -- it keeps reading raw JSON directly, per its own
# docstring above.
from abicheck.buildsource.baseline_set import compute_snapshot_content_hash

#: Canonical storage suffixes a dumped snapshot may carry (ADR-059), longest
#: first so ``.abicheck.json.zst`` is tried before the bare ``.abicheck.json``
#: fallback -- mirrors abicheck.snapshot_io's own suffix table, but this
#: script deliberately doesn't import that (or anything else) from the main
#: abicheck.snapshot_io/serialization surface beyond the one content-hash
#: utility above, per its own docstring.
_SNAPSHOT_SUFFIXES = (".abicheck.json.zst", ".abicheck.json.gz", ".abicheck.json")


def _find_snapshot_path(output_dir: Path, name: str) -> Path | None:
    """Locate library *name*'s dumped snapshot regardless of which storage
    encoding (ADR-059's ``--compression``) produced it.

    Codex review: a leftover sibling from an earlier run using a different
    ``--compression`` setting (e.g. an incomplete cleanup, or a caller that
    invokes this script directly rather than through ``run.sh``, which is
    the one place that actually clears every canonical suffix before a
    fresh dump) must not be silently preferred over -- or silently
    shadowed by -- the snapshot this run actually just produced. More than
    one candidate present is ambiguous stale state, not a priority order to
    pick from; this raises rather than guessing which one is current.
    """
    candidates = [
        output_dir / f"{name}{suffix}"
        for suffix in _SNAPSHOT_SUFFIXES
        if (output_dir / f"{name}{suffix}").is_file()
    ]
    if len(candidates) > 1:
        found = ", ".join(c.name for c in candidates)
        raise SystemExit(
            f"library {name!r} has more than one dumped snapshot present "
            f"({found}) -- this is stale state from a previous run using a "
            "different --compression setting, not a legitimate ambiguity "
            "to guess through. Clear the output directory before dumping "
            "(run.sh's own cleanup does this; a caller invoking this "
            "script directly must do the same)."
        )
    return candidates[0] if candidates else None


def _read_snapshot_meta(path: Path) -> dict[str, Any]:
    # ADR-059: transparently decode a gzip/zstd-compressed snapshot the same
    # way a plain one is read -- detected by magic bytes, not by trusting
    # the filename suffix. Imported lazily (this script otherwise has no
    # dependency on abicheck.snapshot_io) to keep this module's own import
    # surface exactly what its docstring documents.
    from abicheck.snapshot_io import detect_snapshot_compression, read_snapshot_bytes

    raw = json.loads(read_snapshot_bytes(path).decode("utf-8"))
    compression = detect_snapshot_compression(path).value
    # Hash the snapshot with volatile fields removed, not the raw file
    # bytes: dumper.py/collect-facts stamp several fields fresh on every run
    # (absent SOURCE_DATE_EPOCH) even when the actual ABI/source-fact
    # content is identical -- see compute_snapshot_content_hash's docstring.
    sha256 = compute_snapshot_content_hash(raw)
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
        "compression": compression,
        # cli_dump_helpers.fold_dump_provenance_into_json's requested_depth/
        # effective_depth/degraded/frontend/source_scope block -- absent (None)
        # for a snapshot dumped without --depth (audit finding: the baseline
        # manifest recorded profile/schema/fact-set but never the actual depth
        # contract each library's dump satisfied).
        "dump_provenance": raw.get("dump_provenance"),
    }


def _file_sha256(path: Path) -> str:
    """Plain whole-file SHA-256 -- unlike a snapshot's stable *content* hash
    (:func:`compute_snapshot_content_hash`), a staged binary has no
    dumper.py-stamped volatile fields to strip, so hashing the raw bytes is
    both correct and what :func:`~abicheck.buildsource.baseline_set
    ._binary_digest_issue` (the resolver-side check) expects."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(
    output_dir: Path,
    project_ref: str,
    profile: str,
    entries: list[dict[str, Any]],
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
        # ADR-059: the dump step may have written any of the three canonical
        # storage suffixes (plain/gzip/zstd, per --compression) -- discover
        # whichever one actually landed rather than assuming plain JSON.
        snap_path = _find_snapshot_path(output_dir, name)
        if snap_path is None:
            expected = output_dir / (name + ".abicheck.json")
            suffixes = "/".join(_SNAPSHOT_SUFFIXES)
            raise SystemExit(
                f"expected a dumped snapshot for library '{name}' at "
                f"{expected} (or a {suffixes} sibling), but none exist -- "
                "the dump step for this library must have failed silently."
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
        artifact_row: dict[str, Any] = {
            "library": name,
            "artifact": entry.get("artifact", ""),
            "snapshot": snap_path.name,
            # ADR-059: informational only -- resolution/verification both
            # detect compression from the snapshot's own magic bytes
            # regardless of this field, so an older resolver reading this
            # (purely additive) field via its existing defensive .get()
            # access is unaffected whether or not it recognizes the key.
            "compression": meta["compression"],
            "sha256": meta["sha256"],
            "git_commit": meta["git_commit"],
            "git_tag": meta["git_tag"],
            "created_at": meta["created_at"],
            "build_id": meta["build_id"],
            "dump_provenance": meta["dump_provenance"],
        }
        # G30 P1.6 (ADR-047 section 6/section 8 S14 correction): a
        # stage_binary: true entry's run.sh already copied the real ELF
        # binary into output_dir/binaries/<name> -- record its path (relative
        # to output_dir, matching how "snapshot" above is recorded) and a
        # plain whole-file digest, so resolve_bundle() (abicheck/buildsource/
        # baseline_set.py) has both a path to stage and a digest to verify.
        # Read directly from disk rather than trusting entry["stage_binary"]
        # alone: run.sh writing the file and this script recording it are two
        # separate steps, and a run.sh bug that silently skipped the copy
        # must surface as this script's own missing-file error, not a
        # manifest that claims a binary exists when it doesn't.
        if entry.get("stage_binary"):
            binary_path = output_dir / "binaries" / name
            if not binary_path.is_file():
                raise SystemExit(
                    f"library {name!r} declared stage_binary: true, but no "
                    f"staged binary was found at {binary_path} -- the "
                    "binary-staging step for this library must have failed "
                    "silently."
                )
            artifact_row["binary"] = f"binaries/{binary_path.name}"
            artifact_row["binary_sha256"] = _file_sha256(binary_path)
        artifacts.append(artifact_row)

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


def compute_content_digest(manifest: dict[str, Any]) -> str:
    """The normalized "did anything really change" digest -- library name +
    snapshot sha256 + (when staged, G30 P1.6/ADR-047 §8 S14) staged-binary
    sha256, sorted by library, matches action.yml's documented contract
    ("library names + per-file digests").

    Deliberately excludes every other manifest field, most importantly
    ``created_at``: ``dumper.py`` auto-stamps a fresh one on every dump
    call, so hashing the full artifact list (as an earlier version did)
    made the digest change on every run even when every snapshot's actual
    content was identical, defeating its purpose as a change signal
    (CodeRabbit review). Sorted by library so the digest is independent of
    entry/matrix order too.

    ``binary_sha256`` -- the staged ELF binary's own digest, present only
    for a bundle member with ``stage_binary: true`` -- is included
    alongside ``sha256`` (the snapshot's digest), not folded into it or
    left out: a rebuilt binary with unchanged ABI (a code-only or
    reproducibility-only change) has identical snapshot content but
    different binary bytes, and bundle resolution (``resolve_bundle()`` in
    ``abicheck/buildsource/baseline_set.py``) consumes that staged binary
    directly, not the snapshot -- omitting it would let a rerun with a
    genuinely different binary be treated as a safe identical-content retry
    and silently keep the STALE binary published (Codex review). Omitted
    from the hashed row entirely (not hashed as ``None``/``""``) when a
    library has no staged binary, so a manifest schema predating G30 P1.6
    (no ``binary_sha256`` field at all) still reproduces the same digest a
    reader of that older schema would compute.

    A standalone function (not inlined in ``main()``) so a caller outside
    this script's own CLI invocation -- e.g. ``publish-baseline.yml``'s
    release-asset upload step, comparing a freshly-dumped baseline-set's
    digest against an already-published one's -- computes the identical
    normalized digest from a manifest dict, rather than re-deriving the
    formula and risking drift from this one (Codex review: a byte-for-byte
    comparison of the packaged archive would almost always report
    "different content" for a logically-identical retry, since both the
    stamped timestamps and the tar archive's own filesystem metadata vary
    run to run).
    """

    def _row(a: dict[str, Any]) -> dict[str, Any]:
        row = {"library": a["library"], "sha256": a["sha256"]}
        if a.get("binary_sha256"):
            row["binary_sha256"] = a["binary_sha256"]
        return row

    return hashlib.sha256(
        json.dumps(
            sorted(
                (_row(a) for a in manifest["artifacts"]),
                key=lambda a: a["library"],
            ),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def recompute_content_digest_from_disk(
    manifest: dict[str, Any], base_dir: Path
) -> str:
    """:func:`compute_content_digest`, but sourcing each artifact's
    sha256/binary_sha256 from the ACTUAL bytes on disk under *base_dir*,
    never from *manifest*'s own declared fields.

    A safe-retry check that trusts declared digests alone cannot tell
    "identical content" apart from "identical, but wrong, CLAIMS about the
    content" -- an already-published asset whose manifest.json was hand-
    edited, or whose archive member bytes were otherwise corrupted after
    upload, could carry a declared sha256 that still matches a fresh run's
    real digest even though its actual snapshot/binary bytes differ. That
    asset would then pass ``publish-baseline.yml``'s safe-retry comparison
    even though ``resolve_target()``/``resolve_bundle()`` will later
    reject its real member digests when a consumer tries to use it (Codex
    review). Recomputing from disk here closes that gap: this run's own
    manifest (produced by :func:`main` from bytes it just read) is
    unaffected either way, since its declared digests already come from
    the same computation this function repeats -- this exists specifically
    for verifying an EXISTING, previously-published asset's manifest.json
    against the archive it actually shipped with.

    Raises (loudly, not silently) when a referenced snapshot/binary file
    is missing from *base_dir* -- an existing asset this broken is not a
    case ``compute_content_digest`` can meaningfully compare against
    either way, and failing here surfaces that as an actionable error
    rather than a wrong digest.
    """
    recomputed_artifacts: list[dict[str, Any]] = []
    for artifact in manifest.get("artifacts", []):
        library = artifact.get("library", "<unknown>")
        snapshot_name = artifact.get("snapshot")
        if not snapshot_name:
            raise SystemExit(
                f"existing asset's manifest entry for library {library!r} "
                "has no 'snapshot' filename -- cannot verify its real "
                "content."
            )
        snapshot_path = base_dir / snapshot_name
        if not snapshot_path.is_file():
            raise SystemExit(
                f"existing asset's manifest declares snapshot "
                f"{snapshot_name!r} for library {library!r}, but no such "
                "file exists in the extracted archive."
            )
        meta = _read_snapshot_meta(snapshot_path)
        row: dict[str, Any] = {"library": library, "sha256": meta["sha256"]}

        binary_rel = artifact.get("binary")
        if binary_rel:
            binary_path = base_dir / binary_rel
            if not binary_path.is_file():
                raise SystemExit(
                    f"existing asset's manifest declares a staged binary "
                    f"{binary_rel!r} for library {library!r}, but no such "
                    "file exists in the extracted archive."
                )
            row["binary_sha256"] = _file_sha256(binary_path)
        recomputed_artifacts.append(row)

    return compute_content_digest({"artifacts": recomputed_artifacts})


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

    content_digest = compute_content_digest(manifest)

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
