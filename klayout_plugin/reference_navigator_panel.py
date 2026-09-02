"""KLayout dock for opening and confirming full reference GDS views."""

from __future__ import annotations

import json

import pya

from klayout_mcp.errors import AnalysisError
from klayout_mcp.reference_library import (
    load_reference_view_manifest,
    record_reference_gui_confirmation,
)


def open_reference_view_in_klayout(view_manifest, main_window=None):
    """Open the immutable full GDS and mark the confirmed top-view ROI."""

    window = main_window or pya.Application.instance().main_window()
    if window is None:
        raise RuntimeError("KLayout main window is unavailable")
    layout = pya.Layout()
    layout.read(str(view_manifest["stored_layout_path"]))
    top = layout.cell(str(view_manifest["top_cell"]))
    if top is None:
        raise RuntimeError("Reference top cell is unavailable")
    view = window.current_view()
    if view is None:
        window.create_view()
        view = window.current_view()
    if view is None:
        raise RuntimeError("KLayout layout view is unavailable")
    cellview_index = view.show_layout(layout, False)
    view.active_cellview_index = cellview_index
    cellview = view.cellview(cellview_index)
    if cellview is None:
        raise RuntimeError("KLayout cell view is unavailable")
    cellview.cell_name = str(view_manifest["top_cell"])
    if not cellview.is_valid():
        raise RuntimeError("Reference top cell could not be activated")
    view.add_missing_layers()
    view.max_hier()
    x1, y1, x2, y2 = [float(value) for value in view_manifest["view_bbox_um"]]
    margin = max((x2 - x1) * 0.1, (y2 - y1) * 0.1, float(layout.dbu) * 10.0)
    view.zoom_box(pya.DBox(x1 - margin, y1 - margin, x2 + margin, y2 + margin))
    marker = pya.Marker()
    marker.set(pya.DBox(x1, y1, x2, y2))
    marker.color = 0xFF00FF
    marker.frame_color = 0xFF00FF
    marker.line_width = 2
    marker.dither_pattern = -1
    marker.halo = 1
    view.add_marker(marker)
    return {
        "ok": True,
        "view_id": view_manifest["view_id"],
        "reference_id": view_manifest["reference_id"],
        "top_cell": view_manifest["top_cell"],
        "view_bbox_um": view_manifest["view_bbox_um"],
        "relevant_layers": view_manifest["relevant_layers"],
        "usage_mode": view_manifest["usage_mode"],
        "full_reference_opened": True,
        "source_geometry_modified": False,
    }


class ReferenceNavigatorDock(pya.QDockWidget):
    """Reference browser; confirmation is possible only after opening the GDS."""

    def __init__(self, main_window=None, loader=None, opener=None, confirmer=None):
        super().__init__("TEG Reference Navigator", main_window)
        self.setObjectName("teg_reference_navigator_dock")
        self._main_window = main_window
        self._loader = loader or load_reference_view_manifest
        self._opener = opener or open_reference_view_in_klayout
        self._confirmer = confirmer or record_reference_gui_confirmation
        self._manifest_path = None
        self._view_manifest = None
        self._opened = False

        body = pya.QWidget(self)
        column = pya.QVBoxLayout(body)
        help_label = pya.QLabel(
            "1. Load the view.json returned by MCP\n"
            "2. Open the full immutable GDS and inspect the marked ROI\n"
            "3. Confirm the selected style or DRC precedent",
            body,
        )
        help_label.setWordWrap(True)
        column.addWidget(help_label)

        self.status_label = pya.QLabel("No reference view loaded.", body)
        self.status_label.setWordWrap(True)
        column.addWidget(self.status_label)

        self.load_button = pya.QPushButton("Load reference view.json...", body)
        self.load_button.clicked += self.choose_manifest
        column.addWidget(self.load_button)

        self.open_button = pya.QPushButton("Open full reference in KLayout", body)
        self.open_button.clicked += self.open_reference
        self.open_button.setEnabled(False)
        column.addWidget(self.open_button)

        self.copy_path_button = pya.QPushButton("Copy full Ref GDS path", body)
        self.copy_path_button.clicked += self.copy_reference_path
        self.copy_path_button.setEnabled(False)
        column.addWidget(self.copy_path_button)

        self.justification = pya.QLineEdit(body)
        self.justification.setPlaceholderText("Optional confirmation note")
        column.addWidget(self.justification)

        self.confirm_button = pya.QPushButton("Use this reference", body)
        self.confirm_button.clicked += self.confirm_reference
        self.confirm_button.setEnabled(False)
        column.addWidget(self.confirm_button)

        self.output = pya.QPlainTextEdit(body)
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("Reference identity and scope will appear here.")
        column.addWidget(self.output)
        self.setWidget(body)

    def _blocked(self, error):
        if isinstance(error, AnalysisError):
            payload = error.to_result()
            code = error.code
        else:
            code = "REFERENCE_NAVIGATOR_FAILED"
            payload = {"ok": False, "code": code, "message": str(error)}
        self.status_label.setText(f"Blocked: {code}")
        self.output.setPlainText(json.dumps(payload, indent=2, sort_keys=True))
        self.confirm_button.setEnabled(False)
        return None

    def choose_manifest(self):
        path = pya.QFileDialog.getOpenFileName(
            self, "Choose reference view manifest", "", "Reference view (view.json)"
        )
        if path:
            return self.load_manifest(str(path))
        return None

    def load_manifest(self, manifest_path):
        try:
            view = self._loader(str(manifest_path))
        except Exception as error:
            return self._blocked(error)
        self._manifest_path = str(manifest_path)
        self._view_manifest = view
        self._opened = False
        self.open_button.setEnabled(True)
        self.copy_path_button.setEnabled(True)
        self.confirm_button.setEnabled(False)
        self.status_label.setText(
            f"Loaded {view['reference_id']} / {view['concern']} / {view['usage_mode']}"
        )
        self.output.setPlainText(json.dumps(view, indent=2, sort_keys=True))
        return view

    def copy_reference_path(self):
        if self._view_manifest is None:
            return False
        path = self._view_manifest.get("stored_layout_path")
        if not isinstance(path, str) or not path:
            return False
        pya.QApplication.clipboard().setText(path)
        self.status_label.setText("Full Ref GDS path copied to clipboard.")
        return True

    def open_reference(self):
        if self._view_manifest is None:
            return None
        try:
            result = self._opener(self._view_manifest, self._main_window)
        except Exception as error:
            return self._blocked(error)
        self._opened = True
        self.confirm_button.setEnabled(True)
        self.status_label.setText(
            f"Inspecting full GDS: {result['top_cell']} / {result['usage_mode']}"
        )
        return result

    def confirm_reference(self):
        if not self._opened or self._manifest_path is None:
            return None
        note = self.justification.text.strip() or None
        try:
            confirmation = self._confirmer(
                view_manifest_path=self._manifest_path,
                justification=note,
            )
        except Exception as error:
            return self._blocked(error)
        self.confirm_button.setEnabled(False)
        self.status_label.setText(
            f"Confirmed {confirmation['view_id']} as {confirmation['decision']}"
        )
        self.output.setPlainText(json.dumps(confirmation, indent=2, sort_keys=True))
        return confirmation


_dock = None


def install_reference_navigator(main_window=None):
    global _dock
    window = main_window or pya.Application.instance().main_window()
    if window is None:
        raise RuntimeError("KLayout main window is unavailable")
    if _dock is None:
        _dock = ReferenceNavigatorDock(window)
        window.addDockWidget(pya.Qt.RightDockWidgetArea, _dock)
    _dock.show()
    _dock.raise_()
    return _dock
