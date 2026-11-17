from __future__ import annotations

import subprocess
import wave
from pathlib import Path
from typing import Any

import numpy as np

from app.config import get_ffmpeg_exe
from app.models import SceneItem, WordTiming


def get_audio_duration(audio_path: Path) -> float:
    ffmpeg = get_ffmpeg_exe()
    res = subprocess.run(
        [ffmpeg, "-i", str(audio_path)],
        capture_output=True,
        text=True,
    )
    for line in res.stderr.splitlines():
        if "Duration:" in line:
            part = line.split("Duration:")[1].split(",")[0].strip()
            h, m, s = part.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    return 0.0


def find_best_highlight_start(audio_path: Path, clip_duration: float = 45.0) -> float:
    """
    Analyze RMS energy across the track to automatically locate the most energetic
    chorus / riff / groove segment of length `clip_duration`, skipping quiet intros even on shorter clips.
    """
    total_dur = get_audio_duration(audio_path)
    if total_dur <= 8.0:
        return 0.0

    ffmpeg = get_ffmpeg_exe()
    sr = 8000
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(audio_path),
        "-ac",
        "1",
        "-ar",
        str(sr),
        "-f",
        "s16le",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        if total_dur <= clip_duration + 2.0:
            return 0.0
        return max(0.0, min(total_dur - clip_duration, total_dur * 0.25))

    samples = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    n_secs = len(samples) // sr
    if n_secs <= 8:
        return 0.0
    trimmed = samples[: n_secs * sr].reshape(n_secs, sr)
    sec_rms = np.sqrt(np.mean(trimmed ** 2, axis=1) + 1e-9)

    win = max(5, int(round(clip_duration)))
    if total_dur <= clip_duration + 2.0 or win >= n_secs:
        # Track fits within clip_duration: still skip quiet intro pads / fades so music enters on the groove
        body_start = min(4, max(1, n_secs // 5))
        median_body = float(np.median(sec_rms[body_start:]))
        if median_body > 1e-4 and float(sec_rms[0]) < median_body * 0.45:
            scan_limit = min(max(3, int(n_secs * 0.45)), n_secs - 4)
            for idx in range(1, scan_limit + 1):
                if float(sec_rms[idx]) >= median_body * 0.65:
                    return float(idx)
        return 0.0

    csum = np.concatenate([[0.0], np.cumsum(sec_rms)])
    window_energy = (csum[win:] - csum[:-win]) / float(win)

    min_start = 5 if n_secs > win + 10 else 0
    max_start = len(window_energy) - 1
    if min_start >= max_start:
        return 0.0
    best_idx = int(np.argmax(window_energy[min_start : max_start + 1])) + min_start
    return float(best_idx)


def compute_waveform_peaks(
    audio_path: Path,
    num_bars: int = 240,
    highlight_window_sec: float = 12.0,
) -> dict[str, Any]:
    """
    Decode mono 8kHz PCM rapidly via FFmpeg to return normalized waveform bars [0.05..1.0],
    exact duration, and suggested climax start for browser timeline visualization (100% offline).
    """
    total_dur = get_audio_duration(audio_path)
    ffmpeg = get_ffmpeg_exe()
    sr = 8000
    proc = subprocess.run(
        [
            ffmpeg, "-y", "-i", str(audio_path),
            "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "pipe:1",
        ],
        capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        fallback_bars = [0.25] * max(20, num_bars)
        return {
            "duration": round(total_dur, 2),
            "peaks": fallback_bars,
            "suggested_finale_start": round(max(0.0, total_dur * 0.65), 1) if total_dur > 25 else 0.0,
        }

    samples = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    if total_dur <= 0.1:
        total_dur = len(samples) / float(sr)

    n_bars = max(40, min(400, int(num_bars)))
    bins = np.array_split(np.abs(samples), n_bars)
    raw_levels = np.array([
        float(np.sqrt(np.mean(b * b)) * 0.72 + np.percentile(b, 92) * 0.28) if len(b) > 0 else 0.0
        for b in bins
    ], dtype=np.float32)
    ref = float(np.percentile(raw_levels, 97)) if len(raw_levels) else 1.0
    ref = max(ref, 1e-4)
    peaks = [round(float(min(1.0, max(0.06, v / ref))), 3) for v in raw_levels]

    # Suggested finale highlight window (~highlight_window_sec in the second half of the track)
    n_secs = len(samples) // sr
    suggested_fin = round(max(0.0, total_dur * 0.65), 1)
    win = max(4, int(round(highlight_window_sec)))
    if n_secs > win + 6:
        sec_rms = np.sqrt(np.mean(samples[: n_secs * sr].reshape(n_secs, sr) ** 2, axis=1) + 1e-9)
        csum = np.concatenate([[0.0], np.cumsum(sec_rms)])
        w_eng = (csum[win:] - csum[:-win]) / float(win)
        lo = min(max(0, len(w_eng) - 1), int(n_secs * 0.55))
        hi = max(lo + 1, len(w_eng) - 1)
        suggested_fin = float(int(np.argmax(w_eng[lo:hi])) + lo)

    return {
        "duration": round(total_dur, 2),
        "peaks": peaks,
        "suggested_finale_start": suggested_fin,
    }


def prepare_reel2_music_clip(
    audio_path: Path,
    out_wav: Path,
    start_sec: float | None = None,
    duration_sec: float = 45.0,
) -> tuple[Path, float, float]:
    """Extract normalized 44.1kHz stereo WAV segment for Reel 2 ('Ouça a Música')."""
    total_dur = get_audio_duration(audio_path)
    chosen_start = (
        max(0.0, start_sec)
        if start_sec is not None
        else find_best_highlight_start(audio_path, clip_duration=duration_sec)
    )
    if total_dur > 0 and chosen_start + duration_sec > total_dur:
        chosen_start = max(0.0, total_dur - duration_sec)
    actual_dur = min(duration_sec, max(5.0, total_dur - chosen_start)) if total_dur > 0 else duration_sec

    ffmpeg = get_ffmpeg_exe()
    fade_out_start = max(0.5, actual_dur - 2.2)
    afilter = (
        f"afade=t=in:st=0:d=0.8,"
        f"afade=t=out:st={fade_out_start:.2f}:d=2.2,"
        "loudnorm=I=-14:TP=-1.5:LRA=11"
    )
    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        f"{chosen_start:.2f}",
        "-t",
        f"{actual_dur:.2f}",
        "-i",
        str(audio_path),
        "-vn",
        "-af",
        afilter,
        "-ar",
        "44100",
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        str(out_wav),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Falha ao preparar áudio do Reel 2: {res.stderr[-400:]}")
    return out_wav, chosen_start, actual_dur


def mix_narration_with_music(
    narration_wav: Path,
    music_source: Path,
    out_wav: Path,
    music_start_sec: float = 0.0,
    outro_tail_sec: float = 2.0,
) -> tuple[Path, float]:
    """
    Mix narration with music bed (with -14 LUFS loudness normalization & safe ducking):
    - Music sits gently underneath narration (-15 dB bed),
    - Swells up during the final `outro_tail_sec` seconds after narration ends!
    """
    with wave.open(str(narration_wav), "rb") as wf:
        narr_dur = wf.getnframes() / float(wf.getframerate())

    total_dur = narr_dur + outro_tail_sec
    ffmpeg = get_ffmpeg_exe()
    fade_out_start = max(0.5, total_dur - 1.5)

    t_end = f"{narr_dur:.2f}"
    t_ramp = f"{narr_dur + 0.6:.2f}"
    filter_complex = (
        f"[0:a]apad=pad_dur={outro_tail_sec:.2f},atrim=0:{total_dur:.2f},asetpts=PTS-STARTPTS,"
        f"afade=t=in:st=0:d=0.03[narr];"
        f"[1:a]atrim=start={music_start_sec:.2f}:duration={total_dur:.2f},asetpts=PTS-STARTPTS,"
        f"aloop=loop=-1:size=2e+09,atrim=0:{total_dur:.2f},"
        f"volume='if(lt(t,{t_end}),0.16,if(lt(t,{t_ramp}),0.16+(t-{t_end})*0.58,0.52))':eval=frame,"
        f"afade=t=in:st=0:d=0.8,afade=t=out:st={fade_out_start:.2f}:d=1.5[bg];"
        f"[narr][bg]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
        f"loudnorm=I=-14:TP=-1.5:LRA=11,alimiter=limit=0.95[out]"
    )
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(narration_wav),
        "-stream_loop",
        "-1",
        "-i",
        str(music_source),
        "-filter_complex",
        filter_complex,
        "-map",
        "[out]",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        str(out_wav),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Falha ao mixar narração e música: {res.stderr[-400:]}")
    return out_wav, total_dur


def pad_narration_only(narration_wav: Path, out_wav: Path, outro_tail_sec: float = 1.5) -> tuple[Path, float]:
    with wave.open(str(narration_wav), "rb") as wf:
        narr_dur = wf.getnframes() / float(wf.getframerate())
    total_dur = narr_dur + outro_tail_sec
    fade_out_st = max(0.2, total_dur - 0.08)
    ffmpeg = get_ffmpeg_exe()
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(narration_wav),
        "-af",
        f"apad=pad_dur={outro_tail_sec:.2f},atrim=0:{total_dur:.2f},afade=t=in:st=0:d=0.03,afade=t=out:st={fade_out_st:.2f}:d=0.08,loudnorm=I=-14:TP=-1.5:LRA=11",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        str(out_wav),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Falha ao preparar narração limpa: {res.stderr[-400:]}")
    return out_wav, total_dur


def compute_audio_energy_envelope(wav_path: Path, fps: int = 30, num_bands: int = 40) -> np.ndarray:
    """
    Compute per-frame frequency spectrum amplitudes [num_frames, num_bands] (values 0..1)
    for buttery smooth audio-reactive visualization in Reel 2.
    """
    with wave.open(str(wav_path), "rb") as wf:
        sr = wf.getframerate()
        channels = wf.getnchannels()
        raw = wf.readframes(wf.getnframes())

    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1)

    hop = max(1, sr // fps)
    fft_size = 2048
    n_frames = max(1, len(pcm) // hop)
    window = np.hanning(fft_size).astype(np.float32)
    freqs = np.fft.rfftfreq(fft_size, d=1.0 / sr)

    band_edges = np.geomspace(45.0, min(11000.0, sr * 0.45), num_bands + 1)
    band_masks = [(freqs >= band_edges[b]) & (freqs < band_edges[b + 1]) for b in range(num_bands)]

    spectrum = np.zeros((n_frames, num_bands), dtype=np.float32)
    for f_idx in range(n_frames):
        start = f_idx * hop
        seg = pcm[start : start + fft_size]
        if len(seg) < fft_size:
            seg = np.pad(seg, (0, fft_size - len(seg)))
        mag = np.abs(np.fft.rfft(seg * window))
        for b, mask in enumerate(band_masks):
            if np.any(mask):
                spectrum[f_idx, b] = float(np.mean(mag[mask]))

    tilt = np.linspace(0.95, 2.6, num_bands, dtype=np.float32)[None, :]
    spectrum = np.log1p(spectrum * tilt * 0.9)
    peak = float(np.percentile(spectrum, 98.5)) or 1.0
    spectrum = np.clip(spectrum / peak, 0.0, 1.0)

    smoothed = np.copy(spectrum)
    for i in range(1, n_frames):
        smoothed[i] = np.maximum(spectrum[i], smoothed[i - 1] * 0.80) * 0.65 + smoothed[i - 1] * 0.35
    return np.clip(smoothed, 0.0, 1.0)


def analyze_beats_and_onsets(wav_path: Path, fps: int = 30) -> dict[str, Any]:
    """
    Zero-extra-dependency NumPy onset & BPM detector (inspired by FireRed beat-sync & OpenFilm data files):
    Uses low-mid spectral flux across frames to locate beat instants, estimated BPM,
    and a decaying per-frame beat_flash array [0..1].
    """
    spec = compute_audio_energy_envelope(wav_path, fps=fps, num_bands=24)
    n_frames = len(spec)
    # Focus spectral flux on kick/snare weight (bands 0..10)
    low_mid = spec[:, :10]
    diff = np.diff(low_mid, axis=0, prepend=low_mid[:1])
    flux = np.sum(np.maximum(0.0, diff), axis=1)

    # Adaptive local threshold peak picking (min spacing ~0.26s)
    min_gap = max(3, int(round(fps * 0.26)))
    win = max(5, int(round(fps * 0.45)))
    beat_frames: list[int] = []
    last_beat = -min_gap
    mean_flux = float(np.mean(flux))
    std_flux = float(np.std(flux))
    global_thresh = mean_flux + 0.55 * std_flux

    for i in range(1, n_frames - 1):
        lo = max(0, i - win // 2)
        hi = min(n_frames, i + win // 2 + 1)
        local_avg = float(np.mean(flux[lo:hi]))
        if (
            flux[i] > flux[i - 1]
            and flux[i] >= flux[i + 1]
            and flux[i] > max(global_thresh, local_avg * 1.35)
            and (i - last_beat) >= min_gap
        ):
            beat_frames.append(i)
            last_beat = i

    beat_times = [round(f / float(fps), 3) for f in beat_frames]
    bpm = 120.0
    if len(beat_times) >= 4:
        intervals = np.diff(np.array(beat_times))
        valid = intervals[(intervals >= 0.28) & (intervals <= 1.2)]
        if len(valid) >= 2:
            med_ibi = float(np.median(valid))
            raw_bpm = 60.0 / max(0.1, med_ibi)
            while raw_bpm < 72.0:
                raw_bpm *= 2.0
            while raw_bpm > 175.0:
                raw_bpm /= 2.0
            bpm = round(raw_bpm, 1)

    # Generate sharp-attack / exponential-decay beat flash per frame
    beat_flash = np.zeros(n_frames, dtype=np.float32)
    decay = 0.76
    for bf in beat_frames:
        beat_flash[bf] = 1.0
    for i in range(1, n_frames):
        beat_flash[i] = max(beat_flash[i], beat_flash[i - 1] * decay)

    return {
        "bpm": bpm,
        "beat_times": beat_times,
        "beat_flash": beat_flash,
        "fps": fps,
        "n_frames": n_frames,
    }


def snap_durations_to_beats(
    raw_durations: list[float],
    beat_times: list[float],
    max_shift_sec: float = 0.22,
) -> list[float]:
    """
    Gently align scene cut boundaries to the nearest musical beat within ±max_shift_sec
    while keeping total video length strictly equal to sum(raw_durations).
    """
    if len(raw_durations) <= 1 or len(beat_times) < 4:
        return raw_durations

    beats = np.array(beat_times, dtype=np.float64)
    total = sum(raw_durations)
    cum = 0.0
    snapped_cuts: list[float] = [0.0]
    for dur in raw_durations[:-1]:
        cum += dur
        diffs = np.abs(beats - cum)
        idx = int(np.argmin(diffs))
        if diffs[idx] <= max_shift_sec and beats[idx] > snapped_cuts[-1] + 2.2:
            snapped_cuts.append(float(beats[idx]))
        else:
            snapped_cuts.append(cum)
    snapped_cuts.append(total)

    new_durs = [max(2.0, snapped_cuts[i + 1] - snapped_cuts[i]) for i in range(len(raw_durations))]
    # Re-normalize last scene subtly so total exactly equals sum(raw_durations)
    delta = total - sum(new_durs)
    new_durs[-1] = max(2.0, new_durs[-1] + delta)
    return new_durs


def _read_wav_stereo_float(wav_path: Path) -> np.ndarray:
    with wave.open(str(wav_path), "rb") as wf:
        ch = wf.getnchannels()
        raw = wf.readframes(wf.getnframes())
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch == 1:
        return np.column_stack([pcm, pcm])
    return pcm.reshape(-1, ch)[:, :2]


def _write_wav_stereo_float(wav_path: Path, pcm: np.ndarray, sample_rate: int = 44100) -> None:
    clipped = np.clip(pcm, -0.999, 0.999)
    int16_arr = (clipped * 32767.0).astype(np.int16)
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(int16_arr.tobytes())


def _slice_pcm_wrap(src_pcm: np.ndarray, start_smp: int, count: int) -> np.ndarray:
    """Slice `count` stereo samples from `src_pcm` starting at `start_smp`, wrapping cleanly modulo length."""
    n = len(src_pcm)
    if n <= 0 or count <= 0:
        return np.zeros((max(0, count), 2), dtype=np.float32)
    idx = (np.arange(count, dtype=np.int64) + int(start_smp)) % n
    return src_pcm[idx]


def _find_audible_lead_offset(src_pcm: np.ndarray, sr: int = 44100, max_scan_sec: float = 3.5) -> float:
    """Detect digital silence at start of MP3/WAV so opening music bed never plays dead air."""
    limit = min(len(src_pcm), int(max_scan_sec * sr))
    step = max(1, sr // 50)
    for pos in range(0, limit, step):
        win = src_pcm[pos : pos + step]
        if len(win) > 0 and float(np.sqrt(np.mean(win ** 2))) > 0.010:
            return round(pos / float(sr), 3)
    return 0.0


def generate_ambient_synthwave_bed(out_wav: Path, duration_sec: float = 62.0) -> Path:
    """
    Generate a clean, subtle Vice-City synthwave ambient bed (soft analog pad + muted bass pulse)
    purely offline via numpy so gaming Shorts have gentle atmosphere even without an external MP3.
    """
    sr = 44100
    dur = max(8.0, float(duration_sec))
    n = int(round(dur * sr))
    t = np.linspace(0.0, dur, n, endpoint=False, dtype=np.float32)

    # 8-bar Vice City minor progression: Am -> F -> C -> G (each chord 2.0s at 120 BPM)
    chords = [
        (110.0, 220.0, 261.63, 329.63),
        (87.31, 174.61, 220.0, 261.63),
        (130.81, 261.63, 329.63, 392.00),
        (98.00, 196.00, 246.94, 293.66),
    ]
    bar_len = 2.0
    cycle_pos = (t / bar_len).astype(np.int32) % len(chords)
    chord_freqs = np.array(chords, dtype=np.float32)
    f_bass = chord_freqs[cycle_pos, 0]
    f_root = chord_freqs[cycle_pos, 1]
    f_thrd = chord_freqs[cycle_pos, 2]
    f_fift = chord_freqs[cycle_pos, 3]

    # Phase integration avoids clicks at chord changes
    def _osc(freqs: np.ndarray, detune: float = 1.0) -> np.ndarray:
        phase = 2.0 * np.pi * np.cumsum(freqs * detune / sr)
        return np.sin(phase, dtype=np.float32)

    pad_l = (
        0.34 * _osc(f_root, 0.998)
        + 0.28 * _osc(f_thrd, 1.001)
        + 0.24 * _osc(f_fift, 0.999)
    )
    pad_r = (
        0.34 * _osc(f_root, 1.002)
        + 0.28 * _osc(f_thrd, 0.999)
        + 0.24 * _osc(f_fift, 1.001)
    )

    # Subtle eighth-note retro pulse envelope on bass
    pulse_env = (0.35 + 0.65 * (np.sin(np.pi * (t * 4.0 % 1.0)) ** 2)).astype(np.float32)
    bass_sig = 0.42 * _osc(f_bass) * pulse_env

    stereo = np.stack([pad_l + bass_sig, pad_r + bass_sig], axis=1)
    # Soft level (~0.24 peak before ducking/loudnorm)
    stereo = stereo * 0.18
    fade_n = min(n // 4, int(1.2 * sr))
    if fade_n > 0:
        stereo[:fade_n] *= np.linspace(0.0, 1.0, fade_n, dtype=np.float32)[:, None]
        stereo[-fade_n:] *= np.linspace(1.0, 0.0, fade_n, dtype=np.float32)[:, None]

    out_wav.parent.mkdir(parents=True, exist_ok=True)
    _write_wav_stereo_float(out_wav, stereo, sample_rate=sr)
    return out_wav


def assemble_interleaved_audio(
    segments: list[dict[str, Any]],
    work_dir: Path,
    music_source: Path | None = None,
    music_start_sec: float | None = None,
    finale_audio_start_sec: float | None = None,
    intro_music_sec: float = 0.2,
    finale_music_sec: float = 12.0,
    duck_level: float = 0.15,
    full_level: float = 0.92,
    allow_short_finale: bool = False,
) -> dict[str, Any]:
    """
    Build a sample-accurate interleaved Single All-in-One audio master:
      [Optional Intro] -> [Speech 1 + Ducked Bed] -> [Lyric-Cued Break 1 Full] -> ... -> [Speech N] -> [Finale Climax]
    Supports cue-targeted source timestamps (`scene.music_break_audio_start_sec` + `finale_audio_start_sec`)
    with smooth equal-power crossfades right into sung lyric hooks, as well as seamless sequential mode.
    """
    if not segments:
        raise ValueError("Nenhuma cena com narração encontrada para montar a linha do tempo.")

    sr = 44100
    render_dir = work_dir / "render_allinone"
    render_dir.mkdir(parents=True, exist_ok=True)

    intro_dur = round(max(0.0, min(6.0, float(intro_music_sec))), 3)
    min_fin = 0.6 if allow_short_finale else 3.0
    finale_dur = round(max(min_fin, min(40.0, float(finale_music_sec))), 3)

    timeline: list[dict[str, Any]] = []
    visual_segments: list[dict[str, Any]] = []
    break_cues: list[dict[str, Any]] = []
    scene_spans: list[tuple[int, float, float]] = []
    all_words: list[WordTiming] = []
    speech_islands: list[list[float]] = []

    cursor = 0.0
    if intro_dur >= 0.25:
        timeline.append(
            {
                "kind": "intro",
                "label": "Intro Musical",
                "start": 0.0,
                "end": intro_dur,
                "duration_sec": intro_dur,
                "music_state": "loud",
            }
        )
        cursor = intro_dur
    elif intro_dur > 0.02:
        # Micro breath before opening hook so spoken word onset is clean
        timeline.append(
            {
                "kind": "intro",
                "label": "Pickup",
                "start": 0.0,
                "end": intro_dur,
                "duration_sec": intro_dur,
                "music_state": "ducked",
            }
        )
        cursor = intro_dur

    placements: list[tuple[float, Path, float]] = []
    break_counter = 0

    for idx, seg in enumerate(segments):
        scene: SceneItem = seg["scene"]
        voice_dur = float(seg["duration_sec"])
        raw_brk = float(getattr(scene, "music_break_after_sec", 0.0) or 0.0)
        has_break = (idx < len(segments) - 1) and (raw_brk >= 1.0)
        tail_pad = 0.14 if (has_break or idx == len(segments) - 1) else 0.18

        speech_start = round(cursor, 3)
        voice_end = round(speech_start + voice_dur, 3)
        speech_end = round(voice_end + tail_pad, 3)

        placements.append((speech_start, Path(seg["wav_path"]), voice_dur))
        scene_spans.append((scene.index, speech_start, speech_end))
        scene.duration_sec = round(speech_end - speech_start, 3)

        for wt in seg["local_words"]:
            all_words.append(
                WordTiming(
                    word=wt.word,
                    start=round(speech_start + wt.start, 3),
                    end=round(speech_start + wt.end, 3),
                )
            )

        if speech_islands and (speech_start - speech_islands[-1][2]) < 0.85:
            speech_islands[-1][1] = voice_end
            speech_islands[-1][2] = speech_end
        else:
            speech_islands.append([speech_start, voice_end, speech_end])

        timeline.append(
            {
                "kind": "speech",
                "scene_index": scene.index,
                "label": f"C{scene.index}: {scene.title}",
                "start": speech_start,
                "end": speech_end,
                "voice_end": voice_end,
                "duration_sec": round(speech_end - speech_start, 3),
                "music_state": "ducked",
            }
        )

        # Visual segment for Scene 1 includes any micro pickup / Intro seamlessly
        vis_start = 0.0 if idx == 0 else speech_start
        vis_kind = "hook_scene" if idx == 0 else "narration_scene"
        visual_segments.append(
            {
                "kind": vis_kind,
                "scene_index": scene.index,
                "start": round(vis_start, 3),
                "end": speech_end,
                "duration_sec": round(speech_end - vis_start, 3),
                "intro_embedded_sec": intro_dur if (idx == 0 and intro_dur >= 0.25) else 0.0,
            }
        )
        cursor = speech_end

        if has_break:
            break_counter += 1
            brk_dur = round(min(12.0, raw_brk), 3)
            brk_start = round(cursor, 3)
            brk_end = round(brk_start + brk_dur, 3)
            src_cue = getattr(scene, "music_break_audio_start_sec", None)
            lyric_en = (getattr(scene, "music_break_lyric_en", "") or "").strip()
            lyric_pt = (getattr(scene, "music_break_lyric_pt", "") or "").strip()
            timeline.append(
                {
                    "kind": "break",
                    "break_index": break_counter,
                    "after_scene": scene.index,
                    "label": f"Respiro Musical B{break_counter}",
                    "start": brk_start,
                    "end": brk_end,
                    "duration_sec": brk_dur,
                    "music_state": "loud",
                    "audio_src_start_sec": src_cue,
                    "lyric_en": lyric_en,
                    "lyric_pt": lyric_pt,
                }
            )
            visual_segments.append(
                {
                    "kind": "music_break",
                    "break_index": break_counter,
                    "after_scene": scene.index,
                    "start": brk_start,
                    "end": brk_end,
                    "duration_sec": brk_dur,
                    "audio_src_start_sec": src_cue,
                    "lyric_en": lyric_en,
                    "lyric_pt": lyric_pt,
                    "has_lyric_caption": bool(lyric_pt or lyric_en),
                }
            )
            if lyric_pt or lyric_en:
                break_cues.append(
                    {
                        "break_index": break_counter,
                        "start": brk_start,
                        "end": brk_end,
                        "lyric_pt": lyric_pt,
                        "lyric_en": lyric_en,
                    }
                )
            cursor = brk_end

    finale_start = round(cursor, 3)
    finale_end = round(finale_start + finale_dur, 3)
    timeline.append(
        {
            "kind": "finale",
            "label": "Clímax Musical Final",
            "start": finale_start,
            "end": finale_end,
            "duration_sec": finale_dur,
            "music_state": "loud",
            "audio_src_start_sec": finale_audio_start_sec,
        }
    )
    visual_segments.append(
        {
            "kind": "music_finale",
            "start": finale_start,
            "end": finale_end,
            "duration_sec": finale_dur,
            "audio_src_start_sec": finale_audio_start_sec,
        }
    )

    total_dur = finale_end
    total_samples = max(1, int(round(total_dur * sr)))

    # Assemble dry narration PCM at exact speech_start sample indices
    narr_pcm = np.zeros((total_samples, 2), dtype=np.float32)
    for st_sec, w_path, _ in placements:
        seg_pcm = _read_wav_stereo_float(w_path)
        s0 = max(0, min(total_samples, int(round(st_sec * sr))))
        avail = min(len(seg_pcm), total_samples - s0)
        if avail > 0:
            narr_pcm[s0 : s0 + avail] = seg_pcm[:avail]

    active_mask = np.max(np.abs(narr_pcm), axis=1) > 0.012
    if np.any(active_mask):
        speech_rms = float(np.sqrt(np.mean(narr_pcm[active_mask] ** 2)))
        if speech_rms > 1e-4:
            scale = min(1.8, max(0.55, 0.20 / speech_rms))
            narr_pcm = narr_pcm * scale

    narr_timeline_wav = render_dir / "narration_timeline.wav"
    _write_wav_stereo_float(narr_timeline_wav, narr_pcm, sample_rate=sr)

    ffmpeg = get_ffmpeg_exe()
    clean_wav = render_dir / "audio_clean.wav"
    fade_out_clean = max(0.2, total_dur - 0.15)
    res_clean = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(narr_timeline_wav),
            "-af",
            f"afade=t=in:st=0:d=0.03,afade=t=out:st={fade_out_clean:.2f}:d=0.15,loudnorm=I=-14:TP=-1.5:LRA=11",
            "-ar",
            str(sr),
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(clean_wav),
        ],
        capture_output=True,
        text=True,
    )
    if res_clean.returncode != 0:
        raise RuntimeError(f"Falha ao preparar áudio limpo integrado: {res_clean.stderr[-400:]}")

    has_music = bool(music_source and music_source.exists())
    chosen_start = 0.0
    mixed_wav: Path | None = None
    music_bed_raw_wav: Path | None = None

    if has_music and music_source is not None:
        track_dur = get_audio_duration(music_source)
        full_src_wav = render_dir / "_source_full_tmp.wav"
        res_dec = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(music_source),
                "-vn",
                "-af",
                "loudnorm=I=-15:TP=-1.5:LRA=11",
                "-ar",
                str(sr),
                "-ac",
                "2",
                "-c:a",
                "pcm_s16le",
                str(full_src_wav),
            ],
            capture_output=True,
            text=True,
        )
        if res_dec.returncode != 0:
            raise RuntimeError(f"Falha ao decodificar faixa musical completa: {res_dec.stderr[-400:]}")

        src_pcm = _read_wav_stereo_float(full_src_wav)
        full_src_wav.unlink(missing_ok=True)
        src_n = max(1, len(src_pcm))
        actual_track_dur = src_n / float(sr)
        lead_offset = _find_audible_lead_offset(src_pcm, sr=sr)

        has_targeted_cues = any(
            item.get("audio_src_start_sec") is not None
            for item in timeline
            if item.get("kind") == "break"
        )

        if has_targeted_cues:
            # Lyric-hook mode: start under the hook using track opening riff (past silence),
            # then smoothly crossfade directly onto each target lyric timestamp + finale climax!
            chosen_start = lead_offset if intro_dur < 0.35 else (
                max(0.0, float(music_start_sec)) if music_start_sec is not None else lead_offset
            )
            default_finale_src = (
                float(finale_audio_start_sec) % actual_track_dur
                if finale_audio_start_sec is not None
                else find_best_highlight_start(music_source, clip_duration=max(10.0, finale_dur))
            )
        else:
            chosen_start = (
                max(0.0, float(music_start_sec))
                if music_start_sec is not None
                else find_best_highlight_start(music_source, clip_duration=min(36.0, max(12.0, total_dur * 0.6)))
            )
            if actual_track_dur > 4.0 and chosen_start >= actual_track_dur - 2.0:
                chosen_start = max(0.0, actual_track_dur - 4.0)
            default_finale_src = None

        music_pcm = np.zeros((total_samples, 2), dtype=np.float32)

        if not has_targeted_cues:
            music_pcm = _slice_pcm_wrap(src_pcm, int(round(chosen_start * sr)), total_samples)
            for t_item in timeline:
                t_item["src_start_sec"] = round((chosen_start + float(t_item["start"])) % actual_track_dur, 2)
        else:
            # Cue-targeted assembly with equal-power 260ms cosine crossfade landing right on each vocal cue
            xf_pre = int(0.18 * sr)
            xf_post = int(0.08 * sr)
            track_cursor_smp = int(round(chosen_start * sr)) % src_n

            for t_idx, t_item in enumerate(timeline):
                s0 = max(0, min(total_samples, int(round(float(t_item["start"]) * sr))))
                s1 = max(s0, min(total_samples, int(round(float(t_item["end"]) * sr))))
                count = s1 - s0
                if count <= 0:
                    continue

                req_src = t_item.get("audio_src_start_sec")
                if t_item["kind"] == "finale" and req_src is None:
                    req_src = default_finale_src

                if req_src is not None:
                    target_smp = int(round((float(req_src) % actual_track_dur) * sr))
                    # Avoid pointless splice if running track cursor already lands within 0.55s of target
                    if abs(((target_smp - track_cursor_smp + src_n // 2) % src_n) - src_n // 2) > int(0.55 * sr) and s0 > int(0.25 * sr):
                        new_seg = _slice_pcm_wrap(src_pcm, target_smp, count)
                        x0 = max(0, s0 - xf_pre)
                        x1 = min(s1, s0 + xf_post)
                        xlen = x1 - x0
                        old_cont = (
                            _slice_pcm_wrap(src_pcm, track_cursor_smp - (s0 - x0), xlen)
                            if xlen > 32
                            else None
                        )
                        music_pcm[s0:s1] = new_seg

                        if old_cont is not None and xlen > 32:
                            new_lead = _slice_pcm_wrap(src_pcm, target_smp - (s0 - x0), xlen)
                            theta = np.linspace(0.0, np.pi * 0.5, xlen, dtype=np.float32)[:, None]
                            music_pcm[x0:x1] = old_cont * np.cos(theta) + new_lead * np.sin(theta)

                        t_item["src_start_sec"] = round((target_smp % src_n) / float(sr), 2)
                        track_cursor_smp = (target_smp + count) % src_n
                        continue

                # Sequential continuation from previous song position
                music_pcm[s0:s1] = _slice_pcm_wrap(src_pcm, track_cursor_smp, count)
                t_item["src_start_sec"] = round((track_cursor_smp % src_n) / float(sr), 2)
                track_cursor_smp = (track_cursor_smp + count) % src_n

        # Propagate resolved src_start_sec onto visual_segments for MP4 band/MV clip lip-sync!
        brk_src_by_idx = {int(t["break_index"]): t.get("src_start_sec") for t in timeline if t.get("kind") == "break"}
        fin_src = next((t.get("src_start_sec") for t in timeline if t.get("kind") == "finale"), None)
        sp_src_by_idx = {int(t["scene_index"]): t.get("src_start_sec") for t in timeline if t.get("kind") == "speech"}
        for vseg in visual_segments:
            knd = vseg.get("kind")
            if knd == "music_break":
                vseg["audio_src_start_sec"] = brk_src_by_idx.get(int(vseg["break_index"]))
            elif knd == "music_finale":
                vseg["audio_src_start_sec"] = fin_src
            elif "scene_index" in vseg:
                vseg["audio_src_start_sec"] = sp_src_by_idx.get(int(vseg["scene_index"]))

        music_bed_raw_wav = render_dir / "music_bed_raw.wav"
        _write_wav_stereo_float(music_bed_raw_wav, music_pcm, sample_rate=sr)

        # Build smooth S-curve ducking envelope (no pumping between continuous sentences)
        env = np.full(total_samples, full_level, dtype=np.float32)
        for ist, iv_end, _ in speech_islands:
            r_dn0 = max(0, int(round((ist - 0.28) * sr)))
            r_dn1 = min(total_samples, int(round((ist + 0.04) * sr)))
            if r_dn1 > r_dn0 and ist > 0.08:
                u = np.linspace(0.0, 1.0, r_dn1 - r_dn0, dtype=np.float32)
                ramp_dn = duck_level + (full_level - duck_level) * 0.5 * (1.0 + np.cos(np.pi * u))
                env[r_dn0:r_dn1] = np.minimum(env[r_dn0:r_dn1], ramp_dn)

            hold_start = r_dn1 if ist > 0.08 else max(0, int(round(ist * sr)))
            hold_end = min(total_samples, int(round((iv_end + 0.04) * sr)))
            if hold_end > hold_start:
                env[hold_start:hold_end] = duck_level

            if iv_end < total_dur - 0.25:
                r_up0 = hold_end
                r_up1 = min(total_samples, int(round((iv_end + 0.34) * sr)))
                if r_up1 > r_up0:
                    u = np.linspace(0.0, 1.0, r_up1 - r_up0, dtype=np.float32)
                    ramp_up = duck_level + (full_level - duck_level) * 0.5 * (1.0 - np.cos(np.pi * u))
                    env[r_up0:r_up1] = ramp_up

        premix_pcm = narr_pcm * 0.94 + music_pcm * env[:, None]
        premix_wav = render_dir / "_premix.wav"
        _write_wav_stereo_float(premix_wav, premix_pcm, sample_rate=sr)

        mixed_wav = render_dir / "audio_mixed.wav"
        fade_out_st = max(0.5, total_dur - 1.6)
        res_mix = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(premix_wav),
                "-af",
                f"afade=t=in:st=0:d=0.05,afade=t=out:st={fade_out_st:.2f}:d=1.6,loudnorm=I=-14:TP=-1.5:LRA=11,alimiter=limit=0.95",
                "-ar",
                str(sr),
                "-ac",
                "2",
                "-c:a",
                "pcm_s16le",
                str(mixed_wav),
            ],
            capture_output=True,
            text=True,
        )
        premix_wav.unlink(missing_ok=True)
        if res_mix.returncode != 0:
            raise RuntimeError(f"Falha ao normalizar mix integrado: {res_mix.stderr[-400:]}")

    return {
        "mixed_wav": mixed_wav,
        "clean_wav": clean_wav,
        "music_bed_raw_wav": music_bed_raw_wav,
        "narration_wav": narr_timeline_wav,
        "timeline": timeline,
        "visual_segments": visual_segments,
        "break_cues": break_cues,
        "scene_spans": scene_spans,
        "word_timings": all_words,
        "chosen_start": round(chosen_start, 2),
        "total_dur": total_dur,
    }
