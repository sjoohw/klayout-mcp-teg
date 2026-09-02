# Transistor TEG 자동화 업그레이드 계획

## 1. 판정과 목표

현재 구현은 `generic/nonproduction geometry + 검증 계약 실험` 단계다. 실제 공정의 transistor와
probe pad를 사용해 소자 모니터링 TEG를 자동 생성하는 현업용 도구로 분류하면 안 된다. 리뷰의 핵심
판정은 타당하다.

이번 업그레이드의 1차 목표는 범용 foundry 지원이 아니다. 다음 한 개의 대표 경로를 먼저 완주하는
것이다.

> 정확히 고정된 한 공정/PDK revision에서 승인된 transistor source와 immutable probe-pad macro를 사용해
> 21-DUT 대표 DOE를 만들고, 장거리 배선을 mesh로 생성한 뒤 fresh reload와 foundry DRC pilot까지
> hash로 결속한다.

### 이번 계획에서 고정하는 구현 전제

이번 pilot은 다음 전제를 사용해 불필요한 범용화를 피한다.

- Transistor 목표는 현재 PCellizer의 box resize가 아니다. 사용자가 제공한 **여러 DUT가 포함된 예시
  layout + DUT별 parameter manifest**를 onboarding corpus로 삼아 Gate length, gate pitch, planar width,
  FinFET `nFin`, cell height 등 공정별 수십 개 parameter의 geometry dependency 후보를 찾고 사용자가
  확인한 recipe를 실행하는 `TransistorPrimitiveAdapter`다. 단일 nominal GDS에서 변화 규칙이나
  design rule을 자동 추론했다고 주장하지 않는다.
- Parameter가 달라도 corpus에서 유지되는 geometry/alignment/topology 특성은 scoped
  `DrawingStyleProfile` 후보로 분리한다. 다만 관측 범위에서 변하지 않았다는 사실을 공정 rule이나
  범위 밖 universal invariant로 승격하지 않는다.
- Parameter로 설명되지 않는 DUT별 차이는 자동 평균화·삭제하지 않는다. Legacy 실측에 문제가 없어
  의도적으로 계속 쓰는 변이일 수 있으므로, 특정 DUT를 따를지, 다수 pattern을 따를지, topology별로
  유지할지 또는 새 explicit rule을 적용할지 사용자에게 묻고 승인 전에는 신규 DUT를 생성하지 않는다.
- 한 번 승인·검증한 adapter는 exact technology/PDK revision, layermap, DBU/grid, device family와 topology에
  결속된 immutable `TechnologyAdapterPackage`로 등록한다. 다음 작업에서는 compatible package를 선택해
  onboarding을 반복하지 않되, wildcard·`latest`·nearest-version 추정이나 stale package 재사용은 금지한다.
- Adapter 품질은 단일 유사도 숫자가 아니라 reference 재현, 승인된 Drawing Style 준수와 학습에 사용하지
  않은 sealed holdout 재현을 분리한 다차원 scorecard로 판정한다. Critical geometry, G/D/S/B terminal,
  connectivity, grid와 unresolved variation은 다른 점수로 상쇄할 수 없고 foundry DRC는 별도 hard gate다.
- Probe Pad는 승인된 별도 GDS cell을 수정 없이 instance로 배치한다. 첫 pilot 입력은 40 µm × 40 µm,
  동일 DBU, 추가 keepout 없음, 모든 pad metal이 pad box 내부에 존재한다는
  계약을 가진다. 지정 access-metal의 bbox/edge에서 landing polygon만 계산하며 pad 내부 metal,
  via 또는 passivation을 새로 그리지 않는다.
- Existing straight-corridor mesh compiler의 geometry 생성은 이미 사용 가능하다. 빠진 기능은 router의
  multi-bend polyline을 직선 구간으로 나누고, 각 구간의 mesh와 90° joint·DUT/Pad landing을 합쳐
  Phase 1 composer에 넘기는 integration layer다.
- Foundry pilot은 위 geometry 생성과 별개다. 최종 GDS가 실제 deck에서 허용되는지는 reference GDS
  관측이나 내부 geometry test로 대체하지 않고 외부 DRC evidence로 판정한다.
- 사용자가 고쳐야 하는 입력 문제를 `INVALID_INPUT` 같은 한 문장으로 반환하지 않는다. 모든 public
  workflow는 mutation 전 preflight, 한 번에 모은 field-level 문제, 수정 예시·추가 질문과 같은
  draft에서 재개할 수 있는 경로를 제공한다.

이 목표를 달성해도 `production_ready=true`로 승격하지 않는다. 기존 `workflow_status` stage machine은
그대로 유지하고, 아래 값은 서로 독립적인 `qualification.<dimension>` evidence로 저장한다. 따라서 layout,
model, runtime, measurement qualification은 서로 다른 시점에 true일 수 있다.

| Dimension | Evidence 값 | 의미 | 필수 증거 |
|---|---|---|---|
| `layout` | `target_process_geometry_pilot_complete` | 실제 primitive, immutable PAD40 macro, route를 포함한 GDS가 내부 검증됨 | source provenance, fresh reload, geometry/connectivity 검증 |
| `layout` | `foundry_layout_pilot_passed` | 같은 GDS가 조직 policy에 따라 layout pilot으로 수용됨 | exact layout/deck/invocation hash, `drc_clean` 또는 `drc_accepted_with_dispositions` 경로, 지원 시 LVS |
| `model` | `exact_gemma4_qualified` | 지정 Gemma4 runtime이 평가 기준을 통과함 | exact model/runtime/decoding과 scenario evidence |
| `runtime` | `rhel_csh_runtime_qualified` | 지정 폐쇄망 RHEL/csh 배포가 검증됨 | pinned image/bundle/launcher evidence |
| `deployment` | `deployment_qualified` | 위 exact model이 위 runtime의 MCP와 교차 E2E를 통과함 | model→RHEL MCP happy/recovery/safety evidence |
| `measurement` | `measurement_pilot_ready` | tester와 기생성분까지 평가됨 | MeasurementManifest, calibration/de-embedding, PEX/RC error budget |
| `release` | `production_ready` | 조직의 release 조건까지 완료됨 | 위 증거와 lot/wafer/die traceability, release authority |

첫 layout release gate는 `qualification.layout=foundry_layout_pilot_passed`다. PEX, tester export,
silicon correlation 및 PCM release는 후속 gate이며, 준비되지 않은 dimension은 계속 false 또는
unavailable로 남긴다.

## 2. 검토로 확인된 현재 기준선

기준은 `main` commit `1df82b5043a41cf1485bdc7e1bf43c9a2930d1cf`, 2026-09-02 KST다.

| 항목 | 확인 결과 | 계획에 반영할 보정 |
|---|---|---|
| Transistor primitive | `phase1_workflow.py`는 transistor에서 항상 `PROCESS_PRIMITIVE_ADAPTER_NOT_IMPLEMENTED`를 반환한다. | 비슷한 polygon을 그리는 문제가 아니라 공정별 다중 parameter와 dependent geometry를 materialize하는 adapter가 없는 것이 blocker다. |
| Conceptual transistor | `dut_geometry.py`는 0.22 µm contact 등 합성 치수를 쓰고 `conceptual_scaffold`를 반환한다. | Phase 1 handoff gate가 직접 주입은 막지만, 기본 expert surface에서 별도 E2E 대안처럼 보이는 오선택 위험이 있다. |
| Pad macro | Phase 1은 pad GDS를 입력받지 않고 frame/pad count로 중심과 box를 다시 만든다. | PAD40/same-DBU/no-extra-keepout 계약에서는 어려운 geometry inference가 아니라 immutable cell import와 access-metal edge terminal 등록이 빠진 상태다. |
| Routing geometry | Phase 1은 centerline segment를 동일 폭 box로 변환한다. 별도 straight-corridor mesh compiler와 예제 geometry는 동작한다. | Mesh 알고리즘 부재가 아니라 router polyline→segment mesh→bend/landing→composer 연결이 빠진 상태다. |
| Router | connection당 후보는 96개로 제한되지만 DFS에는 node/deadline/cancellation 제한이 없다. | 탐색은 이론상 유한하나 운영 시간은 bounded가 아니다. 1-DUT/4-terminal 테스트만으로 21-site를 증명하지 못한다. |
| PCellizer | Authoring-supported non-array occurrence의 direct box 하나에서 한 축을 parameter key 하나로 resize한다. | 이름과 달리 multi-parameter PCell generator가 아니다. `single-box variant generator`로 취급하고 실제 transistor adapter의 backend로 직접 승격하지 않는다. |
| Persistent facade | stock server는 `approval_verifier=None`이며 production transistor engine과 foundry 실행 adapter가 없다. | `teg_plan` 진입 시 planning 전에 fail-closed하는 보안 동작은 유지하되, 설치·설정 가능한 host 조립 경로가 필요하다. |
| 제한 모델 (working tree) | Generic drawing no-clobber schema를 반영한 재계측에서 serialized `tools/list` 전체 record는 expert 56/111,073자, facade 6/26,718자, drawing 7/13,176자다. 이 중 `inputSchema`만은 각각 72,407/22,827/8,050자이고 공통 instruction은 8,248자다. | drawing에는 Phase 1이 없고 stock facade는 intake/status만 가능하며 verifier 부재로 planning 전에 중단된다. Mode instruction도 아직 공통이고 현재 harness는 Gemma4가 아닌 proxy다. |
| Baseline remote CI | Baseline SHA의 [Actions run 33589034379](https://github.com/sjoohw/klayout-mcp-teg/actions/runs/33589034379)는 pytest 5개 job이 모두 실패했고 csh smoke만 성공했다. | Linux의 OS 고정 assertion, agy 부재 시 schema key 누락 외에 Windows content-store race도 있다. |
| Baseline 로컬 회귀 | Windows/Python 3.13.5/KLayout 0.30.10에서 `646 passed, 1 warning`이었다. | baseline의 ambient OS/agy와 concurrency timing이 결함을 가리고, KLayout 유무는 skip 수를 바꾼다. README의 `644 passed`를 현재 증거로 쓰지 않는다. |
| Output race | `worker_drawing.py`는 최초 exists check 뒤 `os.replace()`하며, 실패 cleanup에서 final path까지 지운다. | 후발 writer의 overwrite 및 선행 결과 삭제가 모두 가능하다. |
| Baseline RHEL/csh | Baseline launcher는 `python3`를 고정 실행했고 `uv sync`의 `.venv`를 사용하지 않았다. | Working tree에서 interpreter 선택은 보정했지만 현재 CI는 online Ubuntu/uv/EOF smoke일 뿐 RHEL·offline·MCP handshake·KLayout smoke가 아니다. |

### 이 명확화 작업에서 선반영한 항목

위 표는 검토 대상 commit의 기준선이다. 현재 working tree에는 production 기능으로 오인하기 쉬운
표현을 먼저 고쳤다. Canonical capability 문서와 `server_status`/tool description은 stock 구현과 목표
계약을 분리하고, model harness는 proxy trace smoke임을 report schema에 명시한다. csh launcher는
`KLAYOUT_MCP_PYTHON` → checkout `.venv/bin/python` → `python3` 순으로 interpreter를 선택하고
Python/dependency preflight를 수행한다. CI의 csh job도 uv venv 경로를 사용한다.

이 조치는 폐쇄망 RHEL qualification이나 production E2E를 완성한 것이 아니다. 이 working tree의
검증 결과는 Windows/Python 3.13.5/KLayout 0.30.10에서 `702 passed, 1 warning`이며, 동일 SHA의
remote CI가 아니므로 release evidence로 사용하지 않는다. Pad/corpus/mesh/onboarding infrastructure는
아래와 같이 구현됐고, 실제 process-specific generator와 foundry pilot은 외부 입력이 필요하다.

## 3. 목표 구조

고수준 persistent facade를 유일한 사용자 E2E로 만들고, 현재 Phase 1 모듈은 그 내부 engine으로
수렴시킨다. LLM이 primitive, PCellizer, layout composer를 임의로 이어 붙이는 경로는 production
surface에서 제거한다.

```text
trusted host configuration
  ├─ approval verifier / process capability provider
  ├─ guided preflight / actionable correction contract
  ├─ labeled DUT corpus onboarding / clarification gate
  ├─ versioned TechnologyAdapterPackage registry + scorecards
  ├─ immutable pad-macro import / edge-terminal contract
  ├─ planning/generation engine registry
  └─ DRC/LVS/PEX runner + signoff policy
                         │
                         v
teg_intake(validate-only/draft) -> teg_plan -> immutable artifacts -> bounded mesh routing
                         │                       │
                         └──────────> pad-macro-preserving overlay composition
                                                   │
                                                   v
teg_generate -> fresh reload/semantic verification -> teg_verify -> external evidence
```

모든 handoff는 path가 아니라 content-addressed artifact와 provenance로 결속한다.

| Artifact | 최소 필드 |
|---|---|
| `ProcessAdapterDescriptor` | process/version/capability hash, adapter id/version/hash, supported device/parameter/rule set |
| `DutCorpusArtifact` | source layout/cell hashes, DUT occurrence IDs/transforms, DUT별 parameter row, terminal/layer roles, normalization anchors, coverage matrix와 corpus fingerprint |
| `CorpusPartitionManifest` | train/reference/sealed-holdout DUT ID와 hash, topology/parameter coverage, split policy/seed, candidate fitting 전에 holdout을 봉인한 증거 |
| `DrawingStyleProfile` | process/device/topology/observed-parameter scope, invariant geometry relations, tolerance, supporting DUT IDs, outliers, confidence가 아닌 evidence coverage, user-approved application policy |
| `CorpusResolutionManifest` | unexplained variation/ambiguity ID, affected DUTs와 geometry diff, candidate reference DUT/policy, 사용자 선택·근거·timestamp, unresolved blockers |
| `ConformanceScoringPolicy` | cohort별 dimension/metric/tolerance/threshold, hard-fail/required 여부, missing-data/aggregation policy, 승인자와 exact policy hash |
| `AdapterConformanceScorecard` | package/corpus/partition/policy/scorer hash, reference/approved-style/sealed-holdout별 raw metric·delta·witness·상태, per-DUT/layer/semantic-group 결과와 aggregate |
| `TechnologyAdapterPackage` | exact technology/PDK/layermap/DBU/grid/device/topology와 supported parameter domain, corpus/partition/recipe/style/resolution/scoring-policy/terminal-mapping/compiler-code hash를 담은 immutable payload |
| `TechnologyAdapterRegistrySnapshot` | exact lookup key→package/scorecard hash, snapshot hash/version/signature, append-only lifecycle/revocation/qualification receipts와 ambiguity 검사 결과 |
| `PrimitiveArtifact` | process/version/capability와 layermap hash, source kind/hash, parameter schema/values/constraints, corpus/dependency-recipe/Drawing-Style/resolution-manifest hash, topology regime, DBU/grid, hierarchy/geometry fingerprint, layer roles, G/D/S/B port polygons와 terminal-stack evidence |
| `PadMacroArtifact` | source file/cell hash, recursive source-cell fingerprint, asserted common DBU, exact 40 µm × 40 µm local bbox, access-metal `(layer, datatype)`와 eligible edge/landing segment, pad ID→instance transform, explicit `extra_keepout=none` |
| `RouteArtifact` | process/version/capability와 layermap hash, source router polyline, straight-segment corridors, DBU/grid, net graph, final mesh boxes/envelopes, transition, bend joint, landing/hole 및 rule-coverage evidence, solver version/budget/status/fingerprint |
| `VerificationEvidence` | qualification dimension/value, kind, layout hash, engine/deck/runset/version/corner/invocation hash, result와 disposition |
| `ActionableIssue` | stable code/category/severity/stage와 human message, RFC 6901 field path, DUT/cell/net/pad/segment identity, safe received value, structured expected constraint, reason, fix/example, 관련 artifact hash |
| `ValidationReport` / `ClarificationRequest` | human summary, request/draft revision, deterministic `issues[]`와 `questions[]`, total/truncation, next action/retry stage, mutation safety state와 resume token |

`geometry_source` 같은 enum만으로 adapter readiness를 주장하지 않는다. 실제 adapter identity와
artifact hash가 process capability 및 workflow manifest에 포함되어야 한다.

### 공통 사용자 경험과 actionable error 계약

알려진 입력·호환성·검증 실패를 `INVALID_INPUT`, `validation failed` 또는 raw exception 한 줄로 끝내는
동작은 결함으로 취급한다. Public MCP tool과 persistent workflow의 모든 단계는 같은
`ValidationReport` 계약을 사용한다.

1. 최초 입력은 `validate_only` preflight로 검사한다. GDS/OAS에서 안전하게 읽을 수 있는 cell,
   occurrence, transform과 layer inventory는 draft manifest에 미리 채우고, 사용자가 제공해야 하는
   parameter/terminal/technology 정보만 빈 field와 설명이 있는 JSON/CSV template로 돌려준다.
2. 응답 첫 부분은 "어느 단계에서 blocker 몇 건이 발견되어 아직 파일을 만들지 않았다"처럼 짧게
   요약한다. 상세 `issues[]`에는 stable `code`, `severity`, `stage`, RFC 6901 `field_path`, 해당
   `dut_id`/cell/occurrence/net/pad/segment, 입력값과 type, 기대 type·unit·range·enum·grid·hash, 실패 이유,
   구체적인 수정 방법과 유효 입력 예시를 넣는다.
3. 독립적으로 확인 가능한 오류는 fail-fast로 하나씩 숨기지 않고 한 번에 모아 deterministic order로
   반환한다. MCP context에는 정해진 개수만 보여주되 `total_issue_count`와 truncation 여부 및 전체
   content-addressed report handle을 제공한다. Warning은 blocker와 분리한다.
4. 사용자의 선택이 필요한 경우 stable `question_id`, 쉬운 문장의 질문, 필요한 이유, 허용 option과
   각 option의 영향, answer schema를 제공한다. PDK 값이나 사용자의 의도를 시스템이 발명해
   `suggested_fix`에 넣지 않는다.
5. 수정은 source artifact를 덮어쓰지 않고 같은 `draft_id`의 새 immutable revision 또는 명시적 JSON
   patch로 적용한다. 유효했던 입력과 이미 답한 질문은 보존하고, 영향받은 validator만 다시 실행한 뒤
   중단된 stage에서 재개한다.
6. Non-secret scalar와 offending collection item은 실제 값을 보여준다. Credential/license/token과 대형
   proprietary geometry는 `redacted=true`, type/length/hash와 정확한 field/index만 남긴다. Raw stack trace,
   local secret path 또는 GDS 전체 shape dump는 사용자 응답에 노출하지 않는다.
7. 오류 category는 최소 `schema`, `semantic`, `coverage_or_identifiability`, `decision_required`,
   `adapter_compatibility`, `execution_environment`, `verification_gate`로 구분한다. 예시는
   `INPUT_FIELD_MISSING`, `INPUT_VALUE_OUT_OF_RANGE`, `INPUT_UNIT_AMBIGUOUS`, `DUT_ID_NOT_FOUND`,
   `TECH_ADAPTER_COMPATIBILITY_MISMATCH`, `TECH_ADAPTER_VERSION_STALE`,
   `REFERENCE_SCORE_BELOW_GATE`다. 예상하지 못한 결함만 `INTERNAL_ERROR`와 incident ID, 안전한 재시도
   방법으로 반환하며 사용자 입력 오류로 위장하지 않는다.
8. 모든 failure report는 `source_modified`, `stage_appended`, `geometry_generation_started`,
   `final_output_promoted` 상태와 다음에 호출할 tool/stage를 명시한다. Input/preflight blocker에서는 네 값이
   모두 false여야 한다. 생성 이후 verification failure는 실제 상태를 숨기지 않고 true/false를 정확히
   보고하되 final promotion과 다음 qualification 승격 여부를 별도로 표시한다.

## 4. 구현 단계

### M0 — 신뢰 기준선과 안전 경계 복구 [P0]

기능 개발 전에 현재 CI와 두 종류의 race를 고친다.

작업:

1. `klayout_adapter`의 안내문 테스트를 Windows/POSIX parameterized test로 바꾸고 platform을 주입 가능하게 한다.
2. `evaluate_mcp_model_robustness.py`의 runner metadata가 agy 설치 여부와 무관하게 같은 schema를 반환하도록 한다. 실제 agy 평가는 opt-in integration으로 분리한다.
3. `WorkflowJobStore`의 content-addressed publish를 `(kind, digest)` 단위 idempotent create-only operation으로 바꾼다. 동일 payload는 성공, 다른 payload는 구조화된 collision이어야 한다.
4. 공용 `publish_new_file()` 계층을 만든다. same-directory temp를 완성·fsync한 뒤 OS별 atomic no-clobber primitive로 승격하고, cleanup은 자신이 소유한 temp/reservation만 삭제한다. `worker_drawing.py`는 final path를 절대 cleanup하지 않는다. 초기 보장 범위는 same-host local NTFS/ext4/XFS로 고정하고, NFS/SMB와 multi-host writer는 해당 filesystem integration을 추가하기 전까지 unsupported로 fail-closed한다.
5. 외부에 노출된 output write path inventory를 만들고 generic drawing, Kelvin, assembly, overlay, style 및 persistent promotion을 모두 공용 publish 계층으로 이전해 같은-path race test를 적용한다.
6. conceptual 도구를 명시적 development mode로 격리하고 surface policy/schema를 고정한다. Stock 기본은 실제로 동작하는 drawing surface, expert는 opt-in으로 둔다. Configured host의 functional facade 기본화는 M1에서 한다. Conceptual artifact에는 `artifact_class=conceptual`을 강제하고 production engine은 이를 구조적으로 거부한다.
7. 수동 pass count를 README에서 release 증거로 쓰지 않는다. CI가 commit/OS/Python/KLayout/pass/skip/run URL을 담은 `validation.json`을 생성하도록 한다.
8. 일반 회귀는 `uv sync --frozen --extra dev`와 `uv run --frozen`으로 실행하고, unit/KLayout integration marker를 분리한다. 별도 wheel-install smoke로 실제 package 배포도 검증해 ambient system package에 성공 여부가 좌우되지 않게 한다.
9. output-root doctor가 exclusive create, no-clobber promotion과 durability capability를 검사한다. 정상 오류/경쟁 loser의 owned temp는 즉시 제거하고, process kill로 남은 owner-tagged temp는 startup scavenger가 TTL 이후 회수한다.

현재 working-tree 진행:

- [x] 공용 `publish_new_file()`의 same-directory fsync + atomic create-only hard-link commit을 구현했다.
- [x] Generic Manhattan drawing을 helper로 이전하고 final-path cleanup을 제거했다.
- [x] 8-writer unit race와 실제 KLayout 2-process same-target test에서 정확히 1개 success,
  loser `OUTPUT_ALREADY_EXISTS`, winner fresh reload를 확인했다.
- [x] Kelvin, conceptual assembly, boundary overlay, style JSON, PCellizer source recovery,
  content-addressed workflow document와 persistent final promotion을 create-only helper로 이전하고
  same-target winner 보존/idempotent race test를 추가했다.
- [x] Reference view/confirmation/selection JSON도 동일 content면 idempotent, 다른 content면 conflict로
  끝나는 create-only publication으로 이전했다.
- [x] PCellizer batch/snapshot/reference의 content-addressed **directory** publication을 공용
  create-only 계층으로 이전하고 실제 concurrent same-target test를 추가했다.
- [x] Output-root doctor가 지원 filesystem, file/directory no-clobber primitive를 능동 점검하며,
  `WorkflowJobStore`가 doctor와 owner-tagged staging TTL scavenger를 공식 노출한다. Scavenger는
  예약 형식과 root containment를 만족하는 오래된 entry만 제거한다.
- [x] 외부 writer inventory 결과, 남은 `os.replace()`는 job lock으로 직렬화되는 mutable head와
  아직 외부에 게시되지 않은 unique staging package 내부 생성물에 한정된다.
- [x] Stock 기본 tool mode를 7-tool `drawing`으로 바꾸고 `expert`를 명시적 opt-in으로 만들었다.
- [ ] 현재 NTFS unit/integration은 검증했으나 ext4/XFS, unsupported NFS/SMB 실제 mount,
  process-kill scavenger와 multi-process 반복 검증은 CI/qualification 환경에서 남았다.

완료 기준:

- Unit은 Windows/Ubuntu × Python 3.11/3.13 matrix, KLayout integration은 지정 Linux/Python/KLayout job, csh smoke는 지정 RHEL-compatible image/Python job으로 나눠 같은 SHA에서 모두 green이다.
- KLayout integration에는 의도하지 않은 skip이 없고 compileall은 pytest 성공 여부와 독립적으로 실행된다.
- agy installed/absent 양쪽이 같은 runner metadata key set을 반환하고, Windows/POSIX 안내문 branch가 host OS와 무관한 mock test로 통과한다.
- 같은 SHA로 최소 3회 연속 CI가 green이며 Windows concurrency stress에서 raw `PermissionError`가 없다.
- frozen lock과 wheel-install 경로가 각각 통과하고 실행에 사용한 dependency provenance가 validation evidence에 남는다.
- 같은 digest의 content object를 여러 thread/process가 동시에 publish해도 canonical bytes 하나만 남고 모두 idempotent success한다. 다른 bytes의 digest collision은 구조화된 오류다.
- 동일 job 생성 경쟁은 정확히 1개 success와 나머지 `WORKFLOW_JOB_HEAD_CONFLICT`로 끝나며 Windows long-path stress에서도 raw sharing 오류가 없다.
- 최초에 존재하지 않는 같은 output path에 서로 다른 geometry를 동시에 쓰면 정확히 한 writer만 성공한다. Loser는 `OUTPUT_ALREADY_EXISTS`를 받고 winner의 hash는 보존된다.
- winner GDS/OAS는 fresh reload 가능하고 final hash가 winner temp와 일치한다. Loser/handled-exception 실패 주입, 기존 zero/nonzero 파일, 다중 process 반복에서 기존 bytes 변경, overwrite/delete와 owned-temp leak가 모두 0이다. 강제 종료는 기존 final bytes를 보존하고 stale temp가 owner/age로 식별되어 TTL 후 회수된다.
- 지원 local filesystem 각각에서 위 보장이 통과하고 unsupported filesystem은 doctor에서 mutation 전에 거부된다.
- stock 기본 tool list와 고정된 production-profile surface contract에 `generate_dut_geometry`, conceptual PCell export/assembly 및 PCellizer draft가 없다.

주요 파일: `.github/workflows/ci.yml`, `manhattan_drawing.py`, `drawing_service.py`,
`worker_drawing.py`, `workflow_store.py`, `klayout_adapter.py`, `server.py`,
`tests/test_manhattan_drawing.py`, 관련 concurrency tests와 신규 atomic-output helper.

### M1 — Host factory와 공통 artifact 계약 [P0]

`server.py`의 전역 singleton과 hard-coded stock facade를 app factory로 분리한다.

작업:

1. `HostComponents` 계약을 만들고 approval verifier, live process provider, immutable technology-adapter
   registry, pad-macro/engine/evidence registry와 signoff policy를 명시적으로 주입한다.
2. host-controlled `deployment.toml`과 별도 production entrypoint를 제공한다. 설치된 adapter entry point 중 config allowlist에 포함된 ID만 로드하며 MCP 입력으로 module/import path를 받지 않는다.
3. secrets, license token 및 credentials는 config와 workflow artifact에 기록하지 않는다.
4. startup `doctor`가 profile별 `intake/plan/generate/DRC/LVS/PEX` readiness, adapter/deck/version, output root를 mutation 전에 검사한다.
5. `server_status`는 hard-coded false 대신 profile×stage readiness matrix와 blocker를 보고한다.
6. Phase 1 planner/composer를 profile engine 구현으로 등록하고 low-level tools는 expert/debug surface에만 남긴다.
7. `TechnologyAdapterRegistry`는 exact technology/PDK revision, adapter kind, device family, topology와
   explicit package version/hash로만 resolve한다. Snapshot은 append-only/content-addressed로 만들고
   revocation/deprecation도 기존 entry mutation이 아닌 새 signed lifecycle record로 기록한다.
8. Plan/job/`PrimitiveArtifact`가 exact registry snapshot과 package entry hash를 pin하도록 한다. Missing,
   ambiguous, revoked, version/layermap/schema/code hash drift에서는 후보와 차이를 설명하고 mutation 전에
   중단한다. Wildcard, process alias, semver-compatible 또는 nearest-version fallback은 사용하지 않는다.
9. 공통 `ActionableIssue` validator와 `ValidationReport`/`ClarificationRequest` serializer를 만들고 schema,
   cross-field semantic, corpus, adapter, pad, route, environment와 verification 오류에 적용한다. 가능한
   독립 문제는 한 번에 모으고 deterministic ordering/deduplication/redaction을 보장한다.
10. Intake를 immutable `draft_id`/revision으로 저장하고 `validate_only → suggested patch 또는 답변 →
    revalidate → 같은 stage에서 resume`를 지원한다. Public boundary는 알려진 domain error를 공통 report로
    변환하고 예상하지 못한 fault만 raw traceback 없이 `INTERNAL_ERROR`+incident ID로 반환한다.

현재 working-tree 진행:

- [x] `HostComponents`, allowlisted stable component ID 기반 TOML loader와 profile×stage `host_doctor`를 구현했다.
- [x] Exact-key immutable `TechnologyAdapterRegistry`, content-addressed package/snapshot과 append-only
  qualification/deprecation/revocation record를 구현하고 intake/privileged reverify에 package/snapshot pin을 결속했다.
- [x] `ActionableIssue`/`ValidationReport`와 immutable draft revision, stale-revision conflict,
  content-bound resume token, no-write `validate_only`를 구현했다.
- [x] External report parser와 별개인 host-only DRC/LVS/PEX runner registry를 추가했다. Runner는
  executable/license/deck/runset identity, out-of-process/timeout/resource-limit readiness를 preflight하고
  report를 exact layout/deck/invocation hash에 결속해야 한다. Stock runner는 없다.
- [ ] 실제 production verifier/provider/transistor engine/runner package와 deployment config는 조직 입력이다.
  따라서 configured production stdio E2E는 M5 입력이 제공될 때까지 실행할 수 없다.

완료 기준:

- stock checkout은 지금처럼 approval과 production action을 fail-closed한다.
- 별도 사용자 Python host 코드를 작성하지 않고 설치된 adapter package와 deployment config만으로 stdio server가 구성된다.
- configured production host의 기본 surface는 functional persistent facade이며 raw/conceptual surface는 명시적 debug opt-in 없이는 노출되지 않는다.
- `production_mode=false`인 test-only host에서 synthetic integration components를 사용한 `teg_intake → plan → generate → verify`가 실제 MCP transport로 동작한다. Production factory는 mock/test/stub component를 거부한다.
- 각 단계 직후 process를 종료·재시작해도 exact job을 resume한다.
- adapter 누락, approval expiry/revocation과 capability drift는 해당 privileged action의 stage append/output promotion 전에 구조화된 오류로 끝난다. 생성 후에만 알 수 있는 output/deck/report drift는 검증 결과를 승격하거나 다음 stage를 append하기 전에 차단한다.
- Registry resolution은 exact compatible active entry 하나만 선택한다. 0개 또는 여러 개인 경우 사용 가능한
  package/version/status/scorecard와 incompatibility field를 보여주고 명시적 선택이나 onboarding을
  요청한다. Restart 후에도 같은 snapshot/entry hash가 유지되고 `server_status`가 resolved identity와
  blocker를 보고한다.
- Missing/type/unit/range/enum/grid/cross-field/hash/version 오류 fixture는 모두 field path, 해당 object ID,
  safe received value, expected constraint, reason, fix/example와 필요한 질문을 반환한다. 여러 오류가 있는
  manifest는 한 번의 preflight에서 가능한 문제를 함께 보여준다.
- Known validation failure에서 generic-only message, raw `ValueError`/`KeyError`/traceback, state append와
  output write가 각각 0건이다. 올바른 field만 수정하면 기존 draft의 유효 입력과 답변을 보존한 채
  재검사·재개된다.

주요 파일: `server.py`, `workflow_store.py`, `approval.py`, `external_evidence.py`,
`process_capability.py`, `mcp_protocol.py`, `pyproject.toml`, 신규 host factory/config/doctor,
technology-adapter registry, actionable-error/validation-report 모듈.

### M2 — Immutable Pad macro overlay와 edge terminal [P0, short path]

Phase 1의 `frame_width/pad_count` 기반 Pad 재합성을 제거한다. Pilot에서는 Pad 내부 구조를 해석하거나
다시 만들지 않고, 승인된 Pad GDS cell 전체를 black-box macro로 보존한다.

입력 계약:

- Source GDS/OAS와 exact pad cell name, source hash.
- Exact local pad bbox 40 µm × 40 µm.
- Output/process와 동일한 DBU. 다르면 변환하지 않고 입력 오류로 중단한다.
- Pad ID별 placement transform과 numbering.
- Routing이 닿을 access-metal `(layer, datatype)`, 허용 edge/side와 landing depth.
- 추가 keepout은 없으며, target이 아닌 Pad instance의 bbox 자체만 routing obstacle로 사용한다.
- Pad macro의 내부 metal/via/passivation은 source owner가 prequalified한 것으로 취급하고 자동 추론하지 않는다.

작업:

1. Source file과 pad cell subtree를 snapshot/hash하고 recursive bbox와 지정 access-metal `Region`을 읽는다.
2. Access-metal이 선언된 pad bbox/edge에 실제로 닿는지 확인하고, edge와 겹치는 positive-area landing
   polygon을 만든다. Side가 `nearest_to_dut`이면 instance transform 후 DUT에 가장 가까운 유효 edge를 고른다.
3. Source pad cell tree를 한 번 import하되 shape/cell을 수정하지 않는다. 새 top에는 explicit pad ID와
   transform으로 macro instance를 놓고 generated DUT/route는 별도 namespace의 overlay cell에 둔다.
4. Router endpoint에는 pad center가 아니라 transform된 landing polygon을 전달한다. Target이 아닌 Pad
   bbox/geometry와의 충돌은 검사한다.
5. `_add_pad_mesh()`는 synthetic fixture 전용으로 격리하고 production Pad 경로에서는 호출하지 않는다.
6. `PadMacroArtifact`에는 source/cell fingerprint, common DBU assertion, local bbox, access layer/edge,
   landing polygon과 pad ID→transform만 고정한다.
7. DBU/bbox/access layer/edge/Pad ID/transform preflight 실패는 공통 `ValidationReport`로 반환한다. 각
   issue는 `/pad_macro/...` field path, Pad ID, source artifact hash, received/expected DBU·layer·edge와
   수정 가능한 manifest 예시 또는 source owner에게 확인할 질문을 포함한다.

현재 working-tree 진행:

- [x] Source stream/cell/hash, 40×40 bbox, common DBU, access layer/edge와 explicit Pad transform을
  content-addressed `PadMacroArtifact`로 등록한다.
- [x] 새 top에서 source Pad cell을 instance로만 배치하고 DUT/routing은 별도 overlay geometry로 생성한다.
  실제 KLayout fresh reload에서 source subtree recursive fingerprint 불변을 검사한다.
- [x] 잘못된 bbox/DBU/access edge와 Pad edit 요청은 output 전에 fail-closed한다.
- [ ] 이 artifact의 landing polygon을 persistent transistor engine/router endpoint로 소비하는 연결은 M5의
  실제 Pad/DUT corpus가 필요하다. Legacy Phase 1의 synthetic Pad 경로는 의도적으로 그대로 분리돼 있다.

완료 기준:

- Fresh reload 후 source pad cell의 recursive geometry fingerprint/XOR가 input과 같고 input bytes가 불변이다.
- Pad instance 수, ID, transform과 numbering이 explicit manifest와 일치하며 좌표를 frame/pad count에서
  재계산한 항목이 0이다.
- 지정 access-metal의 landing polygon이 기대 edge에 positive-area로 접하고 pad bbox 안에 있다.
- Canonical landing stub이 target Pad에는 접속하고 다른 Pad instance에는 닿지 않는다.
- Source/process/output DBU가 exact equality preflight를 통과한다.
- Via stack, under-metal 또는 passivation을 새로 만들거나 source GDS만 보고 공정 규칙을 추론한 항목이 0이다.
- 잘못된 Pad 입력은 source import/output write 전에 모든 독립 issue와 수정 경로를 보여주며 `invalid input`
  한 줄이나 geometry dump로 끝나는 경우가 0건이다.

주요 파일: 기존 source-layout read/copy 경로, `phase1_routing.py`, `phase1_layout.py`,
`phase1_service.py`, 신규 minimal pad-macro artifact/import integration과 fixtures.

### M3 — Example-driven multi-parameter transistor adapter onboarding [P0]

첫 pilot은 한 공정의 한 transistor family와 사용자가 제공한 labeled DUT corpus 하나만 지원한다.
현재 PCellizer의 production 개념은 폐기한다. Hierarchy inventory/snapshot은 corpus reader로 재사용할 수
있지만 `single-box variant generator`나 그 recipe를 transistor backend로 승격하지 않는다.

```text
example layout + DUT parameter manifest
  → normalize / coverage / identifiability
  → parameter dependency + Drawing Style candidates
  → unexplained or outlier differences
  → user clarification and reference-DUT/policy selection
  → immutable resolution manifest
  → recipe materialization
  → holdout regeneration / semantic comparison
  → reference/style/holdout scorecard
  → immutable TechnologyAdapterPackage registry
```

#### M3a — Corpus intake와 identifiability gate

입력:

- 여러 DUT cell/instance가 포함된 source GDS/OAS와 exact top/cell/occurrence ID.
- DUT별 parameter row. 최소 schema는 `gate_length`, `gate_pitch`, planar `width` 또는 FinFET `nfin`,
  `cell_height`와 선택 공정의 dummy/contact/finger/VT parameter를 포함한다.
- Layer semantic role, G/D/S/B terminal mapping, cell/terminal normalization anchor와 topology regime.
- 사용자 설명이 있으면 nominal/reference DUT, known legacy variation, 측정상 허용된 exception을 함께 받는다.
- 사용자는 처음부터 완전한 manifest를 작성할 필요가 없다. `validate_only`가 layout inventory를 읽어
  candidate DUT/cell/occurrence와 known metadata를 채운 draft JSON/CSV를 만들고, 확인하거나 추가해야 할
  field와 예시를 표시한다. 승인되지 않은 자동 추정값은 production input으로 확정하지 않는다.

작업:

1. Read-only inventory로 candidate DUT/cell/occurrence, transform, layer와 hierarchy를 draft에 채우고,
   missing/duplicate/unknown ID와 필요한 parameter column을 사용자가 바로 수정할 수 있는 template과 함께
   반환한다.
2. 모든 DUT occurrence를 공통 orientation/anchor로 normalize하고 layer/role별 recursive geometry,
   hierarchy, terminal anchor와 parameter row를 `DutCorpusArtifact`로 hash한다. Source는 수정하지 않는다.
3. Exact process/PDK, layermap, DBU/grid, device family, topology regime와 `active_when`으로 corpus를
   먼저 partition한다. 다른 regime의 DUT를 한 dependency/style vote에 섞지 않는다.
4. 동일 source cell/geometry hash의 반복 instance는 기본적으로 한 evidence vote로 deduplicate한다.
   독립 evidence로 셀 경우 voting unit과 근거를 manifest에 명시한다.
5. Parameter coverage matrix를 만들고 각 parameter의 distinct values, topology별 sample 수, 함께 변하는
   parameter와 missing combination을 보고한다.
6. Candidate fitting 전에 train/reference-candidate와 holdout DUT ID를 `CorpusPartitionManifest`로
   확정·hash한다. Holdout geometry/labels은 recipe/style extraction과 reference selection에서 읽을 수 없는
   sealed input으로 두고, split 이후의 교체나 leakage는 새 corpus/version으로만 처리한다.
7. 두 parameter가 항상 함께 변하거나 한 값만 있어 effect를 분리할 수 없으면 추론하지 않는다.
   `DUT_CORPUS_IDENTIFIABILITY_INSUFFICIENT`와 reason=`confounded_parameter_effect`, 원인 column/DUT ID,
   필요한 추가 split DUT 조합을 반환한다. 동일 parameter row가 서로 다른 geometry를 가지는 경우도
   reason=`same_row_geometry_diverged`로 같은 gate에서 중단한다.
8. Terminal/layer/parameter row가 빠졌거나 같은 DUT ID에 값이 충돌하면 각각
   `TERMINAL_MAPPING_MISSING`, `DUT_PARAMETER_MANIFEST_INCOMPLETE`, `DUT_PARAMETER_CONFLICT`로 중단한다.
9. Planar/FinFET, contact topology 또는 cell-height regime가 바뀌는 branch는 별도 corpus scope로 나누고
   예시가 없는 branch는 `TOPOLOGY_REGIME_UNCOVERED`로 둔다.
10. 위 blocker는 공통 issue envelope에 parameter row/column의 exact field path, DUT ID, safe received
    value, expected type/unit/domain, evidence와 이유를 넣는다. 부족한 coverage이면 단순히 "데이터 부족"이라
    하지 않고 식별에 필요한 추가 DUT parameter 조합과 답변 template을 제안한다.

#### M3b — Parameter dependency와 Drawing Style 후보 추출

1. Corpus의 geometry diff를 다음 네 범주로 분리한다.
   - `parameter_correlated`: parameter 변화와 일관되게 함께 움직이는 shape/anchor 후보.
   - `observed_invariant_style`: 관측된 parameter/topology 범위에서 항상 유지된 geometry relation.
   - `style_candidate_with_outliers`: 대부분 유지되지만 일부 DUT에서 다른 relation. 자동 majority 채택 금지.
   - `unexplained_variation`: 같은 parameter row 또는 설명 가능한 effect 밖에서 DUT별로 다른 geometry.
2. Gate pitch 변경에 따른 gate center/pitch, Active/RX extent, contact/via, metal/pin, boundary와 G/D/S/B
   anchor의 연동 후보를 만들고 `resize/translate/replicate/contact-pack/anchor-update` operation DAG로
   표현한다. 다른 parameter도 같은 방식으로 semantic shape group dependency 후보를 만든다.
3. `observed_invariant_style`에는 alignment, enclosure/margin pattern, dummy arrangement, contact placement
   style, pin/label convention, hierarchy reuse 등 parameter가 달라도 유지된 관계를 저장한다.
4. Normalized source shape/group마다 정확히 하나의 classification과 provenance를 남긴다. 분류에서
   사라지거나 중복 귀속된 shape는 blocker다.
5. Exact invariant의 기본 tolerance는 0 DBU다. Nonzero tolerance나 quantization normalization은
   사용자가 명시적으로 승인한 policy가 있을 때만 사용한다.
   Merged `Region` XOR이나 bbox histogram만으로 hierarchy, coincident-shape multiplicity, path/text semantics를
   지우지 않는다. Style descriptor가 관리하는 raw semantic-group fingerprint도 함께 비교한다.
6. Style은 exact process/device/topology와 observed parameter range에만 적용한다. Corpus에서 우연히
   변하지 않은 항목을 design rule이나 범위 밖 invariant로 표시하지 않는다.
7. 모든 dependency/style 후보에는 supporting DUT IDs, counterexample/outlier, tolerance와 coverage를
   붙인다. 이를 확률적 confidence나 전기적 동등성 증거로 표현하지 않는다.
8. 기존 generic `ExtractedLayoutStyleProfile`의 histogram/관측값을 executable DUT constraint로 사용하지
   않는다. Corpus style은 `candidate`와 user-approved `approved` 상태 및 exact hash를 별도로 가진다.

#### M3c — Legacy variation clarification과 사용자 승인

1. `style_candidate_with_outliers`와 `unexplained_variation`은 자동 수정·평균화·majority 선택하지 않는다.
   Geometry diff를 layer/role, bbox/area/count, affected terminal과 DUT ID별로 묶어 사용자에게 보여준다.
2. 각 variation에 다음 resolution 후보를 제시하되 사용자가 직접 선택해야 한다.
   - 특정 reference DUT의 geometry를 신규 DUT에 따른다.
   - Majority/common pattern을 따른다.
   - Topology/parameter regime별 exception을 그대로 유지한다.
   - 사용자가 제공한 explicit 신규 rule/recipe를 따른다.
   - 추가 예시 DUT를 제공할 때까지 unresolved로 둔다.
   Majority 선택은 deduplicate된 voting unit과 quorum을 명시하고, 실제로 관측된 **whole semantic-group
   fingerprint 하나**만 선택한다. Shape/attribute별 투표로 어느 DUT에도 없던 Frankenstein geometry를
   합성하지 않는다. Tie, quorum 미달 또는 parameter/topology와 상관된 minority는 blocker다.
3. 측정에 문제가 없었다는 설명은 `legacy_measurement_accepted` 사용자 attestation으로 기록할 수 있지만,
   DRC-clean, electrical equivalence 또는 universal style 증거로 승격하지 않는다.
4. Reference DUT 또는 policy가 필요한데 선택되지 않으면 `REFERENCE_DUT_SELECTION_REQUIRED` 또는
   `DRAWING_STYLE_OUTLIER_REQUIRES_DISPOSITION`을 반환하고 write tool을 호출하지 않는다.
5. 승인된 parameter dependency, Drawing Style와 variation resolution을 immutable
   `CorpusResolutionManifest`로 저장하고 source corpus/parameter manifest hash에 결속한다. Corpus가
   바뀌거나 답변이 일부 variation만 해결하면 기존 승인을 재사용하지 않고
   `DRAWING_STYLE_POLICY_STALE`/`DRAWING_STYLE_VARIATION_POLICY_REQUIRED`로 다시 질문한다.
6. Reference/style/holdout 비교 dimension, tolerance, threshold, hard-fail, missing-evidence와 aggregation
   규칙을 `ConformanceScoringPolicy`로 작성해 holdout을 열기 전에 승인·hash한다. Holdout 결과를 본 뒤
   threshold를 바꾸면 기존 score를 덮어쓰지 않고 새 policy/package candidate로 다시 평가한다.

질문은 공통 `ClarificationRequest`를 재사용하며 최소 계약은 다음을 포함한다.

- Corpus/parameter/style-candidate hash와 variation ID.
- Layer/semantic group, affected DUT/parameter rows, content-distinct candidate fingerprints와 vote counts.
- Topology/parameter correlation 경고와 legacy geometry at risk.
- 허용 선택지 `reference_dut`, `majority`(electorate/quorum 포함), `topology_exception`, `explicit_rule`,
  `provide_more_examples`.
- `source_modified=false`, `geometry_generation_started=false`, `final_output_promoted=false` 안전 상태.
- 관련 field path와 draft revision, 사용자가 답해야 할 exact question, stable question ID/answer schema와
  추가로 필요한 DUT/parameter 조합. 이미 답한 variation은 영향 hash가 바뀌지 않는 한 다시 묻지 않는다.

#### M3d — Recipe materialization과 holdout 검증

1. 승인된 manifest만 `TransistorPrimitiveAdapter` recipe로 compile한다. Gate length/pitch, width/`nFin`,
   cell height와 추가 parameter가 관련 semantic shape group/terminal anchor를 함께 갱신해야 한다.
2. M3a에서 미리 봉인한 holdout을 제외한 corpus로 recipe/style policy를 확정한다. Compiler와
   reference/style selector가 sealed holdout geometry/label에 접근하지 못했음을 실행 evidence로 남긴다.
3. Recipe와 scoring-policy hash가 잠긴 뒤 holdout을 열어 DUT를 재생성한다. Layer/role semantic XOR,
   raw shape count/multiplicity, dimension, hierarchy, terminal anchor/connectivity와 approved exception을
   원본과 비교한다. Mismatch는 자동 보정하지 않고 actionable diff와 함께 dependency 누락 또는
   unresolved variation으로 M3c에 되돌린다.
4. 신규 DUT의 style-managed group이 비어 있으면 approved style을 추가하고 exact match면 idempotent
   no-op한다. 다른/extra geometry가 있으면 자동 delete/replace/union하지 않고
   `DRAWING_STYLE_TARGET_CONFLICT`로 중단한다.
5. 모든 output shape/group은 `parameter_dependency`, `drawing_style`, `explicit_variation_policy` 중
   provenance와 source evidence까지 역추적 가능해야 한다.
6. Adapter는 parameter/schema, corpus, dependency recipe, Drawing Style와 resolution manifest hash를 가진
   `PrimitiveArtifact`를 반환한다. Conceptual scaffold와 unapproved recipe는 production registry가 거부한다.
7. Source DUT bytes와 recursive fingerprint는 불변이어야 한다. Explicit shape-scoped disposition 없이
   silent drop/merge/flatten/layer remap 또는 off-grid quantization loss를 허용하지 않는다.
8. Shared child cell을 수정해 다른 corpus/target DUT까지 바뀔 수 있으면 copy-on-write로 격리하고
   비대상 occurrence fingerprint 보존을 증명한다. 그렇지 못하면 생성 전에 중단한다.

#### M3e — Tech별 immutable adapter package와 conformance scoring [P0 reuse gate]

1. Approved recipe, `DrawingStyleProfile`, resolution manifest, terminal mapping, supported parameter domain,
   exact technology/PDK/layermap/DBU/grid/device/topology, corpus/partition/scoring-policy와 compiler/code hash를 하나의
   content-addressed `TechnologyAdapterPackage`로 묶는다. 수정은 in-place update가 아니라 새 package
   version/hash 생성으로만 허용한다.
2. Score는 다음 cohort를 합치지 않고 별도 vector로 유지한다.
   - `reference_reproduction`: 선택한 reference/training DUT의 동일 parameter를 재생성했을 때의 재현도.
   - `approved_style_conformance`: 생성 DUT가 승인된 Drawing Style과 legacy variation policy를 지키는 정도.
   - `sealed_holdout_generalization`: fitting/선택에 사용하지 않은 DUT를 처음 열어 재생성한 결과.
3. 각 cohort에서 semantic-group geometry/XOR, shape count와 coincident multiplicity, hierarchy/cell reuse,
   layer/grid, dimension/edge/anchor, G/D/S/B terminal stack·topology·connectivity, parameter dependency 예측과
   approved-style/variation 준수를 측정한다. 결과는 per-DUT/layer/semantic-group의 raw numerator,
   denominator, delta, tolerance, witness/diff artifact와 `pass|fail|insufficient_evidence|not_applicable`를
   보존한다.
4. Required terminal/connectivity, forbidden short, critical layer의 missing/extra geometry, off-grid,
   unresolved ambiguity와 holdout leakage는 hard fail이다. 다른 dimension의 높은 점수나 평균으로 상쇄하지
   않는다. Average만 보지 않고 worst-case/min/p5와 coverage를 기본 aggregate로 제시한다.
5. 0–100 summary는 사용자가 package를 비교하기 위한 dashboard 보조값으로만 계산할 수 있다. Summary가
   hard gate, required dimension 또는 `insufficient_evidence`를 통과시키지 못하며 reference similarity를
   design rule, 전기적 동등성, DRC/LVS 증거로 표현하지 않는다.
6. `AdapterConformanceScorecard`는 scorer/version, package/corpus/partition/policy hash와 exact witness를
   결속한다. 같은 hash 집합은 deterministic result를 내고 어느 하나라도 바뀌면 새 scorecard를 만든다.
   실패 시 code와 함께 failing DUT/layer/group, measured/threshold, diff handle, 원인과 다음 correction을
   공통 `ValidationReport`로 보여준다.
7. Package lifecycle은 `candidate → reviewed → geometry_validated → foundry_validated → deprecated/revoked`로
   관리한다. Package bytes는 불변이고 상태·revocation·qualification 변화는 새 registry snapshot의 signed
   record다. `geometry_validated`는 이 절의 내부 재현 gate, `foundry_validated`는 M5의 exact DRC pilot
   receipt까지 결속된 경우에만 사용한다.
8. 새 job은 technology/PDK/device/topology/parameter-domain compatibility가 exact한 active package를 먼저
   조회한다. 하나뿐이면 version/status/scorecard/domain과 선택 이유를 보여주고 재사용하며, 여러 개면
   차이를 나열해 explicit version을 선택하게 한다. 0개, stale/revoked entry 또는 domain 밖 target이면
   어느 field가 다른지와 추가 onboarding에 필요한 DUT 조합을 알려준다.
9. Registry cache는 검증 우회가 아니다. 재사용 package도 매 job에서 input/domain/hash를 preflight하고
   생성 결과를 fresh reload해 같은 scoring policy의 required dimensions와 terminal/connectivity를 다시
   검사한다.

현재 working-tree 진행:

- [x] 여러 labeled DUT cell과 parameter row, semantic layer/terminal mapping, sealed holdout을 받는 corpus
  artifact와 coverage/identifiability gate를 구현했다.
- [x] Observed invariant style metric과 same-parameter/different-geometry ambiguity를 검출하고, 사용자가
  따를 DUT/policy를 immutable resolution manifest로 선택하도록 했다.
- [x] Reproduced GDS를 실제로 다시 읽어 train/holdout을 분리 scoring하고, 모두 통과한 경우에만
  `candidate_scored_not_foundry_qualified` package를 exact registry에 등록할 수 있다.
- [ ] Corpus로부터 CPP 연계 Poly/Active/contact/implant/terminal dependency recipe를 자동 합성하는
  process-specific compiler는 만들지 않았다. 실제 labeled corpus, topology 규칙과 foundry 검증 없이는
  한 GDS에서 이를 추론하지 않으며 candidate score를 PCell/electrical/foundry 동등성으로 승격하지 않는다.

완료 기준:

- 승인된 21-row DOE corpus 전체의 parameter/geometry coverage matrix와 identifiability report가 생성된다.
- Candidate fitting 전에 corpus partition이 봉인되고 holdout 접근 leakage가 0건이다. Split/policy를 바꾸면
  새 package candidate가 되며 기존 score를 재사용하지 않는다.
- 모든 parameter-correlated dependency와 observed-invariant style에 supporting DUT와 반례/범위가 기록된다.
- Style outlier와 설명되지 않은 DUT 차이 100%가 user-approved resolution 또는 명시적 unresolved blocker를 가진다.
- 신규/holdout DUT는 승인된 dependency와 Drawing Style을 적용하며 선택한 reference DUT/exception policy를
  정확히 재현한다.
- 같은 corpus partition의 21 DOE에서 style-managed group fingerprint는 approved profile과 일치하고,
  parameter-dependent group/anchor만 row manifest와 approved recipe대로 달라진다.
- Holdout semantic comparison과 fresh reload에서 G/D/S/B terminal mapping, unrelated geometry 불변,
  deterministic fingerprint가 통과한다.
- Reference reproduction, approved style와 sealed holdout score vector가 모두 생성되고 threshold provenance와
  failing witness를 재현할 수 있다. Required hard-fail 또는 insufficient-evidence dimension이 하나라도
  있으면 package는 `geometry_validated`가 될 수 없다.
- Exact compatible validated package는 다음 job에서 onboarding 없이 resolve되고 동일 package/snapshot hash가
  plan과 output에 결속된다. Missing/ambiguous/stale/domain-outside package는 mutation 전에 field-level
  correction report로 끝난다.
- 범위 밖 parameter, 미식별 effect, 미승인 outlier 또는 corpus hash drift는 파일 생성 전에 중단한다.
- Missing/duplicate manifest, confounded sweep, same-row divergent geometry, tied majority, duplicate-instance
  vote inflation, topology minority, accidental marker/jog, stale decision와 pre-existing style conflict를
  포함한 negative fixtures가 모두 structured blocker와 no-output으로 끝난다.
- 위 negative fixture의 report는 exact DUT/field/value/expected/reason/fix 또는 필요한 질문을 포함하고,
  generic-only error와 이미 답한 질문의 반복은 0건이다.
- Adapter-local 결과를 LVS/foundry legality로 표현하지 않으며 최종 DRC/LVS acceptance는 M5에서 판정한다.

주요 파일: `phase1_workflow.py`, `process_capability.py`, `primitive_verification.py`,
`phase1_layout.py`, `phase1_service.py`, `server.py`, 신규 corpus artifact/coverage analyzer,
semantic-diff classifier, clarification manifest, corpus partition/sealed-holdout runner, scoring policy/evaluator,
technology-adapter package registry, recipe compiler와 process-specific adapter/worker.

### M4 — Existing router와 mesh compiler 연결 및 21-DUT bounded 검증 [P0]

Mesh geometry를 새로 발명하는 단계가 아니다. Existing router의 polyline과 이미 동작하는
straight-corridor mesh compiler 사이의 adapter를 먼저 완성하고, 그 다음 현재 DFS가 21-DUT corpus에서
bounded time 안에 경로를 내는지 검증한다.

#### M4a — Phase 1 polyline → segment mesh integration [P0, short path]

작업:

1. Router가 반환한 `points_um` polyline을 수평/수직 straight segment로 분해하고 zero-length,
   diagonal, self-intersection을 거부한다.
2. 각 segment에 explicit 또는 obstacle scan으로 계산한 corridor를 붙여 기존
   `synthesize_staged_mesh_segment`/`mesh_routing.py` compiler를 호출한다. Compiler가 실제로 소비하는
   width, space, rail pitch, cross-tie pitch와 corridor만 기록한다.
3. 인접 segment 사이에는 pitch-aligned full-width 90° bend/joint를 만들고, DUT terminal에는 길이가
   제한된 transition, M2 Pad edge에는 multi-rail positive-area landing을 만든다.
4. Phase 1 composer의 장거리 `_route_boxes()` 출력을 compiled mesh operations로 교체한다. Fixed-width
   single box는 명시적으로 허용된 짧은 terminal transition에서만 사용한다.
5. 합쳐진 final mesh `Region`으로 hole 존재, rail/cross-tie 수, joint continuity, target landing,
   non-target Pad bbox/geometry와 obstacle, cross-net spacing을 다시 검사한다. 실패하면 single rail로
   낮추지 말고 `MESH_COMPILE_NOT_FEASIBLE`과 실패 segment/corridor를 반환한다.
6. Existing straight-segment/Kelvin example을 golden fixture로 고정하고, single-segment와 multi-bend
   Phase 1 route가 동일 compiler contract를 사용하는지 regression test한다.
7. `RouteArtifact`에 source polyline, segment/corridor list, compiler/rule inputs, final boxes, joints,
   landings와 fresh-reload topology fingerprint를 저장한다.
8. Polyline/mesh blocker는 connection/net/segment/corridor의 exact field path, received geometry/budget,
   expected constraint, 물리적 불가능인지 compiler 제약인지와 안전한 수정 방법을 공통 issue로 반환한다.

완료 기준:

- 기존 mesh 예제의 rail/cross-tie/hole topology가 유지되고 Phase 1 single-segment route에서도 같은
  compiler 결과를 사용한다.
- 1-bend/2-bend route의 모든 segment가 mesh이며 joint에서 단절·single-rail neck-down·다른 net short가 없다.
- DUT와 Pad landing이 positive-area로 접속되고 target이 아닌 Pad instance에는 닿지 않는다.
- Composer가 장거리 centerline box를 직접 생성한 횟수가 0이고 final geometry fingerprint가 plan과 같다.
- Compiled geometry가 corridor/spacing을 만족하지 못하면 output을 만들지 않고 구조화된 오류로 끝난다.
- 실패 report만 보고도 사용자가 어느 net의 몇 번째 segment와 어떤 width/space/pitch/corridor를 확인해야
  하는지 알 수 있으며 generic `MESH_ERROR` 한 줄로 끝나는 경우가 0건이다.

#### M4b — Existing DFS budget과 실제 21-DUT acceptance [P0]

M2의 Pad edge terminal, M3의 DUT terminal polygon과 M4a glue가 준비된 뒤 시작한다. 먼저 현재
0/1/2-bend, connection당 최대 96-candidate solver를 그대로 측정한다. 새 A*/Lee/rip-up router는 실제
corpus가 현재 solver의 scope로 해결되지 않을 때만 후속 조건부 작업으로 연다.

작업:

1. `RoutingSearchBudget(max_nodes, max_backtracks, deadline_ms, max_connections)`과 cancellation을 현재
   DFS에 추가한다. Node cap을 1차 제한, wall-clock을 emergency limit로 사용한다.
2. `search_status={route_found, scope_exhausted, budget_exhausted, execution_deadline_exceeded}`와
   `route_found/search_complete/physical_infeasibility_proven`을 분리한다. Budget 종료를 물리적 불가능으로
   해석하지 않는다.
3. 승인된 21-DUT/Pad mapping의 84 connection을 current solver에 입력하고, target Pad edge/DUT port,
   DUT obstacle와 non-target Pad bbox를 사용한다. 별도 extra keepout ontology를 도입하지 않는다.
4. 각 returned polyline을 M4a에서 mesh로 compile한 뒤 final boxes 기준으로 cross-net spacing과 short를
   검사한다. Mesh compile이 실패하면 다른 기존 candidate/waypoint를 시도한다.
5. Route/search fingerprint에 solver version, budget, selected polyline, segment corridors와 compiled mesh
   fingerprint를 포함하고 composer는 이 operation만 소비한다.
6. Pad macro, process, primitive와 route의 DBU/layermap equality를 mutation 전에 assertion한다. 이 pilot에서
   DBU 변환 계층은 만들지 않는다.
7. Current solver가 대표 corpus를 budget 안에서 풀지 못할 때만 bounded rectilinear A*/Lee 또는 rip-up,
   spatial index 같은 확장을 별도 설계·승인한다.
8. Search failure는 connection/net, candidate/segment, 소비한 node/backtrack/time과 configured budget을
   report하고 `scope_exhausted`, `budget_exhausted`, `execution_deadline_exceeded`별 다음 조치를 구분한다.

완료 기준:

- 25-pad/21-DUT 대표 fixture의 84 connection과 shared-net mapping이 budget 안에 종료하고 short가 없다.
- 정상 corpus 30회 cold run의 p95가 10초 이하이며 hard deadline 30초 전에 구조화된 status를 반환하고
  MCP server hang이 없다.
- 모든 returned polyline이 M4a를 통해 hole-bearing mesh로 compile되고 bend/joint/DUT/Pad landing 검증을
  통과한다. Narrow corridor는 silent single rail 대신 `MESH_COMPILE_NOT_FEASIBLE`로 끝난다.
- Source/process/output DBU와 layermap equality가 확인되고 final fresh-reload fingerprint가 plan과 같다.
- Budget 종료와 physical infeasibility를 혼동하지 않고, 각 실패는 exact object/received/expected/reason과
  retry option을 가진 actionable report로 mutation 없이 끝난다.
- 위 결과는 supplied width/space/pitch/corridor contract의 내부 검증이며 foundry legality는 M5 DRC에서만
  주장한다.

주요 파일: `routing_feasibility.py`, `phase1_routing.py`, `mesh_routing.py`,
`phase1_layout.py`, `phase1_service.py`, `design_contract.py`와 21-site corpus.

현재 working-tree 진행:

- [x] Phase 1 polyline을 segment별 최소 2-rail mesh로 compile하고 bend/terminal tie, final overlap과
  mesh evidence를 composer/fresh-reload 결과에 연결했다. 장거리 single-rail fallback은 없다.
- [x] DFS에 global node/wall-time budget과 명시적 `budget_exhausted` termination을 추가했다.
- [x] 21 DUT×4 terminal에 해당하는 84개의 synthetic parallel connection stress가 bounded node 안에서
  종료하는 회귀를 추가했다.
- [ ] Supplied PAD40 edge와 corpus-derived DUT ports를 쓰는 실제 84-connection fixture, 30 cold-run p95와
  final foundry spacing/short acceptance는 M2/M3 입력 및 M5 runner가 있어야 완료된다.

### M5 — 실제 persistent E2E와 foundry pilot [P0 release gate]

M2, M3, M4b를 M1의 host engine 뒤에서 하나의 job으로 연결한다.

작업:

1. 승인된 21-site DOE, terminal→net→pad mapping, bias와 inactive shared-pad policy를 DesignIntent에 고정한다.
2. 실제 transistor adapter의 registry snapshot/package, corpus/partition/dependency/`DrawingStyleProfile`/
   variation-resolution/scoring-policy/scorecard hash, PAD40 macro artifact와 mesh router의 exact hash를
   plan/manifest/generation result에 전파한다.
3. Process/layermap/grid 및 모든 source/artifact/output DBU의 exact equality를 generation 직전에
   재검사한다. Output을 fresh reload해 hierarchy, layer set, immutable PAD40 subtree fingerprint/XOR,
   primitive fingerprints, route mesh topology, terminal connectivity와 short를 다시 계산하고, M3e와
   같은 scorer/policy로 final primitive의 required conformance dimensions를 재평가한다.
4. foundry DRC runner는 executable/license/deck/runset/version preflight 후 별도 process로 실행하고 timeout/resource limit를 적용한다.
5. parsed report는 exact layout/deck/invocation hash에 묶는다. Waiver가 있다면 marker별 rule/location, rationale, 승인 authority, timestamp와 disposition receipt를 개별 기록한다.
6. LVS를 사용할 수 있으면 expected device/pin netlist와 일치시킨다. PEX는 route-R/terminal-C error budget과 측정 준비도를 평가할 때 추가한다.
7. 시작, plan 직후, staging 직후, final promotion 직후의 crash/restart fixture를 실제 stdio transport로 검증한다.
8. Internal conformance score 통과를 DRC/LVS 통과로 취급하지 않는다. Exact package와 final GDS가 DRC
   policy gate를 통과한 뒤에만 해당 package hash의 새 registry snapshot에 `foundry_validated` receipt를
   append한다.
9. Adapter/package drift, final score, DRC/LVS/report 실패는 failed dimension/rule/object, measured result와
   threshold, 관련 report/diff handle, retry 가능 여부를 공통 `ValidationReport`로 반환한다. Proprietary
   deck 원문이나 raw stack trace는 노출하지 않는다.

현재 working-tree 진행과 외부 차단점:

- [x] Persistent manifest/evidence normalization/signoff-policy 결속과 host-only external runner 실행 계약은
  구현됐다. 실행 report만으로 signoff나 `production_ready`가 되지 않는다.
- [ ] 실제 21-site DesignIntent, qualified transistor adapter, PAD40 source artifact, approved process
  provider/engine/verifier, DRC/LVS/PEX executable·license·deck·runset과 조직 signoff policy가 제공되지 않았다.
  따라서 foundry pilot receipt나 `foundry_validated` lifecycle record는 발행하지 않는다.

완료 기준:

- clean host에서 config와 approved inputs만으로 `teg_intake → teg_plan → teg_generate → teg_verify → external evidence`를 완주한다.
- Final GDS는 source PAD40 macro cell을 수정 없이 instance로 보존하고 실제 transistor 21개와 bounded mesh routing을 포함한다.
- fresh-reload/internal verification이 성공한 직후, external DRC 실행 전 exact GDS hash에 `qualification.layout=target_process_geometry_pilot_complete` evidence를 발행한다.
- violation이 0인 경로만 `drc_clean=true`다. 승인 disposition 경로는 `drc_clean=false`, `drc_accepted_with_dispositions=true`를 유지하고 모든 marker의 exact receipt와 `qualification_path`를 기록한다.
- 지원되는 LVS가 mismatch 0이다. LVS가 없으면 결과는 geometry/DRC pilot로만 표시한다.
- approval revoke/expiry, PDK/deck drift, stale pad/primitive hash, DRC timeout 또는 report mismatch가 fail-closed한다.
- DRC policy gate 성공 후 같은 GDS hash에 `qualification.layout=foundry_layout_pilot_passed`를 발행한다. 조직 release gate 전 `qualification.release.production_ready=false`다.
- Plan부터 final evidence까지 registry snapshot/package, corpus partition, scoring policy/scorecard hash가
  동일하고 final 재평가가 통과한다. Internal score만으로 `foundry_validated`가 된 package는 0건이다.
- Persistent E2E의 known failure는 같은 draft/job에서 correction 가능한 report를 남기고 validation 단계
  오류가 final promotion이나 다음 stage append를 일으킨 경우가 0건이다.

### M6 — Gemma4 및 폐쇄망 RHEL 운용 검증 [P1 deployment qualification gate]

#### 제한 모델

1. Production 사용자는 고수준 `persistent-ready` surface만 사용한다. `phase1-pilot-debug`, `reference`, `pcellizer-draft`는 명시적 nonproduction/debug profile로 격리하고 production surface에는 raw composer나 conceptual tool을 두지 않는다.
2. artifact handle을 사용해 반복되는 nested payload를 줄이고 mode별 schema budget을 CI에 고정한다.
3. exact Gemma4 model/version/digest, context, quantization, decoding/seed를 기록하는 multi-turn matrix runner를 만든다. Self-hosted면 immutable image/model digest를, hosted면 provider revision과 제공되지 않는 필드를 명시한다. Proxy 결과와 target 결과는 별도 report로 보관한다.
4. Happy path뿐 아니라 invalid/missing technology version, ambiguous/stale adapter, missing/unitless/off-grid
   DUT parameter, unresolved style choice, reference/holdout score mismatch, router exhausted, restart/resume,
   DRC failure와 unsafe-write 시나리오를 반복한다.
5. Write-tool guard는 수동 목록이 아니라 live `tools/list.annotations.readOnlyHint`에서 만들고 schema drift를 fail-closed한다. Completed MCP result의 `isError/status/code`, final-answer semantic rubric, 실제 permission allowlist와 non-MCP write 관측을 채점한다.
6. 오류 UX rubric은 모델이 정확한 DUT/field/value/expected/reason을 짧게 설명하고, report에 있는 수정법이나
   필요한 질문만 제시하며, PDK 값을 발명하지 않고 같은 draft resume tool을 선택하는지 평가한다.

현재 working-tree 진행:

- [x] Stock 기본 mode를 `drawing`으로 바꾸고 `expert`는 opt-in으로 제한했다. `drawing` 7개,
  `facade` 7개, `onboarding` 9개 tool이며 compact sorted JSON 기준 각 task surface의
  `tools/list + common instruction` 30,000자 이하를 CI test로 고정했다.
- [x] Proxy harness는 expert opt-in임을 metadata에 기록하고 live annotation에서 write-tool set drift를
  fail-closed한다. Proxy와 exact Gemma4 동등성은 주장하지 않는다.
- [ ] Exact Gemma4 runtime/model digest와 30/100회 반복 trial, final-answer/permission/non-MCP-write rubric은
  해당 runtime이 제공되지 않아 실행하지 않았다.

완료 기준:

- task mode당 tools는 10개 이하이고 `tools/list + mode instruction` 합계는 serialized 30,000자 이하이면서 exact tokenizer 기준 context의 25% 이하다. Instruction에 미노출 tool 언급은 0개다.
- exact Gemma4 일반 시나리오를 각 30 valid trials 실행해 각각 29/30 이상, 전체 성공률 95% 이상을 얻는다. 객관적으로 식별된 infra/provider failure만 scenario당 최대 3회 재시행하며 invalid count도 보고한다. Model inference/tool-decision timeout은 task failure에 포함한다.
- unsafe write 및 invented approval corpus 100회에서 관측 위반이 0건이다. 이를 절대 안전의 증명으로 표현하지 않고 0/100의 Wilson upper bound를 함께 보고한다.
- Known-invalid corpus 전부에서 generic-only 설명, 필수 issue field 누락, 이미 답한 질문의 반복, 승인된
  constraint 밖 suggested value 발명과 오류 후 write 시도가 각각 0건이다. 올바른 correction 뒤에는 같은
  draft/artifact hash chain으로 재개한다.
- model/server/artifact revision, raw tool calls와 scenario별 집계/Wilson interval이 재현 가능한 report에 남는다.
- 이 결과 전에는 문서에 `Gemma4 verified`를 표시하지 않는다.
- 이 절의 통과는 `qualification.model=exact_gemma4_qualified`만 발행한다.

#### 폐쇄망 RHEL/csh

1. 지원 RHEL major/minor, CPU, Python, csh/tcsh, KLayout 배포본의 exact allowlist와 검증 버전을 명시한다.
2. launcher는 `KLAYOUT_MCP_PYTHON`이 설정되면 그 interpreter만 검사하고 실패 시 즉시 종료한다. 설정이 없고 repository `.venv/bin/python`이 존재하면 그것만 검사하며, 둘 다 없을 때만 allowlisted system `python3`를 사용한다. 불량 explicit/venv interpreter에서 조용히 fallback하지 않는다.
3. 배포 방식은 `pinned base image prerequisites + verified offline application bundle`로 고정한다. Base image에는 exact RHEL/tcsh/OS libraries와 검증된 signed KLayout RPM을 두고, bundle에는 `uv.lock` 기준 project/dependency wheels, adapter/deck payload, hashes, signatures, licenses와 SBOM을 포함한다. Runtime에서 uv와 network를 요구하지 않는다.
4. doctor는 선택 profile의 exact Python/KLayout allowlist, `mcp`/PyYAML/package version, executable/권한, output filesystem capability를 검사한다. Production profile만 adapter/deck/license/approval backend를 필수로 하고 일반 drawing smoke는 해당 누락을 readiness blocker로 보고하되 시작은 허용한다.
5. 허용된 base image와 bundle을 staging한 뒤 network를 차단한다. 설치 전에 모든 RPM/wheel/payload hash와 signature를 검증하고, 이후 `--no-index` install, csh launch, MCP initialize/tools/list/status와 KLayout read/write smoke를 수행한다.

현재 working-tree 진행:

- [x] csh launcher는 explicit `KLAYOUT_MCP_PYTHON` → repository `.venv/bin/python` → system `python3`
  순서로만 선택하고, 선택 interpreter의 Python 3.11+/`mcp`/PyYAML/package import 실패를 stderr+nonzero로
  종료한다. `uv`는 runtime dependency가 아니다.
- [x] CI에 csh와 repository uv environment smoke를 추가했다.
- [ ] Exact RHEL image, signed KLayout RPM, offline wheel/payload bundle, SBOM과 network-disabled install
  qualification 입력이 없어 runtime qualification은 발행하지 않는다.

완료 기준:

- system Python에 project dependency가 없어도 launcher가 선택한 venv로 정상 시작한다.
- 고정 base-image prerequisites와 검증된 offline bundle만으로 설치와 최소 한 개의 KLayout-backed read/write가 성공한다. Bundle/RPM/wheel 변조는 설치 전에 거부된다.
- 성공과 실패 모두 launcher 진단이 MCP protocol stdout을 전혀 오염시키지 않는다. Python/KLayout/adapter/deck 누락과 버전 미달은 stderr와 nonzero exit로 설명된다.
- CI csh job은 현재의 online Ubuntu `uv sync` + EOF smoke를 넘어 실제 offline deployment 경로와
  MCP initialize/tools/list/status 및 KLayout read/write를 시험한다.
- 이 절의 통과는 `qualification.runtime=rhel_csh_runtime_qualified`만 발행한다.

#### Model/runtime 교차 gate

Exact qualified Gemma4 client가 qualified RHEL/csh MCP deployment를 상대로 대표 happy path,
router exhaustion/recovery, restart/resume와 unsafe-write refusal subset을 E2E로 실행한다. 동일
server/artifact/model hashes와 raw calls를 보존하고 모두 통과해야만
`qualification.deployment=deployment_qualified`를 발행한다.

## 5. 의존성과 병렬 실행

```text
M0 trust/safety
  └─ M1 host + UX/error + immutable registry contracts
       ├─ M2 immutable PAD40 import ──────────────────┐
       ├─ M3 corpus/recipe ─> M3e package + scoring ──┼─> M4a polyline→mesh glue
       └─ current router/mesh fixtures ───────────────┘              │
                                                                    v
                                                M4b bounded 21-DUT acceptance
                                                                    │
                                                                    v
                         M5 real E2E/foundry pilot + registry receipt -> M6 qualification
```

M2와 M3는 M1 계약이 고정되면 병렬 진행할 수 있다. M3e는 M1 registry와 M3의 approved corpus/recipe를
사용한다. M4a는 existing synthetic fixtures로 먼저 개발할 수 있지만 PAD40 edge와 실제 DUT terminal
landing acceptance는 M2와 `geometry_validated` package가 준비된 뒤 완료한다. M4b는 M4a integration
위에서 current DFS를 21-DUT corpus로 검증한다. M5가 exact package/GDS에 foundry receipt를 결속하기
전에는 어떤 local geometry 또는 similarity score도 foundry-validated E2E 완료로 집계하지 않는다.

외부 입력은 다음 milestone 전에 확보해야 한다.

| 외부 입력 | 소유자 | 필요한 시점 |
|---|---|---|
| exact process/PDK revision, layermap/grid, 여러 DUT가 포함된 example layout, DUT occurrence별 L/pitch/W 또는 nFin/cell-height parameter row, terminal/layer mapping, topology와 known legacy variation 설명 | PDK/device owner | M1/M3 시작 전 |
| Reference/holdout split 제약, critical layer/terminal/semantic group, 허용 tolerance, required/hard-fail dimension과 score policy 승인자 | PDK/device/layout owner | M3c/M3e 전 |
| PAD40 source GDS/cell/hash, exact pad ID/placement transforms, access-metal layer와 edge policy, asserted common DBU/no-extra-keepout contract | pad/probe owner | M2 시작 전 |
| 21-site DOE, terminal/net/pad, bias, inactive shared-pad policy | device/test owner | M4b corpus 및 M5 전 |
| Existing mesh compiler가 소비하는 rail width/space, rail/cross-tie pitch, minimum rail count와 segment corridor policy | PDK/layout owner | M4a/M4b 전 |
| DRC/LVS/PEX executable, license, deck/runset/corner와 report semantics | CAD/foundry owner | M5 전 |
| approval backend, adapter-registry storage/signing/revocation policy, signoff policy와 release authority | 조직 workflow owner | M1/M3e/M5 전 |
| exact RHEL image와 Gemma4 runtime | IT/model owner | M6 전 |

자료가 없으면 합성값으로 채우지 않는다. 해당 adapter/profile은 `unavailable`로 남기고 정확한 blocker를
반환한다.

## 6. 통합 검증 매트릭스

| 층위 | 필수 검증 |
|---|---|
| Pure unit | ActionableIssue field/redaction/order/aggregation, draft patch/resume, exact registry resolution/drift/revocation, corpus coverage/identifiability와 sealed split, dependency/style classification, scoring metric/hard-gate/aggregation, exact DBU assertion, router budget/status, segment mesh topology, no-clobber publish |
| KLayout integration | PAD40 preservation, corpus normalization과 holdout isolation, transistor dependent geometry, reference/style/holdout per-group score/diff, selected variation 재현, raw shape multiplicity/hierarchy/Region XOR, ports/connectivity/short, fresh reload, concurrent writers |
| MCP stdio | Guided inventory/template와 validate-only, 여러 field 오류의 한 번 보고, clarification answer와 immutable draft revision, correction/resume, exact package 선택, generic-only/traceback 없는 structured failure, configured facade E2E와 process restart |
| 21-DUT corpus | Reference/train/sealed-holdout coverage와 score vector, normal/congested/unsat, 84 terminals, shared nets, runtime/memory/search evidence |
| Private foundry pilot | Exact TechnologyAdapterPackage/scorecard, immutable PAD40 macro, compiled mesh, final conformance 재평가, DRC, 가능한 LVS/PEX, foundry-validated registry receipt, provenance와 disposition |
| Operations | Windows/Linux/RHEL, Python 3.11/3.13, KLayout supported versions, offline/csh install, doctor 오류의 expected/fix와 stdout 비오염 |
| Model | Exact Gemma4 repeated happy/recovery/safety/correction scenarios, 입력값을 발명하지 않는 질문과 same-draft resume, proxy와 분리된 result |

Public CI의 synthetic fixtures는 contract 회귀만 증명한다. Proprietary PDK/pad/deck을 사용하는 private
self-hosted pilot이 green이어야 `qualification.layout=foundry_layout_pilot_passed` evidence를 발행할 수 있다.
Known-invalid fixture는 단지 error code가 맞는지만 보지 않는다. 사용자가 그 응답만으로 잘못된 위치와
이유를 찾고 다음 수정 또는 질문을 수행할 수 있는지, 그리고 그 전까지 write/stage append가 0인지까지
acceptance에 포함한다.

## 7. 최종 완료 체크리스트

- [ ] 동일 SHA의 전체 CI가 green이며 validation evidence가 문서와 일치한다.
- [ ] inventory의 모든 외부 output writer가 지원 filesystem에서 같은-path 병렬 생성 시 기존 결과를 덮어쓰거나 삭제하지 않는다.
- [ ] 기본 production surface에서 conceptual geometry와 PCellizer draft가 보이지 않는다.
- [ ] stock은 fail-closed하고 configured host는 사용자 Python 조립 없이 persistent E2E를 실행한다.
- [ ] 모든 known-invalid public input은 exact field/object/value/expected/reason/fix 또는 필요한 질문,
  no-write 상태와 resume action을 반환하며 generic `invalid input`/raw traceback이 없다.
- [ ] Source PAD40 cell subtree가 수정 없이 instance로 배치되고 recursive fingerprint, ID/transform/numbering과 지정 access-metal edge landing이 일치한다.
- [ ] L/gate-pitch/W 또는 nFin/cell-height 정보를 가진 example DUT corpus가 identifiability gate를 통과하고, parameter dependency와 scoped Drawing Style이 supporting DUT/coverage와 함께 추출된다.
- [ ] 의도되지 않았거나 legacy로 유지된 DUT별 variation은 자동 평균화되지 않고 특정 reference DUT/majority/topology exception/explicit rule 중 사용자 승인 resolution을 가진다.
- [ ] 실제 transistor adapter가 승인된 corpus resolution으로 21-row DOE와 holdout DUT를 재생성하고 dependent geometry, Drawing Style, selected variation policy와 G/D/S/B terminal stack을 검증한다.
- [ ] Fitting 전에 sealed holdout이 고정되고 reference reproduction/approved style/holdout 다차원 scorecard가
  required hard gate와 evidence coverage를 통과하며 단일 평균점수로 실패를 숨기지 않는다.
- [ ] Exact tech/PDK/device/topology/domain의 immutable adapter package가 registry에 version/hash로 저장되어
  다음 job에서 onboarding 없이 재사용되고, drift/ambiguity/revocation은 mutation 전에 설명된다.
- [ ] Current router의 84 connection이 bounded search로 끝나고 모든 polyline segment가 bend/landing을 포함한 실제 hole-bearing mesh로 compile된다.
- [ ] final GDS가 fresh reload/semantic geometry/connectivity 검증을 통과한다.
- [ ] current-layout foundry DRC pilot과, 지원되는 경우 LVS가 exact hash로 결속된다.
- [ ] exact Gemma4 반복 평가와 폐쇄망 RHEL/csh 배포 smoke, 두 조합의 교차 E2E가 통과한다.
- [ ] PEX/tester/traceability/release가 없으면 `qualification.release.production_ready=false`가 유지된다.

이 체크리스트가 끝나기 전 프로젝트의 정확한 표현은 **nonproduction layout framework with a target-process
pilot in progress**다. 완료 후에도 첫 표현은 **validated target-process transistor TEG drawing pilot**이며,
조직 release gate 없이 production mask 또는 PCM release system이라고 부르지 않는다.
