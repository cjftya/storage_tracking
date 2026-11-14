# 스토리지 스캐너 (Storage Scanner v1.2)

Windows와 Linux의 로컬 스토리지에서 용량을 많이 차지하는 폴더나 파일을 찾는 Python 프로그램입니다. 기본 **Folder scan**은 모든 일반 파일의 논리적 크기를 폴더별로 합산합니다. 선택 모드인 **Large file scan**은 지정한 크기 이상(`>=`)의 파일을 찾으며 기본 기준은 **100 MiB = 104,857,600바이트**입니다. 내용은 읽지 않고 파일을 수정·이동·삭제하지 않습니다.

## 주요 기능

- 접근 가능한 로컬 볼륨을 자동으로 찾고, 네트워크·가상 파일시스템과 시스템 경로를 기본 제외합니다.
- 폴더 선택 창으로 검색할 폴더 하나를 지정하고 최소 파일 크기와 제외 경로를 설정할 수 있습니다. USB 드라이브도 폴더로 선택합니다.
- 검색 중에도 화면을 조작할 수 있으며, 취소와 재시작을 지원합니다.
- 폴더 모드에는 폴더만, 파일 모드에는 일치 파일과 그 상위 폴더만 표시하며, 폴더를 펼칠 때 결과를 나누어 불러옵니다.
- 폴더에는 하위 검색 결과 파일의 용량 합계를 표시하며, 검색 중 기본 약 1초마다 부분 결과와 합계를 갱신합니다.
- 행마다 옅은 회색 배경을 번갈아 표시합니다. 폴더 우클릭 메뉴의 **Open folder**로 파일 탐색기를 실행하고, 검색이 완료되면 알림창을 표시합니다.
- 화면과 독립된 스캔 엔진, 자동 테스트, 성능 측정 도구를 제공합니다.

## 실행

Python **3.12 이상**과 Tkinter가 필요합니다. pip 의존성은 없습니다. Windows Python에는 보통 Tkinter가 포함됩니다. Linux에서는 배포판의 Tk 패키지(예: `python3-tk`)와 그래픽 세션이 필요합니다. GUI 없는 벤치마크는 Tkinter 없이 실행됩니다.

프로젝트 루트에서:

```powershell
python -m storage_scanner
# 또는
python main.py

# 특정 폴더, 250 MiB 이상
python -m storage_scanner "C:\Users\사용자\Downloads" --mode file --min-size-mib 250

# JSON 설정 적용
python -m storage_scanner --config config.example.json
```

Linux 예시:

```bash
python3 -m storage_scanner /home --mode file --min-size-mib 100 --exclude /home/me/cache
```

시작 시 백그라운드에서 로컬 드라이브를 확인합니다. **Select folder**로 검색할 폴더 하나를 지정합니다. 다른 폴더를 선택하면 기존 선택을 교체하며, **Clear**로 선택을 비웁니다. USB 드라이브도 같은 방식으로 선택합니다. 명령줄이나 설정 파일에서 경로를 지정하지 않았다면 빈 상태에서 시작하며, 폴더를 선택해야 검색할 수 있습니다. 여러 경로가 지정된 기존 설정은 GUI에서 첫 경로만 사용합니다. **Scan / Restart**로 시작하거나 재시작하고 **Cancel**로 취소합니다. **Refresh drives**는 새 장치를 확인하며 선택한 폴더와 편집한 제외 목록을 유지합니다. 검색 시작 시 입력한 설정을 고정하므로, 진행 중 편집은 다음 실행부터 적용됩니다. GUI 문구는 짧은 영어로 표시합니다.

화면에 표시되는 항목은 다음과 같습니다.

| 화면 항목 | 사용 방법 |
| --- | --- |
| Folder scan / Large file scan | 기본 폴더 합산 또는 큰 파일 검색을 선택합니다. 전환 시 이전 스캔을 취소하며, 새 검색은 Scan / Restart를 눌러 시작합니다. |
| Min size (MiB) | 파일 모드에서만 적용하며 기본값은 100입니다. 폴더 모드에서는 비활성화됩니다. |
| Folder / Select folder / Clear | 검색할 폴더 하나를 선택·교체하거나 선택을 비웁니다. |
| Excluded paths | 검색에서 제외할 경로를 한 줄에 하나씩 입력합니다. |
| Refresh drives | 장치를 다시 확인하며 입력한 목록은 유지합니다. |
| Scan / Restart | 현재 설정으로 검색을 시작하거나 다시 시작합니다. |
| Cancel | 진행 중인 검색을 취소합니다. 마지막 배치까지 반영한 부분 결과를 유지합니다. |
| Load next 200… | 해당 폴더의 다음 결과 200개를 불러옵니다. |
| Open folder | 폴더 우클릭 또는 Shift+F10으로 해당 폴더를 파일 탐색기에서 엽니다. |

검색 중에도 기본 약 1초마다 진행 통계와 부분 결과를 갱신합니다. 폴더 모드에는 폴더 행만, 파일 모드에는 일치 파일과 관련된 상위 폴더만 나타납니다. 접힌 폴더의 자식은 펼칠 때 현재 저장소에서 불러옵니다. 폴더마다 처음 200개씩 표시하며, 이후 항목은 **Load next 200…**을 더블클릭하거나 선택 후 Enter로 표시합니다. 기존에 불러온 페이지·선택·확장 상태는 주기적 갱신 때 유지합니다. 집계 중에는 순서를 유지하며, 폴더 모드는 완료/취소 후 용량 내림차순으로 보이는 범위만 예산 안에서 재배치합니다. 이미 불러온 항목을 유지하므로 최종 상위 페이지와 이전 페이지의 합집합이 표시될 수 있습니다. 파일 모드는 기존 탐색 순서를 유지합니다.

**Folder scan**은 최소 크기와 무관하게 모든 일반 파일의 `st_size`를 합산합니다. 개별 파일 경로는 저장하지 않습니다. 1 MiB 파일 100,000개는 약 100,000 MiB로 집계됩니다. 0B 폴더는 생략하지만 선택한 루트는 0B라도 표시합니다. 검색 중에는 `aggregating`, 취소나 접근 오류가 있으면 `partial`로 표시합니다. 결과는 원자적인 디스크 스냅샷이 아닙니다.

**Large file scan**의 폴더 용량은 **검색 조건에 일치한 하위 파일들의 합계**입니다. 예를 들어 하위 폴더 세 곳에 검색된 파일이 각각 100 MiB씩 있으면, 상위 폴더는 **300 MiB**로 표시합니다. 아직 펼치지 않은 하위 폴더와 다음 페이지의 파일도 합계에 포함합니다. 이 모드에서는 최소 크기 미만 파일과 제외 경로를 합산하지 않습니다. 두 모드 모두 읽기 쉬운 용량 단위와 정확한 바이트를 함께 표시합니다.

정상적으로 검색을 마치면 모드별 결과 수·오류 수·소요 시간이 담긴 **Scan complete** 알림창이 한 번 나타납니다. 취소한 검색에는 완료 알림을 띄우지 않으며, 부분 결과의 용량만 표시합니다. 진행률(%)이나 미리 추정한 완료 시간은 표시하지 않습니다. Linux에서 폴더 열기를 사용하려면 `xdg-open`과 파일 관리자가 필요합니다. 폴더가 사라졌거나 열 수 없으면 오류 알림으로 알려줍니다.

## 검색 범위와 제외 정책

정책 코드는 `storage_scanner/platform_fs/`에 분리되어 있습니다. CLI `--exclude`는 반복할 수 있고 JSON `excluded_paths`와 합쳐집니다. `--no-default-excludes` 또는 JSON `use_default_exclusions: false`로 선택적인 시스템 경로 제외를 해제할 수 있습니다. GUI에서 제외 목록도 직접 편집할 수 있습니다. Linux의 필수 가상 경로와 거부된 마운트 경계는 유지됩니다.

| OS | 명령줄 자동 검색 범위 | 기본 제외 |
| --- | --- | --- |
| Windows | Windows API와 `os.listvolumes/listmounts`로 찾은 고정 로컬 볼륨. 드라이브 문자 없는 폴더 마운트도 포함 | `%SystemRoot%`(보통 `C:\Windows`), 각 볼륨의 `System Volume Information`, `$Recycle.Bin`, `Recovery`, `$WinREAgent` |
| Linux | `/proc/self/mountinfo`의 로컬 디스크 파일시스템. 로컬 `/` 포함 | `/proc`, `/sys`, `/dev`, `/run` 및 `/usr`, `/bin`, `/sbin`, `/lib`, `/lib64`, `/boot`, `/etc` |

- Windows의 `Users`, `ProgramData`, 다운로드 폴더는 시스템 드라이브에 있다는 이유로 제외하지 않습니다. Windows 경로는 대소문자 구분 없이 **경로 경계**로 비교하므로 `C:\WindowsBackup`은 `C:\Windows` 제외에 해당하지 않습니다.
- Linux의 `/home`, `/mnt`, `/media`, `/opt`, `/var`는 일괄 제외하지 않습니다. 로컬 타입은 ext2/3/4, xfs, btrfs, f2fs, vfat, exfat, ntfs/ntfs3, fuseblk, zfs 등이며, 알려지지 않은 타입은 `/dev/` 기반 소스일 때만 허용합니다. 전체 타입 목록은 `platform_fs/linux.py`에 공개되어 있습니다.
- NFS, CIFS/SMB, SSHFS 등의 네트워크 파일시스템과 proc/sysfs/tmpfs/cgroup 등 가상 파일시스템은 제외합니다. 컨테이너에서 루트 `/`인 overlay/aufs만 예외로 허용합니다. `/proc`, `/sys`, `/dev`, `/run`은 사용자가 선택적 제외를 해제해도 스캔하지 않습니다.
- GUI에서는 이동식 장치 선택 옵션 없이 USB 드라이브의 폴더를 직접 추가합니다. 명령줄 벤치마크의 자동 검색에서는 이동식 장치를 기본 제외하며 `--include-removable` 또는 JSON `include_removable: true`로 포함할 수 있습니다. Windows는 `GetDriveTypeW`, Linux는 sysfs 블록 장치의 `removable` 플래그를 사용합니다. USB 디스크도 OS가 고정 디스크로 보고하면 기본 검색 대상입니다. Linux 장치 정보가 없으면 비이동식으로 취급합니다.
- 선택되지 않은 마운트도 경계로 기록하므로 상위 루트에서 네트워크·가상·이동식 마운트로 내려가지 않습니다. 중첩 로컬 마운트는 별도 루트에서 한 번씩 스캔합니다. 동일 볼륨의 별칭과 확인 가능한 Linux bind 별칭을 제거합니다. GUI/CLI의 명시적 루트도 선택된 로컬 볼륨 안이어야 합니다.
- Linux의 파일 단위 bind 마운트도 경계로 처리합니다. 파일 모드는 선택된 로컬 파일 마운트를 별도 파일 루트로 한 번 집계하고 일반 파일 루트도 지원합니다. 폴더 모드는 디렉터리 루트를 대상으로 하며 파일 단위 마운트 경계를 넘어가지 않습니다.
- 심볼릭 링크와 Windows junction/reparse 디렉터리는 따라가지 않습니다. Windows에서 볼륨 마운트로 확인된 검색 루트만 예외입니다. 임의 junction을 검색하려면 실제 대상 경로를 루트로 선택하세요.

자동 시스템 경로 식별은 완전하지 않습니다. 추가 시스템/복구 폴더는 제외 목록에 넣으세요. 스캔 도중 마운트가 변경되는 상황까지 원자적으로 보장하지는 않습니다. 저장소 상태가 바뀌면 볼륨을 재검색한 후 다시 스캔하세요.

## 설정 파일과 명령줄 옵션

JSON 예시는 [config.example.json](config.example.json)에 있습니다. Windows에서 검색 경로를 지정하는 설정은 다음과 같습니다.

```json
{
  "roots": ["C:\\Data"],
  "scan_mode": "folder",
  "min_size_mib": "100",
  "include_removable": false,
  "use_default_exclusions": true,
  "excluded_paths": ["C:\\Data\\cache"],
  "gui_update_ms": 1000
}
```

Linux에서는 경로를 `/home` 등으로 바꾸면 됩니다. `roots`를 생략하면 GUI에서 직접 폴더를 선택합니다. GUI 없는 명령줄 벤치마크는 이 경우 로컬 볼륨을 자동으로 찾습니다. 상대 경로는 실행 디렉터리 기준 절대 경로로 바꿉니다. 명령줄에서 지정한 검색 경로와 최소 크기는 JSON 값보다 우선합니다. 최소 크기는 소수 MiB도 허용하되 정확히 정수 바이트로 환산되어야 합니다. 진행 통계와 결과 변경 확인 주기는 `gui_update_ms`로 조정하며 기본 1,000ms, 최솟값 50ms입니다. 기존 사용자 지정 주기도 그대로 적용합니다. 설정 파일은 읽기만 하며 GUI 편집 내용은 자동 저장하지 않습니다.

| 명령줄 옵션 | 설명 |
| --- | --- |
| `--mode folder` / `--mode file` | GUI 모드를 지정합니다. 기본값은 folder입니다. |
| `--min-size-mib 250` | 파일 모드의 최소 파일 크기를 250 MiB로 지정합니다. |
| `--exclude 경로` | 경로를 제외합니다. 여러 번 지정할 수 있습니다. |
| `--include-removable` | 명령줄 벤치마크의 자동 검색에 이동식 장치를 포함합니다. GUI는 폴더 선택으로 지정합니다. |
| `--no-default-excludes` | 선택적인 시스템 경로 기본 제외를 해제합니다. |
| `--config 설정파일.json` | JSON 설정 파일을 불러옵니다. |
| `--help` | 사용 가능한 옵션을 확인합니다. |

## 구조와 엔진 사용

```text
storage_scanner/
  main.py                  CLI 설정 및 GUI 진입점
  core/
    models.py              불변 설정·일치 파일·진행·요약 모델
    scanner.py             GUI/큐 비의존 순회 엔진
    filters.py             정확한 크기 변환·제외 경계
    results.py             저장 인터페이스·메모리 트리 인덱스
    benchmark.py           GUI 없는 측정 진입점
  platform_fs/
    filters.py             공통 제외 경계 정책
    paths.py               호스트 경로 및 링크 정책
    volumes.py             검색 루트·마운트 경계 구성
    windows.py / linux.py  OS별 볼륨·제외 정책
    metrics.py             OS별 프로세스 최대 메모리
    actions.py             운영체제의 파일 탐색기로 폴더 열기
  gui/
    app.py                 Tk 이벤트·주기적 상태 및 결과 갱신
    controller.py          워커·제한 큐·결과 수집·실행 ID
    tree_view.py           예산 제한 렌더링·지연 로딩·페이지
    folder_tree.py         독립 폴더 트리·완료 후 정렬
  folder/
    models.py / scanner.py 폴더 전용 모델·독립 순회 알고리즘
    results.py             직접/누적 용량 RAM 인덱스
    controller.py          독립 실행 상태·수집 큐
    benchmark.py           폴더 엔진/저장 측정 진입점
tests/                     표준 unittest 테스트
benchmarks/                임시 데이터 시나리오 측정
```

```python
from storage_scanner.core.scanner import scan
from storage_scanner.platform_fs.volumes import discover_volumes, make_config
from threading import Event

stop = Event()
config = make_config(discover_volumes(), min_size_bytes=100 * 1024 * 1024)
summary = scan(config,
               on_batch=lambda matches: print("batch:", len(matches)),
               on_progress=lambda p: print(p.files_scanned, p.files_matched),
               should_cancel=stop.is_set)
print(summary)
```

엔진은 단일 워커에서 `os.scandir()`와 명시적 스택을 사용합니다. 일반 파일에만 `stat(follow_symlinks=False)`를 호출하며 내용이나 해시는 읽지 않습니다. 일반적인 `OSError`는 횟수를 집계하고 계속 진행합니다. 디렉터리 경계 및 기본 128개 엔트리마다 취소 여부를 확인합니다. 콜백 예외는 호출자 측 오류로 전파합니다.

결과는 기본 512개 배치로 전달하고, 기본 0.25초가 지나면 다음 확인 지점에서 부분 배치도 전달합니다. 완료·취소 시 남은 결과도 전달합니다. 진행 알림은 기본 0.2초 간격입니다. 대기 중인 OS 호출 자체를 강제로 중단하지는 않습니다.

GUI controller는 최대 8개 배치의 큐와 별도 결과 수집 스레드를 사용합니다. GUI가 처리하지 못하는 동안에도 수집하며, 큐가 가득 차면 워커에 역압을 적용합니다. 진행 상태는 최신 값만 유지합니다. 재시작 시 이전 실행을 취소하고 단일 순회 잠금이 해제된 뒤 새 순회를 시작합니다. 실행별 저장소와 ID로 이전 결과의 혼입을 방지하며 워커에서 Tkinter를 호출하지 않습니다.

파일 메모리 저장소는 일치 파일과 관련 폴더 수에 비례하는 **O(matches + ancestor folders)** 메모리를 사용합니다. 폴더 저장소는 폴더의 직접 용량 증분을 해당 폴더와 각 조상에 한 번씩만 반영하며, 하위 누적 합계를 다시 더하지 않습니다. 개별 파일 결과 객체 없이 **O(결과 폴더 수)** 인덱스와 제한된 배치를 사용합니다. 두 모드 모두 RAM만 사용하며 고정 메모리 상한이나 디스크 저장, 메모리 초과 자동 전환은 없습니다.

폴더/파일의 모델·저장소·순회·실행 상태·결과 트리는 독립적입니다. 모드 전환은 이전 결과를 버리고 새 저장소를 만들며 앱 수준 잠금 하나로 순회 중첩을 방지합니다. `core.scanner.scan`과 `core.benchmark`는 기존 파일 모드 의미를 유지합니다. 폴더 API는 `folder.scanner.scan`, `FolderConfig`, `FolderResultStore`입니다. 기존 설정에 `scan_mode`가 없으면 GUI는 `folder`로 시작하며 `--mode`가 JSON 값보다 우선합니다.

GUI는 기본 1,000ms마다 같은 타이머로 진행 상태와 결과 변경을 확인합니다. 시작 시 실행별 저장소를 연결하고, 보이는 폴더 합계와 열린 폴더의 추가 결과만 증분 반영합니다. 변경 큐·페이지 삽입 작업은 한 번에 최대 150개 작업, 약 12ms 예산으로 나누며 남은 작업은 다음 이벤트 루프에서 이어갑니다. 저장소는 짧은 락 안에서 최대 32개 항목의 페이지와 자식 개수를 함께 복사하며, Tk 호출 중에는 락을 유지하지 않습니다. 완료·취소 시 마지막 배치를 수집한 뒤 남은 변경을 반영하고 트리를 재구축하지 않습니다. 12ms는 하드 실시간 보장이 아니며 아주 느린 개별 Tk/OS 호출은 이 예산을 초과할 수 있습니다.

재시작 시 새 Treeview로 즉시 교체하고, 숨긴 이전 위젯의 실제 렌더 항목을 자식부터 최대 150개/약 12ms씩 삭제한 뒤 빈 위젯을 해제합니다. 큰 하위 트리를 한 번에 삭제하지 않습니다. 렌더 세대와 실행 ID로 이전 콜백을 격리합니다. 정리가 끝날 때까지 이전 위젯 메모리가 잠시 남을 수 있습니다.

폴더 합계는 수집 스레드에서 배치를 저장할 때 상위 폴더로 누적하며, 화면을 펼칠 때 디스크를 다시 검색하지 않습니다. 폴더 열기도 별도 스레드에서 처리하므로 파일 관리자가 늦게 응답해도 GUI가 멈추지 않습니다. 파일 변경 경쟁으로 크기와 상태가 바뀔 수 있으며, 결과는 각 파일의 메타데이터를 조회한 시점 기준입니다. 하드링크는 경로별로 집계하고 희소·압축 파일의 실제 디스크 점유량은 계산하지 않습니다.

## 테스트와 벤치마크

```powershell
python -m unittest discover -s tests -v

# 순회 엔진 기준: 결과는 배치 전달 후 폐기
python -m storage_scanner.core.benchmark "C:\Data" --min-size-mib 100

# 결과 저장 비용 포함
python -m storage_scanner.core.benchmark "C:\Data" --min-size-mib 100 --store

# Python 할당 최대 메모리 추가 측정 (추적 비용이 시간에 포함됨)
python -m storage_scanner.core.benchmark "C:\Data" --store --track-python-memory

# 소유한 임시 fixture만 생성·정리. GUI 창은 자동으로 열고 닫음
python -m benchmarks.run_scenarios --entries 3000 --gui --output benchmarks/results/local.json
```

벤치마크는 JSON으로 파일 수/초, 전체 시간, 일치 수, 오류 수, 취소 여부, 프로세스 최대 resident/working-set 메모리 등을 출력합니다. Ctrl+C는 정상 취소하고 요약을 출력합니다. 최대 프로세스 메모리는 인터프리터·볼륨 검색 등을 포함한 **프로세스 생애 최대값**입니다. Python 메모리 추적 실행의 시간은 비추적 실행과 직접 비교하지 마세요.

시나리오 도구는 빈 디렉터리 다수 / 작은 파일 다수·일치 없음 / 일치 파일 다수를 구분하고 각 모드를 **새 프로세스**에서 실행합니다. 엔진 단독, 저장 포함, 선택적 GUI 실행을 비교합니다. GUI는 일치 루트만 펼쳐 정상 페이지 제한을 적용하고, 수집 포함 엔진 시간·1,000ms 타이머 대기 포함 전체 시간·실제 트리 갱신 시간·10ms heartbeat의 최대 간격을 기록합니다. 전체 GUI 시간에서 엔진 시간을 뺀 값을 순수 렌더링 비용으로 해석하면 안 됩니다. fixture 생성 시간은 스캔 시간에서 제외됩니다. 파일 내용은 1바이트 길이의 테스트 파일로 만들며 대용량 데이터 읽기 성능 시험이 아닙니다.

임시 폴더가 tmpfs 등 제외된 파일시스템에 있으면 `--fixture-parent .`로 프로젝트의 로컬 볼륨에 fixture를 만드세요. 예: `python3 -m benchmarks.run_scenarios --entries 3000 --gui --fixture-parent .`. 이 경우에도 새로 생성한 임시 하위 폴더만 정리합니다.

OS 캐시 상태, 디스크 종류, 보안 소프트웨어, 경로 구조가 속도에 영향을 줍니다. 최소 반복 3회 이상으로 변동을 확인하고, SSD/HDD와 OS별로 따로 기록하세요. 확인하지 않은 저장매체 종류는 `unverified`로 기록합니다. 속도 배수나 목표 완료 시간은 약속하지 않습니다. 현재 환경의 측정과 검증 범위는 [benchmarks/VALIDATION.md](benchmarks/VALIDATION.md)에 기록합니다.

자동 테스트는 크기 경계·정확한 바이트 변환, 한글/공백/특수문자, 중첩/깊은/빈 폴더, 중복 루트/마운트, OS별 경계·타입·bind/subvolume 정책, 삭제 경쟁·접근 실패, 배치·취소·재시작·늦은 결과 격리, 수집 실패, 2만 개 결과의 저장·지연 표시를 검증합니다. 검색 중 1초 갱신·상위 폴더 합계·페이지 유지·변경 큐 분할·취소 마지막 배치·대량 실제 Tk 항목의 연속 재시작, 완료 후 행 유지, 우클릭 폴더 열기·실패 처리, 완료 알림의 중복 방지도 검증합니다. 심볼릭 링크 생성 권한이나 Tk 디스플레이가 없으면 해당 테스트만 건너뜁니다. Linux 실기기, 실제 네트워크/이동식 장치, 다양한 디스크에서의 수동 확인은 별도입니다.

측정 도구별 실행 방법과 보관 자료는 [benchmarks/README.md](benchmarks/README.md)를 참고하세요.

v1.2 테스트와 두 모드의 성능 비교·측정 한계는 [v1.2 검증 기록](benchmarks/VALIDATION_V1_2.md)에 있습니다. 폴더 모드 벤치마크는 다음처럼 별도 진입점으로 실행합니다.

```powershell
python -m storage_scanner.folder.benchmark "C:\Data" --store
python -m benchmarks.folder_scenarios --files 20000 --repeat 3 --output benchmarks/results/folder.json
```
