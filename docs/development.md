# Development and validation

## 프로젝트 구조

```text
src/klayout_mcp/
├─ server.py                 MCP 등록, tool mode, 얇은 adapter
├─ mcp_protocol.py           result schema와 annotation
├─ workflow_types.py         persistent nested MCP input schema
├─ workflow_manifest.py      content-addressed document validation
├─ workflow_store.py         append-only job/facade
├─ approval.py               host approval trust boundary
├─ external_evidence.py      DRC/LVS/PEX report adapter contract
├─ drawing_service.py        create-only Manhattan drawing/fresh reload
├─ layout_service.py         inspect/compare
├─ mesh_routing.py           generic staged mesh/contact compiler
├─ kelvin_*.py               SLN001 Kelvin profile
├─ phase1_*.py               R/MOM scaffold와 transistor-adapter blocker; synthetic Pad/mesh-route composition
├─ pad_macro.py              immutable Pad macro artifact와 overlay composition
├─ dut_corpus.py             labeled DUT coverage/variation/logical-validation scoring
├─ technology_registry.py    exact candidate package registry
├─ file_publication.py       local create-only file/directory publication
├─ host_factory.py           host component 조립과 preflight
├─ verification_runner.py    host-only external runner 계약
├─ pcellizer_*.py            non-array direct-box 1-parameter static-GDS authoring
├─ reference_*.py            content-addressed reference library
└─ examples/profiles/        isolated nonproduction process adapters

klayout_plugin/              PCellizer/Reference KLayout GUI
examples/                    Cataloged runnable/GDS/style/UI/reference examples
artifacts/                   Preserved golden/reference artifacts
skills/
├─ klayout-drawing/          General pya/PCell/hierarchy/fresh-reload skill and helpers
└─ klayout-teg-routing/      Kelvin-specific measurement/routing orchestration
tests/                       Pure, stdio and KLayout integration tests
```

MCP runtime은 repository의 skill 파일을 읽지 않는다. Skill은 LLM이 질문과 tool 순서를
일관되게 따르도록 돕는 선택적 orchestration layer다. `klayout-drawing`은 로컬 환경에만
존재하는 전제 없이 저장소에 완전한 references/scripts/assets와 함께 보관한다.

## 로컬 검증

```powershell
uv run --frozen --extra dev pytest -q
uv run --frozen --extra dev python -m compileall -q src tests examples
```

Historical baseline, not release evidence:

| 항목 | 관측값 |
|---|---|
| checked at | 2026-09-02 KST |
| exact commit | `1df82b5043a41cf1485bdc7e1bf43c9a2930d1cf` |
| local diagnostic | Windows / Python 3.13.5 / KLayout 0.30.10: `646 passed, 1 warning` |
| same-SHA remote CI | [Actions run 33589034379](https://github.com/sjoohw/klayout-mcp-teg/actions/runs/33589034379): pytest 5 jobs failed, csh smoke only passed |
| release verdict | red; local pass count를 current-main 또는 cross-platform 검증으로 사용하지 않음 |

Upgrade commit `7a7348268c8af2593e59d2a1c6d434b32c0fb087`의 같은 로컬 환경 진단은
`702 passed, 1 warning`이다. 같은 SHA의 [Actions run 33617837011](https://github.com/sjoohw/klayout-mcp-teg/actions/runs/33617837011)은
Windows 3.11/3.13, wheel smoke와 csh launcher가 통과했지만 Ubuntu 3.11/3.13과 KLayout integration이
OS별 안내문 테스트 한 건으로 실패했다. 따라서 현재 release verdict도 red다.

Pass count를 README와 여러 문서에 복사하지 않는다. Release evidence는 exact SHA, clean/dirty state,
lock provenance, OS/Python/KLayout, pass/skip/warning과 Actions URL을 가진 generated artifact로만
갱신해야 한다.

검증 범위:

- Geometry validation, Pad/slot mapping, rule and connectivity guardrail.
- KLayout hierarchical GDS/OAS read, fresh reload, layer Region XOR.
- Live/generated PCell과 hierarchy variant reuse.
- Kelvin six-split regeneration과 project regression reference recursive XOR 0.
- 분리된 Phase 1 R/MOM primitive, conceptual transistor fixture와 context contract.
- Phase 1 polyline→multi-rail mesh integration, bend/terminal tie와 bounded-search stress. 실제
  Pad/DUT landing을 사용한 84-connection acceptance는 아님.
- Immutable Pad macro 등록/overlay와 recursive source-cell preservation. Legacy Phase 1 handoff는 아님.
- Labeled DUT corpus coverage, invariant style, variation resolution, train/logical-validation score와 candidate
  registry. Parameter dependency compiler나 actual transistor materialization 검증은 아님.
- PCellizer occurrence/array inventory와 transform/snapshot determinism, non-array direct-box static-GDS
  batch. Reusable PCell이나 array-member/composite authoring 검증은 아님.
- Reference library selection/precedent contract.
- MCP stdio schema, annotations, tool modes와 error propagation.
- Persistent manifest/approval/external evidence/hash binding.
- Real KLayout nonproduction persistent Kelvin demo.

한 개의 upstream warning은 `pydantic-settings 2.15.0`의 unresolved `lifespan` forward reference다.

## CI

GitHub Actions 정의는 Windows/Ubuntu, Python 3.11/3.13, compileall과 pytest를 포함한다.
Ubuntu KLayout integration은 공식 package/checksum과 offscreen Qt를 사용한다. 로컬 결과와
원격 CI 실행 상태는 별개이며, release 시 실제 CI run을 확인해야 한다.

## 현재 내부 우선순위

현재 capability의 권위 있는 경계는
[current-capability-boundaries.md](current-capability-boundaries.md), 상세 실행 순서는
[upgrade_plan.md](../upgrade_plan.md)에 있다.

완료된 무결성 항목:

1. `teg_status` actual output file existence/root/hash 재검증.
2. DesignIntent measurement execution/timing/safety와 MeasurementManifest exact binding.
3. Process capability의 verification 상태와 persistent workflow gate 분리.
4. `generation_staged`와 `drawing_complete`에서 deterministic resume.
5. Mode별 `server_status` capability/recommendation filtering.
6. Host-only trusted signoff policy가 선택한 current-layout evidence subset과 receipt binding.
7. `workflow://` document status rehash, Windows-safe job id와 per-job append serialization.
8. 공개 file/content-addressed directory writer의 local create-only publication과 same-target winner 보존.
9. Phase 1 polyline의 multi-rail mesh composition과 router node/wall-time budget.
10. Immutable Pad macro overlay, labeled DUT corpus/logical-validation score와 exact candidate registry.

남은 우선순위:

1. Linux에서 실패하는 OS별 안내문 테스트를 수정하고 동일 SHA CI를 green으로 만든다.
2. Corpus에서 CPP 연계 dependent geometry를 만드는 actual transistor compiler를 구현한다.
3. Immutable Pad macro, corpus-derived DUT와 mesh route를 persistent target-process engine에 연결한다.
4. Supplied Pad edge/DUT port를 쓰는 실제 21-DUT, 84-connection routing acceptance를 수행한다.
5. Host-injected component를 포함한 real stdio persistent restart E2E와 foundry pilot을 수행한다.
6. ext4/XFS, unsupported NFS/SMB와 폐쇄망 RHEL deployment를 qualification한다.

각 항목은 drawing 자체를 막는 임의 gatekeeper가 아니라, 해당 evidence/readiness claim을
정확히 제한하는 방식으로 구현한다.

## 외부 입력 후 가능한 개선

- 위 code blocker를 검증할 actual foundry transistor/resistor/capacitor sample과 layer map XOR matrix.
- 업무상 필요한 경우 approved DRC/LVS/PEX adapter와 해당 host policy.
- Probe pad/scribe/de-embedding/tester 계약.
- Obstacle-aware multi-net global orthogonal mesh router.
- Extracted-RC/EM/density/current-crowding 기반 routing 후보 평가.
- Company DRC result-db와 reference precedent classifier adapter.
- PCellizer composite-DUT 승인과 general recipe operator.

## Phase 2

Ring oscillator, MUX/decoder, buffer, supply/ground, load와 divider는 roadmap이다. 다음 조건 전에는
구현하지 않는다.

- Phase 1 transistor/resistor/capacitor geometry와 measurement contracts가 안정적임.
- 한 실제 공정에서 sample/generation과 조직이 선택한 verification flow가 검증됨.
- Multi-device routing과 measurement semantics가 deterministic하게 재현됨.
- User가 Phase 2 topology와 Pad/measurement budget을 승인함.

## 유지보수 원칙

- 기존 사용자 artifact와 final GDS를 보존한다.
- 넓은 cleanup/reset 대신 exact target만 수정한다.
- Profile-specific 규칙을 generic core로 승격하지 않는다.
- KLayout file write 후 fresh layout으로 다시 검증한다.
- Raw GDS bytes보다 semantic geometry/XOR를 비교한다.
- 새 review 지적은 재현 테스트를 만든 뒤 수정한다.
- Historical 실행 일지와 반복된 test count는 README에 누적하지 않는다.
