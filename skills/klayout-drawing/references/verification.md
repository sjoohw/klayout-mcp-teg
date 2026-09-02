# Fresh-reload semantic verification

Verification must examine the file that another tool will open, not only the in-memory layout that generated it.

## Minimum round trip

1. Write the output to a new path.
2. Create a fresh `pya.Layout` and read the file.
3. Verify DBU, top cells, `(layer, datatype)` pairs, hierarchy, arrays, and transforms.
4. Recompute critical dimensions, recursive geometry, and connectivity.
5. Render the reloaded layout if appearance or placement matters.

## Recursive geometry

Direct top-cell shape counts are insufficient when DUTs or routes live in children. For each relevant top/layer,
build a `pya.Region(top.begin_shapes_rec(layer_index))`, merge it, and inspect bbox, area, polygon count, holes,
and connected components. Preserve hierarchy in the file even when verification uses a flattened recursive
view.

## Dimensions and grid

Compare integer DBU coordinates exactly where possible. For cross-layout micron comparisons, derive tolerance
from the participating DBUs and declared grid; report the tolerance. Check both nominal dimensions and snapped
values, especially min/max widths, small contacts, spaces, and narrow joints.

## Equivalence and determinism

Use recursive per-layer `Region` XOR when claiming geometric equivalence. Zero XOR means merged geometry is
equivalent on the compared layers; it does not prove equal hierarchy, text, properties, nets, process legality,
or electrical behavior. Check those separately.

Generate twice from identical normalized inputs and compare semantic inventories or region fingerprints. Avoid
raw GDS-byte equality because record ordering and metadata may differ.

## Connectivity and joint checks

For routed conductors, verify expected merged connected-component counts and absence of cross-net merging. At
each interface inspect local geometry for positive-area overlap, centerline alignment, min-width necks,
max-width bulges, spacing, dangling rails, and isolated fragments.

Connectivity inferred from touching geometry is an internal drawing check, not LVS. It cannot establish terminal
meaning or device connectivity without an explicit contract.

## Reference comparison

Compare a candidate against the user-confirmed reference at both whole-layout and analogous local-region levels.
Record inherited reference violations separately from newly introduced differences. Similar local precedent can
justify an intentional exception only when the user or organization policy confirms it; do not silently convert
reference behavior into a foundry rule.

## Visual evidence

Inspect rendered previews for clipping, transforms, array placement, thin joints, doubled overlaps, asymmetry,
text offsets, and layer visibility. A PNG is supplementary and never replaces coordinate, hierarchy, or region
assertions.
