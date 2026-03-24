"""
WhisperX를 사용한 가사 정렬 테스트
- align_lyrics.py (stable-ts)와 결과 비교용

사용법:
    cd 95_make_video_script
    python whisperX_test.py 01_Forgotten_Cathedral_v1
"""

import os
import re
import sys
import subprocess
import torch
import whisperx

from moviepy.config import FFMPEG_BINARY

_ffmpeg_dir = os.path.dirname(FFMPEG_BINARY)
if _ffmpeg_dir not in os.environ.get('PATH', ''):
    os.environ['PATH'] = _ffmpeg_dir + os.pathsep + os.environ.get('PATH', '')

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
PROMPT_DIR = os.path.join(BASE_DIR, "04_Suno_Prompt")
MP3_DIR = os.path.join(BASE_DIR, "05_Mp3")
SRT_DIR = os.path.join(BASE_DIR, "95_make_video_script", "srt")
VOCAL_DIR = os.path.join(BASE_DIR, "95_make_video_script", "vocals")


def safe_print(text):
    try:
        print(text.encode('cp949', errors='replace').decode('cp949'))
    except Exception:
        print(text.encode('utf-8', errors='replace').decode('utf-8', errors='replace'))


def extract_lyrics_from_prompt(base_name):
    """04_Suno_Prompt에서 가사 줄 추출"""
    # base_name에서 _v1/_v2 제거
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
        if not line:
            continue
        if re.match(r'^\[.*\]$', line):
            continue
        lines.append(line)
    return lines


def format_srt_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def separate_vocals(mp3_path, target_name):
    """demucs 보컬 분리 (캐시 재사용)"""
    import numpy as np

    os.makedirs(VOCAL_DIR, exist_ok=True)
    vocal_path = os.path.join(VOCAL_DIR, f"{target_name}_vocals.wav")

    if os.path.exists(vocal_path):
        safe_print(f"  보컬 캐시 존재: {os.path.basename(vocal_path)} (재사용)")
        return vocal_path

    safe_print(f"  오디오 로드 중...")
    decode_cmd = [
        FFMPEG_BINARY, '-i', mp3_path,
        '-f', 'f32le', '-ac', '2', '-ar', '44100',
        '-v', 'quiet', '-'
    ]
    proc = subprocess.run(decode_cmd, capture_output=True)
    audio_np = np.frombuffer(proc.stdout, dtype=np.float32)
    audio_np = audio_np.reshape(-1, 2).T

    safe_print(f"  demucs 보컬 분리 중... ({len(audio_np[0])/44100:.1f}초)")
    from demucs.pretrained import get_model
    from demucs.apply import apply_model

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model = get_model('htdemucs_ft')
    model = model.to(device)
    audio_tensor = torch.from_numpy(audio_np.copy()).float().unsqueeze(0).to(device)

    with torch.no_grad():
        sources = apply_model(model, audio_tensor, device=device, progress=True)

    vocal_idx = model.sources.index('vocals')
    vocals = sources[0, vocal_idx].cpu().numpy()

    import wave
    vocals_int16 = (vocals.T * 32767).clip(-32768, 32767).astype(np.int16)
    with wave.open(vocal_path, 'w') as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(vocals_int16.tobytes())

    safe_print(f"  보컬 분리 완료: {os.path.basename(vocal_path)}")
    return vocal_path


def run_whisperx(mp3_path, target_name, lyrics, language='en'):
    """WhisperX로 정렬 — transcribe로 대략적 타이밍 획득 후 가사 교체하여 align"""
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    compute_type = 'float16' if device == 'cuda' else 'int8'

    # 보컬 분리
    vocal_path = separate_vocals(mp3_path, target_name)
    audio_source = vocal_path if vocal_path else mp3_path

    safe_print(f"  WhisperX 가사 주입 모드 (device: {device})")
    audio = whisperx.load_audio(audio_source)

    # 1단계: transcribe로 대략적 타이밍 획득
    safe_print(f"  1단계: transcribe (대략적 타이밍 획득)...")
    model = whisperx.load_model("large-v3", device, compute_type=compute_type, language=language)
    raw_result = model.transcribe(audio, batch_size=16, language=language)
    safe_print(f"  인식된 세그먼트: {len(raw_result['segments'])}개")

    # 2단계: 인식 결과의 텍스트를 우리 가사로 교체
    # 인식된 세그먼트의 타이밍은 유지하고, 텍스트만 교체
    raw_segments = raw_result['segments']

    # 인식된 텍스트를 단어 단위로 풀어서, 우리 가사의 단어 수에 맞게 재분배
    all_words_from_asr = []
    for seg in raw_segments:
        text = seg.get('text', '').strip()
        if text:
            words = text.split()
            seg_start = seg.get('start', 0)
            seg_end = seg.get('end', 0)
            # 세그먼트 내 단어를 균등 배분
            if len(words) > 0:
                dur = seg_end - seg_start
                for j, w in enumerate(words):
                    w_start = seg_start + dur * j / len(words)
                    w_end = seg_start + dur * (j + 1) / len(words)
                    all_words_from_asr.append({'start': w_start, 'end': w_end})

    safe_print(f"  ASR 단어 수: {len(all_words_from_asr)}")

    # 우리 가사의 총 단어 수
    lyrics_word_counts = [len(line.split()) for line in lyrics]
    total_lyrics_words = sum(lyrics_word_counts)
    safe_print(f"  가사 단어 수: {total_lyrics_words}")

    # ASR 단어 타이밍을 가사 줄에 매핑
    # ASR 단어를 가사 단어 비율에 맞춰 분배
    injected_segments = []
    asr_idx = 0
    ratio = len(all_words_from_asr) / max(total_lyrics_words, 1)

    for i, line in enumerate(lyrics):
        word_count = len(line.split())
        # 이 줄에 해당하는 ASR 단어 범위
        start_word = int(sum(lyrics_word_counts[:i]) * ratio)
        end_word = int(sum(lyrics_word_counts[:i+1]) * ratio)
        start_word = min(start_word, len(all_words_from_asr) - 1)
        end_word = min(end_word, len(all_words_from_asr))

        if start_word < len(all_words_from_asr) and end_word > start_word:
            seg_start = all_words_from_asr[start_word]['start']
            seg_end = all_words_from_asr[end_word - 1]['end']
        else:
            seg_start = 0.0
            seg_end = 0.0

        injected_segments.append({
            "text": line,
            "start": seg_start,
            "end": seg_end,
        })

    safe_print(f"  2단계: 가사 주입 완료 ({len(injected_segments)}줄, 타이밍 매핑)")

    # 3단계: align으로 정밀 정렬 (wav2vec2 phoneme 기반)
    safe_print(f"  3단계: align (phoneme 기반 정밀 정렬)...")
    align_model, metadata = whisperx.load_align_model(language_code=language, device=device)
    result = whisperx.align(injected_segments, align_model, metadata, audio, device, return_char_alignments=False)

    safe_print(f"  정렬된 세그먼트: {len(result['segments'])}개")

    return result


def parse_existing_srt(srt_path):
    """기존 SRT 파일 파싱"""
    if not os.path.exists(srt_path):
        return []

    with open(srt_path, 'r', encoding='utf-8') as f:
        content = f.read()

    entries = []
    blocks = content.strip().split('\n\n')
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) >= 3:
            time_line = lines[1]
            text = '\n'.join(lines[2:])
            match = re.match(r'(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})', time_line)
            if match:
                start = srt_time_to_sec(match.group(1))
                end = srt_time_to_sec(match.group(2))
                entries.append({'start': start, 'end': end, 'text': text})
    return entries


def srt_time_to_sec(time_str):
    h, m, rest = time_str.split(':')
    s, ms = rest.split(',')
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def compare_results(whisperx_segments, stable_ts_entries, lyrics):
    """WhisperX vs stable-ts 결과 비교"""
    safe_print(f"\n{'='*80}")
    safe_print(f"  WhisperX vs stable-ts 비교")
    safe_print(f"{'='*80}")
    safe_print(f"  {'#':>3}  {'stable-ts':>14}  {'whisperX':>14}  {'차이':>6}  가사")
    safe_print(f"  {'-'*75}")

    wx_issues = 0
    st_issues = 0

    for i, lyric in enumerate(lyrics):
        # stable-ts
        st_start = "-"
        st_end = "-"
        st_ok = False
        if i < len(stable_ts_entries):
            e = stable_ts_entries[i]
            st_start = f"{e['start']:.1f}"
            st_end = f"{e['end']:.1f}"
            st_ok = (e['end'] - e['start']) > 0.5

        # whisperX - 텍스트 매칭으로 찾기
        wx_start = "-"
        wx_end = "-"
        wx_ok = False
        for seg in whisperx_segments:
            seg_text = seg.get('text', '').strip().lower()
            if lyric.lower() in seg_text or seg_text in lyric.lower():
                wx_start = f"{seg['start']:.1f}"
                wx_end = f"{seg['end']:.1f}"
                wx_ok = (seg['end'] - seg['start']) > 0.5
                break

        if not st_ok:
            st_issues += 1
        if not wx_ok:
            wx_issues += 1

        # 차이 계산
        diff = ""
        if st_start != "-" and wx_start != "-":
            d = abs(float(st_start) - float(wx_start))
            diff = f"{d:.1f}s"

        st_flag = "✓" if st_ok else "✗"
        wx_flag = "✓" if wx_ok else "✗"

        safe_print(f"  {i+1:>3}  {st_flag} {st_start:>5}~{st_end:<5}  {wx_flag} {wx_start:>5}~{wx_end:<5}  {diff:>6}  {lyric[:40]}")

    safe_print(f"\n  {'='*40}")
    safe_print(f"  stable-ts  실패: {st_issues}/{len(lyrics)}줄 ({st_issues/len(lyrics)*100:.0f}%)")
    safe_print(f"  whisperX   실패: {wx_issues}/{len(lyrics)}줄 ({wx_issues/len(lyrics)*100:.0f}%)")
    safe_print(f"  {'='*40}")


def main():
    if len(sys.argv) < 2:
        safe_print("사용법: python whisperX_test.py <곡이름_v1>")
        safe_print("예: python whisperX_test.py 01_Forgotten_Cathedral_v1")
        return

    target = sys.argv[1]
    base_name = re.sub(r'_v\d+$', '', target)

    # 파일 경로
    mp3_path = os.path.join(MP3_DIR, f"{target}.mp3")
    if not os.path.exists(mp3_path):
        safe_print(f"mp3 파일 없음: {mp3_path}")
        return

    # 가사 로드
    lyrics = extract_lyrics_from_prompt(target)
    if not lyrics:
        safe_print("가사를 찾을 수 없습니다.")
        return
    safe_print(f"가사: {len(lyrics)}줄")

    # 언어 감지
    language = 'en'
    if any(re.search(r'[가-힣]', l) for l in lyrics):
        language = 'ko'
    safe_print(f"언어: {language}")

    # WhisperX 실행 (가사 주입)
    safe_print(f"\n--- WhisperX 실행 (가사 주입 모드) ---")
    wx_result = run_whisperx(mp3_path, target, lyrics, language)

    # WhisperX 결과 출력
    safe_print(f"\n--- WhisperX 결과 ---")
    for i, seg in enumerate(wx_result['segments']):
        start = seg.get('start', 0)
        end = seg.get('end', 0)
        text = seg.get('text', '').strip()
        safe_print(f"  [{i+1:>2}] {start:>6.1f}~{end:<6.1f}  {text}")

    # WhisperX SRT 저장
    wx_srt_path = os.path.join(SRT_DIR, f"{target}_whisperx.srt")
    os.makedirs(SRT_DIR, exist_ok=True)
    with open(wx_srt_path, 'w', encoding='utf-8') as f:
        for i, seg in enumerate(wx_result['segments']):
            start = seg.get('start', 0)
            end = seg.get('end', 0)
            text = seg.get('text', '').strip()
            f.write(f"{i+1}\n")
            f.write(f"{format_srt_time(start)} --> {format_srt_time(end)}\n")
            f.write(f"{text}\n\n")
    safe_print(f"\nWhisperX SRT 저장: {wx_srt_path}")

    # stable-ts 기존 결과 로드 & 비교
    st_srt_path = os.path.join(SRT_DIR, f"{target}.srt")
    if os.path.exists(st_srt_path):
        st_entries = parse_existing_srt(st_srt_path)
        compare_results(wx_result['segments'], st_entries, lyrics)
    else:
        safe_print(f"\nstable-ts SRT 없음 — 비교 건너뜀 ({st_srt_path})")


if __name__ == "__main__":
    main()
