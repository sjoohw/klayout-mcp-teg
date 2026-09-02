"""Load the PCellizer dock in a hidden KLayout GUI process."""

import json
import sys
from pathlib import Path


project_root = Path(str(project_root))
result_path = Path(str(result_path))
sys.path.insert(0, str(project_root / "src"))
sys.path.insert(0, str(project_root / "klayout_plugin"))

from pcellizer_panel import install_panel


dock = install_panel()
result = {
    "object_name": dock.objectName,
    "title": dock.windowTitle,
    "visible": dock.isVisible(),
    "copy_enabled": dock.copy_button.isEnabled(),
}
dock._capture_function = lambda: {
    "kind": "PCellizerParameterCapture",
    "endpoint_manifests": [{}, {}],
}
dock.capture_button.click()
result["capture_status"] = dock.status_label.text
result["copy_enabled_after_capture"] = dock.copy_button.isEnabled()
dock._snapshot_function = lambda **kwargs: {
    "manifest": {"snapshot_package_sha256": "a" * 64},
    "package_dir": "C:/snapshot/packages/" + "a" * 64,
}
dock.create_snapshot("ignored-test-root")
result["snapshot_status"] = dock.status_label.text
result["snapshot_enabled_after_capture"] = dock.snapshot_button.isEnabled()
result["package_path_enabled_after_snapshot"] = dock.copy_package_path_button.isEnabled()
result_path.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
