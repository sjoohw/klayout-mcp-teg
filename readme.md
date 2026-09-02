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

## 현재 가능한 것

| 사용자 결과 | Stock checkout | 의미 |
|---|---|---|
| GDS/OAS inspect·compare | 가능 | 입력을 바꾸지 않고 hierarchy/layer/geometry 관측 |
| Generic Manhattan drawing | 가능 | 명시적 DBU/layer/geometry로 새 nonproduction file 생성 |
| Kelvin regression example | 가능 | 보존된 project reference 범위에서만 유효 |
| PCellizer split batch | 제한 지원 | 직접 선택한 box 한 개·parameter 한 개, flatten 없이 hierarchy copy-on-write |
| Reference Library | 가능 | Full GDS hash 보관과 사용자 KLayout confirmation |
| Reference style 추출 | 가능 | hierarchy/layer/직교성/치수 빈도 관측; rule·net·전기 특성 추론 없음 |
| Persistent `teg_intake` | 가능 | Exact profile/version/family의 draft/job 저장 |
| Persistent plan/generate/verify | Host 통합 필요 | Trusted approval verifier와 process generation engine 필요 |
| Foundry sign-off·PCM release | 불가 | 실제 PDK/deck/probe/scribe/조직 정책 필요 |

최근 무결성 보강으로 stream file과 `workflow://` 문서의 `teg_status` 재해시,
DesignIntent↔MeasurementManifest actual source/program/compliance/timing/safety 결속,
host-policy-selected external evidence 결속, durable generation staging과 job별 append 직렬화가 구현됐다.
`signoff_evidence_approved`는 layout evidence 승인일 뿐이며 `production_ready`를 true로 만들지 않는다.

현재 가장 중요한 미비사항:

- 조직 signoff policy 주입 지점은 구현됐지만 stock host에는 policy가 설정되지 않는다.
- 실제 stdio `teg_*`와 host-injected verifier/engine을 함께 재시작하는 E2E가 없다.
- Actual foundry scribe/probe/de-embedding/tester/PCM 계약과 adapter가 없다.

이 항목은 [production 계약](docs/contracts-and-production.md)과
[개발 우선순위](docs/development.md)에 상세히 기록한다.

## 설치

필수 환경:

- Python 3.11 이상.
- `uv`.
- KLayout 0.30.0 이상. 현재 검증 버전은 0.30.10.

Repository root에서:

```powershell
uv sync --extra dev
uv run python --version
uv run python -c "from klayout_mcp.klayout_adapter import find_klayout_executable; print(find_klayout_executable())"
```

일반 MCP host 설정:

```json
{
  "mcpServers": {
    "klayout-drawing": {
      "command": "uv",
      "args": ["run", "klayout-teg-mcp"],
      "cwd": "C:\\absolute\\path\\to\\klayout-auto",
      "env": {
        "KLAYOUT_EXE": "C:\\absolute\\path\\to\\klayout_app.exe",
        "KLAYOUT_MCP_TOOL_MODE": "expert"
      }
    }
  }
}
```

Host가 `cwd`를 지원하지 않으면 `uv --directory <repository-root> run klayout-teg-mcp`를
사용한다. 직접 실행했을 때 아무 문구 없이 대기하는 것이 정상이다. Transport는 stdio이며
보통 MCP host가 필요할 때 process를 시작하고 종료한다.

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
| `expert` | 전체 기능. 기본값이지만 전체 기능을 비교할 수 있는 모델/operator만 권장 |
| `facade` | `server_status`, `teg_intake/status/plan/generate/verify` |
| `drawing` | `server_status`, draw/inspect/style/compare, mesh/contact planner |

오타가 난 mode는 시작 시 실패한다. 실제 호출 가능 목록은 MCP `tools/list`를 기준으로 한다.
작은 모델의 persistent 작업은 `facade`, 단순 geometry 작업은 `drawing`을 우선 사용하고,
PCellizer/reference/profile 도구를 함께 선택해야 할 때만 `expert`를 사용한다.

## 어떤 workflow를 선택할까

| 목적 | 시작점 |
|---|---|
| 기존 layout 읽기 | `inspect_layout` |
| 기존 layout의 관측 style 추출 | `extract_layout_style` |
| 두 layout 비교 | `compare_layouts` |
| 명시적 box/text/instance/boolean drawing | `draw_manhattan_layout` |
| 불완전한 transistor/resistor/capacitor TEG 요청 | `plan_direct_measurement_teg` |
| Kelvin reference 재현 | `plan_kelvin_m1_routing` |
| Existing GDS parameterization | `inventory_pcellizer_hierarchy` |
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

### 3. Atomic nonproduction drawing

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
다시 `inspect_layout`에 전달해 top/DBU/layer/bbox를 확인한다. 같은 파일명은 덮어쓰지 않는다.

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
- Output은 새 basename과 host-controlled root만 허용한다.
- Atomic write와 fresh KLayout reload 후에만 성공한다.
- Persistent manifest는 append-only, content-addressed ancestry를 사용한다.
- Job ID는 lowercase `[a-z0-9_-]`만 허용하고 Windows device alias를 거부한다.
- Generation은 verified staging manifest를 먼저 남기고 sibling temp+replace로 final을 승격한다.
- 동일 job의 manifest append는 OS file lock과 expected-parent 비교로 직렬화한다.
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
uv run --extra dev pytest -q
uv run --extra dev python -m compileall -q src tests examples
```

Current local snapshot:

```text
Windows / Python 3.13.5 / KLayout 0.30.10
644 passed, 0 skipped, 1 upstream warning
compileall passed
```

상세 검증 범위, 구조, CI와 roadmap은 [development.md](docs/development.md)에 있다.

## 문서

- [타깃 공정 온보딩 절차](onboarding.md)
- [보존된 최종 예제와 용도](examples/README.md)
- [실행 가능성과 한계를 구분한 사용자 시나리오](scenario.md)
- [Workflows and examples](docs/workflows.md)
- [Contracts and production boundaries](docs/contracts-and-production.md)
- [Development and validation](docs/development.md)
- [최신 외부 검토 원문](feedback.md)과 [조치 기록](answer.md)

README에는 처음 실행에 필요한 현재 사실만 유지한다. 긴 profile 사양, production 계약,
개발 기록은 위 세 문서에서 관리하며 같은 내용을 여러 문서에 반복하지 않는다.

## 공식 참고자료

- [KLayout Python API](https://www.klayout.de/doc-qt5/programming/python.html)
- [KLayout PCell programming](https://www.klayout.de/doc/programming/python.html)
- [KLayout command-line options](https://www.klayout.de/command_args.html)
- [Model Context Protocol tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)
