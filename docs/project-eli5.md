# 이 프로젝트는 무엇인가

이 프로젝트는 반도체 측정용 GDS 도면을 만들고 다시 검사하는 KLayout 작업 도구다.

GDS는 반도체 도형을 담는 파일이다. TEG는 공정과 소자 특성을 측정하는 시험 구조다.

## 작업장으로 생각하면 된다

이 프로젝트는 주문형 금속 가공 작업장과 비슷하다.

- 사용자의 요청은 작업 주문서다.
- LLM은 주문을 읽고 작업 순서를 정하는 작업자다.
- MCP는 주문 양식과 검사 도구를 제공한다.
- KLayout은 실제 GDS를 읽고 그리는 기계다.
- Process profile은 해당 공정의 작업 규칙표다.
- Reference GDS는 사용자가 승인한 기존 견본이다.
- Skill은 작업자가 따라야 할 작업 설명서다.

규칙표와 견본이 없으면 작업자는 치수를 추측하지 않는다. 필요한 정보가 들어온 뒤 도면을 만든다.

## MCP는 무엇인가

MCP는 LLM과 KLayout 사이의 정해진 연결 방식이다.

LLM은 파일을 직접 만지기보다 MCP의 작업 항목을 호출한다. MCP는 입력을 검사하고 KLayout에 전달한다.

KLayout은 GDS를 만들고 다시 읽는다. MCP는 결과의 크기, 층, 연결, 계층 구조를 확인한다.

이 방식은 같은 요청이 같은 도형으로 이어지게 돕는다. 또한 누락된 입력과 추측한 입력을 구분한다.

## 사용자가 주는 정보

공정마다 다음 정보가 달라진다.

1. 정확한 공정 이름과 버전
2. GDS 층 번호와 용도를 연결한 layermap
3. DBU와 grid
4. 이번 도면에 필요한 선폭, 간격, contact 규칙
5. Pad 위치, DUT 종류, split 표, terminal 연결
6. 참고할 Reference GDS

DBU는 GDS가 표현하는 가장 작은 좌표 단위다. Grid는 실제 도형이 올라가야 하는 좌표 간격이다.

Reference GDS는 작업장 견본과 같다. 사용자가 이번 작업에 맞는 견본인지 확인해야 한다.

## 한 번의 작업 흐름

1. MCP가 원본 GDS와 입력 정보를 읽는다.
2. LLM이 빠진 정보만 묶어서 질문한다.
3. 사용자가 공정 규칙과 Reference GDS를 확인한다.
4. MCP가 새 경로에 GDS를 만든다.
5. KLayout이 저장된 파일을 새로 열어 검사한다.

마지막 검사를 fresh reload라고 부른다. 작업 중인 화면이 아니라 실제 저장 파일을 다시 확인한다.

필요하면 두 GDS의 도형 차이도 계산한다. 이 비교를 XOR이라고 부른다.

XOR이 0이면 비교한 층의 도형은 같다. 공정 규칙과 전기 특성까지 같다는 뜻은 아니다.

## TEG를 만들 때 지키는 방향

이 프로젝트의 대표 TEG는 약 2000×54 µm 크기와 25개 Pad를 사용한다.

이 크기와 Pad 수는 공정 규칙이 아니다. 작업마다 바꿀 수 있는 시작값이다.

긴 측정 배선은 한 줄로 만들지 않는다. 여러 평행선과 가로 연결선을 가진 mesh를 우선 사용한다.

Mesh는 전류가 지나갈 길을 늘린다. 금속 배선의 전압 손실을 줄이려는 목적이다.

측정 대상 금속은 별도로 보존한다. 주변 배선만 넓은 mesh로 연결한다.

배선이 Pad나 DUT와 만나는 부분도 검사한다. 얇은 목, 어긋난 중심, 겹쳐서 넓어진 부분을 찾는다.

## Transistor TEG

DUT는 실제로 측정할 소자다. Transistor 한 개를 측정해도 주변은 빈 땅으로 두지 않는다.

기본 설정은 DUT 영역을 같은 transistor 배열로 채운다. 그중 중앙에 가까운 소자 하나를 측정한다.

주변 transistor는 보통 배선하지 않는다. 조건이 맞으면 이웃 소자끼리 diffusion을 공유한다.

측정할 transistor가 넓어지면 contact 수도 늘린다. 허용된 규칙 안에서 가능한 수를 사용한다.

공정별 transistor 생성에는 별도 adapter가 필요하다. Adapter는 승인된 PCell이나 Reference GDS를 사용한다.

## PCellizer

PCell은 치수를 바꿔 다시 만들 수 있는 도형 묶음이다.

PCellizer는 기존 GDS에서 바꿀 부분을 고른다. 사용자는 ruler로 두 edge 사이를 표시할 수 있다.

확정된 치수와 split 표를 주면 여러 GDS를 만든다. CSV와 Excel에서 복사한 표도 입력 후보가 된다.

원본의 계층 구조는 유지한다. Mirroring, array, 여러 cell 조합도 occurrence 경로와 transform으로 구분한다.

현재 PCellizer는 제한된 첫 버전이다. 선택한 box 하나와 parameter 하나가 주된 지원 범위다.

## Reference Library

Reference Library는 공정별 견본 창고다.

예를 들어 node와 option별로 전체 GDS를 저장한다. 파일 hash로 어떤 견본인지 구분한다.

MCP는 hierarchy, 선폭 빈도, pitch, 직교 배선 같은 보이는 특징을 정리한다.

Net 의미와 공정 합법성은 모양만 보고 알 수 없다. 사용자가 KLayout에서 견본을 확인해야 한다.

비슷한 위치의 기존 위반은 참고 근거가 될 수 있다. 자동으로 새 도면의 허가가 되지는 않는다.

## Skill과 MCP의 차이

MCP는 실제 작업 도구다. Skill은 LLM이 그 도구를 쓰는 순서를 적은 설명서다.

`skills/klayout-drawing`은 범용 GDS, PCell, hierarchy, 검사를 다룬다.

`skills/klayout-teg-routing`은 Kelvin 저항과 낮은 기생저항 배선을 다룬다.

Skill이 없어도 MCP 서버는 실행된다. 다른 컴퓨터에서는 저장소의 Skill을 LLM에 등록해야 한다.

## 프로젝트 폴더 지도

- `src/klayout_mcp/`: MCP의 실제 Python 코드
- `skills/`: LLM용 작업 설명서
- `onboarding.md`: 새 공정 정보를 받는 순서
- `examples/`: 보존한 GDS, 설정, 실행 예제
- `artifacts/`: 기준으로 사용하는 최종 GDS
- `tests/`: 같은 입력이 같은 결과를 내는지 확인하는 검사
- `output/`: 작업 중 만든 결과를 두는 공간

원본과 기준 GDS는 덮어쓰지 않는다. 새 결과는 다른 이름과 경로에 저장한다.

## 현재 할 수 있는 일

- GDS와 OAS 파일의 크기, 층, cell, instance를 읽는다.
- 새 Manhattan 도형을 그린다. Manhattan은 가로와 세로 선만 쓰는 방식이다.
- Kelvin M1 예제를 같은 도형으로 다시 만든다.
- 두 layout의 도형 차이를 비교한다.
- Reference GDS에서 보이는 drawing style을 정리한다.
- 제한된 PCell split batch를 만든다.
- 작업 상태와 입력 근거를 파일로 남긴다.

## 아직 자동으로 할 수 없는 일

- 처음 보는 공정의 규칙을 스스로 알아내지 못한다.
- 이름과 색만 보고 layer 용도를 확정하지 못한다.
- 공정 adapter 없이 production transistor를 만들지 못한다.
- 내부 검사만으로 DRC, LVS, PEX 통과를 선언하지 못한다.
- GDS가 실제 wafer 측정에 준비됐다고 혼자 승인하지 못한다.

DRC는 도형 규칙 검사다. LVS는 도면과 회로 연결 비교다. PEX는 배선의 저항과 용량 계산이다.

이 세 검사는 일반 drawing의 필수 조건이 아니다. 다만 production 승인에는 별도 근거가 필요하다.

## 처음 사용할 때

1. Python, `uv`, KLayout을 설치한다.
2. MCP host에 이 저장소의 실행 명령을 등록한다.
3. `onboarding.md`의 공정 입력을 준비한다.
4. 대표 DUT 하나로 pilot GDS를 만든다.
5. KLayout에서 pilot을 확인한 뒤 전체 split을 만든다.

첫 작업은 `onboarding.md`의 입력 표를 채우고 대표 DUT 한 개로 pilot GDS를 만드는 것이다.
