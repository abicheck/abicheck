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

"""Environment matrix — declared deployment constraints for parameterized ABI checks.

When checking ABI compatibility for heterogeneous stacks (SYCL, CUDA), the
result depends on the deployment environment: which GPU architectures, driver
versions, and backend plugins are targeted.

The ``EnvironmentMatrix`` dataclass captures these constraints as explicit
inputs, converting "catch everything" into a checkable contract.

Usage::

    matrix = EnvironmentMatrix.from_yaml("env-matrix.yaml")
    result = compare(old, new, env_matrix=matrix)

YAML format::

    target_os: linux
    target_arch: x86_64

    compilers:
      - gcc-13
      - clang-17
    abi_version: "18"
    libstdcxx_dual_abi: cxx11

    sycl:
      implementation: dpcpp
      backends:
        - level_zero
        - opencl

    cuda:
      gpu_architectures:
        - sm_80
        - sm_90
      driver_range: ["525.0", "580.0"]
      toolkit_version: "12.4"

See ADR-020b for design rationale.

Classified ``model`` in ``architecture/modules.yaml`` (ADR-061), not
``workflows``: this module is a pure data shape plus a parser, with no
orchestration logic of its own, so it belongs in the innermost ring every
other layer may import — the layer's own comment names the one dependency
(``diff_versioning.py``'s dotted-version parser) that had to move to
``model/dotted_version.py`` first to make that legal.
"""
from __future__ import annotations

import copyreg
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .model.dotted_version import parse_dotted_numeric_version

log = logging.getLogger(__name__)


def _make_mapping_proxy(data: dict[str, str]) -> MappingProxyType[str, str]:
    """Reconstruct a ``MappingProxyType`` from pickled/deep-copied state.

    The actual reconstruction callable a ``copyreg`` reducer names must
    itself be importable by (module, qualified name) -- ``MappingProxyType``
    fails that on CPython: its ``__module__`` reports ``"builtins"`` but
    ``builtins.mappingproxy`` is not an attribute pickle can look up
    (``PicklingError: attribute lookup mappingproxy on builtins failed``),
    a genuine CPython quirk, not something specific to this module. A
    plain wrapper function defined here (a real, importable module-level
    name) sidesteps it.
    """
    return MappingProxyType(data)


def _reduce_mapping_proxy(
    proxy: MappingProxyType[str, str],
) -> tuple[Any, tuple[dict[str, str]]]:
    """``copyreg`` reducer for ``types.MappingProxyType`` (Codex review, P2,
    Finding 2).

    Python's stdlib registers no pickle support for ``MappingProxyType`` at
    all -- ``pickle.dumps(types.MappingProxyType({}))`` raises ``TypeError:
    cannot pickle 'mappingproxy' object`` unconditionally, and
    ``copy.deepcopy`` for a plain object with no ``__deepcopy__`` falls back
    to the identical ``__reduce_ex__``/``copyreg.dispatch_table`` machinery
    pickling uses, so it hits the same error. That is the *actual* root
    cause behind ``EnvironmentMatrix.runtime_floors`` (a ``MappingProxyType``
    since the immutability fix above) breaking ``copy.deepcopy``,
    ``pickle.dumps``/``loads``, and a direct ``dataclasses.asdict()`` call
    over this now-embeddable-in-``CompareRequest`` type (``asdict()``
    recurses field-by-field and falls back to ``copy.deepcopy`` for any
    field value it does not otherwise recognize as a dataclass/namedtuple/
    list/tuple/dict -- a bare ``Mapping``-typed proxy is none of those).

    Registered once, at import time, for the type itself (``copyreg.
    dispatch_table`` -- consulted by both ``pickle`` and ``copy.deepcopy``)
    rather than adding a custom ``__getstate__``/``__setstate__`` pair to
    :class:`EnvironmentMatrix` alone: a per-class override would still leave
    a *direct* ``dataclasses.asdict()`` call broken (it deep-copies each
    field's raw value, never consulting the *containing* dataclass's own
    pickle/copy protocol methods at all), and would need to be duplicated
    onto any future dataclass that also freezes a mapping field this way.
    Fixing the type once here is strictly additive: nothing in this
    process previously depended on a ``MappingProxyType`` *failing* to
    pickle/deep-copy.
    """
    return _make_mapping_proxy, (dict(proxy),)


copyreg.pickle(MappingProxyType, _reduce_mapping_proxy)


@dataclass(frozen=True)
class SyclConstraints:
    """SYCL-specific deployment constraints.

    Codex review, P2 (Finding 3): genuinely ``frozen=True`` now, not just a
    frozen *collection* field on an otherwise-mutable dataclass -- plain
    attribute reassignment (``constraints.implementation = "x"``) used to
    silently change this object's hash out from under a dict/set it was
    already inserted into, the same hash-invariant violation the
    ``backends`` tuple-freeze below was fixing for collection fields alone.
    ``__post_init__`` uses ``object.__setattr__`` to set the frozen
    ``backends`` field once, which is the standard pattern for a frozen
    dataclass that still needs to normalize a field at construction time.
    """

    implementation: str = ""              # "dpcpp" | "adaptivecpp"
    backends: tuple[str, ...] = field(default_factory=tuple)  # ("level_zero", "opencl")
    min_pi_version: str = ""              # minimum PI version required

    def __post_init__(self) -> None:
        # Codex review, P2 follow-up: a plain mutable `list` field remains
        # directly mutable after construction, which changes this object's
        # (and any containing `EnvironmentMatrix`'s) hash out from under a
        # dict/set it was already inserted into -- the classic Python
        # hash-invariant violation. Freezing into a `tuple` here, once, at
        # construction, is genuine immutability rather than merely a
        # hashable *projection* computed fresh each `__hash__` call: the
        # earlier round's "nothing currently mutates this" reasoning was
        # necessary but not sufficient, since immutability is a structural
        # guarantee, not an audit of today's call sites. `tuple(...)` also
        # accepts an already-`tuple` input unchanged, so this is idempotent
        # across repeated construction (e.g. a caller round-tripping an
        # existing instance's own `backends` back into the constructor).
        object.__setattr__(self, "backends", tuple(self.backends))

    def __hash__(self) -> int:
        # `backends` is already a tuple post-`__post_init__`, so no
        # projection is needed here beyond what `hash()` does natively.
        return hash((self.implementation, self.backends, self.min_pi_version))


@dataclass(frozen=True)
class CudaConstraints:
    """CUDA-specific deployment constraints (placeholder for future use).

    ``frozen=True`` for the same reason as :class:`SyclConstraints` above
    (Codex review, P2, Finding 3).
    """

    gpu_architectures: tuple[str, ...] = field(
        default_factory=tuple
    )  # ("sm_80", "sm_90")
    driver_range: tuple[str, str] | None = None   # (min_version, max_version)
    toolkit_version: str = ""
    require_ptx: bool = False              # require PTX for forward-compat

    def __post_init__(self) -> None:
        # See `SyclConstraints.__post_init__` above for why this freezes
        # `gpu_architectures` into a `tuple` rather than merely hashing a
        # `tuple(...)` projection of a still-mutable `list`.
        object.__setattr__(self, "gpu_architectures", tuple(self.gpu_architectures))

    def __hash__(self) -> int:
        # `gpu_architectures` is already a tuple post-`__post_init__`.
        return hash(
            (
                self.gpu_architectures,
                self.driver_range,
                self.toolkit_version,
                self.require_ptx,
            )
        )


#: Top-level keys :meth:`EnvironmentMatrix.from_dict` understands; anything
#: else is ignored with a warning in lenient mode, or rejected outright in
#: ``strict=True`` mode.
_KNOWN_KEYS = frozenset({
    "compilers", "abi_version", "libstdcxx_dual_abi",
    "sycl", "cuda", "target_os", "target_arch", "runtime_floors",
})

#: Nested ``sycl:``/``cuda:`` keys :meth:`EnvironmentMatrix.from_dict`
#: understands -- same lenient-warn/strict-reject treatment as the top-level
#: keys above, since a typo'd nested key (e.g. ``sycl.backend`` for
#: ``sycl.backends``) silently drops that whole constraint the same way a
#: mistyped top-level key would.
_KNOWN_SYCL_KEYS = frozenset({"implementation", "backends", "min_pi_version"})
_KNOWN_CUDA_KEYS = frozenset(
    {"gpu_architectures", "driver_range", "toolkit_version", "require_ptx"}
)


def _check_unknown_keys(
    data: dict[str, Any], known: frozenset[str], label: str, *, strict: bool
) -> None:
    """Handle keys of *data* outside *known*: raise in strict mode, warn otherwise.

    *label* names the section for the warning/error text (e.g.
    ``"EnvironmentMatrix"``, ``"EnvironmentMatrix.sycl"``).
    """
    unknown = set(data) - known
    if not unknown:
        return
    if strict:
        raise ValueError(f"{label}: unknown key(s) {sorted(unknown)}")
    log.warning("%s: unknown keys ignored: %s", label, unknown)


def _section_dict(data: dict[str, Any], key: str) -> dict[str, Any]:
    """Return the *key* sub-dict of *data* (default empty), validating its type."""
    section = data.get(key, {})
    if not isinstance(section, dict):
        raise ValueError(f"'{key}' must be a dict, got {type(section).__name__}")
    return section


def _parse_sycl_constraints(
    sycl_data: dict[str, Any], *, strict: bool = False
) -> SyclConstraints:
    """Parse the validated ``sycl`` section into :class:`SyclConstraints`."""
    _check_unknown_keys(sycl_data, _KNOWN_SYCL_KEYS, "EnvironmentMatrix.sycl", strict=strict)
    backends = sycl_data.get("backends", [])
    if not isinstance(backends, list):
        raise ValueError(
            f"'sycl.backends' must be a list, got {type(backends).__name__}"
        )
    return SyclConstraints(
        implementation=str(sycl_data.get("implementation", "")),
        backends=tuple(str(b) for b in backends),
        min_pi_version=str(sycl_data.get("min_pi_version", "")),
    )


def _parse_cuda_constraints(
    cuda_data: dict[str, Any], *, strict: bool = False
) -> CudaConstraints:
    """Parse the validated ``cuda`` section into :class:`CudaConstraints`."""
    _check_unknown_keys(cuda_data, _KNOWN_CUDA_KEYS, "EnvironmentMatrix.cuda", strict=strict)
    gpu_archs = cuda_data.get("gpu_architectures", [])
    if not isinstance(gpu_archs, list):
        raise ValueError(
            f"'cuda.gpu_architectures' must be a list, got {type(gpu_archs).__name__}"
        )

    driver_range_raw = cuda_data.get("driver_range")
    driver_range = None
    if isinstance(driver_range_raw, (list, tuple)) and len(driver_range_raw) == 2:
        driver_range = (str(driver_range_raw[0]), str(driver_range_raw[1]))
    elif driver_range_raw is not None:
        raise ValueError(
            f"'cuda.driver_range' must be a 2-element list [min, max], "
            f"got {driver_range_raw!r}"
        )

    require_ptx = cuda_data.get("require_ptx", False)
    if not isinstance(require_ptx, bool):
        raise ValueError(
            f"'cuda.require_ptx' must be a bool, got {type(require_ptx).__name__}"
        )

    return CudaConstraints(
        gpu_architectures=tuple(str(a) for a in gpu_archs),
        driver_range=driver_range,
        toolkit_version=str(cuda_data.get("toolkit_version", "")),
        require_ptx=require_ptx,
    )


#: runtime_floors keys whose value is not a dotted-numeric version — they
#: declare a presence flag (MUSLLINUX, WHEEL_CONTEXT) or a non-version token
#: (WHEEL_ARCH, e.g. "x86_64") rather than a floor, so the dotted-numeric
#: validation below doesn't apply to them (Codex review #583: WHEEL_ARCH
#: was unreachable via --env-matrix/from_dict entirely — every value was
#: rejected before check_wheel_tag_architecture_mismatch ever ran, since
#: only the direct-constructor path bypassing from_dict's validation could
#: set a non-numeric runtime_floors value at all).
_NON_NUMERIC_RUNTIME_FLOOR_KEYS = frozenset(
    {"WHEEL_ARCH", "MUSLLINUX", "WHEEL_CONTEXT"}
)

#: Presence-flag keys (MUSLLINUX, WHEEL_CONTEXT — unlike WHEEL_ARCH, which
#: expects an actual architecture string, not a yes/no flag) where a YAML
#: boolean or blank value is meaningful and must be honored as "disabled",
#: not silently stringified: ``str(False)`` is the non-empty string
#: ``"False"`` and ``str(None)`` is ``"None"`` — a blank YAML entry
#: (``WHEEL_CONTEXT:`` with no value, which PyYAML loads as ``None``) or an
#: explicit ``false`` both parse to a truthy string, which the downstream
#: checks' plain ``floors.get(...)`` truthiness test reads as *enabled* —
#: the exact opposite of what a blank or explicitly-disabled entry means
#: (Codex review #583).
_PRESENCE_FLAG_RUNTIME_FLOOR_KEYS = frozenset({"MUSLLINUX", "WHEEL_CONTEXT"})


def _parse_runtime_floors(floors_raw: object) -> dict[str, str]:
    """Parse and validate the ``runtime_floors`` prefix → version mapping."""
    if not isinstance(floors_raw, dict):
        raise ValueError(
            f"'runtime_floors' must be a dict of version-node prefix → "
            f"version (e.g. {{GLIBC: '2.28'}}), got {type(floors_raw).__name__}"
        )
    runtime_floors: dict[str, str] = {}
    for key, value in floors_raw.items():
        key_upper = str(key).upper()
        if key_upper in _PRESENCE_FLAG_RUNTIME_FLOOR_KEYS and (
            isinstance(value, (bool, int, float)) or value is None
        ):
            # False/blank (None)/numeric zero all mean "not enabled" — omit
            # the key entirely so downstream `floors.get(...)` truthiness
            # checks see it as not declared, rather than storing a truthy
            # string ("False"/"None"/"0") that would silently enable the
            # gate. `WHEEL_CONTEXT: 0`/`MUSLLINUX: 0` reach here as the
            # plain int 0 (not a bool), which `str(0) == "0"` — a non-empty,
            # truthy string — would otherwise pass through untouched (Codex
            # review #583).
            if value:
                runtime_floors[key_upper] = "1"
            continue
        if isinstance(value, float):
            # An unquoted YAML floor has already been lossily parsed:
            # `GLIBC: 2.40` reaches us as the float 2.4, which would
            # silently declare a *lower* floor than the user wrote.
            # Reject rather than guess (Codex review #510).
            raise ValueError(
                f"'runtime_floors.{key}' must be a quoted string version: "
                f"unquoted YAML floats lose trailing zeros "
                f"(2.40 parses as 2.4). Write {key}: \"{value}\" "
                f"with the intended digits."
            )
        if key_upper in _NON_NUMERIC_RUNTIME_FLOOR_KEYS and not isinstance(value, str):
            # WHEEL_ARCH/MUSLLINUX/WHEEL_CONTEXT are exempt from the
            # dotted-numeric check below because they carry a non-version
            # token (an architecture name or a presence flag) rather than a
            # version -- but that exemption must not become a license to
            # accept *any* type. A YAML list/mapping/lone-bool value (e.g.
            # `WHEEL_ARCH: [x86_64]`) reaching here would otherwise fall
            # through to the unconditional `str(value)` below and silently
            # become the literal string `"['x86_64']"`, which the
            # downstream architecture-mismatch detector treats as an
            # unrecognized claim and reports nothing for -- a malformed
            # config silently disabling a hard check instead of raising the
            # config error `strict=True` promises (Codex review, PR #1221).
            # Presence-flag bool/int/float/None values for MUSLLINUX/
            # WHEEL_CONTEXT are already normalized and `continue`d above,
            # so only a genuinely wrong shape (list/dict, or a bare bool/
            # numeric on WHEEL_ARCH, which isn't a presence-flag key) can
            # still reach this branch.
            raise ValueError(
                f"'runtime_floors.{key}' must be a quoted string, got "
                f"{type(value).__name__}: {value!r}"
            )
        floor = str(value)
        if key_upper not in _NON_NUMERIC_RUNTIME_FLOOR_KEYS:
            # Every dot-separated component must be purely numeric: the floor
            # contract parses with int() per component, so a "2.28-1" or "2.x"
            # would silently truncate to (2,) and flip verdicts. Reject
            # malformed text here instead (Codex review #510).
            if parse_dotted_numeric_version(floor) is None:
                raise ValueError(
                    f"'runtime_floors.{key}' must be a dotted numeric version "
                    f"(digits and dots only, e.g. '2.28'), with each component "
                    f"at most 9 digits, got {value!r}"
                )
        runtime_floors[key_upper] = floor
    return runtime_floors


@dataclass(frozen=True)
class EnvironmentMatrix:
    """Declared deployment constraints — shared across SYCL, CUDA, etc.

    When constraints are unspecified (empty), detectors emit conditional
    results (e.g., "breaking if backend X is required").
    """

    # Host toolchain
    compilers: tuple[str, ...] = field(default_factory=tuple)
    abi_version: str | None = None                    # -fabi-version value
    libstdcxx_dual_abi: str | None = None             # "cxx11" | "old"

    # Declared deployment runtime floors, keyed by ELF version-node prefix
    # (case-insensitive; normalized to upper): {"GLIBC": "2.28",
    # "GLIBCXX": "3.4.30", "CXXABI": "1.3.13"}. When set, a new symbol-version
    # requirement at or below the floor is COMPATIBLE (every declared target
    # already ships it) and one above the floor is BREAKING (a declared target
    # can no longer load the binary); unspecified prefixes keep the default
    # RISK classification. A read-only ``MappingProxyType`` (see
    # ``__post_init__``), not a plain ``dict`` -- callers still read it via
    # ``.get(...)``/``[...]``/``.items()``, all of which a mapping proxy
    # supports identically.
    runtime_floors: Mapping[str, str] = field(default_factory=dict)

    # Heterogeneous stack constraints
    sycl: SyclConstraints = field(default_factory=SyclConstraints)
    cuda: CudaConstraints = field(default_factory=CudaConstraints)

    # Target platform — None means unspecified (no assumption).
    target_os: str | None = None
    target_arch: str | None = None

    def __post_init__(self) -> None:
        """Freeze the mutable-looking fields into genuinely immutable ones.

        Codex review, P2 follow-up: the earlier round made this class
        hashable by computing a hashable *projection* of ``compilers``/
        ``runtime_floors`` inside ``__hash__`` (below), reasoning that
        nothing in this codebase mutates either field in place after
        construction. That reasoning was necessary but not sufficient --
        the Python hash contract requires an object's hash to stay stable
        for as long as it is a dict/set member, which means the fields
        feeding the hash must be *actually* immutable, not merely
        "currently unmutated." A live counterexample: once a
        ``CompareRequest`` carrying this matrix is inserted into a dict/set,
        ``matrix.runtime_floors["GLIBC"] = "2.34"`` is a perfectly legal
        mutation of a plain ``dict`` that silently changes the computed
        hash, making the object unfindable in that container afterward.

        This freezes ``compilers`` into a ``tuple`` and ``runtime_floors``
        into a ``types.MappingProxyType`` wrapping a private copy of the
        dict passed in -- a *view*, so no caller holding a reference to the
        original dict can mutate this instance's copy through it either.
        Both conversions are idempotent (a ``tuple``/``MappingProxyType``
        argument round-trips unchanged in content), so repeated
        construction from an already-frozen instance's own fields is safe.
        ``sycl``/``cuda`` freeze their own ``list`` fields the same way in
        their own ``__post_init__``.

        Codex review, P2 (Finding 3): this class is now genuinely
        ``@dataclass(frozen=True)`` -- freezing only the mutable-looking
        *collection* fields (this method's original job) left ordinary
        attribute reassignment (``matrix.target_os = "windows"``) able to
        change this object's hash out from under a dict/set it was already
        inserted into, the exact same hash-invariant violation the
        collection freeze above was fixing for ``compilers``/
        ``runtime_floors`` alone. ``object.__setattr__`` is the standard
        pattern for a frozen dataclass's own ``__post_init__`` to set a
        field once (ordinary ``self.x = ...`` would raise
        ``FrozenInstanceError`` here).
        """
        object.__setattr__(self, "compilers", tuple(self.compilers))
        object.__setattr__(
            self, "runtime_floors", MappingProxyType(dict(self.runtime_floors))
        )

    def __hash__(self) -> int:
        """A structural hash over a hashable projection of every field.

        Codex review, P2: ``CompareRequest`` is a frozen dataclass with a
        dataclass-generated ``__hash__``, and its ``env_matrix`` field holds
        an ``EnvironmentMatrix`` -- so a ``CompareRequest`` supplying real
        deployment configuration could not be hashed at all, since a plain
        (non-frozen) dataclass with ``eq=True`` gets ``__hash__`` set to
        ``None`` by default, and that unhashability propagates through any
        containing frozen dataclass's own generated ``__hash__``.

        ``compilers``/``runtime_floors`` are already frozen by
        ``__post_init__`` above (a ``tuple`` and a ``MappingProxyType``
        respectively), so this needs no further projection for those two
        fields beyond ``tuple(sorted(...))`` on ``runtime_floors.items()``
        so key insertion order never changes the hash of two mappings
        holding the same entries. ``__eq__`` is left as the
        dataclass-generated structural comparison: ``MappingProxyType``
        compares equal to another mapping (or a proxy) with the same
        entries, and ``tuple``/``tuple`` compare structurally, so two
        instances built from differently-ordered-but-equal inputs still
        compare equal via ``__eq__`` and therefore must (and do) hash equal
        here -- the hash/eq contract every hashable type must satisfy.
        """
        return hash(
            (
                self.compilers,
                self.abi_version,
                self.libstdcxx_dual_abi,
                tuple(sorted(self.runtime_floors.items())),
                self.sycl,
                self.cuda,
                self.target_os,
                self.target_arch,
            )
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, strict: bool = False) -> EnvironmentMatrix:
        """Parse from a dictionary (e.g., loaded from YAML).

        *strict* controls how an unrecognized top-level or nested
        ``sycl``/``cuda`` key is handled: lenient (the default) logs a
        warning and ignores it, preserving this method's original
        forward-compat behavior for direct typed-API/``from_yaml`` callers.
        ``strict=True`` raises :class:`ValueError` instead -- used by
        ``.abicheck.yml``'s embedded ``deployment:`` block
        (:mod:`abicheck.buildsource.build_config`), which enforces the same
        hard-error-on-unknown-key contract every other ``.abicheck.yml``
        block does (ADR-043): a typo there (e.g. ``runtime_floor`` for
        ``runtime_floors``) would otherwise silently disable the whole
        runtime-floor check rather than failing to load.

        Raises:
            TypeError: If *data* is not a dict.
            ValueError: If field types are wrong, or (``strict=True``) an
                unknown key is present.
        """
        if not isinstance(data, dict):
            raise TypeError(
                f"EnvironmentMatrix expects a dict, got {type(data).__name__}"
            )

        _check_unknown_keys(data, _KNOWN_KEYS, "EnvironmentMatrix", strict=strict)

        sycl_data = _section_dict(data, "sycl")
        cuda_data = _section_dict(data, "cuda")

        compilers = data.get("compilers", [])
        if not isinstance(compilers, list):
            raise ValueError(f"'compilers' must be a list, got {type(compilers).__name__}")

        sycl = _parse_sycl_constraints(sycl_data, strict=strict)
        cuda = _parse_cuda_constraints(cuda_data, strict=strict)
        runtime_floors = _parse_runtime_floors(data.get("runtime_floors", {}))

        return cls(
            compilers=tuple(compilers),
            abi_version=data.get("abi_version"),
            libstdcxx_dual_abi=data.get("libstdcxx_dual_abi"),
            runtime_floors=runtime_floors,
            sycl=sycl,
            cuda=cuda,
            target_os=data.get("target_os"),
            target_arch=data.get("target_arch"),
        )

    @classmethod
    def from_dict_or_none(
        cls, data: object, *, strict: bool = False
    ) -> EnvironmentMatrix | None:
        """Parse an *optional* embedded block, e.g. ``.abicheck.yml``'s
        ``deployment:`` key (:mod:`abicheck.buildsource.build_config`).

        Returns ``None`` when *data* is absent or not a mapping -- the
        config-key "unset" state -- instead of raising; a present-but-wrong
        *type* (a list, a string, ...) is still a genuine schema error, which
        is exactly what ``build_config_schema.deployment_findings()`` checks
        for and reports as a usage error before this is ever called.
        :meth:`from_dict` itself still raises on a *malformed dict*.

        This is the "is there one at all" glue every embedding caller would
        otherwise duplicate around a bare :meth:`from_dict` call -- moved
        here (rather than staying a few lines of ``isinstance`` glue inside
        ``BuildConfig.from_dict``) per this file's own architecture-debt
        entry (``architecture/debt.yaml``): a property of the *matrix's own
        parse*, not something a caller should re-derive.
        """
        if not isinstance(data, dict):
            return None
        return cls.from_dict(data, strict=strict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize back to the ``EnvironmentMatrix`` YAML/dict shape.

        Round-trips via :meth:`from_dict` — only non-default fields are
        emitted, matching :class:`~abicheck.buildsource.build_config.
        BuildConfig.to_dict`'s own "minimal, round-trippable" convention.
        This is what lets ``BuildConfig``'s ``deployment:`` block (ADR-020b
        §4.1) embed this shape inline in ``.abicheck.yml`` and dump it back
        out unchanged, now that ``compare --env-matrix FILE`` has been
        demoted to that config key.
        """
        out: dict[str, Any] = {}
        if self.compilers:
            out["compilers"] = list(self.compilers)
        if self.abi_version is not None:
            out["abi_version"] = self.abi_version
        if self.libstdcxx_dual_abi is not None:
            out["libstdcxx_dual_abi"] = self.libstdcxx_dual_abi
        if self.runtime_floors:
            out["runtime_floors"] = dict(self.runtime_floors)
        if self.sycl.implementation or self.sycl.backends or self.sycl.min_pi_version:
            sycl: dict[str, Any] = {}
            if self.sycl.implementation:
                sycl["implementation"] = self.sycl.implementation
            if self.sycl.backends:
                sycl["backends"] = list(self.sycl.backends)
            if self.sycl.min_pi_version:
                sycl["min_pi_version"] = self.sycl.min_pi_version
            out["sycl"] = sycl
        if (
            self.cuda.gpu_architectures
            or self.cuda.driver_range is not None
            or self.cuda.toolkit_version
            or self.cuda.require_ptx
        ):
            cuda: dict[str, Any] = {}
            if self.cuda.gpu_architectures:
                cuda["gpu_architectures"] = list(self.cuda.gpu_architectures)
            if self.cuda.driver_range is not None:
                cuda["driver_range"] = list(self.cuda.driver_range)
            if self.cuda.toolkit_version:
                cuda["toolkit_version"] = self.cuda.toolkit_version
            if self.cuda.require_ptx:
                cuda["require_ptx"] = self.cuda.require_ptx
            out["cuda"] = cuda
        if self.target_os is not None:
            out["target_os"] = self.target_os
        if self.target_arch is not None:
            out["target_arch"] = self.target_arch
        return out

    @staticmethod
    def dump_or_empty(matrix: EnvironmentMatrix | None) -> dict[str, Any]:
        """:meth:`to_dict` for an *optional* matrix -- ``{}`` when unset.

        The serialization-side counterpart of :meth:`from_dict_or_none`
        above: ``BuildConfig``'s own ``deployment:`` block round-trips a
        ``self.deployment: EnvironmentMatrix | None`` field, and this is the
        one-line glue that used to be its own private ``_deployment_block``
        method there.
        """
        return {} if matrix is None else matrix.to_dict()

    @classmethod
    def from_yaml(cls, path: Path) -> EnvironmentMatrix:
        """Load from a YAML file.

        Malformed YAML raises :class:`ValueError` (like the shape errors from
        :meth:`from_dict`), so callers need not depend on the ``yaml`` package
        for their error handling.
        """
        import yaml

        with open(path, encoding="utf-8") as f:
            try:
                data = yaml.safe_load(f) or {}
            except yaml.YAMLError as exc:
                raise ValueError(f"malformed YAML: {exc}") from exc
        return cls.from_dict(data)
