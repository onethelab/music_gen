"""
자막계획 2단계 테스트: WhisperX로 플랫 리스트(단어+타이밍) 작성
- 보컬 wav 파일 → WhisperX transcribe + align → 단어별 시작/끝 시간 리스트 출력
"""

import os
import sys
import json
import torch
import whisperx

def safe_print(text):
    try:
        print(text.encode('cp949', errors='replace').decode('cp949'))
    except Exception:
        print(text.encode('utf-8', errors='replace').decode('utf-8', errors='replace'))


def generate_flat_list(vocal_path, language='en'):
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    compute_type = 'float16' if device == 'cuda' else 'int8'

    safe_print(f"[1] WhisperX 모델 로딩 (device: {device}, compute: {compute_type})...")
    model = whisperx.load_model("large-v3", device, compute_type=compute_type, language=language,
                                 vad_options={"vad_onset": 0.3, "vad_offset": 0.21})

    safe_print(f"[2] 오디오 로딩: {vocal_path}")
    audio = whisperx.load_audio(vocal_path)

    safe_print("[3] Transcribe 실행...")
    chunk_size = 10 if language == 'ja' else 30
    result = model.transcribe(audio, batch_size=16, language=language, chunk_size=chunk_size)

    safe_print(f"  세그먼트 수: {len(result['segments'])}")

    safe_print("[4] Align (word-level 타이밍) 실행...")
    align_model, metadata = whisperx.load_align_model(language_code=language, device=device)
    result = whisperx.align(result['segments'], align_model, metadata, audio, device, return_char_alignments=False)

    # 플랫 리스트 추출
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

    return flat_list, result['segments']


def main():
    # 커맨드라인 인자로 파일명과 언어 지정 가능
    if len(sys.argv) >= 2:
        vocal_file = sys.argv[1]
    else:
        vocal_file = "02_Before_the_Neon_Dies_v2_vocals.wav"

    language = sys.argv[2] if len(sys.argv) >= 3 else 'en'

    vocal_path = os.path.join(os.path.dirname(__file__), "vocals", vocal_file)
    if not os.path.exists(vocal_path):
        safe_print(f"파일 없음: {vocal_path}")
        sys.exit(1)

    flat_list, segments = generate_flat_list(vocal_path, language=language)

    # 결과 출력
    safe_print(f"\n{'='*60}")
    safe_print(f"총 단어 수: {len(flat_list)}")
    safe_print(f"{'='*60}")

    # 세그먼트별 텍스트 출력
    safe_print("\n[세그먼트별 텍스트]")
    for i, seg in enumerate(segments):
        text = seg.get('text', '').strip()
        start = seg.get('start', 0)
        end = seg.get('end', 0)
        safe_print(f"  {i+1:3d}. [{start:7.2f}s ~ {end:7.2f}s] {text}")

    # 플랫 리스트 출력
    safe_print("\n[플랫 리스트 (단어별 타이밍)]")
    for i, w in enumerate(flat_list):
        end_str = f"{w['end']:7.3f}s" if w['end'] is not None else "   N/A "
        safe_print(f"  {i+1:4d}. [{w['start']:7.3f}s ~ {end_str}] {w['word']}")

    # JSON 저장
    output_path = os.path.join(os.path.dirname(__file__), "test_flat_list_result.json")
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump({
            'vocal_file': os.path.basename(vocal_path),
            'total_words': len(flat_list),
            'flat_list': flat_list,
            'segments': [{'text': s.get('text', ''), 'start': s.get('start', 0), 'end': s.get('end', 0)} for s in segments],
        }, f, ensure_ascii=False, indent=2)
    safe_print(f"\n결과 저장: {output_path}")


if __name__ == '__main__':
    main()
