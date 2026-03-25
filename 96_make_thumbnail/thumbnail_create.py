"""
썸네일 자동 생성
- 06_img/ 앨범 자켓 위에 곡 제목 + 장르 태그 합성
- 10_thumbnail/에 저장

입력:
    04_Suno_Prompt/*.md  → 한글/영문 제목
    08_youtube_script/*.md → 장르 추출
    06_img/*_v1.png, *_v2.png → 배경 이미지

출력:
    10_thumbnail/번호_곡명_v1.png, _v2.png (1280x720)

사용법:
    cd 96_make_thumbnail
    python thumbnail_create.py              # 전체 배치
    python thumbnail_create.py 06_Black_Music_Box   # 특정 곡
"""

import os
import re
import sys

from PIL import Image, ImageDraw, ImageFont, ImageFilter

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
PROMPT_DIR = os.path.join(BASE_DIR, "04_Suno_Prompt")
SCRIPT_DIR = os.path.join(BASE_DIR, "08_youtube_script")
IMG_DIR = os.path.join(BASE_DIR, "06_img")
OUT_DIR = os.path.join(BASE_DIR, "10_thumbnail")

# 폰트 경로
FONT_BOLD = "C:/Windows/Fonts/malgunbd.ttf"
FONT_REGULAR = "C:/Windows/Fonts/malgun.ttf"

WIDTH = 1280
HEIGHT = 720


def safe_print(text):
    print(text.encode('cp949', errors='ignore').decode('cp949'))


def parse_title(prompt_path):
    """04_Suno_Prompt에서 한글/영문 제목 추출"""
    with open(prompt_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # ## Title 섹션에서 제목 추출
    m = re.search(r'## Title\s*\n(.+)', content)
    if not m:
        return None, None

    title_line = m.group(1).strip()

    # "한글제목 English Title" 패턴 분리
    # 영문이 대문자로 시작하는 단어 연속을 찾아서 분리
    # 예: "검은 오르골 Black Music Box" → ("검은 오르골", "Black Music Box")
    # 예: "잊힌 성당 Forgotten Cathedral" → ("잊힌 성당", "Forgotten Cathedral")
    parts = re.match(r'^(.+?)\s+([A-Z][A-Za-z]+(?:\s+[A-Za-z]+)*)$', title_line)
    if parts:
        return parts.group(1).strip(), parts.group(2).strip()

    # 분리 실패 시 전체를 한글 제목으로
    return title_line, None


def parse_genre(script_path):
    """08_youtube_script에서 장르 추출"""
    with open(script_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # "## 유튜브 제목" 라인에서 "- Genre |" 패턴
    m = re.search(r'## 유튜브 제목\s*\n(.+)', content)
    if not m:
        return None

    title_line = m.group(1).strip()
    g = re.search(r'-\s*([A-Za-z][A-Za-z &\-]+?)\s*\|', title_line)
    if g:
        return g.group(1).strip()
    return None


def draw_text_with_shadow(draw, position, text, font, fill, shadow_color=(0, 0, 0), shadow_offset=3):
    """그림자 + 본문 텍스트"""
    x, y = position
    # 그림자 (여러 방향으로 두껍게)
    for dx in range(-shadow_offset, shadow_offset + 1):
        for dy in range(-shadow_offset, shadow_offset + 1):
            if dx == 0 and dy == 0:
                continue
            draw.text((x + dx, y + dy), text, font=font, fill=shadow_color)
    # 본문
    draw.text((x, y), text, font=font, fill=fill)


def draw_genre_tag(draw, position, text, font, bg_color=(255, 255, 255, 180), text_color=(0, 0, 0)):
    """장르 태그 (배경 박스 + 텍스트, 수직 중앙 정렬)"""
    x, y = position
    bbox = font.getbbox(text)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    y_offset = bbox[1]  # 폰트 상단 여백 보정
    pad_x, pad_y = 20, 12

    # 배경 박스
    box_left = x - pad_x
    box_top = y - pad_y
    box_right = x + tw + pad_x
    box_bottom = y + th + pad_y
    draw.rounded_rectangle(
        [box_left, box_top, box_right, box_bottom],
        radius=10,
        fill=bg_color
    )
    # 텍스트 (y_offset 보정으로 수직 중앙)
    text_y = y - y_offset
    draw.text((x, text_y), text, font=font, fill=text_color)


def create_thumbnail(bg_path, title_kr, title_en, genre, version, out_path):
    """썸네일 1장 생성"""
    # 배경 이미지 로드
    img = Image.open(bg_path).convert("RGBA")
    img = img.resize((WIDTH, HEIGHT), Image.LANCZOS)

    # 하단 그라데이션 오버레이 (텍스트 가독성)
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    gradient_start = int(HEIGHT * 0.45)
    for y in range(gradient_start, HEIGHT):
        alpha = int(200 * (y - gradient_start) / (HEIGHT - gradient_start))
        overlay_draw.line([(0, y), (WIDTH, y)], fill=(0, 0, 0, alpha))
    img = Image.alpha_composite(img, overlay)

    draw = ImageDraw.Draw(img)

    # 폰트 로드
    try:
        font_kr = ImageFont.truetype(FONT_BOLD, 100)
        font_en = ImageFont.truetype(FONT_REGULAR, 50)
        font_genre = ImageFont.truetype(FONT_BOLD, 40)
    except OSError:
        safe_print(f"  폰트 로드 실패: {FONT_BOLD}")
        return False

    # 텍스트 위치 계산 (좌하단 기준)
    margin_x = 60
    margin_bottom = 60

    # 한글 제목 (메인, 크고 굵게)
    kr_bbox = font_kr.getbbox(title_kr)
    kr_h = kr_bbox[3] - kr_bbox[1]

    # 영문 제목 높이
    en_h = 0
    if title_en:
        en_bbox = font_en.getbbox(title_en)
        en_h = en_bbox[3] - en_bbox[1] + 20  # 간격 포함

    # 아래에서 위로 배치
    total_text_h = kr_h + en_h
    kr_y = HEIGHT - margin_bottom - total_text_h
    en_y = kr_y + kr_h + 20

    # 한글 제목 그리기
    draw_text_with_shadow(
        draw, (margin_x, kr_y), title_kr,
        font_kr, fill=(255, 255, 255), shadow_offset=6
    )

    # 영문 제목 그리기
    if title_en:
        draw_text_with_shadow(
            draw, (margin_x, en_y), title_en,
            font_en, fill=(220, 220, 220), shadow_offset=4
        )

    # 장르 태그 (우상단)
    if genre:
        genre_upper = genre.upper()
        genre_bbox = font_genre.getbbox(genre_upper)
        genre_w = genre_bbox[2] - genre_bbox[0]
        tag_x = WIDTH - margin_x - genre_w
        tag_y = 40
        draw_genre_tag(
            draw, (tag_x, tag_y), genre_upper,
            font_genre, bg_color=(255, 255, 255, 180), text_color=(20, 20, 20)
        )

    # 버전 표시 (우하단, 반투명 — 구별 용도)
    if version:
        font_ver = ImageFont.truetype(FONT_REGULAR, 60)
        ver_text = version.upper()
        ver_bbox = font_ver.getbbox(ver_text)
        ver_w = ver_bbox[2] - ver_bbox[0]
        ver_h = ver_bbox[3] - ver_bbox[1]
        ver_y_offset = ver_bbox[1]
        draw.text(
            (WIDTH - margin_x - ver_w, HEIGHT - margin_bottom - ver_h - ver_y_offset),
            ver_text, font=font_ver, fill=(255, 255, 255, 80)
        )

    # RGB로 변환 후 저장
    img_rgb = img.convert("RGB")
    img_rgb.save(out_path, quality=95)
    return True


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    # 특정 곡 지정 여부
    target = sys.argv[1] if len(sys.argv) > 1 else None

    # 04_Suno_Prompt 파일 목록
    prompt_files = sorted(f for f in os.listdir(PROMPT_DIR) if f.endswith('.md'))
    safe_print(f"프롬프트 파일 수: {len(prompt_files)}")

    for pf in prompt_files:
        name = pf.replace('.md', '')

        # 특정 곡 필터
        if target and target not in name:
            continue

        prompt_path = os.path.join(PROMPT_DIR, pf)
        script_path = os.path.join(SCRIPT_DIR, pf)

        # 제목 파싱
        title_kr, title_en = parse_title(prompt_path)
        if not title_kr:
            safe_print(f"  [{name}] 제목 파싱 실패 (건너뜀)")
            continue

        # 장르 파싱
        genre = None
        if os.path.exists(script_path):
            genre = parse_genre(script_path)

        safe_print(f"\n처리: {name}")
        safe_print(f"  한글: {title_kr}")
        safe_print(f"  영문: {title_en or '(없음)'}")
        safe_print(f"  장르: {genre or '(없음)'}")

        # v1, v2 각각 생성
        for ver in ['v1', 'v2']:
            bg_file = f"{name}_{ver}.png"
            bg_path = os.path.join(IMG_DIR, bg_file)
            out_path = os.path.join(OUT_DIR, bg_file)

            if not os.path.exists(bg_path):
                safe_print(f"  [{ver}] 배경 이미지 없음: {bg_file} (건너뜀)")
                continue

            if os.path.exists(out_path) and not target:
                safe_print(f"  [{ver}] 이미 존재 (건너뜀)")
                continue

            ok = create_thumbnail(bg_path, title_kr, title_en, genre, ver, out_path)
            if ok:
                size_kb = os.path.getsize(out_path) // 1024
                safe_print(f"  [{ver}] 생성 완료: {bg_file} ({size_kb} KB)")
            else:
                safe_print(f"  [{ver}] 생성 실패")

    safe_print("\n썸네일 생성 완료!")


if __name__ == "__main__":
    main()
