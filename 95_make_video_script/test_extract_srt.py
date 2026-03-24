"""
process.extract로 각 가사줄이 어떤 갭 그룹에 매칭되는지 top2 탐색
→ 반복 가사도 1st/2nd 모두 찾기
→ WhisperX raw + 파란색 가사줄 SRT 생성
"""
import os
import sys
import torch
import whisperx
from rapidfuzz import process, fuzz


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

    # 갭 그룹핑
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

    # 각 그룹의 텍스트
    group_texts = []
    for g in groups:
        text = joiner.join(w['word'] for w in g)
        group_texts.append(text)

    # 후보 리스트: 단일 그룹 + 인접 그룹 합침
    # choices[idx] = (텍스트, 관련 그룹 인덱스 리스트)
    choices = {}  # idx → (text, [gi, ...])
    choice_texts = []
    idx = 0
    for gi in range(len(groups)):
        choices[idx] = (group_texts[gi], [gi])
        choice_texts.append(group_texts[gi])
        idx += 1
    for gi in range(len(groups) - 1):
        merged = group_texts[gi] + group_texts[gi + 1]
        choices[idx] = (merged, [gi, gi + 1])
        choice_texts.append(merged)
        idx += 1

    safe_print(f"  후보: 단일 {len(groups)}개 + 인접합침 {len(groups)-1}개 = {len(choice_texts)}개")

    # process.extract로 각 가사줄 → 매칭 후보 top3 탐색
    group_lyrics = {i: [] for i in range(len(groups))}

    for li, line in enumerate(lyrics):
        query = line.replace(' ', '')
        results = process.extract(query, choice_texts, scorer=fuzz.partial_ratio, limit=3)

        for matched_text, score, ci in results:
            if score >= 50:
                related_groups = choices[ci][1]
                # 인접합침인 경우 → 가사 앞부분이 첫 그룹에, 뒷부분이 둘째 그룹에 해당
                # 단일 그룹이면 그대로
                for gi in related_groups:
                    if not any(x[0] == li for x in group_lyrics[gi]):
                        group_lyrics[gi].append((li, score))
                grp_str = '+'.join(str(g+1) for g in related_groups)
                safe_print(f"    가사{li+1:2d} → 그룹{grp_str} score={score:.0f} {line[:25]}")

    # 가사줄 번호 순 정렬
    for gi in group_lyrics:
        group_lyrics[gi].sort(key=lambda x: x[0])

    # SRT 생성
    srt_idx = 0
    with open(srt_path, 'w', encoding='utf-8') as f:
        for gi, g in enumerate(groups):
            raw_text = group_texts[gi]
            start_t = g[0]['start']
            end_t = g[-1]['end']

            matched = group_lyrics.get(gi, [])

            srt_idx += 1
            f.write(f"{srt_idx}\n")
            f.write(f"{format_srt_time(start_t)} --> {format_srt_time(end_t)}\n")
            f.write(f"{raw_text}\n")
            for li, score in matched:
                f.write(f'<font color="#4488ff">{lyrics[li]} (#{li+1} s={score:.0f})</font>\n')
            f.write("\n")

    matched_count = sum(1 for v in group_lyrics.values() if v)
    total_lyrics = sum(len(v) for v in group_lyrics.values())
    safe_print(f"  SRT 저장: {srt_path}")
    safe_print(f"  {len(groups)}개 그룹 중 {matched_count}개에 가사 매칭 (총 {total_lyrics}건)")


def main():
    base = os.path.dirname(os.path.dirname(__file__))
    mp3_dir = os.path.join(base, "05_Mp3")
    vocal_dir = os.path.join(os.path.dirname(__file__), "vocals")

    ja_lyrics = [
        '金曜の終電 空いた席に沈む',
        'ウォークマンの再生ボタン 爪で押す',
        '窓ガラスに映る 東京タワーの先',
        'あの日と同じ色で まだ点いてる',
        'カセットの隙間に 残った吐息',
        '巻き戻せない テープのように',
        '終電のカセット 誰にも聞こえない',
        'あなたの声だけが レールの上を走る',
        '車窓の光が 涙に変わる前に',
        'イヤホンを外して 改札を抜ける',
        '自販機の灯りで 缶コーヒーを選ぶ',
        '指先が覚えてる ブラック 砂糖なし',
        'ホームのベンチに 忘れ物のように',
        'あの笑い声だけが 座っている',
        'テープが絡まる 夏の終わりの',
        '録音した約束 もう届かない',
        '終電のカセット 誰にも聞こえない',
        'あなたの声だけが レールの上を走る',
        '車窓の光が 涙に変わる前に',
        'イヤホンを外して 改札を抜ける',
        '終電のカセット いつか擦り切れても',
        'この声が最後に 残るものでいい',
        '明日の始発まで ホームで待とうか',
        'いや 歩いて帰ろう 夜風が気持ちいい',
    ]

    safe_print("=== 04_Last_Train_Cassette_v1 (JA) ===")
    run(
        os.path.join(vocal_dir, '04_Last_Train_Cassette_v1_vocals.wav'),
        os.path.join(mp3_dir, '04_Last_Train_Cassette_v1.srt'),
        'ja', 0.2, ja_lyrics,
    )
    safe_print("\n완료!")


if __name__ == '__main__':
    main()
