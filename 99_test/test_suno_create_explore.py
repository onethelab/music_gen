"""
Suno Create 페이지 구조 탐색
- Custom Mode UI 요소(입력 필드, 버튼)의 셀렉터를 파악한다
"""

from playwright.sync_api import sync_playwright
import os

PROFILE_DIR = os.path.join(os.path.dirname(__file__), "chrome_suno_profile")
SUNO_CREATE_URL = "https://suno.com/create"


def explore_create_page():
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )

        page = browser.pages[0] if browser.pages else browser.new_page()
        print(f"Suno Create 페이지 접속 중... {SUNO_CREATE_URL}")
        page.goto(SUNO_CREATE_URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(3000)

        # Custom Mode 버튼 찾기
        print("\n=== Custom Mode 버튼 탐색 ===")
        custom_btn = page.query_selector('button:has-text("Advanced")')
        if custom_btn:
            print(f"Advanced 버튼 발견")
            custom_btn.click()
            page.wait_for_timeout(2000)
        else:
            print("Custom 버튼 못 찾음. 페이지 버튼 목록:")
            buttons = page.query_selector_all('button')
            for btn in buttons[:20]:
                txt = btn.inner_text().strip()
                if txt:
                    print(f"  - button: '{txt}'")

        # 입력 필드 탐색
        print("\n=== 입력 필드 탐색 ===")

        # textarea 탐색
        textareas = page.query_selector_all('textarea')
        print(f"textarea 개수: {len(textareas)}")
        for i, ta in enumerate(textareas):
            placeholder = ta.get_attribute('placeholder') or ""
            name = ta.get_attribute('name') or ""
            aria = ta.get_attribute('aria-label') or ""
            info = f"  textarea[{i}]: placeholder='{placeholder}', name='{name}', aria='{aria}'"
            print(info.encode('cp949', errors='ignore').decode('cp949'))

        # input 탐색
        inputs = page.query_selector_all('input[type="text"], input:not([type])')
        print(f"\ninput 개수: {len(inputs)}")
        for i, inp in enumerate(inputs):
            placeholder = inp.get_attribute('placeholder') or ""
            name = inp.get_attribute('name') or ""
            aria = inp.get_attribute('aria-label') or ""
            info = f"  input[{i}]: placeholder='{placeholder}', name='{name}', aria='{aria}'"
            print(info.encode('cp949', errors='ignore').decode('cp949'))

        # label 탐색
        print("\n=== Label 탐색 ===")
        labels = page.query_selector_all('label')
        for lb in labels:
            txt = lb.inner_text().strip()
            if txt:
                print(f"  label: '{txt}'")

        # Create/Generate 버튼 탐색
        print("\n=== Create/Generate 버튼 탐색 ===")
        for text in ["Create", "Generate", "Make"]:
            btn = page.query_selector(f'button:has-text("{text}")')
            if btn:
                print(f"  '{text}' 버튼 발견: '{btn.inner_text().strip()}'")

        # 페이지 주요 텍스트
        print("\n=== 페이지 텍스트 (앞 800자) ===")
        body_text = page.inner_text('body')
        safe_text = body_text[:800].encode('cp949', errors='ignore').decode('cp949')
        print(safe_text)

        browser.close()


if __name__ == "__main__":
    explore_create_page()
