"""Second half of the default edit corpus, scheduled as a separate file."""

import pytest

from tests.test_abi_edit_corpus import (
    CELLS,
    _assert_edit_ground_truth,
    baseline_cache as baseline_cache,
    corpus_root as corpus_root,
)


@pytest.mark.integration
@pytest.mark.parametrize("op,seed,compiler,backend", CELLS[1::2])
def test_edit_produces_declared_ground_truth(
    op, seed, compiler, backend, baseline_cache, corpus_root
) -> None:
    _assert_edit_ground_truth(op, seed, compiler, backend, baseline_cache, corpus_root)
