# Examples

이 디렉터리에는 재실행·회귀·사용성 설명에 필요한 최종 예제만 둔다. 실행 중간물과 test temporary
file은 `output/`에 생성하며 예제로 승격하지 않는다.

## Layout examples

| 경로 | 의미 | 검증 경계 |
|---|---|---|
| `gds/kelvin_m1_w24_48_100nm_l2_3um.gds` | Custom 3×2 Kelvin split 예제 | W 24/48/100 nm × L 2/3 µm; SLN001 profile geometry, nonproduction |
| `../artifacts/SLN001_kelvin_m1/SLN001_kelvin_m1_aligned_force_pad_joint_v15.gds` | 보존된 Kelvin regression reference | 생성 예제가 아니라 immutable comparison reference |

## Style and settings

| 경로 | 용도 |
|---|---|
| `style-profiles/sln001_kelvin_style.json` | 위 Kelvin GDS에서 `extract_layout_style`로 추출한 path-independent profile |
| `settings/sln001_kelvin_reference_layermap.yaml` | Kelvin example의 explicit semantic layermap |
| `settings/organization_measurement_preset.yaml` | 회사 고정 항목의 reference-only preset 예시 |

Style profile은 source GDS SHA-256, layermap SHA-256, hierarchy reuse, layer별 직교성·치수 빈도·
merged topology와 reference-view descriptor를 포함한다. 관측값은 design rule이나 electrical model이
아니며, 실제 drawing reference로 사용할 때는 사용자가 KLayout에서 GDS를 확인해야 한다.

## UI and external research inputs

- `images/pcellizer-klayout-gui-concept-v1.png`: PCellizer KLayout dock의 GUI concept.
- `external/nangate45/`: PCellizer/reference 연구용 standard-cell GDS와 preview. 프로젝트가 생성하거나
  승인한 PDK 자산이 아니며, process onboarding 입력이나 fallback으로 사용하지 않는다. 외부 배포 전
  원 출처와 license를 별도로 확인해야 한다.

## Runnable example

`run_persistent_kelvin_demo.py`는 test-only host components로 Kelvin persistent workflow를 재현한다.

```powershell
uv run python examples/run_persistent_kelvin_demo.py --run-root output/persistent-kelvin-demo-01
```

결과가 `measurement_package_complete`여도 tester program, silicon measurement 또는 production
readiness를 뜻하지 않는다.
