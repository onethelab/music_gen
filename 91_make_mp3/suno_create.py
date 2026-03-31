"""
Suno AI 곡 생성 자동화
- 04_Suno_Prompt/*.md 파일에서 Title, Style, Lyrics를 파싱
- Suno Create 페이지에서 Advanced 모드로 입력 후 생성
- 생성 완료 대기 후 mp3 다운로드 → 05_Mp3/ 저장

사용법:
    cd 91_make_mp3
    python suno_create.py
"""

from playwright.sync_api import sync_playwright
import os
import re
import time
from mutagen.mp3 import MP3

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
PROFILE_DIR = os.path.join(os.path.dirname(__file__), "chrome_suno_profile")
PROMPT_DIR = os.path.join(BASE_DIR, "04_Suno_Prompt")
MP3_DIR = os.path.join(BASE_DIR, "05_Mp3")
SUNO_CREATE_URL = "https://suno.com/create"


def safe_print(text):
    """cp949 인코딩 안전 출력"""
    print(text.encode('cp949', errors='ignore').decode('cp949'))


def parse_prompt_file(filepath):
    """프롬프트 md 파일에서 Title, Style, Lyrics 추출"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    title = ""
    style = ""
    lyrics = ""

    title_match = re.search(r'## Title\s*\n(.+)', content)
    if title_match:
        title = title_match.group(1).strip()

    style_match = re.search(r'## Style(?:\s+of\s+Music)?\s*\n(.+)', content)
    if style_match:
        style = style_match.group(1).strip()

    lyrics_match = re.search(r'## Lyrics\s*\n([\s\S]+?)(?=\n## |\Z)', content)
    if lyrics_match:
        lyrics = lyrics_match.group(1).strip()

    return title, style, lyrics


def check_login_headless(page):
    """Suno 로그인 상태만 확인 (수동 로그인 대기 없음)"""
    page.goto("https://suno.com", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)
    try:
        page.wait_for_selector(
            'button[aria-label="Open user menu"], '
            '[data-testid="user-menu"], '
            'img[alt*="avatar" i], '
            'button:has(img[class*="avatar" i]), '
            'div[class*="avatar" i]',
            timeout=20000,
        )
        safe_print("로그인 확인! (headless)")
        return True
    except Exception:
        return False


def check_login(page):
    """Suno 로그인 상태 확인. 미로그인 시 수동 로그인 대기."""
    safe_print("Suno 로그인 확인 중...")
    page.goto("https://suno.com", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)

    try:
        page.wait_for_selector(
            'button[aria-label="Open user menu"], '
            '[data-testid="user-menu"], '
            'img[alt*="avatar" i], '
            'button:has(img[class*="avatar" i]), '
            'div[class*="avatar" i]',
            timeout=10000,
        )
        safe_print("로그인 확인!")
        return True
    except Exception:
        sign_in = page.query_selector('button:has-text("Sign"), a:has-text("Sign")')
        if sign_in:
            safe_print("로그인되어 있지 않습니다.")
            safe_print("→ 브라우저에서 Suno에 로그인해주세요. (최초 1회)")
            safe_print("→ 로그인 완료를 자동 감지합니다. (최대 6분 대기)")
            try:
                page.wait_for_selector(
                    'button[aria-label="Open user menu"], '
                    '[data-testid="user-menu"], '
                    'img[alt*="avatar" i], '
                    'button:has(img[class*="avatar" i]), '
                    'div[class*="avatar" i]',
                    timeout=360000,
                )
                safe_print("로그인 감지! 세션이 저장되었습니다.")
                return True
            except Exception:
                safe_print("로그인 대기 시간 초과 (6분)")
                return False
        return False


def create_song(page, title, style, lyrics):
    """Suno Create 페이지에서 곡 생성"""

    # 1. Create 페이지 이동
    safe_print("Create 페이지 이동...")
    page.goto(SUNO_CREATE_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2000)

    # 2. 팝업/오버레이 닫기 (최대 3회 시도)
    for dismiss_try in range(3):
        page.wait_for_timeout(1000)
        overlay = page.query_selector(
            'div[class*="overlay"], div[class*="modal"], '
            'div[data-state="open"][aria-hidden="true"], '
            'div[class*="bg-black"], '
            'div.fixed.inset-0[class*="z-"]'
        )
        if overlay:
            page.keyboard.press("Escape")
            page.wait_for_timeout(1000)
            safe_print(f"오버레이 닫기 (시도 {dismiss_try + 1})")
        else:
            break

    # 2.5. 오버레이가 남아있으면 JavaScript로 강제 제거
    page.evaluate("""
        () => {
            document.querySelectorAll('div[data-state="open"][aria-hidden="true"]').forEach(el => el.remove());
            document.querySelectorAll('div.fixed.inset-0').forEach(el => {
                if (el.style.zIndex > 9000 || el.className.includes('z-[')) el.remove();
            });
        }
    """)
    page.wait_for_timeout(500)

    # 3. Advanced 모드 클릭
    advanced_btn = page.query_selector('button:has-text("Advanced")')
    if advanced_btn:
        advanced_btn.click(force=True)
        page.wait_for_timeout(1000)
        safe_print("Advanced 모드 전환 완료")

    # 4. Lyrics Mode를 Manual로 설정
    manual_btn = page.query_selector('button:has-text("Manual")')
    if manual_btn:
        manual_btn.click(force=True)
        page.wait_for_timeout(500)
        safe_print("Lyrics Mode: Manual 설정")

    # 5. 입력 필드 채우기 (클립보드 붙여넣기 사용)
    page.wait_for_timeout(1000)
    textareas = page.query_selector_all('textarea')

    def paste_into(element, text):
        """클립보드를 통해 텍스트 붙여넣기 (React textarea 호환)"""
        element.click()
        page.wait_for_timeout(300)
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        page.wait_for_timeout(200)
        # 클립보드에 텍스트 설정 후 붙여넣기
        page.evaluate(f"""
            (text) => {{
                const el = document.activeElement;
                const dt = new DataTransfer();
                dt.setData('text/plain', text);
                const evt = new ClipboardEvent('paste', {{
                    clipboardData: dt,
                    bubbles: true,
                    cancelable: true
                }});
                el.dispatchEvent(evt);
            }}
        """, text)
        page.wait_for_timeout(500)
        # fallback: 직접 값 설정 + input 이벤트
        actual = element.input_value()
        if len(actual) < len(text) // 2:
            page.evaluate(f"""
                (text) => {{
                    const el = document.activeElement;
                    const nativeSetter = Object.getOwnPropertyDescriptor(
                        window.HTMLTextAreaElement.prototype, 'value'
                    ).set;
                    nativeSetter.call(el, text);
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}
            """, text)
            page.wait_for_timeout(300)

    # Lyrics (textarea[0])
    if len(textareas) > 0:
        paste_into(textareas[0], lyrics)
        actual = textareas[0].input_value()
        safe_print(f"Lyrics 입력 완료 ({len(lyrics)}자 → 실제 {len(actual)}자)")

    # Style (textarea[1])
    if len(textareas) > 1:
        paste_into(textareas[1], style)
        actual = textareas[1].input_value()
        safe_print(f"Style 입력 완료 ({len(style)}자 → 실제 {len(actual)}자)")

    # Title (native setter — React input 호환)
    title_result = page.evaluate("""
        (title) => {
            const inputs = document.querySelectorAll('input[placeholder="Song Title (Optional)"]');
            for (const inp of inputs) {
                if (window.getComputedStyle(inp).visibility === 'visible') {
                    const nativeSetter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                    ).set;
                    nativeSetter.call(inp, title);
                    inp.dispatchEvent(new Event('input', { bubbles: true }));
                    inp.dispatchEvent(new Event('change', { bubbles: true }));
                    return inp.value;
                }
            }
            return '';
        }
    """, title)
    if title_result:
        safe_print(f"Title 입력 완료: {title_result}")
    else:
        safe_print("Title 입력 필드를 찾을 수 없습니다. 제목 없이 진행합니다.")

    page.wait_for_timeout(1000)

    # 6. Create 버튼 클릭
    page.wait_for_timeout(1000)
    create_btn = page.query_selector('button:has-text("Create")')
    if create_btn:
        is_disabled = create_btn.is_disabled()
        if is_disabled:
            safe_print("Create 버튼이 비활성 상태입니다. 입력 필드 확인 필요.")
            # 스크린샷 저장
            page.screenshot(path=os.path.join(BASE_DIR, "99_test", "create_disabled.png"))
            return False
        safe_print("Create 버튼 클릭!")
        create_btn.click()
    else:
        safe_print("Create 버튼을 찾을 수 없습니다.")
        return False

    # 7. 생성 완료 대기 (새 song link 출현 감지 + UI 완료 신호)
    safe_print("곡 생성 대기 중... (최대 5분)")

    # 현재 존재하는 song ID 수집
    existing_links = page.query_selector_all('a[href*="/song/"]')
    existing_ids = set()
    for l in existing_links:
        href = l.get_attribute('href') or ""
        if "/song/" in href:
            existing_ids.add(href.split('/song/')[-1].split('?')[0])

    # 새 곡 ID를 저장할 리스트
    new_song_ids = []

    try:
        page.wait_for_timeout(10000)

        # Phase 1: 새 song link 출현 감지
        for i in range(30):
            page.wait_for_timeout(10000)
            links = page.query_selector_all('a[href*="/song/"]')
            for l in links:
                href = l.get_attribute('href') or ""
                if "/song/" in href:
                    sid = href.split('/song/')[-1].split('?')[0]
                    if sid not in existing_ids and sid not in new_song_ids:
                        new_song_ids.append(sid)
            if new_song_ids:
                safe_print(f"곡 생성 감지! ({(i+1)*10}초 경과, {len(new_song_ids)}곡)")
                break
            safe_print(f"  대기 중... ({(i+1)*10}초)")

        if not new_song_ids:
            safe_print("곡 생성 감지 실패. 타임아웃.")
            return True

        # Phase 2: UI 완료 신호 대기 — duration 텍스트(X:XX) 출현 폴링
        # Suno UI는 렌더링 완료 시 곡 카드에 "2:39" 같은 duration을 표시한다.
        # 생성 중에는 duration이 없고, spinner SVG(animate-spin)가 표시된다.
        safe_print("렌더링 완료 대기 중... (duration 출현 폴링, 최대 3분)")

        for wait_i in range(36):  # 최대 3분 (5초 × 36)
            page.wait_for_timeout(5000)
            completed, details = _check_songs_completed(page, new_song_ids)

            # 10초마다 상태 출력
            if (wait_i + 1) % 2 == 0:
                status = ", ".join(details)
                safe_print(f"  {(wait_i+1)*5}초: {status}")

            if completed:
                safe_print(f"모든 곡 렌더링 완료! ({(wait_i+1)*5}초 경과)")
                # duration 정보 파싱하여 저장
                durations = {}
                for d in details:
                    parts = d.split(':')
                    if len(parts) >= 2:
                        sid_prefix = parts[0]
                        dur_str = ':'.join(parts[1:])
                        dur_sec = _parse_duration_str(dur_str)
                        if dur_sec > 0:
                            # sid_prefix(8자)로 full song_id 매칭
                            for full_id in new_song_ids:
                                if full_id.startswith(sid_prefix):
                                    durations[full_id] = dur_sec
                safe_print(f"  duration 정보: {len(durations)}곡 → {durations}")
                page.wait_for_timeout(20000)  # CDN 안정화 대기 20초
                return True, new_song_ids, durations

        safe_print("렌더링 대기 타임아웃 (3분). 진행합니다.")
        return True, new_song_ids, {}

    except Exception as e:
        safe_print(f"대기 중 오류: {e}")

    return True, new_song_ids, {}


def _parse_duration_str(duration_str):
    """'2:39' → 159초. 파싱 실패 시 0 반환."""
    try:
        parts = duration_str.strip().split(':')
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, IndexError):
        pass
    return 0


def _get_mp3_duration(filepath):
    """mp3 파일의 실제 재생 시간(초) 반환. 실패 시 0."""
    try:
        audio = MP3(filepath)
        return audio.info.length
    except Exception:
        return 0


def _check_songs_completed(page, song_ids):
    """곡 카드의 UI 상태로 렌더링 완료 여부 확인.
    Suno UI는 렌더링 완료 시 곡 카드에 duration 텍스트(예: "2:39")를 표시한다.
    생성 중에는 duration이 없고 spinner(animate-spin)가 표시된다.
    Returns: (all_complete: bool, details: list[str])
    """
    try:
        result = page.evaluate("""
            (songIds) => {
                let allComplete = true;
                const details = [];

                for (const songId of songIds) {
                    const link = document.querySelector(`a[href*="/song/${songId}"]`);
                    if (!link) {
                        details.push(songId.substring(0, 8) + ':NOT_FOUND');
                        allComplete = false;
                        continue;
                    }

                    // clip-row 컨테이너 찾기
                    let card = link.closest('[data-testid="clip-row"]');
                    if (!card) {
                        // fallback: 8단계 위로
                        card = link;
                        for (let i = 0; i < 8; i++) {
                            if (card.parentElement) card = card.parentElement;
                        }
                    }

                    // duration 텍스트 확인 (X:XX 형식)
                    const durationMatch = card.innerText.match(/(\\d{1,2}:\\d{2})/);
                    const hasDuration = !!durationMatch;

                    // spinner(animate-spin) 존재 확인
                    const hasSpinner = !!card.querySelector('.animate-spin');

                    const isComplete = hasDuration && !hasSpinner;
                    const duration = durationMatch ? durationMatch[1] : '--:--';
                    details.push(songId.substring(0, 8) + ':' + (isComplete ? duration : 'PENDING'));

                    if (!isComplete) allComplete = false;
                }

                return { allComplete, details };
            }
        """, song_ids)

        return (result.get('allComplete', False), result.get('details', []))

    except Exception as e:
        safe_print(f"  완료 체크 오류: {e}")
        return (False, [str(e)])


def find_song_ids(page, title):
    """Create 페이지에서 곡 제목에 해당하는 song ID들을 추출"""
    safe_print("\n곡 ID 추출 중...")

    page.keyboard.press("Escape")
    page.wait_for_timeout(1000)

    # Create 페이지에서 먼저 시도 (이미 여기 있을 수 있음)
    song_ids = _extract_song_ids_from_page(page, title)

    # 못 찾으면 Studio로 이동 시도
    if not song_ids:
        try:
            page.goto("https://suno.com/studio", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
            song_ids = _extract_song_ids_from_page(page, title)
        except Exception as e:
            safe_print(f"  Studio 이동 실패: {e}")

    # 그래도 못 찾으면 Create 페이지에서 모든 새 ID 수집
    if not song_ids:
        try:
            page.goto("https://suno.com/create", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(8000)
            song_links = page.query_selector_all('a[href*="/song/"]')
            for link in song_links[:4]:
                href = link.get_attribute('href') or ""
                song_id = href.split('/')[-1].split('?')[0]
                if song_id and len(song_id) > 30 and song_id not in song_ids:
                    song_ids.append(song_id)
                    safe_print(f"  추가: {song_id}")
        except Exception as e:
            safe_print(f"  Create 페이지 ID 수집 실패: {e}")

    return song_ids


def _extract_song_ids_from_page(page, title):
    """현재 페이지에서 제목 매칭하여 song ID 추출"""
    song_links = page.query_selector_all('a[href*="/song/"]')
    song_ids = []

    title_en = title.split()[-1] if title else ""

    for link in song_links:
        href = link.get_attribute('href') or ""
        text = link.inner_text().strip()
        if title_en and title_en.lower() in text.lower():
            song_id = href.split('/')[-1].split('?')[0]
            if song_id and song_id not in song_ids:
                song_ids.append(song_id)
                safe_print(f"  발견: {song_id} - '{text[:50]}'")

    return song_ids


def _get_audio_url_from_song_page(page, song_id):
    """곡 페이지에서 audio_url을 JavaScript로 추출"""
    try:
        page.goto(f"https://suno.com/song/{song_id}", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)

        # Next.js __NEXT_DATA__ 또는 React 상태에서 audio_url 추출
        audio_url = page.evaluate("""
            () => {
                // Method 1: __NEXT_DATA__
                if (window.__NEXT_DATA__) {
                    const json = JSON.stringify(window.__NEXT_DATA__);
                    const match = json.match(/"audio_url":"(https?:\\/\\/[^"]+\\.mp3[^"]*)"/);
                    if (match) return match[1];
                }
                // Method 2: audio/source 태그
                const audio = document.querySelector('audio source[type="audio/mpeg"], audio[src*=".mp3"]');
                if (audio) return audio.src || audio.getAttribute('src');
                // Method 3: 페이지 내 모든 텍스트에서 CDN URL 추출
                const body = document.body.innerHTML;
                const cdnMatch = body.match(/(https?:\\/\\/cdn[^"'\\s]+\\.mp3[^"'\\s]*)/);
                if (cdnMatch) return cdnMatch[1];
                return null;
            }
        """)
        return audio_url
    except Exception as e:
        safe_print(f"  audio_url 추출 실패: {e}")
        return None


def _download_via_browser(page, url, filepath):
    """Playwright의 download 이벤트를 사용한 브라우저 네이티브 다운로드"""
    try:
        with page.expect_download(timeout=60000) as download_info:
            page.evaluate(f"""
                () => {{
                    const a = document.createElement('a');
                    a.href = '{url}';
                    a.download = 'download.mp3';
                    document.body.appendChild(a);
                    a.click();
                    a.remove();
                }}
            """)
        download = download_info.value
        download.save_as(filepath)
        return True
    except Exception as e:
        safe_print(f"  브라우저 다운로드 실패: {e}")
        return False


def download_mp3(page, song_ids, prompt_basename, expected_durations=None):
    """Suno 곡 페이지에서 audio_url 추출 후 다운로드 (duration 검증 포함)

    다운로드 전략:
    1. 곡 페이지에서 audio_url (CDN URL) 추출
    2. cdn1.suno.ai/{id}.mp3 패턴 시도
    3. 브라우저 네이티브 다운로드 (download 이벤트)
    4. fallback: page.request.get (audiopipe)

    Args:
        page: Playwright page (브라우저 세션의 쿠키/인증 사용)
        song_ids: 다운로드할 곡 ID 리스트
        prompt_basename: 파일명 접두사 (예: "20_Empty_Seats")
        expected_durations: {song_id: 예상_초} 딕셔너리 (Suno UI에서 가져온 값)
    """
    if expected_durations is None:
        expected_durations = {}

    safe_print(f"\nmp3 다운로드 시작 ({len(song_ids)}곡)...")
    os.makedirs(MP3_DIR, exist_ok=True)

    downloaded = []
    for i, song_id in enumerate(song_ids):
        version = f"v{i+1}"
        filename = f"{prompt_basename}_{version}.mp3"
        filepath = os.path.join(MP3_DIR, filename)

        expected_sec = expected_durations.get(song_id, 0)
        safe_print(f"\n  [{version}] song_id: {song_id[:16]}...")
        safe_print(f"  저장: {filename}")
        if expected_sec > 0:
            safe_print(f"  예상 duration: {int(expected_sec//60)}:{int(expected_sec%60):02d} ({expected_sec}초)")

        try:
            success = False

            # 전략 1: 곡 페이지에서 audio_url 추출
            safe_print(f"  전략1: 곡 페이지에서 audio_url 추출...")
            audio_url = _get_audio_url_from_song_page(page, song_id)
            if audio_url:
                safe_print(f"  audio_url 발견: {audio_url[:80]}...")
                response = page.request.get(audio_url)
                with open(filepath, 'wb') as f:
                    f.write(response.body())
                actual_sec = _get_mp3_duration(filepath)
                file_size = os.path.getsize(filepath)
                safe_print(f"  결과: {file_size // 1024} KB, {actual_sec:.1f}초")
                if expected_sec <= 0 or actual_sec >= expected_sec * 0.9:
                    safe_print(f"  전략1 성공!")
                    success = True

            # 전략 2: cdn1.suno.ai CDN URL
            if not success:
                cdn_url = f"https://cdn1.suno.ai/{song_id}.mp3"
                safe_print(f"  전략2: CDN URL 시도 → {cdn_url}")
                try:
                    response = page.request.get(cdn_url)
                    if response.status == 200 and len(response.body()) > 10000:
                        with open(filepath, 'wb') as f:
                            f.write(response.body())
                        actual_sec = _get_mp3_duration(filepath)
                        file_size = os.path.getsize(filepath)
                        safe_print(f"  결과: {file_size // 1024} KB, {actual_sec:.1f}초")
                        if expected_sec <= 0 or actual_sec >= expected_sec * 0.9:
                            safe_print(f"  전략2 성공!")
                            success = True
                    else:
                        safe_print(f"  CDN 응답: status={response.status}, size={len(response.body())}")
                except Exception as e:
                    safe_print(f"  CDN 실패: {e}")

            # 전략 3: 곡 페이지의 다운로드 버튼 (three-dot menu)
            if not success:
                safe_print(f"  전략3: UI 다운로드 버튼 시도...")
                try:
                    page.goto(f"https://suno.com/song/{song_id}", wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(3000)
                    # three-dot 메뉴 찾기
                    menu_btn = page.query_selector('button[aria-label*="more" i], button[aria-label*="menu" i], button:has(svg[class*="dots" i]), button:has(svg[class*="ellipsis" i])')
                    if not menu_btn:
                        # fallback: ... 텍스트 버튼
                        menu_btn = page.query_selector('button:has-text("...")')
                    if menu_btn:
                        menu_btn.click()
                        page.wait_for_timeout(1000)
                        # Download 옵션 클릭
                        dl_option = page.query_selector('button:has-text("Download"), [role="menuitem"]:has-text("Download"), a:has-text("Download")')
                        if dl_option:
                            # Audio(MP3) 선택
                            dl_option.click()
                            page.wait_for_timeout(1000)
                            mp3_option = page.query_selector('button:has-text("Audio"), button:has-text("MP3"), [role="menuitem"]:has-text("Audio")')
                            if mp3_option:
                                dl_success = _download_via_browser(page, "", filepath)
                                if not dl_success:
                                    mp3_option.click()
                                    page.wait_for_timeout(5000)
                            safe_print(f"  UI 다운로드 시도 완료")
                            if os.path.exists(filepath):
                                actual_sec = _get_mp3_duration(filepath)
                                file_size = os.path.getsize(filepath)
                                safe_print(f"  결과: {file_size // 1024} KB, {actual_sec:.1f}초")
                                if expected_sec <= 0 or actual_sec >= expected_sec * 0.9:
                                    safe_print(f"  전략3 성공!")
                                    success = True
                except Exception as e:
                    safe_print(f"  UI 다운로드 실패: {e}")

            # 전략 4: fallback — audiopipe (truncated일 수 있음)
            if not success:
                url = f"https://audiopipe.suno.ai/?item_id={song_id}"
                safe_print(f"  전략4: audiopipe fallback → {url}")
                max_attempts = 3
                for attempt in range(max_attempts):
                    response = page.request.get(url)
                    with open(filepath, 'wb') as f:
                        f.write(response.body())
                    file_size = os.path.getsize(filepath)
                    actual_sec = _get_mp3_duration(filepath)
                    safe_print(f"  시도 {attempt+1}: {file_size // 1024} KB, {actual_sec:.1f}초")
                    if expected_sec <= 0 or actual_sec >= expected_sec * 0.9:
                        safe_print(f"  전략4 성공!")
                        success = True
                        break
                    if attempt < max_attempts - 1:
                        time.sleep(30)

            if not success:
                actual_sec = _get_mp3_duration(filepath) if os.path.exists(filepath) else 0
                safe_print(f"  경고: 모든 전략 실패. 최종 duration {actual_sec:.1f}초 (예상 {expected_sec}초)")

            if os.path.exists(filepath):
                downloaded.append(filepath)
        except Exception as e:
            safe_print(f"  실패: {e}")

    return downloaded


def main():
    prompt_files = sorted([
        f for f in os.listdir(PROMPT_DIR)
        if f.endswith('.md') and not f.startswith('00_')
    ])

    if not prompt_files:
        print("04_Suno_Prompt에 프롬프트 파일이 없습니다.")
        return

    safe_print(f"\n프롬프트 파일 목록:")
    for i, f in enumerate(prompt_files):
        safe_print(f"  [{i}] {f}")

    # 이미 mp3가 있는 프롬프트는 건너뛰고, 아직 생성 안 된 첫 번째 파일 선택
    target_file = None
    for pf in prompt_files:
        basename = os.path.splitext(pf)[0]  # 예: "02_Blueprint_Mind"
        existing_mp3 = [f for f in os.listdir(MP3_DIR) if f.startswith(basename + '_') and f.endswith('.mp3')] if os.path.exists(MP3_DIR) else []
        if not existing_mp3:
            target_file = pf
            break
        else:
            safe_print(f"  {pf}: mp3 존재 ({len(existing_mp3)}개) → 건너뜁니다")

    if target_file is None:
        safe_print("모든 프롬프트에 대해 mp3가 이미 존재합니다.")
        return
    filepath = os.path.join(PROMPT_DIR, target_file)
    safe_print(f"\n처리할 파일: {target_file}")

    title, style, lyrics = parse_prompt_file(filepath)
    safe_print(f"  Title: {title}")
    safe_print(f"  Style: {style[:80]}...")
    safe_print(f"  Lyrics: {len(lyrics)}자")

    with sync_playwright() as p:
        # headless=False (Suno 봇 감지 우회)
        browser = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )

        page = browser.pages[0] if browser.pages else browser.new_page()

        # 로그인 확인 (headless에서는 수동 로그인 불가)
        if not check_login_headless(page):
            browser.close()
            safe_print("미로그인 상태. 브라우저를 열어 수동 로그인합니다...")
            # headless=False로 재시작하여 수동 로그인
            browser = p.chromium.launch_persistent_context(
                user_data_dir=PROFILE_DIR,
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = browser.pages[0] if browser.pages else browser.new_page()
            if not check_login(page):
                safe_print("로그인 실패. 종료합니다.")
                browser.close()
                return
            # 로그인 성공 — headless 전환 없이 그대로 진행
            safe_print("로그인 완료. 브라우저 모드로 계속 진행...")

        # 곡 생성
        result = create_song(page, title, style, lyrics)
        success = result[0] if isinstance(result, tuple) else result
        created_song_ids = result[1] if isinstance(result, tuple) and len(result) > 1 else []
        expected_durations = result[2] if isinstance(result, tuple) and len(result) > 2 else {}

        if success:
            safe_print("\n곡 생성 프로세스 완료!")

            # create_song에서 이미 song_ids를 가져왔으면 사용, 아니면 페이지에서 추출
            song_ids = created_song_ids if created_song_ids else find_song_ids(page, title)

            if song_ids:
                prompt_basename = os.path.splitext(target_file)[0]
                downloaded = download_mp3(page, song_ids, prompt_basename, expected_durations)
                safe_print(f"\n총 {len(downloaded)}곡 다운로드 완료!")
                for f in downloaded:
                    safe_print(f"  {f}")
            else:
                safe_print("곡 ID를 찾지 못했습니다. Studio에서 수동 확인 필요.")

        browser.close()


if __name__ == "__main__":
    main()
