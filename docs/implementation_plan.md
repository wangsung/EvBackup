# Implementation Plan - BackupManager 모던 독립형 웹 UI 대시보드 구축 (3안)

사용자가 복잡한 터미널 명령이나 CMD 창을 직접 보지 않고도, 로컬 웹 대시보드에서 마우스 클릭 한 번으로 에버노트 백업, API 보안 토큰 관리, 마크다운 변환 상태 모니터링을 실시간으로 수행할 수 있도록 **독립형 모던 웹 UI 대시보드(3안)**를 구현합니다.

---

## User Review Required

> [!IMPORTANT]
> **주요 설계 사양 및 보안 고려사항:**
> 1. **포트 5001 독립 구동:** 
>    기존 `MDBrowser`(포트 5000)와 독립적으로 실행되도록 **포트 5001**에서 동작하는 초경량 Flask 서버로 빌드합니다.
> 2. **실시간 로그 터미널 스트리밍 (Server-Sent Events - SSE):**
>    백업 과정(`backup.py`)의 모든 터미널 출력(콘솔 로깅)을 웹 UI 상의 모던 스타일 가상 터미널창에 **실시간 한 줄씩 딜레이 없이 스트리밍** 출력하도록 SSE 아키텍처를 적용합니다.
> 3. **중복 실행 방지 락 (Worker Lock):**
>    백업 작업 도중 중복해서 백업 실행 버튼을 누르는 일을 방지하기 위해 백엔드에 프로세스 락(Active Process Check) 기능을 두어 안전하게 조작을 제어합니다.
> 4. **원클릭 자동 실행 스크립트:**
>    더블 클릭만으로 Flask 서버가 켜지고 브라우저에서 `http://127.0.0.1:5001` 페이지가 즉시 열리는 전용 배치를 제공합니다.

---

## Proposed Changes

### [BackupManager Component]

#### [NEW] [manager_server.py](file:///C:/_My2026/_EVERBK/BackupManager/manager_server.py)
* **초경량 웹 관리용 Flask 백엔드 서버 개발**:
  * **보안 토큰 및 로컬 환경 분석 API (`/api/status`):** `backup.py` 내부의 `check_dependencies()`, `check_database()`, `check_exports()`, `check_markdown()` 함수 결과를 수집하여 JSON 형태로 웹 클라이언트에 반환합니다.
  * **비동기 백업 실행 컨트롤러 (`/api/run`):** `action` 매개변수(`init`, `sync`, `export`, `convert`, `all`)를 수신하고, `backup.py` 백업 스크립트를 독립 프로세스로 호출합니다.
  * **실시간 터미널 스트림 API (`/api/stream`):** 실행 중인 백업 백그라운드 프로세스의 `stdout` 표준 출력을 실시간 가로채서 클라이언트에 전달하는 SSE 텍스트 스트림을 반환합니다.

#### [NEW] [templates/index.html](file:///C:/_My2026/_EVERBK/BackupManager/templates/index.html)
* **어두운 유리 테마(Glassmorphism) 기반 1페이지 대시보드 마크업**:
  * **대시보드 헤더:** 세련된 Outfit/Inter 폰트와 그라디언트 로고 적용.
  * **진단 결과 영역 (Row 1):** 의존성 상태, API 로그인 상태, 백업 파일 누적 규모 카드가 HSL 글로우 테두리를 달고 출력됩니다.
  * **조작 버튼 컨트롤바 (Row 2):** 원클릭 전체 백업(`all`), DB 토큰 인증 로그인(`init`), 단순 동기화(`sync`), 파일 변환(`convert`) 등의 그라디언트 액션 버튼 배치.
  * **가상 개발 터미널 뷰 (Row 3):** 실시간으로 수신되는 콘솔 텍스트를 스트리밍해 주는 다크 코딩 스타일 프리뷰 박스 구현 (프로그레스바 애니메이션 스피너 내장).

#### [NEW] [static/style.css](file:///C:/_My2026/_EVERBK/BackupManager/static/style.css)
* **프리미엄 다크 글래스모피즘 테마 전용 스타일시트**:
  * Harmonious HSL 칼라셋 정의 (`--accent-primary`, `--bg-dark`, `--glass-bg`, `--glow-shadow`).
  * 토스트 알림 메시지 애니메이션 키프레임 및 진동 호버 효과 정의.
  * 실시간 터미널의 가독성을 극대화하는 폰트 크기 및 다크 콘솔 맞춤 스크롤바 디자인.

#### [NEW] [run_manager.bat](file:///C:/_My2026/_EVERBK/BackupManager/run_manager.bat)
* **원클릭 자동 백업 관리 구동기**:
  * Flask 서버 가동 명령(`python manager_server.py`) 및 자동으로 기본 브라우저를 통해 `http://127.0.0.1:5001` 접속창을 띄우는 윈도우 배치 스크립트 작성.

#### [MODIFY] [requirements.txt](file:///C:/_My2026/_EVERBK/BackupManager/requirements.txt)
* **Flask 의존성 추가**:
  * 기존 설치 리스트 최하단에 `flask` 라이브러리를 추가하여, 사용자가 Step 1(의존성 설치)을 진행할 때 서버 관련 종속성 패키지도 누락 없이 한 번에 자동 설치되도록 보완합니다.

---

## Verification Plan

### Automated & Manual Tests
1. **서버 구동 검증:** `run_manager.bat` 실행 시 에러 없이 로컬 포트 5001로 서버가 정상 구동되고 대시보드가 브라우저에 열리는지 테스트합니다.
2. **실시간 로그 스트리밍 검증:** 백업 프로세스를 트리거했을 때, 콘솔 진행 상황이 가상 터미널 뷰에 버퍼링(지연) 없이 흘러나오는지 검증합니다.
3. **토크 로그인 연동 검증:** `Initialize Database (OAuth)` 버튼 클릭 시 새 탭으로 브라우저 인증 창이 성공적으로 열리는지 확인합니다.
4. **증분 업데이트 반영:** 백업 실행 완료 후 대시보드 상태가 실시간으로 분석 갱신되어 최신 파일 카운트와 용량을 표시하는지 확인합니다.
