# KLayout Drawing MCP 활성 피드백

- 재검토일: 2026-09-02
- 검토 대상: `answer.md`의 조치 주장, 현재 `readme.md`/`docs/`, 구현 및 tests
- 관점: foundry device monitoring/PCM, layout 작업자, LLM operator, persistent workflow 무결성
- 관리 원칙: **해결된 과거 지적은 이 문서에서 제거하고, 현재 재현되는 문제와 미완료 계약만 기록한다.**

## 현재 판정

`answer.md`의 조치 주장을 구현과 테스트로 대조했고, 해결이 확인된 과거 지적은 이 문서에서
제거했다. 아래에는 부분 해결, 미해결 또는 새로 재현된 항목만 남긴다.

그러나 현재 구현은 여전히 **generic/nonproduction drawing 및 일부 persistent integrity workflow**다.
`foundry production-ready` 또는 `device-monitoring/PCM E2E`라고 판정할 수 없다. 특히 코드가
반환하는 `production_ready=true`는 문서에 적힌 production 완료 정의보다 훨씬 약하다.

## 독립 검증 결과

전체 회귀를 다시 실행했다.

```text
command: uv run --extra dev pytest -q
result: 645 passed, 1 warning, 0 failed, 0 skipped
elapsed: 243.56 s
```

핵심 workflow 관련 targeted suite도 별도로 실행했다.

```text
workflow/store/manifest/external-evidence/process/evidence/tool-mode/MCP tests
result: 106 passed
```

테스트 통과는 현재 작성된 기대 동작과 구현이 일치한다는 뜻이다. 아래 P0 중 일부는 회귀
테스트가 오히려 위험한 결과를 정상 성공으로 기대하고 있어, 단순히 test 수를 늘리는 것으로는
해결되지 않는다.

## P0 — production 또는 측정 안전성을 잘못 승격할 수 있는 문제

### P0-1. Current-job DRC 한 건만으로 `production_ready=true`가 가능하다

문서 `docs/contracts-and-production.md:183-193`은 `production_ready=true` 전에 approved
pad/scribe/probe 계약, exact DRC/LVS/PEX, measurement/de-embedding, correlation과 PCM authority까지
요구한다. 실제 구현은 이 정의를 강제하지 않는다.

- `src/klayout_mcp/workflow_store.py:1886-1905`는 DesignIntent의
  `external_evidence_required` 목록만 exact set으로 취급한다.
- `src/klayout_mcp/workflow_store.py:2026-2038`은 process capability의 DRC/LVS/PEX 상태 문자열이
  `approved`인지 확인한다.
- `src/klayout_mcp/workflow_store.py:2060-2120`은 trusted policy가 전달한 evidence set을 승인하면
  즉시 `production_ready=true`를 반환한다.
- `tests/test_workflow_store.py:960-1054`는 `external_evidence_required=["drc"]`와 DRC 보고서
  한 건만으로 `production_ready=true`가 되는 경로를 성공 테스트로 고정한다.

즉 process profile의 LVS/PEX `approved` 문자열은 current GDS에 대한 LVS/PEX report가 아니다.
Current layout의 LVS/PEX 결과 없이 DRC report 하나만으로 production 승격이 가능하다.

추가로 external report의 `deck_sha256`은 형식과 disposition 내부 일관성만 확인한다. 승인된
process capability의 exact deck/runset hash와 비교하지 않는다. 현재 테스트도 임의의
`"a" * 64` deck hash를 사용한다. Signoff policy의 `receipt_sha256` 역시 실제 receipt artifact를
재해시하지 않고 64자리 hex 형식만 확인한다.

필수 수정:

1. `production_ready`를 당장은 `layout_signoff_evidence_approved`로 이름을 낮춘다. 또는 문서의
   전체 조건을 별도 독립 gate로 실제 구현한다.
2. Production policy가 요구하는 current-job evidence set을 host-controlled versioned policy로
   고정하고 LLM/DesignIntent가 축소하지 못하게 한다.
3. 해당 GDS에 대한 DRC/LVS/PEX 또는 승인된 명시적 N/A disposition을 모두 요구한다.
4. Process/device/pad/scribe/probe/deck/runset/PEX-corner hash를 approval 및 report와 결속한다.
5. `measurement_program_ready`, `silicon_correlation_ready`, `pcm_release_ready`를 실제 boolean
   evidence state로 구현하고 하나가 다른 상태를 암시하지 않게 한다.

Acceptance test:

> DRC report만 있고 current-layout LVS/PEX report 또는 승인된 N/A가 없는 fixture는 trusted
> signoff policy가 승인해도 `production_ready=false`여야 한다.

### P0-2. 승인된 측정 safety와 실제 stimulus program이 DesignIntent에 결속되지 않는다

K1 요구를 K2로 바꾸는 기존 drift와 requirement multiplicity 문제는 해결됐다. 그러나 현재
cross-validation은 manifest가 스스로 선언한 `requirement_kind/mode/quantity/unit` label을
DesignIntent와 비교할 뿐, 그 label이 실제 `source_mode`, `program`, compliance와 같은지는
확인하지 않는다.

`src/klayout_mcp/workflow_manifest.py:1295-1529` 기준으로 다음 반례를 재현했다.

```text
DesignIntent requirement mode: voltage_sweep
Manifest requirement_mode label: voltage_sweep
실제 source_mode: current
실제 program: {value: 0.001, unit: A}
결과: intent_binding_verified=true
```

더 심각하게 DesignIntent의 승인된 safety envelope와 MeasurementManifest의 safety envelope를
서로 비교하지 않는다. 다음 반례도 통과했다.

```text
DesignIntent max_abs_voltage_v: 1.0
Manifest max_abs_voltage_v: 10.0
실제 program: 5.0 V
결과: intent_binding_verified=true
```

Validator는 manifest가 나중에 스스로 올린 10 V 한계만 기준으로 5 V를 검사한다. 또한 shared-Pad
inactive terminal의 `force` 값은 finite value/unit만 검사하며 safety envelope와 비교하지 않는다.

`workflow_types.py`에서도 `program`, `integration`, `environment`, safety `limits`, topology와
de-embedding reference는 여전히 자유형 object 또는 `list[Any]`다. 따라서 “exact measurement
semantics binding 완료”가 아니라 **requirement identity/coverage binding 완료**로 범위를 줄여야 한다.

필수 수정:

1. DesignIntent 또는 별도 human-approved MeasurementProgramIntent에 source type, sweep
   start/stop/step/direction, compliance, polarity, timing, environment와 safety를 canonical schema로
   포함한다.
2. Manifest의 실제 source/program/compliance를 label이 아닌 승인된 canonical semantics와 비교한다.
3. Safety envelope는 manifest가 재정의하지 못하게 하고 exact approved hash를 참조하게 한다.
4. Active stimulus뿐 아니라 inactive `force`/guard와 shared-Pad effective bias도 safety gate에 넣는다.
5. Voltage/current mode-unit 불일치, safety 완화, sweep drift와 inactive over-bias 회귀 테스트를 추가한다.

### P0-3. Foundry 물리 계약과 device-monitoring release 계약은 문서에만 있다

Production에 필요한 외부 입력 목록은 `docs/contracts-and-production.md`에 잘 정리됐다. 과거의
“문서에 scribe/probe 항목이 없다”는 지적은 해결됐다. 그러나 실행 가능한 typed input, approval
binding과 readiness gate는 아직 없다.

`src/klayout_mcp/workflow_types.py:21-35`의 frame/Pad schema에는 다음이 없다.

- reticle/shot/scribe revision, kerf와 seal-ring keepout
- approved pad macro hash와 top/under-metal/via/passivation stack
- probe technology, scrub, overtravel, touchdown current와 damage limit
- expected netlist/model mapping, exact deck/runset와 PEX corner

Approval의 `source_artifact_sha256s`도 `workflow_manifest.py:637-648`에서 이름이 자유로운 artifact
한 건만 있으면 된다. Production에 필요한 role set을 요구하지 않는다.

Device-monitoring 측면에서도 tester exporter, sweep execution, de-embedding formula/order,
dataset naming, metric extraction, lot/wafer/die/site/DUT traceability와 PCM release authority가 없다.
이 정보가 제공되지 않은 상태를 정직하게 fail-closed하는 것은 맞지만, 현재 목표인 foundry
device-monitoring E2E는 아직 완성되지 않았다.

## P1 — persistent E2E와 운영 복구에 남은 문제

### P1-1. 실제 stdio host-integrated persistent E2E와 bootstrap이 없다

Stock fail-closed와 test-only Python facade demo의 경계는 이제 명확하다. 하지만 실제 host에서
다음을 등록하고 운영하는 실행 가능한 bootstrap은 없다.

- process capability provider와 profile engine registry
- production approval verifier, expiry/revocation backend
- external evidence adapter와 signoff policy
- workflow/output root, secret/credential와 policy rotation

Persistent Kelvin demo는 Python에서 facade를 직접 생성한다. 실제
`typed MCP request → stdio teg_* → host-injected components → process restart → teg_status/resume`
경로는 여전히 자동화되지 않았다.

### P1-2. Generation resume에는 `drawing_complete` 직전 orphan window가 남아 있다

`drawing_complete` manifest가 기록된 뒤의 resume은 구현됐다. 그러나
`workflow_store.py:1512-1612`에서 engine이 final GDS를 쓴 뒤 첫 drawing manifest를 append하기
전에 process가 중단되면 다음 상태가 된다.

```text
head stage: plan_complete
final GDS: 존재
재호출 결과: WORKFLOW_OUTPUT_ALREADY_EXISTS
```

즉 `answer.md`의 “drawing_complete 중단 복구”는 맞지만 포괄적인 “generation resume 완료”는
아니다. Output을 쓰기 전 durable intent/staging stage를 기록하거나, orphan output과 generation
result를 exact plan/approval에 안전하게 재결속하는 recovery가 필요하다.

Acceptance test:

> Final output write 직후와 `drawing_complete` append 직전에 crash를 주입한 뒤, 동일 요청을
> 재호출하면 engine 중복 실행이나 output overwrite 없이 안전하게 복구돼야 한다.

### P1-3. `teg_status`는 GDS를 재해시하지만 내부 workflow artifact는 검사하지 않는다

실제 GDS/OAS의 missing/tamper 검사는 해결됐다. 그러나 `workflow_store.py:827-866`은
`workflow://` reference를 모두 건너뛴다. Plan, generation result, MeasurementManifest, external
evidence와 signoff decision document가 삭제되어도 manifest ancestry만 남으면 status가 성공한다.

특히 signoff decision document가 없는데도 `highest_attained_state=signoff_evidence_approved`와
`production_ready=true`를 보고할 수 있다. `output_file_integrity_verified=true`라는 이름도 외부
stream file만 확인했다는 범위가 드러나지 않는다.

필수 수정:

- role별 `workflow://<kind>/<sha256>`를 parse해 expected content-addressed store에서 재로드·재해시한다.
- missing/kind mismatch/hash mismatch를 fail-closed한다.
- `external_stream_files_verified`와 `workflow_documents_verified`를 별도 결과로 반환한다.

### P1-4. Shared-Pad의 실제 bias 안전성은 아직 승인·검증되지 않는다

Inactive-terminal coverage, serial/simultaneous mode, `follow_shared_pad`와 물리 Pad 충돌 검사는
실제로 구현됐다. 남은 문제는 다음과 같다.

- inactive policy가 DesignIntent의 승인 요구가 아니라 MeasurementManifest에서 처음 결정된다.
- inactive `force` 값이 approved safety envelope와 비교되지 않는다.
- 21-site topology의 per-DUT active/inactive bias truth table과 reference netlist가 없다.
- leakage, shared-bus IR drop, body effect와 simultaneous measurement error budget이 없다.

따라서 이 항목은 “강한 구조 검증”까지는 반영됐지만 foundry 측정 격리 완료는 아니다.

### P1-5. Engine/deck preflight와 electrical acceptance가 없다

Organization preset의 DRC/LVS/PEX `available`은 여전히 정적 문자열이다. 실제 executable,
license, adapter, exact deck/runset/version과 `checked_at`을 확인하는 preflight 결과 schema가 없다.

Mesh가 geometry goal일 뿐 PEX-optimal이 아니라는 문서 경계는 해결됐다. 다만 production gate에
다음 electrical acceptance가 연결되지 않는다.

- extracted route R와 terminal C budget
- PEX corner와 parasitic error budget
- EM/current-density/current-crowding
- density/antenna/thermal 및 waiver disposition

### P1-6. PCM DOE와 데이터 흐름은 부분 구현에 머문다

Baseline/sweep, OFAT/full-factorial과 logical replicate는 구현됐다. 따라서 과거의 “replicate가
없다”는 지적은 제거한다. 남은 것은 wafer/reticle/site sampling block, randomization,
spec/control limit, lot/wafer/die traceability, reference/de-embedding 배치와 metric release다.

## P2 — 문서·operator·동시성 완성도

### P2-1. Current validation snapshot이 문서끼리 다시 충돌한다

실제 이번 실행 결과는 `645 passed, 1 warning`이며 `readme.md:275-280` 및 `answer.md`와 일치한다.
그러나 `docs/development.md:40-50`은 여전히 `637 collected / 637 passed`를 “Current validated
snapshot”으로 표시한다.

Commit, checked-at, command, collected/passed/skipped/warning을 한 generated source에서 읽도록 해
README와 development 문서를 동기화해야 한다.

### P2-2. Operator용 complete payload와 recovery surface가 부족하다

Nested schema와 handoff 표는 개선됐지만 다음은 여전히 없다.

- persistent 4-call 전체 request/expected-response fixture
- production approval/signoff receipt와 external evidence 예제
- actual padset 분석 결과를 다음 plan/generation으로 넘기거나 명시적으로 종료하는 예
- `job_id` 분실 시 권한 제한된 list/discovery 경로

README는 299줄과 세 상세 문서로 크게 개선됐다. 추가 분리 자체보다 위 실행 예와 host
integration guide를 우선하는 것이 적절하다.

### P2-3. 제한 모델의 기본 surface는 여전히 expert 57개다

Mode별 status 불일치는 해결됐다. 다만 일반 MCP host 예제가 `expert`를 사용하고 expert가
기본값이다. 제한 모델 또는 처음 사용하는 operator에게는 목적에 맞춰 `drawing`이나 `facade`를
권장하고, expert opt-in 기준을 설명하는 편이 안전하다.

### P2-4. Windows job ID와 concurrent append 경계가 남아 있다

- `JOB_ID_PATTERN`은 Windows에서 alias가 되는 trailing dot 및 reserved device name을 허용한다.
  예: `alpha`와 `alpha.`가 같은 directory를 가리킬 수 있다.
- manifest head 확인과 pointer 교체 사이에 per-job lock 또는 atomic compare-and-swap이 없어 같은
  job에 대한 동시 요청이 lost update를 만들 수 있다.

Windows-safe canonical job ID validation과 per-job serialization/CAS test가 필요하다.

## 남은 완료 기준

다음 조건을 충족한 뒤에만 `foundry device-monitoring E2E` 또는 포괄적인
`production_ready=true`를 사용하는 것이 적절하다.

1. Current layout의 mandatory DRC/LVS/PEX와 approved deck/runset hash를 host policy가 강제한다.
2. Scribe/probe/pad/device/model 계약이 typed schema와 approval hash에 포함된다.
3. 실제 source program, safety, timing, environment와 inactive bias가 승인 intent에 결속된다.
4. Measurement program, de-embedding, dataset/metric/traceability와 PCM release 상태가 독립적으로
   구현된다.
5. Real stdio host-injected 4-call을 process restart와 함께 검증한다.
6. Generation의 output-write 전후 모든 crash window에서 deterministic resume가 가능하다.
7. `teg_status`가 external file과 모든 referenced workflow document를 함께 재검증한다.
8. Shared-Pad truth table/netlist와 leakage/IR/body-effect error budget을 승인한다.
9. Executable/license/deck/runset/adapter preflight 및 electrical PEX/EM acceptance를 구현한다.
10. Validation snapshot을 문서 전체에서 하나의 현재 값으로 유지한다.

## 최종 결론

남은 가장 높은 위험은 다음 두 가지다.

1. Current-job DRC 한 건만으로 `production_ready=true`가 가능한 gate.
2. 승인된 safety와 다른 실제 stimulus program을 MeasurementManifest가 self-declared label로
   통과시킬 수 있는 semantic binding.

이 두 항목을 해결하기 전에는 현재 상태를 **nonproduction drawing과 persistent integrity가
강화된 상태**로 표현하는 것이 정확하다.
