from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


SEMANTIC_SUCCESSOR_SCHEMA = "scriptorium-semantic-successor/v1"
BERT_NSP_SCORE_FORMULA = "log(max(1e-12,p_is_next(source,target)))"
BERT_TINY_NSP_PRESET = "bert-tiny-uncased-nsp"
BERT_TINY_NSP_MODEL = "google/bert_uncased_L-2_H-128_A-2"
BERT_TINY_NSP_REVISION = "30b0a37ccaaa32f332884b96992754e246e48c5f"
BERT_TINY_NSP_LICENSE = "Apache-2.0"


class SemanticPairScorer(Protocol):
    @property
    def descriptor(self) -> Mapping[str, Any]: ...

    def score_pairs(self, pairs: Sequence[tuple[str, str]]) -> list[float]: ...


@dataclass(frozen=True)
class BertNspScorerConfig:
    model: str = BERT_TINY_NSP_MODEL
    revision: str = BERT_TINY_NSP_REVISION
    model_license: str = BERT_TINY_NSP_LICENSE
    load_from: str | Path | None = None
    cache_path: str | Path | None = None
    batch_size: int = 256
    max_length: int = 96
    device: str = "cpu"
    local_files_only: bool = False


class BertNspScorer:
    """Batch and cache BERT next-sentence log probabilities.

    The descriptor records the canonical model identity. ``load_from`` may point
    at a local snapshot without changing that identity, which keeps manifests
    reproducible when model acquisition is handled outside the pipeline.
    """

    def __init__(self, config: BertNspScorerConfig) -> None:
        if not config.model.strip():
            raise ValueError("semantic model must be non-empty")
        if not config.revision.strip():
            raise ValueError("semantic model revision must be pinned")
        if not config.model_license.strip():
            raise ValueError("semantic model license must be declared")
        if config.batch_size < 1:
            raise ValueError("semantic scorer batch_size must be at least 1")
        if config.max_length < 8:
            raise ValueError("semantic scorer max_length must be at least 8")
        self._config = config
        self._tokenizer: Any | None = None
        self._model: Any | None = None
        self._cache_path = Path(config.cache_path) if config.cache_path is not None else None
        descriptor_json = json.dumps(
            self.descriptor,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        self._scorer_key = hashlib.sha256(descriptor_json.encode("utf-8")).hexdigest()

    @property
    def descriptor(self) -> Mapping[str, Any]:
        return {
            "schema": SEMANTIC_SUCCESSOR_SCHEMA,
            "kind": "bert-next-sentence-prediction",
            "model": self._config.model,
            "revision": self._config.revision,
            "model_license": self._config.model_license,
            "score_formula": BERT_NSP_SCORE_FORMULA,
            "is_next_logit_index": 0,
            "max_length": self._config.max_length,
            "truncation": "longest-first",
        }

    def score_pairs(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        normalized = [(str(source), str(target)) for source, target in pairs]
        if not normalized:
            return []
        pair_keys = [_pair_key(source, target) for source, target in normalized]
        cached = self._read_cached_scores(pair_keys)
        missing_by_key: dict[str, tuple[str, str]] = {}
        for key, pair in zip(pair_keys, normalized, strict=True):
            if key not in cached:
                missing_by_key.setdefault(key, pair)
        if missing_by_key:
            missing_keys = list(missing_by_key)
            missing_pairs = [missing_by_key[key] for key in missing_keys]
            missing_scores = self._score_uncached_pairs(missing_pairs)
            if len(missing_scores) != len(missing_pairs):
                raise RuntimeError("semantic scorer returned an unexpected score count")
            additions: dict[str, float] = {}
            for key, score in zip(missing_keys, missing_scores, strict=True):
                value = float(score)
                if not math.isfinite(value):
                    raise RuntimeError("semantic scorer returned a non-finite score")
                additions[key] = value
            self._write_cached_scores(additions)
            cached.update(additions)
        return [cached[key] for key in pair_keys]

    def _score_uncached_pairs(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        tokenizer, model, torch = self._backend()
        scores: list[float] = []
        with torch.inference_mode():
            for start in range(0, len(pairs), self._config.batch_size):
                batch = pairs[start : start + self._config.batch_size]
                encoded = tokenizer(
                    [source for source, _ in batch],
                    [target for _, target in batch],
                    padding=True,
                    truncation="longest_first",
                    max_length=self._config.max_length,
                    return_tensors="pt",
                )
                encoded = {name: value.to(self._config.device) for name, value in encoded.items()}
                logits = model(**encoded).logits
                log_probabilities = torch.log_softmax(logits, dim=-1)[:, 0]
                scores.extend(float(value) for value in log_probabilities.cpu())
        return scores

    def _backend(self) -> tuple[Any, Any, Any]:
        if self._tokenizer is not None and self._model is not None:
            import torch

            return self._tokenizer, self._model, torch
        try:
            import torch
            from transformers import AutoTokenizer, BertForNextSentencePrediction
        except ImportError as exc:
            raise RuntimeError(
                "Install requirements-semantic-order.txt to use the BERT NSP scorer"
            ) from exc
        source = str(self._config.load_from or self._config.model)
        revision = None if self._config.load_from is not None else self._config.revision
        load_options = {
            "revision": revision,
            "local_files_only": self._config.local_files_only,
            "trust_remote_code": False,
        }
        self._tokenizer = AutoTokenizer.from_pretrained(source, **load_options)
        self._model = BertForNextSentencePrediction.from_pretrained(source, **load_options)
        self._model.to(self._config.device)
        self._model.eval()
        return self._tokenizer, self._model, torch

    def _read_cached_scores(self, pair_keys: Sequence[str]) -> dict[str, float]:
        if self._cache_path is None or not self._cache_path.is_file():
            return {}
        result: dict[str, float] = {}
        with self._connect_cache() as connection:
            for start in range(0, len(pair_keys), 500):
                chunk = pair_keys[start : start + 500]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"SELECT pair_key, score FROM semantic_scores "
                    f"WHERE scorer_key = ? AND pair_key IN ({placeholders})",
                    [self._scorer_key, *chunk],
                )
                result.update((str(key), float(score)) for key, score in rows)
        return result

    def _write_cached_scores(self, scores: Mapping[str, float]) -> None:
        if self._cache_path is None or not scores:
            return
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect_cache() as connection:
            connection.executemany(
                "INSERT OR REPLACE INTO semantic_scores (scorer_key, pair_key, score) "
                "VALUES (?, ?, ?)",
                [(self._scorer_key, key, float(score)) for key, score in scores.items()],
            )
            connection.commit()

    def _connect_cache(self) -> sqlite3.Connection:
        assert self._cache_path is not None
        connection = sqlite3.connect(self._cache_path, timeout=30)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS semantic_scores ("
            "scorer_key TEXT NOT NULL, "
            "pair_key TEXT NOT NULL, "
            "score REAL NOT NULL, "
            "PRIMARY KEY (scorer_key, pair_key))"
        )
        return connection


def create_semantic_pair_scorer(
    preset: str,
    *,
    load_from: str | Path | None = None,
    cache_path: str | Path | None = None,
    batch_size: int = 256,
    max_length: int = 96,
    device: str = "cpu",
    local_files_only: bool = False,
) -> SemanticPairScorer:
    normalized = preset.strip().lower()
    if normalized != BERT_TINY_NSP_PRESET:
        raise ValueError(f"unsupported semantic scorer preset: {preset}")
    return BertNspScorer(
        BertNspScorerConfig(
            load_from=load_from,
            cache_path=cache_path,
            batch_size=batch_size,
            max_length=max_length,
            device=device,
            local_files_only=local_files_only,
        )
    )


def semantic_scorer_identity(descriptor: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(descriptor),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _pair_key(source: str, target: str) -> str:
    payload = json.dumps([source, target], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
