import re

with open('modules/audio_core.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Add imports
content = content.replace('import numpy as np\nimport os', 'import numpy as np\nimport os\nfrom typing import Tuple, List, Union, Dict, Any\nimport parselmouth')

# Extract magic numbers
magic_numbers = """
# 静音阈值：振幅 ≈ -50dB (10^(-50/20) ≈ 0.00316)
SILENCE_AMPLITUDE_THRESHOLD = 10 ** (-50 / 20)
VOP_BUFFER_SEC = 0.02
VOP_WIN_LEN_SEC = 0.010
VOP_HOP_LEN_SEC = 0.002
VAD_TIME_STEP = 0.01
VAD_MIN_DURATION = 0.1
VAD_MERGE_THRESHOLD = 0.25
"""
content = content.replace('# 静音阈值：振幅 ≈ -50dB (10^(-50/20) ≈ 0.00316)\nSILENCE_AMPLITUDE_THRESHOLD = 10 ** (-50 / 20)', magic_numbers)

# Replace magic numbers in detect_vowel_onset
content = content.replace('buffer = 0.02', 'buffer = VOP_BUFFER_SEC')
content = content.replace('win_len = int(0.010 * sr)', 'win_len = int(VOP_WIN_LEN_SEC * sr)')
content = content.replace('hop_len = int(0.002 * sr)', 'hop_len = int(VOP_HOP_LEN_SEC * sr)')

# Replace magic numbers in macroscopic_vad
content = content.replace('intensity = snd.to_intensity(time_step=0.01)', 'intensity = snd.to_intensity(time_step=VAD_TIME_STEP)')
content = content.replace('if s[0] - merged[-1][1] < 0.25:', 'if s[0] - merged[-1][1] < VAD_MERGE_THRESHOLD:')
content = content.replace('return [s for s in merged if s[1]-s[0] > 0.1]', 'return [s for s in merged if s[1]-s[0] > VAD_MIN_DURATION]')

# Add type hints to functions
content = content.replace('def detect_vowel_onset(snd, rough_start, rough_end):', 'def detect_vowel_onset(snd: parselmouth.Sound, rough_start: float, rough_end: float) -> float:')
content = content.replace('def macroscopic_vad(snd):', 'def macroscopic_vad(snd: parselmouth.Sound) -> List[List[float]]:')
content = content.replace('def core_microscopic_vowel_nucleus(snd, global_pitch_or_arrays, t_min, t_max, drop_db, skip_front, trim_silence):', 'def core_microscopic_vowel_nucleus(snd: parselmouth.Sound, global_pitch_or_arrays: Union[parselmouth.Pitch, Tuple[np.ndarray, np.ndarray]], t_min: float, t_max: float, drop_db: float, skip_front: float, trim_silence: bool) -> Tuple[float, float, float, float]:')
content = content.replace('def recalculate_bounds_fast(snd, global_pitch_or_arrays, temp_s, temp_e, trim_silence):', 'def recalculate_bounds_fast(snd: parselmouth.Sound, global_pitch_or_arrays: Union[parselmouth.Pitch, Tuple[np.ndarray, np.ndarray]], temp_s: float, temp_e: float, trim_silence: bool) -> Tuple[float, float]:')
content = content.replace('def check_audio_segments(path):', 'def check_audio_segments(path: str) -> int:')
content = content.replace('def batch_process_worker(path, params, trim_silence):', 'def batch_process_worker(path: str, params: Dict[str, float], trim_silence: bool) -> Dict[str, Any]:')
content = content.replace('def long_process_worker(snd_values, snd_sf, pitch_xs, pitch_freqs, ms, me, params, trim_silence):', 'def long_process_worker(snd_values: np.ndarray, snd_sf: float, pitch_xs: np.ndarray, pitch_freqs: np.ndarray, ms: float, me: float, params: Dict[str, float], trim_silence: bool) -> Dict[str, Any]:')

# Remove duplicate local imports
content = content.replace('    import parselmouth\n', '')

with open('modules/audio_core.py', 'w', encoding='utf-8') as f:
    f.write(content)
