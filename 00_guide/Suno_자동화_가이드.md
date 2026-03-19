# Suno AI 브라우저 자동화 가이드

이 문서는 Playwright를 사용하여 Suno AI에 자동으로 접속·작업하는 방법을 기술한다.

---

## 환경 구성

### 설치된 패키지
- Python 3.10.8
- Playwright 1.58.0 (`python -m pip install playwright`)
- Chromium (Playwright 내장, `python -m playwright install chromium`)

### 주요 경로
| 항목 | 경로 |
|------|------|
| 테스트 스크립트 | `99_test/test_suno_login.py` 등 |
| Playwright 전용 Chrome 프로필 | `91_make_mp3/chrome_suno_profile/` |
| Suno 프롬프트 파일 | `04_Suno_Prompt/*.md` |
| mp3 저장 폴더 | `05_Mp3/` |

---

## 핵심 구조: 별도 프로필 방식

### 왜 별도 프로필인가
- Chrome은 하나의 User Data 디렉토리를 **동시에 하나의 프로세스만** 사용할 수 있다
- 사용자의 기본 Chrome 프로필을 사용하면 **사용자가 Chrome을 동시에 사용할 수 없다**
- Playwright 전용 프로필(`chrome_suno_profile/`)을 사용하면 **충돌 없이 독립 실행** 가능

### 인증 방식
- Suno는 Clerk 기반 인증을 사용하며, 세션은 localStorage에 저장된다
- 쿠키 복사 방식은 작동하지 않는다 (시도 후 확인됨)
- Playwright가 Chromium을 직접 실행하면 localStorage가 유지되어 세션이 보존된다

### 로그인 절차
1. **최초 1회**: 스크립트 실행 → Chromium 브라우저가 열림 → Suno 로그인 화면에서 **사용자가 수동 로그인** (Google/Discord 등) → 세션이 `chrome_suno_profile/`에 저장됨
2. **이후 실행**: 저장된 세션으로 자동 로그인 (수동 작업 불필요)

---

## 시도한 방법과 결과

### 1. 쿠키 복사 방식 (실패)
- Chrome 프로필의 Cookies, Login Data 등을 임시 폴더에 복사
- Suno는 localStorage 기반 인증이라 쿠키만으로 로그인 유지 안 됨

### 2. CDP(Chrome DevTools Protocol) 연결 방식 (실패)
- `--remote-debugging-port=9222`로 Chrome 실행 후 Playwright가 CDP로 연결
- 기존 Chrome User Data와 동시 사용 불가 (프로필 락 충돌)
- `taskkill`로 Chrome 종료 후에도 락 파일이 남아 포트가 열리지 않음

### 3. Playwright 전용 프로필 방식 (성공)
- Playwright 내장 Chromium + 별도 프로필 디렉토리 사용
- 사용자 Chrome과 완전히 독립적으로 동작
- 최초 1회 수동 로그인 후 세션 영구 유지

---

## 현재 완성된 기능

### test_suno_login.py
- Suno 접속 및 로그인 상태 확인
- 로그인 시: 닉네임, 크레딧 정보 출력
- 미로그인 시: 수동 로그인 대기 (3분) → 세션 자동 저장

### 실행 방법
```
cd 99_test
python test_suno_login.py
```

### 확인된 정보 (2026-03-13)
- 닉네임: gunug850
- 크레딧: 2210 credits

---

## 곡 생성 자동화 (구현 완료)

### 스크립트: `91_make_mp3/suno_create.py`

전체 파이프라인을 한 번에 실행한다:
```
cd 91_make_mp3
python suno_create.py
```

### 동작 순서
1. `04_Suno_Prompt/` 첫 번째 md 파일에서 Title, Style, Lyrics 파싱
2. Suno Create 페이지 접속 → Advanced 모드 전환
3. Lyrics Mode: Manual 설정
4. Lyrics, Style, Title 입력
5. Create 버튼 클릭
6. 곡 생성 완료 대기 (최대 5분)
7. Studio 페이지에서 곡 ID 추출
8. CDN(`https://cdn1.suno.ai/{song_id}.mp3`)에서 직접 다운로드
9. `05_Mp3/` 폴더에 저장

### Suno Create 페이지 UI 셀렉터 (2026-03-13 기준)

| 항목 | 셀렉터 | 비고 |
|------|--------|------|
| Advanced 모드 | `button:has-text("Advanced")` | (구 Custom Mode) |
| Lyrics Mode Manual | `button:has-text("Manual")` | force click 필요 |
| Lyrics 입력 | `textarea[0]` | placeholder: "Write some lyrics..." |
| Style 입력 | `textarea[1]` | placeholder: 장르, 아티스트... |
| Title 입력 | `input[placeholder*="Song Title"]` | visible한 것 선택 |
| Create 버튼 | `button:has-text("Create")` | |
| 곡 링크 | `a[href*="/song/"]` | href에서 song ID 추출 |

### mp3 다운로드 방식
- Suno UI의 Download 메뉴 대신 **CDN URL 직접 다운로드** 사용
- URL 패턴: `https://cdn1.suno.ai/{song_id}.mp3`
- `urllib.request.urlretrieve()`로 다운로드

### mp3 파일명 규칙
```
01_Midnight_Compile_v1.mp3
01_Midnight_Compile_v2.mp3
```
- 번호는 04_Suno_Prompt의 번호와 일치
- Suno는 1회 생성에 2곡을 만들므로 `_v1`, `_v2`로 구분

---

## 주의사항
- `chrome_suno_profile/` 폴더를 삭제하면 로그인 세션이 사라지므로 다시 수동 로그인 필요
- Playwright 실행 중 사용자 Chrome은 **그대로 사용 가능** (별도 프로필이므로 충돌 없음)
- `headless=False`로 실행하면 브라우저 창이 보이고, 최소화해도 정상 동작
- Suno UI가 변경되면 셀렉터 수정이 필요할 수 있음
