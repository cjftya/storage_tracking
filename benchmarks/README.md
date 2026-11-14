# 벤치마크 도구

프로젝트 루트에서 실행합니다. Python 표준 라이브러리만 사용하며 GUI 측정에는 Tk와 그래픽 세션이 필요합니다. 디스크 측정은 도구가 생성한 임시 폴더만 스캔합니다.

| 모듈 | 측정 대상 |
| --- | --- |
| `run_scenarios` | 빈 폴더 / 일치 없는 파일 / 일치 파일의 엔진·저장·선택적 GUI 시간 |
| `compare_versions` | 기준 commit과 현재 코드의 시나리오 반복 비교. Git과 Tk 필요 |
| `index_store` | 합성 메타데이터를 RAM 저장소에 저장하는 비용. 파일 생성·스캔 없음 |
| `tree_reset` | 실제 Tk 항목 초기화 시간, 개별 Tk 호출과 이벤트 간격 |
| `folder_scenarios` | 폴더 전용 엔진·저장·GUI, 파일 수와 폴더 수에 따른 메모리 비교 |
| `shared_fixture` | 같은 fixture를 재사용하며 기준/현재 파일 엔진·저장을 번갈아 비교 |

```powershell
python -m benchmarks.run_scenarios --entries 3000 --gui --fixture-parent . --output benchmarks/results/local.json
python -m benchmarks.compare_versions --entries 3000 --repeat 3 --output benchmarks/results/comparison.json
python -m benchmarks.index_store --matches 20000 --repeat 3 --output benchmarks/results/index.json
python -m benchmarks.tree_reset --rows 100000 --repeat 3 --visible --modes baseline reset --output benchmarks/results/reset.json
python -m benchmarks.folder_scenarios --files 20000 --repeat 3 --output benchmarks/results/folder.json
python -m storage_scanner.folder.benchmark "C:\Data" --store
python -m benchmarks.shared_fixture --baseline-ref 35cdbfc --files 10000 --repeat 7 --output benchmarks/results/shared.json
```

새 측정 결과인 `results/*.json`은 Git 추적에서 제외합니다. 보관된 v1.1 측정은 `results/v1.1/`에 있으며 위 명령으로 덮어쓰지 않습니다. 환경·검증 범위·비교 수치는 [VALIDATION.md](VALIDATION.md)에 있습니다. 이전 v1.0 검증 기록은 Git 이력에 남아 있습니다.

비교 기준은 [__init__.py](__init__.py)의 `BASELINE_REF` 한 곳에 정의되어 있습니다. 비교 도구의 `--baseline-ref <commit>`으로 다른 기준을 지정할 수 있습니다. 기준 commit을 포함한 Git 이력이 필요하며, `main`이 바뀌어도 기본 비교 대상은 유지됩니다.

`tree_reset`의 기본 모드는 원래 reset, 일괄 delete, 즉시 destroy/교체, 같은 위젯의 leaf 분할 삭제, 현재 reset을 모두 비교합니다. 현재 방식과 기준만 비교하려면 위 예시처럼 `--modes baseline reset`을 지정하세요. `--visible`은 실제 창 그리기를 포함합니다. 파일 행 외에 200개당 부모 폴더 하나도 생성됩니다.

각 측정은 새 프로세스에서 실행합니다. 최대 프로세스 메모리에는 인터프리터와 모듈 로딩도 포함합니다. 캐시와 외부 부하는 통제하지 않으므로 반복 측정하고, GUI 전체 시간에는 상태 타이머 대기가 포함된다는 점을 고려하세요. `--help`로 전체 옵션을 확인할 수 있습니다.

`core.benchmark`와 기존 시나리오는 계속 파일 모드를 측정합니다. `folder.benchmark`는 최소 크기 필터 없이 디렉터리 루트를 합산하는 별도 진입점입니다. `folder_scenarios`는 직접 생성한 임시 폴더만 스캔하며 5,000개/20,000개 파일을 같은 4개 폴더에 넣는 경우와 20,000개 파일을 4,000개 폴더에 분포시키는 경우를 비교합니다. Python 할당량 추적은 별도 실행하므로 추적 실행의 시간을 일반 실행 시간과 비교하지 않습니다. v1.2 결과와 한계는 [VALIDATION_V1_2.md](VALIDATION_V1_2.md)에 있습니다.
