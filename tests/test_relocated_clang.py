# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from abicheck.comparability import (
    check_contracts_comparable,
    compute_extraction_contract,
)
from abicheck.errors import ProfileMismatchError
from abicheck.model import AbiSnapshot


@pytest.mark.parametrize(
    ("new_digest", "new_target", "comparable"),
    [
        ("a" * 64, "x86_64", True),
        ("b" * 64, "x86_64", False),
        ("", "x86_64", False),
        ("a" * 64, "aarch64", False),
    ],
)
def test_relocated_clang_version_banner(new_digest, new_target, comparable):
    import json

    def snapshot(path, digest, target):
        banner = f"clang version 18.1.3\nTarget: {target}\nInstalledDir: {path}"
        contract = compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="clang",
            compiler_version=json.dumps(["clang", banner, banner]),
        )
        return AbiSnapshot(
            library="libfoo.so",
            version="1",
            contract=contract,
            ast_toolchain={"compiler_sha256": digest},
        )

    old = snapshot("/usr/bin", "a" * 64, "x86_64")
    new = snapshot("/opt/bin", new_digest, new_target)
    if comparable:
        check_contracts_comparable(old, new)
    else:
        with pytest.raises(ProfileMismatchError):
            check_contracts_comparable(old, new)
