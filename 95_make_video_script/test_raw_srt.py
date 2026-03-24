"""
WhisperX raw 플랫리스트 → 단어별 SRT 생성
각 인식된 단어를 start~end 그대로 자막으로 출력
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


def main():
    vocal_path = os.path.join(os.path.dirname(__file__), "vocals", "04_Last_Train_Cassette_v2_vocals.wav")
    srt_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "05_Mp3", "04_Last_Train_Cassette_v2.srt")
    language = 'ja'

    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    compute_type = 'float16' if device == 'cuda' else 'int8'

    safe_print(f"[1] WhisperX 모델 로딩 (device: {device})...")
    model = whisperx.load_model("large-v3", device, compute_type=compute_type, language=language,
                                 vad_options={"vad_onset": 0.3, "vad_offset": 0.21})

    safe_print(f"[2] 오디오 로딩...")
    audio = whisperx.load_audio(vocal_path)

    safe_print("[3] Transcribe...")
    result = model.transcribe(audio, batch_size=16, language=language, chunk_size=10)

    safe_print("[4] Align...")
    align_model, metadata = whisperx.load_align_model(language_code=language, device=device)
    result = whisperx.align(result['segments'], align_model, metadata, audio, device, return_char_alignments=False)

    # 플랫 리스트 추출
    flat = []
    for seg in result['segments']:
        for w in seg.get('words', []):
            word = w.get('word', '').strip()
            start = w.get('start')
            end = w.get('end')
            if word and start is not None and end is not None:
                flat.append({'word': word, 'start': float(start), 'end': float(end)})

    safe_print(f"  총 {len(flat)}개 단어")

    # SRT 생성 - 각 단어를 개별 자막으로
    with open(srt_path, 'w', encoding='utf-8') as f:
        for i, w in enumerate(flat):
            f.write(f"{i+1}\n")
            f.write(f"{format_srt_time(w['start'])} --> {format_srt_time(w['end'])}\n")
            f.write(f"{w['word']}\n\n")

    safe_print(f"  SRT 저장: {srt_path}")
    safe_print(f"  완료! ({len(flat)}개 자막)")

    del model, align_model
    torch.cuda.empty_cache()


if __name__ == '__main__':
    main()
