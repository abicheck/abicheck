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


"""ADR-061 D9 taxonomy: build-evidence-level ChangeKind entries.

Facts sourced from the optional L3 build-evidence layer (compiler flags,
compile context, toolchain/language-standard floors) and from
release/package-level evidence: bundle (multi-library release) coherence,
wheel/NumPy packaging and platform-tag facts, and header/binary
build-context reconciliation that stays scoped to *which build produced
which artifact* rather than to the source declarations or binary symbols
themselves (that reconciliation is ``source.py``'s territory).

Categorized by which detector module actually produces each kind (verified
against the real ``ChangeKind.X`` construction sites in
``buildsource/build_diff.py``, ``diff_build_config.py``,
``diff_wheel_deployment.py``, ``diff_numpy_capi.py``, ``bundle.py``
(bundle_* kinds only -- that module also references many unrelated kinds
incidentally for its own cross-library rollup, which this categorization
does not follow), and ``buildsource/crosscheck.py``'s two build-context-
reconciliation kinds specifically -- not by which flat
``change_registry_*.py`` sibling an entry happened to live in for pure
line-count reasons before this migration.
"""

from __future__ import annotations

from .registry import ChangeKindMeta, Verdict

_B = Verdict.BREAKING
_C = Verdict.COMPATIBLE
_A = Verdict.API_BREAK
_R = Verdict.COMPATIBLE_WITH_RISK
_E = ChangeKindMeta

BUILD_ENTRIES: list[ChangeKindMeta] = [
    _E("abi_relevant_build_flag_changed", _R,
       impact="An ABI-affecting compiler/build option changed (e.g. -std, "
              "-fabi-version, _GLIBCXX_USE_CXX11_ABI, -fvisibility, -fpack-struct, "
              "--target/-mabi, sysroot). The artifact diff decides whether the "
              "shipped ABI actually broke; this flags the elevated risk and "
              "localizes the cause for review."),
    _E("behavioural_default_changed", _R,
       impact="A documented default value changed without altering any "
              "signature — e.g. the default device selector, the default "
              "execution backend, or the default policy. Source compiles "
              "and links unchanged; runtime behaviour silently differs. "
              "Read from the probe manifest's `defaults:` section."),
    _E("build_context_changed", _C,
       impact="Non-ABI-relevant build metadata changed between versions (e.g. "
              "include-path ordering, output paths, or generator version). "
              "Informational quality signal; no ABI impact on its own."),
    _E("bundle_intra_dep_removed", _B,
       impact="A sibling library in this bundle still imports a symbol that no "
              "library in the new bundle exports. Loading the consumer will fail "
              "with undefined symbol at runtime."),
    _E("bundle_intra_dep_resolved_to_different_version", _R,
       impact="A sibling import that previously resolved to one symbol version "
              "now resolves to a different version in the new bundle (gnu.version_r "
              "drift). Compatible at the linker level but the underlying ABI of "
              "that version may differ."),
    _E("bundle_intra_dep_signature_changed", _B,
       impact="A sibling library imports a symbol whose provider changed its "
              "DWARF signature (parameters or return type) while keeping the same "
              "mangled name (typical of extern \"C\" or weak boundaries). The "
              "linker resolves the symbol but the calling convention is wrong; "
              "callers pass arguments with the old layout, callee reads the new."),
    _E(
        "bundle_intra_dep_signature_unverified",
        _R,
        impact="A sibling library's undefined import resolves by name to a "
        "provider's export -- the same C-linkage match that would "
        "otherwise confirm a real signature change -- but one or "
        "both sides lack DWARF/header type evidence for this exact "
        "symbol (a stripped provider, or a provider only ever "
        "dumped at L0). Binary-name-compatible: the linker resolves "
        "the symbol. Whether ABI compatibility actually still holds "
        "is unconfirmed, not proven safe.",
        description_template="{name} calls a symbol {detail} still exports by name, but one or both sides lack type evidence to confirm the signature agrees.",
    ),
    _E("bundle_intra_type_changed", _B,
       impact="A type defined in one library of this bundle is used in the public "
              "ABI of a sibling library, and its layout changed. The sibling's "
              "ABI looks unchanged on its own, but every cross-DSO call that "
              "passes the type by value or reads its fields is now miscompiled."),
    _E("bundle_library_added", _C, is_addition=True,
       impact="A new library appears in the bundle; existing consumers unaffected."),
    _E("bundle_library_removed", _B,
       impact="A library present in the old bundle is absent in the new bundle "
              "and at least one of its exported symbols was consumed by a sibling. "
              "Loading any consumer fails with NEEDED-library-not-found."),
    _E("bundle_manifest_instantiation_added", _C, is_addition=True,
       impact="A symbol present in the new manifest is not in the old one; "
              "new instantiation now publicly promised."),
    _E("bundle_manifest_instantiation_removed", _B,
       impact="A symbol listed in the supplied --manifest as a public ABI "
              "promise is not exported by any library in the new bundle. "
              "Consumers of the previously-promised template instantiation will "
              "fail to link or load."),
    _E("bundle_provider_changed", _R,
       impact="A symbol moved from one library in this bundle to another. "
              "Downstream binaries that had DT_NEEDED on the old provider may "
              "still resolve transitively through the bundle's link graph, or "
              "may not — depends on whether the consumer's existing dependency "
              "chain reaches the new provider."),
    _E("bundle_unresolved_intra_dependency", _R,
       impact="Audit-mode (scan --artifact-set, no old side): a library in this "
              "artifact set imports a symbol that no library in the set exports, "
              "and the import is not covered by the declared or built-in "
              "system-provider allow-list. Unlike bundle_intra_dep_removed this "
              "is not diff-confirmed (there is no old side to compare against), "
              "so it is reported as a risk rather than a confirmed break — the "
              "symbol may be satisfied by a dependency outside the declared set."),
    _E(
        "bundle_variant_coverage_regressed",
        _R,
        impact="A build variant present in the old release's variant set "
        "(e.g. the CPU-only build alongside an ONEDAL_DATA_PARALLEL/"
        "DPC build) has no matching variant in the new release. This "
        "is a build-coverage gap, not by itself proof the missing "
        "variant's ABI broke -- it may have been dropped from the "
        "release intentionally -- but a consumer pinned to that "
        "variant can no longer be evaluated and needs to see the "
        "gap.",
        description_template="Build variant '{name}' present in the old release has no matching variant in the new release ({detail}).",
    ),
    _E(
        "compile_context_conflict",
        _R,
        impact="Two or more L3 compile units attributed to the same build target "
        "carry conflicting ABI-relevant compile contexts — e.g. one unit "
        "built -frtti and another -fno-rtti (or -fexceptions vs "
        "-fno-exceptions), or the same preprocessor define bound to two "
        "different values. Aggregating them into one build context (as a "
        "synthetic public-consumer TU, or by first-match wins) silently "
        "picks one and drops the other, so the recorded L3/L4 facts may "
        "describe a build the shipped library never used (AC-008). A "
        "source-tooling risk, never an artifact-proven ABI break: scope the "
        "evidence to a single build target / link unit (or pass an explicit "
        "compile-DB filter) so one coherent context feeds the analysis.",
    ),
    _E("cxx_standard_floor_raised", _A,
       impact="The library's minimum required C++ standard increased "
              "between releases (e.g. C++17 → C++20). Consumers still "
              "building with the old standard no longer get a working "
              "header set; standard-library facilities removed in newer "
              "standards (e.g. std::result_of) may also disappear from "
              "the API surface.",
       description_template="C++ standard floor raised from {old} to {new}. Consumers still building with the old standard get a degraded or non-functional API surface."),
    _E("generated_file_dependency_unstable", _R,
       impact="The build graph indicates a generated-file dependency risk "
              "(e.g. missing or unstable generator dependencies). Generated "
              "public declarations may differ from what was analyzed; rebuild "
              "determinism is not guaranteed."),
    _E(
        "header_binary_context_mismatch",
        _R,
        impact="A record's header-AST declaration and its DWARF debug-info "
        "counterpart could not be corroborated as the same declaration "
        "(disagreeing kind, or no field/base overlap) even though a "
        "uniquely-named DWARF candidate existed. That record's layout is "
        "not backfilled and stays header-only/incomplete rather than "
        "merged, so no incorrect size/offset data reaches this "
        "comparison -- but any real layout change on that specific "
        "record is invisible to this analysis. Re-dump with matching "
        "compile context, or investigate the named record(s) directly "
        "(see the snapshot's dwarf_layout_coherence_mismatches).",
    ),
    _E("header_build_context_mismatch", _A,
       impact="The public headers were parsed without the build's ABI-relevant "
              "context (the L3 build evidence records ABI-affecting flags/macros, but "
              "the header AST was captured context-free). The declared API surface may "
              "therefore not match what the shipped translation units actually "
              "compile to (e.g. a macro-conditional field or a packing pragma is "
              "evaluated differently). Re-dump the headers with the build's "
              "compile_commands.json so the L2 surface reflects the real build."),
    _E("header_parse_context_drift", _R,
       impact="The public-header AST was parsed under a different context (flags, "
              "defines, include paths) than the real build used. Header-derived "
              "API facts may be unreliable; align the parse context (e.g. via "
              "compile_commands.json) to restore confidence."),
    _E("layer_coverage_asymmetric", _R,
       impact="The base snapshot was analyzed with evidence layers the target "
              "lacks (e.g. debug info, build context, or source ABI). The "
              "comparison is scoped to the layers both sides share, so changes "
              "only the missing layers could prove are not reported. Re-scan "
              "the target with the same inputs to restore full coverage."),
    _E("link_export_policy_changed", _R,
       impact="The export policy changed — version script, export map, or .def "
              "file. The set of exported symbols may have shifted. When this "
              "actually removes or alters exports, the artifact diff (L0) emits "
              "the corresponding BREAKING findings separately; this kind explains "
              "and localizes them and does not escalate on its own."),
    _E(
        "macos_deployment_target_raised",
        _R,
        impact="The binary's own Mach-O minimum-OS load command "
        "(LC_VERSION_MIN_MACOSX/LC_BUILD_VERSION) exceeds the macOS "
        "deployment target promised by the wheel's platform tag (e.g. "
        "macosx_10_9_x86_64) or an explicit --env-matrix declaration — "
        "the macOS counterpart of G10's manylinux glibc-floor check. "
        "Existing installs on the tag's promised deployment target can "
        "refuse to load the binary (dyld enforces the minimum-OS load "
        "command at load time), or exhibit undefined behavior calling "
        "into SDK symbols introduced after the promised floor.",
        description_template="macOS deployment target exceeded: binary requires {new}, declared target promises at most {old} (required by: {name})",
    ),
    _E(
        "musllinux_glibc_dependency_detected",
        _B,
        impact="The binary is claimed musllinux-compatible (PEP 656 — "
        "runs on musl libc, e.g. Alpine) but shows glibc evidence: a "
        "GLIBC_*-versioned symbol requirement (including the synthetic "
        "GLIBC_ABI_DT_RELR marker), a direct DT_NEEDED dependency on a "
        "glibc-only SONAME (libc.so.6, libm.so.6, ...), or glibc's own "
        "dynamic linker as PT_INTERP: glibc's own libc.so.6/loader "
        "symbol-versioning namespace doesn't exist on a musl system at "
        "all. (GLIBCXX_*/CXXABI_* alone are not flagged here — a musl "
        "system's libstdc++ can legitimately carry its own such verneed "
        "entries; see "
        "diff_versioning.check_musllinux_glibc_dependency's docstring.) "
        "This is not a version mismatch abicheck can rate as a deployment "
        "risk, it is a dependency that doesn't exist on the target — the "
        "dynamic loader fails to resolve the glibc-flavoured shared object "
        "outright. Rebuild against a musl toolchain (e.g. the musllinux "
        "manylinux-equivalent Docker images) rather than relinking a glibc "
        "build under the musllinux tag.",
        description_template="musllinux-tagged binary requires glibc: {new} (required by: {name})",
    ),
    _E(
        "numpy_abi_major_incompatible",
        _B,
        impact="The binary's NumPy C-API target crosses the NumPy 1.x/2.x "
        "ABI boundary (NumPy 2.0 changed the ABI: a module built "
        "against it does not load against a NumPy 1.x runtime), but "
        "the declared numpy requirement still allows a NumPy 1.x "
        "runtime. This is not just a stale metadata claim — installing "
        "the declared floor produces a hard import crash, not merely a "
        "missing API surface.",
        description_template="NumPy C-API target ({new}) requires NumPy >= 2.0, but declared requirement ({old}) still allows NumPy 1.x",
    ),
    _E(
        "numpy_capi_consumption_added",
        _R,
        impact="The module started consuming the NumPy C-API "
        "(_ARRAY_API/_UFUNC_API, populated by import_array()/"
        "import_ufunc()) — a runtime dependency ordinary symbol-table "
        "diffing cannot see, since the API is consumed through an "
        "indirect function-pointer capsule table, not ordinary dynamic "
        "symbol imports. Verify the wheel/package metadata now "
        "declares a numpy runtime dependency; if it doesn't, users "
        "without numpy installed get an ImportError this diff never "
        "flagged.",
        description_template="Module now consumes the NumPy C-API: {detail}",
    ),
    _E(
        "numpy_capi_consumption_removed",
        _C,
        impact="The module stopped consuming the NumPy C-API. A dependency "
        "reduction; existing consumers with numpy installed are "
        "unaffected.",
        description_template="Module no longer consumes the NumPy C-API",
    ),
    _E(
        "numpy_metadata_understates_required_version",
        _R,
        impact="The wheel/package's declared numpy requirement is looser "
        "than (or absent relative to) the binary's own NumPy C-API "
        "target version recovered from binary evidence. A user who "
        "installs the oldest numpy the metadata nominally allows gets "
        "a NumPy C-API version mismatch at import time despite pip "
        "reporting a satisfied dependency.",
        description_template="Declared numpy requirement ({old}) understates the binary's own NumPy C-API target ({new})",
    ),
    _E(
        "numpy_target_floor_raised",
        _R,
        impact="The module's compiled-in NumPy C-API usage now targets a "
        "newer minimum NumPy release (NPY_TARGET_VERSION, recovered "
        "from the module's own import_array() failure-message string) "
        "than the previous build. Runtimes with the old, lower NumPy "
        "that worked before can now fail to import this module.",
        description_template="NumPy C-API target floor raised: {old} → {new}",
    ),
    _E("runtime_floor_raised", _R,
       impact="The maximum symbol version this binary requires from a provider "
              "library rose (e.g. GLIBC_2.28 → GLIBC_2.34). The binary is "
              "interface-identical for existing consumers but no longer loads on "
              "runtimes older than the new floor — a deployment-envelope change, "
              "typically caused by rebuilding/relinking on a newer distro or "
              "sysroot rather than by a source change. Check the listed symbols: "
              "a floor pulled up only by symbols like __libc_start_main is a pure "
              "relink artifact; a new API symbol means the code now genuinely "
              "depends on the newer runtime.",
       description_template="Runtime floor raised for {detail}: {old} → {new} (required by: {name})"),
    _E("stdlib_debug_mode_changed", _R,
       impact="A standard-library debug/hardening mode was toggled between builds "
              "(_GLIBCXX_DEBUG / _GLIBCXX_ASSERTIONS for libstdc++, "
              "_ITERATOR_DEBUG_LEVEL for the MSVC STL). These modes change the "
              "layout and size of std:: containers (extra debug members / iterator "
              "bookkeeping), so any public type embedding a std:: container by "
              "value, or a function taking one across the boundary, is "
              "ABI-incompatible between a debug-mode build and a normal one. Build "
              "the library and its consumers with the matching setting."),
    _E("toolchain_version_changed", _R,
       impact="The compiler, standard library, or sysroot/SDK changed between "
              "versions. Layout, mangling, and codegen can shift even with "
              "identical sources; review for ABI-affecting toolchain drift."),
    _E(
        "wheel_closure_dependency_violation",
        _B,
        impact="A DT_NEEDED entry matches the auditwheel/delocate vendored "
        "content-hash naming convention (the same pattern G9's vendored-"
        "library pairing recognizes) — a strong signal the binary is meant "
        "to load a bundled dependency — but the binary carries no "
        "$ORIGIN-relative RPATH/RUNPATH entry at all, so the dynamic "
        "loader has no mechanism to ever find it. The vendored library is "
        "not actually part of the wheel's resolvable dependency closure "
        "regardless of whether the file itself was physically included; "
        "the binary fails to load with an unresolved-dependency error.",
        description_template="Vendored dependency unresolvable (no $ORIGIN-relative RPATH/RUNPATH): {new} (binary: {name})",
    ),
    _E(
        "wheel_rpath_not_portable",
        _R,
        impact="The binary's RPATH/RUNPATH carries an entry that isn't "
        "$ORIGIN-relative — almost always a build-machine artifact (the "
        "build sysroot, a CI runner's checkout path, a developer's local "
        "prefix) that will not exist once the wheel is installed to an "
        "arbitrary per-user site-packages path. auditwheel/delocate exist "
        "specifically to rewrite RPATH/RUNPATH to $ORIGIN-relative paths; "
        "a wheel that skipped that repair step ships a search path that "
        "resolves nothing on a clean install.",
        description_template="RPATH/RUNPATH not $ORIGIN-relative: {new} (binary: {name})",
    ),
    _E(
        "wheel_tag_architecture_mismatch",
        _B,
        impact="The wheel's platform tag names exactly one CPU architecture "
        "(e.g. manylinux_2_17_x86_64, macosx_11_0_arm64), but the contained "
        "binary's own ELF e_machine / Mach-O cpu_type records a different "
        "one. This is not a deployment-envelope risk — the wheel simply "
        "cannot be loaded on the architecture it claims to support at all. "
        "Typically a packaging/CI mistake (wrong cross-compilation target, "
        "mismatched build matrix leg, or a stale artifact reused under the "
        "wrong tag).",
        description_template="Wheel tag claims architecture {old}, binary is {new} (required by: {name})",
    ),
]
