#!/bin/csh -f

# Source-checkout launcher for Linux/csh environments. KLayout itself remains
# optional until a layout-backed MCP tool is called.
set script_dir = `dirname "$0"`
set project_root = `cd "$script_dir/.." && pwd`

if ( ! $?PYTHONPATH ) then
    setenv PYTHONPATH "$project_root/src"
else
    setenv PYTHONPATH "$project_root/src:$PYTHONPATH"
endif

set mcp_python = ""
if ( $?KLAYOUT_MCP_PYTHON ) then
    set mcp_python = "$KLAYOUT_MCP_PYTHON"
    if ( ! -x "$mcp_python" ) then
        echo "KLAYOUT_MCP_PYTHON is not an executable file: $mcp_python" > /dev/stderr
        exit 1
    endif
else if ( -x "$project_root/.venv/bin/python" ) then
    set mcp_python = "$project_root/.venv/bin/python"
else
    which python3 >& /dev/null
    if ( $status != 0 ) then
        echo "No Python interpreter found; set KLAYOUT_MCP_PYTHON or create .venv." > /dev/stderr
        exit 1
    endif
    set mcp_python = `which python3`
endif

"$mcp_python" -c 'import sys; assert sys.version_info >= (3, 11); import mcp, yaml, klayout_mcp' >& /dev/null
if ( $status != 0 ) then
    echo "Selected Python must be >=3.11 and import mcp, yaml, and klayout_mcp: $mcp_python" > /dev/stderr
    exit 1
endif

exec "$mcp_python" -m klayout_mcp.server
