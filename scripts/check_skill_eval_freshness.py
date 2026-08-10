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

"""G37 D6 — skill-evaluation freshness, as a mechanism rather than a rule.

G36 stated in three separate items, and re-patched each review round, that a
publication-relied-on evaluation "must postdate every later commit that changes
the generated skill trees' content". A prose requirement with no enforcement is
why it kept needing restatement. This is the enforcement: the eval pack records
content hashes, every committed transcript bundle records the hashes it ran
against, and this check fails when a bundle claims to be evidence for content
it did not exercise.

Three checks, and the first two are a *dual pair* — each closes a failure the
other cannot see, and both have actually occurred in this plan's own review
history, pointing in opposite directions:

  staleness      a hash a bundle recorded no longer matches the pack. The
                 bundle is evidence about a build, a skill, or a fixture that
                 no longer exists

  mapping        every hash resolves to a non-empty set of published skills.
                 A hash that moves while nominating nothing to re-run leaves
                 the author with a failing check and no way to satisfy it —
                 a deadlock hit twice before this was stated as an invariant

  completeness   every input a run was *observed* reading routes to some hash.
                 An input hashed nowhere accepts evidence that no longer
                 corresponds to anything — which is what the trigger corpus
                 silently was for one commit

Completeness is derived from what the runner's accessor observed, never from a
declaration: a runner self-reporting its inputs reproduces the same failure one
level up, since a runner that starts reading a new file and forgets to declare
it produces a fully-hashed declared set and stale evidence that passes.

**No bundles is not a failure.** Phase 0 ships the contract before the runner
that produces evidence, and a check that failed on an empty evidence tree could
only be satisfied by disabling it. What Phase 0 gates is the pack's own
integrity; Phase 2 adds `check_skill_eval_evidence.py`, which is what makes a
*missing* bundle a failure rather than a vacuous pass.

Run:

    python scripts/check_skill_eval_freshness.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from findings_report import Findings as _SharedFindings  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PACK = ROOT / "agent-evals" / "skills" / "skill-eval-pack.json"
EVIDENCE = ROOT / "agent-evals" / "skills" / "evidence"

#: The pack shape this checker understands. A pack written by a newer
#: generator fails loudly rather than being read field-by-field against
#: assumptions that may no longer hold.
SUPPORTED_PACK_VERSION = 1

REGENERATE = "python scripts/gen_skill_eval_pack.py"


class Findings(_SharedFindings):
    SUMMARY_LABEL = "skill-eval-freshness"


def _rel(path: Path) -> str:
    """Repo-relative for display, absolute when the path is outside the repo —
    the tests point `PACK`/`EVIDENCE` at a tmp tree, and a checker that raised
    on its own error message would be unable to report the failure it found."""
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _load_pack() -> dict[str, Any]:
    return json.loads(PACK.read_text(encoding="utf-8"))


def hash_entries(pack: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Flatten the pack into one `hash id -> entry` table.

    Every consumer here reads this rather than walking the pack's sections, so
    a section added to the pack without being routed shows up as a missing
    entry in one of the checks below rather than as a silently unchecked hash.
    """
    entries: dict[str, dict[str, Any]] = {}
    for name, entry in pack.get("skills", {}).items():
        entries[f"skill_tree:{name}"] = {**entry, "digest": entry.get("tree")}
    for scenario_id, entry in pack.get("scenarios", {}).items():
        entries[f"scenario:{scenario_id}"] = entry
    for shared_id, entry in pack.get("shared", {}).items():
        entries[shared_id] = entry
    return entries


def route(path: str, entries: dict[str, dict[str, Any]]) -> set[str]:
    """Every hash a repo-relative path contributes to.

    A path may route to several hashes (`scenarios.yaml` routes to every
    scenario), and that is the safe direction: over-nomination costs a re-run,
    while a path routing to nothing is an input no hash covers.
    """
    posix = Path(path).as_posix()
    matched: set[str] = set()
    for hash_id, entry in entries.items():
        for root in entry.get("roots", []):
            if posix == root or posix.startswith(root.rstrip("/") + "/"):
                matched.add(hash_id)
    return matched


def check_mapping(
    entries: dict[str, dict[str, Any]], skills: set[str], out: Findings
) -> None:
    """Every hash nominates something to re-run, and can be routed to."""
    for hash_id, entry in sorted(entries.items()):
        if not entry.get("digest"):
            out.err("mapping", f"{hash_id}: no digest recorded in the pack")
        affects = entry.get("affects") or []
        if not affects:
            out.err(
                "mapping",
                f"{hash_id}: affects no skill — a hash that moves while nominating "
                f"nothing to re-run leaves a failing check with nothing to run",
            )
        for skill in affects:
            if skill not in skills:
                out.err(
                    "mapping",
                    f"{hash_id}: affects unpublished skill {skill!r} "
                    f"(published: {', '.join(sorted(skills)) or 'none'})",
                )
        if not entry.get("roots"):
            out.err(
                "mapping",
                f"{hash_id}: declares no roots, so no observed input can ever route "
                f"to it and the completeness check cannot see it",
            )


#: The provenance every bundle must carry, both kinds alike — the same set
#: `transcript-bundle.schema.json` marks required. Checked explicitly rather
#: than by validating the schema, because this module is stdlib-only (the same
#: constraint the other structural gates run under) and `jsonschema` is a dev
#: dependency. Without it the checks below iterate whatever fields happen to
#: exist, so a truncated `meta.json` — in the limit `{"kind": "trigger"}` —
#: passes every comparison by having nothing to compare (Codex review). An
#: omitted field must fail exactly as loudly as a stale one; a runner
#: regression that drops provenance is the case this catches.
REQUIRED_BUNDLE_FIELDS = ("schema_version", "kind", "scenario_id", "agent", "model")
REQUIRED_BUNDLE_HASHES = (
    "skill_tree",
    "abicheck_surface",
    "harness",
    "trigger_corpus",
    "scenarios",
)


def check_bundle_completeness(rel: str, bundle: dict[str, Any], out: Findings) -> None:
    for field in REQUIRED_BUNDLE_FIELDS:
        if bundle.get(field) in (None, ""):
            out.err(
                "completeness", f"{rel}: no {field!r} — this is not a gradeable bundle"
            )
    hashes = bundle.get("hashes") or {}
    for name in REQUIRED_BUNDLE_HASHES:
        if not hashes.get(name):
            out.err(
                "completeness",
                f"{rel}: records no {name!r} hash, so nothing here can go stale when "
                f"that input changes",
            )
    if not bundle.get("observed_inputs"):
        out.err(
            "completeness",
            f"{rel}: records no observed inputs — the completeness check reads what the "
            f"accessor observed, so an empty set asserts the run read nothing at all",
        )


def _bundle_hash_ids(bundle: dict[str, Any]) -> dict[str, str]:
    """The recorded hashes, keyed the same way `hash_entries` keys the pack."""
    hashes = bundle.get("hashes", {})
    recorded: dict[str, str] = {}
    for key, value in hashes.items():
        if key == "scenarios":
            for scenario_id, digest in (value or {}).items():
                recorded[f"scenario:{scenario_id}"] = digest
        elif key == "skill_tree":
            # The bundle names one skill's tree; which skill is the bundle's
            # own subdirectory, resolved by the caller.
            recorded["skill_tree"] = value
        else:
            recorded[key] = value
    return recorded


def check_bundle(
    path: Path,
    entries: dict[str, dict[str, Any]],
    out: Findings,
) -> set[str]:
    """Check one bundle; return the skills its stale hashes nominate."""
    rel = _rel(path)
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        out.err("bundle", f"{rel}: unreadable ({exc})")
        return set()

    # evidence/<skill>/<...>/meta.json — the skill a `skill_tree` hash belongs
    # to is the tree position, not a field, so a bundle cannot claim to be
    # evidence for a skill it is not filed under.
    try:
        skill = path.relative_to(EVIDENCE).parts[0]
    except ValueError:
        out.err("bundle", f"{rel}: not under {_rel(EVIDENCE)}/<skill>/")
        return set()

    check_bundle_completeness(rel, bundle, out)

    # A behavioral bundle is evidence for one scenario, so it must hash *that*
    # scenario. Without this a bundle could carry a perfectly fresh hash for an
    # unrelated scenario and be accepted as evidence for the one it names —
    # the same "claims to be evidence for something it did not exercise" shape
    # the directory rule above closes for skills.
    if bundle.get("kind") == "behavioral":
        scenario_id = bundle.get("scenario_id", "")
        scenario = entries.get(f"scenario:{scenario_id}")
        if scenario is None:
            out.err(
                "bundle",
                f"{rel}: names scenario {scenario_id!r}, which the pack has no",
            )
        else:
            if scenario_id not in (bundle.get("hashes", {}).get("scenarios") or {}):
                out.err(
                    "completeness",
                    f"{rel}: names scenario {scenario_id!r} but records no hash for it, "
                    f"so editing that scenario would leave this bundle 'fresh'",
                )
            if scenario.get("skill") != skill:
                out.err(
                    "bundle",
                    f"{rel}: is filed under {skill!r} but scenario {scenario_id!r} "
                    f"belongs to {scenario.get('skill')!r}",
                )

    nominated: set[str] = set()
    for key, digest in _bundle_hash_ids(bundle).items():
        hash_id = f"skill_tree:{skill}" if key == "skill_tree" else key
        entry = entries.get(hash_id)
        if entry is None:
            out.err(
                "staleness",
                f"{rel}: records hash {hash_id!r}, which the pack no longer has — "
                f"the input it covered is gone or was renamed",
            )
            continue
        if digest != entry.get("digest"):
            out.err(
                "staleness",
                f"{rel}: {hash_id} is stale (bundle {digest}, pack {entry.get('digest')}) — "
                f"re-run the evaluation for: {', '.join(entry.get('affects') or []) or '?'}",
            )
            nominated.update(entry.get("affects") or [])

    for observed in bundle.get("observed_inputs", []):
        observed_path = observed.get("path", "")
        if not route(observed_path, entries):
            out.err(
                "completeness",
                f"{rel}: read {observed_path!r}, which routes to no hash — evidence "
                f"stays 'fresh' when that input changes. Add it to a pack entry's "
                f"roots ({REGENERATE})",
            )
    return nominated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.parse_args(argv)

    out = Findings()

    if not PACK.is_file():
        out.err("pack", f"{_rel(PACK)} is missing — run {REGENERATE}")
        out.report()
        return 1

    try:
        pack = _load_pack()
    except json.JSONDecodeError as exc:
        out.err("pack", f"{_rel(PACK)} is not valid JSON ({exc})")
        out.report()
        return 1

    version = pack.get("pack_version")
    if version != SUPPORTED_PACK_VERSION:
        out.err(
            "pack",
            f"pack_version {version!r} is not the supported {SUPPORTED_PACK_VERSION} — "
            f"this checker would read a shape it does not understand",
        )
        out.report()
        return 1

    entries = hash_entries(pack)
    skills = set(pack.get("skills", {}))
    if not skills:
        out.err(
            "pack", "the pack records no skills — nothing can be evidence for anything"
        )

    check_mapping(entries, skills, out)

    nominated: set[str] = set()
    bundles = sorted(EVIDENCE.rglob("meta.json")) if EVIDENCE.is_dir() else []
    for bundle_path in bundles:
        nominated |= check_bundle(bundle_path, entries, out)

    if nominated:
        out.err(
            "staleness",
            f"re-run the evaluation for these skills and commit the refreshed "
            f"bundles: {', '.join(sorted(nominated))}",
        )

    return out.report()


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
