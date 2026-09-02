"""Thin KLayout subprocess entrypoint and operation dispatcher."""

import json
import os
import sys


PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, PACKAGE_ROOT)

from klayout_mcp.worker_assembly import assemble_teg
from klayout_mcp.worker_compare import (
    compare_kelvin_layouts,
    compare_layouts,
)
from klayout_mcp.worker_drawing import draw_manhattan_layout
from klayout_mcp.worker_dut_corpus import inspect_dut_corpus
from klayout_mcp.worker_inventory import inspect_layout
from klayout_mcp.worker_kelvin import generate_kelvin_m1_teg
from klayout_mcp.worker_padset import analyze_padset
from klayout_mcp.worker_pad_macro import compose_pad_macro_overlay, inspect_pad_macro
from klayout_mcp.worker_style import extract_layout_style
from klayout_mcp.worker_pcellizer import inventory_pcellizer_hierarchy
from klayout_mcp.worker_pcellizer_batch import generate_pcellizer_batch
from klayout_mcp.worker_protocol import worker_error


HANDLERS = {
    "analyze_padset_integrated": analyze_padset,
    "inspect_pad_macro": inspect_pad_macro,
    "compose_pad_macro_overlay": compose_pad_macro_overlay,
    "inspect_sample_layout": inspect_layout,
    "inspect_layout": inspect_layout,
    "extract_layout_style": extract_layout_style,
    "assemble_teg": assemble_teg,
    "generate_kelvin_m1_teg": generate_kelvin_m1_teg,
    "compare_kelvin_layouts": compare_kelvin_layouts,
    "compare_layouts": compare_layouts,
    "draw_manhattan_layout": draw_manhattan_layout,
    "inspect_dut_corpus": inspect_dut_corpus,
    "inventory_pcellizer_hierarchy": inventory_pcellizer_hierarchy,
    "generate_pcellizer_batch": generate_pcellizer_batch,
}


def _required_variable(name):
    value = globals().get(name)
    if not value:
        raise RuntimeError("Pass -rd %s=<path>" % name)
    return os.path.abspath(str(value))


def dispatch_worker_request(request):
    """Dispatch one validated JSON request without retaining mutable session state."""

    operation = request.get("operation")
    handler = HANDLERS.get(operation)
    if handler is None:
        return worker_error(
            "UNKNOWN_OPERATION",
            "KLayout worker operation is not supported.",
            {"operation": operation},
        )
    return handler(request)


request_file = _required_variable("request_path")
response_file = _required_variable("response_path")
try:
    with open(request_file, "r", encoding="utf-8") as handle:
        worker_request = json.load(handle)
    worker_result = dispatch_worker_request(worker_request)
except Exception as exc:
    worker_result = worker_error(
        "KLAYOUT_WORKER_FAILED",
        "KLayout worker failed.",
        {"error_type": type(exc).__name__, "error": str(exc)},
        "Inspect the worker error and KLayout version compatibility.",
    )

with open(response_file, "w", encoding="utf-8") as handle:
    json.dump(worker_result, handle, indent=2, sort_keys=True)
    handle.write("\n")
