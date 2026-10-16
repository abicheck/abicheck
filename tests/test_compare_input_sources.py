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

from abicheck.workflows.compare_input_sources import collect_force_public_symbols


class TestSymbolListParsing:
    def test_the_shared_merge_helper_reads_both_sources(self, tmp_path):
        # `collect_force_public_symbols` is the one definition the live CLI and
        # this resolver share, so the format stays in one place.
        listed = tmp_path / "symbols.txt"
        listed.write_text("# comment\n\nfrom_file\n  spaced  \n")
        assert collect_force_public_symbols(("inline", " ", ""), listed) == {
            "inline",
            "from_file",
            "spaced",
        }

    def test_no_list_file_means_inline_symbols_only(self):
        assert collect_force_public_symbols(("a", "b"), None) == {"a", "b"}
