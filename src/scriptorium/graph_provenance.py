from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


GRAPH_PROPOSAL_PROVENANCE_SCHEMA = "scriptorium-graph-proposal-provenance/v1"
DOCUMENT_OOF_MODE = "document-oof"
FROZEN_FIT_MODEL_MODE = "frozen-fit-model"
SERIALIZED_FIT_MODEL_MODE = "serialized-fit-model"


def benchmark_prediction_provenance(
    *,
    producer_schema: str,
    head: str,
    feature_version: str,
    prediction_mode: str,
    train_corpus_manifest_sha256: str,
    source_corpus_manifest_sha256: str,
    cross_validation_folds: int,
) -> dict[str, Any]:
    if prediction_mode not in {DOCUMENT_OOF_MODE, FROZEN_FIT_MODEL_MODE}:
        raise ValueError("benchmark graph prediction mode is unsupported")
    return {
        "schema": GRAPH_PROPOSAL_PROVENANCE_SCHEMA,
        "producer_schema": producer_schema,
        "head": head,
        "feature_version": feature_version,
        "prediction_mode": prediction_mode,
        "threshold_selection": "fit-document-oof",
        "train_corpus_manifest_sha256": train_corpus_manifest_sha256,
        "source_corpus_manifest_sha256": source_corpus_manifest_sha256,
        "cross_validation_unit": "document",
        "cross_validation_folds": int(cross_validation_folds),
        "fit_model_training": (
            "fold-excluding-document"
            if prediction_mode == DOCUMENT_OOF_MODE
            else "all-fit-documents"
        ),
    }


def serialized_prediction_provenance(
    *,
    producer_schema: str,
    head: str,
    feature_version: str,
    model_manifest: Mapping[str, Any],
    model_manifest_path: str | Path,
) -> dict[str, Any]:
    train_sha256 = str(model_manifest.get("train_corpus_manifest_sha256") or "")
    model_sha256 = str(model_manifest.get("model_sha256") or "")
    if not _is_sha256(train_sha256) or not _is_sha256(model_sha256):
        raise ValueError("serialized graph model requires corpus and model SHA-256 provenance")
    return {
        "schema": GRAPH_PROPOSAL_PROVENANCE_SCHEMA,
        "producer_schema": producer_schema,
        "head": head,
        "feature_version": feature_version,
        "prediction_mode": SERIALIZED_FIT_MODEL_MODE,
        "threshold_selection": "fit-document-oof",
        "train_corpus_manifest_sha256": train_sha256,
        "source_corpus_manifest_sha256": None,
        "cross_validation_unit": "document",
        "cross_validation_folds": int(model_manifest.get("cross_validation_folds") or 0),
        "fit_model_training": "all-fit-documents",
        "model_sha256": model_sha256,
        "model_manifest_sha256": file_sha256(Path(model_manifest_path)),
    }


def proposal_provenance_for_input(
    base: Mapping[str, Any],
    *,
    input_sha256: object,
) -> dict[str, Any]:
    sha256 = str(input_sha256 or "")
    if not _is_sha256(sha256):
        raise ValueError("graph proposal input requires a valid SHA-256")
    return {**dict(base), "input_sha256": sha256}


def validate_benchmark_provenance(
    payload: object,
    *,
    expected_producer_schema: str,
    expected_head: str,
    expected_feature_version: str,
    expected_prediction_mode: str,
    expected_input_sha256: str,
    expected_train_corpus_manifest_sha256: str,
    expected_source_corpus_manifest_sha256: str,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("graph proposal requires prediction_provenance")
    provenance = dict(payload)
    required = {
        "schema": GRAPH_PROPOSAL_PROVENANCE_SCHEMA,
        "producer_schema": expected_producer_schema,
        "head": expected_head,
        "feature_version": expected_feature_version,
        "prediction_mode": expected_prediction_mode,
        "threshold_selection": "fit-document-oof",
        "input_sha256": expected_input_sha256,
        "train_corpus_manifest_sha256": expected_train_corpus_manifest_sha256,
        "source_corpus_manifest_sha256": expected_source_corpus_manifest_sha256,
        "cross_validation_unit": "document",
        "fit_model_training": (
            "fold-excluding-document"
            if expected_prediction_mode == DOCUMENT_OOF_MODE
            else "all-fit-documents"
        ),
    }
    for key, expected in required.items():
        if provenance.get(key) != expected:
            raise ValueError(
                f"graph proposal provenance {key} does not match expected {expected!r}"
            )
    if int(provenance.get("cross_validation_folds") or 0) < 2:
        raise ValueError("graph proposal provenance requires document cross-validation folds")
    return provenance


def input_payload_sha256(source: str | Path | Mapping[str, Any]) -> str:
    if isinstance(source, Mapping):
        encoded = json.dumps(
            dict(source),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
    return file_sha256(Path(source))


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
