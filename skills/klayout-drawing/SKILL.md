---
name: klayout-drawing
description: Create, modify, parameterize, and semantically verify KLayout GDS/OAS geometry and Python PCells. Use for pya drawing, hierarchy, transforms, DBU/grid handling, layout rendering, or fresh-reload geometry checks; use the Kelvin-specific project skill for Kelvin electrical routing decisions.
---

# KLayout Drawing

Build deterministic KLayout geometry without inventing process facts, then prove the written stream
file preserves the requested geometry and hierarchy.

This repository's MCP is the preferred execution surface when it exposes the required operation. The
skill supplies question order and engineering invariants; it is not a hidden runtime dependency and the
MCP must continue to work when the skill is not installed.

## Intake

Resolve these items from the request, project contracts, or inspected files before drawing:

- input and output path, format, top cell, and source-preservation requirement;
- DBU, manufacturing grid, and explicit `(layer, datatype)` mapping;
- hierarchy policy, including which occurrence is edited and whether arrays, mirroring, or rotations exist;
- dimensions and their physical directions when `width` and `length` could be ambiguous;
- applicable width, spacing, enclosure, contact, routing, and reference-precedent constraints;
- acceptance evidence: dimensions, hierarchy, connectivity, XOR, rendering, or an approved external check.

Ask only when a missing choice materially changes geometry. Never infer a fabrication layer from color,
a process rule from a public example, or electrical intent from cell or terminal names alone.

## Workflow

1. Inspect the source and declared process inputs. Preserve the source file and its hierarchy.
2. Quantize user dimensions once to integer DBU coordinates and retain the snapped values in the report.
3. Generate reusable geometry in the lowest appropriate cell. Apply placement through instance transforms.
4. For hierarchy-aware parameterization, identify an occurrence by instance path, array index, and composed
   transform; use copy-on-write so unrelated occurrences remain unchanged.
5. Write a new output file, open it in a fresh `pya.Layout`, and run semantic assertions on the reloaded file.
6. Render the reloaded output when appearance, placement, joints, or transforms matter.
7. Report paths, KLayout version, DBU/grid, top cell, layers, hierarchy changes, snapped parameters, and checks.

## Conditional references

- Read [references/geometry.md](references/geometry.md) for DBU arithmetic, layers, hierarchy, transforms,
  orthogonal geometry, or interface joints.
- Read [references/pcells.md](references/pcells.md) before creating or modifying a PCell, parameterizing a
  hierarchical GDS, or generating a split table.
- Read [references/verification.md](references/verification.md) when defining acceptance checks, comparing
  layouts, or diagnosing a visual/semantic mismatch.
- Read [references/project-integration.md](references/project-integration.md) when using this repository's MCP,
  process onboarding, reference library, or project skills.
- For Kelvin four-wire routing, also read [../klayout-teg-routing/SKILL.md](../klayout-teg-routing/SKILL.md).

## Drawing invariants

- Set `layout.dbu` before inserting geometry. Use exact integer DBU arithmetic after input quantization.
- Treat layer number and datatype as one identity. Display names and colors are not a layermap.
- Preserve hierarchy by default. Never flatten a source or PCellizer deliverable to simplify implementation.
- Keep `produce_impl` deterministic and independent of KLayout windows, selection, timestamps, or mutable state.
- Use orthogonal boxes/polygons when the confirmed contract requires Manhattan routing. Do not universalize
  Manhattan-only behavior to geometry that explicitly permits curves or arbitrary polygons.
- At routed interfaces, verify positive-area overlap, centerline alignment, local min/max width, spacing, and
  merged connectivity. Edge-only contact and accidental doubled-width overlaps are failures.
- A reference layout is evidence of local precedent, not an inferred rule deck. Record confirmed precedent and
  compare any new violations by location and context instead of promising zero additional errors.

## Validation gate

Completion requires all applicable checks on a fresh reload:

- expected DBU, top cell, layers, hierarchy, instance transforms, and critical bboxes;
- exact integer dimensions or an explicitly reported grid-derived tolerance;
- recursive geometry and connectivity, not only direct shapes in the top cell;
- deterministic regeneration and semantic XOR when equivalence is claimed;
- visual inspection when shape balance, joint quality, orientation, or placement is part of the request.

Do not treat successful serialization, a PNG, internal connectivity, or zero XOR as DRC/LVS/PEX or
production sign-off.

## Bundled helpers

Use `scripts/run_klayout.py` to invoke the installed KLayout runtime on Windows or Linux. Run
`scripts/inspect_layout.py` for a JSON inventory and `scripts/render_layout.py` for a preview. The
`assets/python_pcell_library.py` file is a minimal reusable PCell starting point; adapt its geometry and
parameters rather than embedding project-specific process assumptions.
