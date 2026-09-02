# Workflows

이 문서는 목표별 실행 순서와 profile 사용법을 설명한다. 공통 안전 계약과 production
조건은 [contracts-and-production.md](contracts-and-production.md), 내부 구조와 테스트는
[development.md](development.md)를 참고한다. 목표 계약과 현재 구현을 구분한 권위 있는 요약은
[current-capability-boundaries.md](current-capability-boundaries.md)다.

## 목표별 경로

| 목표 | 시작점 | stock checkout | 결과 |
|---|---|---|---|
| 기존 GDS/OAS 확인 | `inspect_layout`, `compare_layouts` | 지원 | 입력 불변 inventory/XOR |
| 기존 GDS/OAS style 관측 | `extract_layout_style` | 지원 | source/profile hash를 포함한 JSON profile |
| 명시적 직교 도형 생성 | `draw_manhattan_layout` | 지원 | 새 nonproduction GDS/OAS |
| Kelvin reference 재현 | Kelvin 전용 plan/generate/compare | 지원 | 6-split nonproduction GDS |
| Non-array occurrence의 direct box 한 축을 resize한 static split GDS 생성 | PCellizer workflow | 제한 지원 | one parameter, row별 standalone GDS; reusable PCell 아님 |
| 실제 Pad macro 등록·보존 배치 | `register_pad_macro` → `compose_registered_pad_macro` | 지원 | Source Pad subtree를 수정하지 않는 overlay GDS |
| Labeled transistor corpus 등록·검토 | `onboard_transistor_corpus` → `resolve_transistor_corpus` | 지원 | Compiler-declared basis coverage, invariant style, ambiguity와 human resolution artifact |
| 재현 DUT 진단 score | `score_transistor_adapter` | 지원 | 호출자 policy 결과는 비교·진단 전용이며 candidate 자격 없음 |
| Host 승인 score·candidate 저장 | host policy authority → score → build/register candidate | 제한 지원 | 승인자/policy/hash/필수 metric 결속; callable transistor compiler나 foundry 승인은 아님 |
| Node별 reference 관리 | Reference Library workflow | 지원 | immutable reference selection |
| Persistent job | `teg_intake` | 제한 지원 | Stock은 bundled research-only Kelvin resistor profile/version만 지원 |
| Persistent plan/generate/verify | 4-call facade | host 통합 필요 | resumable evidence chain |
| Foundry sign-off/PCM release | 조직 workflow | 미지원 | 외부 PDK/deck/probe 계약 필요 |

## Tool surface mode

`KLAYOUT_MCP_TOOL_MODE`로 LLM에 노출되는 도구 수를 줄일 수 있다.

| Mode | 공개 범위 | 용도 |
|---|---|---|
| `expert` | 등록된 전체 surface | Conceptual, incomplete Phase 1과 runnable tool을 구분할 수 있는 개발자/operator 전용 |
| `facade` | Persistent facade | `server_status`와 persistent 4-call/status; stock은 `teg_plan`에서 planning 전 fail-closed |
| `drawing` | 범용 drawing surface | 범용 draw/inspect/style/compare와 standalone mesh/contact planner; Phase 1 없음 |
| `onboarding` | Pad/DUT example onboarding | Immutable pad macro, labeled DUT corpus, variation resolution, logical-validation score와 candidate package |

잘못된 mode는 `expert`로 fallback하지 않고 시작 시 실패한다.
`server_status.tool_surface.active_tools`, `capabilities`, `recommended_entrypoints`와
`persistent_facade.tools`는 선택한 mode에 맞게 filter된다. 실제 호출 가능 목록은 MCP
`tools/list`와 동일해야 하며 regression test가 이를 확인한다.
환경변수를 생략하면 `drawing`이 기본이다. 작은 모델은 persistent E2E에 `facade`, 범용 geometry에
`drawing`, Pad/DUT 등록에 `onboarding`을 사용한다. 여러 profile,
PCellizer와 reference library를 한 세션에서 함께 골라야 할 때만 `expert`가 적합하다.
도구 수는 release마다 달라질 수 있으므로 숫자를 capability로 사용하지 않고 MCP `tools/list`를
권위 있는 surface로 사용한다.
Mode는 tool schema/list만 줄이고 현재 server instruction은 공통이다. 따라서 작은 모델 권장은
검증 완료 주장이 아니라 context 부담을 줄이는 운영 지침이다.

## Generic Manhattan drawing

명시적 DBU, layer map, cell과 operation을 한 번에 전달한다. Output은 반드시 새 경로다. 이미
존재하는 target은 보존된다. 같은 local target의 동시 writer는 create-only publish를 사용하므로
정확히 하나만 성공하고 loser는 winner를 덮어쓰거나 삭제하지 않은 채 `OUTPUT_ALREADY_EXISTS`를 반환한다.

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

Host가 trusted approval verifier를 주입하고 선택 profile에 matching engine이 등록된 경우의 순서는
다음과 같다. Bundled research Kelvin engine은 stock에 등록돼 있지만 verifier는 없다. 임의 target에는
provider와 profile별 engine을 추가해야 한다.

```text
teg_intake → teg_plan → teg_generate → teg_verify
```

불완전한 intake는 immutable draft revision으로 저장되고 `draft_id`, revision, content-bound
`resume_token`을 반환한다. 단순 검사만 원하면 `validate_only=true`를 사용하며 이 경우 draft/job을
쓰지 않는다. 오류는 field path, 받은 값, 기대 조건, 이유와 다음 수정법을 포함한다.

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
저장한다. Final은 target directory의 sibling stage에서 create-only로 publish된다. 같은 local
job의 head append는 OS lock과 expected-parent로 직렬화되고, 같은 output 경쟁의 loser는 winner를
변경하지 않는다. Staging 직후,
final 기록 직후 또는 `drawing_complete` 이후 중단되면 동일 approval과 exact filename으로
`teg_generate`를 재호출한다. 저장된 layout/result hash를 확인해 generation engine을 재실행하지
않고 다음 stage를 append한다.

Bundled nonproduction Kelvin demo test의 남은 공백은 실제 stdio `teg_*`와 host-injected component를
함께 사용한 process restart다. Target-production에는 verifier, process provider, profile별
planning/generation engine, DRC/LVS/PEX execution runner/registry와 signoff policy도 추가로 없다.

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

> **현재 상태: nonproduction contract scaffold.** Stock transistor 요청은 primitive 단계에서
> `PROCESS_PRIMITIVE_ADAPTER_NOT_IMPLEMENTED`로 중단한다. 여기서 Pad는 입력 padset macro가 아니라
> frame/count로 재합성한 단일-row geometry다. Feasibility와 composition은 고정 폭 centerline box를
> 사용한다. 현재 route polyline은 multi-rail mesh compiler로 연결되었지만 실제 Pad macro 보존은
> 별도 immutable pad-macro 경로에만 구현되어 있다.

사용자가 process/Pad/DUT/terminal/bias/obstacle을 완전히 제공하지 않았다면 먼저
`plan_direct_measurement_teg`로 질문을 닫는다. 이후 순서는 다음과 같다.

```text
process capability
→ intake/terminal mapping
→ optional DOE
→ resistor/MOM primitive 또는 외부에서 주입한 verified primitive
  (stock transistor adapter 없음)
→ synthetic-pad centerline route feasibility
→ synthetic PAD_MESH + bounded multi-rail route-mesh composition
→ create-only no-clobber generation/fresh reload
```

장거리 single rail 금지와 explicit multi-metal escalation은 **목표 acceptance contract**다. 현재
Phase 1 composer는 각 bounded polyline segment를 최소 2-rail cross-tied mesh로 만들고 bend와
terminal tie를 검사한다. 여러 net의 mesh envelope를 실제 21-DUT/Pad corpus에서 함께 검증한 전역
router는 없다. 따라서 `generate_phase1_direct_teg` 결과를 실제
transistor/pad-macro/mesh E2E로 설명하면 안 된다.

## Immutable Pad macro onboarding

Pad GDS/OAS는 내부 stack을 다시 그리지 않고 black-box macro로 등록한다.

```text
register_pad_macro
→ source stream/top cell/DBU/access layer/instance transform 고정
→ eligible edge landing 확인
→ compose_registered_pad_macro
→ source Pad subtree 불변과 fresh reload 확인
```

`compose_registered_pad_macro`는 새 top cell에 등록된 Pad instance와 별도 DUT/routing box를 넣는다.
Pad cell 안의 metal, via와 passivation을 수정하거나 새 Pad geometry를 합성하지 않는다. 현재 이
overlay composer는 legacy Phase 1의 synthetic Pad 경로와 분리돼 있다.

## Labeled transistor corpus onboarding

복잡한 transistor를 한 GDS에서 추측하지 않는다. 여러 DUT가 들어 있는 source layout과 DUT별
parameter, terminal, topology와 semantic layer role을 함께 받는다.

```text
onboard_transistor_corpus
→ coverage/invariant style/same-parameter variation 확인
→ resolve_transistor_corpus
→ 외부 process-specific compiler가 reproduced GDS 생성
→ score_transistor_adapter
→ build_transistor_adapter_candidate
→ register_transistor_adapter_candidate
```

Parameter schema에는 Gate length, CPP, planar width, nFin과 cell height 같은 필요한 축을 모두 이름과
단위로 등록할 수 있다. 각 DUT row는 schema의 모든 값을 가져야 한다. Validation DUT는 이 모듈의
fitting 계산에서 제외하지만 같은 source GDS와 metadata에 남는다. 따라서 현재 경계는 sealed holdout이
아닌 logical partition이다.
`kind=integer` 값은 실제 정수여야 한다. 각 terminal은 존재하는 semantic `layer_role`을 명시하고,
각 DUT의 topology는 corpus topology와 정확히 같아야 한다.

`compiler_model_spec`에는 실제 compiler가 사용하는 basis를 명시한다. 지원 항목은 intercept,
parameter main effect, 여러 parameter interaction, 숫자 category indicator와 threshold-based regime다.
Training DUT로 만든 그 basis matrix가 full rank인지 검사한다. 이어 각 열의 크기를 정규화한 뒤
minimum singular value와 condition number를 검사한다. 따라서 형식상 full rank여도 L, CPP와 cell
height가 거의 같은 비율로 움직여 작은 입력 오차가 큰 coefficient 변화를 만드는 DOE는 차단한다.
현재 고정 gate는 minimum normalized singular value `1e-4` 이상, normalized condition number
`10000` 이하다. Parameter-space minimum margin도 evidence에 기록하지만 추가 근접 샘플 하나가 있다는
이유만으로 정상 DOE를 막지 않도록 참고 정보로만 사용한다. 따라서 L과 CPP의 각 축 예제가 있어도
`L×CPP` 항이 식별되지 않으면 차단하고, 반대로 일반 DOE가 full rank라면 one-factor-at-a-time 쌍이
없다는 이유만으로 차단하지 않는다. Conditional-variation 쌍은 이해를 돕는 정보일 뿐 합격 조건이
아니다. 부족한 basis와 rank는 `identifiability_evidence`에 영구 저장되며 score와 candidate 생성은
`DUT_CORPUS_IDENTIFIABILITY_BLOCKED`로 중단된다.

같은 parameter row인데 geometry가 다르면 `onboard_transistor_corpus`는 어느 reference DUT를 따를지
묻는다. 사용자의 결정은 immutable resolution artifact에 기록된다. 관측된 invariant metric은
drawing-style 후보이며 공정 규칙으로 승격되지 않는다.

Score는 reproduced train/validation cell을 실제로 다시 읽어 비교한다. MCP 호출자가 전달하는
`scoring_policy`는 진단용이다. Stock처럼 host `qualification_policy_authority`가 없으면 scorecard를
만들 수는 있지만 adapter candidate에는 사용할 수 없다. Candidate용 score는 host authority가 발행한
policy ID/version/hash, 승인자, corpus/compiler binding과 non-revoked receipt를 저장한다. Policy는
각 metric에 `metric_kind`(`length_um`, `area_um2`, `count`, `binary`), 비교 방식, absolute/relative
tolerance, weight와 hard-fail 여부를 따로 지정한다. Binary는 exact 비교만 허용하며 policy는 corpus에
있는 모든 metric을 빠짐없이 다뤄야 한다. Hard-fail metric 하나라도 기준을 벗어나면 평균점수와
무관하게 실패한다. Candidate build 시 authority receipt를 다시 확인하고 per-DUT weighted score와
각 metric 판정을 policy로 재계산한다.

원본 corpus GDS 자체를
reproduced output으로 제출하면 candidate evidence로 인정하지 않는다. 원본과 SHA가 다른 결과는
`distinct_stream_logical_validation_no_execution_receipt`로만 표시한다. Resolution/scorecard/candidate를
소비할 때 directory hash, schema/type, partition, unresolved blocker, compiler code hash와
technology/device/topology 결속을 다시 확인한다. 이 검사는 변조·불일치 방지이며 producer 서명이나
compiler 실행 receipt를 대신하지 않는다. 통과한 candidate도
`candidate_scored_logical_validation_not_foundry_qualified` 상태다. 현재 corpus workflow는 CPP와 연계된 Gate/Active/
Contact/implant/terminal dependency recipe를 자동 합성하거나 callable transistor PCell을 만들지 않는다.

`exact_fingerprint_required=true`이면 fingerprint 불일치는 score threshold가 0이어도 per-DUT hard-fail이다.
Reproduced GDS DBU도 corpus DBU와 도형 비교 전에 정확히 같아야 하며, 다르면 scorecard를 만들지 않는다.

Technology lifecycle의 local head는 마지막 파일 하나가 빠지는 실수를 잡는다. 같은 registry root의
record와 head를 함께 과거 상태로 바꿀 수 있는 관리자 침해도 막아야 하는 배포는 별도 WORM 또는 signed
ledger adapter를 `lifecycle_trust_anchor`로 설정해야 한다. 외부 anchor가 설정되면 append와 startup에서
현재 package sequence/hash를 확인하며, 불일치나 anchor 부재 시 registry가 시작되지 않는다.

조직 구현을 설치한 host의 `deployment.toml`은 두 stable component ID를 allowlist하고 선택한다.
아래 ID는 형식 예시일 뿐 stock에 포함된 구현 이름이 아니다.

```toml
[security]
allowed_component_ids = ["org-qualification-v1", "org-lifecycle-ledger-v1"]

[components]
qualification_policy_authority = "org-qualification-v1"
lifecycle_trust_anchor = "org-lifecycle-ledger-v1"
```

`host_doctor`는 qualification authority가 구성·trusted인지, technology registry가 external anchor로
rollback을 검사하는지 따로 표시한다.

## PCellizer

현재 목표는 복잡한 hierarchy를 flatten하지 않고 authoring-supported **non-array occurrence**의
direct box 한 축을 parameter key 하나로 resize해 CSV/Excel row별 standalone nonproduction GDS를
만드는 것이다. Reusable KLayout PCell declaration/library는 생성하지 않는다.

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
polygon PCell과 composite-DUT 자동 추론도 지원하지 않는다. Inventory/snapshot은 array member를
식별하지만 현재 batch writer는 array-member authoring을 거부한다.

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
SHA-256을 포함한다. 기존 target은 보존한다. 지원 local filesystem의 same-target concurrent writer는
create-only publish를 사용하며 loser가 winner를 덮어쓰거나 삭제하지 않는다.

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
