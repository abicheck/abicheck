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

"""Unit-level (no real compiler) property coverage for
``dumper_toolchain._resolve_force_cpp``'s export-symbol-evidence fallback.

Split out of ``tests/test_dumper_coverage.py`` (that file is already over
its recorded ``architecture/debt.yaml`` no-growth baseline) rather than
growing it further -- see this repo's root ``AGENTS.md`` "Files that are
large — edit carefully": move responsibility to a properly-owned module
instead of trimming to fit.

Bug class ``extraction.language_mode_export_evidence`` (see
``tests/regressions/manifest.py``): a header with no structural C++ syntax
at all (a plain top-level function declaration -- no ``class``/
``namespace``/``template``/``extern "C"``) gives ``_detect_cpp_headers``
nothing to key on, so an unspecified ``lang`` auto-detected C even for a
header that is unambiguously part of a C++ library. Root-caused and fixed
for PR #1138 (the ``exported_not_public``/``public_not_exported``
contradictory-pair regression a real compiled C++ binary triggered end to
end -- see ``tests/test_crosscheck_language_mode_export_evidence.py`` for
the full compiled-binary reproduction). The binary's own already-computed
export table is checked last, only when every header-content heuristic
already in ``_resolve_force_cpp`` found nothing.
"""

from __future__ import annotations

from abicheck.dumper import _resolve_force_cpp


class TestResolveForceCppExportedSymbolEvidence:
    def test_plain_header_stays_c_with_no_export_evidence(self, tmp_path):
        """No signal anywhere (no C++ syntax, no export evidence, e.g. a
        genuine header-only comparison with no binary at all) -- still C,
        unchanged from before this fix."""
        h = tmp_path / "h.h"
        h.write_text("int f(int x);\n", encoding="utf-8")
        assert _resolve_force_cpp(None, [h], None, ()) is False
        assert _resolve_force_cpp(None, [h], None, (), frozenset()) is False

    def test_itanium_mangled_export_forces_cpp(self, tmp_path):
        """The actual reported bug: a plain function declaration with a
        real Itanium-mangled export must force C++ mode."""
        h = tmp_path / "h.h"
        h.write_text("int main_op(int x);\n", encoding="utf-8")
        assert (
            _resolve_force_cpp(None, [h], None, (), frozenset({"_Z7main_opi"})) is True
        )

    def test_macho_itanium_mangled_export_forces_cpp(self, tmp_path):
        """Mach-O's own leading-underscore-decorated Itanium spelling is
        equally decisive evidence."""
        h = tmp_path / "h.h"
        h.write_text("int main_op(int x);\n", encoding="utf-8")
        assert (
            _resolve_force_cpp(None, [h], None, (), frozenset({"__Z7main_opi"}))
            is True
        )

    def test_msvc_mangled_export_forces_cpp(self, tmp_path):
        """MSVC's ``?``-prefixed decoration is equally decisive evidence."""
        h = tmp_path / "h.h"
        h.write_text("int main_op(int x);\n", encoding="utf-8")
        assert (
            _resolve_force_cpp(None, [h], None, (), frozenset({"?main_op@@YAHH@Z"}))
            is True
        )

    def test_bare_name_export_does_not_force_cpp(self, tmp_path):
        """A genuinely plain-C export (no mangling prefix at all) is not
        C++ evidence -- an ordinary C library must stay in C mode."""
        h = tmp_path / "h.h"
        h.write_text("int f(int x);\n", encoding="utf-8")
        assert _resolve_force_cpp(None, [h], None, (), frozenset({"f"})) is False

    def test_explicit_lang_c_wins_over_export_evidence(self, tmp_path):
        """An explicit ``--lang c`` always wins, even against a real
        Itanium-mangled export -- matching every other override in this
        function (structural C++ syntax, C++20 constructs, explicit std)."""
        h = tmp_path / "h.h"
        h.write_text("int main_op(int x);\n", encoding="utf-8")
        assert (
            _resolve_force_cpp("c", [h], None, (), frozenset({"_Z7main_opi"}))
            is False
        )

    def test_export_evidence_is_a_fallback_not_a_veto(self, tmp_path):
        """A header that already has real C++ syntax needs no export
        evidence at all -- an empty/irrelevant export set must not
        suppress the header-content signal."""
        h = tmp_path / "h.h"
        h.write_text("namespace ns { int f(); }\n", encoding="utf-8")
        assert _resolve_force_cpp(None, [h], None, (), frozenset({"f"})) is True

    def test_unrelated_cxx_export_elsewhere_in_binary_does_not_force_cpp(
        self, tmp_path
    ):
        """CodeRabbit review, fresh evidence: a genuinely plain-C header
        must not be forced into C++ mode merely because some *unrelated*
        header/TU in the same multi-header binary happens to export real
        C++ symbols. Concrete failure case: ``struct options { int new; };``
        is valid C -- ``new`` is a C++ reserved word, so forcing C++ mode
        on it breaks parsing outright. The export table here names a
        function (``other_cxx_func``) this header's own text never
        mentions at all -- correlation must reject it as evidence."""
        h = tmp_path / "options.h"
        h.write_text("struct options { int new; };\n", encoding="utf-8")
        assert (
            _resolve_force_cpp(
                None, [h], None, (), frozenset({"_Z14other_cxx_funcv"})
            )
            is False
        )

    def test_export_matching_this_headers_own_identifier_still_forces_cpp(
        self, tmp_path
    ):
        """Companion to the unrelated-export test above: when the export
        DOES correlate with a name this header's own text declares, C++
        mode is still (correctly) forced -- the fix narrows the evidence
        to per-header correlation, it does not disable the fallback."""
        h = tmp_path / "h.h"
        h.write_text("int compute(int x);\n", encoding="utf-8")
        assert (
            _resolve_force_cpp(None, [h], None, (), frozenset({"_Z7computei"}))
            is True
        )

    def test_coincidental_substring_without_length_prefix_does_not_correlate(
        self, tmp_path
    ):
        """A bare textual substring match is not enough -- the correlation
        requires the real Itanium length-prefix encoding of the declared
        identifier, not just the name appearing anywhere in the mangled
        string. ``compute`` appears inside ``_Z14recomputeStuff`` as a
        plain substring but not as the length-prefixed token ``"7compute"``
        (the export is a 14-character *different* identifier,
        ``recomputeStuff``, that merely happens to contain ``compute``), so
        this header's own ``compute`` declaration must not be matched to
        that unrelated export."""
        h = tmp_path / "h.h"
        h.write_text("int compute(int x);\n", encoding="utf-8")
        assert (
            _resolve_force_cpp(
                None, [h], None, (), frozenset({"_Z14recomputeStuffi"})
            )
            is False
        )
