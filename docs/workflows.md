# Workflows

이 문서는 목표별 실행 순서와 profile 사용법을 설명한다. 공통 안전 계약과 production
조건은 [contracts-and-production.md](contracts-and-production.md), 내부 구조와 테스트는
[development.md](development.md)를 참고한다.

## 목표별 경로

| 목표 | 시작점 | stock checkout | 결과 |
|---|---|---|---|
| 기존 GDS/OAS 확인 | `inspect_layout`, `compare_layouts` | 지원 | 입력 불변 inventory/XOR |
| 기존 GDS/OAS style 관측 | `extract_layout_style` | 지원 | content-addressed JSON profile |
| 명시적 직교 도형 생성 | `draw_manhattan_layout` | 지원 | 새 nonproduction GDS/OAS |
| Kelvin reference 재현 | Kelvin 전용 plan/generate/compare | 지원 | 6-split nonproduction GDS |
| 선택한 box 한 개를 PCell화하고 split batch 생성 | PCellizer workflow | 제한 지원 | one-parameter, hierarchy-preserving batch |
| Node별 reference 관리 | Reference Library workflow | 지원 | immutable reference selection |
| Persistent job | `teg_intake` | 지원 | content-addressed draft/job |
| Persistent plan/generate/verify | 4-call facade | host 통합 필요 | resumable evidence chain |
| Foundry sign-off/PCM release | 조직 workflow | 미지원 | 외부 PDK/deck/probe 계약 필요 |

## Tool surface mode

`KLAYOUT_MCP_TOOL_MODE`로 LLM에 노출되는 도구 수를 줄일 수 있다.

| Mode | 공개 도구 | 용도 |
|---|---:|---|
| `expert` | 56 | 전체 기능. 기본값이지만 복합 작업에만 권장 |
| `facade` | 6 | `server_status`와 persistent 4-call/status |
| `drawing` | 7 | 범용 draw/inspect/style/compare와 mesh/contact planner |

잘못된 mode는 `expert`로 fallback하지 않고 시작 시 실패한다.
`server_status.tool_surface.active_tools`, `capabilities`, `recommended_entrypoints`와
`persistent_facade.tools`는 선택한 mode에 맞게 filter된다. 실제 호출 가능 목록은 MCP
`tools/list`와 동일해야 하며 regression test가 이를 확인한다.
작은 모델은 persistent E2E에 `facade`, 범용 geometry에 `drawing`을 먼저 사용한다. 여러 profile,
PCellizer와 reference library를 한 세션에서 함께 골라야 할 때만 `expert`가 적합하다.

## Generic Manhattan drawing

명시적 DBU, layer map, cell과 operation을 한 번에 전달한다. Output은 반드시 새 경로다.

```json
{
  "output_layout_path": "output/example/one-unit.gds",
  "dbu_um": 0.001,
  "top_cell": "TOP",
  "cells": ["TOP", "UNIT"],
  "layers": [
    {"name": "m1", "layer": 15, "datatype": 0},
    {"name": "text", "layer": 100, "datatype": 0}
  ],
  "operations": [
    {"type": "add_box", "cell": "UNIT", "layer": "m1", "bbox_um": [0, 0, 1, 0.3]},
    {"type": "add_instance", "parent_cell": "TOP", "child_cell": "UNIT", "origin_um": [10, 5], "rotation_deg": 0, "mirror_x": false},
    {"type": "add_text", "cell": "TOP", "layer": "text", "text": "EXAMPLE", "origin_um": [0, 0]}
  ],
  "confirm_nonproduction": true
}
```

성공 기준은 `fresh_reload_verified=true`, single top, 요청한 DBU/layer/cell/shape가
fresh layout에서 다시 확인되고 `production_ready=false`인 것이다.

## Persistent 4-call workflow

Host가 trusted approval verifier와 해당 process engine을 주입한 경우의 순서는 다음과 같다.

```text
teg_intake → teg_plan → teg_generate → teg_verify
```

Stock server는 `approval_verifier=None`, `production_mode=True`이므로 `teg_intake` 이후
`APPROVAL_BACKEND_UNAVAILABLE`로 중단되는 것이 정상이다. LLM이 approval reference를
직접 만들면 안 된다.

| 단계 | 입력 | 보존할 반환값 | 중단 조건 |
|---|---|---|---|
| Template intake | exact profile/version/family | template, required questions | 미확정 질문 존재 |
| Persist intake | 완전한 DesignIntentDraft | job/design/process hash | unresolved question 존재 |
| Plan | job id와 host-issued approval | plan/manifest hash | approval/process binding 실패 |
| Generate | 같은 approval과 새 filename | layout path/hash | output 존재 또는 stale binding |
| Verify | layout-bound MeasurementManifest | evidence state/hash | layout/measurement mismatch |

Template mode 예:

```json
{
  "template_process_profile": "sln001_kelvin_reference_demo",
  "template_process_version": "golden-v15-2026-08-25",
  "template_family": "resistor"
}
```

실제 KLayout과 test-only host integration을 사용하는 nonproduction 예:

```powershell
uv run python examples/run_persistent_kelvin_demo.py --run-root output/persistent-kelvin-demo-01
```

이 예제는 project regression reference XOR, fresh reload, connectivity projection, 실제 file hash와
MeasurementManifest binding을 확인한다. 마지막 상태는 `measurement_package_complete`지만
tester program이나 sign-off를 의미하지 않는다. 예제 verifier는 production mode에서 거부된다.

`teg_status`는 manifest ancestry, host output root 안의 stream file과 모든 `workflow://` document를
각 content-addressed namespace에서 다시 로드·해시한다. 누락·kind/hash mismatch·root escape·변조는
성공 상태를 반환하지 않는다. Measurement requirement는 actual source/program/compliance,
timing/environment/safety와 exact multiplicity까지 승인 intent와 대조한다.

Generation engine은 unique staging stream을 만들고 검증 결과와 hash를 `generation_staged`에 먼저
저장한다. Final은 target directory의 sibling temp를 거쳐 atomic replace된다. Staging 직후,
final 기록 직후 또는 `drawing_complete` 이후 중단되면 동일 approval과 exact filename으로
`teg_generate`를 재호출한다. 저장된 layout/result hash를 확인해 generation engine을 재실행하지
않고 다음 stage를 append한다.

남은 E2E 공백은 실제 stdio `teg_*`와 host-injected component를 함께 사용한 process restart다.

## Kelvin M1 reference profile

Reference GDS:

```text
artifacts/SLN001_kelvin_m1/SLN001_kelvin_m1_aligned_force_pad_joint_v15.gds
```

Profile 계약:

- Exact id/version: `sln001_kelvin_reference_demo` / `golden-v15-2026-08-25`.
- Frame 2000×54 µm, DBU 0.00025 µm, 25개 40×40 µm Pad.
- M1 `(15,0)`, orthogonal box geometry만 사용.
- Width/length split: 22/100/300 nm × 300/1000 nm의 6개 조합.
- 측정 metal은 horizontal이며 양 끝 300×300 nm landing에 force/sense access를 분리한다.
- 측정 line 외 routing은 wide cross-tied mesh, repeated ties, multiple Pad landing을 사용한다.
- One-sided four-stage expansion, aligned full-width 90° joint, width 이상의 clear space를 적용한다.
- 최종 reference와 generated layout은 fresh reload 후 recursive layer XOR 0을 확인한다.

`plan_kelvin_m1_routing` 없이 좌표를 재구성하거나 profile-local 수치를 범용 규칙으로
사용하면 안 된다.

## Direct-measurement Phase 1

사용자가 process/Pad/DUT/terminal/bias/obstacle을 완전히 제공하지 않았다면 먼저
`plan_direct_measurement_teg`로 질문을 닫는다. 이후 순서는 다음과 같다.

```text
process capability
→ intake/terminal mapping
→ optional DOE
→ device primitive
→ terminal route feasibility
→ phase1 layout composition
→ atomic generation/fresh reload
```

First-metal routing이 불가능하면 single rail로 낮추지 않고 explicit multi-metal escalation을
요청한다. 현재 여러 net의 mesh envelope를 동시에 최적화하는 전역 router는 없다.

## PCellizer

목표는 복잡한 hierarchy를 flatten하지 않고 선택한 DUT occurrence를 parameterize해 CSV/Excel
split table로 1개 또는 수십 개 GDS를 만드는 것이다.

```text
inventory_pcellizer_hierarchy
→ KLayout dock에서 shape/ruler capture
→ create/inspect snapshot
→ plan process inputs
→ define parameters
→ compile recipe
→ plan split table
→ generate/inspect batch
```

Snapshot은 source bytes, exact occurrence path, array member, transform와 neighborhood fingerprint를
hash로 묶는다. Batch는 hierarchy copy-on-write, duplicate variant reuse와 fresh reload를 사용한다.
현재 recipe compiler는 직접 선택된 box 한 개와 parameter 한 개만 지원한다. W/L 의미를 자동
추론하거나 두 parameter의 Cartesian product를 한 recipe로 컴파일하지 않는다. 일반화된 임의
polygon PCell과 composite-DUT 자동 추론도 지원하지 않는다.

## Reference Library

사용자가 제공한 LN14LPU/LN08LPU 등의 full GDS/OAS를 Node/option/revision별로 보관한다.
LLM은 후보를 추천할 수 있지만 reference를 확인할 수 없다.

```text
register_reference_layout
→ list_reference_layouts
→ prepare_reference_view
→ 사용자가 KLayout에서 full GDS 확인
→ confirm_reference_view
→ consult_reference_selection
```

Reference precedent는 동일 process/concern/layer/violation/context에서 의도적인 DRC 위반을
설명할 수 있다. Marker count가 reference보다 많다는 사실만으로 거부하지 않지만, unmatched
marker는 비차단 `REVIEW_NEEDED`로 남긴다. `REF_ACCEPTED`는 DRC-clean이나 production sign-off가 아니다.

### GDS 기반 style 추출

`extract_layout_style`은 immutable snapshot을 KLayout에서 fresh-load하여 다음 관측값을 만든다.

- Top hierarchy reuse, instance rotation/mirror와 flatten 여부.
- Layer별 recursive shape 종류, box horizontal/vertical/square 빈도.
- 관측된 box width/height/short/long side의 상위 빈도.
- Manhattan 직교성, merged component/hole/area/bbox fill ratio.
- Text label 수와 sample string.
- `prepare_reference_view.style_descriptors`에 전달할 수 있는 descriptor 목록.

Layer role은 supplied layermap에서만 붙이고 geometry나 display color로 추측하지 않는다. 관측된
치수는 reference의 drawing 관행이지 design-rule minimum/maximum이 아니다. Net, terminal,
electrical performance와 DRC waiver도 추론하지 않는다. JSON profile은 source GDS SHA-256과 profile
SHA-256을 포함하며 기존 파일을 덮어쓰지 않는다.

Portable example은
`examples/style-profiles/sln001_kelvin_style.json`에 있고 source GDS와 layermap은 각각
`examples/gds/kelvin_m1_w24_48_100nm_l2_3um.gds`,
`examples/settings/sln001_kelvin_reference_layermap.yaml`이다. Runtime absolute path는 profile hash에
들어가지 않으므로 Windows/Linux에서 같은 GDS·layermap·KLayout 관측값이면 같은 profile hash를 낸다.

## 대표 오류 복구

| Code/상태 | 조치 |
|---|---|
| `KLAYOUT_NOT_FOUND` | `KLAYOUT_EXE`를 정확한 executable로 설정 후 재시작 |
| `KLAYOUT_TIMEOUT` | 입력과 timeout을 확인하고 같은 호출을 새 output으로 재시도 |
| `OUTPUT_ALREADY_EXISTS` | 덮어쓰지 말고 새 이름 사용 |
| `TOP_CELL_AMBIGUOUS` | 사용자가 top cell을 명시 |
| Off-grid/DBU | 승인된 grid에 좌표를 정확히 snap |
| Pad/route short | mapping/corridor를 수정; single-rail fallback 금지 |
| `APPROVAL_BACKEND_UNAVAILABLE` | Stock의 정상 fail-closed; approval을 지어내지 않음 |
| `SHARED_PAD_INACTIVE_TERMINAL_POLICY_REQUIRED` | Active DUT와 모든 inactive terminal state 명시 |
| `INACTIVE_SHARED_PAD_STATE_CONFLICT` | 같은 active Pad는 `follow_shared_pad` 사용 |
| `ACTIVE_SHARED_PAD_STIMULUS_CONFLICT` | 같은 Pad의 stimulus를 일치시키거나 serial로 분리 |
| External adapter unavailable | Report를 직접 신뢰하지 않고 host registry 확인 |
| Stale hash/manifest | 기존 artifact를 수정하지 말고 새 snapshot/job 생성 |
