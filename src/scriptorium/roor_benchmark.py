from __future__ import annotations

import json
from dataclasses import dataclass
from http.client import IncompleteRead
from pathlib import Path
from time import sleep
from typing import Callable, Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


RoorSplit = Literal["train", "val"]
RoorDownloader = Callable[[str], bytes]

ROOR_DATASET_REPOSITORY = "https://github.com/chongzhangFDU/ROOR-Datasets"
ROOR_DATASET_LICENSE = "CC BY 4.0"
# Keep benchmark acquisition stable even when the upstream default branch changes.
ROOR_DATA_REVISION = "6b5ca2b2cc6ad02ab1dd8ec1c17551ab614f0aa0"
ROOR_DATA_BASE_URL = f"https://raw.githubusercontent.com/chongzhangFDU/ROOR-Datasets/{ROOR_DATA_REVISION}/data"
ROOR_FETCH_SCHEMA = "scriptorium-roor-benchmark/v1"
ROOR_DOWNLOAD_ATTEMPTS = 3
ROOR_DOWNLOAD_RETRY_DELAY_SECONDS = 0.5


@dataclass(frozen=True)
class RoorBenchmarkSample:
    sample_id: str
    image_path: Path
    structure_path: Path
    semantic_sidecar_path: Path
    annotation_path: Path


@dataclass(frozen=True)
class RoorBenchmarkFetchResult:
    out_dir: Path
    manifest_path: Path
    split: RoorSplit
    samples: tuple[RoorBenchmarkSample, ...]


def fetch_roor_benchmark_samples(
    out_dir: str | Path,
    *,
    split: RoorSplit = "val",
    sample_count: int = 5,
    refresh: bool = False,
    downloader: RoorDownloader | None = None,
) -> RoorBenchmarkFetchResult:
    """Fetch a deterministic official ROOR split prefix for relation evaluation.

    The generated structure JSON deliberately contains only image metadata and
    layout/text anchors.  It removes ``ro_linkings`` and all task labels, while
    the adjacent ``.semantic-order.json`` keeps the untouched official relation
    annotations for evaluation.  This makes the generated sample suitable for
    testing geometry/stream candidates without passing the answer relations to
    ``--structure-json``.
    """

    if split not in {"train", "val"}:
        raise ValueError("ROOR split must be 'train' or 'val'.")
    if sample_count < 1:
        raise ValueError("ROOR sample_count must be at least 1.")

    download = downloader or _download_bytes
    split_url = f"{ROOR_DATA_BASE_URL}/data.{split}.txt"
    split_names = _split_sample_names(_download_with_retry(download, split_url))
    if sample_count > len(split_names):
        raise ValueError(
            f"ROOR {split} split has {len(split_names)} samples; requested {sample_count}."
        )

    target = Path(out_dir)
    images_dir = target / "images"
    structure_dir = target / "structure"
    annotations_dir = target / "annotations"
    images_dir.mkdir(parents=True, exist_ok=True)
    structure_dir.mkdir(parents=True, exist_ok=True)
    annotations_dir.mkdir(parents=True, exist_ok=True)

    samples: list[RoorBenchmarkSample] = []
    manifest_samples: list[dict[str, str]] = []
    for annotation_name in split_names[:sample_count]:
        sample_id = _sample_id(annotation_name)
        annotation_url = f"{ROOR_DATA_BASE_URL}/jsons/{annotation_name}"
        raw_annotation = _load_annotation(_download_with_retry(download, annotation_url), annotation_url)
        image_name = _annotation_image_name(raw_annotation, annotation_url)
        image_url = f"{ROOR_DATA_BASE_URL}/images/{image_name}"

        image_path = images_dir / image_name
        annotation_path = annotations_dir / f"{sample_id}.roor.json"
        structure_path = structure_dir / f"{sample_id}.structure.json"
        semantic_sidecar_path = image_path.with_suffix(".semantic-order.json")

        if refresh or not image_path.exists():
            image_path.write_bytes(_download_with_retry(download, image_url))
        annotation_text = json.dumps(raw_annotation, indent=2, ensure_ascii=False) + "\n"
        if refresh or not annotation_path.exists():
            annotation_path.write_text(annotation_text, encoding="utf-8")
        if refresh or not semantic_sidecar_path.exists():
            semantic_sidecar_path.write_text(annotation_text, encoding="utf-8")
        if refresh or not structure_path.exists():
            structure_path.write_text(
                json.dumps(_layout_anchor_payload(raw_annotation), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

        samples.append(
            RoorBenchmarkSample(
                sample_id=sample_id,
                image_path=image_path,
                structure_path=structure_path,
                semantic_sidecar_path=semantic_sidecar_path,
                annotation_path=annotation_path,
            )
        )
        manifest_samples.append(
            {
                "id": sample_id,
                "image": str(image_path.relative_to(target)),
                "structure": str(structure_path.relative_to(target)),
                "semantic_sidecar": str(semantic_sidecar_path.relative_to(target)),
                "annotation": str(annotation_path.relative_to(target)),
                "image_url": image_url,
                "annotation_url": annotation_url,
            }
        )

    manifest_path = target / "roor_benchmark_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema": ROOR_FETCH_SCHEMA,
                "dataset": "ROOR",
                "repository": ROOR_DATASET_REPOSITORY,
                "revision": ROOR_DATA_REVISION,
                "license": ROOR_DATASET_LICENSE,
                "split": split,
                "selection": "published-split-prefix",
                "sample_count": len(samples),
                "structure_input": {
                    "kind": "layout-anchor-only",
                    "relations_removed": True,
                    "description": (
                        "Uses official image/text/bbox anchors without ro_linkings or task labels. "
                        "The adjacent semantic sidecar remains evaluation-only."
                    ),
                },
                "samples": manifest_samples,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return RoorBenchmarkFetchResult(
        out_dir=target,
        manifest_path=manifest_path,
        split=split,
        samples=tuple(samples),
    )


def _download_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "Scriptorium/0.1 (+https://github.com/followcat/Scriptorium)"})
    with urlopen(request, timeout=30) as response:
        return response.read()


def _download_with_retry(download: RoorDownloader, url: str) -> bytes:
    last_error: BaseException | None = None
    for attempt in range(1, ROOR_DOWNLOAD_ATTEMPTS + 1):
        try:
            return download(url)
        except HTTPError as error:
            last_error = error
            if not _retryable_http_status(error.code):
                raise RuntimeError(f"ROOR download failed for {url}: HTTP {error.code}.") from error
        except (URLError, TimeoutError, OSError, IncompleteRead) as error:
            last_error = error

        if attempt < ROOR_DOWNLOAD_ATTEMPTS:
            sleep(ROOR_DOWNLOAD_RETRY_DELAY_SECONDS * attempt)

    if isinstance(last_error, HTTPError):
        detail = f"HTTP {last_error.code}"
    elif isinstance(last_error, URLError):
        detail = str(last_error.reason)
    else:
        detail = str(last_error) or type(last_error).__name__
    raise RuntimeError(
        f"ROOR download failed for {url} after {ROOR_DOWNLOAD_ATTEMPTS} attempts: {detail}."
    ) from last_error


def _retryable_http_status(status: int) -> bool:
    return status in {408, 425, 429} or status >= 500


def _split_sample_names(payload: bytes) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for raw_name in payload.decode("utf-8").splitlines():
        name = raw_name.strip()
        if not name:
            continue
        if Path(name).name != name or Path(name).suffix.lower() != ".json":
            raise ValueError(f"Invalid ROOR split entry: {name!r}.")
        if name in seen:
            raise ValueError(f"Duplicate ROOR split entry: {name!r}.")
        names.append(name)
        seen.add(name)
    if not names:
        raise ValueError("ROOR split did not contain any sample entries.")
    return names


def _sample_id(annotation_name: str) -> str:
    sample_id = Path(annotation_name).stem.strip()
    if not sample_id:
        raise ValueError(f"Invalid ROOR annotation name: {annotation_name!r}.")
    return sample_id


def _load_annotation(payload: bytes, source_url: str) -> dict[str, object]:
    try:
        raw = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Invalid ROOR annotation JSON from {source_url}.") from error
    if not isinstance(raw, dict):
        raise ValueError(f"ROOR annotation from {source_url} must be an object.")
    if not isinstance(raw.get("document"), list):
        raise ValueError(f"ROOR annotation from {source_url} does not contain a document list.")
    if not isinstance(raw.get("ro_linkings"), list):
        raise ValueError(f"ROOR annotation from {source_url} does not contain ro_linkings.")
    return raw


def _annotation_image_name(annotation: dict[str, object], source_url: str) -> str:
    image = annotation.get("img")
    if not isinstance(image, dict):
        raise ValueError(f"ROOR annotation from {source_url} does not contain image metadata.")
    raw_name = str(image.get("fname") or "").strip()
    image_name = Path(raw_name).name
    if not image_name or image_name != raw_name.removeprefix("images/") or Path(image_name).suffix.lower() != ".png":
        raise ValueError(f"Invalid ROOR image reference {raw_name!r} in {source_url}.")
    return image_name


def _layout_anchor_payload(annotation: dict[str, object]) -> dict[str, object]:
    """Return the public layout/text anchor payload with answer relations removed."""

    return {
        "schema": "scriptorium-roor-layout-anchor-only/v1",
        "uid": annotation.get("uid"),
        "img": annotation.get("img"),
        "document": annotation.get("document"),
        "relations_removed": True,
    }
