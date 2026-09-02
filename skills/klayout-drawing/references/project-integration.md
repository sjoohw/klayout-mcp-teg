# Integration with KLayout Drawing MCP

Use this reference inside the `klayout-auto` repository.

## Responsibility boundary

- The MCP performs deterministic inspection, drawing, persistence, and semantic verification.
- This skill helps an LLM collect inputs and select a workflow; the server never reads the skill at runtime.
- `skills/klayout-teg-routing` adds Kelvin-specific electrical/routing guidance. Do not apply its fixed geometry
  to unrelated drawing.
- Process facts come from target onboarding, not bundled public-PDK assumptions.

## Preferred execution

Use MCP operations rather than hand-editing a GDS when the operation is represented by a server contract. Typical
flows start with inspection/style extraction, explicit process and reference selection, planning, generation to a
new output path, fresh-reload verification, and comparison. Use direct `pya` scripts only for capabilities the
MCP does not expose or while developing a new server primitive, then add proportional tests.

Do not bypass a missing required input by encoding a hidden constant in a script. Update the explicit process,
design, or measurement contract instead.

## Process onboarding

Follow the repository `onboarding.md`. At minimum, obtain DBU/grid, layermap, relevant min/max geometry rules,
contact/enclosure rules, device geometry source or adapter, pad/scribe constraints, and user-confirmed reference
GDS. Optional external DRC/LVS/PEX availability is evidence configuration, not a prerequisite for ordinary
drawing.

## Reference behavior

Store references content-addressed by process/node and require the user to confirm which reference applies to the
new job. Style extraction may observe hierarchy, layers, orthogonality, widths, pitches, and local joint patterns;
it must not infer net semantics, rule legality, or electrical optimality.

## Claims

Internal fresh reload, XOR, region connectivity, and visual inspection support deterministic drawing claims.
Production-ready, rule-clean, LVS-clean, or electrically optimal claims require the corresponding trusted target
evidence and organizational approval.
