"""21_Selfie_Law 다운로드 테스트 (새 다운로드 전략 검증)"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '91_make_mp3'))

from playwright.sync_api import sync_playwright
from suno_create import download_mp3, check_login_headless, safe_print, PROFILE_DIR

SONG_IDS = [
    "01164413-5831-4d5e-968e-139ef8766a38",
    "b4ffe79c-be0c-4c19-996b-ee31652321ab",
]
EXPECTED_DURATIONS = {
    "01164413-5831-4d5e-968e-139ef8766a38": 121,
    "b4ffe79c-be0c-4c19-996b-ee31652321ab": 136,
}

with sync_playwright() as p:
    browser = p.chromium.launch_persistent_context(
        user_data_dir=PROFILE_DIR,
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
    )
    page = browser.pages[0] if browser.pages else browser.new_page()

    if not check_login_headless(page):
        safe_print("로그인 필요!")
        browser.close()
        sys.exit(1)

    downloaded = download_mp3(page, SONG_IDS, "21_Selfie_Law", EXPECTED_DURATIONS)
    safe_print(f"\n다운로드 결과: {len(downloaded)}곡")
    for f in downloaded:
        safe_print(f"  {f}")

    browser.close()
