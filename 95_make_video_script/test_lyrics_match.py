"""
자막계획 3단계: 실제 가사의 각 단어(일본어는 음절)에 플랫리스트 기반 시간 작성
- 유사도 값이 높을때 시간을 작성
- 역전(뒤 가사와 앞 가사의 시간이 교차)되지 않을것
- 불분명한것에는 NaN 표시
"""

import os
import sys
import re
import json
import torch
import whisperx
from difflib import SequenceMatcher


def safe_print(text):
    try:
        print(text.encode('cp949', errors='replace').decode('cp949'))
    except Exception:
        print(text.encode('utf-8', errors='replace').decode('utf-8', errors='replace'))


def generate_flat_list(vocal_path, language='en'):
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    compute_type = 'float16' if device == 'cuda' else 'int8'
    safe_print(f"[WhisperX] 모델 로딩 (device: {device})...")
    model = whisperx.load_model("large-v3", device, compute_type=compute_type, language=language,
                                 vad_options={"vad_onset": 0.3, "vad_offset": 0.21})
    audio = whisperx.load_audio(vocal_path)
    chunk_size = 10 if language == 'ja' else 30
    result = model.transcribe(audio, batch_size=16, language=language, chunk_size=chunk_size)
    align_model, metadata = whisperx.load_align_model(language_code=language, device=device)
    result = whisperx.align(result['segments'], align_model, metadata, audio, device, return_char_alignments=False)

    flat_list = []
    for seg in result['segments']:
        for w in seg.get('words', []):
            word = w.get('word', '').strip()
            start = w.get('start')
            end = w.get('end')
            if word and start is not None:
                flat_list.append({
                    'word': word,
                    'start': round(float(start), 3),
                    'end': round(float(end), 3) if end is not None else None,
                })
    return flat_list


def normalize_word(text, language):
    """비교를 위한 정규화"""
    text = text.lower().strip(".,!?;:'\"()-–—")
    if language == 'ja':
        result = []
        for ch in text:
            cp = ord(ch)
            # 전각 카타카나 → 히라가나
            if 0x30A1 <= cp <= 0x30F6:
                result.append(chr(cp - 0x60))
            # 반각 카타카나 → 전각 히라가나
            elif 0xFF66 <= cp <= 0xFF9D:
                hw_map = {
                    0xFF66: 'を', 0xFF67: 'ぁ', 0xFF68: 'ぃ', 0xFF69: 'ぅ',
                    0xFF6A: 'ぇ', 0xFF6B: 'ぉ', 0xFF6C: 'ゃ', 0xFF6D: 'ゅ',
                    0xFF6E: 'ょ', 0xFF6F: 'っ', 0xFF70: 'ー',
                    0xFF71: 'あ', 0xFF72: 'い', 0xFF73: 'う', 0xFF74: 'え', 0xFF75: 'お',
                    0xFF76: 'か', 0xFF77: 'き', 0xFF78: 'く', 0xFF79: 'け', 0xFF7A: 'こ',
                    0xFF7B: 'さ', 0xFF7C: 'し', 0xFF7D: 'す', 0xFF7E: 'せ', 0xFF7F: 'そ',
                    0xFF80: 'た', 0xFF81: 'ち', 0xFF82: 'つ', 0xFF83: 'て', 0xFF84: 'と',
                    0xFF85: 'な', 0xFF86: 'に', 0xFF87: 'ぬ', 0xFF88: 'ね', 0xFF89: 'の',
                    0xFF8A: 'は', 0xFF8B: 'ひ', 0xFF8C: 'ふ', 0xFF8D: 'へ', 0xFF8E: 'ほ',
                    0xFF8F: 'ま', 0xFF90: 'み', 0xFF91: 'む', 0xFF92: 'め', 0xFF93: 'も',
                    0xFF94: 'や', 0xFF95: 'ゆ', 0xFF96: 'よ',
                    0xFF97: 'ら', 0xFF98: 'り', 0xFF99: 'る', 0xFF9A: 'れ', 0xFF9B: 'ろ',
                    0xFF9C: 'わ', 0xFF9D: 'ん',
                }
                result.append(hw_map.get(cp, ch))
            # 반각 濁点/半濁点 스킵
            elif cp in (0xFF9E, 0xFF9F):
                continue
            else:
                result.append(ch)
        return ''.join(result)
    return text


def similarity(a, b):
    return SequenceMatcher(None, a, b).ratio()


def extract_lyrics_words(prompt_path, language):
    """프롬프트 파일에서 가사 추출 → 단어 리스트 (일본어는 음절 단위)"""
    with open(prompt_path, 'r', encoding='utf-8') as f:
        content = f.read()
    lyrics_match = re.search(r'## Lyrics\s*\n(.+?)(?:\n##|\Z)', content, re.DOTALL)
    if not lyrics_match:
        return []

    lines = []
    current_section = ''
    for line in lyrics_match.group(1).split('\n'):
        line = line.strip()
        if re.match(r'^\[.*\]$', line):
            current_section = line
            continue
        if not line:
            continue
        lines.append({'section': current_section, 'text': line})

    words = []
    for line_info in lines:
        text = line_info['text']
        section = line_info['section']
        if language == 'ja':
            for ch in text:
                if ch.strip():
                    words.append({'word': ch, 'section': section, 'line': text})
        else:
            for w in text.split():
                words.append({'word': w, 'section': section, 'line': text})
    return words


def match_lyrics_to_flat(lyrics_words, flat_list, language, sim_threshold=0.5):
    """
    2패스 앵커 기반 매칭:
    Pass 1: 높은 유사도(>=0.8) 앵커를 순서대로 찾아 고정
    Pass 2: 앵커 사이 구간에서 낮은 threshold로 남은 단어 매칭
    - 역전 방지
    - 불분명하면 NaN
    """
    n_lyrics = len(lyrics_words)
    n_flat = len(flat_list)
    results = [None] * n_lyrics

    # 정규화 캐시
    lyrics_norms = [normalize_word(lw['word'], language) for lw in lyrics_words]
    flat_norms = [normalize_word(fw['word'], language) for fw in flat_list]

    # ── Pass 1: 높은 유사도 앵커 매칭 (시간 기반 범위 제한) ──
    anchor_threshold = 1.0 if language == 'ja' else 0.8
    flat_idx = 0
    anchors = []  # (lyrics_idx, flat_idx)

    for li in range(n_lyrics):
        lw_norm = lyrics_norms[li]
        if not lw_norm:
            continue
        best_sim = 0.0
        best_fi = -1

        # 시간 기반 범위 제한: 이전 앵커로부터 최대 30초 이내만 탐색
        max_time_jump = 30.0
        last_anchor_time = flat_list[flat_idx - 1]['start'] if flat_idx > 0 else -1.0
        search_end = min(flat_idx + (80 if language == 'ja' else 40), n_flat)

        for fi in range(flat_idx, search_end):
            fw = flat_list[fi]
            fw_norm = flat_norms[fi]

            # 시간 점프가 너무 크면 정지 (단, 첫 매칭이거나 가사 라인이 바뀌면 허용)
            if last_anchor_time >= 0 and fw['start'] - last_anchor_time > max_time_jump:
                break

            if language == 'ja':
                sim = 1.0 if lw_norm == fw_norm else 0.0
            else:
                sim = 1.0 if lw_norm == fw_norm else similarity(lw_norm, fw_norm)

            if sim >= anchor_threshold and sim > best_sim:
                best_sim = sim
                best_fi = fi
                if sim == 1.0:
                    break

        if best_fi >= 0:
            fw = flat_list[best_fi]
            results[li] = {
                'word': lyrics_words[li]['word'],
                'section': lyrics_words[li]['section'],
                'line': lyrics_words[li]['line'],
                'start': fw['start'],
                'end': fw['end'],
                'matched_word': fw['word'],
                'similarity': round(best_sim, 3),
            }
            anchors.append((li, best_fi))
            flat_idx = best_fi + 1

    safe_print(f"  Pass1 앵커: {len(anchors)}개")

    # ── Pass 2: 앵커 사이 미매칭 단어 처리 ──
    # 앵커 사이 구간별로 남은 가사↔플랫 매칭
    boundary_anchors = [(-1, -1)] + anchors + [(n_lyrics, n_flat)]

    for ai in range(len(boundary_anchors) - 1):
        li_start = boundary_anchors[ai][0] + 1
        li_end = boundary_anchors[ai + 1][0]
        fi_start = boundary_anchors[ai][1] + 1
        fi_end = boundary_anchors[ai + 1][1]

        if li_start >= li_end or fi_start >= fi_end:
            continue

        # 이 구간의 미매칭 가사를 순서대로 플랫리스트에서 찾기
        fi_cursor = fi_start
        for li in range(li_start, li_end):
            if results[li] is not None:
                continue
            lw_norm = lyrics_norms[li]
            if not lw_norm:
                continue

            best_sim = 0.0
            best_fi = -1
            for fi in range(fi_cursor, fi_end):
                fw_norm = flat_norms[fi]
                if language == 'ja':
                    sim = 1.0 if lw_norm == fw_norm else similarity(lw_norm, fw_norm)
                else:
                    sim = similarity(lw_norm, fw_norm)
                if sim > best_sim:
                    best_sim = sim
                    best_fi = fi

            if best_fi >= 0 and best_sim >= sim_threshold:
                fw = flat_list[best_fi]
                results[li] = {
                    'word': lyrics_words[li]['word'],
                    'section': lyrics_words[li]['section'],
                    'line': lyrics_words[li]['line'],
                    'start': fw['start'],
                    'end': fw['end'],
                    'matched_word': fw['word'],
                    'similarity': round(best_sim, 3),
                }
                fi_cursor = best_fi + 1

    # ── 미매칭 항목 NaN 처리 ──
    for li in range(n_lyrics):
        if results[li] is None:
            results[li] = {
                'word': lyrics_words[li]['word'],
                'section': lyrics_words[li]['section'],
                'line': lyrics_words[li]['line'],
                'start': None,
                'end': None,
                'matched_word': '',
                'similarity': 0,
            }

    # ── 역전 검증 및 수정 ──
    last_time = -1.0
    for r in results:
        if r['start'] is not None:
            if r['start'] <= last_time:
                r['start'] = None
                r['end'] = None
                r['matched_word'] = ''
                r['similarity'] = 0
            else:
                last_time = r['start']

    return results


def write_result_md(results, output_path, song_name, language, lyrics_word_count, flat_count):
    """결과를 MD 파일로 출력"""
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(f"# {song_name} - 가사 타이밍 매핑 결과\n\n")
        f.write(f"- 언어: {language}\n")
        f.write(f"- 가사 단위 수: {lyrics_word_count}\n")
        f.write(f"- WhisperX 플랫리스트 수: {flat_count}\n")

        matched = sum(1 for r in results if r['start'] is not None)
        nan_count = sum(1 for r in results if r['start'] is None)
        f.write(f"- 매핑 성공: {matched}\n")
        f.write(f"- NaN (불분명): {nan_count}\n\n")

        unit = '음절' if language == 'ja' else '단어'

        # 섹션별로 그룹핑
        current_section = None
        current_line = None
        idx = 0
        for r in results:
            if r['section'] != current_section:
                current_section = r['section']
                if current_section:
                    f.write(f"\n## {current_section}\n\n")
                else:
                    f.write(f"\n## (no section)\n\n")
                f.write(f"| # | {unit} | start | end | 매칭{unit} | 유사도 |\n")
                f.write(f"|---|--------|-------|-----|-----------|--------|\n")
                current_line = None

            idx += 1
            start_str = f"{r['start']:.3f}" if r['start'] is not None else "NaN"
            end_str = f"{r['end']:.3f}" if r['end'] is not None else "NaN"
            sim_str = f"{r['similarity']:.3f}" if r['similarity'] > 0 else "-"
            matched_w = r['matched_word'] if r['matched_word'] else "-"

            f.write(f"| {idx} | {r['word']} | {start_str} | {end_str} | {matched_w} | {sim_str} |\n")


def process_song(vocal_filename, prompt_filename, language, output_name):
    base_dir = os.path.dirname(os.path.dirname(__file__))
    vocal_path = os.path.join(os.path.dirname(__file__), "vocals", vocal_filename)
    prompt_path = os.path.join(base_dir, "04_Suno_Prompt", prompt_filename)
    output_path = os.path.join(os.path.dirname(__file__), f"{output_name}_lyrics_timing.md")

    safe_print(f"\n{'='*60}")
    safe_print(f"처리: {vocal_filename} (lang={language})")
    safe_print(f"{'='*60}")

    # 1. 플랫리스트 생성
    flat_list = generate_flat_list(vocal_path, language)
    safe_print(f"  플랫리스트: {len(flat_list)}개")

    # 2. 가사 추출
    lyrics_words = extract_lyrics_words(prompt_path, language)
    safe_print(f"  가사 단위: {len(lyrics_words)}개")

    # 3. 매칭
    threshold = 0.6 if language == 'ja' else 0.5
    results = match_lyrics_to_flat(lyrics_words, flat_list, language, sim_threshold=threshold)

    matched = sum(1 for r in results if r['start'] is not None)
    safe_print(f"  매핑 성공: {matched}/{len(results)}")

    # 4. MD 파일 출력
    write_result_md(results, output_path, output_name, language, len(lyrics_words), len(flat_list))
    safe_print(f"  결과 저장: {output_path}")

    return output_path


def main():
    songs = [
        {
            'vocal': '02_Before_the_Neon_Dies_v2_vocals.wav',
            'prompt': '02_Before_the_Neon_Dies.md',
            'language': 'en',
            'name': '02_Before_the_Neon_Dies_v2',
        },
        {
            'vocal': '04_Last_Train_Cassette_v1_vocals.wav',
            'prompt': '04_Last_Train_Cassette.md',
            'language': 'ja',
            'name': '04_Last_Train_Cassette_v1',
        },
    ]

    for song in songs:
        process_song(song['vocal'], song['prompt'], song['language'], song['name'])

    safe_print("\n완료!")


if __name__ == '__main__':
    main()
