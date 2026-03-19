"""
Suno Studio에서 최근 곡 다운로드 구조 탐색
- 곡 카드의 ... 메뉴 → Download 옵션 찾기
"""

from playwright.sync_api import sync_playwright
import os

PROFILE_DIR = os.path.join(os.path.dirname(__file__), "chrome_suno_profile")


def safe_print(text):
    print(text.encode('cp949', errors='ignore').decode('cp949'))


def explore_download():
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )

        page = browser.pages[0] if browser.pages else browser.new_page()
        page.goto("https://suno.com/studio", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(3000)

        # 최근 곡 목록의 첫 번째 곡에서 ... 메뉴 찾기
        print("=== 곡 목록 탐색 ===")

        # 곡 항목에 마우스 hover하면 메뉴가 나타날 수 있음
        # 먼저 곡 관련 요소 찾기
        song_links = page.query_selector_all('a[href*="/song/"]')
        print(f"song 링크 수: {len(song_links)}")
        for i, link in enumerate(song_links[:5]):
            href = link.get_attribute('href') or ""
            text = link.inner_text().strip()[:50]
            safe_print(f"  [{i}] href={href}, text='{text}'")

        # 더보기(...) 버튼 찾기
        print("\n=== 더보기 메뉴 탐색 ===")
        more_btns = page.query_selector_all('button[aria-label*="more" i], button[aria-label*="option" i], button[aria-label*="menu" i], button[aria-label*="action" i]')
        print(f"more/option 버튼 수: {len(more_btns)}")
        for i, btn in enumerate(more_btns[:5]):
            aria = btn.get_attribute('aria-label') or ""
            safe_print(f"  [{i}] aria='{aria}'")

        # 첫 번째 곡 영역에서 hover하여 숨겨진 메뉴 찾기
        if song_links:
            print("\n=== 첫 번째 곡에 hover ===")
            song_links[0].hover()
            page.wait_for_timeout(1000)

            # hover 후 나타나는 버튼들
            visible_btns = page.query_selector_all('button:visible')
            for btn in visible_btns[:30]:
                aria = btn.get_attribute('aria-label') or ""
                text = btn.inner_text().strip()[:30]
                if aria or text:
                    safe_print(f"  button: aria='{aria}', text='{text}'")

        # 직접 다운로드 URL 패턴 확인
        # Suno 곡 URL이 있으면 /song/{id} 형태
        if song_links:
            first_href = song_links[0].get_attribute('href') or ""
            song_id = first_href.split('/')[-1] if '/song/' in first_href else ""
            if song_id:
                safe_print(f"\n첫 번째 곡 ID: {song_id}")
                safe_print(f"예상 다운로드 URL: https://cdn1.suno.ai/{song_id}.mp3")

        browser.close()


if __name__ == "__main__":
    explore_download()
