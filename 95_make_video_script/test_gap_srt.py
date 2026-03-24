"""
갭 기반 구절 그룹핑 → SRT 생성 (3언어 테스트)
EN/KO: 0.8초, JA: 0.2초
"""
import os
import sys
import torch
import whisperx


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


def process(vocal_path, srt_path, language, gap_threshold):
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    compute_type = 'float16' if device == 'cuda' else 'int8'

    safe_print(f"  [{language}] WhisperX 로딩...")
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

    safe_print(f"  플랫리스트: {len(flat)}개 → 갭 {gap_threshold}초 그룹핑")

    # 갭 기반 그룹핑
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

    # SRT 생성
    joiner = '' if language == 'ja' else ' '
    with open(srt_path, 'w', encoding='utf-8') as f:
        for i, g in enumerate(groups):
            text = joiner.join(w['word'] for w in g)
            start = g[0]['start']
            end = g[-1]['end']
            f.write(f"{i+1}\n")
            f.write(f"{format_srt_time(start)} --> {format_srt_time(end)}\n")
            f.write(f"{text}\n\n")

    safe_print(f"  → {len(groups)}개 구절, 저장: {srt_path}")

    del model, align_model
    torch.cuda.empty_cache()


def main():
    base = os.path.dirname(os.path.dirname(__file__))
    mp3_dir = os.path.join(base, "05_Mp3")
    vocal_dir = os.path.join(os.path.dirname(__file__), "vocals")

    songs = [
        {'vocal': '02_Before_the_Neon_Dies_v2_vocals.wav', 'srt': '02_Before_the_Neon_Dies_v2.srt', 'lang': 'en', 'gap': 0.8},
        {'vocal': '03_Midnight_Singongno_v1_vocals.wav', 'srt': '03_Midnight_Singongno_v1.srt', 'lang': 'ko', 'gap': 0.8},
        {'vocal': '04_Last_Train_Cassette_v1_vocals.wav', 'srt': '04_Last_Train_Cassette_v1.srt', 'lang': 'ja', 'gap': 0.2},
    ]

    for s in songs:
        safe_print(f"\n처리: {s['srt']} (lang={s['lang']}, gap={s['gap']})")
        process(
            os.path.join(vocal_dir, s['vocal']),
            os.path.join(mp3_dir, s['srt']),
            s['lang'],
            s['gap'],
        )

    safe_print("\n완료!")


if __name__ == '__main__':
    main()
