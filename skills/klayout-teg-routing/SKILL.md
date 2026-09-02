---
name: klayout-teg-routing
description: Plan, generate, and verify orthogonal low-parasitic Kelvin M1 TEG routing as an optional workflow over the general-purpose KLayout drawing MCP. Use for four-wire resistor structures, Kelvin-specific width/length or Pad-role confirmation, and Kelvin GDS comparison; do not use for unrelated layout drawing.
---

# KLayout TEG Routing

This is a Kelvin application skill over a general-purpose drawing MCP. It does not define or limit the
scope of the MCP server. Keep Kelvin assumptions in this skill or the explicit Kelvin profile; do not
apply them to unrelated drawing requests.

Use the MCP tools as the source of geometry and verification. Do not manually patch a GDS when the
Kelvin profile can represent the requested change. Ask for missing domain meaning conversationally,
but rely on the MCP profile validation to reject an incomplete or unconfirmed contract.

## Workflow

1. Inspect the padset and explicit layermap. Never infer a production layer from display color or file history.
2. Confirm ambiguous electrical meaning before drawing:
   - width is transverse to current flow;
   - length is longitudinal to current flow;
   - measured line orientation;
   - Pad roles and ordering;
   - current-force versus voltage-sense direction;
   - allowed width, spacing, density, and current limits.
3. Call `plan_kelvin_m1_routing`. Stop if it returns a confirmation or split-set error.
4. Keep all provisional files in one project `output/<run-name>/` directory. Supply that directory as `work_directory_path`.
5. Call `generate_kelvin_m1_teg` with a new output path. Require golden equivalence only when the
   requested dimensions and routing contract match the golden. For changed dimensions, compare two
   independent generations made from the same confirmed inputs instead.
6. Call `compare_kelvin_layouts` independently after generation when the result will be promoted or reviewed.
7. Inspect at least the full layout and the minimum/maximum width and length cases in KLayout. Visual
   inspection supplements geometry checks; it does not replace them.
8. Promote only the verified final GDS. Delete disposable GDS, PNG, JSON, and worker files by removing the run directory under `output/`.

## Routing contract

- Draw routing with axis-aligned boxes only. Diagonal, 45-degree, tapered, and arbitrary-angle routes are forbidden.
- Keep the measured M1 line horizontal. Its width is the short transverse dimension and its length is the long current-flow axis, regardless of numeric ordering.
- Append one 300 x 300 nm terminal square outside each measured-line end. The measured length excludes these squares.
- Keep the measured-line interval free of routing above, below, or across it.
- Route current forcing directly left/right from the terminal squares to the adjacent Pads.
- Route voltage sensing straight upward from each terminal square without an initial horizontal jog.
- Outside the measured DUT, retain the persistent 300 nm baseline and expand on one side through 1, 2, 4, then 6 rails. Use orthogonal cross-ties so the route remains a mesh rather than a solid sheet or a long isolated line.
- Preserve the intermediate mesh when joining another structure. Modify only the interface end geometry when possible, align its centerline to the receiving rail, and do not exceed the confirmed 300 nm width at the joint.
- Make every horizontal/vertical joint a full-width 90-degree corner with positive-area overlap. Do not accept edge-only contact, a thin neck, a half-width recess, or an overlaid double-width strip.
- At the SLN001 voltage corner, keep the last vertical cross-tie one 1 um pitch below the horizontal corner rail and extend the horizontal rail to the persistent baseline.
- Prefer clear space at least equal to the wider adjacent metal when no approved foundry rule is available. An approved rule deck overrides this provisional assumption.

## Acceptance gates

Require all of the following from fresh-reloaded layouts:

- expected DBU, top cell, layer set, and 2000 x 54 um bbox;
- only box shapes in generated Kelvin M1 cells;
- six direct Kelvin top instances and the expected reusable common/local cells;
- seven recursive M1 connected components for the current SLN001 six-split profile;
- no cross-group short and isolated spare Pad 25;
- zero recursive geometry XOR area against the golden reference when equivalence is required;
- matching text, bbox, layer set, M1 component count, and M1 hole count.

Treat byte-for-byte GDS equality as unnecessary. Different legal record ordering or duplicate boxes may serialize differently while merged recursive geometry is identical.

## Claims and limits

Report the present structure as `minimize_with_available_constraints_not_rc_proven`. Do not call it electrically optimal or production-ready without approved foundry rules, sheet/contact resistance, density and EM limits, extracted-RC comparison, DRC, and LVS or equivalent evidence.

The MCP server must remain runnable without this skill installed. This skill handles Kelvin-specific
question order and workflow guidance; the general drawing MCP enforces domain-neutral geometry and
file-safety contracts, while the optional Kelvin profile enforces Kelvin-specific confirmations and
acceptance gates.
