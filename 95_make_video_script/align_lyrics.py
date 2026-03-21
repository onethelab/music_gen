"""
stable-ts를 사용한 가사 강제 정렬 (Forced Alignment)
- 실제 가사 텍스트를 오디오에 정렬하여 정확한 타이밍 추출
- Whisper의 텍스트 인식 없이, 타이밍만 계산

사용법:
    cd 95_make_video_script
    python align_lyrics.py                          # 전체 보컬곡 처리
    python align_lyrics.py 78_Empty_Save_Slot_v2    # 특정 곡만 처리
"""

import os
import re
import sys
import subprocess

from moviepy.config import FFMPEG_BINARY

# Whisper가 ffmpeg을 찾을 수 있도록 PATH에 추가
_ffmpeg_dir = os.path.dirname(FFMPEG_BINARY)
if _ffmpeg_dir not in os.environ.get('PATH', ''):
    os.environ['PATH'] = _ffmpeg_dir + os.pathsep + os.environ.get('PATH', '')

import stable_whisper

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
PROMPT_DIR = os.path.join(BASE_DIR, "04_Suno_Prompt")
MP3_DIR = os.path.join(BASE_DIR, "05_Mp3")
SRT_DIR = os.path.join(BASE_DIR, "95_make_video_script", "srt")


def safe_print(text):
    """cp949 인코딩 안전 출력"""
    try:
        print(text.encode('cp949', errors='replace').decode('cp949'))
    except Exception:
        print(text.encode('utf-8', errors='replace').decode('utf-8', errors='replace'))


def extract_lyrics_from_prompt(base_name):
    """04_Suno_Prompt에서 실제 가사 줄 목록 추출 (구조태그 제외)"""
    prompt_path = os.path.join(PROMPT_DIR, f"{base_name}.md")
    if not os.path.exists(prompt_path):
        return []

    with open(prompt_path, 'r', encoding='utf-8') as f:
        content = f.read()

    lyrics_match = re.search(r'## Lyrics\s*\n(.+?)(?:\n##|\Z)', content, re.DOTALL)
    if not lyrics_match:
        return []

    lines = []
    for line in lyrics_match.group(1).split('\n'):
        line = line.strip()
        if not line:
            continue
        if re.match(r'^\[.*\]$', line):
            continue
        lines.append(line)

    return lines


def detect_language(base_name):
    """04_Suno_Prompt에서 언어 판별"""
    prompt_path = os.path.join(PROMPT_DIR, f"{base_name}.md")
    if not os.path.exists(prompt_path):
        return 'en'

    with open(prompt_path, 'r', encoding='utf-8') as f:
        content = f.read()

    style_match = re.search(r'## Style of Music\s*\n(.+?)(?:\n##|\Z)', content, re.DOTALL)
    style_text = style_match.group(1).strip() if style_match else ""

    if 'Korean lyrics' in style_text or 'Korean' in style_text:
        return 'ko'
    elif 'English lyrics' in style_text or 'English' in style_text:
        return 'en'

    lyrics_match = re.search(r'## Lyrics\s*\n(.+?)(?:\n##|\Z)', content, re.DOTALL)
    lyrics_text = lyrics_match.group(1) if lyrics_match else ""
    return 'ko' if re.search(r'[가-힣]', lyrics_text) else 'en'


def format_srt_time(seconds):
    """초를 SRT 시간 형식으로 변환"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def load_api_key():
    """Gemini API 키 로드"""
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    env_file = os.path.join(BASE_DIR, "92_make_image", ".env")
    if os.path.exists(env_file):
        with open(env_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith("GEMINI_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def translate_lyrics(lines, source_lang):
    """Gemini로 가사 번역"""
    from google import genai

    api_key = load_api_key()
    if not api_key:
        safe_print("  Gemini API 키 없음 — 번역 건너뜀")
        return None

    if source_lang == 'ko':
        instruction = "Translate each Korean lyrics line to natural English."
    else:
        instruction = "Translate each English lyrics line to natural Korean."

    numbered = "\n".join(f"{i+1}. {line}" for i, line in enumerate(lines))
    prompt = (
        f"{instruction}\n"
        f"Return ONLY the translations, one per line, numbered to match.\n"
        f"Keep the same number of lines. Do not add explanations.\n\n"
        f"{numbered}"
    )

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        result_text = response.text.strip()

        translated = []
        for line in result_text.split('\n'):
            line = line.strip()
            if not line:
                continue
            cleaned = re.sub(r'^\d+[\.\)]\s*', '', line)
            translated.append(cleaned)

        while len(translated) < len(lines):
            translated.append("")
        translated = translated[:len(lines)]

        return translated
    except Exception as e:
        safe_print(f"  번역 오류: {e}")
        return None


def align_and_generate_srt(mp3_path, srt_path, base_name, language='en'):
    """stable-ts로 가사 강제 정렬 → SRT 생성"""
    actual_lyrics = extract_lyrics_from_prompt(base_name)
    if not actual_lyrics:
        safe_print(f"  가사를 찾을 수 없음: {base_name}")
        return False

    safe_print(f"  가사 {len(actual_lyrics)}줄 로드 완료")

    # 가사를 줄바꿈으로 연결
    lyrics_text = "\n".join(actual_lyrics)

    safe_print(f"  stable-ts 모델 로드: large-v3")
    model = stable_whisper.load_model("large-v3")

    # moviepy의 ffmpeg로 오디오를 numpy로 로드 (stable-ts의 ffprobe 의존성 회피)
    import numpy as np
    decode_cmd = [
        FFMPEG_BINARY, '-i', mp3_path,
        '-f', 'f32le', '-ac', '1', '-ar', '16000',
        '-v', 'quiet', '-'
    ]
    proc = subprocess.run(decode_cmd, capture_output=True)
    audio_np = np.frombuffer(proc.stdout, dtype=np.float32)
    safe_print(f"  오디오 로드: {len(audio_np)/16000:.1f}초")

    safe_print(f"  강제 정렬 중... (가사 → 오디오 타이밍 매칭)")
    result = model.align(audio_np, lyrics_text, language=language)

    # 단어 단위 타이밍 추출
    words = []
    for seg in result.segments:
        for word in seg.words:
            w = word.word.strip()
            if w:
                words.append({
                    'start': word.start,
                    'end': word.end,
                    'word': w,
                })

    safe_print(f"  단어 정렬 결과: {len(words)}개 단어")

    # 단어를 원본 가사 줄에 매핑
    segments = []
    word_idx = 0
    for lyric_line in actual_lyrics:
        # 이 줄의 단어들
        line_words = lyric_line.split()
        matched_start = None
        matched_end = None

        for lw in line_words:
            if word_idx >= len(words):
                break
            if matched_start is None:
                matched_start = words[word_idx]['start']
            matched_end = words[word_idx]['end']
            word_idx += 1

        if matched_start is not None:
            segments.append({
                'start': matched_start,
                'end': matched_end,
                'text': lyric_line,
            })

    safe_print(f"  줄 단위 매핑: {len(segments)}개 세그먼트")

    if not segments:
        safe_print(f"  정렬 실패!")
        return False

    # 번역
    original_lines = [seg['text'] for seg in segments]
    safe_print(f"  가사 번역 중... ({len(original_lines)}줄)")
    translations = translate_lyrics(original_lines, language)

    # SRT 파일 생성
    with open(srt_path, 'w', encoding='utf-8') as f:
        for i, seg in enumerate(segments):
            f.write(f"{i + 1}\n")
            f.write(f"{format_srt_time(seg['start'])} --> {format_srt_time(seg['end'])}\n")
            f.write(f"{seg['text']}\n")
            if translations and i < len(translations) and translations[i]:
                f.write(f"{translations[i]}\n")
            f.write(f"\n")

    safe_print(f"  SRT 생성 완료: {srt_path}")
    safe_print(f"  세그먼트 수: {len(segments)}개")

    # 타이밍 요약 출력
    for i, seg in enumerate(segments):
        safe_print(f"    [{i+1:2d}] {seg['start']:6.2f}~{seg['end']:6.2f}  {seg['text']}")

    return True


def main():
    os.makedirs(SRT_DIR, exist_ok=True)

    # 특정 곡 지정 시
    if len(sys.argv) > 1:
        target_name = sys.argv[1]
        # base_name과 version 분리
        version_match = re.match(r'^(.+)_(v\d+)$', target_name)
        if version_match:
            base_name = version_match.group(1)
        else:
            base_name = target_name

        mp3_path = os.path.join(MP3_DIR, f"{target_name}.mp3")
        if not os.path.exists(mp3_path):
            safe_print(f"mp3 파일 없음: {mp3_path}")
            return

        language = detect_language(base_name)
        srt_path = os.path.join(SRT_DIR, f"{target_name}.srt")

        # 기존 SRT 백업
        if os.path.exists(srt_path):
            backup_path = srt_path.replace('.srt', '_backup.srt')
            import shutil
            shutil.copy2(srt_path, backup_path)
            safe_print(f"  기존 SRT 백업: {backup_path}")
            os.remove(srt_path)

        safe_print(f"\n정렬: {target_name} (언어: {language})")
        align_and_generate_srt(mp3_path, srt_path, base_name, language)
        return

    safe_print("사용법: python align_lyrics.py <곡이름>")
    safe_print("예시: python align_lyrics.py 78_Empty_Save_Slot_v2")


if __name__ == "__main__":
    main()
