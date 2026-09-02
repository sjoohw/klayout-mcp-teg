# feedback.md 조치 결과 — historical snapshot

> **Historical response record.** 이 문서는 특정 피드백에 대한 당시 조치 주장을 보존한다.
> 현재 검증 결과나 capability의 권위 있는 출처가 아니다. 현재 기준은
> [Current capability boundaries](docs/current-capability-boundaries.md), 후속 계획은
> [upgrade_plan.md](upgrade_plan.md)를 사용한다.
> 아래의 “현재”와 pass count는 모두 당시 local snapshot을 가리킨다. Exact commit SHA가 기록되지
> 않았으므로 release evidence로 사용할 수 없다.

- 당시 작성일: 2026-09-02
- 당시 기준: `feedback.md`와 local working tree (exact commit SHA 미기록)
- 범위: 즉시 재현 가능한 workflow 무결성·측정 안전·운영 복구 문제
- 당시 회귀 주장: `644 passed, 0 skipped, 1 upstream warning`; compileall passed
- Kelvin final GDS 보존 SHA-256:
  `7819CC1887FF07A8DB3C54FCB91EE9A695AB6326457F895369234F0CE6E45220`

당시 구현은 generic/nonproduction drawing과 layout-bound persistent evidence workflow였다.
Foundry device-monitoring/PCM E2E 또는 production release system이라고 주장하지 않는다.

## 이번에 반영한 항목

### P0-1: External evidence가 production으로 과승격됨 — 수정 후 필수조건 정정

- Evidence ladder의 `production_ready`는 항상 `false`다.
- 기존 signoff 성공 의미를 `layout_signoff_evidence_approved`로 낮췄다.
- Host signoff policy는 version과 required evidence kind를 가진다.
- DRC/LVS/PEX는 선택적 evidence 종류이며 세 가지를 범용 필수조건으로 두지 않는다.
- Host policy가 업무에 맞는 non-empty subset을 고정하고 전달 evidence와 exact set으로 비교한다.
- Process capability의 DRC/LVS/PEX 상태도 선택적 진단 정보로 바꾸고 production gate에서 분리했다.
- Policy decision은 exact evidence hash set, policy id/version, review, approval reference를 확인한다.
- Receipt document를 canonical hash로 다시 계산해 `receipt_sha256`과 대조한다.
- 예를 들어 policy가 DRC만 요구하면 DRC evidence만으로 layout evidence 승인은 가능하지만
  `production_ready`는 계속 false다. 세 종류 전체를 요구하는 policy도 별도로 동작한다.

남은 범위:

- Approved deck/runset/PEX-corner hash와 report의 exact 결속은 아직 없다.
- Pad/scribe/probe, measurement program, silicon correlation과 PCM release가 모두 포함된 독립
  production gate는 구현하지 않았다.

### P0-2: 승인 safety와 실제 stimulus program 불일치 — 핵심 수정

- DesignIntent에 typed `dc_value`, `linear_sweep`, `ac_amplitude` program을 추가했다.
- Source mode, program, compliance, polarity, frequency를 MeasurementManifest의 실제 실행 값과
  canonical equality로 비교한다.
- Timing, environment와 safety envelope도 승인 intent와 같아야 한다.
- Manifest가 safety 한계를 완화하면 거부한다.
- Active stimulus와 inactive `force|guard`의 voltage/current 값을 승인 한계로 검사한다.
- Mode/unit 불일치, 실제 program drift, safety 완화와 timing drift 테스트를 추가했다.

남은 범위:

- `measurement_package_complete`는 승인된 layout-bound program intent이며 tester-native exporter나
  실제 instrument quantization 완료 상태가 아니다.
- Shared-Pad leakage/IR/body-effect error budget과 전체 DUT bias truth table은 추가 입력이 필요하다.

### P1-2: Final GDS 기록 직후 orphan window — 수정

- Generator는 final 이름이 아닌 unique staging stream에 기록한다.
- Staging file hash와 generation result를 먼저 `generation_staged` manifest로 영속화한다.
- Final file은 같은 directory의 sibling temp를 fsync한 뒤 `os.replace`로 승격한다.
- Staging 직후와 final 기록 직후 중단 모두 동일 요청으로 복구한다.
- Resume은 exact approval/output name/hash/result/fingerprint를 확인하고 generator를 재실행하지 않는다.
- 두 crash window를 각각 주입하는 테스트를 추가했다.

### P1-3: `teg_status`가 내부 문서를 건너뜀 — 수정

- `plan`, `generation_result`, `measurement`, `external-evidence`, `signoff-policy` namespace를
  명시적으로 매핑한다.
- 모든 `workflow://<namespace>/<sha256>`를 parse하고 CAS에서 다시 로드·재해시한다.
- URI namespace/hash mismatch, missing document와 tamper를 fail-closed한다.
- Stream과 문서 검증 결과를 `external_stream_files_verified`,
  `workflow_documents_verified`로 분리한다.

### P2-4: Windows job alias와 concurrent append — 수정

- Job ID를 lowercase `[a-z0-9_-]`, 최대 96자로 제한했다.
- Windows device alias, dot, space, colon과 case alias를 거부한다.
- Job별 OS file lock으로 local thread/process의 head update를 직렬화한다.
- Lock 안에서 current head를 다시 읽고 expected parent와 비교한다.
- 동시 job 생성 중 하나만 성공하고 다른 하나가 head conflict가 되는 테스트를 추가했다.

### 문서 snapshot·사용성 — 수정

- README와 상세 문서에 `generation_staged`, measurement binding과 signoff 의미를 반영했다.
- `expert`는 복합 기능용이며 작은 모델은 `facade` 또는 `drawing`을 우선하도록 명시했다.
- 최종 회귀 `644 passed`를 README/development와 이 문서에 동일하게 기록했다.

## 일부만 반영한 항목

| Feedback | 현재 상태 | 남은 이유 |
|---|---|---|
| P0-3 foundry 물리·release 계약 | 필요한 외부 입력과 production 완료 정의를 문서화 | 실제 reticle/scribe/pad/probe/deck/model/waiver authority가 없음 |
| P1-1 real stdio host E2E | Python host-injected Kelvin demo와 stock fail-closed 존재 | production verifier/revocation/credential bootstrap 및 stdio restart fixture 없음 |
| P1-4 shared-Pad safety | 구조 coverage, conflict, 승인 safety limit 검사 | 회로 truth table과 leakage/IR/body-effect budget 없음 |
| P1-5 preflight/electrical acceptance | external evidence adapter와 layout hash 검증 | executable/license/deck preflight 및 extracted-RC/EM acceptance 없음 |
| P1-6 PCM DOE/data | baseline/sweep/factorial/replicate drawing DOE | wafer sampling, randomization, traceability, metric/release system 없음 |
| P2-2 operator payload | template, Python 4-call demo, recovery 문서 존재 | production receipt 예제와 권한 제한 job discovery 없음 |
| P2-3 제한 모델 surface | `facade`/`drawing` mode 및 권장 기준 문서화 | 호환성을 위해 process default `expert`는 유지 |

## 의도적으로 하지 않은 작업

- Foundry/회사 자료를 임의로 만들어 production schema의 사실처럼 채우지 않았다.
- DRC/LVS/PEX availability preset을 실제 deck/license preflight로 가장하지 않았다.
- Layout signoff evidence를 measurement, silicon correlation 또는 PCM release로 승격하지 않았다.
- DRC/LVS/PEX를 drawing 또는 measurement package의 보편적 gate로 사용하지 않았다.
- `feedback.md`는 검토 원문이므로 삭제하거나 수정하지 않았다.

## agy 교차 검토

Agy에는 production 의미, measurement 결속, CAS rehash, crash recovery와 concurrency를 요약해
독립 리뷰를 요청했다. Atomic-copy 우려는 구현이 final에 직접 copy하지 않고 sibling temporary를
완전히 쓰고 해시 검증한 뒤 `os.replace`하는 구조여서 이미 충족한다. URI traversal 우려도
고정 namespace 정규식과 CAS root containment를 사용하므로 재현되지 않았다. Float exactness는
현재 문서가 instrument 실행 로그가 아니라 human-approved canonical program intent이므로 유지하며,
향후 tester exporter가 생기면 별도 quantization/tolerance 계약을 둔다.

## 현재 결론

- Generic/nonproduction drawing: 지원.
- Persistent layout evidence integrity: 이번 feedback의 재현 가능한 핵심 결함을 수정.
- Layout signoff evidence: trusted host policy가 선택한 current-layout evidence set이 있을 때만 승인.
- `production_ready`: 현재 어떤 workflow 경로에서도 true가 아님.
- Foundry device-monitoring/PCM E2E: 미완료.
