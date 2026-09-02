# Python PCells and hierarchy-aware parameterization

Read this before implementing a PCell or turning an existing hierarchical GDS occurrence into a parameterized
family.

## PCell lifecycle

Subclass `pya.PCellDeclarationHelper` and keep only lifecycle orchestration in the class:

1. Declare explicit, unit-bearing parameters in `__init__`.
2. Snap and bound dependent values in `coerce_parameters_impl`.
3. Return concise text from `display_text_impl`.
4. Call deterministic ordinary geometry functions from `produce_impl`.

Do not depend on `MainWindow`, `LayoutView`, selection, or mouse state in `produce_impl`. Avoid a changing set
of helper cells inside production. Register reusable declarations in a stable `pya.Library`.

## Parameters from an existing GDS

Do not assume that a visually repeated DUT is one flat cell. Before parameterization, capture:

- source file hash, DBU, top cell, and selected occurrence path;
- array indices and every translation, rotation, and mirror in the composed transform;
- selected layer and the two user-marked anchor edges or rulers;
- fixed anchors, moving boundary, dependent layers, and whether repetition pitch changes;
- expected terminals or connectivity evidence without inferring nets from names.

The parameter should change a stable geometric relation such as edge-to-edge distance. Define which edge stays
fixed, how the opposite edge moves, and how dependent shapes follow. Present MCP-suggested candidates as
proposals and require user confirmation before committing the recipe.

## Hierarchy-preserving rewrite

Flattening is not an implementation shortcut. Use copy-on-write:

1. Resolve the exact occurrence.
2. Clone the minimal shared branch that must diverge.
3. Apply the parameterized geometry in local coordinates.
4. Retarget only the selected occurrence.
5. Recheck every parent transform and unaffected sibling fingerprint.

For arrays, either preserve the array when all members share the same split or expand only the member that must
diverge. Record that choice in the recipe.

## Batch splits

Normalize pasted CSV/Excel rows into typed values before generation. Require a stable split identifier, reject
duplicate output/cell names, snap every value to grid, and sort deterministically. Generate each split from the
same immutable source and confirmed recipe; do not use a previous split as the next split's input.

Verify one minimum, one maximum, and at least one interior split semantically. For dozens of splits, also report
per-split source hash, recipe hash, snapped values, output hash, bbox, and verification status.

## Portable output

Keep the editable, hierarchy-preserving source and PCell outputs intact. If a downstream consumer explicitly
requires static interchange geometry, create a separate export and verify it without the library loaded. Never
flatten the only editable artifact.

Official references: [PCell helper](https://klayout.de/doc-qt5/code/class_PCellDeclarationHelper.html)
and [PCell concepts](https://klayout.de/doc-qt5/about/about_pcells.html).
