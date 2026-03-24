"""
1. 갭 그룹 → raw 자막 (흰색)
2. concat + rapidfuzz로 각 가사줄의 위치 탐색 → 플랫리스트 start/end 추출
3. 가사줄 자막 (파란색) - 자체 타이밍
반복 가사는 마스킹으로 2nd 위치도 탐색
"""
import os
import sys
import torch
import whisperx
from rapidfuzz.fuzz import partial_ratio_alignment


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


def run(vocal_path, srt_path, language, gap_threshold, lyrics):
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

    # 갭 그룹핑 (raw 표시용)
    joiner = '' if language == 'ja' else ' '
    groups = []
    current = [flat[0]]
    for i in range(1, len(flat)):
        gap = flat[i]['start'] - flat[i-1]['end']
        if gap >= gap_threshold:
            groups.append(current)
            current = [flat[i]]
        else:
            current.append(flat[i])
    groups.append(current)

    safe_print(f"  플랫리스트: {len(flat)}개 → {len(groups)}개 그룹")

    # concat 문자열 + 인덱스 매핑
    # EN/KO: 단어 사이 공백 유지 (단어 경계 오매칭 방지)
    # JA: 공백 없이 연결 (원래 붙여 씀)
    concat = ''
    char_to_flat_idx = []
    for fi, w in enumerate(flat):
        if fi > 0 and language != 'ja':
            concat += ' '
            char_to_flat_idx.append(fi)  # 공백은 다음 단어의 인덱스로 매핑
        for ch in w['word']:
            concat += ch
            char_to_flat_idx.append(fi)

    # 각 가사줄의 위치 탐색
    # 1단계: 유니크 가사 score 높은 순 점유 + 블랭크
    # 2단계: 경합 가사 (동일 텍스트) → 인접 인덱스 기준 배정

    # 가사 쿼리도 언어별 공백 처리
    # EN/KO: 공백 유지, JA: 공백 제거
    def make_query(line):
        if language == 'ja':
            return line.replace(' ', '')
        return line

    # 유니크 vs 경합 분류
    from collections import Counter
    text_counts = Counter(make_query(line) for line in lyrics)
    unique_indices = []   # 1회만 등장하는 가사
    competing = {}        # text → [li, li, ...] 복수 등장하는 가사
    for li, line in enumerate(lyrics):
        key = make_query(line)
        if text_counts[key] == 1:
            unique_indices.append(li)
        else:
            competing.setdefault(key, []).append(li)

    # 1단계: 유니크 가사 — score 높은 순 점유
    unique_scores = []
    for li in unique_indices:
        query = make_query(lyrics[li])
        r = partial_ratio_alignment(query, concat)
        score = r.score if r else 0
        unique_scores.append((li, score))
    unique_scores.sort(key=lambda x: -x[1])

    lyrics_entries = []  # (li, start_t, end_t, score)
    masked = concat

    safe_print(f"  1단계: 유니크 가사 {len(unique_scores)}개 점유")
    for li, init_score in unique_scores:
        line = lyrics[li]
        query = make_query(line)
        r = partial_ratio_alignment(query, masked)
        if r and r.score > 0:
            ds = r.dest_start
            de = r.dest_end
            # 시작 보정: 매칭 시작이 이전 단어 끝부분이면 다음 단어로 이동
            start_fi = char_to_flat_idx[ds]
            while ds < de and char_to_flat_idx[ds] == start_fi and concat[ds] != query[0]:
                ds += 1
            start_fi = char_to_flat_idx[min(ds, len(char_to_flat_idx) - 1)]
            end_fi = char_to_flat_idx[min(de - 1, len(char_to_flat_idx) - 1)]
            start_t = flat[start_fi]['start']
            end_t = flat[end_fi]['end']
            lyrics_entries.append((li, start_t, end_t, r.score))
            masked = masked[:r.dest_start] + '\x00' * (r.dest_end - r.dest_start) + masked[r.dest_end:]
            orig = concat[r.dest_start:r.dest_end]
            diff = '' if query == orig else f'  matched=[{orig}]'
            safe_print(f"    가사{li+1:2d} score={r.score:.0f} [{start_t:6.1f}~{end_t:6.1f}] {line[:30]}{diff}")
        else:
            safe_print(f"    가사{li+1:2d} 매칭실패 {line[:30]}")

    # 2단계: 경합 가사 — 모든 후보 위치를 찾고 인접 인덱스로 배정
    safe_print(f"  2단계: 경합 가사 {sum(len(v) for v in competing.values())}개 배정")
    for text_key, indices in competing.items():
        # 이 텍스트의 모든 매칭 위치 찾기 (마스킹 반복)
        positions = []  # (dest_start, dest_end, start_t, end_t, score)
        temp_masked = masked
        for _ in range(len(indices)):
            r = partial_ratio_alignment(text_key, temp_masked)
            if r and r.score > 0:
                ds = r.dest_start
                de = r.dest_end
                # 시작 보정
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
            for li in indices:
                safe_print(f"    가사{li+1:2d} 매칭실패 {lyrics[li][:30]}")
            continue

        # 각 위치의 "주변 인덱스" 계산 — 이미 매칭된 가사 중 시간이 가까운 것의 인덱스
        def get_neighbor_index(pos_start_t):
            best_li = -1
            best_dist = float('inf')
            for eli, est, eet, esc in lyrics_entries:
                dist = abs(est - pos_start_t)
                if dist < best_dist:
                    best_dist = dist
                    best_li = eli
            return best_li

        # 각 위치에 대해 주변 인덱스 계산
        pos_neighbors = []
        for ds, de, st, et, sc in positions:
            neighbor = get_neighbor_index(st)
            pos_neighbors.append((ds, de, st, et, sc, neighbor))

        # 인덱스 배정: proximity가 작은 (가장 인접한) 쌍부터 배정
        candidates = []  # (proximity, li, pos_idx)
        for li in indices:
            for pi, (ds, de, st, et, sc, neighbor) in enumerate(pos_neighbors):
                proximity = abs(li - neighbor)
                candidates.append((proximity, li, pi))
        candidates.sort(key=lambda x: x[0])  # proximity 작은 순

        used_positions = set()
        used_lyrics_idx = set()
        assignments = []

        for proximity, li, pi in candidates:
            if li in used_lyrics_idx or pi in used_positions:
                continue
            used_positions.add(pi)
            used_lyrics_idx.add(li)
            assignments.append((li, pi))

        # 배정 결과 적용
        for li, pi in assignments:
            ds, de, st, et, sc, neighbor = pos_neighbors[pi]
            lyrics_entries.append((li, st, et, sc))
            masked = masked[:ds] + '\x00' * (de - ds) + masked[de:]
            safe_print(f"    가사{li+1:2d} score={sc:.0f} [{st:6.1f}~{et:6.1f}] 인접#{neighbor+1} {lyrics[li][:30]}")

    # SRT 생성 - raw 그룹 + 가사줄 (각각 독립 타이밍)
    srt_idx = 0
    with open(srt_path, 'w', encoding='utf-8') as f:
        # 1) raw 그룹 자막
        for gi, g in enumerate(groups):
            raw_text = joiner.join(w['word'] for w in g)
            start_t = g[0]['start']
            end_t = g[-1]['end']
            srt_idx += 1
            f.write(f"{srt_idx}\n")
            f.write(f"{format_srt_time(start_t)} --> {format_srt_time(end_t)}\n")
            f.write(f"{raw_text}\n\n")

        # 2) 평균 음절당 시간 계산
        total_chars = 0
        total_duration = 0
        for li, start_t, end_t, score in lyrics_entries:
            chars = len(lyrics[li].replace(' ', ''))
            dur = end_t - start_t
            total_chars += chars
            total_duration += dur
        sec_per_char = total_duration / total_chars if total_chars > 0 else 0.3
        safe_print(f"  평균 음절당 시간: {sec_per_char:.3f}초 ({total_chars}자/{total_duration:.1f}초)")

        # 3) 유실된 가사줄 추정 삽입 — 앞뒤 매칭 사이 균등 분배
        matched_indices = set(li for li, _, _, _ in lyrics_entries)
        matched_map = {li: (st, et, sc) for li, st, et, sc in lyrics_entries}
        all_entries = list(lyrics_entries)

        # 연속된 미매칭 구간을 찾아 앞뒤 매칭 사이 시간을 균등 분배
        li = 0
        while li < len(lyrics):
            if li in matched_indices:
                li += 1
                continue

            # 연속 미매칭 구간 찾기
            gap_start_li = li
            while li < len(lyrics) and li not in matched_indices:
                li += 1
            gap_end_li = li  # 이 인덱스는 매칭됨 (또는 끝)

            gap_count = gap_end_li - gap_start_li

            # 앞쪽 매칭 가사의 end
            prev_end = None
            for pi in range(gap_start_li - 1, -1, -1):
                if pi in matched_map:
                    prev_end = matched_map[pi][1]
                    break
            if prev_end is None:
                prev_end = 0.0

            # 뒤쪽 매칭 가사의 start
            next_start = None
            for ni in range(gap_end_li, len(lyrics)):
                if ni in matched_map:
                    next_start = matched_map[ni][0]
                    break

            if next_start is not None:
                # 앞뒤 매칭 사이 시간을 글자 수 비율로 분배
                available = next_start - prev_end - 0.1
                if available > 0:
                    # 각 추정 가사의 글자 수
                    char_counts = []
                    for j in range(gap_count):
                        idx = gap_start_li + j
                        char_counts.append(len(lyrics[idx].replace(' ', '')))
                    total_chars_gap = sum(char_counts) if sum(char_counts) > 0 else 1

                    cursor = prev_end + 0.1
                    for j in range(gap_count):
                        idx = gap_start_li + j
                        ratio = char_counts[j] / total_chars_gap
                        dur = available * ratio
                        est_start = cursor
                        est_end = cursor + dur - 0.05
                        all_entries.append((idx, est_start, est_end, -1))
                        cursor += dur
                else:
                    for j in range(gap_count):
                        idx = gap_start_li + j
                        est_start = prev_end + 0.1 + j * 0.5
                        all_entries.append((idx, est_start, est_start + 0.3, -1))
            else:
                # 뒤쪽 매칭 없음 → 평균 음절당 시간 사용
                for j in range(gap_count):
                    idx = gap_start_li + j
                    chars = len(lyrics[idx].replace(' ', ''))
                    dur = chars * sec_per_char
                    est_start = prev_end + 0.1 + j * dur
                    all_entries.append((idx, est_start, est_start + dur, -1))
                    prev_end = est_start + dur

        # 4) 시간순 정렬 + 겹침 제거
        all_entries.sort(key=lambda x: x[1])
        for i in range(len(all_entries) - 1):
            li, start_t, end_t, score = all_entries[i]
            next_start = all_entries[i + 1][1]
            if end_t > next_start:
                all_entries[i] = (li, start_t, next_start - 0.05, score)

        # 5) score < 30 + 순서 이탈자 → 추정으로 전환
        # 5a) 시간순 인덱스에서 LIS(최장 증가 부분수열)에 포함 안 되는 것 = 이탈자
        time_sorted = sorted(all_entries, key=lambda x: x[1])
        time_order_indices = [e[0] for e in time_sorted if e[3] >= 0]  # 매칭된 것만

        # LIS 구하기
        def find_lis(seq):
            from bisect import bisect_left
            tails = []
            indices = []  # 각 원소가 LIS에서 어느 위치인지
            for val in seq:
                pos = bisect_left(tails, val)
                if pos == len(tails):
                    tails.append(val)
                else:
                    tails[pos] = val
                indices.append(pos)
            # 역추적으로 LIS 원소 복원
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

        lis = find_lis(time_order_indices)
        outliers = set(idx for idx in time_order_indices if idx not in lis)
        if outliers:
            safe_print(f"  순서 이탈자: {sorted(outliers)} → 추정으로 전환")

        final_entries = []
        low_score_indices = set()
        for li, start_t, end_t, score in all_entries:
            if score >= 0 and (score < 30 or li in outliers):
                low_score_indices.add(li)
            else:
                final_entries.append((li, start_t, end_t, score))

        # 이탈자 + score < 30 가사는 추정 삽입
        for li in low_score_indices:
            chars = len(lyrics[li].replace(' ', ''))
            estimated_dur = chars * sec_per_char
            prev_end = None
            for entry in sorted(final_entries, key=lambda x: x[1]):
                if entry[0] < li and entry[2] is not None:
                    prev_end = entry[2]
            for entry in sorted(final_entries, key=lambda x: x[1]):
                if entry[0] > li and entry[1] is not None:
                    break
            if prev_end is not None:
                est_start = prev_end + 0.1
            else:
                est_start = 0.0
            final_entries.append((li, est_start, est_start + estimated_dur, -1))

        # 6) 시간순 정렬 + 겹침 제거 + 최소 duration 보장 (2.0초)
        MIN_DUR = 2.0
        final_entries.sort(key=lambda x: x[1])
        # 겹침 제거
        for i in range(len(final_entries) - 1):
            li, start_t, end_t, score = final_entries[i]
            next_start = final_entries[i + 1][1]
            if end_t > next_start:
                final_entries[i] = (li, start_t, next_start - 0.05, score)
        # 최소 duration 미달 시 뒤로 연장 (다음 자막과 안 겹치는 범위에서)
        for i in range(len(final_entries)):
            li, start_t, end_t, score = final_entries[i]
            if end_t - start_t < MIN_DUR:
                max_end = final_entries[i + 1][1] - 0.05 if i + 1 < len(final_entries) else start_t + MIN_DUR
                final_entries[i] = (li, start_t, min(start_t + MIN_DUR, max_end), score)

        # 7) SRT 출력 — score 구간별 색상
        for li, start_t, end_t, score in final_entries:
            if end_t <= start_t:
                continue
            srt_idx += 1
            f.write(f"{srt_idx}\n")
            f.write(f"{format_srt_time(start_t)} --> {format_srt_time(end_t)}\n")
            if score >= 70:
                f.write(f'<font color="#4488ff">{lyrics[li]} (#{li+1} s={score:.0f})</font>\n\n')
            elif score >= 30:
                f.write(f'<font color="#ffaa00">{lyrics[li]} (#{li+1} s={score:.0f})</font>\n\n')
            else:
                f.write(f'<font color="#ff4444">{lyrics[li]} (#{li+1} 추정)</font>\n\n')

    high = sum(1 for _, _, _, s in lyrics_entries if s >= 70)
    low = sum(1 for _, _, _, s in lyrics_entries if 30 <= s < 70)
    est = len(lyrics) - high - low
    safe_print(f"  SRT 저장: {srt_path}")
    safe_print(f"  가사 {len(lyrics)}줄: 파랑 {high} + 노랑 {low} + 빨강 {est}")


def extract_lyrics(prompt_path):
    """프롬프트 파일에서 가사 추출"""
    with open(prompt_path, 'r', encoding='utf-8') as f:
        content = f.read()
    import re
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
    """프롬프트 파일에서 언어 감지"""
    import re
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


def main():
    import glob
    import re

    base = os.path.dirname(os.path.dirname(__file__))
    mp3_dir = os.path.join(base, "05_Mp3")
    prompt_dir = os.path.join(base, "04_Suno_Prompt")
    vocal_dir = os.path.join(os.path.dirname(__file__), "vocals")

    gap_map = {'en': 0.8, 'ko': 0.8, 'ja': 0.2}

    mp3_files = sorted(glob.glob(os.path.join(mp3_dir, "*_v*.mp3")))

    for mp3_path in mp3_files:
        mp3_name = os.path.splitext(os.path.basename(mp3_path))[0]
        # base_name: 02_Before_the_Neon_Dies, version: v2
        m = re.match(r'^(.+)_(v\d+)$', mp3_name)
        if not m:
            continue
        base_name = m.group(1)

        prompt_path = os.path.join(prompt_dir, f"{base_name}.md")
        vocal_path = os.path.join(vocal_dir, f"{mp3_name}_vocals.wav")
        srt_path = os.path.join(mp3_dir, f"{mp3_name}.srt")

        if not os.path.exists(prompt_path):
            safe_print(f"프롬프트 없음: {base_name}.md — 건너뜀")
            continue
        if not os.path.exists(vocal_path):
            safe_print(f"보컬 없음: {mp3_name}_vocals.wav — 건너뜀")
            continue

        language = detect_language(prompt_path)
        lyrics = extract_lyrics(prompt_path)
        if not lyrics:
            safe_print(f"가사 없음: {base_name} — 건너뜀")
            continue

        gap = gap_map.get(language, 0.8)
        safe_print(f"\n=== {mp3_name} ({language}, gap={gap}, 가사 {len(lyrics)}줄) ===")
        run(vocal_path, srt_path, language, gap, lyrics)

    safe_print("\n전체 완료!")


if __name__ == '__main__':
    main()
