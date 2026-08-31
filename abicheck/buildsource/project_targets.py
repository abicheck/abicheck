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

"""``.abicheck.yml`` ``targets:``/``bundles:``/``profiles:``/``baseline:``
block (ADR-047 §3, G30 P1.5).

Extends the project config with the portable, project-owned surface
``check-project.yml``'s run-plan generator (G30 P1.4, not built yet) will
consume: which libraries/consumers/plugin-contracts exist, how they group
into release bundles, which build profiles are ABI contracts, which baseline
channels exist, and — the schema gap ADR-047 §3 flags and this module
resolves — exactly which ``{channel, depth, required, gate_mode, profiles}``
checks run against each target/bundle.

This module only defines the contract and validates a hand-authored
``targets:``/``bundles:``/``profiles:``/``baseline:`` block — there is no
run-plan generator here yet (that's G30 P1.4, which *consumes* this) and no
``abicheck project-targets init`` scaffolding either. Pure: parses a dict,
never touches the filesystem beyond reading the one YAML file.

``BuildConfig`` (:mod:`abicheck.buildsource.build_config`) recognizes
``targets``/``bundles``/``profiles``/``baseline`` as known top-level
``.abicheck.yml`` keys (so their presence doesn't trip its own strict
unknown-key error) but does not parse them itself — the same
recognized-but-not-parsed treatment it already gives ``risk_rules``/
``crosschecks``, which are likewise owned by a sibling module. This module's
own loader (:func:`load_project_targets_config`) re-reads the same YAML file
and is the sole owner of this block's schema.

Two design choices this module makes, where ADR-047 §3 flagged an open gap
and deliberately left the choice to P1.5:

- **Profile scoping for ``checks:`` entries.** Rather than assume the naive
  "cross every check with every ``contract: true`` profile" product is safe
  (ADR-047 §3 explicitly warns this produces impossible cells for a target
  that doesn't exist on every profile), each ``checks:`` entry carries an
  *optional* explicit ``profiles:`` selector. When set, the check runs only
  on the listed profile ids (validated against ``profiles:`` block). When
  omitted, this module does not resolve it to a profile list at all — G30
  P1.4's run-plan generator is responsible for deriving the actual
  ``(target, profile)`` cells from each profile's own ``build-output.json``
  ``targets[]`` list (the ADR's second, safer option), never from a blind
  cross-product. This module's validator does not and cannot enforce that
  downstream behaviour; it only validates that an *explicit* ``profiles:``
  selector, when present, names real declared profile ids.
- **``app-consumer``/``plugin-contract`` redirection.** Per ADR-047 §3's
  two "unstated rule" corrections, both the baseline-lookup key and the
  candidate-artifact lookup for these two ``kind``s resolve through their
  ``library`` field, while the check's own reporting identity stays the
  contract target's own name. This module validates that ``library`` names
  a real ``kind: library`` target (not an app-consumer/plugin-contract
  target, which cannot itself be resolved further) but does not perform the
  redirection itself — that is G30 P1.2 (``resolve-baseline``)/P1.3
  (``check-target``)'s job at run time.

``bundles:`` entries also carry their own ``checks:`` (same shape as a
target's) — the ADR-047 §5 run-plan emits a ``kind: "bundle"`` check
alongside per-target ones (S14 bundle-scoped analysis), and that cell needs
its own baseline-channel/depth/gate policy just like a target's does; see
:class:`BundleSpec`.

``ProjectTargetsConfig.from_dict`` validates every top-level key in the raw
mapping against :data:`~.build_config.KNOWN_TOP_LEVEL_KEYS` — the *full*
``.abicheck.yml`` key set, not just this module's four owned keys — so a
misspelled block (``tagrets:``) is a hard error rather than silently
parsing as an empty, all-default config. Every ``targets:``/``bundles:``/
``profiles:``/``baseline.channels:`` mapping key must itself be a real YAML
string (PyYAML's default resolver reads a bare ``on``/``off``/``yes``/``no``
key as a bool and a bare digit key as an int; silently ``str()``-coercing
either would mint an id the user never actually wrote). ``"none"`` is
reserved and cannot be declared as a real ``baseline.channels`` id — it is
:data:`NO_BASELINE_CHANNEL`, the sentinel a ``checks[].channel`` uses to
bypass ``resolve-baseline`` entirely (ADR-047 §6 S5); allowing a real
channel of that name would make the sentinel ambiguous with an actual
baseline lookup.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..api_types import HEADER_AST_FRONTENDS
from .build_config import KNOWN_TOP_LEVEL_KEYS
from .scan_levels import USER_DEPTHS, EvidenceDepth

#: The identifier charset every target/bundle/profile/channel id must satisfy
#: — matches the per-component pattern the report-identity envelope (ADR-047
#: §7, ``compare_report.schema.json``'s ``check_id``) already enforces for
#: ``target@profile#baseline_channel@depth``, so a name valid here can never
#: produce an ambiguous/unparseable check_id downstream.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: ADR-047 §3 ``targets:`` ``kind`` discriminator.
TARGET_KIND_LIBRARY = "library"
TARGET_KIND_APP_CONSUMER = "app-consumer"
TARGET_KIND_PLUGIN_CONTRACT = "plugin-contract"
TARGET_KINDS = frozenset(
    {TARGET_KIND_LIBRARY, TARGET_KIND_APP_CONSUMER, TARGET_KIND_PLUGIN_CONTRACT}
)

#: ADR-047 §4/§7 ``check-target`` gate-mode values.
GATE_MODES = frozenset({"local", "deferred", "advisory"})

#: ADR-047 §10 baseline storage backends (external object store is P2, out of
#: scope here).
BASELINE_SOURCES = frozenset({"github-release", "actions-cache", "git"})

#: The evidence-depth ladder a ``checks:`` entry's ``depth`` must be one of —
#: the same four public rungs ``requested_depth``/``effective_depth`` accept
#: in the report schema (ADR-047 §7).
CHECK_DEPTHS = frozenset(d.value for d in USER_DEPTHS)

#: A ``bundle`` check's ``depth`` is further restricted to this single rung --
#: ``kind: bundle`` always compares directories (the resolved binaries-dir vs.
#: the candidate bundle directory) in ``actions/check-target``, which routes
#: through the CLI's per-library release fan-out and never collects inline
#: build/source evidence for that path (``actions/check-target/
#: validate-inputs.sh`` rejects ``build``/``source`` for ``kind: bundle``).
#: ``headers`` is *also* rejected here (not just build/source): a bundle
#: baseline's old-library operand is always a directory of raw binaries
#: (``actions/baseline``'s bundle staging, unlike its single-target mode,
#: never produces pre-dumped ``.abi.json`` snapshots with historical header
#: data already baked in), so at ``depth: headers`` both the old and new
#: sides would be freshly header-parsed at compare time using the SAME
#: current checkout's headers (``check-project.yml`` has only one
#: project-wide ``header:`` input) -- a header-only change between baseline
#: and candidate (e.g. an inline function or template removed) would be
#: silently invisible, since only one version of the headers is ever parsed
#: (Codex review). Until per-bundle-member baseline header staging exists,
#: only binary-level (L0/L1) evidence is safe for a bundle check.
BUNDLE_CHECK_DEPTHS = frozenset({EvidenceDepth.BINARY.value})

#: Sentinel ``channel`` value for a ``baseline: none`` check (ADR-047 §6 S5
#: correction) — ``check-target`` (P1.3) must skip ``resolve-baseline``
#: entirely for a check carrying this value, never look it up as a declared
#: channel name.
NO_BASELINE_CHANNEL = "none"


def _opt_str_field(d: dict[str, Any], key: str, *, where: str) -> str:
    """A strictly-typed optional string field: absent/``None`` -> ``""``, any
    non-string present value is a hard error (ADR-043 strict-config
    convention — never silently coerced via ``str(...)``, unlike a bare
    ``str(d.get(key, "") or "")`` which would turn e.g. a YAML list into the
    synthetic string ``"['x']"``)."""
    value = d.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(
            f"{where}.{key} must be a string, got {type(value).__name__}: {value!r}"
        )
    return value


def _require_str_list(d: dict[str, Any], key: str, *, where: str) -> list[str]:
    """A strictly-typed optional list-of-strings field: absent -> ``[]``, a
    non-list or a list containing a non-string element is a hard error."""
    raw = d.get(key)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{where}.{key} must be a list of strings, got {raw!r}")
    bad = [x for x in raw if not isinstance(x, str)]
    if bad:
        raise ValueError(f"{where}.{key} must be a list of strings, got {bad!r}")
    return list(raw)


def _parse_checks_list(d: dict[str, Any], *, where: str) -> list[CheckSpec]:
    """Parse an optional ``checks:`` list, shared by ``TargetSpec``/
    ``BundleSpec`` (both accept the identical shape — review finding)."""
    checks_raw = d.get("checks")
    if checks_raw is not None and not isinstance(checks_raw, list):
        raise ValueError(f"{where}.checks must be a list")
    checks: list[CheckSpec] = []
    for i, c in enumerate(checks_raw or []):
        if not isinstance(c, dict):
            raise ValueError(f"{where}.checks[{i}] must be a mapping")
        checks.append(CheckSpec.from_dict(c, where=f"{where}.checks[{i}]"))
    return checks


def _unknown_keys(d: dict[str, Any], known: set[str]) -> list[Any]:
    """``sorted(set(d) - known)``, but safe when *d* carries a non-string key
    (a bare PyYAML 1.1 ``on``/``off``/``yes``/``no`` mapping key parses as a
    bool) alongside a string one -- plain ``sorted()`` would raise ``TypeError``
    comparing ``bool``/``str`` instead of surfacing the documented ``ValueError``
    usage error (Codex finding, mirrors the top-level key check's own fix)."""
    return sorted(set(d) - known, key=repr)


def _require_mapping(data: object, block: str) -> dict[str, Any]:
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(
            f"{block} must be a mapping, got {type(data).__name__}: {data!r}"
        )
    bad_keys = [k for k in data if not isinstance(k, str)]
    if bad_keys:
        # PyYAML's default (YAML 1.1) resolver reads a bare `on`/`off`/`yes`/`no`
        # mapping key as a bool, and a bare digit key as an int -- silently
        # stringifying either here (e.g. `str(True)` -> "True") would mint a
        # target/bundle/profile/channel id the user never actually wrote.
        raise ValueError(f"{block}: key(s) must be strings, got {bad_keys!r}")
    return data


@dataclass
class CheckSpec:
    """One ``{channel, depth, required, gate_mode, profiles}`` tuple (ADR-047 §3).

    Closes the gap ADR-047 §3 flags: ``baseline: channels:`` alone declares
    which channels *exist*, not which channel/depth/policy a given target
    actually runs — this is the per-check assignment that does.
    """

    channel: str = ""
    depth: str = ""
    required: bool = True
    #: Direct-construction default is ``"local"`` (matching this field's own
    #: default); ``from_dict`` instead derives an unset ``gate_mode`` from
    #: ``channel`` — ``"advisory"`` for the ``NO_BASELINE_CHANNEL`` sentinel,
    #: ``"local"`` otherwise (ADR-047 §8 S5: "Advisory by default").
    gate_mode: str = "local"
    #: Explicit profile-id selector (see module docstring). Empty = every
    #: ``contract: true`` profile, filtered against ``build-output.json`` by
    #: G30 P1.4's run-plan generator — not resolved here.
    profiles: list[str] = field(default_factory=list)
    #: Forwarded to ``check-target``'s own ``allow-new-target`` input (and,
    #: through it, ``resolve-baseline``'s) — ``False`` (default) means a
    #: target absent from this check's otherwise-resolved baseline-set
    #: always fails ``ambiguous``; ``True`` opts this specific check into
    #: the ``new_target`` lifecycle state instead (a new library's first
    #: release checked against a baseline-set that predates it). Rejected
    #: outright on a bundle check by :func:`_check_issues` — a bundle
    #: comparison needs one coherent release where every member already
    #: coexisted, so "this member is new" has no well-defined old side (see
    #: ``abicheck.buildsource.baseline_set.resolve_bundle``'s docstring).
    allow_new_target: bool = False

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "channel": self.channel,
            "depth": self.depth,
            "required": self.required,
            "gate_mode": self.gate_mode,
        }
        if self.profiles:
            d["profiles"] = list(self.profiles)
        if self.allow_new_target:
            d["allow_new_target"] = True
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any], *, where: str) -> CheckSpec:
        known = {
            "channel",
            "depth",
            "required",
            "gate_mode",
            "profiles",
            "allow_new_target",
        }
        unknown = _unknown_keys(d, known)
        if unknown:
            raise ValueError(f"{where}: unknown key(s) {unknown}")
        channel = d.get("channel")
        if not isinstance(channel, str) or not channel:
            raise ValueError(f"{where}.channel must be a non-empty string")
        depth = d.get("depth")
        if not isinstance(depth, str) or not depth:
            raise ValueError(f"{where}.depth must be a non-empty string")
        required = d.get("required", True)
        if not isinstance(required, bool):
            raise ValueError(
                f"{where}.required must be a boolean, got "
                f"{type(required).__name__}: {required!r}"
            )
        if "gate_mode" in d:
            gate_mode = d["gate_mode"]
            if not isinstance(gate_mode, str):
                raise ValueError(
                    f"{where}.gate_mode must be a string, got "
                    f"{type(gate_mode).__name__}: {gate_mode!r}"
                )
        else:
            # ADR-047 §8: a channel: "none" no-baseline audit (S5) defaults
            # to advisory, not local -- unlike a real-channel check, it has
            # no baseline-drift verdict to gate CI on in the first place, so
            # defaulting it to a blocking gate would surprise a minimal
            # `{channel: none, depth: ...}` entry into failing CI.
            gate_mode = "advisory" if channel == NO_BASELINE_CHANNEL else "local"
        if d.get("profiles") == []:
            # `_require_str_list` can't distinguish an omitted `profiles:`
            # key from an explicit `profiles: []` -- both parse to `[]` --
            # but this field's own semantics (see the dataclass docstring)
            # treat an empty selector as "every contract profile," so a
            # config author who wrote `profiles: []` expecting "select
            # nothing" would silently get the opposite (Codex review).
            # Reject the explicit-empty spelling outright instead of
            # reinterpreting it: omit the key for "every profile," or name
            # at least one profile id.
            raise ValueError(
                f"{where}.profiles must not be an explicit empty list -- "
                "omit the key entirely to run on every contract profile, "
                "or list at least one profile id"
            )
        profiles = _require_str_list(d, "profiles", where=where)
        allow_new_target = d.get("allow_new_target", False)
        if not isinstance(allow_new_target, bool):
            raise ValueError(
                f"{where}.allow_new_target must be a boolean, got "
                f"{type(allow_new_target).__name__}: {allow_new_target!r}"
            )
        return cls(
            channel=channel,
            depth=depth,
            required=required,
            gate_mode=gate_mode,
            profiles=profiles,
            allow_new_target=allow_new_target,
        )


@dataclass
class TargetSpec:
    """One ``targets:`` entry (ADR-047 §3)."""

    id: str = ""
    kind: str = TARGET_KIND_LIBRARY
    binary_pattern: str = ""
    public_headers: list[str] = field(default_factory=list)
    bundle: str = ""
    bundle_only: bool = False
    #: ``app-consumer`` only.
    consumer_binary_pattern: str = ""
    #: ``app-consumer``/``plugin-contract`` only — the ``kind: library``
    #: target this one resolves its baseline/candidate lookup through.
    library: str = ""
    #: ``plugin-contract`` only — a ``.syms`` file (one required linker
    #: symbol per line, ``#`` comments allowed), not YAML.
    contract_file: str = ""
    checks: list[CheckSpec] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind}
        if self.kind == TARGET_KIND_LIBRARY:
            if self.binary_pattern:
                d["binary_pattern"] = self.binary_pattern
            if self.public_headers:
                d["public_headers"] = list(self.public_headers)
            if self.bundle:
                d["bundle"] = self.bundle
            if self.bundle_only:
                d["bundle_only"] = self.bundle_only
        elif self.kind == TARGET_KIND_APP_CONSUMER:
            d["consumer_binary_pattern"] = self.consumer_binary_pattern
            d["library"] = self.library
        elif self.kind == TARGET_KIND_PLUGIN_CONTRACT:
            d["contract_file"] = self.contract_file
            d["library"] = self.library
        if self.checks:
            d["checks"] = [c.to_dict() for c in self.checks]
        return d

    @classmethod
    def from_dict(cls, name: str, d: dict[str, Any]) -> TargetSpec:
        where = f"targets.{name}"
        if not isinstance(d, dict):
            raise ValueError(
                f"{where} must be a mapping, got {type(d).__name__}: {d!r}"
            )
        known = {
            "kind",
            "binary_pattern",
            "public_headers",
            "bundle",
            "bundle_only",
            "consumer_binary_pattern",
            "library",
            "contract_file",
            "checks",
        }
        unknown = _unknown_keys(d, known)
        if unknown:
            raise ValueError(f"{where}: unknown key(s) {unknown}")
        kind = d.get("kind", TARGET_KIND_LIBRARY)
        if not isinstance(kind, str) or kind not in TARGET_KINDS:
            raise ValueError(
                f"{where}.kind must be one of {sorted(TARGET_KINDS)}, got {kind!r}"
            )
        bundle_only = d.get("bundle_only", False)
        if not isinstance(bundle_only, bool):
            raise ValueError(f"{where}.bundle_only must be a boolean")
        checks = _parse_checks_list(d, where=where)
        return cls(
            id=name,
            kind=kind,
            binary_pattern=_opt_str_field(d, "binary_pattern", where=where),
            public_headers=_require_str_list(d, "public_headers", where=where),
            bundle=_opt_str_field(d, "bundle", where=where),
            bundle_only=bundle_only,
            consumer_binary_pattern=_opt_str_field(
                d, "consumer_binary_pattern", where=where
            ),
            library=_opt_str_field(d, "library", where=where),
            contract_file=_opt_str_field(d, "contract_file", where=where),
            checks=checks,
        )


@dataclass
class BundleSpec:
    """One ``bundles:`` entry (ADR-047 §3) — a release group of library targets.

    ``checks:`` (same ``{channel, depth, required, gate_mode, profiles}``
    shape as a target's — review finding, ADR-047 §5): the run-plan example
    emits a ``kind: "bundle"`` check entry alongside per-target ones (S14
    bundle-scoped analysis), and that cell needs its own baseline-channel/
    depth/gate policy just like a target's checks do — this plan's own
    ``checks:`` design note says "per target, **or per bundle**", which an
    earlier draft of this schema only implemented the target half of.
    """

    id: str = ""
    targets: list[str] = field(default_factory=list)
    checks: list[CheckSpec] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"targets": list(self.targets)}
        if self.checks:
            d["checks"] = [c.to_dict() for c in self.checks]
        return d

    @classmethod
    def from_dict(cls, name: str, d: dict[str, Any]) -> BundleSpec:
        where = f"bundles.{name}"
        if not isinstance(d, dict):
            raise ValueError(
                f"{where} must be a mapping, got {type(d).__name__}: {d!r}"
            )
        unknown = _unknown_keys(d, {"targets", "checks"})
        if unknown:
            raise ValueError(f"{where}: unknown key(s) {unknown}")
        targets = d.get("targets")
        if not isinstance(targets, list) or not targets:
            raise ValueError(f"{where}.targets must be a non-empty list of target ids")
        bad = [t for t in targets if not isinstance(t, str)]
        if bad:
            raise ValueError(f"{where}.targets must be a list of strings, got {bad!r}")
        checks = _parse_checks_list(d, where=where)
        return cls(id=name, targets=[str(t) for t in targets], checks=checks)


#: Flags that reach a compiler frontend's own plugin/response-file/spec-
#: substitution/config-file/subprocess-forwarding machinery rather than an
#: ABI-relevant compile axis. Each is a single whitespace-free atom (so
#: ``_safe_profile_atom``'s smuggling check alone does not reject it) that
#: falls into one of four families:
#:
#: - loads arbitrary code directly into the compiler process
#:   (``-Xclang``/``-load``/``-fplugin=``/``-fpass-plugin=``);
#: - re-expands attacker-controlled file content into more argv
#:   (``@response-file``; Clang's ``--config``/``--config=<file>`` -- a
#:   "configuration file" of additional command-line options, including
#:   ``-fplugin=``, so leaving it unblocked would let it re-smuggle back in
#:   everything else this denylist rejects; the ``--config`` prefix below
#:   also catches ``--config-system-dir=``/``--config-user-dir=``, which
#:   redirect where an implicit/explicit config file is looked up);
#: - substitutes the compiler driver's own trusted built-in command line
#:   for one read from disk (``-specs=``/``--specs=`` -- GCC's driver
#:   accepts both spellings, translating the GNU-style ``--specs``
#:   long-option form to ``-specs`` internally, so both must be blocked --
#:   and ``-wrapper``); or
#: - **forwards its entire payload verbatim into a different subprocess**
#:   (preprocessor/assembler/linker), which can smuggle any of the above
#:   past a prefix check on the *outer* atom alone: GCC's ``-Wa,``/
#:   ``-Wp,``/``-Wl,`` pass their comma-joined payload straight to the
#:   assembler/preprocessor/linker (confirmed via ``gcc --help``) --
#:   ``-Wp,-fplugin=./evil.so`` reaches cc1 and loads the plugin exactly as
#:   a bare ``-fplugin=`` would, and ``-Wl,-plugin=./evil.dso`` loads an
#:   LTO linker plugin the same way (confirmed via ``ld --help``); Clang's
#:   separate-argument equivalents ``-Xpreprocessor``/``-Xassembler``/
#:   ``-Xlinker`` are the same mechanism as the already-blocked
#:   ``-Xclang``, so blocking the forwarding flag itself (its target is a
#:   separate, otherwise-inert atom) is sufficient, same as ``-Xclang``.
#:
#: ``profiles.<id>.compile.args`` is documented (this module's docstring,
#: ``ProfileCompileSpec``'s own docstring) as a "normalized extra args"
#: escape hatch for ABI-relevant flags an auto-discovered, untrusted
#: ``.abicheck.yml`` may declare — never an executable-code path. Checked as
#: an exact match or a prefix (for the ``=``-/``,``-joined spellings)
#: against each ``args`` atom; case-sensitive, matching how compiler CLIs
#: themselves parse these flags. This denylist targets the *delivery
#: mechanism* (a flag whose whole job is loading code or re-expanding more
#: argv), not every dangerous flag those mechanisms could carry -- a new
#: subprocess-forwarding mechanism found later belongs here as another
#: blocked prefix, not as a growing list of the individual flags it could
#: smuggle. (Codex review, PR #639: the initial denylist
#: omitted ``--config``, the ``--specs`` double-dash spelling of
#: ``-specs``, the whole ``-Wa,``/``-Wp,``/``-Wl,``/``-Xpreprocessor``/
#: ``-Xassembler``/``-Xlinker`` subprocess-forwarding family, and
#: ``--castxml-cc-``.
#:
#: ``--castxml-cc-`` is a different case from the rest of this list: it
#: does not smuggle an already-blocked flag past a prefix check, it
#: targets the trusted ``--castxml-cc-<id> <path>`` pair
#: ``dumper_ast_config.py`` itself composes *ahead of* this denylist's
#: ``args`` -- a second occurrence naively looks like it could replace the
#: verified compiler path with an attacker-controlled one. Verified
#: empirically against the installed castxml (0.6.3) that this is not
#: actually exploitable: castxml hard-rejects any repeated
#: ``--castxml-cc-*`` occurrence at argv-parse time --
#: ``error: '--castxml-cc-<id>' may be given at most once!`` -- regardless
#: of whether the ``<id>`` matches the first occurrence, so the scan fails
#: outright rather than silently invoking a substituted binary. Blocked
#: here anyway for defense-in-depth/a clearer abicheck-level error instead
#: of relying on that castxml-internal invariant holding across every
#: supported castxml version.
#:
#: ``-B<dir>``/``-B <dir>`` is the same shape of finding as
#: ``--castxml-cc-`` above: real for the mechanism it names, but verified
#: empirically to not reach abicheck's actual pipeline as claimed. GCC's
#: ``-B<dir>`` really does add *dir* to its compiler-component search path
#: and really does execute an attacker-supplied ``cc1``/``cc1plus`` placed
#: there instead of the real one (confirmed: ``gcc -B./tools/ -E`` ran a
#: planted ``./tools/cc1``) -- but every consumer of this composed string
#: (castxml's internal bundled Clang, and the direct ``--ast-frontend
#: clang`` backend) is Clang, not GCC, and Clang has no separate,
#: ``-B``-discoverable ``cc1`` to substitute: it re-execs itself via
#: ``-cc1`` instead. Confirmed empirically that ``-B./tools/`` does not
#: run a planted ``./tools/cc1`` for either castxml or a direct ``clang -E``
#: invocation, even though ``clang -B./tools/ -print-prog-name=cc1`` shows
#: ``-B`` does influence *where Clang would look* for a tool named that.
#: Blocked anyway: cheap, and closes the door in case a future toolchain-
#: execution-contract change (AGENTS.md's "Known gaps" entry) ever forwards
#: these flags to a real GCC invocation directly, which would restore the
#: attack.
#:
#: ``/clang:<arg>`` is clang-cl's (Clang's MSVC-compatible driver mode,
#: reachable via a Windows toolchain binding whose path stem contains
#: "clang" -- e.g. ``clang-cl``/``clang-cl.exe``, which
#: ``dumper_clang._is_clang_family_binary`` recognizes) own escape hatch
#: for passing an argument straight to the underlying clang driver,
#: bypassing clang-cl's normally-MSVC-shaped option parsing entirely --
#: confirmed **actually exploitable** (unlike ``--castxml-cc-``/``-B``
#: above): ``clang --driver-mode=cl "/clang:-fplugin=./evil.so" -c t.h``
#: really does load and run the planted plugin. ``/link <options>`` is the
#: same driver's documented "forward options to the linker" escape hatch
#: -- the cl-mode spelling of the already-blocked ``-Wl,`` mechanism above
#: -- blocked for the same reason, on the same LTO-linker-plugin grounds,
#: even without a from-scratch clang-cl empirical repro of that specific
#: sub-case.
#:
#: ``-cc1``/``-cc1as`` are a different shape of finding again: Clang's
#: driver only enters its internal ``cc1``/``cc1as`` frontend mode when
#: ``-cc1``/``-cc1as`` is literally the *first* argument after the program
#: name -- confirmed empirically (``clang -c t.h -o t.o -cc1 -load
#: ./evil.so`` rejects ``-cc1`` as "unknown argument"; ``clang -I. -cc1
#: -load ./evil.so`` does too, since ``-I`` from a real header scan's
#: ``extra_includes`` already occupies that first-argument slot), but
#: ``clang -cc1 -load ./evil.so -plugin foo`` (nothing before ``-cc1``)
#: really does run the planted plugin's constructor before failing on the
#: unresolvable plugin name. This module's caller (``dumper.py``'s
#: ``_build_clang_header_command``) builds argv as ``[cc_bin, *-I dirs,
#: --sysroot, -nostdinc, *gcc_options tokens, ...]`` -- when a scan has no
#: ``extra_includes``/``sysroot``/``nostdinc`` (a header with no separate
#: ``-I`` search path, no cross sysroot), a leading ``-cc1`` in
#: ``compile.args`` genuinely lands in that first-argument slot. Once in
#: cc1 mode, ``-load``/``-fpass-plugin=`` are still blocked above, but cc1
#: mode exposes an entirely different, much larger argument namespace this
#: denylist was never designed to enumerate (Codex review found
#: ``-fcas-plugin-path``, a cc1-only flag not in every Clang build, doing
#: the identical thing) -- reject the *mode switch itself*, the same
#: "block the delivery mechanism, not every payload it could carry"
#: reasoning as ``--config`` above.
_DANGEROUS_ARG_PREFIXES = (
    "-Xclang",
    "-Xpreprocessor",
    "-Xassembler",
    "-Xlinker",
    "-load",
    "-fplugin=",
    "-fplugin-arg-",
    "-fpass-plugin=",
    "-specs=",
    "-specs",
    "--specs=",
    "--specs",
    "-wrapper",
    "--config",
    "-Wa,",
    "-Wp,",
    "-Wl,",
    "--castxml-cc-",
    "-B",
    "/clang:",
    "/link",
    "-cc1",
    "@",
)


def _reject_dangerous_arg(where: str, key: str, value: str) -> None:
    if any(value == p or value.startswith(p) for p in _DANGEROUS_ARG_PREFIXES):
        raise ValueError(
            f"{where}.{key} entry {value!r} is not allowed: it reaches a "
            "compiler plugin/response-file/spec-substitution mechanism, not "
            "an ABI-relevant compile flag — an auto-discovered "
            "profiles.compile.args cannot declare executable configuration"
        )


#: Characters ``shlex.split()`` (POSIX mode) treats specially: quoting and
#: escaping. ``run_plan._compose_gcc_options`` space-joins every atom from
#: every ``compile.*`` field (``standard``/``stdlib``/``target``/
#: ``abi_macros``/``args``) into ONE string, and the eventual consumer
#: (``dumper.py``'s ``--gcc-options`` handling) re-splits that whole string
#: with ``abicheck._compiler_options.split_gcc_options`` (plain
#: ``shlex.split(text, posix=True)`` on POSIX, real quote/backslash-run
#: parity on Windows — quote removal AND backslash escaping both apply on
#: either platform) to recover argv.
#: An atom containing a quote or backslash survives every check above
#: unchanged (neither is whitespace, and a quote-wrapped dangerous flag like
#: ``"'-fplugin=./evil.so'"`` does not start with a bare ``-fplugin=``) but
#: is not inert: POSIX shlex quote-removal reconstitutes it into the exact
#: blocked flag once the composed string is re-split, and because shlex
#: parses the WHOLE joined string in one pass, an unbalanced quote in one
#: atom can also shift where token boundaries fall in a neighboring atom.
#: Rejecting these characters in every atom (not just ``args``) keeps the
#: "one atom == one post-shlex token, independent of its neighbors"
#: invariant the denylist above and the whitespace check both already rely
#: on. (Codex review, PR #639.)
_SHLEX_UNSAFE_CHARS = frozenset("'\"\\")


def _safe_profile_atom(
    where: str, key: str, value: str, *, reject_dangerous: bool = False
) -> str:
    """Reject a compiler-option atom that could smuggle multiple flags/args.

    Mirrors ``buildsource.build_config.BuildConfig``'s own ``_safe_compile_atom``:
    a ``profiles:`` block is read from the same untrusted, auto-discovered
    ``.abicheck.yml`` as ``compile:``/``build.query`` (CLAUDE.md M1's trust
    boundary — see this module's docstring), and its ``compile.*`` overlay
    fields are documented to eventually reach real compiler argv the same way
    ``compile.std``/``compile.defines`` already do. Whitespace would let one
    YAML scalar become several argv tokens (e.g. ``"gnu++17 -Xclang -load
    ./evil.so"``), so it is rejected here at parse time regardless of whether
    a consumer resolves this field into argv yet. A quote or backslash
    character is rejected for the same reason — see
    :data:`_SHLEX_UNSAFE_CHARS`'s docstring — for every field, not just
    ``args``, since all of them land in the same shlex-re-split string.

    ``reject_dangerous`` additionally runs :func:`_reject_dangerous_arg` —
    set only for ``args`` (the one field appended to compiler argv as
    standalone flags rather than folded into a fixed ``-std=``/``-D<name>=``
    prefix, so it is the sole atom-level plugin/response-file injection
    vector; see :data:`_DANGEROUS_ARG_PREFIXES`).
    """
    if not value or any(ch.isspace() for ch in value):
        raise ValueError(f"{where}.{key} must be a single option atom, got {value!r}")
    if any(ch in _SHLEX_UNSAFE_CHARS for ch in value):
        raise ValueError(
            f"{where}.{key} must not contain quote/backslash characters, got "
            f"{value!r} — these survive re-parsing as inert but are not: the "
            "composed compile_gcc_options string is later re-split with "
            "shlex, which would reconstitute them into a different, "
            "unvalidated token"
        )
    if reject_dangerous:
        _reject_dangerous_arg(where, key, value)
    return value


@dataclass
class ProfileCompileSpec:
    """Optional ``profiles.<id>.compile`` overlay (P1 toolchain-profile audit).

    Declares the compiler/dialect/ABI contract axes a profile pins, as plain
    data — never an executable path or shell-interpretable string. Per the
    trust boundary this module's docstring and ``AGENTS.md`` "M1" describe: an
    auto-discovered (untrusted) ``.abicheck.yml`` may *declare* a compiler
    family, a version constraint, a target triple, a dialect/standard, a
    standard-library name, ABI macros, and normalized extra args — but never a
    raw executable path/command. ``binding`` is a *logical* identifier (e.g.
    ``"gcc14"``) meant to be resolved against a separately-trusted toolchain
    bindings file (an explicit ``--config``/CI-managed source), not looked up
    here — this module only validates shape, same as the rest of the file.
    The resolver itself is :func:`~.run_plan.generate_run_plan`'s
    *resolved_bindings* parameter (G30 P1.4); this dataclass stays the pure
    parse/validate layer either way.

    All fields are optional and additive; an empty ``ProfileCompileSpec`` is
    indistinguishable from an absent ``compile:`` block.

    ``frontend`` (G34 Phase B) overrides the global ``--ast-frontend``
    default for this profile's cell only -- one of the same four values
    the CLI flag accepts (``auto``/``castxml``/``clang``/``hybrid``,
    shape-validated the same way; empty means "no override, inherit the
    global default"). Applies identically whichever overlay it's set on
    (``compile:`` for the producer/artifact toolchain, ``consumer_compile:``
    for the client toolchain, G34 Phase 0) since both share this same
    dataclass.
    """

    compiler_family: str = ""
    compiler_version: str = ""
    target: str = ""
    standard: str = ""
    stdlib: str = ""
    binding: str = ""
    frontend: str = ""
    abi_macros: dict[str, str] = field(default_factory=dict)
    args: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return self == ProfileCompileSpec()

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.compiler_family:
            d["compiler_family"] = self.compiler_family
        if self.compiler_version:
            d["compiler_version"] = self.compiler_version
        if self.target:
            d["target"] = self.target
        if self.standard:
            d["standard"] = self.standard
        if self.stdlib:
            d["stdlib"] = self.stdlib
        if self.binding:
            d["binding"] = self.binding
        if self.frontend:
            d["frontend"] = self.frontend
        if self.abi_macros:
            d["abi_macros"] = dict(self.abi_macros)
        if self.args:
            d["args"] = list(self.args)
        return d

    @classmethod
    def from_dict(cls, where: str, d: dict[str, Any]) -> ProfileCompileSpec:
        if not isinstance(d, dict):
            raise ValueError(
                f"{where} must be a mapping, got {type(d).__name__}: {d!r}"
            )
        known = {
            "compiler_family",
            "compiler_version",
            "target",
            "standard",
            "stdlib",
            "binding",
            "frontend",
            "abi_macros",
            "args",
        }
        unknown = _unknown_keys(d, known)
        if unknown:
            raise ValueError(f"{where}: unknown key(s) {unknown}")

        str_fields: dict[str, str] = {}
        for key in (
            "compiler_family",
            "compiler_version",
            "target",
            "standard",
            "stdlib",
            "binding",
        ):
            if key not in d:
                continue
            value = d[key]
            if not isinstance(value, str):
                raise ValueError(f"{where}.{key} must be a string, got {value!r}")
            str_fields[key] = _safe_profile_atom(where, key, value)

        frontend = ""
        if "frontend" in d:
            value = d["frontend"]
            if not isinstance(value, str):
                raise ValueError(f"{where}.frontend must be a string, got {value!r}")
            if value not in HEADER_AST_FRONTENDS:
                allowed = ", ".join(sorted(HEADER_AST_FRONTENDS))
                raise ValueError(
                    f"{where}.frontend must be one of {{{allowed}}}, got {value!r}"
                )
            frontend = value

        abi_macros_raw = d.get("abi_macros", {})
        if not isinstance(abi_macros_raw, dict):
            raise ValueError(f"{where}.abi_macros must be a mapping")
        abi_macros: dict[str, str] = {}
        for macro_name, macro_value in abi_macros_raw.items():
            if not isinstance(macro_name, str) or not isinstance(macro_value, str):
                raise ValueError(
                    f"{where}.abi_macros entries must be string: string, "
                    f"got {macro_name!r}: {macro_value!r}"
                )
            abi_macros[_safe_profile_atom(where, "abi_macros", macro_name)] = (
                _safe_profile_atom(where, "abi_macros", macro_value)
                if macro_value
                else macro_value
            )

        args_raw = d.get("args", [])
        if not isinstance(args_raw, list):
            raise ValueError(f"{where}.args must be a list of strings")
        args: list[str] = []
        for a in args_raw:
            if not isinstance(a, str):
                raise ValueError(f"{where}.args must be a list of strings, got {a!r}")
            args.append(_safe_profile_atom(where, "args", a, reject_dangerous=True))

        return cls(
            compiler_family=str_fields.get("compiler_family", ""),
            compiler_version=str_fields.get("compiler_version", ""),
            target=str_fields.get("target", ""),
            standard=str_fields.get("standard", ""),
            stdlib=str_fields.get("stdlib", ""),
            binding=str_fields.get("binding", ""),
            frontend=frontend,
            abi_macros=abi_macros,
            args=args,
        )


#: The GitHub-hosted runner label a profile's ``os:`` routes its check cell
#: to (G34 Phase C). Before this, every cell ran on ``ubuntu-latest``
#: regardless of what the profile declared, so a ``windows`` profile could
#: not be checked natively through ``check-project.yml`` at all — the
#: mechanical gap that blocked a genuine GCC/Clang/MSVC matrix.
#:
#: ``darwin`` is accepted alongside ``macos`` because both spellings occur in
#: the wild for one platform; nothing else is guessed at.
PROFILE_RUNNER_LABEL_BY_OS = {
    "linux": "ubuntu-latest",
    "windows": "windows-latest",
    "macos": "macos-latest",
    "darwin": "macos-latest",
}

#: What a profile with no ``os:`` at all routes to — today's behaviour for
#: *every* profile, so an existing project's cells are unmoved by Phase C.
DEFAULT_PROFILE_RUNNER_LABEL = "ubuntu-latest"

#: A runner label is accepted verbatim when it names a GitHub-hosted image
#: family, so a profile already written as ``os: ubuntu-24.04`` (pinning an
#: image rather than naming a platform) keeps working. ``os:`` was a free-form,
#: never-consulted string before this phase, so anything narrower would be a
#: breaking config change dressed up as a feature.
_RUNNER_LABEL_PREFIXES = ("ubuntu-", "windows-", "macos-")


def runner_label_for_os(os_value: str) -> str | None:
    """The runner label *os_value* routes to, or ``None`` if it routes nowhere.

    ``None`` is deliberately *not* folded into
    :data:`DEFAULT_PROFILE_RUNNER_LABEL`: silently sending an ``os: freebsd``
    profile to a Linux runner would produce a green cell that checked the
    wrong platform, which is the "a job reports success having gated the
    wrong thing" failure mode ``check-project.yml``'s own guards exist to
    close. An *unset* ``os:`` is a different question and does map to the
    default — that is every existing project, and it is what they get today.
    """
    if not os_value:
        return DEFAULT_PROFILE_RUNNER_LABEL
    mapped = PROFILE_RUNNER_LABEL_BY_OS.get(os_value.lower())
    if mapped is not None:
        return mapped
    if os_value.startswith(_RUNNER_LABEL_PREFIXES):
        return os_value
    return None


def unroutable_os_message(where: str, profile_id: str, os_value: str) -> str:
    """One shared wording for an ``os:`` no runner can be derived from, so
    ``project validate`` and ``project plan`` say the same thing."""
    known = ", ".join(sorted(PROFILE_RUNNER_LABEL_BY_OS))
    return (
        f"{where}: profiles entry {profile_id!r} has os: {os_value!r}, which "
        f"does not name a platform check-project.yml can schedule a runner "
        f"for — use one of {{{known}}}, or a GitHub-hosted runner label "
        f"({', '.join(p + '…' for p in _RUNNER_LABEL_PREFIXES)}) to pin an "
        "image directly."
    )


#: Accepted ``profiles.<id>.dependency_source`` values (G34 Phase C).
#:
#: **Not the fact owner** — the root ``action.yml``'s own ``Resolve
#: dependency-source`` step is, and it validates the identical set. This
#: mirror exists so a bad value fails at ``project validate`` time rather
#: than inside a matrix cell twenty minutes later;
#: ``tests/test_project_targets_dependency_source.py`` asserts the two agree
#: so the copy cannot drift silently.
PROFILE_DEPENDENCY_SOURCES = frozenset(
    {
        "conda-forge",
        "conda-forge-gcc14",
        "conda-forge-clang20",
        "system",
        "none",
    }
)


@dataclass
class ProfileSpec:
    """One ``profiles:`` entry (ADR-047 §3) — a build-lane identity.

    ``contract: true`` (default) means this profile is an ABI contract (gets
    a baseline, gates CI); ``contract: false`` marks a test-only CI lane that
    never gets a baseline (S17's point). The optional ``compile:`` overlay
    (:class:`ProfileCompileSpec`, P1 toolchain-profile audit) declares the
    compiler/dialect/ABI-macro axes this profile pins — additive over the
    root ``compile:`` block (:class:`~abicheck.buildsource.build_config.BuildConfig`);
    a run-plan consumer is expected to merge root-then-profile, same
    precedence as every other config layer in this project.

    The optional ``consumer_compile:`` overlay (G34 Phase 0, same
    :class:`ProfileCompileSpec` shape) separates the two axes ``compile:``
    otherwise conflates: ``compile:`` is the *producer/artifact* toolchain
    the library binary was actually built with; ``consumer_compile:`` is a
    *client* toolchain a user of the library compiles their own code with
    against the public headers, when it differs from the producer (e.g. a
    ``.so`` built once with GCC 14 but contractually supporting a Clang 20
    client under a different standard/standard-library). A profile with no
    ``consumer_compile:`` behaves exactly as today — its ``compile:`` block
    doubles as the consumer's, so existing single-toolchain projects need no
    edits. Shape-validated identically to ``compile:``; a run-plan/dumper
    consumer applying it to the header-AST (L2) extraction step only is not
    yet wired (see ``docs/contribute/plans/
    g34-producer-consumer-compiler-profile-separation.md``'s Phase 0).

    ``os:`` is no longer purely informational (G34 Phase C): it selects the
    runner a ``check-project.yml`` check cell for this profile is scheduled
    on (:func:`runner_label_for_os`). ``dependency_source:`` — new in the
    same phase — selects how that cell provisions its own system
    dependencies, so a GCC-profile cell and a Clang-profile cell in one run
    can each get a matching conda environment instead of sharing whatever
    the workflow-level input happened to say.
    """

    id: str = ""
    contract: bool = True
    os: str = ""
    arch: str = ""
    #: One of :data:`PROFILE_DEPENDENCY_SOURCES`, or ``""`` to inherit the
    #: caller's own workflow-level default (which is what every profile does
    #: today).
    dependency_source: str = ""
    compile: ProfileCompileSpec | None = None
    consumer_compile: ProfileCompileSpec | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"contract": self.contract}
        if self.os:
            d["os"] = self.os
        if self.arch:
            d["arch"] = self.arch
        if self.dependency_source:
            d["dependency_source"] = self.dependency_source
        if self.compile is not None and not self.compile.is_empty:
            d["compile"] = self.compile.to_dict()
        if self.consumer_compile is not None and not self.consumer_compile.is_empty:
            d["consumer_compile"] = self.consumer_compile.to_dict()
        return d

    @classmethod
    def from_dict(cls, name: str, d: dict[str, Any]) -> ProfileSpec:
        where = f"profiles.{name}"
        if not isinstance(d, dict):
            raise ValueError(
                f"{where} must be a mapping, got {type(d).__name__}: {d!r}"
            )
        unknown = _unknown_keys(
            d,
            {
                "contract",
                "os",
                "arch",
                "dependency_source",
                "compile",
                "consumer_compile",
            },
        )
        if unknown:
            raise ValueError(f"{where}: unknown key(s) {unknown}")
        contract = d.get("contract", True)
        if not isinstance(contract, bool):
            raise ValueError(f"{where}.contract must be a boolean")
        for key in ("os", "arch"):
            if key in d and not isinstance(d[key], str):
                raise ValueError(f"{where}.{key} must be a string")
        dependency_source = ""
        if "dependency_source" in d:
            value = d["dependency_source"]
            if not isinstance(value, str):
                raise ValueError(
                    f"{where}.dependency_source must be a string, got {value!r}"
                )
            if value and value not in PROFILE_DEPENDENCY_SOURCES:
                allowed = ", ".join(sorted(PROFILE_DEPENDENCY_SOURCES))
                raise ValueError(
                    f"{where}.dependency_source must be one of {{{allowed}}}, "
                    f"got {value!r}"
                )
            dependency_source = value
        compile_spec: ProfileCompileSpec | None = None
        if "compile" in d:
            compile_spec = ProfileCompileSpec.from_dict(
                f"{where}.compile", d["compile"]
            )
        consumer_compile_spec: ProfileCompileSpec | None = None
        if "consumer_compile" in d:
            consumer_compile_spec = ProfileCompileSpec.from_dict(
                f"{where}.consumer_compile", d["consumer_compile"]
            )
        return cls(
            id=name,
            contract=contract,
            os=str(d.get("os", "") or ""),
            arch=str(d.get("arch", "") or ""),
            dependency_source=dependency_source,
            compile=compile_spec,
            consumer_compile=consumer_compile_spec,
        )

    @property
    def runner_label(self) -> str | None:
        """The runner this profile's check cells are scheduled on (G34 Phase
        C), or ``None`` when its ``os:`` names nothing schedulable — see
        :func:`runner_label_for_os`."""
        return runner_label_for_os(self.os)


@dataclass
class BaselineChannelSpec:
    """One ``baseline: channels:`` entry (ADR-047 §3/§10)."""

    id: str = ""
    source: str = ""
    asset_pattern: str = ""
    key_prefix: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"source": self.source}
        if self.asset_pattern:
            d["asset_pattern"] = self.asset_pattern
        if self.key_prefix:
            d["key_prefix"] = self.key_prefix
        return d

    @classmethod
    def from_dict(cls, name: str, d: dict[str, Any]) -> BaselineChannelSpec:
        where = f"baseline.channels.{name}"
        if not isinstance(d, dict):
            raise ValueError(
                f"{where} must be a mapping, got {type(d).__name__}: {d!r}"
            )
        unknown = _unknown_keys(d, {"source", "asset_pattern", "key_prefix"})
        if unknown:
            raise ValueError(f"{where}: unknown key(s) {unknown}")
        source = d.get("source")
        if not isinstance(source, str) or source not in BASELINE_SOURCES:
            raise ValueError(
                f"{where}.source must be one of {sorted(BASELINE_SOURCES)}, got {source!r}"
            )
        for key in ("asset_pattern", "key_prefix"):
            if key in d and not isinstance(d[key], str):
                raise ValueError(f"{where}.{key} must be a string")
        return cls(
            id=name,
            source=source,
            asset_pattern=str(d.get("asset_pattern", "") or ""),
            key_prefix=str(d.get("key_prefix", "") or ""),
        )


@dataclass
class AggregateGateSpec:
    """``aggregate: gate:`` (CLI cleanup phase two, PR 2 follow-up).

    The durable, project-owned home for the policy ``abicheck project plan``
    stamps onto the ``gate`` block of every ``run-plan.json`` it generates
    (:attr:`RunPlan.gate_missing_required`/
    :attr:`~.run_plan.RunPlan.gate_unexpected_target`) -- the same fields a
    hand-authored ``aggregate --manifest``'s own ``gate`` block carries, so
    the two entry points can never diverge in what they express. Before this,
    the only way to set this policy was ``project plan``'s own
    ``--gate-missing-required``/``--gate-unexpected-target`` flags, re-typed
    on every invocation; this block replaces both (removed, no CLI alias --
    same "no deprecation window" stance as the rest of this cleanup).

    Both fields are optional and independently settable, mirroring
    ``RunPlan``'s own "unset means no policy stated here" contract: an absent
    field is projected as absent from the generated ``run-plan.json``'s
    ``gate`` block too, so ``aggregate``'s own hard-coded defaults
    (``missing_required: fail``, ``unexpected_target: include``) apply
    exactly as they do with no ``aggregate:`` block at all.
    """

    missing_required: str | None = None
    unexpected_target: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.missing_required is not None:
            d["missing_required"] = self.missing_required
        if self.unexpected_target is not None:
            d["unexpected_target"] = self.unexpected_target
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AggregateGateSpec:
        """Validated against the same ``OnMissingRequired``/
        ``OnUnexpectedTarget`` enum vocabulary ``aggregate --manifest``'s own
        ``gate`` block is validated against (``ExpectedTargets.
        from_manifest_data``), so a bad value here is caught at ``project
        plan`` time rather than surfacing later at ``aggregate`` time from a
        run-plan.json this config produced. Imported lazily (function-local),
        the same way :mod:`abicheck.buildsource.run_plan` keeps its own
        ``..aggregate_manifest`` reference lazy, so this leaf validation
        module carries no module-level dependency on it.

        Key **absent** -> that field stays unset (``None``). Key **present**
        with an explicit YAML ``null`` is a hard error, not silently treated
        the same as absent (Codex review, fresh evidence) -- the two are
        different author intents (never mentioned this field at all, vs.
        stating it with a null value), and conflating them would let a
        malformed ``aggregate: gate: {missing_required: null}`` block
        silently fall back to the hard-coded ``fail``/``include`` defaults
        instead of failing closed on the typo. Mirrors
        :func:`abicheck.buildsource.run_plan._parse_run_plan_gate`'s
        identical key/value distinction for the projected ``run-plan.json``
        ``gate`` block this class feeds.
        """
        from ..workflows.aggregate import OnMissingRequired, OnUnexpectedTarget

        where = "aggregate.gate"
        if not isinstance(d, dict):
            raise ValueError(
                f"{where} must be a mapping, got {type(d).__name__}: {d!r}"
            )
        unknown = _unknown_keys(d, {"missing_required", "unexpected_target"})
        if unknown:
            raise ValueError(f"{where}: unknown key(s) {unknown}")
        missing_required: str | None = None
        if "missing_required" in d:
            missing_required = d["missing_required"]
            if missing_required is None:
                raise ValueError(f"{where}.missing_required must not be null")
            if not isinstance(missing_required, str) or missing_required not in {
                v.value for v in OnMissingRequired
            }:
                raise ValueError(
                    f"{where}.missing_required must be one of "
                    f"{[v.value for v in OnMissingRequired]}, got {missing_required!r}"
                )
        unexpected_target: str | None = None
        if "unexpected_target" in d:
            unexpected_target = d["unexpected_target"]
            if unexpected_target is None:
                raise ValueError(f"{where}.unexpected_target must not be null")
            if not isinstance(unexpected_target, str) or unexpected_target not in {
                v.value for v in OnUnexpectedTarget
            }:
                raise ValueError(
                    f"{where}.unexpected_target must be one of "
                    f"{[v.value for v in OnUnexpectedTarget]}, got {unexpected_target!r}"
                )
        return cls(
            missing_required=missing_required, unexpected_target=unexpected_target
        )

    def is_empty(self) -> bool:
        return self.missing_required is None and self.unexpected_target is None


@dataclass
class ProjectTargetsConfig:
    """Parsed ``targets:``/``bundles:``/``profiles:``/``baseline:``/
    ``aggregate:`` block.

    All five sub-blocks are optional; an absent block yields an empty dict
    (or ``None`` for :attr:`aggregate_gate`), matching the
    ``buildsource``-wide convention that a project not yet using G30's
    CI-integration primitives sees no behavior change at all.
    """

    targets: dict[str, TargetSpec] = field(default_factory=dict)
    bundles: dict[str, BundleSpec] = field(default_factory=dict)
    profiles: dict[str, ProfileSpec] = field(default_factory=dict)
    baseline_channels: dict[str, BaselineChannelSpec] = field(default_factory=dict)
    aggregate_gate: AggregateGateSpec | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.targets:
            out["targets"] = {k: v.to_dict() for k, v in self.targets.items()}
        if self.bundles:
            out["bundles"] = {k: v.to_dict() for k, v in self.bundles.items()}
        if self.profiles:
            out["profiles"] = {k: v.to_dict() for k, v in self.profiles.items()}
        if self.baseline_channels:
            out["baseline"] = {
                "channels": {k: v.to_dict() for k, v in self.baseline_channels.items()}
            }
        if self.aggregate_gate is not None and not self.aggregate_gate.is_empty():
            out["aggregate"] = {"gate": self.aggregate_gate.to_dict()}
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectTargetsConfig:
        """Parse the five top-level blocks out of a raw ``.abicheck.yml`` mapping.

        Structural/type errors raise ``ValueError`` immediately (ADR-043
        strict-config convention — the same treatment ``BuildConfig`` gives
        the rest of ``.abicheck.yml``). Cross-reference/semantic issues
        (unknown ``library``/``bundle`` reference, kind-specific required
        fields, identifier charset) are **not** raised here — see
        :func:`validate_project_targets`, which needs the fully-assembled
        config to check references across blocks.

        **A clean parse from this method alone does not mean the config is
        usable.** ``CheckSpec.from_dict`` only checks ``depth``/``gate_mode``/
        ``channel`` are non-empty strings here; it does *not* check ``depth``
        is one of :data:`CHECK_DEPTHS`, ``gate_mode`` is one of
        :data:`GATE_MODES`, or ``channel`` resolves to a declared baseline
        channel — those (and every other cross-reference rule) are
        :func:`validate_project_targets`'s job. Every real caller (today,
        only ``abicheck project-targets validate``) must call both in
        sequence; treating a successful ``from_dict`` alone as "this config
        is valid" will let e.g. ``depth: "banana"`` through unnoticed.

        Every key in *data* is checked against the *full* ``.abicheck.yml``
        top-level key set (:data:`~.build_config.KNOWN_TOP_LEVEL_KEYS`), not just
        this module's own five owned keys — a misspelled block (e.g.
        ``tagrets:``) would otherwise be silently ignored as an unrecognized,
        unrelated key rather than caught as the typo it is (review finding).
        Keys this module doesn't itself parse (``build``, ``severity``, ...)
        are still accepted here and simply ignored, since a real
        ``.abicheck.yml`` legitimately carries those alongside this block.
        """
        unknown_top = sorted((set(data) - KNOWN_TOP_LEVEL_KEYS), key=repr)
        if unknown_top:
            raise ValueError(f"unknown .abicheck.yml key(s) {unknown_top!r}")
        targets_raw = _require_mapping(data.get("targets"), "targets")
        bundles_raw = _require_mapping(data.get("bundles"), "bundles")
        profiles_raw = _require_mapping(data.get("profiles"), "profiles")
        baseline_raw = _require_mapping(data.get("baseline"), "baseline")
        unknown_baseline = sorted(set(baseline_raw) - {"channels"})
        if unknown_baseline:
            raise ValueError(f"baseline: unknown key(s) {unknown_baseline}")
        channels_raw = _require_mapping(
            baseline_raw.get("channels"), "baseline.channels"
        )
        aggregate_raw = _require_mapping(data.get("aggregate"), "aggregate")
        unknown_aggregate = sorted(set(aggregate_raw) - {"gate"})
        if unknown_aggregate:
            raise ValueError(f"aggregate: unknown key(s) {unknown_aggregate}")
        aggregate_gate: AggregateGateSpec | None = None
        if "gate" in aggregate_raw:
            aggregate_gate = AggregateGateSpec.from_dict(aggregate_raw["gate"])

        targets = {
            name: TargetSpec.from_dict(name, t) for name, t in targets_raw.items()
        }
        bundles = {
            name: BundleSpec.from_dict(name, b) for name, b in bundles_raw.items()
        }
        profiles = {
            name: ProfileSpec.from_dict(name, p) for name, p in profiles_raw.items()
        }
        baseline_channels = {
            name: BaselineChannelSpec.from_dict(name, c)
            for name, c in channels_raw.items()
        }
        return cls(
            targets=targets,
            bundles=bundles,
            profiles=profiles,
            baseline_channels=baseline_channels,
            aggregate_gate=aggregate_gate,
        )


def load_project_targets_config(path: Path) -> ProjectTargetsConfig:
    """Load the ``targets:``/``bundles:``/``profiles:``/``baseline:`` block from
    a ``.abicheck.yml`` at *path*.

    Tolerant of a missing/empty file (yields an all-empty config), matching
    :func:`abicheck.buildsource.build_config.load_build_config`'s same contract.
    """
    if not path.is_file():
        return ProjectTargetsConfig()
    import yaml

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read project config {path}: {exc}") from exc
    if not isinstance(raw, dict):
        return ProjectTargetsConfig()
    return ProjectTargetsConfig.from_dict(raw)


@dataclass
class ProjectTargetsValidationReport:
    """Result of :func:`validate_project_targets` (mirrors
    :class:`~.build_output.BuildOutputValidationReport`'s shape)."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def _identifier_issues(kind: str, name: str) -> list[str]:
    if not _IDENTIFIER_RE.match(name):
        return [
            f"{kind} id {name!r} is not a valid identifier — must match "
            f"{_IDENTIFIER_RE.pattern!r} (the same charset ADR-047 §7's "
            "check_id components require, so every id stays embeddable in a "
            "target@profile#baseline_channel@depth string without ambiguity)."
        ]
    return []


#: Every kind-specific "content" field a ``targets:`` entry can carry
#: (excludes ``kind``/``checks``, which every kind allows).
_ALL_KIND_FIELDS = frozenset(
    {
        "binary_pattern",
        "public_headers",
        "bundle",
        "bundle_only",
        "consumer_binary_pattern",
        "library",
        "contract_file",
    }
)
#: Which of `_ALL_KIND_FIELDS` each ``kind`` allows — the complement is each
#: kind's forbidden set, so a newly-added field is automatically forbidden
#: everywhere it isn't explicitly allowed (CodeRabbit review: a partial,
#: hand-maintained forbidden list previously let e.g. a `kind: library`
#: target silently set `library:`, or an `app-consumer` silently set
#: `bundle:`/`bundle_only:`, neither of which means anything for those kinds).
_KIND_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    TARGET_KIND_LIBRARY: frozenset(
        {"binary_pattern", "public_headers", "bundle", "bundle_only"}
    ),
    TARGET_KIND_APP_CONSUMER: frozenset({"consumer_binary_pattern", "library"}),
    TARGET_KIND_PLUGIN_CONTRACT: frozenset({"contract_file", "library"}),
}


def _forbidden_field_issues(target: TargetSpec) -> list[str]:
    allowed = _KIND_ALLOWED_FIELDS.get(target.kind, frozenset())
    issues: list[str] = []
    for name in sorted(_ALL_KIND_FIELDS - allowed):
        if getattr(target, name):
            issues.append(
                f"target {target.id!r}: kind: {target.kind} must not set {name}."
            )
    return issues


def _library_target_issues(
    config: ProjectTargetsConfig, target: TargetSpec
) -> list[str]:
    """Validate a ``kind: library`` target's own required/consistent fields."""
    issues: list[str] = []
    if not target.binary_pattern:
        issues.append(f"target {target.id!r}: kind: library requires binary_pattern.")
    if target.bundle_only and not target.bundle:
        issues.append(f"target {target.id!r}: bundle_only requires bundle to be set.")
    if target.bundle_only and target.checks:
        issues.append(
            f"target {target.id!r}: bundle_only: true target must not set "
            "its own checks: — it is checked only as a bundle member, "
            "never standalone, so a target-level check here would never "
            "run; declare it under bundles:.checks instead."
        )
    if not target.bundle:
        return issues
    declared_bundle = config.bundles.get(target.bundle)
    if declared_bundle is None:
        issues.append(
            f"target {target.id!r}: bundle {target.bundle!r} is not "
            "declared under bundles:."
        )
    elif target.id not in declared_bundle.targets:
        issues.append(
            f"target {target.id!r}: declares bundle: {target.bundle!r} "
            f"but bundles.{target.bundle}.targets does not list "
            f"{target.id!r} back — a target's own bundle: field and its "
            "membership in that bundle's targets: list must agree in "
            "both directions."
        )
    return issues


def _no_baseline_channel_issues(target: TargetSpec) -> list[str]:
    """Reject ``channel: none`` on any target kind other than ``library``.

    ``actions/check-target/validate-inputs.sh`` rejects ``baseline-channel:
    none`` for any target kind other than library -- a no-baseline audit routes
    to ``scan`` (a one-build check), which has no ``--used-by``/
    ``--required-symbols`` equivalent to scope an app-consumer/plugin-contract
    check against. Rejected at generation time rather than letting a
    validated-looking config produce a run-plan cell that check-target refuses
    with no per-cell report for aggregate to read.
    """
    if target.kind == TARGET_KIND_LIBRARY:
        return []
    return [
        f"target {target.id!r}.checks[{i}]: channel: "
        f"{NO_BASELINE_CHANNEL!r} is not supported for kind: "
        f"{target.kind!r} -- a no-baseline audit check has no "
        "--used-by/--required-symbols equivalent to scope an "
        "app-consumer/plugin-contract check against "
        "(actions/check-target/validate-inputs.sh). Use kind: "
        "library for a no-baseline audit, or set a real channel."
        for i, check in enumerate(target.checks)
        if check.channel == NO_BASELINE_CHANNEL
    ]


def _target_issues(config: ProjectTargetsConfig, target: TargetSpec) -> list[str]:
    issues = _identifier_issues("target", target.id)
    issues.extend(_forbidden_field_issues(target))
    if target.kind == TARGET_KIND_LIBRARY:
        issues.extend(_library_target_issues(config, target))
    elif target.kind == TARGET_KIND_APP_CONSUMER:
        if not target.consumer_binary_pattern:
            issues.append(
                f"target {target.id!r}: kind: app-consumer requires "
                "consumer_binary_pattern."
            )
        issues.extend(_library_reference_issues(config, target))
    elif target.kind == TARGET_KIND_PLUGIN_CONTRACT:
        if not target.contract_file:
            issues.append(
                f"target {target.id!r}: kind: plugin-contract requires contract_file."
            )
        issues.extend(_library_reference_issues(config, target))
    issues.extend(_no_baseline_channel_issues(target))
    for i, check in enumerate(target.checks):
        issues.extend(_check_issues(config, f"target {target.id!r}.checks[{i}]", check))
    return issues


def _library_reference_issues(
    config: ProjectTargetsConfig, target: TargetSpec
) -> list[str]:
    if not target.library:
        return [f"target {target.id!r}: kind: {target.kind} requires library."]
    referenced = config.targets.get(target.library)
    if referenced is None:
        return [
            f"target {target.id!r}: library {target.library!r} is not declared "
            "under targets:."
        ]
    if referenced.kind != TARGET_KIND_LIBRARY:
        return [
            f"target {target.id!r}: library {target.library!r} must be a "
            f"kind: library target, not kind: {referenced.kind!r} — "
            "app-consumer/plugin-contract targets resolve their baseline/"
            "candidate lookup through a real library target only (ADR-047 §3)."
        ]
    return []


def _check_issues(
    config: ProjectTargetsConfig,
    where: str,
    check: CheckSpec,
    *,
    is_bundle: bool = False,
) -> list[str]:
    issues: list[str] = []
    if (
        check.channel != NO_BASELINE_CHANNEL
        and check.channel not in config.baseline_channels
    ):
        issues.append(
            f"{where}: channel {check.channel!r} is not declared under "
            f"baseline.channels: (use {NO_BASELINE_CHANNEL!r} for a no-baseline "
            "audit check, ADR-047 §6 S5)."
        )
    if check.depth not in CHECK_DEPTHS:
        issues.append(
            f"{where}: depth must be one of {sorted(CHECK_DEPTHS)}, got {check.depth!r}."
        )
    if check.gate_mode not in GATE_MODES:
        issues.append(
            f"{where}: gate_mode must be one of {sorted(GATE_MODES)}, got {check.gate_mode!r}."
        )
    for profile_id in check.profiles:
        profile = config.profiles.get(profile_id)
        if profile is None:
            issues.append(
                f"{where}: profiles entry {profile_id!r} is not declared under profiles:."
            )
            continue
        if not profile.contract and check.channel != NO_BASELINE_CHANNEL:
            # contract: false profiles are documented as test-only lanes that
            # never get a baseline (S17) -- a real-channel check scoped only
            # to one can never be satisfied. A channel: "none" audit check has
            # no baseline to resolve in the first place, so it's exempt (S5
            # audits on a non-contract lane are a legitimate use case).
            issues.append(
                f"{where}: profiles entry {profile_id!r} has contract: false "
                "(a test-only lane that never gets a baseline) but this check "
                f"declares a real channel ({check.channel!r}) — only a "
                f"{NO_BASELINE_CHANNEL!r}-channel audit check may scope to a "
                "non-contract profile."
            )
        # abicheck/bundle.py's build_bundle_snapshot() skips non-ELF inputs
        # outright (baseline_set.py's _not_elf_issue), so a bundle check
        # explicitly scoped to a declared Windows/macOS profile can never
        # resolve -- it always produces an operationally-failing matrix
        # leg rather than a usable check (Codex review). An UNSET os
        # (profile.os == "") is left unrejected -- most projects never
        # bother declaring it, and treating "unspecified" as an error
        # would punish that common case for a field that's still purely
        # informational everywhere else in this module. Implicit (not
        # explicitly profile-scoped) bundle checks are handled separately
        # at run-plan generation time, where they're silently skipped
        # rather than erroring, the same way a profile that simply
        # doesn't build a given target is skipped.
        if is_bundle and profile.os and profile.os != "linux":
            issues.append(
                f"{where}: profiles entry {profile_id!r} has os: {profile.os!r}, "
                "but a bundle check's backend (abicheck/bundle.py) is ELF-only "
                "and skips every non-ELF member -- explicitly scope this bundle "
                "check to a linux profile, or drop it from profiles: and let "
                "the implicit sweep skip non-ELF profiles automatically."
            )
    if is_bundle and check.allow_new_target:
        issues.append(
            f"{where}: allow_new_target is not supported for a bundle check -- "
            "a bundle comparison needs one coherent release where every "
            "member already coexisted, so there is no well-defined old side "
            "for a member that's new (abicheck.buildsource.baseline_set."
            "resolve_bundle never returns new_target). Scope the new member "
            "individually with a channel/allow_new_target library-kind "
            "target check instead, and add it to this bundle once a real "
            "release has published a baseline-set covering every member."
        )
    return issues


def _bundle_issues(config: ProjectTargetsConfig, bundle: BundleSpec) -> list[str]:
    issues = _identifier_issues("bundle", bundle.id)
    for member in bundle.targets:
        referenced = config.targets.get(member)
        if referenced is None:
            issues.append(
                f"bundle {bundle.id!r}: target {member!r} is not declared under targets:."
            )
        elif referenced.kind != TARGET_KIND_LIBRARY:
            issues.append(
                f"bundle {bundle.id!r}: target {member!r} must be kind: library, "
                f"not kind: {referenced.kind!r}."
            )
        elif referenced.bundle and referenced.bundle != bundle.id:
            issues.append(
                f"bundle {bundle.id!r}: target {member!r} declares bundle: "
                f"{referenced.bundle!r}, not {bundle.id!r} — a target's own "
                "bundle: field and its membership here must agree."
            )
    for i, check in enumerate(bundle.checks):
        if check.depth not in BUNDLE_CHECK_DEPTHS and check.depth in CHECK_DEPTHS:
            # A depth outside CHECK_DEPTHS entirely is already reported by
            # _check_issues below -- only flag the bundle-specific
            # restriction for an otherwise-valid depth (build/source).
            issues.append(
                f"bundle {bundle.id!r}.checks[{i}]: depth {check.depth!r} is not "
                f"supported for a bundle check -- use one of "
                f"{sorted(BUNDLE_CHECK_DEPTHS)} (actions/check-target/"
                "validate-inputs.sh rejects build/source for kind: bundle, "
                "which always compares directories)."
            )
        if check.channel == NO_BASELINE_CHANNEL:
            # channel: none routes check-target to the root Action's scan
            # mode (no baseline to compare against) -- but a bundle check's
            # candidate is always a staged directory of member binaries
            # (check-project.yml's own bundle-staging step), and scan mode
            # rejects a directory/package new-library outright (Codex
            # review). There is no real bundle audit path today.
            issues.append(
                f"bundle {bundle.id!r}.checks[{i}]: channel: "
                f"{NO_BASELINE_CHANNEL!r} is not supported for a bundle "
                "check -- a bundle's candidate is always a staged directory "
                "of member binaries, and action/validate-inputs.sh rejects "
                "a directory/package new-library for scan mode (the "
                "no-baseline routing). Set a real baseline channel for a "
                "bundle check, or scope each member individually with a "
                "channel: 'none' library-kind target check instead."
            )
        issues.extend(
            _check_issues(
                config, f"bundle {bundle.id!r}.checks[{i}]", check, is_bundle=True
            )
        )
    return issues


def _profile_issues(profile: ProfileSpec) -> list[str]:
    issues = _identifier_issues("profile", profile.id)
    # G34 Phase C: `os:` now selects a runner, so a value no runner can be
    # derived from is a real misconfiguration rather than the harmless free
    # text it used to be. Caught here, at `project validate` time, so it
    # surfaces before a matrix cell exists to be scheduled wrongly.
    if profile.os and runner_label_for_os(profile.os) is None:
        issues.append(unroutable_os_message("profiles", profile.id, profile.os))
    return issues


def _baseline_channel_issues(channel: BaselineChannelSpec) -> list[str]:
    issues = _identifier_issues("baseline channel", channel.id)
    if channel.id == NO_BASELINE_CHANNEL:
        issues.append(
            f"baseline channel {channel.id!r} is reserved as the no-baseline "
            "sentinel (ADR-047 §6 S5) and cannot be declared as a real "
            "channel — a checks[].channel: 'none' entry would then be "
            "ambiguous between 'skip resolve-baseline' and 'resolve this "
            "declared channel', and check-target always takes the former."
        )
    if channel.source == "github-release" and not channel.asset_pattern:
        issues.append(
            f"baseline channel {channel.id!r}: source: github-release requires "
            "asset_pattern (ADR-047 §10)."
        )
    if channel.source == "actions-cache" and not channel.key_prefix:
        issues.append(
            f"baseline channel {channel.id!r}: source: actions-cache requires "
            "key_prefix (ADR-047 §10)."
        )
    return issues


def validate_project_targets(
    config: ProjectTargetsConfig,
) -> ProjectTargetsValidationReport:
    """Validate cross-references and kind-specific rules across the whole block.

    Never raises for a structurally-parsed :class:`ProjectTargetsConfig` —
    problems are reported, not thrown, matching
    :func:`~.build_output.validate_build_output`'s same contract. Structural/
    type errors already raised during :meth:`ProjectTargetsConfig.from_dict`.
    """
    report = ProjectTargetsValidationReport()
    if not config.targets and not config.bundles and not config.profiles:
        report.warnings.append(
            "no targets:/bundles:/profiles: declared — nothing for a G30 "
            "run-plan generator to act on yet."
        )
    for target in config.targets.values():
        report.errors.extend(_target_issues(config, target))
    for bundle in config.bundles.values():
        report.errors.extend(_bundle_issues(config, bundle))
    for profile in config.profiles.values():
        report.errors.extend(_profile_issues(profile))
    for channel in config.baseline_channels.values():
        report.errors.extend(_baseline_channel_issues(channel))
    return report
