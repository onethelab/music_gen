"""
플레이리스트 썸네일 생성기
- 기존 곡 썸네일을 4x4 격자 콜라주로 합치고
- 텍스트 오버레이 (제목 + 부제)
- 1280x720 출력

사용법: python make_thumbnail.py
"""

from PIL import Image, ImageDraw, ImageFont, ImageFilter
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
IMG_DIR = BASE_DIR / "06_img"
OUTPUT_DIR = Path(__file__).parent

# 출력 해상도
WIDTH = 1280
HEIGHT = 720

# 콜라주에 사용할 썸네일 (플레이리스트 수록곡 순서)
THUMBNAILS = [
    "19_Steel_Forest_v1.png",
    "14_Chrome_Highway_v1.png",
    "15_Derelict_Protocol_v1.png",
    "25_Dead_Signal_v1.png",
    "22_Blackout_Chase_v1.png",
    "23_Rusted_Orbit_v2.png",
    "24_Terminal_Loop_v1.png",
    "26_Iron_Descent_v1.png",
    "27_Neon_Hemorrhage_v1.png",
    "19_Steel_Forest_v2.png",
    "14_Chrome_Highway_v2.png",
    "15_Derelict_Protocol_v2.png",
    "22_Blackout_Chase_v2.png",
    "25_Dead_Signal_v2.png",
    "27_Neon_Hemorrhage_v2.png",
    "26_Iron_Descent_v2.png",
]

# 폰트 경로
FONT_BOLD = "C:/Windows/Fonts/malgunbd.ttf"
FONT_REGULAR = "C:/Windows/Fonts/malgun.ttf"


def make_collage(cols=4, rows=4):
    """4x4 격자 콜라주 생성"""
    cell_w = WIDTH // cols
    cell_h = HEIGHT // rows
    canvas = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))

    for i, fname in enumerate(THUMBNAILS):
        if i >= cols * rows:
            break
        img_path = IMG_DIR / fname
        if not img_path.exists():
            print(f"[경고] 없음: {fname}")
            continue

        img = Image.open(img_path)
        img = img.resize((cell_w, cell_h), Image.LANCZOS)

        x = (i % cols) * cell_w
        y = (i // cols) * cell_h
        canvas.paste(img, (x, y))

    return canvas


def add_dark_overlay(canvas, opacity=140):
    """어두운 반투명 오버레이"""
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, opacity))
    canvas = canvas.convert("RGBA")
    canvas = Image.alpha_composite(canvas, overlay)
    return canvas.convert("RGB")


def add_text(canvas):
    """텍스트 오버레이"""
    draw = ImageDraw.Draw(canvas)

    # 폰트 준비
    font_title = ImageFont.truetype(FONT_BOLD, 108)
    font_sub = ImageFont.truetype(FONT_BOLD, 72)
    font_badge = ImageFont.truetype(FONT_BOLD, 54)

    title = "GOTHIC SYNTHWAVE"
    subtitle = "INSTRUMENTAL MIX"
    badge_text = "1 HOUR"

    # 각 요소 실제 렌더링 높이 측정
    title_bbox = draw.textbbox((0, 0), title, font=font_title)
    title_h = title_bbox[3] - title_bbox[1]
    title_w = title_bbox[2] - title_bbox[0]

    sub_bbox = draw.textbbox((0, 0), subtitle, font=font_sub)
    sub_h = sub_bbox[3] - sub_bbox[1]
    sub_w = sub_bbox[2] - sub_bbox[0]

    badge_bbox = draw.textbbox((0, 0), badge_text, font=font_badge)
    badge_h = badge_bbox[3] - badge_bbox[1]
    badge_w = badge_bbox[2] - badge_bbox[0]

    pad_x, pad_y = 24, 12
    gap1 = 45  # 제목-부제 간격
    gap2 = 60  # 부제-배지 간격

    # 전체 블록 높이 계산 후 정중앙 배치 (채널명 영역 제외)
    total_h = title_h + gap1 + sub_h + gap2 + badge_h
    block_top = (HEIGHT - total_h) // 2 - 40

    # 메인 제목
    tx = (WIDTH - title_w) // 2
    ty = block_top
    draw.text((tx + 4, ty + 4), title, fill=(0, 0, 0), font=font_title)
    draw.text((tx, ty), title, fill=(255, 255, 255), font=font_title)

    # 부제
    sx = (WIDTH - sub_w) // 2
    sy = ty + title_h + gap1
    draw.text((sx + 3, sy + 3), subtitle, fill=(0, 0, 0), font=font_sub)
    draw.text((sx, sy), subtitle, fill=(200, 50, 50), font=font_sub)

    # 1 HOUR
    bx = (WIDTH - badge_w) // 2
    by = sy + sub_h + gap2
    draw.text((bx + 3, by + 3), badge_text, fill=(0, 0, 0), font=font_badge)
    draw.text((bx, by), badge_text, fill=(200, 50, 50), font=font_badge)

    # 채널명
    font_ch = ImageFont.truetype(FONT_REGULAR, 28)
    channel = "deelup 디루프"
    bbox = draw.textbbox((0, 0), channel, font=font_ch)
    cw = bbox[2] - bbox[0]
    draw.text(((WIDTH - cw) // 2, HEIGHT - 55), channel, fill=(180, 180, 180), font=font_ch)

    return canvas


def main():
    print("[1/3] 콜라주 생성...")
    canvas = make_collage()

    print("[2/3] 오버레이 적용...")
    canvas = add_dark_overlay(canvas, opacity=140)

    print("[3/3] 텍스트 배치...")
    canvas = add_text(canvas)

    output_path = OUTPUT_DIR / "gothic_synthwave_1hour_thumbnail.png"
    canvas.save(output_path, quality=95)
    print(f"[완료] {output_path}")


if __name__ == "__main__":
    main()
