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

이 절은 만들고 싶은 TEG의 **목표 규칙**이다. 현재 stock Phase 1이 이 규칙을 모두 자동으로
구현한다는 뜻은 아니다.

이 프로젝트의 대표 TEG는 약 2000×54 µm 크기와 25개 Pad를 사용한다.

이 크기와 Pad 수는 공정 규칙이 아니다. 작업마다 바꿀 수 있는 시작값이다.

긴 측정 배선은 한 줄로 만들지 않는다. 여러 평행선과 가로 연결선을 가진 mesh를 우선 사용한다.

Mesh는 전류가 지나갈 길을 늘린다. 금속 배선의 전압 손실을 줄이려는 목적이다.

측정 대상 금속은 별도로 보존한다. 주변 배선만 넓은 mesh로 연결한다.

배선이 Pad나 DUT와 만나는 부분도 검사한다. 얇은 목, 어긋난 중심, 겹쳐서 넓어진 부분을 찾는다.

현재 Kelvin 예제와 mesh compiler가 이 규칙 일부를 확인한다. Direct Phase 1도 계산한 중심 경로를
최소 2개의 평행선과 가로 연결선으로 바꾼다. 꺾이는 곳과 양 끝의 연결도 검사한다.

다만 Direct Phase 1의 Pad는 사용자가 준 Pad 파일이 아니다. Frame과 Pad 개수로 다시 만든 사각형이다.
따라서 mesh 연결은 구현됐지만, 실제 Pad와 transistor를 함께 쓴 완성 흐름은 아니다.

## Transistor TEG

DUT는 실제로 측정할 소자다. Transistor 한 개를 측정해도 주변은 빈 땅으로 두지 않는다.

Planning 기본 설정은 DUT 영역을 같은 transistor 배열로 채우고 그중 중앙에 가까운 소자 하나를
측정하는 것이다.

주변 transistor는 보통 배선하지 않는다. 조건이 맞으면 이웃 소자끼리 diffusion을 공유한다.

측정할 transistor가 넓어지면 contact 수도 늘리는 것이 목표 계약이다. 현재 독립 contact planner와
conceptual fixture가 있을 뿐 실제 transistor 생성 경로에 통합돼 있지 않다.

공정별 transistor 생성에는 별도 adapter가 필요하다. Adapter는 승인된 PCell이나 Reference GDS를
사용해야 하며, stock checkout에는 이 adapter가 없다.

## 여러 DUT가 든 예시 GDS를 배우는 흐름

사용자는 여러 DUT가 든 GDS와 각 DUT의 parameter 표를 함께 줄 수 있다.

예를 들어 Gate length, CPP, Width, nFin, cell height를 DUT별로 적는다. Terminal과 layer의 의미도
함께 알려줘야 한다. 이 정보가 빠지거나 서로 맞지 않으면 MCP는 문제가 있는 항목과 고치는 방법을
구체적으로 알려준다.

또한 compiler가 어떤 관계를 학습할지도 적는다. 예를 들어 L과 CPP가 각각 영향을 주는지,
`L×CPP` 조합도 따로 영향을 주는지, nFin이 특정 값에서 다른 구조로 바뀌는지를 명시한다.
MCP는 예시 DUT가 그 관계들을 서로 구분하기에 충분한지 계산한다. 충분하지 않으면 어떤 종류의
DUT 예시가 더 필요한지 알려주고 후보 등록을 막는다.

MCP는 DUT 사이에서 항상 같은 도형 특징을 찾아 drawing style 후보로 기록한다. 같은 parameter인데
도형이 다른 DUT가 있으면 어느 DUT를 따를지 사용자에게 묻는다. 차이를 몰래 평균내지 않는다.

새로 재현한 DUT GDS가 있으면 원본 DUT와 비교해 점수를 낼 수 있다. 일부 DUT는 계산에서 빼두었다가
검사용으로 따로 비교한다. 다만 그 DUT도 원본 파일 안에 보이므로 비밀 시험 문제는 아니다. 원본 GDS를
그대로 답으로 제출하면 통과시키지 않는다. 파일이 다르다는 사실만으로 “compiler가 만들었다”고
주장하지도 않는다. 저장된 답안이 나중에 바뀌지 않았는지 다시 hash를 확인하고, 미해결 질문이나
공정·소자 종류가 다르면 후보 생성을 중단한다. 사용자가 직접 낮춘 합격점은 비교용으로만 쓸 수 있다.
Adapter 후보를 만들 때는 회사나 담당 조직이 서버에 설정한 합격 기준만 사용한다. 그 기준이 정한
필수 치수 하나라도 틀리면 평균점수가 높아도 실패한다. 누가 어떤 기준을 승인했는지도 함께 저장한다.
통과한 결과는 공정과 버전별 adapter 후보로 저장한다.

Adapter의 사용 중지 기록도 순서대로 저장한다. 실수로 마지막 기록 하나를 지우면 프로그램이 이를
알아챈다. 하지만 관리자가 기록과 목차를 함께 예전 상태로 바꿀 수 있는 상황까지 막으려면,
별도 보안 저장소에 마지막 상태를 한 번 더 기록해야 한다. 이 연결은 지원하지만 기본 설치에는 없다.

이 과정은 CPP가 바뀔 때 Gate, Active, Contact가 어떻게 함께 움직이는지 자동으로 알아내는 기능은
아니다. 현재는 입력 검사, 비교, 질문, 점수 계산과 후보 저장까지 구현돼 있다. 실제 DUT를 만드는
공정별 compiler는 별도로 필요하다.

## PCellizer

PCell은 치수를 바꿔 다시 만들 수 있는 도형 묶음이다.

PCellizer는 기존 GDS에서 바꿀 direct box를 고른다. 사용자는 ruler로 두 edge 사이를 표시할 수 있다.

확정된 치수와 split 표를 주면 row별 static GDS를 만든다. CSV와 Excel에서 복사한 표도 입력 후보가
된다. 다시 호출할 수 있는 KLayout PCell library를 만드는 기능은 아니다.

원본의 계층 구조는 유지한다. Mirroring, array, 여러 cell 조합도 occurrence 경로와 transform으로 구분한다.

현재 PCellizer는 제한된 기존 도구다. Non-array occurrence의 선택한 box 한 축과 parameter key 하나가
지원 범위다. Array member specialization과 W×L dependent-shape 변경은 지원하지 않는다.

따라서 실제 transistor adapter는 이 PCellizer를 확장하는 방식으로 만들지 않는다. 위의 여러 DUT
corpus를 바탕으로 별도의 공정별 compiler를 만들고 검증하는 방향을 사용한다.

예시 DUT에서 Lg와 CPP가 항상 같이 변하면 어느 값이 도형을 바꾼 원인인지 알 수 없다. `L×CPP`까지
학습하는 compiler라면 그 조합을 구분할 DUT도 더 필요하다. 반대로 적절히 흩어진 일반 DOE가 모든
관계를 구분할 수 있다면, 꼭 “한 번에 한 값만 바꾼 쌍”이 없어도 된다. 또한 “모양이 정확히 같아야
함”을 선택했다면 합격 점수를 0으로 잡아도 다른 모양은 통과하지 못한다.

도넛 모양 금속의 구멍 위치도 이제 “정확히 같은 모양” 비교에 들어간다. 단자의 작은 접촉 영역을
알려주면 그 단자가 실제 금속 위에 있는지, 같은 층의 두 단자가 같은 금속 덩어리에 닿는지도 본다.
이 정보를 안 주면 작업을 막지 않고 무엇이 확인되지 않았는지 경고한다. 다만 층 사이 via까지 따라가는
전기 연결 검사는 아니므로 최종 확인에는 LVS가 필요하다.

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

원본과 기준 GDS는 입력으로 수정하지 않는다. 새 결과는 다른 이름과 경로에 저장한다. 지원하는
같은 컴퓨터의 local filesystem에서는 여러 작업이 같은 새 경로를 동시에 만들 때 첫 결과만 남긴다.
뒤 작업은 기존 결과를 덮어쓰거나 지우지 않는다. NFS, SMB와 여러 컴퓨터가 함께 쓰는 경로는 아직
지원하지 않는다.

## 현재 할 수 있는 일

- GDS와 OAS 파일의 크기, 층, cell, instance를 읽는다.
- 새 Manhattan 도형을 그린다. Manhattan은 가로와 세로 선만 쓰는 방식이다.
- Kelvin M1 예제를 같은 도형으로 다시 만든다.
- 두 layout의 도형 차이를 비교한다.
- Reference GDS에서 보이는 drawing style을 정리한다.
- 실제 Pad macro를 수정하지 않고 별도 top cell에 배치한다.
- 여러 labeled DUT를 등록하고 누락 정보와 설명되지 않은 차이를 찾는다.
- 겉보기에는 조합이 충분해도 L과 CPP가 거의 같이 움직이면 경고하되 작업을 막지는 않는다.
- 재현 DUT를 train/검사용 그룹으로 나눠 비교하고 공정별 adapter 후보를 저장한다.
- 길이, 면적, 도형 개수와 존재 여부에 각각 맞는 합격 기준을 적용한다.
- Direct Phase 1의 계산 경로를 여러 rail과 cross-tie가 있는 mesh로 바꾼다.
- 제한된 PCell split batch를 만든다.
- 작업 상태와 입력 근거를 파일로 남긴다.

## 아직 자동으로 할 수 없는 일

- 처음 보는 공정의 규칙을 스스로 알아내지 못한다.
- 이름과 색만 보고 layer 용도를 확정하지 못한다.
- 공정 adapter 없이 production transistor를 만들지 못한다.
- 실제 Pad macro, corpus 기반 transistor와 mesh 배선을 하나의 자동 Phase 1 작업으로 묶지 못한다.
- 실제 21-DUT와 84개 연결을 사용한 배선 시간과 성공률을 아직 검증하지 못했다.
- 예시 DUT만 보고 CPP와 연결된 모든 도형 변경 규칙을 자동으로 만들지 못한다.
- 내부 검사만으로 DRC, LVS, PEX 통과를 선언하지 못한다.
- GDS가 실제 wafer 측정에 준비됐다고 혼자 승인하지 못한다.

DRC는 도형 규칙 검사다. LVS는 도면과 회로 연결 비교다. PEX는 배선의 저항과 용량 계산이다.

이 세 검사는 일반 drawing의 필수 조건이 아니다. 다만 production 승인에는 별도 근거가 필요하다.

## 처음 사용할 때

1. Python, `uv`, KLayout을 설치한다.
2. MCP host에 이 저장소의 실행 명령을 등록한다.
3. `onboarding.md`의 공정 입력, 실제 Pad macro와 labeled DUT 표를 준비한다.
4. Pad와 DUT corpus를 등록하고, 누락 정보와 DUT별 차이를 먼저 해결한다.
5. 공정별 compiler가 만든 DUT를 원본의 train/검사용 그룹과 비교해 adapter 후보를 등록한다.
6. 실제 adapter와 Pad/mesh 연결이 준비된 공정만 대표 DUT 하나로 pilot GDS를 만든다.
7. KLayout과 foundry 검증 환경에서 pilot을 확인한 뒤 전체 split을 만든다.

첫 작업은 `onboarding.md`의 입력 표와 예시 DUT별 parameter 표를 채우는 것이다.
