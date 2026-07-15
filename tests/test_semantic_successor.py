from __future__ import annotations

from pathlib import Path

import pytest

from scriptorium.semantic_successor import (
    BERT_TINY_NSP_MODEL,
    BERT_TINY_NSP_REVISION,
    BertNspScorer,
    BertNspScorerConfig,
    create_semantic_pair_scorer,
)


class _FakeCachedNspScorer(BertNspScorer):
    def __init__(self, config: BertNspScorerConfig) -> None:
        super().__init__(config)
        self.uncached_batches: list[list[tuple[str, str]]] = []

    def _score_uncached_pairs(self, pairs):  # type: ignore[no-untyped-def]
        self.uncached_batches.append(list(pairs))
        return [-float(len(source) + len(target)) for source, target in pairs]


def test_bert_nsp_scorer_deduplicates_and_reuses_sqlite_cache(tmp_path: Path) -> None:
    cache_path = tmp_path / "semantic.sqlite3"
    config = BertNspScorerConfig(cache_path=cache_path)
    first = _FakeCachedNspScorer(config)

    scores = first.score_pairs([("alpha", "beta"), ("alpha", "beta"), ("beta", "gamma")])

    assert scores == [-9.0, -9.0, -9.0]
    assert first.uncached_batches == [[("alpha", "beta"), ("beta", "gamma")]]
    second = _FakeCachedNspScorer(config)
    assert second.score_pairs([("beta", "gamma"), ("alpha", "beta")]) == [-9.0, -9.0]
    assert second.uncached_batches == []


def test_semantic_scorer_descriptor_keeps_canonical_identity_for_local_snapshot(
    tmp_path: Path,
) -> None:
    scorer = create_semantic_pair_scorer(
        "bert-tiny-uncased-nsp",
        load_from=tmp_path,
        local_files_only=True,
    )

    assert scorer.descriptor["model"] == BERT_TINY_NSP_MODEL
    assert scorer.descriptor["revision"] == BERT_TINY_NSP_REVISION
    assert "load_from" not in scorer.descriptor


def test_semantic_scorer_rejects_unpinned_or_unknown_models() -> None:
    with pytest.raises(ValueError, match="revision must be pinned"):
        BertNspScorer(BertNspScorerConfig(revision=""))
    with pytest.raises(ValueError, match="unsupported semantic scorer preset"):
        create_semantic_pair_scorer("unknown")
