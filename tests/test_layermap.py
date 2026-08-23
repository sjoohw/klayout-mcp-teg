from __future__ import annotations

import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.layermap import LayerSpec, load_layermap


def test_load_layermap_pair_and_mapping(tmp_path) -> None:
    path = tmp_path / "layers.yaml"
    path.write_text(
        "layers:\n  m1: [10, 2]\n  text: {layer: 100, datatype: 0}\n",
        encoding="utf-8",
    )

    result = load_layermap(path)

    assert result["m1"] == LayerSpec(10, 2)
    assert result["text"] == LayerSpec(100, 0)


def test_missing_m1_is_rejected(tmp_path) -> None:
    path = tmp_path / "layers.yaml"
    path.write_text("layers:\n  poly: [5, 0]\n", encoding="utf-8")

    with pytest.raises(AnalysisError) as failure:
        load_layermap(path)

    assert failure.value.code == "M1_NOT_IN_LAYERMAP"


@pytest.mark.parametrize("value", ["10/0", [10], [-1, 0], [True, 0], [10, 0.0]])
def test_invalid_m1_is_rejected(tmp_path, value) -> None:
    import yaml

    path = tmp_path / "layers.yaml"
    path.write_text(yaml.safe_dump({"layers": {"m1": value}}), encoding="utf-8")

    with pytest.raises(AnalysisError) as failure:
        load_layermap(path)

    assert failure.value.code == "INVALID_LAYERMAP"


def test_empty_layer_name_has_actionable_error(tmp_path) -> None:
    path = tmp_path / "layers.yaml"
    path.write_text("layers:\n  '': [1, 0]\n", encoding="utf-8")

    with pytest.raises(AnalysisError) as failure:
        load_layermap(path)

    assert failure.value.code == "INVALID_LAYERMAP"
    assert failure.value.next_action
