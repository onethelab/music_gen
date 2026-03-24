"""
VAD 블록 방식 자막 테스트
- Demucs 보컬 분리 (캐시 재사용)
- pyannote VAD로 보컬 구간 감지
- 가사를 구간 길이 비례로 배분
- 구간 내에서 줄을 균등 시간 간격으로 순차 표시
- Gemini 번역 포함
"""
import os
import re
import sys
import subprocess
import numpy as np
import torch

from moviepy.config import FFMPEG_BINARY

sys.path.insert(0, os.path.dirname(__file__))
from equalizer import generate_circular_equalizer

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
PROMPT_DIR = os.path.join(BASE_DIR, "04_Suno_Prompt")
MP3_DIR = os.path.join(BASE_DIR, "05_Mp3")
IMG_DIR = os.path.join(BASE_DIR, "06_img")
VIDEO_DIR = os.path.join(BASE_DIR, "07_Video")
SRT_DIR = os.path.join(BASE_DIR, "95_make_video_script", "srt")
VOCAL_DIR = os.path.join(BASE_DIR, "95_make_video_script", "vocals")
ENV_FILE = os.path.join(BASE_DIR, "92_make_image", ".env")

TARGET_WIDTH = 1280
TARGET_HEIGHT = 720


def safe_print(text):
    try:
        print(text.encode('cp949', errors='replace').decode('cp949'))
    except Exception:
        print(text.encode('utf-8', errors='replace').decode('utf-8', errors='replace'))


def format_srt_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def extract_lyrics(base_name):
    """가사만 추출 (섹션 태그 제외)"""
    prompt_name = re.sub(r'_v\d+$', '', base_name)
    prompt_path = os.path.join(PROMPT_DIR, f"{prompt_name}.md")
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
        if not line or re.match(r'^\[.*\]$', line):
            continue
        lines.append(line)
    return lines


def extract_sections(base_name):
    """가사를 섹션 단위로 구조화 추출"""
    prompt_name = re.sub(r'_v\d+$', '', base_name)
    prompt_path = os.path.join(PROMPT_DIR, f"{prompt_name}.md")
    if not os.path.exists(prompt_path):
        return []
    with open(prompt_path, 'r', encoding='utf-8') as f:
        content = f.read()
    lyrics_match = re.search(r'## Lyrics\s*\n(.+?)(?:\n##|\Z)', content, re.DOTALL)
    if not lyrics_match:
        return []

    sections = []  # [{'tag': '[Verse 1]', 'lines': [...], 'has_lyrics': True}]
    current_tag = ''
    current_lines = []

    for line in lyrics_match.group(1).split('\n'):
        line = line.strip()
        if re.match(r'^\[.*\]$', line):
            # 이전 섹션 저장
            if current_tag or current_lines:
                sections.append({
                    'tag': current_tag,
                    'lines': current_lines,
                    'has_lyrics': len(current_lines) > 0,
                })
            current_tag = line
            current_lines = []
        elif line:
            current_lines.append(line)

    # 마지막 섹션
    if current_tag or current_lines:
        sections.append({
            'tag': current_tag,
            'lines': current_lines,
            'has_lyrics': len(current_lines) > 0,
        })

    return sections


def detect_vocal_segments(vocal_path):
    """pyannote VAD로 보컬 구간 감지 (whisperx 내부 모듈 활용)"""
    safe_print("  VAD 감지 중 (pyannote)...")

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

    # ffmpeg으로 16kHz mono로 변환하여 로드
    decode_cmd = [FFMPEG_BINARY, '-i', vocal_path, '-f', 'f32le', '-ac', '1', '-ar', '16000', '-v', 'quiet', '-']
    proc = subprocess.run(decode_cmd, capture_output=True)
    audio_np = np.frombuffer(proc.stdout, dtype=np.float32)
    waveform = torch.from_numpy(audio_np.copy()).float().unsqueeze(0)

    safe_print(f"  오디오: {len(audio_np)/16000:.1f}초")

    # pyannote segmentation 모델 직접 사용
    from pyannote.audio import Model, Inference
    from pyannote.audio.pipelines import VoiceActivityDetection
    from pyannote.audio.pipelines.utils import get_devices

    # whisperx에 포함된 segmentation 모델 사용
    import whisperx
    model_fp = os.path.join(os.path.dirname(whisperx.__file__), "assets", "pytorch_model.bin")

    vad_model = Model.from_pretrained(model_fp)
    vad_model = vad_model.to(device)

    # VoiceActivityDetection 파이프라인
    pipeline = VoiceActivityDetection(segmentation=vad_model)
    pipeline.instantiate({
        "onset": 0.25,
        "offset": 0.20,
        "min_duration_on": 0.5,
        "min_duration_off": 0.5,
    })

    # in-memory 오디오로 실행
    vad_result = pipeline({"waveform": waveform, "sample_rate": 16000})

    # 구간 추출
    segments = []
    for speech_turn, _, _ in vad_result.itertracks(yield_label=True):
        start = speech_turn.start
        end = speech_turn.end
        if end - start >= 1.5:
            segments.append([start, end])

    # 인접 구간 병합 (간격 2초 이내)
    merged = []
    for seg in segments:
        if merged and seg[0] - merged[-1][1] < 2.0:
            merged[-1][1] = seg[1]
        else:
            merged.append(seg)

    safe_print(f"  보컬 구간: {len(merged)}개")
    for i, (s, e) in enumerate(merged):
        safe_print(f"    [{i}] {s:.1f}~{e:.1f} ({e-s:.1f}초)")

    # GPU 메모리 해제
    del vad_model, pipeline
    torch.cuda.empty_cache()

    return merged


def distribute_lyrics(segments, lyrics, sections):
    """곡 구조 기반 가사 배분: 섹션 단위로 보컬 구간에 매핑"""

    # 가사 있는 섹션만 추출
    lyric_sections = [s for s in sections if s['has_lyrics']]

    # 전체 보컬 시간과 줄 수
    total_vocal_time = sum(e - s for s, e in segments)
    total_lines = sum(len(s['lines']) for s in lyric_sections)
    avg_time_per_line = total_vocal_time / total_lines if total_lines > 0 else 3.0

    safe_print(f"\n  평균 줄당 시간: {avg_time_per_line:.1f}초")

    # 각 구간이 수용 가능한 줄 수 추정
    seg_capacity = []
    for s, e in segments:
        cap = max(1, round((e - s) / avg_time_per_line))
        seg_capacity.append(cap)

    # 섹션 단위로 구간에 배정 (섹션을 쪼개지 않음)
    seg_lines = {i: [] for i in range(len(segments))}
    seg_tags = {i: [] for i in range(len(segments))}
    seg_idx = 0
    current_count = 0

    for sec in lyric_sections:
        sec_size = len(sec['lines'])

        if seg_idx >= len(segments):
            # 구간 부족 → 마지막 구간에 추가
            seg_lines[len(segments) - 1].extend(sec['lines'])
            seg_tags[len(segments) - 1].append(sec['tag'])
            continue

        # 이 섹션이 현재 구간에 들어갈 수 있는지 확인
        # 용량에 20% 여유를 줌 (빡빡한 용량 때문에 섹션이 밀리는 것 방지)
        effective_cap = int(seg_capacity[seg_idx] * 1.2)
        remaining = effective_cap - current_count
        if remaining < sec_size and current_count > 0:
            # 현재 구간에 공간 부족 → 다음 구간으로 이동
            seg_idx += 1
            current_count = 0
            # 다음 구간도 부족하면 계속 탐색 (여유 포함)
            while seg_idx < len(segments) and int(seg_capacity[seg_idx] * 1.2) < sec_size:
                seg_idx += 1

        if seg_idx >= len(segments):
            seg_lines[len(segments) - 1].extend(sec['lines'])
            seg_tags[len(segments) - 1].append(sec['tag'])
            continue

        # 현재 구간이 이 섹션보다 너무 작으면 건너뛰기
        while seg_idx < len(segments) and seg_capacity[seg_idx] < sec_size and len(seg_lines[seg_idx]) == 0:
            safe_print(f"    구간{seg_idx} 건너뜀 (용량={seg_capacity[seg_idx]} < 섹션={sec_size}줄)")
            seg_idx += 1

        if seg_idx >= len(segments):
            seg_lines[len(segments) - 1].extend(sec['lines'])
            seg_tags[len(segments) - 1].append(sec['tag'])
            continue

        seg_lines[seg_idx].extend(sec['lines'])
        seg_tags[seg_idx].append(sec['tag'])
        current_count += sec_size

        # 구간 용량 찼으면 다음으로
        if current_count >= seg_capacity[seg_idx]:
            seg_idx += 1
            current_count = 0

    # 결과 출력
    safe_print(f"\n  구간별 배분:")
    for i, (s, e) in enumerate(segments):
        lines = seg_lines.get(i, [])
        tags = seg_tags.get(i, [])
        tag_str = ', '.join(tags) if tags else '(없음)'
        safe_print(f"    구간{i} [{s:.1f}~{e:.1f}] 용량={seg_capacity[i]} → {len(lines)}줄 {tag_str}")

    # 구간 내에서 줄을 균등 배치
    assignments = []
    for seg_idx, (seg_start, seg_end) in enumerate(segments):
        lines = seg_lines.get(seg_idx, [])
        if not lines:
            continue

        seg_duration = seg_end - seg_start
        time_per_line = seg_duration / len(lines)

        for i, line_text in enumerate(lines):
            line_start = seg_start + i * time_per_line
            line_end = seg_start + (i + 1) * time_per_line
            assignments.append({
                'start': line_start,
                'end': line_end,
                'text': line_text,
            })

    return assignments


def whisperx_fine_tune(assignments, vocal_path, language='ja', tolerance=2.0):
    """WhisperX word-level 타이밍으로 균등 배분 결과를 미세조정
    - 각 줄의 균등 배분 시작 시간 ±tolerance 이내에 유사 단어가 있으면 조정
    - 없으면 균등 배분 유지
    """
    import whisperx
    from difflib import SequenceMatcher

    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    compute_type = 'float16' if device == 'cuda' else 'int8'

    safe_print(f"\n  WhisperX 미세조정 (tolerance: ±{tolerance}초)...")

    # transcribe + align
    model = whisperx.load_model("large-v3", device, compute_type=compute_type, language=language,
                                 vad_options={"vad_onset": 0.3, "vad_offset": 0.21})
    audio = whisperx.load_audio(vocal_path)
    chunk_size = 10 if language == 'ja' else 30
    result = model.transcribe(audio, batch_size=16, language=language, chunk_size=chunk_size)

    align_model, metadata = whisperx.load_align_model(language_code=language, device=device)
    result = whisperx.align(result['segments'], align_model, metadata, audio, device, return_char_alignments=False)

    # word-level 타이밍 플랫 리스트
    all_words = []
    for seg in result['segments']:
        for w in seg.get('words', []):
            word = w.get('word', '').strip()
            start = w.get('start')
            if word and start is not None:
                all_words.append({'word': word, 'start': float(start)})

    safe_print(f"  WhisperX 단어: {len(all_words)}개")

    # 일본어 정규화
    _kakasi = None
    def normalize(text):
        nonlocal _kakasi
        if language != 'ja':
            return text.lower().strip(".,!?;:'\"")
        if _kakasi is None:
            import pykakasi
            _kakasi = pykakasi.kakasi()
        result = _kakasi.convert(text)
        return ''.join([item['hira'] for item in result]).strip()

    # 각 줄의 첫 단어를 WhisperX 단어와 매칭
    adjusted = 0
    kept = 0

    for a in assignments:
        baseline_start = a['start']
        first_words = a['text'].split()[:3]  # 앞 3단어로 검색
        first_normalized = normalize(' '.join(first_words))

        best_match_time = None
        best_score = 0

        for w in all_words:
            # tolerance 범위 체크
            if abs(w['start'] - baseline_start) > tolerance:
                continue

            w_normalized = normalize(w['word'])
            # 첫 단어 유사도
            score = SequenceMatcher(None, first_normalized[:len(w_normalized)*2], w_normalized).ratio()

            if score > best_score and score >= 0.4:
                best_score = score
                best_match_time = w['start']

        if best_match_time is not None:
            diff = best_match_time - baseline_start
            a['start'] = best_match_time
            a['end'] = a['end'] + diff  # end도 같은 양만큼 이동
            adjusted += 1
        else:
            kept += 1

    # 순서 보정: 다음 줄 시작이 현재 줄 끝보다 앞이면 트리밍
    for i in range(len(assignments) - 1):
        if assignments[i]['end'] > assignments[i+1]['start']:
            assignments[i]['end'] = assignments[i+1]['start'] - 0.05

    safe_print(f"  미세조정: {adjusted}줄 조정, {kept}줄 유지")

    # GPU 메모리 해제
    del model, align_model
    torch.cuda.empty_cache()

    return assignments


def load_api_key():
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


def translate_lyrics(lines, source_lang):
    from google import genai
    api_key = load_api_key()
    if not api_key:
        safe_print("  Gemini API 키 없음 — 번역 건너뜀")
        return None

    if source_lang == 'ko':
        instruction = "Translate each Korean lyrics line to natural English."
    elif source_lang == 'ja':
        instruction = "Translate each Japanese lyrics line to natural Korean."
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
        response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        translated = []
        for line in response.text.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            translated.append(re.sub(r'^\d+[\.\)]\s*', '', line))
        while len(translated) < len(lines):
            translated.append("")
        return translated[:len(lines)]
    except Exception as e:
        safe_print(f"  번역 오류: {e}")
        return None


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "04_Last_Train_Cassette_v1"
    language = sys.argv[2] if len(sys.argv) > 2 else "ja"

    base_name = target.rsplit('_v', 1)[0]
    version = 'v' + target.rsplit('_v', 1)[1]

    mp3_path = os.path.join(MP3_DIR, f"{target}.mp3")
    img_path = os.path.join(IMG_DIR, f"{base_name}_{version}.png")
    vocal_path = os.path.join(VOCAL_DIR, f"{target}_vocals.wav")
    srt_path = os.path.join(SRT_DIR, f"{target}_vad.srt")
    output_path = os.path.join(VIDEO_DIR, f"{target}_vad.mp4")

    if not os.path.exists(vocal_path):
        safe_print(f"보컬 파일 없음: {vocal_path}")
        return

    # 1. 가사 + 구조 추출
    lyrics = extract_lyrics(target)
    sections = extract_sections(target)
    if not lyrics:
        safe_print("가사 없음")
        return
    safe_print(f"가사: {len(lyrics)}줄, 섹션: {len(sections)}개")
    for sec in sections:
        tag = sec['tag'] or '(no tag)'
        safe_print(f"  {tag}: {len(sec['lines'])}줄")

    # 2. VAD 보컬 구간 감지
    segments = detect_vocal_segments(vocal_path)
    if not segments:
        safe_print("보컬 구간 없음")
        return

    # 3. 구조 기반 가사 배분
    assignments = distribute_lyrics(segments, lyrics, sections)
    safe_print(f"\n균등 배분 결과:")
    for a in assignments:
        safe_print(f"  {a['start']:>6.1f}~{a['end']:<6.1f} {a['text'][:40]}")

    # 4. WhisperX 미세조정
    assignments = whisperx_fine_tune(assignments, vocal_path, language)
    safe_print(f"\n미세조정 결과:")
    for a in assignments:
        safe_print(f"  {a['start']:>6.1f}~{a['end']:<6.1f} {a['text'][:40]}")

    # 5. 번역
    original_lines = [a['text'] for a in assignments]
    safe_print(f"\n번역 중... ({len(original_lines)}줄)")
    translations = translate_lyrics(original_lines, language)

    # 5. SRT 저장
    os.makedirs(SRT_DIR, exist_ok=True)
    with open(srt_path, 'w', encoding='utf-8') as f:
        for i, a in enumerate(assignments):
            f.write(f"{i+1}\n")
            f.write(f"{format_srt_time(a['start'])} --> {format_srt_time(a['end'])}\n")
            f.write(f"{a['text']}\n")
            if translations and i < len(translations) and translations[i]:
                f.write(f"{translations[i]}\n")
            f.write(f"\n")
    safe_print(f"SRT 저장: {srt_path}")

    # 6. 오디오 길이
    result = subprocess.run([FFMPEG_BINARY, '-i', mp3_path, '-f', 'null', '-'], capture_output=True, text=True)
    duration = None
    for line in result.stderr.split('\n'):
        if 'time=' in line:
            m = re.search(r'time=(\d+):(\d+):(\d+\.\d+)', line)
            if m:
                duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))

    # 7. 이퀄라이저
    eq_path = os.path.join(SRT_DIR, f"{target}_vad_eq.mov")
    if not os.path.exists(eq_path):
        generate_circular_equalizer(mp3_path, eq_path, duration)

    # 8. MP4 생성
    srt_escaped = srt_path.replace('\\', '/').replace(':', '\\:')

    safe_print("MP4 생성 중...")
    filter_complex = (
        f"[0:v]scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={TARGET_WIDTH}:{TARGET_HEIGHT}:(ow-iw)/2:(oh-ih)/2[bg];"
        f"[bg][2:v]overlay=(W-w)/2:(H-h)/2,"
        f"subtitles='{srt_escaped}':force_style='"
        f"FontName=Malgun Gothic Bold,"
        f"FontSize=26,"
        f"PrimaryColour=&H00FFFFFF,"
        f"OutlineColour=&H00000000,"
        f"BackColour=&H80000000,"
        f"BorderStyle=4,"
        f"Outline=1,"
        f"Shadow=0,"
        f"MarginV=40,"
        f"Alignment=2"
        f"'[out]"
    )
    cmd = [
        FFMPEG_BINARY,
        '-loop', '1', '-i', img_path,
        '-i', mp3_path,
        '-i', eq_path,
        '-filter_complex', filter_complex,
        '-map', '[out]', '-map', '1:a',
        '-c:v', 'libx264', '-preset', 'medium', '-crf', '23',
        '-c:a', 'aac', '-b:a', '192k',
        '-t', str(duration), '-shortest', '-y',
        output_path,
    ]

    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        safe_print(f"ffmpeg 오류: {r.stderr[-500:]}")
        return

    size = os.path.getsize(output_path)
    safe_print(f"완료: {output_path} ({size // (1024*1024)} MB)")


if __name__ == "__main__":
    main()
