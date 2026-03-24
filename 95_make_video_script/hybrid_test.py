"""
Hybrid SRT 생성: stable-ts 기본 + 실패 구간만 WhisperX 보완
- stable-ts로 전체 정렬
- 실패 구간(start==end, duration<0.5초) 감지
- 실패 구간의 오디오를 잘라서 WhisperX에 해당 가사만 전달
- 결과를 합쳐서 최종 SRT 생성

사용법:
    cd 95_make_video_script
    python hybrid_test.py 01_Forgotten_Cathedral_v1
"""

import os
import re
import sys
import subprocess
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


def srt_time_to_sec(time_str):
    h, m, rest = time_str.split(':')
    s, ms = rest.split(',')
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def separate_vocals(mp3_path, target_name):
    """demucs 보컬 분리 (캐시 재사용)"""
    os.makedirs(VOCAL_DIR, exist_ok=True)
    vocal_path = os.path.join(VOCAL_DIR, f"{target_name}_vocals.wav")
    if os.path.exists(vocal_path):
        safe_print(f"  보컬 캐시 존재 (재사용)")
        return vocal_path

    safe_print(f"  demucs 보컬 분리 중...")
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


def load_audio_np(audio_path, sr=16000, mono=True):
    """ffmpeg으로 오디오를 numpy로 로드"""
    channels = '1' if mono else '2'
    decode_cmd = [
        FFMPEG_BINARY, '-i', audio_path,
        '-f', 'f32le', '-ac', channels, '-ar', str(sr),
        '-v', 'quiet', '-'
    ]
    proc = subprocess.run(decode_cmd, capture_output=True)
    return np.frombuffer(proc.stdout, dtype=np.float32)


# ─── Phase 1: stable-ts ───

def run_stable_ts(vocal_path, lyrics, language='en'):
    """stable-ts로 전체 가사 강제 정렬"""
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    safe_print(f"\n[Phase 1] stable-ts 강제 정렬 (device: {device})")

    model = stable_whisper.load_model("large-v3", device=device)
    audio_np = load_audio_np(vocal_path)
    lyrics_text = "\n".join(lyrics)

    safe_print(f"  정렬 중...")
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


# ─── Phase 2: 실패 구간 감지 ───

def detect_failures(entries, min_duration=0.5):
    """실패 구간 감지: duration < min_duration 또는 start==end"""
    failures = []
    for i, e in enumerate(entries):
        dur = e['end'] - e['start']
        if dur < min_duration:
            failures.append(i)
    return failures


def group_consecutive_failures(failures, entries, margin=5.0):
    """연속 실패를 구간으로 묶고, 오디오 범위 결정"""
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

        # 오디오 범위: 직전 성공 줄의 end ~ 직후 성공 줄의 start (또는 끝)
        if first_idx > 0:
            audio_start = entries[first_idx - 1]['end'] - margin
        else:
            audio_start = 0

        if last_idx < len(entries) - 1:
            # 직후 성공 줄 찾기
            next_ok = last_idx + 1
            while next_ok < len(entries) and (entries[next_ok]['end'] - entries[next_ok]['start']) < 0.5:
                next_ok += 1
            if next_ok < len(entries):
                audio_end = entries[next_ok]['start'] + margin
            else:
                audio_end = None  # 끝까지
        else:
            audio_end = None

        audio_start = max(0, audio_start)
        lyrics_indices = group
        lyrics_lines = [entries[i]['text'] for i in group]

        result.append({
            'indices': lyrics_indices,
            'lyrics': lyrics_lines,
            'audio_start': audio_start,
            'audio_end': audio_end,
        })

    return result


# ─── Phase 3: WhisperX 보완 ───

def run_whisperx_on_segment(vocal_path, lyrics_lines, audio_start, audio_end, language='en'):
    """오디오 구간을 잘라서 WhisperX로 정렬, 오프셋 적용하여 반환"""
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    compute_type = 'float16' if device == 'cuda' else 'int8'

    # 오디오 구간 추출 (whisperx는 자체 로드 사용)
    import tempfile
    tmp_wav = tempfile.NamedTemporaryFile(suffix='.wav', delete=False).name

    end_args = ['-to', str(audio_end)] if audio_end else []
    cut_cmd = [
        FFMPEG_BINARY, '-i', vocal_path,
        '-ss', str(audio_start),
        *end_args,
        '-ar', '16000', '-ac', '1',
        '-y', '-v', 'quiet', tmp_wav
    ]
    subprocess.run(cut_cmd, capture_output=True)

    # WhisperX transcribe → 가사 교체 → align
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

    # align
    align_model, metadata = whisperx.load_align_model(language_code=language, device=device)
    result = whisperx.align(injected, align_model, metadata, audio, device, return_char_alignments=False)

    # 오프셋 적용 (구간 시작 시간 더하기)
    aligned = []
    for seg in result['segments']:
        aligned.append({
            'start': seg.get('start', 0) + audio_start,
            'end': seg.get('end', 0) + audio_start,
            'text': seg.get('text', '').strip(),
        })

    # 정리
    try:
        os.unlink(tmp_wav)
    except Exception:
        pass

    return aligned


# ─── 비교 출력 ───

def parse_existing_srt(srt_path):
    if not os.path.exists(srt_path):
        return []
    with open(srt_path, 'r', encoding='utf-8') as f:
        content = f.read()
    entries = []
    for block in content.strip().split('\n\n'):
        lines = block.strip().split('\n')
        if len(lines) >= 3:
            match = re.match(r'(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})', lines[1])
            if match:
                entries.append({
                    'start': srt_time_to_sec(match.group(1)),
                    'end': srt_time_to_sec(match.group(2)),
                    'text': '\n'.join(lines[2:])
                })
    return entries


def print_comparison(hybrid_entries, stable_entries, lyrics):
    """Hybrid vs stable-ts 비교"""
    safe_print(f"\n{'='*80}")
    safe_print(f"  Hybrid vs stable-ts 비교")
    safe_print(f"{'='*80}")
    safe_print(f"  {'#':>3}  {'stable-ts':>14}  {'hybrid':>14}  {'src':>5}  가사")
    safe_print(f"  {'-'*75}")

    st_fail = 0
    hy_fail = 0

    for i, lyric in enumerate(lyrics):
        # stable-ts
        st_s, st_e, st_ok = "-", "-", False
        if i < len(stable_entries):
            e = stable_entries[i]
            st_s = f"{e['start']:.1f}"
            st_e = f"{e['end']:.1f}"
            st_ok = (e['end'] - e['start']) >= 0.5

        # hybrid
        hy_s, hy_e, hy_ok, src = "-", "-", False, ""
        if i < len(hybrid_entries):
            e = hybrid_entries[i]
            hy_s = f"{e['start']:.1f}"
            hy_e = f"{e['end']:.1f}"
            hy_ok = (e['end'] - e['start']) >= 0.5
            src = e.get('source', '?')

        if not st_ok:
            st_fail += 1
        if not hy_ok:
            hy_fail += 1

        st_flag = "O" if st_ok else "X"
        hy_flag = "O" if hy_ok else "X"

        safe_print(f"  {i+1:>3}  {st_flag} {st_s:>5}~{st_e:<5}  {hy_flag} {hy_s:>5}~{hy_e:<5}  {src:>5}  {lyric[:40]}")

    safe_print(f"\n  {'='*40}")
    safe_print(f"  stable-ts  실패: {st_fail}/{len(lyrics)}줄 ({st_fail/len(lyrics)*100:.0f}%)")
    safe_print(f"  hybrid     실패: {hy_fail}/{len(lyrics)}줄 ({hy_fail/len(lyrics)*100:.0f}%)")
    safe_print(f"  {'='*40}")


# ─── Main ───

def main():
    if len(sys.argv) < 2:
        safe_print("사용법: python hybrid_test.py <곡이름_v1>")
        return

    target = sys.argv[1]
    mp3_path = os.path.join(MP3_DIR, f"{target}.mp3")
    if not os.path.exists(mp3_path):
        safe_print(f"mp3 없음: {mp3_path}")
        return

    lyrics = extract_lyrics_from_prompt(target)
    if not lyrics:
        safe_print("가사 없음")
        return

    language = 'ko' if any(re.search(r'[가-힣]', l) for l in lyrics) else 'en'
    safe_print(f"곡: {target} | 가사: {len(lyrics)}줄 | 언어: {language}")

    # 보컬 분리
    vocal_path = separate_vocals(mp3_path, target)

    # Phase 1: stable-ts
    st_entries = run_stable_ts(vocal_path, lyrics, language)

    safe_print(f"\n[Phase 1 결과]")
    for i, e in enumerate(st_entries):
        dur = e['end'] - e['start']
        flag = "O" if dur >= 0.5 else "X"
        safe_print(f"  [{i+1:>2}] {flag} {e['start']:>6.1f}~{e['end']:<6.1f} ({dur:.1f}s)  {e['text'][:40]}")

    # Phase 2: 실패 감지
    failures = detect_failures(st_entries)
    safe_print(f"\n[Phase 2] 실패 감지: {len(failures)}줄 — {failures}")

    if not failures:
        safe_print("실패 없음! stable-ts 결과를 그대로 사용합니다.")
        hybrid_entries = [dict(e, source='st') for e in st_entries]
    else:
        groups = group_consecutive_failures(failures, st_entries)
        safe_print(f"  실패 그룹: {len(groups)}개")
        for g in groups:
            safe_print(f"    줄 {[i+1 for i in g['indices']]} → 오디오 {g['audio_start']:.1f}~{g['audio_end'] if g['audio_end'] else 'END'}초")

        # Phase 3: WhisperX 보완
        safe_print(f"\n[Phase 3] WhisperX 보완")
        wx_patches = {}

        for g in groups:
            safe_print(f"  구간: 줄 {[i+1 for i in g['indices']]}, 오디오 {g['audio_start']:.1f}~{g['audio_end'] if g['audio_end'] else 'END'}초")
            aligned = run_whisperx_on_segment(
                vocal_path, g['lyrics'], g['audio_start'], g['audio_end'], language
            )
            for j, idx in enumerate(g['indices']):
                if j < len(aligned):
                    wx_patches[idx] = aligned[j]
                    safe_print(f"    [{idx+1}] {aligned[j]['start']:.1f}~{aligned[j]['end']:.1f}  {aligned[j]['text'][:40]}")
                else:
                    safe_print(f"    [{idx+1}] WhisperX 결과 없음")

        # 합치기
        hybrid_entries = []
        for i, e in enumerate(st_entries):
            if i in wx_patches:
                patched = wx_patches[i]
                hybrid_entries.append({
                    'start': patched['start'],
                    'end': patched['end'],
                    'text': e['text'],
                    'source': 'wx',
                })
            else:
                hybrid_entries.append(dict(e, source='st'))

    # SRT 저장
    hybrid_srt_path = os.path.join(SRT_DIR, f"{target}_hybrid.srt")
    os.makedirs(SRT_DIR, exist_ok=True)
    with open(hybrid_srt_path, 'w', encoding='utf-8') as f:
        for i, e in enumerate(hybrid_entries):
            f.write(f"{i+1}\n")
            f.write(f"{format_srt_time(e['start'])} --> {format_srt_time(e['end'])}\n")
            f.write(f"{e['text']}\n\n")
    safe_print(f"\nHybrid SRT 저장: {hybrid_srt_path}")

    # 비교 (기존 stable-ts SRT와)
    existing_srt = os.path.join(SRT_DIR, f"{target}.srt")
    if os.path.exists(existing_srt):
        stable_srt_entries = parse_existing_srt(existing_srt)
        print_comparison(hybrid_entries, stable_srt_entries, lyrics)
    else:
        print_comparison(hybrid_entries, st_entries, lyrics)


if __name__ == "__main__":
    main()
