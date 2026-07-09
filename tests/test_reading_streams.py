from scriptorium.reading_streams import assign_reading_streams_to_metadata


def test_body_segments_stay_main_without_structural_break_signal() -> None:
    metadata_items = [
        {
            "semantic_order": 1,
            "visual_order": 1,
            "reading_order_scope": "body",
            "flow_segment_index": 1,
            "column_span": "column",
            "reading_order_strategy": "column-flow-v1",
            "reading_order_evidence": ["column-flow"],
        },
        {
            "semantic_order": 2,
            "visual_order": 2,
            "reading_order_scope": "body",
            "flow_segment_index": 2,
            "column_span": "column",
            "reading_order_strategy": "column-flow-v1",
            "reading_order_evidence": ["column-flow"],
        },
    ]

    assign_reading_streams_to_metadata(
        metadata_items,
        order_key=lambda item: item["semantic_order"],
    )

    assert {item["reading_order_stream_id"] for item in metadata_items} == {"body-main"}
    assert [item["reading_order_stream_index"] for item in metadata_items] == [1, 2]


def test_full_width_breaks_create_local_body_segment_streams() -> None:
    metadata_items = [
        {
            "semantic_order": 1,
            "visual_order": 1,
            "reading_order_scope": "body",
            "flow_segment_index": 1,
            "column_span": "column",
            "reading_order_strategy": "column-flow-v1",
            "reading_order_evidence": ["column-flow"],
        },
        {
            "semantic_order": 2,
            "visual_order": 2,
            "reading_order_scope": "body",
            "flow_segment_index": 2,
            "column_span": "full",
            "reading_order_strategy": "column-flow-v1",
            "reading_order_evidence": ["full-width-flow-break"],
        },
        {
            "semantic_order": 3,
            "visual_order": 3,
            "reading_order_scope": "body",
            "flow_segment_index": 3,
            "column_span": "column",
            "reading_order_strategy": "column-flow-v1",
            "reading_order_evidence": ["column-flow"],
        },
    ]

    assign_reading_streams_to_metadata(
        metadata_items,
        order_key=lambda item: item["semantic_order"],
    )

    assert [item["reading_order_stream_id"] for item in metadata_items] == [
        "body-main",
        "body-segment-002",
        "body-segment-002",
    ]
    assert [item["reading_order_stream_index"] for item in metadata_items] == [1, 1, 2]


def test_recursive_xy_cut_regions_create_local_body_streams() -> None:
    metadata_items = [
        {
            "semantic_order": 1,
            "visual_order": 1,
            "reading_order_scope": "body",
            "flow_segment_index": 1,
            "column_span": "column",
            "reading_order_strategy": "recursive-xy-cut-v1",
            "reading_order_region_path": "root/h0/v0",
            "reading_order_evidence": ["recursive-xy-cut"],
        },
        {
            "semantic_order": 2,
            "visual_order": 2,
            "reading_order_scope": "body",
            "flow_segment_index": 2,
            "column_span": "column",
            "reading_order_strategy": "recursive-xy-cut-v1",
            "reading_order_region_path": "root/h1/v0",
            "reading_order_evidence": ["recursive-xy-cut"],
        },
    ]

    assign_reading_streams_to_metadata(
        metadata_items,
        order_key=lambda item: item["semantic_order"],
    )

    assert [item["reading_order_stream_type"] for item in metadata_items] == ["body", "body"]
    assert [item["reading_order_stream_id"] for item in metadata_items] == [
        "body-main",
        "body-segment-002",
    ]


def test_grid_island_uses_region_path_as_translation_stream_id() -> None:
    metadata_items = [
        {
            "semantic_order": 1,
            "visual_order": 1,
            "reading_order_scope": "body",
            "flow_segment_index": 1,
            "column_span": "grid-full",
            "reading_order_strategy": "mixed-grid-column-flow-v1",
            "reading_order_region_path": "root/grid-island-003",
            "reading_order_evidence": ["grid-island-row-major", "local-structure-grid"],
        },
        {
            "semantic_order": 2,
            "visual_order": 2,
            "reading_order_scope": "body",
            "flow_segment_index": 1,
            "column_span": "grid-full",
            "reading_order_strategy": "mixed-grid-column-flow-v1",
            "reading_order_region_path": "root/grid-island-003",
            "reading_order_evidence": ["grid-island-row-major", "local-structure-grid"],
        },
    ]

    assign_reading_streams_to_metadata(
        metadata_items,
        order_key=lambda item: (item["semantic_order"], item["visual_order"]),
    )

    assert [item["reading_order_stream_type"] for item in metadata_items] == ["grid-island", "grid-island"]
    assert [item["reading_order_stream_id"] for item in metadata_items] == ["grid-island-003", "grid-island-003"]
    assert [item["reading_order_stream_index"] for item in metadata_items] == [1, 2]


def test_external_structure_stream_metadata_is_preserved() -> None:
    metadata_items = [
        {
            "semantic_order": 1,
            "visual_order": 1,
            "reading_order_scope": "body",
            "flow_segment_index": 1,
            "column_span": "grid-external",
            "external_structure_stream_id": "product-row",
            "external_structure_stream_type": "grid-island",
            "external_structure_stream_index": 2,
        },
        {
            "semantic_order": 2,
            "visual_order": 2,
            "reading_order_scope": "body",
            "flow_segment_index": 1,
            "column_span": "grid-external",
            "external_structure_stream_id": "product-row",
            "external_structure_stream_type": "grid-island",
            "external_structure_stream_index": 1,
        },
    ]

    assign_reading_streams_to_metadata(
        metadata_items,
        order_key=lambda item: (item["semantic_order"], item["visual_order"]),
    )

    assert [item["reading_order_stream_type"] for item in metadata_items] == ["grid-island", "grid-island"]
    assert [item["reading_order_stream_id"] for item in metadata_items] == ["product-row", "product-row"]
    assert [item["reading_order_stream_index"] for item in metadata_items] == [2, 1]
