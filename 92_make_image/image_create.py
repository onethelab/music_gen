"""
앨범 자켓 이미지 생성 자동화
- 05_image_prompt/*.md 파일에서 이미지 프롬프트를 파싱
- Gemini API (Nano Banana 2)로 이미지 생성
- 06_img/에 저장

사용법:
    cd 92_make_image
    python image_create.py

환경변수:
    GEMINI_API_KEY: Gemini API 키 (필수)
    또는 92_make_image/.env 파일에 GEMINI_API_KEY=xxx 형식으로 저장
"""

import os
import re
import sys
from io import BytesIO

from google import genai
from google.genai import types
from PIL import Image

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
PROMPT_DIR = os.path.join(BASE_DIR, "05_image_prompt")
IMG_DIR = os.path.join(BASE_DIR, "06_img")
ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")

TARGET_WIDTH = 1280
TARGET_HEIGHT = 720


def safe_print(text):
    """cp949 인코딩 안전 출력"""
    print(text.encode('cp949', errors='ignore').decode('cp949'))


def load_api_key():
    """환경변수 또는 .env 파일에서 API 키 로드"""
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key

    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith("GEMINI_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")

    return None


def parse_image_prompt(filepath):
    """이미지 프롬프트 md 파일에서 v1/v2 프롬프트 텍스트 추출

    v1/v2 분리 형식:
        ## 이미지 프롬프트 (v1)
        ## 이미지 프롬프트 (v2)

    단일 프롬프트 형식 (하위 호환):
        ## 이미지 프롬프트
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    title = ""
    prompts = {}

    title_match = re.search(r'^# (.+)', content, re.MULTILINE)
    if title_match:
        title = title_match.group(1).strip()

    # v1/v2 분리 형식 시도
    for version in ["v1", "v2"]:
        match = re.search(
            r'## 이미지 프롬프트 \(' + version + r'\)\s*\n([\s\S]+?)(?=\n## |\Z)',
            content
        )
        if match:
            prompts[version] = match.group(1).strip()

    # 분리 형식이 없으면 단일 프롬프트를 v1/v2 공용으로 사용
    if not prompts:
        match = re.search(
            r'## 이미지 프롬프트\s*\n([\s\S]+?)(?=\n## |\Z)', content
        )
        if match:
            prompt = match.group(1).strip()
            prompts["v1"] = prompt
            prompts["v2"] = prompt

    return title, prompts


def generate_image(api_key, prompt):
    """Gemini API로 이미지 생성 (매번 새 클라이언트로 독립 세션)"""
    # 매 호출마다 새 클라이언트 생성 → 이전 세션 오염 방지
    client = genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model="gemini-2.5-flash-image",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
        ),
    )

    for part in response.candidates[0].content.parts:
        if part.inline_data and part.inline_data.mime_type.startswith("image/"):
            image_bytes = part.inline_data.data
            img = Image.open(BytesIO(image_bytes))
            return img

    return None


def resize_image(img, width, height):
    """비율 유지하며 크롭 후 리사이즈 (찌그러짐 방지)"""
    target_ratio = width / height
    img_ratio = img.width / img.height

    if img_ratio > target_ratio:
        # 이미지가 더 넓음 → 좌우 크롭
        new_w = int(img.height * target_ratio)
        left = (img.width - new_w) // 2
        img = img.crop((left, 0, left + new_w, img.height))
    else:
        # 이미지가 더 높음 → 상하 크롭
        new_h = int(img.width / target_ratio)
        top = (img.height - new_h) // 2
        img = img.crop((0, top, img.width, top + new_h))

    return img.resize((width, height), Image.LANCZOS)


def main():
    api_key = load_api_key()
    if not api_key:
        safe_print("GEMINI_API_KEY가 설정되지 않았습니다.")
        safe_print("방법 1: 환경변수 설정 - set GEMINI_API_KEY=your_key")
        safe_print("방법 2: 92_make_image/.env 파일에 GEMINI_API_KEY=your_key 저장")
        sys.exit(1)

    os.makedirs(IMG_DIR, exist_ok=True)

    prompt_files = sorted([
        f for f in os.listdir(PROMPT_DIR)
        if f.endswith('.md')
    ])

    if not prompt_files:
        safe_print("05_image_prompt에 이미지 프롬프트 파일이 없습니다.")
        return

    safe_print(f"\n프롬프트 파일 목록:")
    for i, f in enumerate(prompt_files):
        safe_print(f"  [{i}] {f}")

    for pf in prompt_files:
        filepath = os.path.join(PROMPT_DIR, pf)
        title, prompts = parse_image_prompt(filepath)

        if not prompts:
            safe_print(f"\n{pf}: 프롬프트를 찾을 수 없습니다. 건너뜁니다.")
            continue

        safe_print(f"\n처리: {pf}")
        safe_print(f"  제목: {title}")

        basename = os.path.splitext(pf)[0]

        # v1, v2 각각 별도 이미지 생성 (각자의 프롬프트 사용)
        for version in ["v1", "v2"]:
            output_path = os.path.join(IMG_DIR, f"{basename}_{version}.png")

            if os.path.exists(output_path):
                safe_print(f"  이미 존재: {basename}_{version}.png (건너뜁니다)")
                continue

            prompt = prompts.get(version, "")
            if not prompt:
                safe_print(f"  {version} 프롬프트 없음, 건너뜁니다.")
                continue

            safe_print(f"  프롬프트 ({version}): {prompt[:100]}...")
            safe_print(f"  이미지 생성 중... ({version})")
            try:
                img = generate_image(api_key, prompt)
                if img:
                    img = resize_image(img, TARGET_WIDTH, TARGET_HEIGHT)
                    img.save(output_path, "PNG")
                    file_size = os.path.getsize(output_path)
                    safe_print(f"  저장 완료: {basename}_{version}.png ({file_size // 1024} KB)")
                else:
                    safe_print(f"  이미지 생성 실패 ({version}): 응답에 이미지가 없습니다.")
            except Exception as e:
                safe_print(f"  오류 ({version}): {e}")

    safe_print("\n모든 이미지 생성 완료!")


if __name__ == "__main__":
    main()
