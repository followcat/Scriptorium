from __future__ import annotations

import json

import pytest

import scriptorium.roor_benchmark as roor_benchmark
from scriptorium.roor_benchmark import ROOR_DATA_BASE_URL, ROOR_DATA_REVISION, fetch_roor_benchmark_samples


def test_fetch_roor_samples_separates_layout_anchors_from_relation_labels(tmp_path) -> None:
    records = {
        f"{ROOR_DATA_BASE_URL}/data.val.txt": b"sample-b.json\nsample-a.json\n",
        f"{ROOR_DATA_BASE_URL}/jsons/sample-b.json": _annotation_bytes("sample-b"),
        f"{ROOR_DATA_BASE_URL}/jsons/sample-a.json": _annotation_bytes("sample-a"),
        f"{ROOR_DATA_BASE_URL}/images/sample-b.png": b"sample-b-image",
        f"{ROOR_DATA_BASE_URL}/images/sample-a.png": b"sample-a-image",
    }
    downloaded: list[str] = []

    def download(url: str) -> bytes:
        downloaded.append(url)
        return records[url]

    result = fetch_roor_benchmark_samples(
        tmp_path / "roor",
        split="val",
        sample_count=2,
        downloader=download,
    )

    assert [sample.sample_id for sample in result.samples] == ["sample-b", "sample-a"]
    assert result.samples[0].image_path.read_bytes() == b"sample-b-image"
    assert result.samples[0].semantic_sidecar_path.name == "sample-b.semantic-order.json"

    layout_payload = json.loads(result.samples[0].structure_path.read_text(encoding="utf-8"))
    semantic_payload = json.loads(result.samples[0].semantic_sidecar_path.read_text(encoding="utf-8"))
    assert layout_payload["schema"] == "scriptorium-roor-layout-anchor-only/v1"
    assert layout_payload["relations_removed"] is True
    assert "ro_linkings" not in layout_payload
    assert "label_entities" not in layout_payload
    assert layout_payload["document"][0]["text"] == "Sample B"
    assert semantic_payload["ro_linkings"] == [[0, 1]]
    assert semantic_payload["label_entities"] == [{"entity_id": 0, "label": "question", "word_idx": [0]}]

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["selection"] == "published-split-prefix"
    assert manifest["sample_count"] == 2
    assert manifest["revision"] == ROOR_DATA_REVISION
    assert [sample["id"] for sample in manifest["samples"]] == ["sample-b", "sample-a"]
    assert downloaded[0].endswith("data.val.txt")


def test_fetch_roor_samples_rejects_request_larger_than_official_split(tmp_path) -> None:
    def download(url: str) -> bytes:
        assert url.endswith("data.val.txt")
        return b"single.json\n"

    with pytest.raises(ValueError, match="has 1 samples"):
        fetch_roor_benchmark_samples(
            tmp_path / "roor",
            split="val",
            sample_count=2,
            downloader=download,
        )


def test_fetch_roor_samples_retries_transient_download_errors(tmp_path, monkeypatch) -> None:
    records = {
        f"{ROOR_DATA_BASE_URL}/data.val.txt": b"sample.json\n",
        f"{ROOR_DATA_BASE_URL}/jsons/sample.json": _annotation_bytes("sample"),
        f"{ROOR_DATA_BASE_URL}/images/sample.png": b"sample-image",
    }
    attempts: dict[str, int] = {}

    def download(url: str) -> bytes:
        attempts[url] = attempts.get(url, 0) + 1
        if url.endswith("sample.png") and attempts[url] == 1:
            raise TimeoutError("temporary network failure")
        return records[url]

    monkeypatch.setattr(roor_benchmark, "sleep", lambda _delay: None)
    result = fetch_roor_benchmark_samples(
        tmp_path / "roor",
        split="val",
        sample_count=1,
        downloader=download,
    )

    assert result.samples[0].image_path.read_bytes() == b"sample-image"
    assert attempts[f"{ROOR_DATA_BASE_URL}/images/sample.png"] == 2


def _annotation_bytes(sample_id: str) -> bytes:
    label = sample_id.replace("sample-", "Sample ").title()
    return json.dumps(
        {
            "uid": sample_id,
            "img": {"fname": f"images/{sample_id}.png", "height": 1000, "width": 800},
            "document": [
                {"id": 0, "box": [10, 10, 80, 25], "text": label, "words": []},
                {"id": 1, "box": [10, 40, 80, 55], "text": "Answer", "words": []},
            ],
            "label_entities": [{"entity_id": 0, "label": "question", "word_idx": [0]}],
            "label_linkings": [[0, 1]],
            "ro_linkings": [[0, 1]],
        }
    ).encode("utf-8")
