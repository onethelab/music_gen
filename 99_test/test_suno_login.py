"""
Suno AI 로그인 상태 확인 테스트
- Playwright 전용 Chrome 프로필 사용 (사용자 Chrome과 충돌 없음)
- 첫 실행: Suno 로그인 화면 → 사용자가 수동 로그인 → 세션 저장
- 이후 실행: 로그인 유지 상태로 자동 작업
"""

from playwright.sync_api import sync_playwright
import os

# Playwright 전용 프로필 경로 (사용자 Chrome과 별도)
PROFILE_DIR = os.path.join(os.path.dirname(__file__), "chrome_suno_profile")
SUNO_URL = "https://suno.com"


def check_suno_login():
    with sync_playwright() as p:
        # Playwright 전용 프로필로 Chromium 실행
        browser = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )

        page = browser.pages[0] if browser.pages else browser.new_page()
        print(f"Suno 접속 중... {SUNO_URL}")
        page.goto(SUNO_URL, wait_until="networkidle", timeout=30000)

        # 로그인 여부 확인
        try:
            profile_btn = page.wait_for_selector(
                'button[aria-label="Open user menu"], '
                '[data-testid="user-menu"], '
                'img[alt*="avatar" i], '
                'button:has(img[class*="avatar" i]), '
                'div[class*="avatar" i]',
                timeout=10000,
            )

            if profile_btn:
                profile_btn.click()
                page.wait_for_timeout(1000)

                nickname = None
                selectors = [
                    '[class*="username"]',
                    '[class*="display-name"]',
                    '[class*="profile"] span',
                    '[class*="user"] span',
                    '[role="menu"] span',
                    '[role="menu"] p',
                ]

                for sel in selectors:
                    elements = page.query_selector_all(sel)
                    for el in elements:
                        text = el.inner_text().strip()
                        if text and len(text) > 1 and text not in (
                            "Sign out", "Settings", "Profile", "Log out",
                        ):
                            nickname = text
                            break
                    if nickname:
                        break

                if nickname:
                    print(f"로그인 확인! 닉네임: {nickname}")
                else:
                    # 메뉴 전체 텍스트에서 닉네임 추출 시도
                    menu = page.query_selector('[role="menu"]')
                    menu_text = menu.inner_text() if menu else ""
                    # Profile, Account, Theme, Sign Out 등을 제외한 텍스트가 닉네임
                    skip = {"Profile", "Account", "Theme", "Sign Out", "Log out", "Settings", ""}
                    lines = [l.strip() for l in menu_text.split("\n") if l.strip() not in skip]
                    if lines:
                        print(f"로그인 확인! 닉네임: {lines[0]}")
                    else:
                        print(f"로그인 확인! (메뉴: {menu_text.replace(chr(10), ', ')[:200]})")
            else:
                print("로그인되어 있지 않습니다.")

        except Exception:
            sign_in = page.query_selector(
                'button:has-text("Sign"), a:has-text("Sign")'
            )
            if sign_in:
                print("로그인되어 있지 않습니다.")
                print("→ 브라우저에서 Suno에 로그인해주세요. (최초 1회)")
                print("→ 로그인 완료를 자동 감지합니다. (최대 3분 대기)")

                # 로그인 완료 대기: 아바타/프로필 요소가 나타날 때까지
                try:
                    page.wait_for_selector(
                        'button[aria-label="Open user menu"], '
                        '[data-testid="user-menu"], '
                        'img[alt*="avatar" i], '
                        'button:has(img[class*="avatar" i]), '
                        'div[class*="avatar" i]',
                        timeout=180000,  # 3분 대기
                    )
                    print("로그인 감지! 세션이 저장되었습니다.")
                    print("다음 실행부터 자동 로그인됩니다.")
                except Exception:
                    print("로그인 대기 시간 초과 (3분)")
            else:
                print(f"페이지 상태 확인 필요. 현재 URL: {page.url}")

        # 페이지 정보 출력 (디버깅용)
        print(f"\n--- 페이지 정보 ---")
        print(f"URL: {page.url}")
        print(f"Title: {page.title()}")

        # 좌측 네비게이션 메뉴 텍스트 출력
        nav = page.query_selector('nav')
        if nav:
            print(f"Nav: {nav.inner_text()[:200]}")

        # 페이지 내 주요 텍스트 출력
        body_text = page.inner_text('body')
        # 이모지 등 cp949 인코딩 불가 문자 제거 후 출력
        safe_text = body_text[:500].encode('cp949', errors='ignore').decode('cp949')
        print(f"Body(500자): {safe_text}")

        browser.close()


if __name__ == "__main__":
    check_suno_login()
