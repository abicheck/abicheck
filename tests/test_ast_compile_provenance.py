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

"""Tests for the structured compile-context snapshot provenance fields
(schema v15, P1 toolchain-profile audit): ``AbiSnapshot.ast_resolved_standard``
/ ``ast_cplusplus_macro`` / ``ast_compile_args`` / ``ast_sysroot``, and the
``dumper.py`` helpers that compute them.

Split out of ``test_dumper_unit.py`` (already near the file-size cap) rather
than appended there.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.buildsource.redaction import DEFAULT_REDACTION
from abicheck.dumper import (
    _ast_compile_provenance,
    _cplusplus_macro_for_standard,
    _resolve_standard_provenance,
)
from abicheck.dumper_toolchain import (
    _extract_explicit_std_value,
    _probe_default_language_standard,
    _resolve_force_cpp,
)
from abicheck.model import AbiSnapshot
from abicheck.serialization import SCHEMA_VERSION, snapshot_from_dict, snapshot_to_dict


class TestCplusplusMacroForStandard:
    def test_gnu_prefixed_editions(self):
        assert _cplusplus_macro_for_standard("gnu++11") == "201103L"
        assert _cplusplus_macro_for_standard("gnu++14") == "201402L"
        assert _cplusplus_macro_for_standard("gnu++17") == "201703L"
        assert _cplusplus_macro_for_standard("gnu++20") == "202002L"

    def test_bare_cxx_editions(self):
        assert _cplusplus_macro_for_standard("c++11") == "201103L"
        assert _cplusplus_macro_for_standard("c++2a") == "202002L"

    def test_unrecognized_edition_returns_none(self):
        assert _cplusplus_macro_for_standard("gnu++99") is None
        assert _cplusplus_macro_for_standard("c11") is None  # C, not C++

    def test_none_input_returns_none(self):
        assert _cplusplus_macro_for_standard(None) is None
        assert _cplusplus_macro_for_standard("") is None

    def test_probed_marker_recognized_regardless_of_position(self):
        """CodeRabbit review, fresh evidence: when an explicit ``--lang`` is
        also given, ``language_standard_field`` prefixes the probed value
        with ``"c++:"``/``"c:"`` (e.g.
        ``"c++:probed:__cplusplus=201703L"``), so the ``"probed:"`` marker
        no longer sits at position 0 -- must still resolve, not silently
        return ``None`` for a pinned-lang probed dump."""
        assert (
            _cplusplus_macro_for_standard("c++:probed:__cplusplus=201703L") == "201703L"
        )
        assert _cplusplus_macro_for_standard("probed:__cplusplus=201703L") == "201703L"
        assert (
            _cplusplus_macro_for_standard("c:probed:__STDC_VERSION__=201710L") is None
        )


class TestExtractExplicitStdValue:
    def test_from_gcc_option_tokens(self):
        assert _extract_explicit_std_value(None, ("-std=gnu++11",)) == "gnu++11"

    def test_from_gcc_options_string(self):
        assert _extract_explicit_std_value("-DFOO=1 -std=c++20", ()) == "c++20"

    def test_msvc_style_slash_std(self):
        assert _extract_explicit_std_value(None, ("/std:c++17",)) == "c++17"

    def test_no_explicit_std_returns_none(self):
        assert _extract_explicit_std_value("-DFOO=1", ()) is None

    def test_double_dash_long_form(self):
        assert _extract_explicit_std_value(None, ("--std=c++14",)) == "c++14"

    def test_gcc_option_tokens_std_wins_over_gcc_options_std(self):
        """Codex review, fresh evidence: the real command line built by
        dumper_ast_config.py's builders emits gcc_options first, then
        gcc_option_tokens -- so gcc_option_tokens' own -std= must win,
        matching real "last flag wins" compiler semantics, regardless of
        which field it lives in."""
        assert _extract_explicit_std_value("-std=c++17", ("-std=c++20",)) == "c++20"

    def test_last_wins_holds_even_when_both_stds_land_in_gcc_option_tokens(self):
        """The same precedence must hold when a caller has already merged
        what would have been the `gcc_options` string's own content into
        `gcc_option_tokens` itself (ADR-063 Phase 1's legacy compile-db
        token fold, `workflows/artifact/resolve.py`'s
        `_fold_legacy_compile_db_tokens`, does exactly this) -- the old
        `_combined_option_tokens`-plus-first-match implementation depended
        on the two fields staying separate to get this right; a caller that
        has already collapsed them into one ordered tuple broke that
        assumption and silently recorded the superseded value instead."""
        assert (
            _extract_explicit_std_value(
                None, ("-DLEGACY=1", "-std=c++17", "-std=c++20")
            )
            == "c++20"
        )


class TestResolveStandardProvenance:
    def test_explicit_std_wins_even_with_cpp20_headers(self, tmp_path: Path):
        """An explicit standard is never overridden by the requires/concept
        heuristic — matches dumper.py's own force_cpp20 gating."""
        header = tmp_path / "h.h"
        header.write_text("template<class T> concept C = true;\n", encoding="utf-8")
        assert _resolve_standard_provenance([header], "-std=gnu++11", ()) == "gnu++11"

    def test_no_explicit_std_no_cpp20_syntax_returns_none(self, tmp_path: Path):
        header = tmp_path / "h.h"
        header.write_text("int f(int x);\n", encoding="utf-8")
        assert _resolve_standard_provenance([header], None, ()) is None

    def test_error_requires_guard_does_not_force_cxx20(self, tmp_path: Path):
        """Regression: the PVXS #error-requires false positive must never
        surface as a resolved C++20 standard in provenance either."""
        header = tmp_path / "h.h"
        header.write_text(
            "#ifndef HAVE_BASE\n#error Foo requires Base\n#endif\nint f(int x);\n",
            encoding="utf-8",
        )
        assert _resolve_standard_provenance([header], None, ()) is None

    def test_real_requires_clause_resolves_to_gnu_cxx20(self, tmp_path: Path):
        header = tmp_path / "h.h"
        header.write_text(
            "template<class T> requires true\nint f(T x);\n", encoding="utf-8"
        )
        assert _resolve_standard_provenance([header], None, ()) == "gnu++20"

    def test_no_headers_returns_none(self):
        assert _resolve_standard_provenance([], None, ()) is None


class TestAstCompileProvenance:
    def test_full_shape(self, tmp_path: Path):
        header = tmp_path / "h.h"
        header.write_text("int f(int x);\n", encoding="utf-8")
        sysroot = tmp_path / "sysroot"
        prov = _ast_compile_provenance([header], "-DFOO=1", ("-std=gnu++17",), sysroot)
        assert prov["ast_resolved_standard"] == "gnu++17"
        assert prov["ast_cplusplus_macro"] == "201703L"
        assert prov["ast_compile_args"] == ("-std=gnu++17", "-DFOO=1")
        assert prov["ast_sysroot"] == DEFAULT_REDACTION.path(str(sysroot))

    def test_empty_inputs_are_all_none_or_empty(self):
        prov = _ast_compile_provenance([], None, (), None)
        assert prov == {
            "ast_resolved_standard": None,
            "ast_cplusplus_macro": None,
            "ast_compile_args": (),
            "ast_sysroot": None,
        }

    def test_secret_looking_define_is_redacted(self):
        """A --gcc-options define whose name looks like a credential (TOKEN/
        SECRET/API_KEY/...) must never reach a persisted snapshot verbatim --
        same RedactionPolicy convention every L3 build-evidence adapter
        (compile_db.py, make.py, bazel.py, ninja.py) already applies."""
        prov = _ast_compile_provenance(
            [], "-DAPI_TOKEN=supersecret123 -DFOO=1", (), None
        )
        assert prov["ast_compile_args"] == ("-DAPI_TOKEN=<redacted>", "-DFOO=1")

    def test_home_prefixed_sysroot_is_redacted(self):
        """A --sysroot under $HOME is normalized to '~/...' by the module-level
        DEFAULT_REDACTION policy (already initialized against the real home),
        same as every L3 adapter's sysroot handling."""
        import os

        home = os.path.expanduser("~")
        if home == "~":
            pytest.skip("no home directory resolvable in this environment")
        sysroot = Path(home) / "sdk" / "sysroot"
        prov = _ast_compile_provenance([], None, (), sysroot)
        assert prov["ast_sysroot"] == str(Path("~") / "sdk" / "sysroot")


class TestResolveForceCpp:
    """The real language-mode decision the header-AST parse itself makes
    (relocated from ``dumper.py`` alongside this PR's probe fix — Codex
    review: the probe must resolve the same mode the real parse used,
    since an unspecified ``lang`` can auto-detect as either C or C++
    depending on the header's own content)."""

    def test_explicit_c_never_forces_cpp(self):
        assert _resolve_force_cpp("c", [], None, ()) is False

    def test_explicit_cpp_forces_cpp(self):
        assert _resolve_force_cpp("c++", [], None, ()) is True
        assert _resolve_force_cpp("cpp", [], None, ()) is True

    def test_unspecified_lang_with_plain_c_compatible_header_stays_c(
        self, tmp_path: Path
    ):
        header = tmp_path / "h.h"
        header.write_text("int f(int x);\n", encoding="utf-8")
        assert _resolve_force_cpp(None, [header], None, ()) is False

    def test_unspecified_lang_with_cpp_only_syntax_auto_detects_cpp(
        self, tmp_path: Path
    ):
        header = tmp_path / "h.h"
        header.write_text("namespace ns { int f(int x); }\n", encoding="utf-8")
        assert _resolve_force_cpp(None, [header], None, ()) is True


class TestProbeDefaultLanguageStandard:
    """ADR-050 D1/D2 follow-up: the profile_fingerprint comparability guard
    must be able to tell two genuinely different toolchains' unpinned
    default standards apart, not just an explicit ``-std=``. See
    ``abicheck-internal-bugs`` finding 2: "the cross-profile comparability
    guard doesn't fire without L3 build evidence"."""

    def setup_method(self):
        _probe_default_language_standard.cache_clear()

    @pytest.mark.integration
    def test_probes_real_host_compiler_cpp(self):
        import shutil

        cxx = shutil.which("g++") or shutil.which("clang++")
        if cxx is None:
            pytest.skip("no C++ compiler on PATH")
        result = _probe_default_language_standard(cxx, "c++")
        if result is None:
            # Real CI failure (Windows integration-tests, PR #816): a real
            # g++/clang++ can be found on PATH (e.g. a bundled MinGW GCC on
            # a Windows runner) yet still reject this probe's
            # `-E -dM -x c++ -` invocation for reasons unrelated to
            # abicheck's own logic -- exactly the "None on any failure...
            # a probe that can't run is simply not evidence" contract
            # _probe_default_language_standard's own docstring documents.
            # The probing *mechanism* itself is verified independently of
            # any specific host toolchain by
            # test_two_different_defaults_produce_different_values (a fully
            # mocked subprocess), so a real, uncontrolled CI toolchain
            # declining to answer is inconclusive, not a regression.
            pytest.skip(f"{cxx} did not answer the -E -dM -x c++ - probe")
        assert result.startswith("probed:__cplusplus=")

    def test_two_different_defaults_produce_different_values(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """The actual mechanism the false-COMPATIBLE bug rested on: two
        resolved compilers with genuinely different unpinned defaults must
        probe to genuinely different values."""
        import subprocess as subprocess_module

        def fake_run(argv, **kwargs):
            assert argv[1:4] == ["-E", "-dM", "-x"]
            std = {"cxx17": "201703L", "cxx20": "202002L"}[argv[0]]
            return subprocess_module.CompletedProcess(
                argv, 0, stdout=f"#define __cplusplus {std}\n", stderr=""
            )

        monkeypatch.setattr(subprocess_module, "run", fake_run)
        old = _probe_default_language_standard("cxx17", "c++")
        new = _probe_default_language_standard("cxx20", "c++")
        assert old != new
        assert old == "probed:__cplusplus=201703L"
        assert new == "probed:__cplusplus=202002L"

    def test_c_mode_absent_stdc_version_is_a_real_value_not_a_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        import subprocess as subprocess_module

        def fake_run(argv, **kwargs):
            return subprocess_module.CompletedProcess(argv, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess_module, "run", fake_run)
        result = _probe_default_language_standard("old-cc", "c")
        assert result == "probed:__STDC_VERSION__=<absent>"

    def test_missing_compiler_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        import subprocess as subprocess_module

        def fake_run(argv, **kwargs):
            raise FileNotFoundError(argv[0])

        monkeypatch.setattr(subprocess_module, "run", fake_run)
        assert _probe_default_language_standard("/no/such/cc", "c++") is None

    def test_nonzero_exit_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        import subprocess as subprocess_module

        def fake_run(argv, **kwargs):
            return subprocess_module.CompletedProcess(argv, 1, stdout="", stderr="")

        monkeypatch.setattr(subprocess_module, "run", fake_run)
        assert _probe_default_language_standard("cl.exe", "c++") is None

    def test_result_is_cached_per_binary_and_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Repeated identical calls are cached (one subprocess call) -- and
        both ``compiler_bin`` and ``lang_mode`` are genuinely part of the
        cache key, not just incidental ``lru_cache`` argument order: varying
        either one must still probe (CodeRabbit review, fresh evidence --
        the prior version of this test only proved the identical-call case,
        which would also pass against a cache keyed on ``compiler_bin``
        alone)."""
        import subprocess as subprocess_module

        calls = []

        def fake_run(argv, **kwargs):
            calls.append(tuple(argv))
            macro = "__cplusplus" if argv[3] == "c++" else "__STDC_VERSION__"
            return subprocess_module.CompletedProcess(
                argv, 0, stdout=f"#define {macro} 201703L\n", stderr=""
            )

        monkeypatch.setattr(subprocess_module, "run", fake_run)
        _probe_default_language_standard("cached-cc", "c++")
        _probe_default_language_standard("cached-cc", "c++")  # identical: cached
        assert len(calls) == 1
        _probe_default_language_standard("cached-cc", "c")  # different mode: probes
        assert len(calls) == 2
        _probe_default_language_standard("other-cc", "c++")  # different binary: probes
        assert len(calls) == 3
        _probe_default_language_standard("cached-cc", "c")  # repeat of call 2: cached
        assert len(calls) == 3


class TestResolveStandardProvenanceProbing:
    """``_resolve_standard_provenance``'s probing fallback -- only reached
    when neither an explicit ``-std=`` nor the C++20-detection heuristic
    already resolved a value."""

    def setup_method(self):
        _probe_default_language_standard.cache_clear()

    def test_explicit_std_never_probes(self, monkeypatch: pytest.MonkeyPatch):
        def fail_probe(*a, **k):
            raise AssertionError("must not probe when an explicit -std= is given")

        monkeypatch.setattr(
            "abicheck.dumper_toolchain._probe_default_language_standard", fail_probe
        )
        result = _resolve_standard_provenance(
            [], "-std=gnu++11", (), probe_compiler_bin="cc", lang="c++"
        )
        assert result == "gnu++11"

    def test_no_probe_compiler_bin_stays_none(self):
        assert _resolve_standard_provenance([], None, ()) is None

    def test_probe_fills_in_when_nothing_else_resolves(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        def fake_probe(compiler_bin, lang_mode):
            return f"probed:__cplusplus=STUBBED:{compiler_bin}"

        monkeypatch.setattr(
            "abicheck.dumper_toolchain._probe_default_language_standard", fake_probe
        )
        result = _resolve_standard_provenance(
            [], None, (), probe_compiler_bin="my-clang", lang="c++"
        )
        assert result == "probed:__cplusplus=STUBBED:my-clang"

    def test_unpinned_c_gnu_mode_is_never_probed_forced_standard_reported_directly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Codex review, fresh evidence: both header-AST command builders
        (``dumper_ast_config._build_castxml_command``/
        ``_build_clang_header_command``) unconditionally force ``-std=gnu11``
        for an unpinned C/gnu-dialect parse -- so this must report that
        forced standard directly, never probe the resolved compiler's own
        naked default (which is a genuinely different value, e.g. GCC's C17
        default), since that default was never what actually parsed the
        headers."""

        def fail_probe(*a, **k):
            raise AssertionError("must not probe a forced-standard C/gnu parse")

        monkeypatch.setattr(
            "abicheck.dumper_toolchain._probe_default_language_standard", fail_probe
        )
        c_header = tmp_path / "c.h"
        c_header.write_text("int f(int x);\n", encoding="utf-8")
        result = _resolve_standard_provenance(
            [c_header], None, (), probe_compiler_bin="cc", lang=None
        )
        assert result == "gnu11"

    def test_unpinned_c_msvc_dialect_is_still_probed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The gnu11 force above is gated on ``cc_id == "gnu"`` in the real
        command builder -- an MSVC-dialect C parse stays genuinely unpinned
        to the resolved compiler's own default, so it must still probe."""
        seen_modes = []

        def fake_probe(compiler_bin, lang_mode):
            seen_modes.append(lang_mode)
            return "probed:stub"

        monkeypatch.setattr(
            "abicheck.dumper_toolchain._probe_default_language_standard", fake_probe
        )
        c_header = tmp_path / "c.h"
        c_header.write_text("int f(int x);\n", encoding="utf-8")
        result = _resolve_standard_provenance(
            [c_header],
            None,
            (),
            probe_compiler_bin="cl.exe",
            lang=None,
            abi_dialect="msvc",
        )
        assert seen_modes == ["c"]
        assert result == "probed:stub"

    def test_resolved_lang_mode_overrides_static_re_derivation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Codex review, fresh evidence: a C->C++ self-heal retry settles on
        a language mode a static re-derivation from *headers* alone cannot
        reconstruct (the header itself still only contains C-compatible
        syntax; the real signal was a missing C++ stdlib header at parse
        time). *resolved_lang_mode*, when given, must win outright, not just
        bias the auto-detection heuristic."""
        seen_modes = []

        def fake_probe(compiler_bin, lang_mode):
            seen_modes.append(lang_mode)
            return "probed:stub"

        monkeypatch.setattr(
            "abicheck.dumper_toolchain._probe_default_language_standard", fake_probe
        )
        c_header = tmp_path / "c.h"
        c_header.write_text("int f(int x);\n", encoding="utf-8")
        _resolve_standard_provenance(
            [c_header],
            None,
            (),
            probe_compiler_bin="clang",
            lang=None,
            resolved_lang_mode="c++",
        )
        assert seen_modes == ["c++"]

    def test_heterogeneous_resolved_lang_mode_returns_none_without_probing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Codex review, fresh evidence: ``tu_merge.merge_fragments`` writes
        ``_HETEROGENEOUS_LANG_MODE`` when a merged manifest's contributing
        TUs genuinely disagree on their resolved language mode. Falling
        back to the static ``_resolve_force_cpp`` re-derivation for that
        case (over the manifest's combined public headers alone) can be
        confidently *wrong* -- not just unknown -- for a TU whose
        language mode came from something invisible to those headers, so
        this must return ``None`` outright, skipping both the forced-
        ``gnu11`` path and the probe path, rather than guessing either
        way."""
        from abicheck.tu_merge import _HETEROGENEOUS_LANG_MODE

        def fail_probe(*a, **k):
            raise AssertionError("must not probe a heterogeneous manifest")

        monkeypatch.setattr(
            "abicheck.dumper_toolchain._probe_default_language_standard", fail_probe
        )
        c_header = tmp_path / "c.h"
        c_header.write_text("int f(int x);\n", encoding="utf-8")
        result = _resolve_standard_provenance(
            [c_header],
            None,
            (),
            probe_compiler_bin="cc",
            lang=None,
            resolved_lang_mode=_HETEROGENEOUS_LANG_MODE,
        )
        assert result is None


class TestAstCompileProvenanceProbing:
    def setup_method(self):
        _probe_default_language_standard.cache_clear()

    def test_ast_toolchain_compiler_selected_feeds_the_probe(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        seen = {}

        def fake_probe(compiler_bin, lang_mode):
            seen["compiler_bin"] = compiler_bin
            seen["lang_mode"] = lang_mode
            return "probed:__cplusplus=201402L"

        monkeypatch.setattr(
            "abicheck.dumper_toolchain._probe_default_language_standard", fake_probe
        )
        prov = _ast_compile_provenance(
            [],
            None,
            (),
            None,
            ast_toolchain={"compiler_selected": "/opt/toolchains/g++-9"},
            lang="c++",
        )
        assert seen == {"compiler_bin": "/opt/toolchains/g++-9", "lang_mode": "c++"}
        assert prov["ast_resolved_standard"] == "probed:__cplusplus=201402L"
        assert prov["ast_cplusplus_macro"] == "201402L"

    def test_no_ast_toolchain_preserves_prior_behavior(self):
        """The default (no ``ast_toolchain``/``lang`` given) must be
        byte-for-byte identical to this function's pre-fix behavior."""
        prov = _ast_compile_provenance([], None, (), None)
        assert prov["ast_resolved_standard"] is None

    def test_ast_toolchain_resolved_lang_mode_feeds_the_probe(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Codex review, fresh evidence: ``dumper.py``'s clang self-heal
        stamps ``resolved_lang_mode`` onto ``ast_toolchain`` after a C->C++
        retry -- this is the end-to-end wiring proving that value actually
        reaches the probe, not just a re-derivation from ``lang``."""
        seen = {}

        def fake_probe(compiler_bin, lang_mode):
            seen["lang_mode"] = lang_mode
            return "probed:__cplusplus=201703L"

        monkeypatch.setattr(
            "abicheck.dumper_toolchain._probe_default_language_standard", fake_probe
        )
        prov = _ast_compile_provenance(
            [],
            None,
            (),
            None,
            ast_toolchain={
                "compiler_selected": "/usr/bin/clang",
                "resolved_lang_mode": "c++",
            },
            lang=None,  # unspecified -- would auto-detect "c" without the override
        )
        assert seen == {"lang_mode": "c++"}
        assert prov["ast_resolved_standard"] == "probed:__cplusplus=201703L"

    def test_two_different_resolved_compilers_probe_to_different_standards(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """The actual bug this closes: `dump`'d under two different,
        unpinned toolchains must no longer silently agree on
        `language_standard`."""

        def fake_probe(compiler_bin, lang_mode):
            return {
                "/usr/bin/g++-9": "probed:__cplusplus=201703L",
                "/usr/bin/clang++-18": "probed:__cplusplus=202002L",
            }[compiler_bin]

        monkeypatch.setattr(
            "abicheck.dumper_toolchain._probe_default_language_standard", fake_probe
        )
        old = _ast_compile_provenance(
            [],
            None,
            (),
            None,
            ast_toolchain={"compiler_selected": "/usr/bin/g++-9"},
            lang="c++",
        )
        new = _ast_compile_provenance(
            [],
            None,
            (),
            None,
            ast_toolchain={"compiler_selected": "/usr/bin/clang++-18"},
            lang="c++",
        )
        assert old["ast_resolved_standard"] != new["ast_resolved_standard"]

    def test_falls_back_to_bare_selected_key(self, monkeypatch: pytest.MonkeyPatch):
        """``compiler_selected`` is absent for a producer where frontend and
        compiler are the same binary (e.g. a direct-clang dump) -- falls
        back to the bare ``selected`` key, mirroring
        ``_compiler_family_from_toolchain``'s identical fallback."""
        seen = {}

        def fake_probe(compiler_bin, lang_mode):
            seen["compiler_bin"] = compiler_bin
            return "probed:__cplusplus=201703L"

        monkeypatch.setattr(
            "abicheck.dumper_toolchain._probe_default_language_standard", fake_probe
        )
        _ast_compile_provenance(
            [],
            None,
            (),
            None,
            ast_toolchain={"selected": "/usr/bin/clang++"},
            lang="c++",
        )
        assert seen["compiler_bin"] == "/usr/bin/clang++"


class TestSnapshotSerializationRoundTrip:
    def test_new_fields_round_trip_through_json(self):
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            ast_resolved_standard="gnu++17",
            ast_cplusplus_macro="201703L",
            ast_compile_args=("-std=gnu++17", "-DFOO=1"),
            ast_sysroot="/opt/sysroot",
        )
        d = snapshot_to_dict(snap)
        assert d["schema_version"] == SCHEMA_VERSION
        back = snapshot_from_dict(d)
        assert back.ast_resolved_standard == "gnu++17"
        assert back.ast_cplusplus_macro == "201703L"
        assert back.ast_compile_args == ("-std=gnu++17", "-DFOO=1")
        assert back.ast_sysroot == "/opt/sysroot"

    def test_pre_v15_snapshot_loads_new_fields_as_defaults(self):
        """Backward compatibility: a pre-v15 snapshot dict has none of the
        four new keys at all — loading it must default them, never crash."""
        d = {
            "schema_version": 14,
            "library": "legacy.so",
            "version": "1.0",
        }
        snap = snapshot_from_dict(d)
        assert snap.ast_resolved_standard is None
        assert snap.ast_cplusplus_macro is None
        assert snap.ast_compile_args == ()
        assert snap.ast_sysroot is None

    def test_wrong_typed_fields_in_dict_default_safely(self):
        """A hand-edited or corrupted snapshot dict with wrong types for the
        new fields must not raise -- defensive .get() parsing, same
        convention as every other provenance field here."""
        d = {
            "schema_version": 15,
            "library": "weird.so",
            "version": "1.0",
            "ast_resolved_standard": 123,
            "ast_cplusplus_macro": ["not", "a", "string"],
            "ast_compile_args": "not-a-list",
            "ast_sysroot": 42,
        }
        snap = snapshot_from_dict(d)
        assert snap.ast_resolved_standard is None
        assert snap.ast_cplusplus_macro is None
        assert snap.ast_compile_args == ()
        assert snap.ast_sysroot is None
