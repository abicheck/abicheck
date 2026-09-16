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

"""ABICC ``<skip_headers>``/``<skip_including>`` rule semantics.

Bug class (``tests/regressions/manifest.py``: ``descriptor-skip-rule-
class-collapsed``): a rule language with several *classes* implemented as
one membership test, so every rule outside the implemented class silently
matched nothing while the run still produced a confident verdict. The
reported instance was Intel MKL's tree-relative ``fftw/fftw.h`` and
``fftw/offload/`` rules -- sixteen load-bearing skips, zero headers
excluded.

The primitive gets property-style coverage of its own contract (this
repository's "Primitive-level property tests" guidance): the rewrite that
was *not* taken -- matching every rule as a bare basename -- passes any
MKL-shaped example test and over-excludes an unrelated ``version.h``
under a different subtree, so a fixed-input test could not have told the
two apart.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.compat.descriptor import parse_descriptor
from abicheck.errors import ValidationError
from abicheck.model.header_skip_rules import (
    ACTION_DO_NOT_INCLUDE,
    ACTION_EXCLUDE,
    KIND_NAME,
    KIND_PATH,
    KIND_PATTERN,
    achieved_exclusion_patterns,
    apply_skip_rules,
    compile_skip_rule,
    compile_skip_rules,
    match_skip_rule,
)


def _r(value: str) -> object:
    return compile_skip_rule(value, ACTION_EXCLUDE)


class TestRuleClassification:
    """The three classes ABICC's ``classifyPath`` distinguishes."""

    @pytest.mark.parametrize(
        ("value", "kind"),
        [
            ("mkl_direct_types.h", KIND_NAME),
            ("version.h", KIND_NAME),
            ("fftw/fftw.h", KIND_PATH),
            ("fftw/offload/", KIND_PATH),
            ("fftw\\fftw.h", KIND_PATH),
            ("/opt/mkl/include/x.h", KIND_PATH),
            ("mkl_*.h", KIND_PATTERN),
            ("a?.h", KIND_PATTERN),
            ("x[0-9].h", KIND_PATTERN),
            ("fftw/*.h", KIND_PATTERN),
        ],
    )
    def test_kind(self, value: str, kind: str) -> None:
        assert _r(value).kind == kind

    def test_an_empty_rule_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            compile_skip_rule("   ", ACTION_EXCLUDE)

    def test_an_invalid_pattern_is_refused_by_name(self) -> None:
        """Not dropped silently: an unparseable rule is a descriptor to fix."""
        with pytest.raises(ValidationError) as exc:
            compile_skip_rule("x[0-9.h", ACTION_EXCLUDE)
        assert "x[0-9.h" in str(exc.value)


class TestNameRules:
    def test_matches_the_basename_anywhere(self) -> None:
        assert _r("version.h").matches("/opt/pkg/include/sub/version.h")

    def test_does_not_match_a_different_basename(self) -> None:
        assert not _r("version.h").matches("/opt/pkg/include/versions.h")

    def test_does_not_match_a_path_suffix(self) -> None:
        """A bare name is a *file name*, never a path fragment."""
        assert not _r("include").matches("/opt/pkg/include/a.h")


class TestPathRules:
    """The reported defect and the over-correction it must not become."""

    @pytest.mark.parametrize(
        "header",
        [
            "/opt/mkl/include/fftw/fftw.h",
            "/opt/mkl/latest/include/fftw/fftw.h",
            "fftw/fftw.h",
            "include/fftw/fftw.h",
        ],
    )
    def test_tree_relative_file_matches_at_component_boundaries(
        self, header: str
    ) -> None:
        assert _r("fftw/fftw.h").matches(header)

    @pytest.mark.parametrize(
        "header",
        [
            # The over-exclusion a basename rewrite would have caused.
            "/opt/mkl/include/other/fftw.h",
            # The component-boundary requirement.
            "/opt/mkl/include/myfftw/fftw.h",
            "/opt/mkl/include/fftw/sub/fftw.h",
            "/opt/mkl/include/fftw/fftw.hpp",
        ],
    )
    def test_does_not_match_off_boundary_or_elsewhere(self, header: str) -> None:
        assert not _r("fftw/fftw.h").matches(header)

    def test_directory_rule_takes_descendants(self) -> None:
        rule = _r("fftw/offload/")
        assert rule.matches("/opt/mkl/include/fftw/offload/a.h")
        assert rule.matches("/opt/mkl/include/fftw/offload/deep/b.h")

    def test_directory_rule_does_not_take_a_file_of_that_name(self) -> None:
        assert not _r("fftw/offload/").matches("/opt/mkl/include/fftw/offload")

    def test_directory_rule_respects_the_boundary(self) -> None:
        assert not _r("fftw/offload/").matches("/opt/mkl/include/fftw/offloadx/a.h")

    def test_a_dirless_path_rule_also_takes_descendants(self) -> None:
        """``foo/bar`` names a file *or* a directory -- ABICC does not make
        the author say which, and neither does this."""
        assert _r("fftw/offload").matches("/inc/fftw/offload/a.h")
        assert _r("fftw/offload").matches("/inc/fftw/offload")

    def test_windows_separators_on_either_side(self) -> None:
        assert _r("fftw\\fftw.h").matches("/opt/mkl/include/fftw/fftw.h")
        assert _r("fftw/fftw.h").matches("C:\\mkl\\include\\fftw\\fftw.h")

    def test_absolute_file_and_directory_rules(self) -> None:
        assert _r("/opt/mkl/include/x.h").matches("/opt/mkl/include/x.h")
        assert _r("/opt/mkl/include/").matches("/opt/mkl/include/sub/x.h")
        assert not _r("/opt/mkl/include/x.h").matches("/other/opt/mkl/include/x.h")

    @pytest.mark.parametrize(
        ("rule", "header"),
        [
            ("fftw//fftw.h", "/opt/mkl/include/fftw/fftw.h"),
            ("fftw/fftw.h", "/opt/mkl//include//fftw/fftw.h"),
            ("fftw\\\\fftw.h", "/opt/mkl/include/fftw/fftw.h"),
            ("fftw/offload//", "/opt/mkl/include/fftw/offload/a.h"),
        ],
    )
    def test_a_doubled_separator_names_the_same_thing(
        self, rule: str, header: str
    ) -> None:
        """On either side, and in either spelling.

        A descriptor concatenating a root and a relative path, or a header
        list built the same way, routinely carries `//`. It names the same
        header as a single separator does, so the rule must too -- otherwise
        a skip silently stops matching for a reason invisible in the text.
        """
        assert _r(rule).matches(header)

    def test_a_lone_separator_rule_matches_nothing(self) -> None:
        """`/` normalizes to an empty component list, which would otherwise
        make the boundary test degenerate and take every header. A rule that
        names no component excludes nothing."""
        rule = _r("/")
        assert not rule.matches("/opt/mkl/include/x.h")
        assert not rule.matches("x.h")

    def test_duplicate_basenames_under_different_subtrees_are_distinguished(
        self,
    ) -> None:
        """The reason the basename rewrite was rejected outright."""
        rule = _r("core/version.h")
        assert rule.matches("/pkg/include/core/version.h")
        assert not rule.matches("/pkg/include/plugin/version.h")


class TestPatternRules:
    def test_star_matches_the_basename(self) -> None:
        assert _r("mkl_*.h").matches("/inc/mkl_dfti.h")
        assert not _r("mkl_*.h").matches("/inc/other.h")

    def test_question_mark_is_one_character(self) -> None:
        assert _r("a?.h").matches("/inc/ab.h")
        assert not _r("a?.h").matches("/inc/abc.h")

    def test_character_class(self) -> None:
        assert _r("x[0-9].h").matches("/inc/x3.h")
        assert not _r("x[0-9].h").matches("/inc/xa.h")

    @pytest.mark.parametrize(
        ("pattern", "header"),
        [
            (r"mkl_.*\.h", "/inc/mkl_dfti.h"),
            (r"[\w]+\.h", "/inc/mkl_dfti.h"),
            (r"mkl_\w+\.h", "/inc/mkl_dfti.h"),
            (r"(fftw|mkl)_.*\.h", "/inc/mkl_dfti.h"),
        ],
    )
    def test_regex_escapes_survive_compilation(self, pattern: str, header: str) -> None:
        r"""A backslash escape is a regex escape, not a path separator.

        The pattern source used to be separator-normalized, which turned
        ``\.`` into ``/.`` and ``[\w]`` into ``[/w]`` -- so ``mkl_.*\.h``
        compiled to ``mkl_..*/.h`` and matched nothing at all, while the
        module docstring claimed every regex construct passed through
        (CodeRabbit review). The pre-existing test here dodged this by
        spelling its dot as ``[.]``, which is precisely a test written to
        agree with the implementation rather than the contract.
        """
        assert _r(pattern).matches(header)

    def test_an_escaped_wildcard_stays_literal(self) -> None:
        r"""The other half of honoring escapes: ``\*`` is an asterisk, not a
        wildcard, which is what a regex author writing the escape means."""
        rule = _r(r"mkl\*\.h")
        assert rule.matches("/inc/mkl*.h")
        assert not rule.matches("/inc/mkl_dfti.h")

    def test_a_separator_inside_a_pattern_is_written_forward(self) -> None:
        r"""The documented consequence: inside a pattern ``\`` is the escape
        character, so a path separator is spelled ``/``. The operand path is
        normalized to ``/`` before matching, so this always works."""
        assert _r("fftw/.*[.]h").matches("C:\\mkl\\fftw\\fftw.h")

    def test_a_pattern_with_a_separator_is_matched_against_the_whole_path(
        self,
    ) -> None:
        rule = _r("fftw/.*[.]h")
        assert rule.matches("/opt/mkl/include/fftw/fftw.h")
        assert not rule.matches("/opt/mkl/include/other/fftw.h")


class TestTheTwoElementsAreDifferentRules:
    def test_actions_are_distinct(self) -> None:
        rules = compile_skip_rules(["a.h"], ["b.h"])
        assert [r.action for r in rules] == [ACTION_EXCLUDE, ACTION_DO_NOT_INCLUDE]

    def test_the_stronger_action_wins_a_tie(self) -> None:
        rules = compile_skip_rules(["a.h"], ["a.h"])
        assert match_skip_rule("/inc/a.h", rules).action == ACTION_EXCLUDE

    def test_both_drop_the_header_from_the_direct_include_list(self) -> None:
        rules = compile_skip_rules(["a.h"], ["b.h"])
        kept = apply_skip_rules(
            [Path("/inc/a.h"), Path("/inc/b.h"), Path("/inc/c.h")], rules
        )
        assert kept == [Path("/inc/c.h")]

    def test_only_skip_headers_is_recorded_as_an_achieved_narrowing(self) -> None:
        rules = compile_skip_rules(["a.h"], ["b.h"])
        headers = [Path("/inc/a.h"), Path("/inc/b.h")]
        assert achieved_exclusion_patterns(rules, headers) == ("a.h",)

    def test_a_rule_matching_nothing_is_not_recorded(self) -> None:
        rules = compile_skip_rules(["nosuch.h"])
        assert achieved_exclusion_patterns(rules, [Path("/inc/a.h")]) == ()

    def test_the_record_is_sorted_and_deduplicated(self) -> None:
        rules = compile_skip_rules(["z.h", "a.h", "z.h"])
        headers = [Path("/inc/z.h"), Path("/inc/a.h")]
        assert achieved_exclusion_patterns(rules, headers) == ("a.h", "z.h")


class TestDescriptorEndToEnd:
    """The reported FFTW2/FFTW3 collision, through the real parser."""

    @staticmethod
    def _tree(tmp_path: Path) -> Path:
        inc = tmp_path / "include"
        (inc / "fftw" / "offload").mkdir(parents=True)
        (inc / "fftw" / "fftw.h").write_text("int fftw2(void);\n")
        (inc / "fftw" / "offload" / "a.h").write_text("int off(void);\n")
        (inc / "mkl.h").write_text("int mkl(void);\n")
        (inc / "fftw3.h").write_text("int fftw3(void);\n")
        return inc

    def test_the_original_tree_relative_rules_exclude_what_they_name(
        self, tmp_path: Path
    ) -> None:
        inc = self._tree(tmp_path)
        desc_path = tmp_path / "d.xml"
        desc_path.write_text(
            "<version>1.0</version>\n<headers>include</headers>\n"
            "<libs>lib.so</libs>\n"
            "<skip_headers>\n  fftw/fftw.h\n  fftw/offload/\n</skip_headers>\n",
            encoding="utf-8",
        )
        desc = parse_descriptor(desc_path)
        rules = desc.skip_rules()
        walked = sorted(inc.rglob("*.h"))
        survivors = sorted(p.name for p in apply_skip_rules(walked, rules))
        assert survivors == ["fftw3.h", "mkl.h"]

    def test_skip_including_keeps_its_own_element(self, tmp_path: Path) -> None:
        desc_path = tmp_path / "d.xml"
        desc_path.write_text(
            "<version>1.0</version>\n<libs>lib.so</libs>\n"
            "<skip_headers>a.h</skip_headers>\n"
            "<skip_including>b.h</skip_including>\n",
            encoding="utf-8",
        )
        desc = parse_descriptor(desc_path)
        assert desc.skip_headers == ["a.h"]
        assert desc.skip_including == ["b.h"]
