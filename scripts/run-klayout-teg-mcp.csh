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

exec python3 -m klayout_mcp.server
