# Geometry, grid, hierarchy, and joints

Use this reference for KLayout `pya` geometry decisions.

## DBU and grid

`layout.dbu` is microns per integer database unit. Set it before inserting shapes. Convert a requested
micron value once with `round(value_um / layout.dbu)`, then construct and compare integer `Point`, `Box`,
`Polygon`, `Path`, and `Trans` objects. Record both requested and snapped values.

Do not use a fixed floating epsilon across layouts with different DBUs. Exact integer comparisons are
preferred; otherwise derive the tolerance from the larger participating DBU and the declared manufacturing
grid. Reject a requested positive feature that snaps to zero.

`DBox`, `DPolygon`, `DPath`, `DPoint`, and `DTrans` are convenient at a micron-valued input boundary, but
mixing `D*` and integer objects throughout an algorithm makes off-grid errors hard to see.

## Layers

Treat `(layer, datatype)` as persistent identity and the layout layer index as a runtime handle:

```python
info = pya.LayerInfo(10, 2)
layer_index = layout.layer(info)
```

Never derive production identity from `.lyp` color. Require an explicit layermap or a user-confirmed
mapping extracted from a trusted target reference.

## Hierarchy and transforms

Keep reusable shapes in child cells and instantiate them from parents. Preserve each occurrence's full
identity: instance path, array row/column, and composed transform. Test a known point through the composed
transform whenever mirroring or rotation is involved.

Use arrays only for truly regular repetition. Editing one occurrence in a shared or arrayed cell requires
copy-on-write: clone only the necessary hierarchy branch, retarget that occurrence, and keep every unrelated
occurrence and transform intact.

## Orthogonal geometry

When the contract requires Manhattan routing, use axis-aligned boxes or orthogonal polygons and validate
every edge after snapping. A `Path` can hide corner and end-extension behavior; convert it to explicit
polygonal geometry when exact joint width matters.

Do not infer that all drawing must be orthogonal. Curves, arbitrary polygons, and non-orthogonal transforms
remain valid when explicitly required by the design contract.

## Routed interfaces

Treat a mesh or wide route as a stable middle structure and adapt only its interface when practical. At each
join verify:

- source and receiver centerlines align on grid;
- the overlap has positive area, not only a shared edge;
- the merged joint has no neck below minimum width;
- overlapping rectangles do not create an unintended region above maximum width;
- spacing to neighboring nets remains valid;
- the merged `Region` has the expected connected-component count.

For an orthogonal 90-degree turn, extend one rail through the other rail's centerline far enough to form a
full-width corner. Inspect the merged outline, not the individual rectangles, because legal rectangles can
combine into an illegal or visually weak joint.

## Primitive choice

- `Box`: exact axis-aligned rectangle.
- `Polygon`: explicit filled outline, including orthogonal stepped geometry.
- `Path`: centerline geometry only when its join/end semantics are acceptable.
- `Text`: annotation; its origin is not a reliable rendered text bbox.
- `Edge`: geometric edge, not fabricated filled metal.

Official API references: [database API](https://www.klayout.de/doc-qt5/programming/database_api.html)
and [class index](https://www.klayout.de/doc-qt5/code/module_db.html).
