# Target Process Onboarding

이 문서는 실제 사용환경에서 이 MCP를 처음 운용하는 LLM을 위한 실행 절차다. 이 저장소에는
production/fabrication-approved process profile이 내장되어 있지 않다. Persistent facade의
research-only Kelvin demo profile은 target PDK가 아니다. 예제 GDS, 이름이 비슷한 layer, display color,
다른 node의 rule 또는 LLM의 상식으로 공정값을 채우지 않는다.

> 이 문서는 target-process adapter를 **준비하기 위한 입력 계약**이다. 현재 checkout은 실제 Pad macro를
> 보존해 overlay하고, labeled DUT corpus를 검사·score·등록하며, legacy Phase 1 route를 mesh로 만들 수
> 있다. Stock만으로 transistor pilot까지 완료할 수는 없다. Corpus의 dependent geometry를 만드는 실제
> transistor compiler와 Pad/DUT/route를 묶는 target-process engine이 없기 때문이다. 현재 구현 경계는
> [docs/current-capability-boundaries.md](docs/current-capability-boundaries.md)를 따른다.

## 완료 조건

Onboarding 완료는 다음 artifact가 서로 일치할 때만 선언한다.

1. Exact process/node/option/revision identity.
2. 사용자 또는 승인된 자료가 확인한 DBU, manufacturing grid와 semantic layermap.
3. 현재 요청에 필요한 routing/contact/device geometry rule subset.
4. PDK와 분리된 organization measurement preset.
5. 사용자가 KLayout에서 확인한 node별 reference selection.
6. `validate_process_capability_profile`을 통과한 schema-v1 capability.
7. 실제 Pad를 쓰는 경우 immutable Pad macro artifact와 access-layer edge landing.
8. Transistor를 쓰는 경우 labeled DUT corpus, variation resolution, sealed holdout score와 exact adapter candidate.
9. 타깃 공정 primitive compiler/adapter와 대표 pilot GDS의 fresh-reload 검증.

Schema 통과만으로 production, 측정 가능성 또는 sign-off를 선언하지 않는다.

## LLM 행동 원칙

- 작업 시작 시 저장소의 `skills/klayout-drawing/SKILL.md`를 읽고, Kelvin 구조라면 추가로
  `skills/klayout-teg-routing/SKILL.md`를 읽는다. 로컬 사용자 skill 설치를 전제로 하지 않는다.
- 처음에는 `server_status`, 다음에는 `describe_pdk_profile_inputs`를 호출한다.
- `describe_process_capability`에서 내장 profile을 찾으려 하지 않는다. 내장 profile이 없는 것이 정상이다.
- 사용자가 이미 제공한 답을 다시 묻지 않는다. 빠진 항목을 출처별로 묶어 한 번에 질문한다.
- Geometry가 달라지는 모호성만 확인하고, 답을 기다리는 동안 가능한 read-only inventory는 진행한다.
- Unknown은 `unknown`, 적용하지 않는 값은 `not_applicable`로 남긴다. 숫자를 추정하지 않는다.
- 원본 PDK/GDS/reference는 수정하지 않고 snapshot/hash로 결속한다.
- 검증되지 않은 advisory gate가 drawing을 불필요하게 막지 않게 한다. 다만 모르는 공정값으로
  geometry를 생성하는 것은 중단한다.
- 결정과 근거를 짧은 onboarding summary로 남겨 다음 세션이 같은 질문을 반복하지 않게 한다.

## 1. 입력을 세 종류로 분리한다

### A. Process/PDK profile

공정 revision과 함께 바뀌는 사실이다.

- Process/node/option/revision과 evidence status.
- Layout DBU와 manufacturing grid.
- Semantic role별 `(GDS layer, datatype)` layermap.
- 사용 가능한 routing metal 순서와 각 metal의 최소 width/space.
- Width/parallel-length에 따른 spacing table이 있으면 exact threshold.
- Contact/via 사용 시 cut size/space, array, lower/upper enclosure.
- Transistor 생성 시 well/active/gate/implant/contact role과 W/L 정의·grid·범위.
- Gate/active extension, enclosure, body tie, well continuity와 job에서 사용할 LDE axis의 물리 의미.
- Device별 현재 geometry coverage 상태: `approved_pcell`, `reference_geometry`,
  `rule_synthesized` 중 실제 선택 근거.

Layermap만으로 width/space/contact rule 또는 connectivity를 추론하지 않는다. Techfile importer가
없으면 techfile을 직접 해석했다고 주장하지 않는다.

### B. Organization preset

회사 내에서 공정과 무관하게 고정되는 운영 convention이다.

- Device family/measurement별 terminal 이름과 순서.
- 지원 measurement mode와 naming.
- 기본 transistor context policy.
- 검증 engine availability와 신규 device coverage 확인 방식.

기본 예시는 `examples/settings/organization_measurement_preset.yaml`이지만 reference-only다. 실제
조직 preset은 사용자가 승인한 별도 파일로 관리한다.

### C. Drawing job

매 작업마다 바뀌므로 profile에 넣지 않는다.

- Frame 크기, origin과 allowed boundary.
- Pad macro source/top/access layer/instance transform 또는 승인된 Pad geometry.
- Pad count/rows/outline/pitch/numbering/reserved role.
- DUT 종류·개수·split table과 W/L/DOE/LDE axes.
- Example DUT cell별 parameter row, topology, terminal mapping과 sealed holdout.
- Terminal→net→Pad와 bias/safety contract.
- Routing layer, obstacle, corridor와 optional project max width.
- Reference selection, output 경로와 authorization state.

조직의 일반적인 시작 후보는 약 2000×54 µm, 25개 40×40 µm Pad, direct measurement,
first-metal 우선이다. 이것은 PDK 사실이 아니며 작업별 변경을 허용한다. 16개 Pad 2-row 같은
예외는 명시적으로 받은 경우에만 적용한다.

## 2. 첫 질문은 묶어서 한다

다음 template에서 이미 알려진 줄은 제거하고 한 번에 질문한다.

```text
1) 정확한 process/node/option/revision과 승인 수준은?
2) layermap 파일 또는 필요한 semantic role의 layer/datatype은?
3) DBU와 manufacturing grid는?
4) 이번 device/routing에 필요한 width/space/contact/enclosure rule의 출처는?
5) device geometry는 승인 PCell, 확인된 reference, explicit rule 중 무엇을 사용할까?
6) organization preset 파일을 사용할까, terminal/measurement convention을 지금 확인할까?
7) frame/Pad/DUT split/terminal-Pad/bias/obstacle/output 중 기본 후보와 다른 항목은?
8) 참고할 node별 full reference GDS와 사용자가 확인할 KLayout top/occurrence는?
9) 실제 Pad macro의 top/access layer/instance transform은 무엇인가?
10) Example DUT별 cell/parameter/terminal 표와 holdout DUT는 무엇인가?
```

W/L 또는 width/length가 나오면 다음 의미를 반드시 확인한다.

```text
width = current flow에 수직인 단축, length = current flow 방향의 장축인가?
```

## 3. Layermap과 reference를 조사한다

1. 제공된 GDS/OAS를 `inspect_layout`으로 snapshot/fresh-load한다.
2. Top이 여러 개면 추측하지 말고 사용자가 사용할 top을 확인한다.
3. Layer role은 제공된 layermap으로만 붙인다.
4. `extract_layout_style`로 hierarchy reuse, orthogonality, 치수 빈도와 mesh topology를 관측한다.
5. `register_reference_layout`로 node/option/revision과 full-file hash를 등록한다.
6. `prepare_reference_view` 후 사용자가 KLayout에서 full GDS를 직접 확인한다.
7. `confirm_reference_view` 전에는 reference 선택을 확정하지 않는다.

Reference 관행은 사용자가 승인한 유사 context에서 design-rule precedent보다 우선할 수 있다.
Reference와 같은 영역·motif에서 반복된 위반은 advisory precedent로 기록한다. 새 marker가 더 많다는
이유만으로 자동 중단하지 않되, unmatched context는 `REVIEW_NEEDED`로 남긴다.

## 4. Capability object를 만든다

Placeholder를 실제 승인값으로 바꾸고 `validate_process_capability_profile`에 전달한다.

```json
{
  "schema_version": 1,
  "process": {
    "name": "<exact_process_name>",
    "version": "<exact_revision>",
    "evidence_status": "approved"
  },
  "dbu_um": "<positive_number>",
  "manufacturing_grid_um": "<integer_multiple_of_dbu>",
  "layers": {
    "<semantic_role>": ["<gds_layer_integer>", "<datatype_integer>"]
  },
  "routing_metals": [
    {
      "name": "<canonical_metal_name>",
      "layer_role": "<mapped_role>",
      "min_width_um": "<positive_number>",
      "min_space_um": "<positive_number>",
      "width_dependent_spacing": false,
      "spacing_table": []
    }
  ],
  "devices": {
    "<device_name>": {
      "family": "transistor",
      "terminals": ["G", "D", "S", "B"],
      "measurements": ["dc_4t"],
      "doe_axes": ["w_um", "l_um"],
      "required_layers": ["<mapped_roles_used_by_adapter>"],
      "geometry_source": "<approved_pcell|reference_geometry|rule_synthesized>"
    }
  },
  "verification": {
    "drc": "<approved|public|projection_only|not_available>",
    "lvs": "<approved|public|projection_only|not_available>",
    "pex": "<approved|public|projection_only|not_available>"
  }
}
```

JSON 예시의 terminal 이름은 organization preset과 맞춰야 한다. Verification 상태는 선택적
evidence 설명이며 세 항목이 모두 `approved`여도 capability 자체는 `production_ready=false`다.
`geometry_source` enum을 채우는 것 역시 callable adapter가 설치·등록·검증됐다는 증거가 아니다.
Adapter identity/version/hash와 실제 materialization evidence가 별도로 필요하다.

Validation error는 원인을 고친 뒤 같은 semantic profile을 새 revision/hash로 다시 검증한다.
Layer collision, off-grid manufacturing grid, 누락 device role 또는 빈 spacing table을 임의로 완화하지 않는다.

## 5. Device adapter readiness를 판단한다

Resistor와 capacitor의 explicit metal geometry planner는 capability가 요구한 rule로 제한할 수 있다.
Transistor는 generic core가 layer 이름만 보고 생성하지 않는다. 다음 중 하나가 필요하다.

- Approved PCell을 호출하고 parameter/pin/layer mapping을 검증하는 adapter.
- 사용자가 확인한 reference hierarchy를 보존해 parameterize하는 adapter.
- 승인된 explicit rule table로 geometry를 합성하고 representative reference와 비교한 adapter.

Adapter acceptance:

- Exact process/version/hash mismatch를 거부한다.
- W/L 의미, supported range와 grid를 검사한다.
- Width가 증가하면 rule이 허용하는 Source/Drain contact 수가 함께 증가한다.
- Required layer와 terminal M1 landing을 positive area로 확인한다.
- Repeated generation의 semantic geometry가 결정론적이다.
- Fresh reload에서 top/DBU/layer/hierarchy/terminal geometry를 재확인한다.

Adapter가 없으면 LLM은 “transistor primitive adapter 미구현”을 보고하고 resistor/capacitor 또는
read-only reference 분석처럼 가능한 작업만 계속한다.

### Example DUT corpus로 candidate를 준비하는 현재 경로

여러 DUT가 든 source GDS와 DUT별 parameter 정보를 다음 순서로 등록할 수 있다.

```text
onboard_transistor_corpus
→ resolve_transistor_corpus
→ 외부 compiler가 reproduced GDS 생성
→ score_transistor_adapter
→ build_transistor_adapter_candidate
→ register_transistor_adapter_candidate
```

`parameter_schema`에는 Gate length, CPP, planar width, nFin, cell height와 필요한 추가 축을 이름/단위/
numeric kind로 등록한다. 각 `dut_record`는 exact cell name, 모든 parameter 값, topology와 terminal
landing/layer mapping을 가져야 한다. 최소 한 DUT는 fitting 전에 sealed holdout으로 분리한다.

Corpus onboarding은 observed invariant style과 same-parameter/different-geometry variation을 찾는다.
설명되지 않은 차이는 사용자가 따를 reference DUT를 선택하기 전까지 clarification 상태로 남긴다.
Reproduced GDS score가 통과해도 candidate 상태는 `candidate_scored_not_foundry_qualified`다.

현재 경로는 CPP가 바뀔 때 Gate/Active/Contact/implant/terminal을 함께 움직이는 dependency recipe나
callable PCell을 자동 생성하지 않는다. 실제 compiler identity/code hash는 candidate에 결속되지만,
compiler 구현과 foundry 검증은 외부에서 제공해야 한다.

### 실제 Pad macro를 준비하는 현재 경로

```text
register_pad_macro
→ source stream/top cell/DBU/access layer/instance transform 고정
→ eligible edge landing 확인
→ compose_registered_pad_macro
```

Composer는 source Pad subtree를 수정하지 않고 새 top에 instance로 넣는다. DUT와 routing은 별도
operation으로만 추가한다. 이 overlay는 legacy Phase 1 synthetic Pad composer와 아직 연결돼 있지 않다.

## 6. Target TEG drawing contract를 확인한다

이 절은 adapter와 composer가 충족해야 할 acceptance 조건이다. Stock Phase 1은 synthetic Pad를 쓰지만
route polyline을 multi-rail mesh로 compile한다. Immutable Pad overlay 경로는 별도로 구현돼 있으며,
현재 target-process engine은 actual Pad, corpus-derived DUT와 mesh route를 한 결과로 묶지 않는다.

- Routing은 horizontal/vertical Manhattan만 허용하며 diagonal은 사용하지 않는다.
- 측정 metal 이외의 장거리 routing은 넓은 parallel rail과 repeated cross-tie mesh를 사용한다.
- DUT 인접 bounded transition 외의 긴 single line을 피한다.
- Pad landing은 여러 mesh rail이 positive area로 연결되게 한다.
- DUT terminal에서 한쪽 방향의 staged expansion으로 mesh를 넓힌다.
- Vertical/horizontal joint는 full-width 90° turn처럼 align하고 얇은 neck·돌출을 만들지 않는다.
- 중간 mesh topology는 유지하고 Pad/DUT/다른 mesh와 만나는 interface만 조정한다.
- 실제 spacing rule이 없을 때는 adjacent metal width 이상의 spacing을 보수적 후보로 제안하되,
  production rule이라고 주장하지 않고 사용자 확인을 받는다.
- First metal이 불가능하면 narrow/single-rail로 몰래 단순화하지 않고 multi-metal 승인을 요청한다.

Transistor context의 organization default:

- DUT window를 unrouted `same_as_measured` array로 채운다.
- Compatible neighbor는 diffusion을 공유하는 방향을 기본으로 한다.
- Array edge 5 µm 이내를 피한 balanced center region에서 1개를 기본 측정한다.
- 여러 개 측정 시 요청 개수만 routing한다.
- `standard_cell_like`는 n/p/p/n 반복과 `standard_cell_height_um`이 필수다.

이 기본은 실제 LDE 안전성을 증명하지 않는다. 공정별 STI/WPE/LOD/dummy/guard-ring 관행은
reference와 사용자 승인으로 보완한다.

## 7. Representative pilot을 수행한다

이 단계는 stock에서 보장하는 turnkey 절차가 아니라 외부 process adapter와 통합 engine을 검증하는
acceptance gate다. Actual transistor compiler, registered Pad macro와 실제 DUT/Pad port를 쓰는 mesh
routing이 하나의 flow로 준비되지 않았다면 `not_ready`로 끝내고 conceptual scaffold로 대체하지 않는다.

전체 split 전에 가장 작은 pilot으로 다음을 확인한다.

1. 한 representative DUT와 실제 context array.
2. 실제 Pad macro 또는 승인된 Pad geometry.
3. First-metal mesh와 terminal/contact scaling.
4. `draw/plan → write new output → fresh inspect`.
5. Reference가 있으면 hierarchy-aware semantic comparison과 style difference report.
6. 사용자에게 KLayout에서 pilot GDS 확인 요청.

Pilot 승인 후 split table을 확장한다. 같은 입력의 재생성은 raw bytes가 아니라 semantic
geometry/fingerprint로 결정론성을 검사한다.

## 8. Onboarding summary 형식

LLM은 마지막에 다음 표를 채운다.

| 항목 | 상태 | 근거/경로 | 다음 조치 |
|---|---|---|---|
| Process identity | confirmed / missing | revision source |  |
| DBU/grid | confirmed / missing |  |  |
| Layermap | validated / missing | file + hash |  |
| Required rules | complete / partial | rule source |  |
| Organization preset | confirmed / missing | file + hash |  |
| Reference | user-confirmed / candidate / none | selection id |  |
| Pad macro | registered / missing / not applicable | package hash + edge landing |  |
| DUT corpus | resolved / clarification required / missing | corpus/resolution hash |  |
| Adapter score | passed / failed / not run | scorecard + holdout hash |  |
| Device adapters | ready / partial / absent | adapter id |  |
| Pilot | fresh-reload verified / not run / failed | GDS path + hash |  |
| Production evidence | outside scope / attached | policy receipt |  |

`ready_for_target_drawing`은 process capability, 필요한 adapter와 pilot이 모두 준비된 경우에만 true다.
`production_ready`는 이 onboarding에서 true로 만들지 않는다.
