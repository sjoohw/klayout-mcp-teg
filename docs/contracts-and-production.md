# Contracts and production boundaries

이 문서는 process/drawing/measurement의 **목표 계약**과 production 승격 조건을 설명한다.
현재 stock 구현이 어느 항목을 실제로 enforce하는지는
[current-capability-boundaries.md](current-capability-boundaries.md)를 함께 확인한다.

## 공통 안전 원칙

- 원본 GDS/OAS와 reference는 변경하지 않는다.
- Output은 새 경로를 사용한다. 기존 target은 거부한다. 지원 same-host local filesystem의 같은
  경로에 여러 writer가 접근하면 create-only publish로 첫 결과를 보존하고 나머지는 거부한다.
- DBU와 `(layer, datatype)`을 명시하고 display color로 layer를 추측하지 않는다.
- User-facing 치수는 µm, exact geometry 계산은 integer DBU를 사용한다.
- Routing은 horizontal/vertical Manhattan geometry만 허용한다.
- Width는 current flow의 단축, length는 장축이라는 의미를 drawing 전에 확인한다.
- File write 성공이 아니라 fresh KLayout reload와 semantic geometry 검증을 완료 조건으로 삼는다.
- Internal geometry/connectivity 검사는 DRC/LVS/PEX나 silicon 상관성을 대신하지 않는다.
- 검증되지 않은 similarity/gatekeeper는 drawing을 막지 않고 advisory로만 남긴다.

## 입력의 소유권

### Process/PDK profile

PDK 또는 승인된 adapter에서 받아야 한다.

- Exact process name/version과 capability hash.
- DBU/manufacturing grid.
- Semantic layermap `(layer, datatype)`.
- Routing metal width/space/max-width/spacing table.
- Contact/via cut, spacing, enclosure와 terminal 규칙.
- Device family, terminal, measurement와 지원 DOE/LDE 축.
- DRC/LVS/PEX를 사용할 경우 해당 evidence 상태. 세 항목 자체는 범용 drawing의 필수 입력이 아니다.

Repository는 production/fabrication-approved process profile을 번들하지 않는다. Persistent facade의
research-only Kelvin demo profile은 target PDK가 아니다. Geometry source가 PDK에 있을 것으로
추정하지 않으며 [onboarding](../onboarding.md)에서 실제 근거를 선택한다.

### Organization preset

회사 내에서 고정되는 terminal naming/order, 지원 측정 방식, verification environment와
transistor context 기본값을 저장한다. DRC/LVS/PEX 선택은 evidence requirement일 뿐 executable,
license, deck/runset 또는 adapter readiness가 아니다. Host preflight가 확인하기 전에는 unavailable로
취급한다. 선택하지 않은 verification은 generic drawing을 막지 않지만 실제 production 실행 가능성은
별도 조직 workflow가 판단한다.

### Drawing job

매번 바뀌는 frame, Pad topology, DUT count/split, W/L/DOE/LDE, terminal→net→Pad, bias,
obstacle/corridor, routing layer escalation과 output을 사용자에게 받는다.

## Profile/frame 구분

| Workflow/profile | Frame | DBU source | Pad topology | Production eligibility |
|---|---|---|---|---|
| Generic drawing | 사용자 입력 | 사용자/PDK | 사용자 입력 | 항상 nonproduction |
| SLN001 Kelvin demo | 2000×54 µm | profile 0.25 nm | 25×40 µm, 1 row | nonproduction |
| Actual process | 사용자/PDK 계약 | 승인 PDK | 승인 pad macro | 외부 gate 필요 |

54 µm는 Kelvin regression 값일 뿐 전역 default가 아니다.

## Target routing acceptance contract

이 절은 전체 target-process E2E가 완료됐다는 뜻이 아니다. Stock Phase 1은 Pad를 재합성하고,
DUT–Pad bounded polyline의 각 segment를 최소 2-rail cross-tied mesh로 compile한다. 그러나 legacy
Phase 1은 실제 Pad macro를 import하지 않고 stock transistor adapter도 없다. Kelvin regression과
Phase 1 mesh integration은 아래 조건 일부만 검증하며 통합 transistor E2E conformance는 없다.

Direct measurement는 routing IR drop을 최소화하는 방향으로 다음 geometry를 사용한다.

- DUT 바로 옆 bounded transition을 제외하면 장거리 single rail을 금지한다.
- 가능한 corridor를 넓게 사용한 parallel rail과 repeated cross-tie mesh를 만든다.
- Mesh에는 실제 hole이 있어야 하고, solid sheet/trunk를 mesh라고 부르지 않는다.
- Pad에는 여러 개의 positive-area landing을 둔다.
- Terminal transition은 aligned one-sided staged expansion을 사용한다.
- Vertical/horizontal mesh joint는 한 rail이 자연스럽게 full-width 90°로 꺾이도록 맞춘다.
- 중간 mesh 구조는 유지하고 다른 구조와 만나는 interface만 조정한다.
- Approved spacing rule이 없을 때는 인접 metal 중 더 넓은 width 이상의 clear space를 선호한다.
- Source/Drain contact는 legal cut/space/enclosure/neighbor 조건 안에서 최대화하며 device width가
  커지면 가능한 contact 수도 증가해야 한다.

이것은 geometry acceptance다. Mesh hole과 contact 수만으로 route resistance, current crowding,
EM, density, capacitance 또는 thermal 성능을 증명하지 않는다. “최적” 주장은 approved PEX/RC
비교와 error budget이 있어야 한다.

## Transistor context default

Single-transistor measurement의 조직 planning 기본값은 다음과 같다. Stock checkout에는 실제
transistor primitive adapter가 없으며 conceptual scaffold가 이를 대신하지 않는다.

- DUT window를 `same_as_measured` array로 채운다.
- 주변 transistor는 routing하지 않는다.
- Compatible neighbor는 기본적으로 diffusion을 공유한다.
- Array edge 5 µm 안쪽의 balanced center region에서 1개를 기본 측정한다.
- 여러 개를 요청하면 같은 region에서 지정 개수만 routing한다.
- `standard_cell_like`는 `n/p/p/n` sequence와 `standard_cell_height_um`이 필요하다.

이는 사용자가 요청한 geometry 기본값이며 production-safe context라는 뜻은 아니다. Isolated,
array, standard-cell-like, guarded replica와 silicon correlation은 실제 PDK와 측정 목적에 따라
별도 승인해야 한다.

## Workflow documents

Persistent workflow는 schema v1의 content-addressed 문서를 사용한다.

- `DesignIntentDraft`: process/frame/Pad/DUT/terminal/measurement/routing/output intent.
- `ApprovedDesignIntent`: host-issued approval reference와 exact hash binding.
- `JobManifest`: append-only stage, parent hash, outputs와 fingerprints.
- `MeasurementManifest`: layout hash, DUT pin map, topology, stimulus, observable, timing, safety,
  calibration/de-embedding.

`teg_intake`, `teg_plan`, `teg_generate`, `teg_verify`는 nested `$defs`, required field,
schema version, units와 enum을 MCP schema에 노출한다. 일부 profile별 parameter/program/environment
중 program/timing/safety는 typed schema다. Profile별 calibration, de-embedding과 일부 environment
세부 값은 여전히 더 구체화해야 한다.

Canonical JSON profile은 UTF-8, sorted key, no whitespace, finite numbers와 lowercase SHA-256을
사용한다. RFC 8785 준수는 주장하지 않는다.

## MeasurementManifest와 shared Pad

Manifest는 다음 mapping을 exact design/layout hash에 묶는다.

```text
dut_id → terminal → net → pad → probe_pin → instrument_channel → electrical_role
```

Multi-DUT shared Pad는 `inactive_terminal_policy`가 필수다.

- `execution_mode`: `serial` 또는 `simultaneous`.
- `active_dut_ids`: serial은 정확히 1개, simultaneous는 2개 이상.
- 모든 non-active DUT terminal을 정확히 한 번 지정.
- State: `force`, `float`, `ground`, `guard`, `follow_shared_pad`.
- Active terminal과 같은 물리 Pad의 inactive terminal은 `follow_shared_pad`와 exact
  `terminal:<dut_id>:<terminal>` reference를 사용.
- 같은 Pad의 inactive state 및 active stimulus program은 서로 일치해야 한다.

DesignIntent의 stimulus/bias와 MeasurementManifest는 source mode, typed program,
compliance, polarity, frequency 및 정확한 multiplicity로 대조한다. Timing, environment와 safety
envelope는 exact canonical semantics가 같아야 하고 manifest가 safety를 완화할 수 없다. Active와
inactive force/guard 값도 승인 한계를 검사한다. Observable의 kind/DUT/terminal/mode/quantity/unit,
누락·추가 항목과 중복 ID/label도 거부된다. 다만
`measurement_package_complete`는 layout-bound semantics가 저장됐다는 뜻이지 exporter가 만든
tester program 또는 silicon 측정 준비 완료가 아니다.

## Evidence와 readiness

Evidence ladder:

```text
intent_draft_complete
→ intent_approved
→ plan_complete
→ generation_staged
→ drawing_complete
→ connectivity_projected
→ measurement_package_complete
→ external_evidence_attached
→ signoff_evidence_approved
```

각 단계는 선행 단계와 자체 증거를 모두 필요로 한다. 다음 readiness dimension은 독립적이다.

| Dimension | 현재 의미 |
|---|---|
| Geometry verified | Fresh reload와 semantic geometry가 확인됨 |
| Layout evidence approved | 해당 조직의 host policy가 선택한 evidence와 disposition이 필요 |
| Measurement program ready | Tester sequence/exporter와 semantic binding이 필요 |
| Silicon correlation ready | Model/PEX/silicon correlation evidence 필요 |
| PCM release ready | Lot/wafer/die traceability와 조직 release 필요 |

`process_capability.production_ready`는 호환성을 위해 남긴 필드지만 profile만으로 production을
판정하지 않으므로 항상 false이며 persistent workflow의 gate로 사용하지 않는다. DRC/LVS/PEX는
선택적 external evidence 종류다. Host `SignoffPolicy`가 공정과
업무에 맞는 non-empty subset을 고정하고, 전달된 current-layout evidence가 그 exact set과 같을
때만 content-addressed receipt를 승인한다. 성공해도 반환 상태는
`layout_signoff_evidence_approved=true`, `production_ready=false`다. Stock host에는 policy가 없어
`external_evidence_attached`에서 fail-closed하며, 조직별 release는 facade 밖의 별도 gate다.

## Production에 필요한 외부 계약

다음 정보는 repository만으로 만들 수 없다.

- Reticle/shot/scribe revision, usable keep-in, kerf, seal-ring clearance와 orientation.
- Scribe ownership, neighbor contract, allowed layer/fill/slotting/OPC/waiver 정책.
- Approved probe-pad macro hash, top/under-metal/via/passivation stack과 keepout.
- Probe technology, pitch/numbering, scrub/overtravel/touchdown와 pad damage limit.
- Exact PDK/device PCell/model, DRC/LVS/PEX deck/runset와 invocation hash.
- Adapter candidate의 필수 geometry metric/tolerance를 승인·취소하고 exact corpus/compiler에 receipt를
  발행하는 host qualification-policy authority.
- Registry writer/admin compromise까지 threat model에 포함하면 local registry root와 독립된
  WORM/signed lifecycle ledger 또는 monotonic trust anchor.
- Expected CDL/SPICE netlist, pin labels, device model mapping과 LVS tolerance.
- PEX corner, route-R/terminal-C error budget, EM/current-density/thermal/density evidence.
- Open/short/through/dummy structures와 de-embedding formula/order.
- Tester sweep/step/direction, precondition/stress/recovery와 compliance behavior.
- Dataset naming, lot/wafer/die/site/DUT traceability와 metric extraction formula.
- Replicate, wafer/reticle sampling, randomization, spec/control limit와 release authority.

이 항목은 missing이면 drawing을 불필요하게 막지는 않지만 production/signoff/measurement/PCM
readiness 승격은 막아야 한다.

## Production-ready 완료 정의

다음을 모두 만족하기 전에는 `production_ready: true`를 사용하지 않는다.

1. Approved process, layermap, Pad macro, scribe/probe 계약이 exact hash로 고정됨.
2. DesignIntent와 measurement semantics가 승인되고 exact manifest로 결합됨.
3. Fresh reload, hierarchy, DBU, layer, bbox, geometry와 connectivity evidence가 통과함.
4. 해당 조직 policy가 요구한 경우 exact layout/deck/runset evidence와 disposition이 통과함.
5. 조직 signoff policy가 evidence set을 독립적으로 승인함.
6. Measurement program, calibration/de-embedding와 traceability가 준비됨.
7. 필요한 silicon/model correlation과 PCM release authority가 확인됨.

현재 workflow evidence ladder는 이 전체 조건을 승격하는 상태를 구현하지 않았으므로 stock과
host-integrated signoff 모두 `production_ready=false`를 유지한다.

## Result와 파일 안전

모든 MCP 결과는 `ok=true` success 또는 `ok=false` structured error envelope를 사용한다.
Expected 업무 오류는 MCP `isError=true`, code/message/details/next_action을 반환한다.

- Relative input path는 MCP process `cwd` 기준이다.
- Input file은 snapshot 후 hash를 고정한다.
- Worker는 protocol stdin을 상속하지 않는다.
- Persistent output은 host-controlled root 안의 basename만 허용한다. Generic drawing은 사용자가
  지정한 기존 parent directory를 사용할 수 있다.
- 공개 file writer는 same-directory stage를 fsync한 뒤 create-only로 publish한다. Content-addressed
  directory도 같은 digest는 idempotent success, 다른 content는 conflict로 끝난다.
- 동일 job의 mutable manifest head는 OS lock과 expected-parent로 직렬화한다. 기존 final은 경쟁 loser나
  handled failure가 삭제하지 않는다.
- 이 보장은 same-host local NTFS/ext4/XFS 계약이다. NFS/SMB/multi-host writer는 mutation 전에
  fail-closed한다. ext4/XFS와 unsupported mount의 실제 qualification은 별도로 남아 있다.
- Final output은 fresh KLayout에서 다시 읽는다.
- Restart 상태는 append-only manifest ancestry로 확인한다.

`teg_status`는 모든 non-workflow output을 host output root 안에서 다시 resolve하고 regular-file
여부와 현재 SHA-256을 재검증한다. `teg_verify`는 여기에 trusted approval 재검증과 optional
measurement/external evidence promotion을 추가한다.
