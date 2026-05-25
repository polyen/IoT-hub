import sounddevice as sd
import soundfile as sf

DATASETS_DIR = "datasets/voice/wake_word/positive"
WAKE_WORD = "хей хата"

for i in range(100):
    input(f"Sample {i+1}/100 — press Enter, then say '{WAKE_WORD}'")
    audio = sd.rec(int(1.5 * 16000), samplerate=16000, channels=1, dtype="int16")
    sd.wait()
    sf.write(f"{DATASETS_DIR}/sample_{i:03d}.wav", audio, 16000)
    print("  saved")
