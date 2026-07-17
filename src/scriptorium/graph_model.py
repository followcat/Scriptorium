from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


GRAPH_MODEL_SECURITY = (
    "Load only locally generated model files; joblib loading can execute code."
)


@dataclass(frozen=True)
class GraphModelArtifact:
    model_path: Path
    manifest_path: Path
    manifest: dict[str, Any]
    bundle: dict[str, Any]


def save_graph_model(
    *,
    model_path: str | Path,
    schema: str,
    head: str,
    feature_version: str,
    threshold: float,
    estimator: Any,
    estimator_parameters: Mapping[str, Any],
    feature_count: int,
    nearest_candidates: int | None = None,
    train_corpus_manifest_sha256: str | None = None,
    fit_document_count: int | None = None,
    fit_page_count: int | None = None,
    fit_candidate_count: int | None = None,
    fit_positive_count: int | None = None,
    cross_validation_folds: int | None = None,
    minimum_edge_precision: float | None = None,
    minimum_selected_edges: int | None = None,
    random_seed: int | None = None,
    scikit_learn_version: str | None = None,
    extra_manifest: Mapping[str, Any] | None = None,
) -> GraphModelArtifact:
    """Serialize a review-only graph head with an adjacent SHA-256 manifest."""

    path = Path(model_path)
    if path.suffix != ".joblib":
        raise ValueError("graph model path must end with .joblib")
    path.parent.mkdir(parents=True, exist_ok=True)
    frozen_threshold = round(float(threshold), 8)
    bundle: dict[str, Any] = {
        "schema": schema,
        "head": head,
        "feature_version": feature_version,
        "threshold": frozen_threshold,
        "estimator": estimator,
        "estimator_parameters": dict(estimator_parameters),
        "feature_count": int(feature_count),
        "runtime_reorder": False,
    }
    if nearest_candidates is not None:
        bundle["nearest_candidates"] = int(nearest_candidates)
    joblib = _joblib_module()
    joblib.dump(bundle, path)
    model_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest: dict[str, Any] = {
        "schema": schema,
        "head": head,
        "feature_version": feature_version,
        "threshold": frozen_threshold,
        "feature_count": int(feature_count),
        "estimator_type": type(estimator).__name__,
        "estimator_parameters": dict(estimator_parameters),
        "model_file": path.name,
        "model_sha256": model_sha256,
        "runtime_reorder": False,
        "security": GRAPH_MODEL_SECURITY,
    }
    if nearest_candidates is not None:
        manifest["nearest_candidates"] = int(nearest_candidates)
    if train_corpus_manifest_sha256 is not None:
        manifest["train_corpus_manifest_sha256"] = train_corpus_manifest_sha256
    if fit_document_count is not None:
        manifest["fit_document_count"] = int(fit_document_count)
    if fit_page_count is not None:
        manifest["fit_page_count"] = int(fit_page_count)
    if fit_candidate_count is not None:
        manifest["fit_candidate_count"] = int(fit_candidate_count)
    if fit_positive_count is not None:
        manifest["fit_positive_count"] = int(fit_positive_count)
    if cross_validation_folds is not None:
        manifest["cross_validation_folds"] = int(cross_validation_folds)
    if minimum_edge_precision is not None:
        manifest["minimum_edge_precision"] = float(minimum_edge_precision)
    if minimum_selected_edges is not None:
        manifest["minimum_selected_edges"] = int(minimum_selected_edges)
    if random_seed is not None:
        manifest["random_seed"] = int(random_seed)
    if scikit_learn_version is not None:
        manifest["scikit_learn_version"] = scikit_learn_version
    if extra_manifest:
        for key, value in extra_manifest.items():
            if key in manifest:
                raise ValueError(f"extra manifest key collides with reserved field: {key}")
            manifest[key] = value
    manifest_path = path.with_suffix(f"{path.suffix}.manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return GraphModelArtifact(path, manifest_path, manifest, bundle)


def load_graph_model(
    model_path: str | Path,
    *,
    expected_schema: str,
    expected_head: str,
    expected_feature_version: str,
) -> GraphModelArtifact:
    """Load a hash-checked review-only graph model."""

    path = Path(model_path)
    manifest_path = path.with_suffix(f"{path.suffix}.manifest.json")
    if not path.is_file() or not manifest_path.is_file():
        raise ValueError("graph model and adjacent .joblib.manifest.json are required")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("graph model manifest is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise ValueError("graph model manifest must be a JSON object")
    expected_sha256 = str(manifest.get("model_sha256") or "")
    actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    if not expected_sha256 or actual_sha256 != expected_sha256:
        raise ValueError("graph model hash does not match its manifest")
    bundle = _joblib_module().load(path)
    if not isinstance(bundle, dict):
        raise ValueError("graph model bundle must be a mapping")
    if bundle.get("schema") != expected_schema or manifest.get("schema") != expected_schema:
        raise ValueError("unsupported graph model schema")
    if bundle.get("head") != expected_head or manifest.get("head") != expected_head:
        raise ValueError("graph model head does not match expected head")
    if (
        bundle.get("feature_version") != expected_feature_version
        or manifest.get("feature_version") != expected_feature_version
    ):
        raise ValueError("graph model feature version does not match expected version")
    if bundle.get("runtime_reorder") is not False or manifest.get("runtime_reorder") is not False:
        raise ValueError("graph model must declare runtime_reorder=false")
    if "estimator" not in bundle or "threshold" not in bundle:
        raise ValueError("graph model bundle requires estimator and threshold")
    if int(bundle.get("feature_count") or -1) != int(manifest.get("feature_count") or -2):
        raise ValueError("graph model feature_count does not match its manifest")
    return GraphModelArtifact(path, manifest_path, manifest, bundle)


def predict_feature_batches(
    estimator: Any,
    feature_batches: Sequence[Sequence[Sequence[float]]],
    *,
    numpy: Any,
) -> list[Any]:
    """Score candidates page-by-page to avoid one giant feature matrix."""

    scores: list[Any] = []
    for batch in feature_batches:
        if not batch:
            scores.append(numpy.asarray([], dtype=float))
            continue
        matrix = numpy.asarray(batch, dtype=float)
        scores.append(estimator.predict_proba(matrix)[:, 1])
    return scores


def flatten_page_scores(page_scores: Sequence[Any], *, numpy: Any) -> Any:
    if not page_scores:
        return numpy.asarray([], dtype=float)
    nonempty = [scores for scores in page_scores if len(scores)]
    if not nonempty:
        return numpy.asarray([], dtype=float)
    return numpy.concatenate(nonempty)


def _joblib_module() -> Any:
    try:
        import joblib
    except ImportError as exc:
        raise RuntimeError(
            "graph model serialization requires requirements-relation-ranker.txt"
        ) from exc
    return joblib
