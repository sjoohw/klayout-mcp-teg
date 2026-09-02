from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = PROJECT_ROOT / "scripts" / "run-klayout-teg-mcp.csh"


def test_csh_launcher_source_declares_interpreter_precedence_and_preflight() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    explicit = source.index("KLAYOUT_MCP_PYTHON")
    repository_venv = source.index("$project_root/.venv/bin/python")
    system_python = source.index("which python3")

    assert explicit < repository_venv < system_python
    assert "sys.version_info >= (3, 11)" in source
    assert "import mcp, yaml, klayout_mcp" in source
    assert 'exec "$mcp_python" -m klayout_mcp.server' in source
