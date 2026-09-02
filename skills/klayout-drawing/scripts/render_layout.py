"""Render a fresh layout to PNG using a hidden KLayout view."""

import os

import pya


def required_script_variable(name):
    value = globals().get(name)
    if not value:
        raise RuntimeError(f"Pass -rd {name}=<value>")
    return str(value)


layout_path = os.path.abspath(required_script_variable("layout_path"))
image_path = os.path.abspath(required_script_variable("image_path"))
image_width = int(globals().get("image_width") or 1200)
image_height = int(globals().get("image_height") or 900)

main_window = pya.Application.instance().main_window()
main_window.load_layout(layout_path, 0)
view = main_window.current_view()
if view is None:
    raise RuntimeError("KLayout did not create a layout view")

view.add_missing_layers()
view.zoom_fit()
view.save_image(image_path, image_width, image_height)
print(f"render=ok output={image_path} size={image_width}x{image_height}")
