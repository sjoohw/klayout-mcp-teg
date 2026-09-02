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
├─ drawing_service.py        atomic Manhattan drawing
├─ layout_service.py         inspect/compare
├─ mesh_routing.py           generic staged mesh/contact compiler
├─ kelvin_*.py               SLN001 Kelvin profile
├─ phase1_*.py               transistor/resistor/capacitor workflow
├─ pcellizer_*.py            hierarchy-preserving parameterization
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
uv run --extra dev pytest -q
uv run --extra dev python -m compileall -q src tests examples
```

Current validated snapshot:

```text
checked_at: 2026-09-02 KST
base_commit: 8423943 + current working-tree changes
host: Windows, Python 3.13.5, KLayout 0.30.10
command: uv run --extra dev pytest -q -p no:cacheprovider
collected: 644
passed: 644
skipped: 0
warnings: 1 upstream pydantic-settings warning
compileall: passed
```

검증 범위:

- Geometry validation, Pad/slot mapping, rule and connectivity guardrail.
- KLayout hierarchical GDS/OAS read, fresh reload, layer Region XOR.
- Live/generated PCell과 hierarchy variant reuse.
- Kelvin six-split regeneration과 project regression reference recursive XOR 0.
- Process-neutral Phase 1 primitives, context, mesh와 width-scaled contact contracts.
- PCellizer occurrence/array/transform/snapshot/batch determinism.
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

완료된 무결성 항목:

1. `teg_status` actual output file existence/root/hash 재검증.
2. DesignIntent measurement execution/timing/safety와 MeasurementManifest exact binding.
3. Process capability의 verification 상태와 persistent workflow gate 분리.
4. `generation_staged`와 `drawing_complete`에서 deterministic resume.
5. Mode별 `server_status` capability/recommendation filtering.
6. Host-only trusted signoff policy가 선택한 current-layout evidence subset과 receipt binding.
7. `workflow://` document status rehash, Windows-safe job id와 per-job append serialization.

남은 우선순위:

1. Host-injected component를 포함한 real stdio persistent restart E2E.
2. Profile별 sweep/topology/de-embedding schema 구체화.

각 항목은 drawing 자체를 막는 임의 gatekeeper가 아니라, 해당 evidence/readiness claim을
정확히 제한하는 방식으로 구현한다.

## 외부 입력 후 가능한 개선

- Actual foundry transistor/resistor/capacitor sample과 layer map XOR matrix.
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
