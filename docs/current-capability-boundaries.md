# Current capability boundaries

이 문서는 **현재 main checkout이 실제로 실행하는 stock 동작**만 설명한다. 목표 계약은
[contracts-and-production.md](contracts-and-production.md), 구현 계획은
[upgrade_plan.md](../upgrade_plan.md)에서 별도로 관리한다.

현재 코드 구현 기준선은 commit `eb02520b53c95c200ccc6d42413a9d19767ac1bb`이다. 이후 이 검증 결과를
기록하는 문서 전용 commit은 코드 동작을 바꾸지 않는다. 과거 review 기준선과 중간 upgrade SHA는
validation 표에만 남긴다.
등록된 tool, schema 또는 planning contract가 있다는 사실은 target-process readiness를 뜻하지 않는다.

## 한 문장 판정

현재 프로젝트는 **generic/nonproduction drawing과 검증 계약 framework**다. Immutable probe-pad
overlay, labeled DUT corpus onboarding/score와 장거리 mesh compiler는 구현됐지만, 이들을 실제
transistor generator와 foundry DRC에 연결한 stock E2E는 없다.

## 리뷰 문장을 해석하는 기준

| 항목 | 현재 사실 | 오해하면 안 되는 부분 |
|---|---|---|
| Transistor Phase 1 | `family=transistor`는 `PROCESS_PRIMITIVE_ADAPTER_NOT_IMPLEMENTED`로 중단된다. | Conceptual transistor scaffold가 Phase 1의 fallback으로 자동 사용되지는 않는다. 다만 `expert` mode에는 두 계열 tool이 함께 보이므로 operator가 직접 잘못 선택할 수 있다. |
| Conceptual transistor | `generate_dut_geometry`, `export_pcell_code`, `assemble_teg`는 synthetic 치수의 `conceptual_scaffold`다. | 실제 PCell/process adapter, DRC-clean device 또는 fabrication mask가 아니다. |
| Phase 1 Pad | `frame_width`, `pad_count`, pad width/height로 단일 row 위치와 box를 다시 계산한다. | Pad GDS/OAS를 읽거나 source hierarchy, via stack, under-metal, passivation, keepout, numbering variant를 보존하지 않는다. |
| Phase 1 route | Feasibility polyline의 각 segment를 최소 2-rail, cross-tied mesh로 compile하고 bend/terminal tie를 검증한다. | Synthetic Pad 위치를 쓰는 legacy Phase 1이며 실제 Pad/DUT landing 또는 foundry legality 증거는 아니다. |
| Mesh compiler | Straight-segment compiler와 polyline glue가 Phase 1에 연결됐다. | Full-PDK legality/PEX 또는 실제 21-DUT corpus acceptance를 뜻하지 않는다. |
| Routing search | Candidate뿐 아니라 전체 DFS node와 wall-time budget이 있으며 budget 종료를 physical infeasibility와 구분한다. | 84개 connection의 synthetic parallel stress가 supplied 21-DUT/pad placement의 feasibility를 증명하지 않는다. |
| PCellizer | Authoring-supported non-array occurrence의 direct box 한 축과 parameter key 하나를 resize해 row별 standalone GDS를 만든다. | Reusable KLayout PCell이나 Poly/Active/contact/implant/pin을 함께 움직이는 W×L composite transistor generator가 아니다. |
| Persistent facade | Stock intake는 bundled research-only Kelvin resistor profile/version에 한정되고 `teg_plan`은 approval 검증에서 planning 전에 fail-closed한다. | 실패는 승인 우회가 아니라 의도된 trust boundary다. 임의 target profile이나 일반 MCP 설정만으로 production E2E가 된다는 뜻도 아니다. |
| Model evaluation | 기본 live model은 `gemini-3.5-flash-medium`이고 한 실행은 scenario 하나의 MCP tool-call trace smoke다. 결과는 `qualification_claim=none`, `proxy_equivalence_claimed=false`다. | Completed tool result, final-answer semantics, non-MCP write와 permission enforcement를 아직 채점하지 않으므로 exact Gemma4 reliability/write-safety 검증으로 인용하면 안 된다. |
| Pad/corpus onboarding | Pad artifact와 corpus/resolution/scorecard/candidate는 소비 직전에 schema와 content address를 다시 검사한다. Compiler-declared basis의 rank 부족만 identifiability blocker이며 normalized singular value·condition number는 non-blocking warning으로 저장한다. Caller policy score는 진단 전용이고 candidate에는 metric별 종류·허용오차·가중치·hard-fail을 가진 host-approved policy와 승인자가 필요하다. Candidate build는 metric 판정과 weighted score를 재계산한다. | 다른 file SHA는 `distinct_stream`일 뿐 compiler 실행 증거가 아니다. 검증 DUT도 같은 source에 보여서 비밀 holdout이 아니며 similarity는 PCell·전기·foundry 동등성이 아니다. Stock에는 qualification-policy authority가 없어 candidate scoring은 fail-closed한다. 수치 안정성 warning 기준은 보편적 합격선이나 foundry 증거가 아니다. |
| Persistent host state | Technology lifecycle은 package별 `sequence + prev_record_sha256` chain과 final sequence/hash head를 다시 읽고 revoke를 terminal state로 처리한다. Local record/head rollback도 탐지해야 하면 host가 별도 `lifecycle_trust_anchor`를 연결하고 startup에 재검증한다. Deployment TOML root를 status/onboarding도 사용하며 engine 0개 doctor는 실패한다. | `recorded_at`은 표시용 provenance다. Stock local head는 record와 head를 함께 되돌릴 수 있는 writer/admin compromise를 탐지하지 않으며, 외부 WORM/signed ledger가 구성됐다고 주장하지 않는다. |
| Output publication | 공개 writer inventory에 create-only file/directory publish와 same-target race 회귀를 적용했다. | NTFS/ext4/XFS local contract이며 NFS/SMB/multi-host는 fail-closed한다. |
| External runner | Host-only runner registry, executable/license/deck/runset preflight, timeout/resource declaration과 report provenance binding 계약이 있다. | Stock runner나 foundry deck/license는 없고 runner output 자체는 signoff가 아니다. |
| Linux/csh launcher | Source-checkout helper다. | 현재 폐쇄망 RHEL 배포 bundle이나 KLayout/PDK readiness doctor가 아니다. |

## Tool mode의 정확한 의미

| Mode | 적합한 용도 | 현재 경계 |
|---|---|---|
| `drawing` | Generic inspect/compare/style/nonproduction drawing | Phase 1과 persistent facade가 보이지 않는다. |
| `facade` | Persistent intake/status 및 host-integrated job | Stock은 verifier가 없어 `teg_plan` 호출 시 계획 생성 전에 fail-closed한다. |
| `expert` | 여러 실험·reference·PCellizer profile을 비교하는 개발자/operator | Conceptual, incomplete Phase 1과 runnable tool을 함께 노출한다. `expert`는 readiness 등급이 아니다. |
| `onboarding` | Immutable Pad/DUT corpus와 adapter candidate 준비 | Candidate는 qualified adapter가 아니며 foundry receipt를 발행하지 않는다. |

처음 사용하는 모델에는 `drawing` 또는 `facade`를 명시한다. `tools/list`가 실제 surface의
권위 있는 값이며, tool 등록 여부와 target-process capability는 별개다.
현재 mode는 tool schema/list만 줄이고 server instruction은 공통이다. 따라서 mode 권장은 exact
Gemma4 또는 다른 제한 모델의 성공률 검증이 아니다.

이 경계 metadata를 반영한 HEAD 재계측은 다음과 같다. 전체 record는 compact, sorted-key
JSON의 serialized `tools/list` 길이다.

| Mode | Tools | 전체 `tools/list` chars |
|---|---:|---:|
| `expert` | 64 | 115,533 |
| `facade` | 7 | 21,919 |
| `drawing` | 7 | 13,136 |
| `onboarding` | 9 | 10,827 |

Mode 공통 server instruction은 8,017자다. `drawing`, `facade`, `onboarding`은 tool 10개 이하,
`tools/list + instruction` 30,000자 이하를 CI에서 검사한다. Tool 수나 문자 수 자체는 usability 또는 model
qualification evidence가 아니다.

## Direct-measurement Phase 1의 현재 범위

현재 sequence는 handoff와 검증 계약을 시험하는 nonproduction 경로다.

```text
intake/process contract
→ optional DOE
→ resistor/MOM primitive, 또는 외부에서 주입된 verified primitive
→ synthetic-pad centerline feasibility
→ synthetic PAD_MESH + bounded multi-rail route-mesh composition
→ fresh reload
```

Transistor는 stock adapter가 없으므로 primitive 단계에서 의도적으로 중단된다. 이후 단계에 임의의
conceptual geometry를 주입해 완료시켜도 실제 transistor workflow가 되지 않는다. Phase 1에는
immutable pad macro artifact와 corpus-derived transistor adapter를 받는 production handoff가 없다.

따라서 `complete_verified_nonproduction`, `fresh_reload_verified` 또는 `production_ready=false`는
요청한 파일/geometry handoff의 무결성만 뜻한다. 실제 Pad/DUT landing을 사용한 route acceptance,
DRC/LVS/PEX, 측정 가능성이나 release readiness의 증거가 아니다.

## 목표 계약과 구현 보장의 구분

다음은 target direct-measurement geometry의 **목표 acceptance contract**다.

- 장거리 single rail 금지.
- Parallel rail, repeated cross-tie와 실제 hole.
- Multiple positive-area Pad landing.
- Process-rule 기반 contact packing과 terminal stack.

Current Phase 1은 supplied internal corridor에서 mesh/contact geometry 계약을 검증한다. 하지만 실제
Pad macro/DUT adapter와 full-PDK DRC/PEX를 연결하지 않았으므로 이를 transistor E2E 또는 저항 최적화
증거로 합치면 안 된다.

## Persistent, foundry와 production 상태

- Stock `teg_intake`는 bundled research-only `sln001_kelvin_reference_demo`의 exact resistor
  profile/version에서 draft/job을 저장할 수 있다. 임의 target은 host ProcessCapabilityProvider가 필요하다.
- Stock Kelvin은 planning/generation engine이 이미 등록되어 있지만 trusted verifier가 없어
  `teg_plan`에서 planning 전에 중단된다. 임의 target/production profile에는 trusted verifier 외에도
  matching ProcessCapabilityProvider, planning/generation engine, runner와 policy가 필요하다.
- Bundled Kelvin demo는 Python에서 test-only component를 직접 조립하는 nonproduction regression이다.
- Research-only Kelvin planning/generation engine, immutable Pad overlay와 외부 JSON report
  normalization/binding contract는 있다. Target-production transistor planning/generation engine,
  Pad/DUT/route 통합 composer, foundry DRC/LVS/PEX **execution runner**와 production host bootstrap은 없다.
- `layout_signoff_evidence_approved`는 layout evidence 상태일 뿐이며 현재 모든 workflow의
  `production_ready`는 false다.

## Validation 기록을 읽는 법

문서에 적힌 pass count를 다른 snapshot의 release evidence로 사용하지 않는다.

| Snapshot | 상태 | 검증 결과 | 해석 |
|---|---|---|---|
| Baseline commit | `1df82b5043a41cf1485bdc7e1bf43c9a2930d1cf` | 로컬 Windows/Python 3.13.5/KLayout 0.30.10: `646 passed, 1 warning` | 과거 local diagnostic |
| Baseline remote CI | 같은 baseline SHA의 [Actions run 33589034379](https://github.com/sjoohw/klayout-mcp-teg/actions/runs/33589034379) | pytest 5개 job 실패, csh smoke만 성공 | baseline release verdict는 red |
| Upgrade implementation | `7a7348268c8af2593e59d2a1c6d434b32c0fb087` | 같은 로컬 환경: `702 passed, 1 warning` | local regression |
| Upgrade remote CI | 같은 upgrade SHA의 [Actions run 33617837011](https://github.com/sjoohw/klayout-mcp-teg/actions/runs/33617837011) | Windows 2개, wheel, csh 성공; Ubuntu 2개와 KLayout integration은 동일 OS 안내문 test 1건 실패 | current release verdict는 red |
| Review hardening | `c4d456481a214cf380e91e507aed92c1f77e03f8` | 로컬 Windows/Python 3.13.5/KLayout 0.30.10: `708 passed, 1 warning`; [Actions run 33626068843](https://github.com/sjoohw/klayout-mcp-teg/actions/runs/33626068843) 전체 green | Windows/Ubuntu 3.11·3.13, wheel, csh, KLayout 0.30.10 통합 통과 |
| Evidence hardening | code `afd90cd7dbdfdac4ca4d76bee8a5a6ee583fde80`, docs `084a6c116f4021317f90c3a2dd26e28892e157c0` | 로컬 Windows/Python 3.13.5/KLayout 0.30.10: `715 passed, 1 warning`; [Actions run 33637295915](https://github.com/sjoohw/klayout-mcp-teg/actions/runs/33637295915) 전체 green | Lifecycle chain/content-address/schema와 Windows/Ubuntu/KLayout 통합 회귀 통과 |
| Qualification-gate hardening | code `dbd25060dc35e48e8d1cc55dd9bf313d8bac3d77`, docs `21a4b28cdd58122700ee80adca45e692b28400bf` | 로컬 Windows/Python 3.13.5/KLayout 0.30.10: `719 passed, 1 warning`; [Actions run 33643053110](https://github.com/sjoohw/klayout-mcp-teg/actions/runs/33643053110) 전체 green | Lifecycle trusted head, exact fingerprint/DBU hard gate와 persisted DOE identifiability를 Windows/Ubuntu/KLayout에서 통과 |
| Policy/model/anchor hardening | code `eb02520b53c95c200ccc6d42413a9d19767ac1bb`, docs `c20522356fc51b4061d3b85a1c5c910f51ff0f3d` | 로컬 Windows/Python 3.13.5/KLayout 0.30.10: `722 passed, 1 warning`; [Actions run 33693275318](https://github.com/sjoohw/klayout-mcp-teg/actions/runs/33693275318) 전체 green | Host-owned candidate policy, compiler-declared basis rank와 optional external lifecycle anchor를 Windows/Ubuntu/KLayout에서 통과 |
| Metric policy/advisory DOE | `512c67f45df57708d07ff296c91c8cabc60b674e` | 로컬 Windows/Python 3.13.5/KLayout 0.30.10: `723 passed`; [Actions run 33697986336](https://github.com/sjoohw/klayout-mcp-teg/actions/runs/33697986336) 전체 green | Metric별 typed tolerance/weight/hard-fail, score 재계산과 non-blocking DOE stability warning을 Windows/Ubuntu/KLayout에서 통과 |

Local pass와 remote CI green은 별도 조건이다. Review-hardening과 evidence-hardening code set은
두 조건을 모두 통과했다. Qualification-gate, policy/model/anchor와 metric/advisory DOE hardening도 각각
표에 적힌 동일 code/docs snapshot의 remote CI까지 통과했다.
이는 repository regression 기준선이며 실제 target transistor/foundry qualification을 뜻하지 않는다.

`feedback.md`와 `answer.md`는 각 검토 시점의 historical record다. 현재 상태의 권위 있는 요약은
이 문서, 현재 코드/테스트와 동일 SHA의 CI 결과이며, 다음 구현 순서는
[upgrade_plan.md](../upgrade_plan.md)를 따른다.

## Linux/csh와 폐쇄망 경계

`scripts/run-klayout-teg-mcp.csh`는 source checkout launcher다. Python project dependency와 KLayout을
설치하는 배포 도구가 아니며, offline wheelhouse/RPM/SBOM 또는 foundry adapter/deck을 제공하지 않는다.
폐쇄망 RHEL qualification은 아직 완료되지 않았다. 정확한 지원 image, interpreter, KLayout binary,
shared libraries와 dependency provenance가 고정되기 전에는 “RHEL supported”라고 표현하지 않는다.
