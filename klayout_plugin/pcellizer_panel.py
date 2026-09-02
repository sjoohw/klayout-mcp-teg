"""Read-only KLayout dock for exact PCellizer selection/ruler capture."""

from __future__ import annotations

import json

import pya

from klayout_mcp.errors import AnalysisError
from klayout_mcp.pcellizer_klayout_capture import capture_parameter_from_view
from klayout_mcp.pcellizer_snapshot import create_pcellizer_snapshot_package


class PCellizerDock(pya.QDockWidget):
    """Small fail-closed UI; it never edits source geometry or hierarchy."""

    def __init__(self, main_window=None, capture_function=None, snapshot_function=None):
        super().__init__("TEG PCellizer", main_window)
        self.setObjectName("teg_pcellizer_dock")
        self._capture_function = capture_function or capture_parameter_from_view
        self._snapshot_function = snapshot_function or create_pcellizer_snapshot_package
        self._last_capture = None
        self._last_snapshot = None

        body = pya.QWidget(self)
        column = pya.QVBoxLayout(body)
        instructions = pya.QLabel(
            "1. Save the source layout\n"
            "2. Select one layer (or exact shapes)\n"
            "3. Select one Manhattan ruler whose endpoints touch edges\n"
            "4. Capture (current occurrence, exact DBU)",
            body,
        )
        instructions.setWordWrap(True)
        column.addWidget(instructions)

        self.status_label = pya.QLabel("Ready for read-only capture.", body)
        self.status_label.setWordWrap(True)
        column.addWidget(self.status_label)

        self.capture_button = pya.QPushButton("Capture selected edges + ruler", body)
        self.capture_button.clicked += self.capture
        column.addWidget(self.capture_button)

        self.copy_button = pya.QPushButton("Copy capture JSON", body)
        self.copy_button.clicked += self.copy_json
        self.copy_button.setEnabled(False)
        column.addWidget(self.copy_button)

        self.snapshot_button = pya.QPushButton(
            "Create standalone snapshot package...", body
        )
        self.snapshot_button.clicked += self.choose_snapshot_root
        self.snapshot_button.setEnabled(False)
        column.addWidget(self.snapshot_button)

        self.copy_package_path_button = pya.QPushButton(
            "Copy snapshot package path for MCP", body
        )
        self.copy_package_path_button.clicked += self.copy_package_path
        self.copy_package_path_button.setEnabled(False)
        column.addWidget(self.copy_package_path_button)

        self.output = pya.QPlainTextEdit(body)
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("Exact selection manifests will appear here.")
        column.addWidget(self.output)
        self.setWidget(body)

    def capture(self):
        try:
            payload = self._capture_function()
        except AnalysisError as error:
            self._last_capture = None
            self.copy_button.setEnabled(False)
            self.snapshot_button.setEnabled(False)
            self.copy_package_path_button.setEnabled(False)
            self.output.setPlainText(json.dumps(error.to_result(), indent=2, sort_keys=True))
            self.status_label.setText(f"Blocked: {error.code}")
            return None
        except Exception as error:  # GUI boundary: expose unexpected API failures.
            self._last_capture = None
            self.copy_button.setEnabled(False)
            self.snapshot_button.setEnabled(False)
            self.copy_package_path_button.setEnabled(False)
            self.output.setPlainText(
                json.dumps(
                    {
                        "ok": False,
                        "code": "PCELLIZER_GUI_CAPTURE_FAILED",
                        "message": str(error),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            self.status_label.setText("Blocked: PCELLIZER_GUI_CAPTURE_FAILED")
            return None
        self._last_capture = payload
        self._last_snapshot = None
        self.copy_package_path_button.setEnabled(False)
        encoded = json.dumps(payload, indent=2, sort_keys=True)
        self.output.setPlainText(encoded)
        self.copy_button.setEnabled(True)
        self.snapshot_button.setEnabled(True)
        self.status_label.setText(
            f"Captured {len(payload['endpoint_manifests'])} exact endpoint manifests."
        )
        return payload

    def copy_json(self):
        if self._last_capture is None:
            return False
        pya.QApplication.clipboard().setText(
            json.dumps(self._last_capture, indent=2, sort_keys=True)
        )
        self.status_label.setText("Capture JSON copied to clipboard.")
        return True

    def choose_snapshot_root(self):
        root = pya.QFileDialog.getExistingDirectory(
            self, "Choose PCellizer snapshot store", ""
        )
        if root:
            return self.create_snapshot(str(root))
        return None

    def copy_package_path(self):
        if self._last_snapshot is None:
            return False
        package_dir = self._last_snapshot.get("package_dir")
        if not isinstance(package_dir, str) or not package_dir:
            return False
        pya.QApplication.clipboard().setText(package_dir)
        self.status_label.setText("Snapshot package path copied for inspect_pcellizer_snapshot.")
        return True

    def create_snapshot(self, package_root):
        if self._last_capture is None:
            return None
        self._last_snapshot = None
        self.copy_package_path_button.setEnabled(False)
        try:
            result = self._snapshot_function(
                capture=self._last_capture, package_root=str(package_root)
            )
        except AnalysisError as error:
            self.status_label.setText(f"Snapshot blocked: {error.code}")
            self.output.setPlainText(json.dumps(error.to_result(), indent=2, sort_keys=True))
            return None
        except Exception as error:
            self.status_label.setText("Snapshot blocked: PCELLIZER_SNAPSHOT_FAILED")
            self.output.setPlainText(
                json.dumps(
                    {
                        "ok": False,
                        "code": "PCELLIZER_SNAPSHOT_FAILED",
                        "message": str(error),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return None
        self._last_snapshot = result
        self.copy_package_path_button.setEnabled(True)
        self.status_label.setText(
            f"Standalone snapshot: {result['manifest']['snapshot_package_sha256'][:12]}"
        )
        self.output.setPlainText(json.dumps(result["manifest"], indent=2, sort_keys=True))
        return result


_dock = None


def install_panel(main_window=None):
    """Install one dock instance in the current KLayout main window."""

    global _dock
    window = main_window or pya.Application.instance().main_window()
    if window is None:
        raise RuntimeError("KLayout main window is unavailable")
    if _dock is None:
        _dock = PCellizerDock(window)
        window.addDockWidget(pya.Qt.RightDockWidgetArea, _dock)
    _dock.show()
    _dock.raise_()
    return _dock
