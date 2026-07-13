from scriptorium.relation_noise import perturb_relation_structure


def _payload() -> dict:
    return {
        "uid": "page-1",
        "img": {"width": 100, "height": 100},
        "document": [
            {
                "id": f"line-{index}",
                "block_id": f"block-{index // 2}",
                "type": "figure" if index == 0 else "text",
                "box": [10, 10 + index * 8, 90, 16 + index * 8],
                "text": "Figure 1. Caption" if index == 1 else f"Line {index}",
            }
            for index in range(40)
        ],
        "relations_removed": True,
    }


def test_relation_noise_is_deterministic_and_answer_free() -> None:
    first, first_diagnostics = perturb_relation_structure(_payload(), profile="stress")
    second, second_diagnostics = perturb_relation_structure(_payload(), profile="stress")

    assert first == second
    assert first_diagnostics == second_diagnostics
    assert first["relations_removed"] is True
    assert "successor_edges" not in first
    assert first_diagnostics["source_element_count"] == 40
    assert first_diagnostics["jittered_element"] == first_diagnostics["retained_element_count"]


def test_clean_relation_noise_is_identity() -> None:
    payload = _payload()

    result, diagnostics = perturb_relation_structure(payload, profile="clean")

    assert result == payload
    assert diagnostics["retained_element_count"] == 40
    assert "jittered_element" not in diagnostics
