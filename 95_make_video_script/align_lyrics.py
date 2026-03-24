"""
stable-ts를 사용한 가사 강제 정렬 (Forced Alignment)
- demucs로 보컬 분리 후, 보컬 트랙에 대해 정렬하여 정확도 향상
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
VOCAL_DIR = os.path.join(BASE_DIR, "95_make_video_script", "vocals")


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


def separate_vocals(mp3_path, target_name):
    """demucs Python API로 보컬 분리, 캐싱된 파일이 있으면 재사용"""
    import numpy as np
    import torch

    os.makedirs(VOCAL_DIR, exist_ok=True)
    vocal_path = os.path.join(VOCAL_DIR, f"{target_name}_vocals.wav")

    if os.path.exists(vocal_path):
        safe_print(f"  보컬 캐시 존재: {os.path.basename(vocal_path)} (재사용)")
        return vocal_path

    # moviepy의 ffmpeg로 오디오 로드 (44100Hz, stereo, float32)
    safe_print(f"  오디오 로드 중...")
    decode_cmd = [
        FFMPEG_BINARY, '-i', mp3_path,
        '-f', 'f32le', '-ac', '2', '-ar', '44100',
        '-v', 'quiet', '-'
    ]
    proc = subprocess.run(decode_cmd, capture_output=True)
    audio_np = np.frombuffer(proc.stdout, dtype=np.float32)
    audio_np = audio_np.reshape(-1, 2).T  # (2, samples)

    safe_print(f"  demucs 보컬 분리 중... ({len(audio_np[0])/44100:.1f}초)")
    from demucs.pretrained import get_model
    from demucs.apply import apply_model

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    safe_print(f"  demucs device: {device}")
    model = get_model('htdemucs_ft')
    model = model.to(device)
    audio_tensor = torch.from_numpy(audio_np.copy()).float().unsqueeze(0).to(device)  # (1, 2, samples)

    with torch.no_grad():
        sources = apply_model(model, audio_tensor, device=device, progress=True)

    # vocals 인덱스 찾기
    vocal_idx = model.sources.index('vocals')
    vocals = sources[0, vocal_idx].cpu().numpy()  # (2, samples)

    # wav로 저장
    import wave
    vocals_int16 = (vocals.T * 32767).clip(-32768, 32767).astype(np.int16)
    with wave.open(vocal_path, 'w') as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(vocals_int16.tobytes())

    safe_print(f"  보컬 분리 완료: {os.path.basename(vocal_path)}")
    return vocal_path


def align_and_generate_srt(mp3_path, srt_path, base_name, language='en'):
    """demucs 보컬 분리 → stable-ts 강제 정렬 → SRT 생성"""
    actual_lyrics = extract_lyrics_from_prompt(base_name)
    if not actual_lyrics:
        safe_print(f"  가사를 찾을 수 없음: {base_name}")
        return False

    safe_print(f"  가사 {len(actual_lyrics)}줄 로드 완료")

    # 가사를 줄바꿈으로 연결
    lyrics_text = "\n".join(actual_lyrics)

    # demucs 보컬 분리
    target_name = os.path.splitext(os.path.basename(mp3_path))[0]
    vocal_path = separate_vocals(mp3_path, target_name)
    audio_source = vocal_path if vocal_path else mp3_path
    if not vocal_path:
        safe_print(f"  보컬 분리 실패 — 원본 mp3로 정렬 진행")

    device = 'cuda' if __import__('torch').cuda.is_available() else 'cpu'
    safe_print(f"  stable-ts 모델 로드: large-v3 (device: {device})")
    model = stable_whisper.load_model("large-v3", device=device)

    # moviepy의 ffmpeg로 오디오를 numpy로 로드 (stable-ts의 ffprobe 의존성 회피)
    import numpy as np
    decode_cmd = [
        FFMPEG_BINARY, '-i', audio_source,
        '-f', 'f32le', '-ac', '1', '-ar', '16000',
        '-v', 'quiet', '-'
    ]
    proc = subprocess.run(decode_cmd, capture_output=True)
    audio_np = np.frombuffer(proc.stdout, dtype=np.float32)
    safe_print(f"  오디오 로드: {len(audio_np)/16000:.1f}초 ({'보컬' if vocal_path else '원본'})")

    safe_print(f"  강제 정렬 중... (가사 → 오디오 타이밍 매칭)")
    result = model.align(audio_np, lyrics_text, language=language)

    # refine으로 단어 경계 타이밍 재조정
    safe_print(f"  refine 후처리 중... (단어 경계 재조정)")
    result = model.refine(audio_np, result)

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

    # 비정상적으로 긴 첫 세그먼트 보정 (인트로 구간에서 시작 타이밍이 너무 일찍 잡히는 문제)
    if len(segments) >= 3:
        first = segments[0]
        first_dur = first['end'] - first['start']
        # 2~4번째 세그먼트의 평균 duration을 기준으로 비교
        ref_durations = [s['end'] - s['start'] for s in segments[1:4]]
        ref_avg = sum(ref_durations) / len(ref_durations)
        if first_dur > ref_avg * 3:
            new_start = first['end'] - ref_avg
            safe_print(f"  첫 세그먼트 보정: {first['start']:.2f}→{new_start:.2f}초 "
                       f"(원래 {first_dur:.1f}초, 기준 평균 {ref_avg:.1f}초)")
            segments[0]['start'] = new_start

    # 비정상적으로 긴 세그먼트 보정 (간주/아웃트로 구간에서 end가 늘어지는 문제)
    if segments:
        durations = [s['end'] - s['start'] for s in segments]
        valid_durations = [d for d in durations if 0.5 <= d <= 15.0]
        avg_dur = sum(valid_durations) / len(valid_durations) if valid_durations else 4.0
        max_cap = max(avg_dur * 2, 8.0)

        capped_count = 0
        for seg in segments:
            duration = seg['end'] - seg['start']
            if duration > max_cap:
                old_end = seg['end']
                seg['end'] = seg['start'] + max_cap
                capped_count += 1
                safe_print(f"  긴 세그먼트 보정: '{seg['text'][:20]}...' "
                           f"end {old_end:.2f}→{seg['end']:.2f}초 "
                           f"({duration:.1f}초→{max_cap:.1f}초)")

        if capped_count > 0:
            safe_print(f"  긴 세그먼트 보정: {capped_count}개 "
                       f"(상한: {max_cap:.1f}초, 평균: {avg_dur:.1f}초)")

    # 비정상적으로 짧은 세그먼트 숨김 처리 (정렬 실패 구간)
    if segments:
        durations = [s['end'] - s['start'] for s in segments]
        valid_durations = [d for d in durations if d >= 0.5]
        avg_duration = sum(valid_durations) / len(valid_durations) if valid_durations else 3.0
        min_threshold = avg_duration * 0.25  # 평균의 1/4 미만이면 비정상

        hidden_count = 0
        for seg in segments:
            duration = seg['end'] - seg['start']
            seg['hidden'] = duration < min_threshold
            if seg['hidden']:
                hidden_count += 1

        if hidden_count > 0:
            safe_print(f"  숨김 처리: {hidden_count}개 세그먼트 "
                       f"(기준: {min_threshold:.2f}초 미만, 평균: {avg_duration:.2f}초)")

    # 표시할 세그먼트만 필터링
    visible = [seg for seg in segments if not seg.get('hidden', False)]

    # 번역
    original_lines = [seg['text'] for seg in visible]
    safe_print(f"  가사 번역 중... ({len(original_lines)}줄)")
    translations = translate_lyrics(original_lines, language)

    # SRT 파일 생성 (숨김 세그먼트 제외)
    with open(srt_path, 'w', encoding='utf-8') as f:
        for i, seg in enumerate(visible):
            f.write(f"{i + 1}\n")
            f.write(f"{format_srt_time(seg['start'])} --> {format_srt_time(seg['end'])}\n")
            f.write(f"{seg['text']}\n")
            if translations and i < len(translations) and translations[i]:
                f.write(f"{translations[i]}\n")
            f.write(f"\n")

    safe_print(f"  SRT 생성 완료: {srt_path}")
    safe_print(f"  세그먼트 수: {len(visible)}개 (숨김: {len(segments) - len(visible)}개)")

    # 타이밍 요약 출력
    for i, seg in enumerate(segments):
        safe_print(f"    [{i+1:2d}] {seg['start']:6.2f}~{seg['end']:6.2f}  {seg['text']}")

    return True


def main():
    os.makedirs(SRT_DIR, exist_ok=True)
    os.makedirs(VOCAL_DIR, exist_ok=True)

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
