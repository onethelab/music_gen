"""
Hybrid SRT 생성: stable-ts 기본 + 실패 구간만 WhisperX 보완
- Phase 1: stable-ts로 전체 가사 강제 정렬 (Whisper large-v3)
- Phase 2: 실패 구간 감지 (duration < 0.5초)
- Phase 3: 실패 구간의 오디오를 잘라서 WhisperX에 해당 가사만 전달 (wav2vec2 phoneme 정렬)
- 결과를 합쳐서 최종 SRT 생성

사용법:
    cd 95_make_video_script
    python hybrid.py                              # 전체 보컬곡 처리
    python hybrid.py 01_Forgotten_Cathedral_v1    # 특정 곡만 처리
"""

import os
import re
import sys
import glob
import subprocess
import tempfile
import numpy as np
import torch
import whisperx
import stable_whisper

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
    """04_Suno_Prompt에서 가사 줄 추출 (구조태그 제외)"""
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


def detect_language(base_name):
    """04_Suno_Prompt에서 언어 판별"""
    prompt_name = re.sub(r'_v\d+$', '', base_name)
    prompt_path = os.path.join(PROMPT_DIR, f"{prompt_name}.md")
    if not os.path.exists(prompt_path):
        return 'en'
    with open(prompt_path, 'r', encoding='utf-8') as f:
        content = f.read()
    style_match = re.search(r'## Style(?:\s+of\s+Music)?\s*\n(.+?)(?:\n##|\Z)', content, re.DOTALL)
    style_text = style_match.group(1).strip() if style_match else ""
    if 'Korean' in style_text:
        return 'ko'
    lyrics_match = re.search(r'## Lyrics\s*\n(.+?)(?:\n##|\Z)', content, re.DOTALL)
    lyrics_text = lyrics_match.group(1) if lyrics_match else ""
    return 'ko' if re.search(r'[가-힣]', lyrics_text) else 'en'


def format_srt_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ─── Demucs 보컬 분리 ───

def separate_vocals(mp3_path, target_name):
    """demucs 보컬 분리 (GPU, 캐시 재사용)"""
    os.makedirs(VOCAL_DIR, exist_ok=True)
    vocal_path = os.path.join(VOCAL_DIR, f"{target_name}_vocals.wav")
    if os.path.exists(vocal_path):
        safe_print(f"  보컬 캐시 존재 (재사용)")
        return vocal_path

    from demucs.pretrained import get_model
    from demucs.apply import apply_model

    decode_cmd = [
        FFMPEG_BINARY, '-i', mp3_path,
        '-f', 'f32le', '-ac', '2', '-ar', '44100',
        '-v', 'quiet', '-'
    ]
    proc = subprocess.run(decode_cmd, capture_output=True)
    audio_np = np.frombuffer(proc.stdout, dtype=np.float32).reshape(-1, 2).T

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    safe_print(f"  demucs 보컬 분리 중... ({len(audio_np[0])/44100:.1f}초, device: {device})")
    model = get_model('htdemucs_ft').to(device)
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

    safe_print(f"  보컬 분리 완료")
    return vocal_path


def load_audio_np(audio_path, sr=16000):
    """ffmpeg으로 오디오를 mono numpy로 로드"""
    decode_cmd = [
        FFMPEG_BINARY, '-i', audio_path,
        '-f', 'f32le', '-ac', '1', '-ar', str(sr),
        '-v', 'quiet', '-'
    ]
    proc = subprocess.run(decode_cmd, capture_output=True)
    return np.frombuffer(proc.stdout, dtype=np.float32)


# ─── Phase 1: stable-ts ───

def run_stable_ts(vocal_path, lyrics, language='en'):
    """stable-ts로 전체 가사 강제 정렬"""
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    safe_print(f"  [Phase 1] stable-ts 강제 정렬 (device: {device})")

    model = stable_whisper.load_model("large-v3", device=device)
    audio_np = load_audio_np(vocal_path)
    lyrics_text = "\n".join(lyrics)

    result = model.align(audio_np, lyrics_text, language=language)
    result = model.refine(audio_np, result)

    # 단어 → 줄 재구성
    words = []
    for seg in result.segments:
        for word in seg.words:
            w = word.word.strip()
            if w:
                words.append({'start': word.start, 'end': word.end, 'text': w})

    entries = []
    word_idx = 0
    for line in lyrics:
        line_words = line.split()
        if not line_words:
            entries.append({'start': 0, 'end': 0, 'text': line})
            continue

        line_start = None
        line_end = None
        matched = 0
        scan_idx = word_idx

        while matched < len(line_words) and scan_idx < len(words):
            if line_start is None:
                line_start = words[scan_idx]['start']
            line_end = words[scan_idx]['end']
            matched += 1
            scan_idx += 1

        if matched == len(line_words):
            word_idx = scan_idx
        else:
            line_start = line_start or 0
            line_end = line_end or 0

        entries.append({'start': line_start, 'end': line_end, 'text': line})

    return entries


# ─── Phase 2: 실패 감지 ───

def detect_failures(entries, min_duration=0.5):
    failures = []
    for i, e in enumerate(entries):
        if (e['end'] - e['start']) < min_duration:
            failures.append(i)
    return failures


def group_consecutive_failures(failures, entries, margin=5.0):
    """연속 실패를 구간으로 묶고 오디오 범위 결정"""
    if not failures:
        return []

    groups = []
    current_group = [failures[0]]
    for i in range(1, len(failures)):
        if failures[i] == failures[i-1] + 1:
            current_group.append(failures[i])
        else:
            groups.append(current_group)
            current_group = [failures[i]]
    groups.append(current_group)

    result = []
    for group in groups:
        first_idx = group[0]
        last_idx = group[-1]

        audio_start = entries[first_idx - 1]['end'] - margin if first_idx > 0 else 0
        audio_start = max(0, audio_start)

        if last_idx < len(entries) - 1:
            next_ok = last_idx + 1
            while next_ok < len(entries) and (entries[next_ok]['end'] - entries[next_ok]['start']) < 0.5:
                next_ok += 1
            audio_end = entries[next_ok]['start'] + margin if next_ok < len(entries) else None
        else:
            audio_end = None

        result.append({
            'indices': group,
            'lyrics': [entries[i]['text'] for i in group],
            'audio_start': audio_start,
            'audio_end': audio_end,
        })

    return result


# ─── Phase 3: WhisperX 보완 ───

def run_whisperx_on_segment(vocal_path, lyrics_lines, audio_start, audio_end, language='en'):
    """실패 구간만 잘라서 WhisperX phoneme 정렬"""
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    compute_type = 'float16' if device == 'cuda' else 'int8'

    tmp_wav = tempfile.NamedTemporaryFile(suffix='.wav', delete=False).name
    end_args = ['-to', str(audio_end)] if audio_end else []
    cut_cmd = [
        FFMPEG_BINARY, '-i', vocal_path,
        '-ss', str(audio_start), *end_args,
        '-ar', '16000', '-ac', '1',
        '-y', '-v', 'quiet', tmp_wav
    ]
    subprocess.run(cut_cmd, capture_output=True)

    # transcribe로 대략적 타이밍 획득
    model = whisperx.load_model("large-v3", device, compute_type=compute_type, language=language)
    audio = whisperx.load_audio(tmp_wav)
    raw_result = model.transcribe(audio, batch_size=16, language=language)

    # ASR 단어 타이밍 추출
    all_words = []
    for seg in raw_result['segments']:
        text = seg.get('text', '').strip()
        if text:
            words = text.split()
            seg_start = seg.get('start', 0)
            seg_end = seg.get('end', 0)
            dur = seg_end - seg_start
            for j, w in enumerate(words):
                w_start = seg_start + dur * j / len(words)
                w_end = seg_start + dur * (j + 1) / len(words)
                all_words.append({'start': w_start, 'end': w_end})

    # 가사 단어 수 기반 매핑
    lyrics_word_counts = [len(l.split()) for l in lyrics_lines]
    total_words = sum(lyrics_word_counts)
    ratio = len(all_words) / max(total_words, 1)

    injected = []
    for i, line in enumerate(lyrics_lines):
        sw = int(sum(lyrics_word_counts[:i]) * ratio)
        ew = int(sum(lyrics_word_counts[:i+1]) * ratio)
        sw = min(sw, max(len(all_words) - 1, 0))
        ew = min(ew, len(all_words))

        if sw < len(all_words) and ew > sw:
            s = all_words[sw]['start']
            e = all_words[ew - 1]['end']
        else:
            s, e = 0.0, 0.0
        injected.append({"text": line, "start": s, "end": e})

    # phoneme 기반 정밀 정렬
    align_model, metadata = whisperx.load_align_model(language_code=language, device=device)
    result = whisperx.align(injected, align_model, metadata, audio, device, return_char_alignments=False)

    # 오프셋 적용
    aligned = []
    for seg in result['segments']:
        aligned.append({
            'start': seg.get('start', 0) + audio_start,
            'end': seg.get('end', 0) + audio_start,
            'text': seg.get('text', '').strip(),
        })

    try:
        os.unlink(tmp_wav)
    except Exception:
        pass

    return aligned


# ─── Gemini 번역 ───

def load_api_key():
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
        f"Keep the same numbering. One translation per line. "
        f"Do not add any explanation.\n\n{numbered}"
    )

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        raw = response.text.strip()
        translated = []
        for line in raw.split('\n'):
            line = line.strip()
            cleaned = re.sub(r'^\d+\.\s*', '', line).strip()
            if cleaned:
                translated.append(cleaned)
        if len(translated) == len(lines):
            return translated
        safe_print(f"  번역 줄 수 불일치: {len(translated)} vs {len(lines)}")
        return None
    except Exception as e:
        safe_print(f"  번역 오류: {e}")
        return None


# ─── SRT 생성 메인 ───

def generate_hybrid_srt(mp3_path, srt_path, target_name, language='en'):
    """Hybrid SRT 생성 메인 함수"""
    lyrics = extract_lyrics_from_prompt(target_name)
    if not lyrics:
        safe_print(f"  가사를 찾을 수 없음: {target_name}")
        return False

    safe_print(f"  가사 {len(lyrics)}줄 (언어: {language})")

    # 보컬 분리
    vocal_path = separate_vocals(mp3_path, target_name)

    # Phase 1: stable-ts
    st_entries = run_stable_ts(vocal_path, lyrics, language)

    # Phase 2: 실패 감지
    failures = detect_failures(st_entries)
    safe_print(f"  [Phase 2] 실패: {len(failures)}/{len(lyrics)}줄")

    if not failures:
        hybrid_entries = st_entries
        safe_print(f"  실패 없음 — stable-ts 결과 사용")
    else:
        groups = group_consecutive_failures(failures, st_entries)
        safe_print(f"  [Phase 3] WhisperX 보완 ({len(groups)}개 구간)")

        wx_patches = {}
        for g in groups:
            end_str = f"{g['audio_end']:.1f}" if g['audio_end'] else "END"
            safe_print(f"    줄 {[i+1 for i in g['indices']]} → {g['audio_start']:.1f}~{end_str}초")
            aligned = run_whisperx_on_segment(
                vocal_path, g['lyrics'], g['audio_start'], g['audio_end'], language
            )
            for j, idx in enumerate(g['indices']):
                if j < len(aligned):
                    wx_patches[idx] = aligned[j]

        hybrid_entries = []
        for i, e in enumerate(st_entries):
            if i in wx_patches:
                hybrid_entries.append({
                    'start': wx_patches[i]['start'],
                    'end': wx_patches[i]['end'],
                    'text': e['text'],
                })
            else:
                hybrid_entries.append(e)

        patched_count = len(wx_patches)
        still_failed = len([e for e in hybrid_entries if (e['end'] - e['start']) < 0.5])
        safe_print(f"  WhisperX 보완: {patched_count}줄 | 최종 실패: {still_failed}줄")

    # 번역
    original_lines = [e['text'] for e in hybrid_entries]
    translated = translate_lyrics(original_lines, language)
    if translated:
        safe_print(f"  가사 번역 완료: {len(translated)}줄")

    # 숨김 처리 (너무 짧은 세그먼트)
    avg_dur = np.mean([e['end'] - e['start'] for e in hybrid_entries if (e['end'] - e['start']) > 0.5]) if hybrid_entries else 3.0
    threshold = avg_dur * 0.25

    # SRT 저장
    os.makedirs(os.path.dirname(srt_path), exist_ok=True)
    with open(srt_path, 'w', encoding='utf-8') as f:
        idx = 1
        for i, e in enumerate(hybrid_entries):
            dur = e['end'] - e['start']
            if dur < threshold:
                continue

            f.write(f"{idx}\n")
            f.write(f"{format_srt_time(e['start'])} --> {format_srt_time(e['end'])}\n")
            if translated and i < len(translated):
                f.write(f"{e['text']}\n{translated[i]}\n\n")
            else:
                f.write(f"{e['text']}\n\n")
            idx += 1

    safe_print(f"  SRT 생성 완료: {srt_path}")
    safe_print(f"  세그먼트 수: {idx-1}개")

    # 타이밍 요약
    for i, e in enumerate(hybrid_entries):
        dur = e['end'] - e['start']
        if dur < threshold:
            continue
        safe_print(f"    [{i+1:>2}] {e['start']:>6.2f}~{e['end']:<6.2f}  {e['text'][:50]}")

    return True


# ─── 배치 처리 ───

def find_vocal_songs():
    """보컬곡 목록 (가사가 있는 곡)"""
    mp3_files = sorted(glob.glob(os.path.join(MP3_DIR, "*_v*.mp3")))
    songs = []
    for mp3 in mp3_files:
        basename = os.path.splitext(os.path.basename(mp3))[0]
        lyrics = extract_lyrics_from_prompt(basename)
        if lyrics:
            language = detect_language(basename)
            songs.append((basename, mp3, language))
    return songs


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else None

    if target:
        # 특정 곡 처리
        mp3_path = os.path.join(MP3_DIR, f"{target}.mp3")
        if not os.path.exists(mp3_path):
            safe_print(f"mp3 없음: {mp3_path}")
            return
        language = detect_language(target)
        srt_path = os.path.join(SRT_DIR, f"{target}.srt")
        safe_print(f"처리: {target} (언어: {language})")
        generate_hybrid_srt(mp3_path, srt_path, target, language)
    else:
        # 전체 보컬곡 배치 처리
        songs = find_vocal_songs()
        safe_print(f"보컬 곡 목록 ({len(songs)}개):")
        for i, (name, _, lang) in enumerate(songs):
            safe_print(f"  [{i}] {name} ({lang})")

        for name, mp3_path, language in songs:
            srt_path = os.path.join(SRT_DIR, f"{name}.srt")
            if os.path.exists(srt_path):
                safe_print(f"\n{name}: SRT 존재 (건너뜀)")
                continue
            safe_print(f"\n처리: {name} (언어: {language})")
            generate_hybrid_srt(mp3_path, srt_path, name, language)

        safe_print(f"\n모든 SRT 생성 완료!")


if __name__ == "__main__":
    main()
