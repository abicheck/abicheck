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

"""Sibling split of ``test_build_source_pack.py`` (redaction, ADR-032 D7),
split out during the P0.3 follow-up round 2 merge against ``main`` to keep
the parent file under the 2000-line AI-readiness hard cap (both this PR's
own additions and independent ``main`` commits grew it past 2000 once
merged) -- follows this repo's established sibling-split convention (e.g.
``test_call_graph_extra.py``, ``test_header_compile_context_split_flags.py``).

Covers ``buildsource.redaction.RedactionPolicy`` and the
``CompileDbAdapter``-level secret/home-path redaction it feeds -- home-prefix
rewriting, secret macro/option-flag redaction, and split-define-form secret
redaction across both the policy's own direct API and a real compile
database's ``argv``/``defines``.
"""

from __future__ import annotations

import json

from abicheck.buildsource.redaction import RedactionPolicy

# ── Redaction (ADR-032 D7) ───────────────────────────────────────────────────


def test_redaction_strips_secret_define():
    pol = RedactionPolicy(redact_home=False)
    assert pol.arg("-DAPI_TOKEN=hunter2") == "-DAPI_TOKEN=<redacted>"
    assert pol.arg("-DFOO=1") == "-DFOO=1"


def test_redaction_rewrites_home_prefix():
    pol = RedactionPolicy(home_replacements={"/home/alice": "~"})
    assert pol.path("/home/alice/proj/foo.cpp") == "~/proj/foo.cpp"


def test_redaction_rewrites_embedded_home_paths_in_argv():
    """Combined flags that embed a home path are redacted in argv (Codex)."""
    pol = RedactionPolicy(home_replacements={"/home/alice": "~"})
    assert pol.path("-I/home/alice/proj/include") == "-I~/proj/include"
    assert pol.path("-DMYROOT=/home/alice/sdk") == "-DMYROOT=~/sdk"
    red = pol.argv(
        ["c++", "-I/home/alice/inc", "-DMYROOT=/home/alice/sdk", "-c", "a.cpp"]
    )
    assert not any("/home/alice" in tok for tok in red)


def test_compile_db_redacts_embedded_home_paths_in_argv(tmp_path):
    """End-to-end: embedded home paths never reach CompileUnit.argv."""
    from abicheck.buildsource.adapters import CompileDbAdapter

    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": "a.cpp",
                    "arguments": ["c++", "-I/home/alice/proj/include", "-c", "a.cpp"],
                }
            ]
        )
    )
    ev = CompileDbAdapter(
        cdb,
        redaction=RedactionPolicy(home_replacements={"/home/alice": "~"}),
    ).collect()
    assert not any("/home/alice" in tok for tok in ev.compile_units[0].argv)


def test_redaction_define_value_redacts_secret_macro():
    pol = RedactionPolicy(home_replacements={"/home/bob": "~"})
    assert pol.define_value("API_TOKEN", "hunter2") == "<redacted>"
    assert pol.define_value("SECRET_KEY", "abc") == "<redacted>"
    # Non-secret macros keep their value but still get home-path normalization.
    assert pol.define_value("FOO", "1") == "1"
    assert pol.define_value("PREFIX", "/home/bob/install") == "~/install"


def test_compile_db_redacts_secret_define(tmp_path):
    from abicheck.buildsource.adapters import CompileDbAdapter

    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": "a.cpp",
                    "arguments": [
                        "c++",
                        "-DAPI_TOKEN=hunter2",
                        "-DFOO=1",
                        "-c",
                        "a.cpp",
                    ],
                }
            ]
        )
    )
    ev = CompileDbAdapter(cdb).collect()
    defines = ev.compile_units[0].defines
    assert defines["API_TOKEN"] == "<redacted>"
    assert defines["FOO"] == "1"


def test_redaction_argv_redacts_split_define_secret():
    """Split -D form ['-D', 'KEY=secret'] must redact the value token."""
    pol = RedactionPolicy(redact_home=False)
    out = pol.argv(["c++", "-D", "API_TOKEN=hunter2", "-D", "FOO=1", "-c", "a.cpp"])
    assert "API_TOKEN=<redacted>" in out
    assert "hunter2" not in " ".join(out)
    assert "FOO=1" in out


def test_redaction_redacts_secret_option_flags():
    """Credential-style CLI flags (not just -D macros) must be redacted (D7)."""
    pol = RedactionPolicy(redact_home=False)
    # Combined --flag=value form.
    assert pol.arg("--token=hunter2") == "--token=<redacted>"
    assert pol.arg("--api-key=abc123") == "--api-key=<redacted>"
    assert pol.arg("--password=p@ss") == "--password=<redacted>"
    # Non-secret options are left untouched.
    assert pol.arg("--output=build/x.json") == "--output=build/x.json"


def test_redaction_argv_redacts_split_secret_option():
    """Split '--token secret' form must redact the value token, not later flags."""
    pol = RedactionPolicy(redact_home=False)
    out = pol.argv(
        [
            "tool",
            "--token",
            "hunter2",
            "--auth-token",
            "abc",
            "--verbose",
            "-c",
            "a.cpp",
        ]
    )
    joined = " ".join(out)
    assert "hunter2" not in joined
    assert "abc" not in joined.split()  # value after --auth-token redacted
    assert out == [
        "tool",
        "--token",
        "<redacted>",
        "--auth-token",
        "<redacted>",
        "--verbose",
        "-c",
        "a.cpp",
    ]
    # A secret flag immediately followed by another flag has no value to redact.
    assert pol.argv(["tool", "--token", "--verbose"]) == [
        "tool",
        "--token",
        "--verbose",
    ]


def test_compile_db_split_define_secret_not_leaked_in_argv(tmp_path):
    """End-to-end: split-form secret never reaches CompileUnit.argv."""
    from abicheck.buildsource.adapters import CompileDbAdapter

    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": "a.cpp",
                    "arguments": [
                        "c++",
                        "-D",
                        "API_TOKEN=hunter2",
                        "-D",
                        "_GLIBCXX_USE_CXX11_ABI=0",
                        "-c",
                        "a.cpp",
                    ],
                }
            ]
        )
    )
    ev = CompileDbAdapter(cdb).collect()
    cu = ev.compile_units[0]
    assert "hunter2" not in " ".join(cu.argv)
    assert cu.defines["API_TOKEN"] == "<redacted>"
    # The split-form ABI macro is still captured as a diffable option.
    assert any(o.key == "define:_GLIBCXX_USE_CXX11_ABI" for o in ev.build_options)
