"""KLayout macro entry point for the development-tree PCellizer dock."""

import sys
from pathlib import Path


project_root = Path(__file__).resolve().parents[1]
for import_root in (project_root / "src", project_root / "klayout_plugin"):
    value = str(import_root)
    if value not in sys.path:
        sys.path.insert(0, value)

from pcellizer_panel import install_panel
from reference_navigator_panel import install_reference_navigator


install_panel()
install_reference_navigator()
