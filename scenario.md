# 사용자 관점 검증 시나리오

이 문서는 현재 checkout에서 실제로 가능한 흐름과 외부 통합이 필요한 흐름을 구분한다.
Fabrication process profile은 번들하지 않으며, 타깃 환경은 [onboarding.md](onboarding.md)에 따라
명시적으로 등록한다. SLN001 값은 Kelvin 회귀 예제 밖에서 재사용하지 않는다.

> 이 프로젝트는 결정론적 layout drawing·inspection·fresh-reload 검증 도구다. Stock 결과는
> foundry sign-off, tape-out, PCM release, 전기적 성능 또는 auto-prober 실행을 의미하지 않는다.
> DRC/LVS/PEX는 조직 정책이 선택할 수 있는 외부 evidence이며 범용 drawing의 필수 단계가 아니다.

등록된 tool과 목표 계약을 현재 구현으로 오해하지 않으려면
[Current capability boundaries](docs/current-capability-boundaries.md)를 먼저 확인한다.

## 지원 수준

| 수준 | 의미 |
|---|---|
| Stock 실행 가능 | Repository와 KLayout만으로 끝까지 실행하고 fresh reload할 수 있음 |
| 조건부 가능 | 사용자 선택, 명시적 공정 입력 또는 trusted host component가 필요함 |
| Roadmap | 현재 API가 결과를 만들거나 검증하지 않음 |

## Profile을 혼동하지 않기 위한 기준

| Profile | 용도 | Frame / DBU | 주요 layer | 근거 수준 |
|---|---|---|---|---|
| `sln001_kelvin_reference_demo` | 6-split Kelvin 회귀 재현 | 2000×54 µm / 0.00025 µm | M1 `(15,0)` | 보존된 layout과의 deterministic regression reference |
| 사용자 제공 profile | Target drawing 후보; 외부 구현 필요 | onboarding 입력 | 사용자 승인 layermap | profile 선언만으로 adapter readiness가 되지 않음 |

25개 40×40 µm Pad와 각 frame 크기는 해당 example의 계약일 뿐 표준 PCM, scribe 또는
probe-card 규격이라는 뜻이 아니다. 실제 공정에는 승인된 layermap, pad macro와 scribe/probe
계약을 별도로 제공해야 한다.

## 시나리오 A — 실제 타깃 공정 온보딩

**지원 수준: Stock 미지원; process-specific host 구현이 있을 때만 조건부 가능**

MCP는 내장 공정값을 제공하지 않는다. 타깃 LLM은 다음 순서를 따른다.

```text
describe_pdk_profile_inputs
→ process identity/DBU/grid/layermap와 필요한 rule 수집
→ organization preset과 drawing-job 입력 분리
→ validate_process_capability_profile
→ 사용자 확인 reference 등록·열람
→ process-specific primitive adapter 검증
→ representative pilot drawing/fresh reload
```

Unknown layer/rule/device geometry는 추정하지 않는다. Frame 2000×54 µm 전후, 25개 40×40 µm Pad,
first-metal 우선과 direct measurement는 조직 기본값 후보이며 PDK 사실이 아니다. Transistor는
타깃 공정 adapter가 준비되기 전에는 geometry 생성을 시작하지 않는다.

여기서 필요한 host 구현에는 실제 transistor primitive adapter, pad macro hierarchy/stack을 보존하는
composer, bounded mesh-aware global router와 foundry verification runner가 포함된다. Profile JSON과
reference GDS를 제공하는 것만으로 이 구현이 생기지 않는다.

## 시나리오 B — SLN001 Kelvin 6-split 재현과 XOR

**지원 수준: Stock 실행 가능, nonproduction regression**

먼저 `output/scenario-validation/kelvin/`을 만든다. Kelvin work directory는 tool이 생성할 수
있지만 output이 그 directory 아래에 있어야 한다.

Reference:

```text
artifacts/SLN001_kelvin_m1/SLN001_kelvin_m1_aligned_force_pad_joint_v15.gds
```

지원되는 split은 W 22/100/300 nm × L 300/1000 nm이다. 측정선은 horizontal이고,
SENSE+/FORCE+/FORCE-/SENSE- 배치, direct force, straight-up sense, one-sided 1→2→4→6 staged
mesh, aligned full-width 90° joint와 multiple Pad landing을 사용한다.

```json
{
  "template_gds_path": "artifacts/SLN001_kelvin_m1/SLN001_kelvin_m1_aligned_force_pad_joint_v15.gds",
  "output_gds_path": "output/scenario-validation/kelvin/regenerated.gds",
  "work_directory_path": "output/scenario-validation/kelvin",
  "dimension_semantics": "width_is_transverse_axis_length_is_longitudinal_axis",
  "confirm_routing_contract": true,
  "reference_gds_path": "artifacts/SLN001_kelvin_m1/SLN001_kelvin_m1_aligned_force_pad_joint_v15.gds",
  "require_reference_equivalence": true
}
```

성공 기준은 fresh reload, single top, orthogonal box-only geometry, expected M1 topology와
recursive geometry XOR 0이다. 이는 이 **특정 regression reference와 geometry가 같다**는 뜻이며,
다른 공정 규칙 준수, contact-resistance 구조 또는 foundry 승인이라는 뜻은 아니다.

## 시나리오 C — Reference precedent marker 분류

**지원 수준: 조건부 가능, advisory only**

사용자가 full reference GDS를 KLayout에서 확인하고 `reference_precedent` selection을 확정한 뒤,
외부 DRC 실행에서 정규화된 marker와 context signature를 제공하면
`classify_reference_drc_markers`가 다음처럼 분류한다.

- 같은 process/concern/layer/rule/context와 허용 deviation에 해당: `REF_ACCEPTED`.
- 일치하는 precedent가 없음: `REVIEW_NEEDED`.

MCP는 foundry DRC deck을 자동 실행하거나 marker를 추출하지 않는다. `REF_ACCEPTED`는 reference에
같은 motif가 있다는 advisory 분류이며 DRC-clean, waiver 승인 또는 sign-off evidence 승인이 아니다.
Marker 증가도 drawing을 자동 차단하지 않는다.

Reference 후보의 drawing 관행은 먼저 `extract_layout_style`로 hierarchy reuse, layer별 geometry,
직교성, 치수 빈도와 mesh hole을 관측할 수 있다. 이 profile은 reference view descriptor의 후보이며,
사용자가 KLayout에서 실제 GDS를 확인하기 전에는 선택된 reference style로 승격하지 않는다.

## 조건부 시나리오 — GDS PCellizer split batch

Hierarchy inventory, KLayout dock의 shape/ruler capture, immutable snapshot, CSV/Excel split table,
hierarchy copy-on-write와 fresh reload는 지원한다. 현재 recipe compiler는 **직접 선택된 box 한 개와
parameter 한 개**만 지원하며 W/L 의미를 자동 추론하지 않는다.

따라서 “임의의 복잡한 DUT GDS에서 Gate/Active를 찾아 W와 L을 동시에 PCell화하고 3×7 Cartesian
batch를 자동 생성”하는 흐름은 아직 지원하지 않는다. 사용자가 한 dimension의 대상 box, ruler,
anchor와 의미를 명시적으로 확정한 경우에만 현재 PCellizer 시나리오를 실행할 수 있다.

## 조건부 시나리오 — Resistor/MOM geometry

`plan_metal_resistor_primitive`와 `plan_mom_capacitor_primitive`는 명시적 width/space/length/finger
등으로 직교 geometry를 계획한다. Sheet resistance, corner correction, capacitance-per-length 또는
process model이 없으므로 목표 Ω/fF를 치수로 합성하거나 전기값을 주장하지 않는다. 목표값 기반
합성은 승인된 공정 모델 adapter가 추가된 뒤의 기능이다.

## 조건부 시나리오 — Persistent evidence workflow

```text
teg_intake → teg_plan → teg_generate → teg_verify
```

`teg_intake`와 durable job 저장은 stock에서 가능하다. Bundled Kelvin planning/generation engine은
등록돼 있지만 trusted approval verifier가 없어 `teg_plan`의 planning 전에 중단된다. 임의 target이나
production profile은 verifier 외에도 matching provider, engine, runner와 policy를 host가 주입해야 한다.
MeasurementManifest는 exact layout hash에
DUT→terminal→net→Pad→probe pin→instrument channel과 stimulus/safety semantics를 묶지만 tester
program이 아니다. Keysight/Cascade exporter, instrument driver, calibration/de-embedding 실행과
wafer traceability는 현재 repository에 없다.

## 현재 Roadmap

- 실제 transistor primitive adapter와 production registry.
- 실제 pad macro hierarchy/stack 보존형 composition.
- Bounded 21-DUT global mesh routing과 Phase 1 통합.
- 임의 hierarchy/composite DUT의 multi-parameter PCell 자동 추론.
- 승인된 process electrical model을 사용한 target R/C synthesis.
- 조직별 DRC/LVS/PEX adapter와 waiver authority 연결.
- Tester/prober program export와 실행·측정 데이터 traceability.
- Silicon correlation 및 PCM/tape-out release workflow.

Roadmap 항목이 없다고 drawing을 막지는 않는다. 다만 해당 evidence 없이 결과의 의미를
production, electrical 또는 measurement readiness로 승격하지 않는다.
