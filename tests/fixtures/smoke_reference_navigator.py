"""Load the Reference Navigator dock in a hidden KLayout GUI process."""

import json
import sys
from pathlib import Path


project_root = Path(str(project_root))
result_path = Path(str(result_path))
sys.path.insert(0, str(project_root / "src"))
sys.path.insert(0, str(project_root / "klayout_plugin"))

from reference_navigator_panel import install_reference_navigator


dock = install_reference_navigator()
view = {
    "view_id": "view-123",
    "reference_id": "ref-123",
    "concern": "contact_array",
    "usage_mode": "reference_precedent",
    "top_cell": "TOP",
    "stored_layout_path": "C:/reference/source.gds",
    "view_bbox_um": [0.0, 0.0, 1.0, 1.0],
    "relevant_layers": ["m1"],
}
dock._loader = lambda path: view
dock._opener = lambda manifest, window: {
    "top_cell": manifest["top_cell"],
    "usage_mode": manifest["usage_mode"],
}
dock._confirmer = lambda **kwargs: {
    "view_id": "view-123",
    "decision": "reference_precedent",
}

result = {
    "object_name": dock.objectName,
    "title": dock.windowTitle,
    "open_enabled_before_load": dock.open_button.isEnabled(),
    "confirm_enabled_before_open": dock.confirm_button.isEnabled(),
}
dock.load_manifest("C:/fake/view.json")
result["load_status"] = dock.status_label.text
result["open_enabled_after_load"] = dock.open_button.isEnabled()
result["copy_path_enabled_after_load"] = dock.copy_path_button.isEnabled()
dock.copy_path_button.click()
result["copy_path_status"] = dock.status_label.text
dock.open_button.click()
result["open_status"] = dock.status_label.text
result["confirm_enabled_after_open"] = dock.confirm_button.isEnabled()
dock.confirm_button.click()
result["confirm_status"] = dock.status_label.text
result["confirm_enabled_after_confirm"] = dock.confirm_button.isEnabled()
result_path.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
