# KLayout Drawing MCP

KLayout 내장 Python API `pya`를 사용하는 범용 layout drawing·inspection·verification stdio
MCP 서버다. Package/command 이름은 호환성을 위해 `klayout-teg-mcp`를 유지한다.

25-Pad TEG, Kelvin M1 resistor, PCellizer와 process-node reference library는
범용 drawing core 위의 profile/workflow다. 특정 Pad 수나 DUT가 MCP 전체 범위를 정의하지 않는다.

Fabrication process profile은 의도적으로 번들하지 않는다. 실제 사용 전 타깃 공정의 layermap,
grid, 필요한 rule과 device geometry 근거를 [onboarding.md](onboarding.md)에 따라 등록해야 한다.

LLM orchestration 지침도 저장소에 함께 둔다. 범용 GDS/PCell 작업은
[`skills/klayout-drawing`](skills/klayout-drawing/SKILL.md), Kelvin routing은
[`skills/klayout-teg-routing`](skills/klayout-teg-routing/SKILL.md)을 사용한다. 이 파일들은
프로젝트의 재현 가능한 지침이며 MCP runtime의 숨은 의존성이 아니다. Host가 project-local skill을
자동 탐색하지 않으면 해당 디렉터리를 명시적으로 등록하거나 LLM context로 제공해야 한다.

> 기본 checkout에는 trusted approval backend, production process engine, foundry
> DRC/LVS/PEX adapter, signoff policy, approved scribe/probe-pad 계약이 없다. 따라서 모든 stock
> 생성 결과는 비생산용이다. Fresh reload, XOR와 internal connectivity 검사는 file/geometry
> 무결성 증거이지 foundry sign-off, 측정 가능성 또는 PCM release 증거가 아니다.

현재 구현과 목표 계약을 혼동하지 않으려면 먼저
[Current capability boundaries](docs/current-capability-boundaries.md)를 읽는다. 특히 tool이
등록되어 있거나 `expert` mode에 보인다는 사실은 target-process readiness를 뜻하지 않는다.

## 현재 가능한 것

| 사용자 결과 | Stock checkout | 의미 |
|---|---|---|
| GDS/OAS inspect·compare | 가능 | 입력을 바꾸지 않고 hierarchy/layer/geometry 관측 |
| Generic Manhattan drawing | 가능 | 명시적 DBU/layer/geometry로 새 nonproduction file 생성 |
| Kelvin regression example | 가능 | 보존된 project reference 범위에서만 유효 |
| PCellizer split batch | 제한 지원 | Non-array occurrence의 direct box 한 축·parameter 한 개를 resize한 row별 static GDS; reusable PCell 생성 아님 |
| Reference Library | 가능 | Full GDS hash 보관과 사용자 KLayout confirmation |
| Reference style 추출 | 가능 | hierarchy/layer/직교성/치수 빈도 관측; rule·net·전기 특성 추론 없음 |
| Direct-measurement Phase 1 | 제한된 nonproduction scaffold | Transistor adapter 없음, Pad 재합성; bounded polyline은 multi-rail mesh로 compile |
| Pad macro onboarding | 지원 | Source cell을 immutable artifact로 등록하고 새 top에서 instance overlay; Phase 1과는 아직 미연결 |
| Transistor corpus onboarding | 지원 | Integer/terminal/topology 입력 검증과 content-addressed resolution/score/compiler 결속; distinct stream 비교일 뿐 실행 receipt·sealed 평가·신규 공정 PCell·foundry 승인은 아님 |
| Persistent `teg_intake` | 제한 지원 | Stock은 bundled research-only Kelvin resistor profile/version에 한정; 임의 target은 host provider 필요 |
| Persistent plan/generate/verify | Host 통합 필요 | Target-production verifier/provider/engine과 external runner/policy 필요 |
| Foundry sign-off·PCM release | 불가 | 실제 PDK/deck/probe/scribe/조직 정책 필요 |

최근 무결성 보강으로 stream file과 `workflow://` 문서의 `teg_status` 재해시,
DesignIntent↔MeasurementManifest actual source/program/compliance/timing/safety 결속,
host-policy-selected external evidence 결속, durable generation staging과 job별 append 직렬화가 구현됐다.
`signoff_evidence_approved`는 layout evidence 승인일 뿐이며 `production_ready`를 true로 만들지 않는다.
Technology adapter lifecycle은 package별 단조 sequence와 이전 record hash를 사용한다. `recorded_at`은
정렬 기준이 아니며 revoke는 해당 exact package의 terminal state다.

현재 가장 중요한 미비사항:

- Stock Phase 1은 transistor에서 `PROCESS_PRIMITIVE_ADAPTER_NOT_IMPLEMENTED`로 중단한다.
- Phase 1은 실제 padset GDS/OAS를 읽거나 보존하지 않고 frame/pad 수치로 Pad geometry를 다시 만든다.
- Phase 1의 DUT–Pad route는 bounded polyline을 multi-rail mesh로 compile한다. 다만 legacy Phase 1은
  여전히 실제 Pad macro 대신 synthetic Pad 위치를 사용한다.
- Host-controlled external runner/preflight 계약은 구현됐지만 stock host에는 실제 runner, deck,
  license 또는 signoff policy가 설정되지 않는다.
- 실제 stdio `teg_*`와 host-injected verifier/engine을 함께 재시작하는 E2E가 없다.
- Actual foundry scribe/probe/de-embedding/tester/PCM 계약과 adapter가 없다.
- Model harness는 single-scenario Gemini proxy tool-call trace smoke이며 exact Gemma4 qualification이 아니다.
- 공개 file/content-addressed directory writer에는 지원 local filesystem의 create-only publish와 race
  회귀가 적용됐다. NFS/SMB/multi-host와 폐쇄망 RHEL deployment는 지원하지 않는다.
- 저장소 최상위 project license가 아직 선택되지 않았다. `examples/external/nangate45/` 자산도 출처와
  재배포 조건을 확인하기 전에는 release 자산으로 간주하지 않는다.

이 항목은 [production 계약](docs/contracts-and-production.md)과
[개발 우선순위](docs/development.md)에 상세히 기록한다.

## 설치

필수 환경:

- Python 3.11 이상.
- Source checkout 설치·개발에는 `uv`. 이미 dependency가 설치된 interpreter로 launcher를 실행할 때
  runtime 자체가 `uv`를 호출하지는 않는다.
- KLayout 0.30.0 이상. 현재 검증 버전은 0.30.10.

Repository root에서:

```powershell
uv sync --frozen --extra dev
uv run --frozen python --version
uv run --frozen python -c "from klayout_mcp.klayout_adapter import find_klayout_executable; print(find_klayout_executable())"
```

일반 MCP host 설정:

```json
{
  "mcpServers": {
    "klayout-drawing": {
      "command": "uv",
      "args": ["run", "--frozen", "klayout-teg-mcp"],
      "cwd": "C:\\absolute\\path\\to\\klayout-auto",
      "env": {
        "KLAYOUT_EXE": "C:\\absolute\\path\\to\\klayout_app.exe",
        "KLAYOUT_MCP_TOOL_MODE": "drawing"
      }
    }
  }
}
```

Host가 `cwd`를 지원하지 않으면 `uv --directory <repository-root> run --frozen klayout-teg-mcp`를
사용한다. 직접 실행했을 때 아무 문구 없이 대기하는 것이 정상이다. Transport는 stdio이며
보통 MCP host가 필요할 때 process를 시작하고 종료한다.

KLayout 0.30.0 이상은 지원 정책이며 현재 검증 버전은 0.30.10이다. `server_status`의 버전 표시는
설치된 executable의 version preflight가 아니므로 layout-backed tool로 별도 확인해야 한다.

### Linux/csh source checkout

`scripts/run-klayout-teg-mcp.csh`는 dependency installer나 폐쇄망 배포 bundle이 아니다. `uv sync`로
만든 repository `.venv/bin/python`을 사용하거나 interpreter를 명시한다.

```csh
setenv KLAYOUT_MCP_PYTHON /absolute/path/to/klayout-auto/.venv/bin/python
setenv KLAYOUT_EXE /absolute/path/to/klayout
csh scripts/run-klayout-teg-mcp.csh
```

Launcher는 Python과 `mcp`/PyYAML import만 preflight한다. 지원 RHEL image, KLayout RPM/shared library,
offline wheelhouse, PDK/deck/license까지 검증하는 deployment qualification은 아직 없다.

상대 경로는 MCP process `cwd` 기준이다. Persistent job/output 기본 경로는 각각
`output/workflow-jobs/`, `output/workflow-final/`이며 다음 환경변수로 변경할 수 있다.

```text
KLAYOUT_MCP_WORKFLOW_ROOT
KLAYOUT_MCP_WORKFLOW_OUTPUT_ROOT
```

## Tool mode

작은 모델이 전체 tool을 모두 비교하지 않도록 공개 surface를 줄일 수 있다.

| `KLAYOUT_MCP_TOOL_MODE` | 내용 |
|---|---|
| `expert` | 전체 기능. 명시적으로 opt-in하는 개발자/operator용 |
| `facade` | `server_status`, `host_doctor`, `teg_intake/status/plan/generate/verify` |
| `drawing` | `server_status`, draw/inspect/style/compare, mesh/contact planner |
| `onboarding` | Pad macro와 labeled DUT corpus 등록·결정·score·candidate package |

오타가 난 mode는 시작 시 실패한다. 실제 호출 가능 목록은 MCP `tools/list`를 기준으로 한다.
환경변수를 생략한 stock 기본값은 `drawing`이다. 작은 모델의 persistent 작업은 `facade`, 단순
geometry 작업은 `drawing`, Pad/DUT example 등록은 `onboarding`을 사용하고,
PCellizer/reference/profile 도구를 함께 선택해야 할 때만 `expert`를 사용한다.
`expert`에는 conceptual DUT/PCell/assembly와 미완성 Phase 1 도구가 함께 보인다. 이는 기능 등급이나
production readiness가 아니며, 처음 사용하는 host 예제는 의도적으로 `drawing`을 명시한다.
`drawing` mode에는 Phase 1 도구가 없고, stock `facade`는 approval backend 부재로 `teg_plan` 호출 시
계획을 만들기 전에 중단한다. Tool surface는 줄어들지만 server instruction은 아직 mode 공통이어서
현재의 mode 분리는 제한 모델 검증 결과가 아니라 schema/tool-list 축소 수단이다.

## 어떤 workflow를 선택할까

| 목적 | 시작점 |
|---|---|
| 기존 layout 읽기 | `inspect_layout` |
| 기존 layout의 관측 style 추출 | `extract_layout_style` |
| 두 layout 비교 | `compare_layouts` |
| 명시적 box/text/instance/boolean drawing | `draw_manhattan_layout` |
| 불완전한 transistor/resistor/capacitor TEG 요청의 intake/questions | `plan_direct_measurement_teg` — drawing 없음, transistor adapter 없음 |
| Kelvin reference 재현 | `plan_kelvin_m1_routing` |
| Existing GDS parameterization | `inventory_pcellizer_hierarchy` |
| 실제 Pad macro 등록·보존 배치 | `register_pad_macro` → `compose_registered_pad_macro` |
| Labeled DUT corpus 등록·차이 해결·score | `onboard_transistor_corpus` → `resolve_transistor_corpus` → `score_transistor_adapter` |
| Scored adapter 후보 저장 | `build_transistor_adapter_candidate` → `register_transistor_adapter_candidate` |
| Process-node reference 등록 | `register_reference_layout` |
| Resumable host job | `teg_intake` |

전체 단계와 예제는 [workflows.md](docs/workflows.md)에 있다.

## 10분 smoke test

### 1. 연결 확인

`server_status({})`를 호출해 version, tool surface, recommended entrypoint와 stock persistent
제한을 확인한다. 이 호출만으로 KLayout 실행 가능성을 증명하지 않는다.

### 2. Bundled GDS inspect

```json
{
  "layout_path": "examples/gds/kelvin_m1_w24_48_100nm_l2_3um.gds",
  "top_cell": "SLN001_PADSET",
  "text_limit": 10
}
```

예상값:

```text
dbu_um: 0.00025
top_cell: SLN001_PADSET
top_bbox_um: [0, 0, 2000, 54]
```

### 3. Create-only nonproduction drawing

먼저 `output/golden-tour/`를 만들고 다음 인자로 `draw_manhattan_layout`을 호출한다.

```json
{
  "output_layout_path": "output/golden-tour/one-unit.gds",
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
    {"type": "add_text", "cell": "TOP", "layer": "text", "text": "GOLDEN_TOUR", "origin_um": [0, 0]}
  ],
  "confirm_nonproduction": true
}
```

`fresh_reload_verified=true`, single top, `production_ready=false`가 성공 기준이다. 생성본을
다시 `inspect_layout`에 전달해 top/DBU/layer/bbox를 확인한다. 이미 존재하는 파일은 보존된다. 같은
local target의 동시 writer는 정확히 하나만 성공하고 loser는 winner를 변경하지 않은 채
`OUTPUT_ALREADY_EXISTS`를 반환한다.

## Persistent workflow

Host-integrated 순서:

```text
teg_intake → teg_plan → teg_generate → teg_verify
```

Stock server는 `teg_plan`에서 `APPROVAL_BACKEND_UNAVAILABLE`로 중단되는 것이 정상이다.
Approval reference는 LLM이 만들 수 없고 trusted host가 exact draft/process/source/output scope에
대해 발급하고 검증해야 한다.

실제 KLayout을 사용하는 test-only persistent Kelvin demo:

```powershell
uv run python examples/run_persistent_kelvin_demo.py --run-root output/persistent-kelvin-demo-01
```

이 예제는 golden XOR, fresh reload, connectivity projection, actual layout hash와
MeasurementManifest binding을 확인하지만 production 또는 tester readiness를 주장하지 않는다.

## 핵심 drawing 원칙

아래 항목은 direct-measurement layout의 **목표 acceptance contract**이며 모든 stock workflow가 이미
구현한다는 뜻이 아니다. 현재 Phase 1은 실제 pad macro를 보존하지 않지만, 계산된 DUT–Pad polyline의
각 segment를 multi-rail mesh로 compile한다. 실제 Pad macro와 corpus 기반 transistor adapter를 이
route에 연결한 target-process E2E는 아직 없다.

- 원본과 reference를 변경하지 않고 새 output만 만든다.
- DBU와 `(layer, datatype)`을 명시하며 display color로 production layer를 추측하지 않는다.
- Routing은 horizontal/vertical Manhattan geometry만 사용한다.
- Width는 current flow의 단축, length는 장축이며 애매하면 사용자 확인을 받는다.
- Direct-measurement 장거리 single rail은 금지한다.
- 가능한 corridor를 넓게 쓰는 parallel rail, repeated cross-tie와 multiple Pad landing을 사용한다.
- Mesh interface는 aligned staged expansion과 자연스러운 full-width 90° joint로 만든다.
- Source/Drain contact는 legal rule 안에서 최대화하고 device width 증가에 따라 늘린다.
- Mesh 형태는 낮은 기생저항을 지향하지만 extracted-RC/EM evidence 없이 최적이라고 주장하지 않는다.
- First metal이 불가능하면 자동 단순화하지 않고 explicit multi-metal escalation을 요청한다.

상세 계약은 [contracts-and-production.md](docs/contracts-and-production.md)에 있다.

## Profile 요약

| Profile | Frame | DBU | Pad | 용도 |
|---|---|---:|---|---|
| Generic | 사용자 입력 | 사용자/PDK | 사용자 입력 | 범용 nonproduction drawing |
| SLN001 Kelvin | 2000×54 µm | 0.00025 µm | 25×40 µm | 6-split M1 reference 재현 |

Kelvin reference:

```text
artifacts/SLN001_kelvin_m1/SLN001_kelvin_m1_aligned_force_pad_joint_v15.gds
```

SLN001의 frame, DBU, M1 mapping은 해당 regression profile의 값이며 전역 default가 아니다.
새 공정에는 이 값을 복사하지 않고 onboarding 결과를 사용한다.

## Transistor context 기본값

이 절은 planning/context policy다. Stock checkout에는 실제 transistor geometry adapter가 없으며
conceptual scaffold가 이 adapter를 대신하지 않는다.

사용자 요청에 따라 single-transistor DUT는 기본적으로 window를 `same_as_measured` array로
채우고, 주변 소자는 routing하지 않으며 compatible neighbor는 diffusion을 공유한다. Array edge
5 µm 안쪽의 balanced center region에서 1개를 기본 측정하고 요청 시 여러 개를 선택한다.
`standard_cell_like`는 `n/p/p/n` sequence와 cell height가 필요하다.

이 기본값은 production-safe context라는 의미가 아니다. 실제 LDE/LOD/WPE/STI/dummy/guard-ring과
silicon correlation은 process별 근거가 필요하다.

## Measurement와 shared Pad

MeasurementManifest는 exact layout hash에 다음 mapping을 묶는다.

```text
dut → terminal → net → Pad → probe pin → instrument channel → electrical role
```

Multi-DUT shared Pad는 active DUT, serial/simultaneous mode와 모든 inactive terminal의
`force|float|ground|guard|follow_shared_pad` 상태가 필요하다. Active terminal과 같은 물리 Pad의
inactive terminal은 `follow_shared_pad`로 연결해야 한다. 같은 Pad의 state/stimulus 충돌은
measurement binding을 거부하지만 GDS drawing은 보존한다.

Stimulus/bias는 source mode, typed `dc_value|linear_sweep|ac_amplitude` program, compliance,
polarity와 frequency를 DesignIntent와 정확히 대조한다. Timing, environment와 safety envelope도
동일해야 하며 manifest가 승인 한계를 완화할 수 없다. Active stimulus와 inactive `force|guard`
값은 승인된 voltage/current 한계를 넘으면 거부된다. Observable identity와 multiplicity도 함께
검사한다.

현재 이 manifest는 tester program이 아니며 silicon measurement/PCM completion도 아니다.

## 파일·오류 안전

- Relative input은 MCP `cwd`에서 resolve한다.
- Input은 snapshot/hash 후 읽는다.
- Persistent output은 새 basename과 host-controlled root를 사용한다. Generic drawing은 사용자가
  지정한 기존 parent directory에 쓸 수 있으므로 같은 정책 범위로 간주하지 않는다.
- 공개 file writer와 content-addressed directory writer는 fsync된 sibling stage를 create-only로
  publish한다. 지원 local filesystem의 same-target 경쟁에서는 첫 winner를 보존한다.
- 단일 writer는 file write와 fresh KLayout reload 후에만 성공한다.
- Persistent manifest는 append-only, content-addressed ancestry를 사용한다.
- Job ID는 lowercase `[a-z0-9_-]`만 허용하고 Windows device alias를 거부한다.
- Generation은 verified staging manifest를 먼저 남기고 sibling stage를 create-only로 final에 승격한다.
- 동일 local job의 manifest-head append는 OS file lock과 expected-parent 비교로 직렬화한다.
- 동일 digest의 content object는 idempotent하게 재사용하고, 같은 이름의 다른 content는 conflict로
  거부한다.
- 보장 범위는 same-host local NTFS/ext4/XFS 계약이다. NFS/SMB/multi-host writer는 doctor에서
  fail-closed하며 실제 ext4/XFS와 unsupported mount qualification은 아직 남아 있다.
- Expected 업무 오류는 MCP `isError=true`와 code/message/details/next_action을 반환한다.

대표 복구:

| 상태 | 조치 |
|---|---|
| KLayout 미탐지 | `KLAYOUT_EXE` 수정 후 host 재시작 |
| Output 존재 | 덮어쓰지 말고 새 이름 사용 |
| Top ambiguous | 정확한 top cell을 사용자에게 확인 |
| Off-grid | 승인 grid에 정확히 snap |
| Approval backend 없음 | Stock의 정상 fail-closed; approval을 지어내지 않음 |
| Shared-Pad state 충돌 | Physical Pad 기준 상태를 맞추거나 serial로 분리 |
| Stale hash | 기존 artifact 수정 대신 새 snapshot/job 생성 |
| `generation_staged`/`drawing_complete` 중단 | 같은 approval과 exact output filename으로 `teg_generate` 재호출; engine은 재실행하지 않음 |

## 테스트

```powershell
uv run --frozen --extra dev pytest -q
uv run --frozen --extra dev python -m compileall -q src tests examples
```

정적 pass count는 현재 commit의 release evidence가 아니다. 로컬 결과와 동일 SHA의 원격 CI 상태를
각각 확인하고, 알려진 기준선은 [Current capability boundaries](docs/current-capability-boundaries.md)와
[Development and validation](docs/development.md)을 따른다.

상세 검증 범위, 구조, CI와 roadmap은 [development.md](docs/development.md)에 있다.

## 문서

- [비전문가를 위한 프로젝트 설명](docs/project-eli5.md)
- [타깃 공정 온보딩 절차](onboarding.md)
- [보존된 최종 예제와 용도](examples/README.md)
- [실행 가능성과 한계를 구분한 사용자 시나리오](scenario.md)
- [Workflows and examples](docs/workflows.md)
- [현재 구현 capability와 known limitations](docs/current-capability-boundaries.md)
- [Contracts and production boundaries](docs/contracts-and-production.md)
- [Development and validation](docs/development.md)
- [Historical external review](feedback.md)와 [historical response](answer.md)
- [현재 upgrade plan](upgrade_plan.md)

README에는 처음 실행에 필요한 현재 사실만 유지한다. 긴 profile 사양, production 계약,
개발 기록은 위 세 문서에서 관리하며 같은 내용을 여러 문서에 반복하지 않는다.

## 공식 참고자료

- [KLayout Python API](https://www.klayout.de/doc-qt5/programming/python.html)
- [KLayout PCell programming](https://www.klayout.de/doc/programming/python.html)
- [KLayout command-line options](https://www.klayout.de/command_args.html)
- [Model Context Protocol tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)
