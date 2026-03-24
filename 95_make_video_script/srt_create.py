"""
SRT 자막 생성 (프로덕션)
1. Demucs htdemucs_6s 보컬 분리 (캐시 재사용)
2. WhisperX transcribe + align → 플랫리스트
3. rapidfuzz concat 매칭 → 가사줄별 start/end 추출
4. 반복 가사 경합 해결 (인접 인덱스 proximity 배정)
5. LIS 이탈자 검출 → 추정 전환
6. 추정 가사 글자 수 비율 분배
7. 최소 duration 보장 + 겹침 제거

사용법:
    cd 95_make_video_script
    python srt_create.py                              # 전체 보컬곡 처리
    python srt_create.py 04_Last_Train_Cassette_v1    # 특정 곡만 처리
"""

import os
import re
import sys
import glob
import subprocess
import numpy as np
import torch
import whisperx
from bisect import bisect_left
from collections import Counter
from rapidfuzz.fuzz import partial_ratio_alignment
from google import genai
from moviepy.config import FFMPEG_BINARY

_ffmpeg_dir = os.path.dirname(FFMPEG_BINARY)
if _ffmpeg_dir not in os.environ.get('PATH', ''):
    os.environ['PATH'] = _ffmpeg_dir + os.pathsep + os.environ.get('PATH', '')

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
PROMPT_DIR = os.path.join(BASE_DIR, "04_Suno_Prompt")
MP3_DIR = os.path.join(BASE_DIR, "05_Mp3")
VOCAL_DIR = os.path.join(os.path.dirname(__file__), "vocals")
ENV_FILE = os.path.join(BASE_DIR, "92_make_image", ".env")

GAP_MAP = {'en': 0.8, 'ko': 0.8, 'ja': 0.2}
MIN_DUR = 2.0


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


# ─── 가사/언어 추출 ───

def extract_lyrics(prompt_path):
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


def detect_language(prompt_path):
    with open(prompt_path, 'r', encoding='utf-8') as f:
        content = f.read()
    style_match = re.search(r'## Style(?:\s+of\s+Music)?\s*\n(.+?)(?:\n##|\Z)', content, re.DOTALL)
    style = style_match.group(1).strip() if style_match else ""
    if 'Korean' in style:
        return 'ko'
    if 'Japanese' in style:
        return 'ja'
    lyrics_match = re.search(r'## Lyrics\s*\n(.+?)(?:\n##|\Z)', content, re.DOTALL)
    lyrics = lyrics_match.group(1) if lyrics_match else ""
    if re.search(r'[가-힣]', lyrics):
        return 'ko'
    if re.search(r'[\u3040-\u309F\u30A0-\u30FF]', lyrics):
        return 'ja'
    return 'en'


def detect_is_instrumental(prompt_path):
    with open(prompt_path, 'r', encoding='utf-8') as f:
        content = f.read()
    style_match = re.search(r'## Style(?:\s+of\s+Music)?\s*\n(.+?)(?:\n##|\Z)', content, re.DOTALL)
    style = style_match.group(1).strip().lower() if style_match else ""
    return any(kw in style for kw in ['instrumental only', 'no vocals', 'no singing', 'no voice'])


# ─── Gemini 번역 ───

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


# ─── Demucs 보컬 분리 ───

def separate_vocals(mp3_path, target_name, demucs_model=None):
    os.makedirs(VOCAL_DIR, exist_ok=True)
    vocal_path = os.path.join(VOCAL_DIR, f"{target_name}_vocals.wav")
    if os.path.exists(vocal_path):
        safe_print(f"  보컬 캐시 재사용")
        return vocal_path

    from demucs.apply import apply_model

    decode_cmd = [FFMPEG_BINARY, '-i', mp3_path, '-f', 'f32le', '-ac', '2', '-ar', '44100', '-v', 'quiet', '-']
    proc = subprocess.run(decode_cmd, capture_output=True)
    audio_np = np.frombuffer(proc.stdout, dtype=np.float32).reshape(-1, 2).T

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    safe_print(f"  Demucs 보컬 분리 중... ({len(audio_np[0])/44100:.1f}초)")

    if demucs_model is None:
        from demucs.pretrained import get_model
        demucs_model = get_model('htdemucs_6s').to(device)

    audio_tensor = torch.from_numpy(audio_np.copy()).float().unsqueeze(0).to(device)
    with torch.no_grad():
        sources = apply_model(demucs_model, audio_tensor, device=device, progress=True)
    vocal_idx = demucs_model.sources.index('vocals')
    vocals = sources[0, vocal_idx].cpu().numpy()

    del audio_tensor, sources
    torch.cuda.empty_cache()

    import wave
    vocals_int16 = (vocals.T * 32767).clip(-32768, 32767).astype(np.int16)
    with wave.open(vocal_path, 'w') as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(vocals_int16.tobytes())
    safe_print(f"  보컬 분리 완료")
    return vocal_path


# ─── WhisperX 플랫리스트 ───

def generate_flat_list(vocal_path, language):
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    safe_print(f"  WhisperX 처리중...")
    model = whisperx.load_model("large-v3", device, compute_type='float16', language=language,
                                 vad_options={"vad_onset": 0.3, "vad_offset": 0.21})
    audio = whisperx.load_audio(vocal_path)
    chunk_size = 10 if language == 'ja' else 30
    result = model.transcribe(audio, batch_size=16, language=language, chunk_size=chunk_size)
    align_model, metadata = whisperx.load_align_model(language_code=language, device=device)
    result = whisperx.align(result['segments'], align_model, metadata, audio, device, return_char_alignments=False)

    flat = []
    for seg in result['segments']:
        for w in seg.get('words', []):
            word = w.get('word', '').strip()
            start = w.get('start')
            end = w.get('end')
            if word and start is not None and end is not None:
                flat.append({'word': word, 'start': float(start), 'end': float(end)})

    del model, align_model
    torch.cuda.empty_cache()
    safe_print(f"  플랫리스트: {len(flat)}개")
    return flat


# ─── LIS 이탈자 검출 ───

def find_lis(seq):
    tails = []
    indices = []
    for val in seq:
        pos = bisect_left(tails, val)
        if pos == len(tails):
            tails.append(val)
        else:
            tails[pos] = val
        indices.append(pos)
    lis_len = len(tails)
    lis_set = set()
    target = lis_len - 1
    for i in range(len(indices) - 1, -1, -1):
        if indices[i] == target:
            lis_set.add(seq[i])
            target -= 1
        if target < 0:
            break
    return lis_set


# ─── 가사 매칭 + SRT 생성 ───

def generate_srt(vocal_path, srt_path, language, lyrics):
    flat = generate_flat_list(vocal_path, language)
    if not flat:
        safe_print(f"  플랫리스트 비어있음")
        return False

    # concat 문자열 + 인덱스 매핑 (EN/KO: 공백 유지, JA: 공백 없음)
    concat = ''
    char_to_flat_idx = []
    for fi, w in enumerate(flat):
        if fi > 0 and language != 'ja':
            concat += ' '
            char_to_flat_idx.append(fi)
        for ch in w['word']:
            concat += ch
            char_to_flat_idx.append(fi)

    def make_query(line):
        return line.replace(' ', '') if language == 'ja' else line

    # 유니크 vs 경합 분류
    text_counts = Counter(make_query(line) for line in lyrics)
    unique_indices = []
    competing = {}
    for li, line in enumerate(lyrics):
        key = make_query(line)
        if text_counts[key] == 1:
            unique_indices.append(li)
        else:
            competing.setdefault(key, []).append(li)

    # 1단계: 유니크 가사 score 높은 순 점유
    unique_scores = []
    for li in unique_indices:
        r = partial_ratio_alignment(make_query(lyrics[li]), concat)
        unique_scores.append((li, r.score if r else 0))
    unique_scores.sort(key=lambda x: -x[1])

    lyrics_entries = []
    masked = concat

    for li, _ in unique_scores:
        query = make_query(lyrics[li])
        r = partial_ratio_alignment(query, masked)
        if r and r.score > 0:
            ds, de = r.dest_start, r.dest_end
            # 시작 보정
            start_fi = char_to_flat_idx[ds]
            while ds < de and char_to_flat_idx[ds] == start_fi and concat[ds] != query[0]:
                ds += 1
            start_fi = char_to_flat_idx[min(ds, len(char_to_flat_idx) - 1)]
            end_fi = char_to_flat_idx[min(de - 1, len(char_to_flat_idx) - 1)]
            lyrics_entries.append((li, flat[start_fi]['start'], flat[end_fi]['end'], r.score))
            masked = masked[:r.dest_start] + '\x00' * (r.dest_end - r.dest_start) + masked[r.dest_end:]

    # 2단계: 경합 가사 — proximity 배정
    for text_key, indices in competing.items():
        positions = []
        temp_masked = masked
        for _ in range(len(indices)):
            r = partial_ratio_alignment(text_key, temp_masked)
            if r and r.score > 0:
                ds, de = r.dest_start, r.dest_end
                orig_sf = char_to_flat_idx[ds]
                while ds < de and char_to_flat_idx[ds] == orig_sf and concat[ds] != text_key[0]:
                    ds += 1
                sf = char_to_flat_idx[min(ds, len(char_to_flat_idx) - 1)]
                ef = char_to_flat_idx[min(de - 1, len(char_to_flat_idx) - 1)]
                positions.append((r.dest_start, r.dest_end, flat[sf]['start'], flat[ef]['end'], r.score))
                temp_masked = temp_masked[:r.dest_start] + '\x00' * (r.dest_end - r.dest_start) + temp_masked[r.dest_end:]
            else:
                break

        if not positions:
            continue

        def get_neighbor(pos_start_t):
            best_li, best_dist = -1, float('inf')
            for eli, est, _, _ in lyrics_entries:
                d = abs(est - pos_start_t)
                if d < best_dist:
                    best_dist, best_li = d, eli
            return best_li

        pos_neighbors = [(ds, de, st, et, sc, get_neighbor(st)) for ds, de, st, et, sc in positions]

        candidates = sorted(
            [(abs(li - nb), li, pi) for li in indices for pi, (*_, nb) in enumerate(pos_neighbors)],
            key=lambda x: x[0]
        )
        used_pos, used_li = set(), set()
        for _, li, pi in candidates:
            if li in used_li or pi in used_pos:
                continue
            used_pos.add(pi)
            used_li.add(li)
            ds, de, st, et, sc, _ = pos_neighbors[pi]
            lyrics_entries.append((li, st, et, sc))
            masked = masked[:ds] + '\x00' * (de - ds) + masked[de:]

    # 평균 음절당 시간
    total_chars = sum(len(lyrics[li].replace(' ', '')) for li, *_ in lyrics_entries)
    total_dur = sum(et - st for _, st, et, _ in lyrics_entries)
    sec_per_char = total_dur / total_chars if total_chars > 0 else 0.3

    # 추정 가사 삽입
    matched_map = {li: (st, et, sc) for li, st, et, sc in lyrics_entries}
    all_entries = list(lyrics_entries)

    li = 0
    while li < len(lyrics):
        if li in matched_map:
            li += 1
            continue
        gap_start = li
        while li < len(lyrics) and li not in matched_map:
            li += 1
        gap_end = li
        gap_count = gap_end - gap_start

        prev_end = next((matched_map[pi][1] for pi in range(gap_start - 1, -1, -1) if pi in matched_map), 0.0)
        next_start = next((matched_map[ni][0] for ni in range(gap_end, len(lyrics)) if ni in matched_map), None)

        if next_start is not None:
            available = next_start - prev_end - 0.1
            if available > 0:
                chars = [len(lyrics[gap_start + j].replace(' ', '')) for j in range(gap_count)]
                total_c = sum(chars) or 1
                cursor = prev_end + 0.1
                for j in range(gap_count):
                    dur = available * chars[j] / total_c
                    all_entries.append((gap_start + j, cursor, cursor + dur - 0.05, -1))
                    cursor += dur
            else:
                for j in range(gap_count):
                    all_entries.append((gap_start + j, prev_end + 0.1 + j * 0.5, prev_end + 0.4 + j * 0.5, -1))
        else:
            cursor = prev_end + 0.1
            for j in range(gap_count):
                dur = len(lyrics[gap_start + j].replace(' ', '')) * sec_per_char
                all_entries.append((gap_start + j, cursor, cursor + dur, -1))
                cursor += dur + 0.1

    # 시간순 정렬 + 겹침 제거
    all_entries.sort(key=lambda x: x[1])
    for i in range(len(all_entries) - 1):
        li, st, et, sc = all_entries[i]
        ns = all_entries[i + 1][1]
        if et > ns:
            all_entries[i] = (li, st, ns - 0.05, sc)

    # LIS 이탈자 검출
    time_order = [e[0] for e in sorted(all_entries, key=lambda x: x[1]) if e[3] >= 0]
    lis = find_lis(time_order)
    outliers = set(idx for idx in time_order if idx not in lis)

    final = []
    reinsert = set()
    for li, st, et, sc in all_entries:
        if sc >= 0 and (sc < 30 or li in outliers):
            reinsert.add(li)
        else:
            final.append((li, st, et, sc))

    for li in reinsert:
        chars = len(lyrics[li].replace(' ', ''))
        dur = chars * sec_per_char
        prev_end = max((e[2] for e in final if e[0] < li), default=0.0)
        final.append((li, prev_end + 0.1, prev_end + 0.1 + dur, -1))

    # 최종 정렬 + 겹침 제거 + 최소 duration
    final.sort(key=lambda x: x[1])
    for i in range(len(final) - 1):
        li, st, et, sc = final[i]
        ns = final[i + 1][1]
        if et > ns:
            final[i] = (li, st, ns - 0.05, sc)
    for i in range(len(final)):
        li, st, et, sc = final[i]
        if et - st < MIN_DUR:
            max_end = final[i + 1][1] - 0.05 if i + 1 < len(final) else st + MIN_DUR
            final[i] = (li, st, min(st + MIN_DUR, max_end), sc)

    # 번역
    original_lines = [lyrics[li] for li, *_ in sorted(final, key=lambda x: x[0])]
    safe_print(f"  번역 중... ({len(original_lines)}줄)")
    translations = translate_lyrics(original_lines, language)
    # 번역 결과를 가사 인덱스로 매핑
    trans_map = {}
    if translations:
        sorted_indices = sorted(set(li for li, *_ in final))
        for i, li in enumerate(sorted_indices):
            if i < len(translations):
                trans_map[li] = translations[i]

    # SRT 출력 (이중언어: 원문 + 번역)
    with open(srt_path, 'w', encoding='utf-8') as f:
        idx = 0
        for li, st, et, sc in final:
            if et <= st:
                continue
            idx += 1
            f.write(f"{idx}\n")
            f.write(f"{format_srt_time(st)} --> {format_srt_time(et)}\n")
            f.write(f"{lyrics[li]}\n")
            if li in trans_map and trans_map[li]:
                f.write(f"{trans_map[li]}\n")
            f.write(f"\n")

    matched = sum(1 for _, _, _, s in final if s >= 30)
    estimated = sum(1 for _, _, _, s in final if s < 30)
    safe_print(f"  SRT 저장: {srt_path} ({matched}줄 매칭 + {estimated}줄 추정)")
    return True


# ─── Main ───

def main():
    target = sys.argv[1] if len(sys.argv) > 1 else None

    mp3_files = sorted(glob.glob(os.path.join(MP3_DIR, "*_v*.mp3")))

    # Demucs 모델 1회 로딩
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    demucs_model = None

    for mp3_path in mp3_files:
        mp3_name = os.path.splitext(os.path.basename(mp3_path))[0]
        m = re.match(r'^(.+)_(v\d+)$', mp3_name)
        if not m:
            continue
        base_name = m.group(1)

        if target and mp3_name != target:
            continue

        prompt_path = os.path.join(PROMPT_DIR, f"{base_name}.md")
        if not os.path.exists(prompt_path):
            continue
        if detect_is_instrumental(prompt_path):
            continue

        language = detect_language(prompt_path)
        lyrics = extract_lyrics(prompt_path)
        if not lyrics:
            continue

        srt_path = os.path.join(MP3_DIR, f"{mp3_name}.srt")

        safe_print(f"\n=== {mp3_name} ({language}, {len(lyrics)}줄) ===")

        # 보컬 분리
        vocal_path = os.path.join(VOCAL_DIR, f"{mp3_name}_vocals.wav")
        if not os.path.exists(vocal_path):
            if demucs_model is None:
                from demucs.pretrained import get_model
                safe_print(f"  Demucs 모델 로딩...")
                demucs_model = get_model('htdemucs_6s').to(device)
            vocal_path = separate_vocals(mp3_path, mp3_name, demucs_model)

        # SRT 생성
        generate_srt(vocal_path, srt_path, language, lyrics)

    safe_print("\n완료!")


if __name__ == "__main__":
    main()
