# KLayout TEG MCP

KLayout의 내장 Python API `pya`를 사용해 wafer scribe lane용 21-site TEG를
분석·계획·검증·조립하는 stdio MCP 서버다.

이 문서가 프로젝트의 단일 기준 문서다. 현재 사양, 사용법, 설계 원칙, 검증 근거,
개선 이력, 남은 미비사항을 모두 여기에서 관리한다.

## 1. 현재 상태

기준일은 2026-08-23 KST다.

| 구분 | 평가 | 의미 |
|---|---:|---|
| 비생산 자동화 완성도 | 약 82/100 | 분석, 계획, conceptual geometry, GDS/OAS 입력과 GDS 전달 경로 검증 가능 |
| 실제 공정 투입 완성도 | 약 30/100 | 실제 DUT·공정·전기 연결 정보 부재 |
| 통합 종합 완성도 | 약 73/100 | 안전성과 MCP 계약은 안정화, 일부 구조·실행 증거 작업 잔존 |

현재 결과는 항상 비생산용이다.

```text
production_ready: false
geometry_status: conceptual_scaffold
electrical_connectivity_verified: false
```

실제 sample DUT와 공정 규칙을 검증하기 전에는 fabrication mask로 사용하면 안 된다.

현재 검증 기준:

- KLayout: 0.30.10.
- 지원 기준: KLayout 0.30.0 이상.
- Python: 3.11 이상.
- MCP SDK: 1.27 이상, 2.0 미만.
- 전체 회귀 테스트: 136 passed.
- Python `compileall`: passed.
- pytest 종료 warning: 외부 `pydantic-settings 2.15.0`의
  `IncompleteFieldDefinitionWarning` 1건.

KLayout 공식 다운로드 페이지 기준 현재 버전도 0.30.10이다.

## 2. 범위와 비범위

지원 범위:

- Padset GDS/OAS의 계층적 M1 분석.
- 25개 Pad 검출과 21개 DUT slot 계산.
- S/D/G/B landing 추출 및 unresolved 상태 보고.
- Pad·slot·landing 상태 overlay PNG 생성.
- Transistor array와 routed-unit pattern 계획.
- DUT PCell parameter 및 terminal contract 조회.
- 비생산용 DUT geometry 생성.
- Sample DUT의 cell/layer/shape/text inventory.
- 21-site parameter sequence와 variant 재사용 계획.
- M1/Poly/Contact/landing 핵심 rule guardrail.
- Labeled M1 short/open component guardrail.
- 비생산용 editable hierarchy 및 static GDS 조립.
- Standalone KLayout Python PCell source export.
- 실제 stdio MCP structured result와 tool-error 전달.

비범위:

- Foundry sign-off DRC와 rule deck 실행.
- LVS, netlist extraction, device extraction.
- Display color나 layer name을 이용한 production layer 추측.
- Sample 설명 없이 geometry만 보고 transistor 물리 의미 추측.
- 현재 conceptual geometry의 생산 승인.
- M2 이상을 이용한 DUT 외부 routing.

## 3. 고정 TEG profile

고정 값의 single source of truth는 `src/klayout_mcp/profiles.py`의
`TegProfile`이다.

| 항목 | 값 |
|---|---:|
| TEG 크기 | 2000 um × 60 um |
| Pad 개수 | 25 |
| Pad 크기 | 40 um × 40 um |
| Pad pitch | 80 um |
| Source/Drain Pad | 1~22 |
| 홀수 site Gate Pad | 23 |
| 짝수 site Gate Pad | 24 |
| Body Pad | 25 |
| DUT site | 21 |
| 기본 device window | 35 um × 40 um |
| DUT 외부 route layer | M1 only |
| DBU | 입력 Padset의 `layout.dbu` |

Site `i`의 고정 mapping:

```text
Source = Pad i
Drain  = Pad i + 1
Gate   = Pad 23 if i is odd, otherwise Pad 24
Body   = Pad 25
i      = 1..21
```

기본 수평 profile의 기준 좌표:

```text
x_pad(n) = 40 + 80 * (n - 1)
y_pad(n) = 30
n = 1..25

x_dut(i) = 80 * i
y_dut(i) = 30
i = 1..21
```

실제 분석 결과의 Pad bbox와 center가 위의 이상 좌표보다 우선한다. 입력 layout을
이 좌표로 강제 이동하지 않는다.

## 4. 핵심 용어

- `Padset`: Pad와 공통 M1 route를 포함한 고정 template layout.
- `DUT slot`: 인접 Source/Drain Pad 사이에서 DUT를 배치할 영역.
- `device_window_um`: transistor array가 들어갈 수 있는 DUT-local 영역.
- `routing_boundary_um`: S/D/G/B landing stub까지 허용하는 DUT-local 경계.
- `anchor_um`: terminal route 방향을 정의하는 경계 기준점.
- `landing_bbox_um`: DUT route와 Padset metal이 최소 면적으로 겹쳐야 하는 영역.
- `variant`: 하나의 정규화된 DUT parameter set에 대응하는 재사용 cell.
- `conceptual scaffold`: 자동화 경로를 시험하기 위한 합성 geometry. 공정 소자가 아님.
- `static export`: PCell library 없이도 열리는 ordinary geometry 전달본.

## 5. 필수 입력

Padset 분석:

- 기존 Padset GDS/OAS.
- 최소 `m1`이 명시된 layermap YAML/JSON.
- Top cell이 여러 개면 명시적 `top_cell`.

Conceptual PCell export:

- `m1`, `active`, `poly`, `contact`가 서로 다른 layer/datatype인 layermap.
- `confirm_conceptual_export: true`.

Conceptual 21-site GDS 조립:

- Padset GDS/OAS.
- `m1`, `active`, `poly`, `contact`, `text`가 서로 다른 layermap.
- 1개 공통 DUT config 또는 정확히 21개 site config.
- 기존에 존재하지 않는 `.gds` output path.
- `confirm_conceptual_export: true`.

생산 후보 작업에는 추가로 다음이 모두 필요하다.

- 실제 sample DUT GDS/OAS.
- Sample의 device type과 parameter 의미.
- S/D/G/B terminal 위치와 내부 연결 설명.
- 승인된 공정 layermap.
- Minimum width/space/enclosure/overlap 등 foundry rule.
- 21-site sweep 값과 물리적 허용 범위.

Layermap 예:

```yaml
layers:
  m1: [10, 2]
  active: [1, 0]
  poly: [2, 0]
  contact: [3, 0]
  text: [100, 0]
```

Layer identity는 항상 `(GDS layer number, datatype)` 쌍이다. `.lyp` 색상은
production layer 근거로 사용하지 않는다.

## 6. 설치와 실행

개발 환경 설치:

```powershell
uv sync --extra dev
```

MCP 서버 실행:

```powershell
uv run klayout-teg-mcp
```

Transport는 stdio다.

KLayout 실행 파일 검색 순서:

1. `KLAYOUT_EXE` 환경 변수.
2. Windows LocalAppData KLayout.
3. Windows Program Files KLayout.
4. `PATH`의 `klayout`, `klayout_app.exe`, `klayout.exe`.

Linux csh:

```csh
setenv KLAYOUT_EXE /path/to/klayout
uv sync --extra dev
scripts/run-klayout-teg-mcp.csh
```

KLayout을 사용하지 않는 pure-planning tool만 호출할 때 `KLAYOUT_EXE`는 선택 사항이다.
Linux csh launcher는 CI job이 정의되어 있지만 현재 Windows host에서는 실제 실행하지
못했다. 이 항목은 미검증 상태다.

### 로컬 skill 의존성

MCP 서버 실행은 이 컴퓨터의 Codex skill, `.agents/skills/**`, `SKILL.md`, 또는
`%USERPROFILE%\.codex\skills`를 전제로 하지 않는다. 해당 파일은 개발·리뷰 시 agent에게
지침을 주는 보조 자료이며 서버 프로세스가 읽거나 import하지 않는다.

런타임에 필요한 것은 Python 3.11 이상, `mcp`, `PyYAML`, 그리고 KLayout 기반 tool을
호출할 경우의 KLayout 실행 파일이다. 입력 작업에는 별도로 padset GDS/OAS와 명시적
layermap이 필요하다. 따라서 다른 컴퓨터에서도 package와 KLayout 및 입력 파일만
준비하면 동일하게 실행할 수 있다.

## 7. MCP tool surface

| Tool | 역할 | 환경 변경 |
|---|---|---|
| `server_status` | 버전, 지원 범위, capability | 없음 |
| `analyze_pad_boxes` | Pad bbox에서 slot 계산 | 없음 |
| `analyze_padset` | GDS/OAS M1, Pad, slot, landing 분석 | 없음 |
| `render_boundary_overlay` | 분석 결과를 새 PNG로 생성 | 새 파일 추가 |
| `select_routed_units` | 측정 대상 transistor index 선택 | 없음 |
| `plan_transistor_array` | Centered array와 selection 계획 | 없음 |
| `describe_dut_pcell` | Parameter와 terminal contract 조회 | 없음 |
| `generate_dut_geometry` | Conceptual DUT geometry 생성 | 없음 |
| `inspect_sample_dut` | Sample layout inventory | 없음 |
| `plan_teg_dut_sequence` | 21-site mapping과 variant 계획 | 없음 |
| `verify_design_rules` | 핵심 geometry/connectivity guardrail | 없음 |
| `assemble_teg` | 새 conceptual 21-site GDS 생성 | 새 파일 추가 |
| `export_pcell_code` | 새 standalone PCell script 생성 | 선택적으로 새 파일 추가 |

### MCP result 계약

모든 tool은 `ok`가 필수인 output envelope schema를 노출한다.

성공:

```json
{
  "ok": true
}
```

- MCP `isError: false`.
- `structuredContent.ok: true`.
- 동일 JSON의 `TextContent`도 제공해 구형 client 호환성을 유지한다.

예상 가능한 업무 오류:

```json
{
  "ok": false,
  "code": "ACTIONABLE_CODE",
  "message": "What failed.",
  "details": {},
  "next_action": "How to correct it."
}
```

- MCP `isError: true`.
- `structuredContent.ok: false`.
- `code`, `message`, `details` 필수.
- 가능한 경우 `next_action` 제공.

Tool annotation은 read-only와 additive-write를 구분한다. Output tool은 기존 artifact를
덮어쓰지 않으므로 `destructiveHint: false`다.

## 8. Padset 분석

`analyze_padset`은 원본을 임시 snapshot으로 한 번 복사한다. 동일 snapshot을 한 KLayout
process에서 한 번 읽어 M1, Pad, slot, landing을 분석한다.

반환 정보:

- 원본 path, snapshot SHA-256, byte count.
- KLayout version, DBU, top cell.
- Pad 1~25의 bbox, center, component ID.
- DUT site 1~21의 origin, device window, routing boundary.
- S/D/G/B Pad mapping.
- Landing polygon, bbox, area, component ID, resolved 상태.
- M1 shape inventory와 unresolved landing 목록.
- `layout_read_count: 1`.
- `input_layout_modified: false`.

Pad 검출 방식:

- Box/path/polygon을 recursive M1 `Region`으로 정규화.
- 40 um square core와 row/pitch contract 검사.
- Narrow route가 붙은 solid Pad는 Region opening으로 core 복원.
- Mesh/slotted Pad는 component hull fallback 사용.
- 여러 후보 row 또는 여러 top cell은 자동 선택하지 않고 structured error 반환.
- Pad가 하나의 M1 component로 합쳐지면 `PAD_SHORT_DETECTED`.

현재 analyzer 제한:

- 고정 profile은 수평 Pad row만 지원.
- 90도 회전/수직 TEG profile은 미지원.
- Morphological opening은 Pad보다 route가 충분히 좁다는 전제가 있음.
- Complex arbitrary-angle conductor의 의미는 foundry extraction으로 검증하지 않음.

### SLN001 padset 실파일 검증

2026-08-24에 로컬 `SLN001_padset.gds`를 KLayout 0.30.10과 실제 stdio MCP로
검증했다. 파일은 로컬 검증 artifact이며 GitHub 배포물에는 포함하지 않는다.

- SHA-256: `e4d322da470627c2de0e7c23d1987dc6c571c5503a24323f1b870ea2156ecc0c`.
- Top cell: `SLN001_PADSET`; DBU: 0.00025 um; bbox: 2000 um × 54 um.
- Cell 2개, top-level mesh-pad instance 25개.
- 생성 자료에 명시된 M1 `(15, 0)`과 outline `(62, 20)`을 사용했으며 display
  정보로 layer 역할을 추측하지 않음.
- M1 raw box 2000개를 25개 독립 component와 25개 pad로 정규화.
- 25 pads, 21 DUT slots, pad short 0건.
- Source/Drain landing 42개 resolved; Gate/Body landing 42개 unresolved.
- 21-site 기본 parameter sequence는 1개 variant로 계획되지만
  `all_landings_resolved: false`이므로 production assembly 준비 완료가 아님.
- Overlay marker: pads 25, slots 21, resolved 42, unresolved 42, labels 46.
- 분석 전후 원본 SHA-256 동일; overlay는 별도 PNG로 생성.

이 검증에서 stdio MCP가 KLayout subprocess에 프로토콜 stdin을 상속해 작은 GDS도
timeout되는 문제를 발견했다. Worker stdin을 `DEVNULL`로 격리한 뒤 동일 stdio 호출이
`isError: false`로 완료되는 것을 재검증했다.

### Overlay

`render_boundary_overlay`는 KLayout hidden view marker를 이용한다.

- Pad: 파랑.
- DUT slot: 주황.
- Resolved landing: 초록.
- Unresolved search band: 빨강.
- Pad/Site label 포함.
- Marker는 production layer에 삽입하지 않음.
- 기존 PNG는 `OUTPUT_EXISTS`로 거부.
- 원본 Padset은 수정하지 않음.

## 9. DUT PCell contract

좌표계:

- 단위: um.
- Local origin: DUT slot center.
- +X: right.
- +Y: top.

Parameter schema:

| 이름 | 타입 | 기본값 | 의미 |
|---|---|---:|---|
| `w_um` | float | 1.0 | Conceptual transistor width |
| `l_um` | float | 0.1 | Conceptual gate length |
| `array_rows` | int | 4 | Row count |
| `array_cols` | int | 8 | Column count |
| `pitch_x_um` | float | 2.0 | X pitch |
| `pitch_y_um` | float | 2.0 | Y pitch |
| `routed_device_count` | int | 10 | M1 tap 대상 unit 수 |
| `m1_width_um` | float | 0.4 | Conceptual route width |
| `m1_overlap_um` | float | 0.2 | Boundary landing overlap |
| `device_window_um` | box | `[-17.5,-20,17.5,20]` | Device 허용 영역 |
| `routing_boundary_um` | box | `[-20,-20,20,20]` | Route 허용 경계 |

검증 규칙:

- 모든 dimension은 finite number.
- Width, length, pitch, M1 width/overlap은 양수.
- Row/column/routed count는 integer.
- `routed_device_count <= array_rows * array_cols`.
- Device window는 routing boundary 안에 포함.
- Array bbox는 device window 안에 포함.
- Sequence 형태의 window/boundary는 생성 시 `Box`로 정규화.
- Boolean과 text geometry 입력은 숫자로 암묵 변환하지 않고 거부.
- Routed unit은 device-window 외곽 5 um inset 안에서 선택.
- Inset을 자동 완화하지 않음.
- Gate/Body collector는 routing boundary 내부로 제한.

Terminal contract:

| Terminal | Net | Boundary | 방향 |
|---|---|---|---|
| S | source | left | `[-1, 0]` |
| D | drain | right | `[1, 0]` |
| G | gate | top | `[0, 1]` |
| B | body | bottom | `[0, -1]` |

각 terminal은 `layer_role: m1`, anchor, landing bbox, route width, minimum overlap을
명시한다. 현재 terminal metadata는 contract이지 실제 공정 connectivity 증명이 아니다.

## 10. Canonical geometry와 routed-unit 선택

`dut_geometry.build_dut_geometry`가 pure Python, live PCell, generated standalone PCell,
assembly worker의 single source of truth다.

같은 parameter 입력의 다음 경로가 semantic geometry를 공유한다.

- Pure geometry response.
- Explicit layer map을 요구하는 live `register_teg_library(layers)` PCell.
- Export된 standalone `# $autorun` PCell.
- 21-site assembly DUT variant.

Routed-unit 선택:

1. Device window 외곽 5 um를 제외.
2. 남은 영역에 target point를 균등 배치.
3. 각 target에서 가장 가까운 미선택 unit을 선택.
4. Tie는 낮은 1-based input index.
5. Count 1은 center-nearest unit.
6. Eligible count 초과는 오류.
7. 같은 topology는 같은 routed-index pattern 재사용.

`routed_device_count`는 실제 M1 shape inventory를 변경한다. 2×2 array에서 count 1은
M1 shape 10개, count 4는 16개라는 회귀 검사가 있다.

DBU 원칙:

- Padset의 `layout.dbu`를 상속.
- User-facing dimension은 micron `DBox` 등 `D*` object 사용.
- Integer DBU가 필요할 때만 의도적으로 변환.
- 음수/양수 좌표에 비대칭 절삭을 적용하지 않음.
- Geometry 변경은 fresh reload와 `Region` 의미 비교로 확인.

## 11. PCell source export

`export_pcell_code`는 KLayout GUI의 `pymacros`에서 자동 등록 가능한 standalone Python
script를 반환한다.

안전 조건:

- `layermap_path` 필수.
- M1/Active/Poly/Contact layer/datatype이 모두 명시되고 서로 달라야 함.
- `confirm_conceptual_export: true` 필수.
- Script header에 non-production 경고 포함.
- `PRODUCTION_READY = False` 포함.
- 기존 script는 `OUTPUT_EXISTS`로 보존.
- Write failure는 `OUTPUT_WRITE_FAILED`와 조치 방법 반환.
- GUI view, selection, timestamp에 의존하지 않음.
- 같은 parameter map은 KLayout PCell variant 재사용.

Standalone script는 canonical geometry source를 포함하므로 live/assembly와 별도로
hand-maintained한 geometry 상수가 없다.

## 12. Sample DUT inventory

`inspect_sample_dut`은 sample에서 생산 의미를 추측하지 않고 구조만 수집한다.

출력:

- Sample path, SHA-256, DBU, format, bbox.
- Top cell과 전체 top cell 목록.
- Cell별 direct instance와 child cell.
- Layer별 recursive shape count, 종류, bbox, area.
- Text string, origin, layer/datatype.
- Layermap role coverage.
- 사용됐지만 mapping되지 않은 layer.
- PCell variant 여부.

`S`, `D`, `G`, `B` text가 있어도 terminal connectivity로 자동 확정하지 않는다.
Geometry에서 PCell parameter나 sweep 의미도 추측하지 않는다.

현재 sample workflow의 핵심 미비:

- 실제 sample DUT가 workspace에 없음.
- Sample 설명에서 PCell source를 자동 생성하는 기능 없음.
- Sample-versus-generated full layer XOR 미구현.
- Text와 geometry를 electrical netlist로 연결하는 extraction 없음.

## 13. 21-site sequence와 variant 계획

`plan_teg_dut_sequence` 입력:

- 분석된 `dut_slots` 정확히 21개.
- `{site: N, parameters: {...}}` 정확히 21개.
- 선택적인 common `defaults`.

검사 항목:

- Site 1~21 누락과 중복.
- 각 slot `origin_um`의 2개 finite numeric coordinate 계약.
- Source/Drain, odd/even Gate, Body Pad mapping.
- Unknown parameter와 잘못된 parameter type/range.
- 공통 rows/columns/pitches/routed-count topology.
- 공통 routed-index pattern.
- Site별 S/D/G/B landing readiness.
- Input 순서와 무관한 stable site order.

동일한 정규화 parameter set은 `VARIANT_001` 같은 하나의 ID를 공유한다. 다른
parameter set만 새 variant를 만든다.

Unresolved landing이 있어도 conceptual plan은 반환하지만 production assembly 준비가
완료됐다고 표시하지 않는다.

## 14. Conceptual TEG assembly

`assemble_teg`는 실제 DUT가 없는 상태에서 placement와 전달 경로만 검증하는 도구다.

동작:

1. Padset을 immutable snapshot으로 읽음.
2. 25-Pad/21-slot profile 분석.
3. Site-local device/routing window 계산.
4. Canonical geometry로 unique DUT variant 생성.
5. 21개 site origin에 ordinary cell instance 배치.
6. 왼쪽에 unmirrored 90-degree TEG label 추가.
7. 임시 sibling GDS에 기록.
8. 새 `Layout`으로 fresh reload.
9. Semantic 검증 후 기존에 없던 목적지로 atomic promotion.

`export_static: false`:

- Editable ordinary-cell hierarchy 유지.
- 동일 parameter set은 하나의 DUT cell 공유.
- 21 site/3 parameter set에서 DUT variant cell 정확히 3개.
- Fresh reload 후 variant별 Active/Poly/Contact/M1 `Region` XOR 0 확인.

`export_static: true`:

- Top hierarchy를 static geometry로 flatten.
- Fresh reload 후 top-level instance 0 확인.
- PCell dependency 0 확인.
- PCell library 없이 독립적으로 열림.

공통 round-trip 검사:

- DBU.
- 필수 layer/datatype.
- Top cell과 non-empty bbox.
- 90-degree, unmirrored TEG label 정확히 1개.
- Output GDS fresh reload.
- Input Padset SHA-256 불변.
- 기존 destination 비덮어쓰기.

현재 assembly M1은 conceptual이며 Source/Drain 내부 open을 보고한다. Padset landing과
DUT 내부 bus가 sample-derived 방식으로 연결되기 전에는 생산 결과가 아니다.

## 15. Design-rule와 connectivity guardrail

`verify_design_rules`는 labeled axis-aligned box에 대한 사전 guardrail이다.

검사:

- M1 minimum width.
- M1 orthogonal spacing.
- M1 diagonal Euclidean edge spacing.
- 서로 다른 labeled net의 overlap/touch short.
- Poly minimum width.
- Contact minimum size.
- S/D/G/B boundary 방향에 맞는 landing overlap.
- Net별 M1 connected component 수와 open.
- Corner 한 점만 맞닿은 M1 box는 연결로 인정하지 않음.
- Edge contact는 한 축에 양의 겹침 길이가 있을 때만 연결로 인정.
- NaN, infinity, invalid tolerance.

상태는 분리해서 반환한다.

- `design_rules_clean`: 설정된 geometry rule 위반이 없음.
- `electrical_connectivity_verified`: labeled M1 component가 연결됨.

제한:

- 이 검사는 LVS가 아님.
- Labeled M1 box가 실제 transistor terminal이라는 것을 증명하지 않음.
- Arbitrary polygon/path sign-off DRC를 대체하지 않음.
- Enclosure, density, antenna, well, implant 등 foundry rule은 없음.
- 현재 default conceptual geometry는 Source/Drain open을 의도적으로 보고함.

## 16. 파일과 실행 안전 규칙

- 원본 GDS/OAS와 layermap은 읽기 전용으로 취급.
- File-based 분석은 immutable snapshot 사용.
- Output path가 input과 같으면 거부.
- 기존 output은 덮어쓰지 않음.
- 임시 output을 fresh reload하고 검증한 뒤 승격.
- Layer/datatype을 추측하거나 silently remap하지 않음.
- 여러 top cell 중 하나를 임의 선택하지 않음.
- Geometry가 window를 넘으면 자동 축소하지 않음.
- Routed inset을 자동 완화하지 않음.
- Unresolved landing과 open을 숨기지 않음.
- Conceptual export는 명시적 opt-in 필요.
- `production_ready: true`는 Phase 4 검증 전 사용 금지.

## 17. 프로젝트 구조

```text
src/klayout_mcp/
├─ server.py              MCP tool orchestration
├─ mcp_protocol.py        output schema, isError, tool annotations
├─ profiles.py            fixed TEG profile
├─ errors.py              AnalysisError envelope
├─ geometry.py            unit-explicit Point/Box
├─ padset.py              pure Pad/slot analysis
├─ selection.py           routed-unit selection
├─ dut_geometry.py        canonical DUT geometry and contract
├─ drc_guardrails.py      geometry/connectivity guardrail
├─ assembly.py            21-site planning
├─ layermap.py            explicit layer parser
├─ pcell_library.py       live/generated PCell integration
├─ klayout_adapter.py     immutable snapshot and subprocess bridge
├─ klayout_worker.py      KLayout database operations
├─ worker_overlay.py      hidden-view overlay operation
└─ worker_protocol.py     worker error envelope

tests/
├─ fixtures/              synthetic Padset and sample DUT builders
└─ test_*.py              pure, MCP, KLayout, E2E regression tests

.github/workflows/ci.yml  Windows/Ubuntu CI
scripts/                  Linux csh launcher
```

로컬 검증 중 생성된 `artifacts/`, 과거 자동화 로그·실행 파일, Codex 첨부파일과 개인
agent 지침은 배포 소스가 아니며 Git 추적 대상에서 제외한다.

실행 모델:

- MCP host는 일반 Python에서 실행.
- KLayout geometry/database 작업은 설치된 KLayout subprocess에서 실행.
- Request와 response는 temporary JSON file로 교환.
- Worker raw traceback은 사용자 API로 직접 노출하지 않음.

남은 구조 문제:

- `klayout_worker.py`에 integrated Padset, sample inventory, assembly가 아직 함께 있음.
- `server.py`에 일부 domain validation과 orchestration이 함께 있음.
- Overlay와 protocol/profile/error 분리는 완료했지만 operation/service 분리는 미완료.

## 18. 테스트와 CI

로컬:

```powershell
uv run --extra dev pytest -q
uv run --extra dev python -m compileall -q src tests
```

현재 전체 결과:

```text
136 passed, 1 upstream dependency warning
compileall passed
```

검증 범위:

- Pure geometry와 parameter validation.
- Pad/slot mapping과 input-order determinism.
- DRC boundary, diagonal spacing, short/open.
- KLayout 0.30.10 hierarchical GDS/OAS read.
- Box/path/polygon/mesh Pad normalization.
- Hidden-view PNG.
- Generated/live PCell geometry와 variant reuse.
- Pure 대비 live/generated layer별 `Region` XOR 0.
- Analyze → sequence → assemble → reload E2E.
- 21-site/1-variant와 21-site/3-variant hierarchy.
- Editable assembly variant별 layer XOR 0.
- Static output top-level instance와 PCell dependency 0.
- DBU, layer/datatype, label transform, SHA-256 불변.
- 실제 stdio `ClientSession`의 status, PCell contract, output schema와 `isError`.
- Null/invalid slot origin이 stdio에서 `INVALID_DUT_SLOT_ORIGIN`과 `isError: true`로 전달.
- KLayout subprocess timeout의 structured error.
- KLayout worker가 MCP stdio를 상속하지 않는지 검사.
- Existing artifact 비덮어쓰기.

CI matrix:

- Windows latest / Ubuntu latest.
- Python 3.11 / 3.13.
- 일반 matrix의 pure/unit 경로와 `pytest`, `compileall`.
- Ubuntu csh launcher job.
- Ubuntu 24 전용 KLayout 0.30.10 integration job.
- 공식 `.deb`와 게시된 checksum 검증 후 KLayout 설치.
- `QT_QPA_PLATFORM=offscreen`에서 전체 pytest 실행.

GitHub Actions workflow와 launcher는 작성되어 있다. 로컬 Windows 검증 결과는 위와
같으며, 원격 CI 실행 상태는 GitHub Actions run을 기준으로 확인한다.

## 19. 해결된 주요 리뷰 이슈

| 과거 문제 | 현재 상태 |
|---|---|
| `export_pcell_code`의 `os` 누락 | 해결 |
| 기존 PCell script 덮어쓰기 | Exclusive create와 structured error로 해결 |
| 음수 DBU 좌표 `int()` 절삭 | `DBox`/대칭 변환 및 실제 0.003 um DBU 검사로 해결 |
| Live PCell `produce_impl` 빈 구현 | Canonical geometry 삽입으로 해결 |
| Standalone S/D/G/B 원점 short 6건 | Pairwise short 0으로 해결 |
| Generated/live/assembly geometry drift | Canonical builder로 해결 |
| Assembly가 `routed_device_count` 무시 | Shape inventory 반영으로 해결 |
| Sequence planner가 Pad/parameter 오류 허용 | 21-site strict validation으로 해결 |
| Gate/Body overlap 축 오류 | `boundary_side` 기반으로 해결 |
| Diagonal spacing false negative | Euclidean edge spacing으로 해결 |
| DRC와 connectivity 상태 혼동 | 별도 status로 해결 |
| MCP 업무 오류가 `isError: false` | stdio E2E 기준 `isError: true`로 해결 |
| Generic MCP result 계약 | 공통 success/error output schema 적용 |
| 산재한 TEG magic number | `TegProfile`로 중앙화 |
| Host/worker error 규칙 이중화 | `AnalysisError` envelope로 통일 |
| Windows pytest cleanup warning | Workspace-local basetemp로 해결 |
| Frozen `DutParameters`의 box sequence 미정규화 | `__post_init__`에서 canonical `Box`로 정규화 |
| 문자열을 box coordinate sequence로 허용 | Text/bytes 입력 거부로 해결 |
| Corner-only M1 접촉을 연결로 판정 | 양의 edge overlap 요구로 해결 |
| Pad 검출 tolerance가 device fit까지 완화 | 물리적 gap 기준 strict fit으로 해결 |
| Null landing에서 `AttributeError` | `unresolved` 상태로 방어 처리 |
| Gate/Body collector의 routing boundary 초과 | Collector center clamp로 해결 |
| Live PCell의 임의 layer 기본값 | 네 역할의 explicit layer/datatype 입력으로 해결 |
| Timeout, OAS 입력, stdio non-status tool 미검증 | 회귀·통합 테스트 추가로 해결 |
| Null `origin_um`이 raw `TypeError` 발생 | `INVALID_DUT_SLOT_ORIGIN` structured error로 해결 |
| `Point`가 bool/text/NaN/Inf 좌표 허용 | 생성 시 finite numeric validation으로 해결 |
| 일부 expected error의 조치 안내 누락 | 모든 `AnalysisError` 생성 경로에 `next_action` 추가 |
| stdio MCP의 KLayout worker가 protocol stdin 상속 | `stdin=DEVNULL` 격리와 회귀 검사로 해결 |

## 20. 개선 과정과 체크포인트 요약

초기 기준선:

- 86 tests.
- 종합 완성도 약 55/100.
- Standalone terminal short, planner validation regression, DRC false negative,
  geometry drift, MCP error-contract 문제가 재현됨.

안전성 단계:

- Conceptual PCell/export opt-in.
- Explicit layermap와 distinct layer 요구.
- Existing output 보호.
- Strict 21-site Pad mapping/parameter/landing validation.
- 93 tests.

검증 단계:

- Gate/Body overlap, diagonal spacing, finite tolerance 수정.
- Labeled M1 connected component와 open 보고.
- `design_rules_clean`과 connectivity 분리.
- 101 tests.

Geometry/E2E 단계:

- Pure/live/generated/assembly canonical geometry 통합.
- Deterministic variant reuse.
- Editable/static fresh reload와 Region XOR.
- Synthetic Padset → analyze → plan → assemble → reload 연결.
- 102 tests.

MCP/유지보수 단계:

- MCP output schema, `isError`, annotation.
- Profile/error/overlay 모듈 분리.
- Windows/Ubuntu CI, csh launcher.
- Pytest warning 제거.
- 103 tests.

Feedback hardening 단계:

- Box parameter 정규화와 text/bool 입력 거부.
- Corner-only contact 분리와 pad physical-fit 일관성.
- Null landing 방어와 Gate/Body collector boundary clamp.
- Explicit live-PCell layer contract.
- Timeout, OAS input, 추가 stdio tool 회귀 검사.
- Ubuntu 24 KLayout 0.30.10 integration CI job 정의.
- 121 tests.

Feedback hardening 2단계:

- Slot origin을 정확히 두 개의 finite numeric micron coordinate로 검증.
- `Point` 좌표 불변식 추가.
- Pitch, layermap, timeout, KLayout response를 포함한 actionable error 일관성 감사.
- Direct API와 실제 stdio MCP 오류 회귀 검사.
- 135 tests.

SLN001 실파일 검증 단계:

- Mesh pad 25개와 21개 DUT slot을 실제 KLayout/stdio MCP 경로로 검출.
- Overlay를 렌더링하고 Source/Drain resolved, Gate/Body unresolved 상태를 시각 확인.
- MCP stdio를 상속한 KLayout worker timeout을 수정하고 회귀 검사 추가.
- 136 tests.

## 21. 미비사항과 우선순위

### P0 — 생산 전 필수 외부 입력

다음 항목은 코드만으로 해결할 수 없다.

- [ ] 실제 sample DUT GDS/OAS 확보.
- [ ] Sample device와 parameter 설명 승인.
- [ ] S/D/G/B terminal 및 내부 bus 설명 승인.
- [ ] Production layermap과 layer 역할 승인.
- [ ] Sweep parameter 의미와 21-site 값 승인.
- [ ] Foundry width/space/enclosure/overlap 규칙 승인.

이 입력이 없으므로 현재 가장 큰 위험은 geometry 버그가 아니라 “합성 geometry를 실제
소자로 오인하는 것”이다.

### P1 — 입력 확보 후 production gate

- [ ] Sample-versus-generated layer별 XOR.
- [ ] Sample terminal metadata와 generated terminal alignment 비교.
- [ ] 실제 Source/Drain/Gate/Body short/open 검증.
- [ ] DUT-to-Padset landing overlap 검증.
- [ ] Foundry DRC 실행 또는 승인된 결과 연동.
- [ ] LVS 또는 승인된 connectivity 증거.
- [ ] 21-site 실제 sweep 배치 검증.
- [ ] Production-candidate static GDS/OAS fresh reload.
- [ ] PCell library가 없는 독립 환경에서 열기.
- [ ] 독립 Layout QA review.

### P2 — 내부 유지보수

- [ ] `klayout_worker.py`의 Padset/sample/assembly operation 완전 분리.
- [ ] `server.py`에서 domain service와 MCP orchestration 완전 분리.
- [ ] Public tool별 성공 schema를 공통 envelope보다 더 구체화.
- [ ] MCP tool 등록 시 `pydantic-settings`의 unresolved forward-reference warning 원인
  확인 및 제거.
- [ ] Unexpected `OSError` 등 일반 예외의 actionable error 정규화 확대.
- [ ] Linux csh launcher 실제 실행.
- [ ] GitHub Actions 실제 run 확인.
- [ ] 공개 배포 라이선스 결정과 `LICENSE` 추가.
- [ ] `LAYOUT_VIEW_UNAVAILABLE` headless failure 경로 자동화 테스트.
- [ ] macOS 지원이 필요할 경우 application bundle 탐색 경로 추가.

### P3 — 기능 확장

- [ ] Sample 설명에서 Python PCell source 생성.
- [ ] Sample-derived S/D/G/B internal bus 생성.
- [ ] Arbitrary polygon/path connectivity 또는 extraction 연동.
- [ ] 90도 회전/수직 Pad row profile.
- [ ] Production OASIS export.
- [ ] 기존 TEG의 안전한 variant 교체 workflow.
- [ ] Property-based geometry test와 mutation test 확대.

## 22. Production-ready 완료 정의

다음을 모두 만족하기 전에는 `production_ready: true`를 사용할 수 없다.

1. Padset과 layermap 원본이 승인됨.
2. 실제 sample DUT와 parameter 설명이 있음.
3. 모든 production layer 역할이 명시됨.
4. S/D/G/B terminal과 내부 bus가 sample 또는 회로 자료로 확인됨.
5. Sample-versus-generated XOR 차이가 설명됨.
6. 21개 site의 Pad mapping과 parameter sweep가 승인됨.
7. 모든 landing이 minimum overlap을 만족함.
8. Short/open 검사가 통과함.
9. Foundry DRC와 LVS 또는 동등한 승인 증거가 있음.
10. Static output이 fresh reload되고 PCell dependency가 없음.
11. 입력 파일 SHA-256이 보존됨.
12. 독립 리뷰가 완료됨.

## 23. 개발·리뷰 원칙

변경은 다음 순서로 완료한다.

1. 기존 실패 또는 위험을 회귀 테스트로 재현.
2. 가장 작은 공용 경로 수정.
3. 집중 테스트.
4. 전체 `pytest`와 `compileall`.
5. PCell/GDS 변경이면 실제 KLayout 생성과 fresh reload.
6. Layer별 XOR 또는 semantic inventory 확인.
7. UX/DX와 Layout QA 반대 검토.
8. 이 README의 현재 상태와 미비사항 갱신.

절대 원칙:

- 추측으로 production geometry를 확정하지 않음.
- Padset과 layermap 없이 실제 TEG를 생성하지 않음.
- 외부 LLM 또는 사용자의 변경을 임의 삭제하지 않음.
- 실패, skip, unresolved 상태를 숨기지 않음.
- 테스트 통과만으로 전기적 의미를 주장하지 않음.

## 24. 자동화 상태

과거 30분 주기의 Windows scheduler 실험은 종료됐다.

- 16개 번호 run과 3개 검증된 자동 run이 있었음.
- 이전 schedule window는 2026-08-21 16:30 KST에 종료.
- 현재 active scheduler 없음.
- 당시 생성된 local runner, machine log와 실행 바이너리는 MCP 배포 소스가 아니므로
  GitHub 저장소에 포함하지 않음.
- 새 scheduler는 별도 요청과 종료 조건이 있을 때만 독립적으로 구성해야 함.

## 25. 문서 정책

- `readme.md`: 유일한 현재 사양·계획·상태·운영 문서.
- 로컬 `.agents/skills/**`: 개발·리뷰용 개인 agent instruction이며 MCP 런타임 문서,
  패키지 또는 GitHub 배포물에 포함되지 않음.
- 과거 background, feedback, plan, work-log, cron, root skill 안내는 이 README에
  통합한 뒤 제거한다.

## 26. 공식 참고자료

- KLayout download/current release: https://www.klayout.de/build.html
- KLayout 0.30.10 release notes: https://www.klayout.de/development.html#0.30.10
- KLayout database API: https://www.klayout.de/doc-qt5/programming/database_api.html
- KLayout Layout API: https://www.klayout.de/doc/code/class_Layout.html
- KLayout Region API: https://www.klayout.de/doc/code/class_Region.html
- MCP tools specification: https://modelcontextprotocol.io/specification/2025-11-25/server/tools
- MCP schema reference: https://modelcontextprotocol.io/specification/2025-11-25/schema
