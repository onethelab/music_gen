"""
WhisperX raw 갭 그룹 + rapidfuzz로 매칭된 가사줄을 함께 표시하는 SRT 생성
가사줄은 파란색 <font color="#4488ff">
"""
import os
import sys
import json
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


def process(vocal_path, srt_path, language, gap_threshold, lyrics):
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    compute_type = 'float16' if device == 'cuda' else 'int8'

    safe_print(f"  WhisperX 처리중...")
    model = whisperx.load_model("large-v3", device, compute_type=compute_type, language=language,
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

    # concat 문자열 + 매핑
    concat = ''
    char_to_flat_idx = []
    for fi, w in enumerate(flat):
        for ch in w['word']:
            concat += ch
            char_to_flat_idx.append(fi)

    # 각 그룹의 concat 내 위치 범위 계산
    group_ranges = []
    pos = 0
    for g in groups:
        text = joiner.join(w['word'] for w in g)
        glen = len(text) if language == 'ja' else sum(len(w['word']) for w in g)
        group_ranges.append((pos, pos + glen))
        pos += glen

    # rapidfuzz로 각 가사줄의 최적 위치 찾기
    lyrics_matches = []  # (lyrics_idx, dest_start, dest_end, score)
    for li, line in enumerate(lyrics):
        query = line.replace(' ', '')
        result = partial_ratio_alignment(query, concat)
        if result and result.score > 0:
            lyrics_matches.append((li, result.dest_start, result.dest_end, result.score))

    # 각 그룹에 해당하는 가사줄 찾기
    group_lyrics = {i: [] for i in range(len(groups))}
    for li, ds, de, score in lyrics_matches:
        if score < 40:
            continue
        # 이 매칭이 어느 그룹과 겹치는지
        match_center = (ds + de) // 2
        for gi, (gs, ge) in enumerate(group_ranges):
            if gs <= match_center < ge:
                group_lyrics[gi].append((li, score))
                break

    # SRT 생성
    srt_idx = 0
    with open(srt_path, 'w', encoding='utf-8') as f:
        for gi, g in enumerate(groups):
            raw_text = joiner.join(w['word'] for w in g)
            start_t = g[0]['start']
            end_t = g[-1]['end']

            matched_lyrics = group_lyrics.get(gi, [])
            # score 순 정렬 후 가사 줄 번호순 정렬
            matched_lyrics.sort(key=lambda x: x[0])

            srt_idx += 1
            f.write(f"{srt_idx}\n")
            f.write(f"{format_srt_time(start_t)} --> {format_srt_time(end_t)}\n")
            f.write(f"{raw_text}\n")
            for li, score in matched_lyrics:
                f.write(f'<font color="#4488ff">{lyrics[li]} (#{li+1} s={score:.0f})</font>\n')
            f.write("\n")

    matched_count = sum(1 for v in group_lyrics.values() if v)
    safe_print(f"  SRT 저장: {srt_path}")
    safe_print(f"  {len(groups)}개 그룹 중 {matched_count}개에 가사 매칭")


def main():
    base = os.path.dirname(os.path.dirname(__file__))
    mp3_dir = os.path.join(base, "05_Mp3")
    vocal_dir = os.path.join(os.path.dirname(__file__), "vocals")

    # 일본어
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
    process(
        os.path.join(vocal_dir, '04_Last_Train_Cassette_v1_vocals.wav'),
        os.path.join(mp3_dir, '04_Last_Train_Cassette_v1.srt'),
        'ja', 0.2, ja_lyrics,
    )

    safe_print("\n완료!")


if __name__ == '__main__':
    main()
