from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.klayout_adapter import find_klayout_executable
from klayout_mcp.pcell_library import (
    LIBRARY_NAME,
    PCELL_NAME,
    generate_pcell_python_source,
)


class MockLayer:
    def __init__(self, layer: int, datatype: int):
        self.layer = layer
        self.datatype = datatype


def _layermap() -> dict[str, MockLayer]:
    return {
        "m1": MockLayer(1, 0),
        "active": MockLayer(2, 0),
        "poly": MockLayer(3, 0),
        "contact": MockLayer(4, 0),
    }


def test_generate_pcell_python_source_contains_contract() -> None:
    source = generate_pcell_python_source(_layermap())

    assert "class DutTransistorArrayPCell(pya.PCellDeclarationHelper):" in source
    assert "class TegPcellLibrary(pya.Library):" in source
    assert f'self.layout().register_pcell("{PCELL_NAME}", DutTransistorArrayPCell())' in source
    assert f'self.register("{LIBRARY_NAME}")' in source

    # Check parameter declarations
    assert 'self.param("w_um"' in source
    assert 'self.param("l_um"' in source
    assert 'self.param("array_rows"' in source
    assert 'self.param("array_cols"' in source
    assert 'self.param("routed_device_count"' in source

    # The generated PCell embeds and calls the canonical pure geometry builder.
    assert "def build_dut_geometry(" in source
    assert "pya.DBox(*shape[\"bbox_um\"])" in source
    assert "pya.Box(int(" not in source
    assert "PRODUCTION_READY = False" in source
    assert "DutParameters(" in source
    assert ").validate()" in source
    assert 'raise ValueError(f"{exc.code}: {exc.message}")' in source
    assert "self.w_um = 1.0" not in source
    assert "self.l_um = 0.1" not in source


def test_generate_pcell_python_source_with_layermap() -> None:
    layermap = {
        "m1": MockLayer(10, 0),
        "active": MockLayer(20, 0),
        "poly": MockLayer(30, 0),
        "contact": MockLayer(40, 0),
    }

    source = generate_pcell_python_source(layermap)
    assert "pya.LayerInfo(10, 0)" in source
    assert "pya.LayerInfo(20, 0)" in source
    assert "pya.LayerInfo(30, 0)" in source
    assert "pya.LayerInfo(40, 0)" in source


def test_generate_pcell_python_source_rejects_missing_or_colliding_layers() -> None:
    with pytest.raises(AnalysisError) as missing:
        generate_pcell_python_source({"m1": MockLayer(1, 0)})
    assert getattr(missing.value, "code", None) == "PCELL_LAYERMAP_INCOMPLETE"

    collision = _layermap()
    collision["active"] = MockLayer(1, 0)
    with pytest.raises(AnalysisError) as duplicate:
        generate_pcell_python_source(collision)
    assert getattr(duplicate.value, "code", None) == "PCELL_LAYERMAP_COLLISION"


def _installed_klayout() -> Path:
    try:
        return find_klayout_executable()
    except Exception:
        pytest.skip("KLayout executable is not installed")


def test_generated_pcell_rounds_dbu_coordinates_in_klayout(tmp_path) -> None:
    executable = _installed_klayout()
    output_path = tmp_path / "generated-pcell.gds"
    script_path = tmp_path / "generated_pcell_smoke.py"
    script_path.write_text(
        generate_pcell_python_source(_layermap())
        + f'''\n
layout = pya.Layout()
layout.dbu = 0.003
layout.register_pcell("SmokeDut", DutTransistorArrayPCell())
variant = layout.create_cell("SmokeDut", {{
    "w_um": 1.0,
    "l_um": 0.1,
    "array_rows": 1,
    "array_cols": 1,
    "routed_device_count": 1,
}})
active_layer = layout.layer(pya.LayerInfo(2, 0))
bbox = variant.bbox(active_layer)
assert [bbox.left, bbox.bottom, bbox.right, bbox.top] == [-167, -167, 167, 167]
m1_layer = layout.layer(pya.LayerInfo(1, 0))
m1_region = pya.Region(variant.begin_shapes_rec(m1_layer)).merged()
geometry = build_dut_geometry(DutParameters(
    w_um=1.0,
    l_um=0.1,
    array_rows=1,
    array_cols=1,
    routed_device_count=1,
))
def region_from_boxes(boxes):
    region = pya.Region()
    for box in boxes:
        region.insert(pya.DBox(*box).to_itype(layout.dbu))
    return region
expected_by_layer = {{
    active_layer: geometry.active_boxes_um,
    layout.layer(pya.LayerInfo(3, 0)): geometry.poly_boxes_um,
    layout.layer(pya.LayerInfo(4, 0)): geometry.contact_boxes_um,
    m1_layer: [shape["bbox_um"] for shape in geometry.m1_shapes_um],
}}
for layer_index, boxes in expected_by_layer.items():
    actual = pya.Region(variant.begin_shapes_rec(layer_index))
    assert (actual ^ region_from_boxes(boxes)).is_empty()
assert len(geometry.m1_shapes_um) == 10
assert sum(1 for _ in m1_region.each_merged()) == 6
net_regions = {{}}
for shape in geometry.m1_shapes_um:
    region = net_regions.setdefault(shape["net"], pya.Region())
    region.insert(pya.DBox(*shape["bbox_um"]).to_itype(layout.dbu))
nets = sorted(net_regions)
for left_index, left_net in enumerate(nets):
    for right_net in nets[left_index + 1:]:
        assert (net_regions[left_net] & net_regions[right_net]).is_empty()
repeat = layout.create_cell("SmokeDut", {{
    "w_um": 1.0,
    "l_um": 0.1,
    "array_rows": 1,
    "array_cols": 1,
    "routed_device_count": 1,
}})
assert repeat.cell_index() == variant.cell_index()
layout.write(r"{output_path}")
fresh = pya.Layout()
fresh.read(r"{output_path}")
assert fresh.dbu == 0.003
assert not fresh.top_cell().bbox().empty()
''',
        encoding="utf-8",
    )

    completed = subprocess.run(
        [str(executable), "-b", "-r", str(script_path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert output_path.is_file()


def test_live_registered_pcell_produces_geometry_in_klayout(tmp_path) -> None:
    executable = _installed_klayout()
    output_path = tmp_path / "live-pcell.gds"
    script_path = tmp_path / "live_pcell_smoke.py"
    source_root = Path(__file__).resolve().parents[1] / "src"
    script_path.write_text(
        f'''import sys
sys.path.insert(0, r"{source_root}")

import pya
from klayout_mcp.dut_geometry import DutParameters, build_dut_geometry
from klayout_mcp.errors import AnalysisError
from klayout_mcp.pcell_library import LIBRARY_NAME, PCELL_NAME, register_teg_library

try:
    register_teg_library({{
        "m1": (1, 0),
        "active": (1, 0),
        "poly": (3, 0),
        "contact": (4, 0),
    }})
except AnalysisError as exc:
    assert exc.code == "PCELL_LAYERMAP_COLLISION"
else:
    raise AssertionError("colliding live PCell layers must be rejected")

assert register_teg_library({{
    "m1": (1, 0),
    "active": (2, 0),
    "poly": (3, 0),
    "contact": (4, 0),
}})
layout = pya.Layout()
layout.dbu = 0.001
variant = layout.create_cell(PCELL_NAME, LIBRARY_NAME, {{
    "array_rows": 1,
    "array_cols": 1,
    "routed_device_count": 1,
}})
assert variant is not None
assert not variant.bbox().empty()
geometry = build_dut_geometry(DutParameters(
    array_rows=1,
    array_cols=1,
    routed_device_count=1,
))
def region_from_boxes(boxes):
    region = pya.Region()
    for box in boxes:
        region.insert(pya.DBox(*box).to_itype(layout.dbu))
    return region
expected_by_layer = {{
    layout.layer(pya.LayerInfo(2, 0)): geometry.active_boxes_um,
    layout.layer(pya.LayerInfo(3, 0)): geometry.poly_boxes_um,
    layout.layer(pya.LayerInfo(4, 0)): geometry.contact_boxes_um,
    layout.layer(pya.LayerInfo(1, 0)): [
        shape["bbox_um"] for shape in geometry.m1_shapes_um
    ],
}}
for layer_index, boxes in expected_by_layer.items():
    actual = pya.Region(variant.begin_shapes_rec(layer_index))
    assert (actual ^ region_from_boxes(boxes)).is_empty()
repeat = layout.create_cell(PCELL_NAME, LIBRARY_NAME, {{
    "array_rows": 1,
    "array_cols": 1,
    "routed_device_count": 1,
}})
assert repeat.cell_index() == variant.cell_index()
layout.write(r"{output_path}")
fresh = pya.Layout()
fresh.read(r"{output_path}")
assert fresh.dbu == 0.001
assert not fresh.top_cell().bbox().empty()
''',
        encoding="utf-8",
    )

    completed = subprocess.run(
        [str(executable), "-b", "-r", str(script_path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert output_path.is_file()
