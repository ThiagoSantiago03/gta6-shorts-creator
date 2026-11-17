from __future__ import annotations

import asyncio
import base64
import logging
import re
import subprocess
import time
import wave
from pathlib import Path
from typing import Any

from app.config import get_ffmpeg_exe, get_gemini_api_key, get_tts_model
from app.models import SceneItem, WordTiming

logger = logging.getLogger(__name__)


EDGE_VOICES_PT_BR = [
    {"id": "pt-BR-AntonioNeural", "label": "Antônio (Masculina, grave/narrador)"},
    {"id": "pt-BR-FranciscaNeural", "label": "Francisca (Feminina, clara/natural)"},
    {"id": "pt-BR-ThalitaMultilingualNeural", "label": "Thalita (Feminina, dinâmica)"},
]

GEMINI_TTS_VOICES = [
    {"id": "Charon", "label": "Charon (Informativa / Documentário)"},
    {"id": "Algenib", "label": "Algenib (Grave / Rock)"},
    {"id": "Fenrir", "label": "Fenrir (Energética / Viral)"},
    {"id": "Kore", "label": "Kore (Firme / Editorial)"},
    {"id": "Aoede", "label": "Aoede (Fluida / Leve)"},
    {"id": "Sadaltager", "label": "Sadaltager (Especialista)"},
]


def _wav_duration_seconds(wav_path: Path) -> float:
    with wave.open(str(wav_path), "rb") as wf:
        frames = wf.getnframes()
        rate = wf.getframerate()
        return frames / float(rate or 1)


def _convert_to_standard_wav(src_path: Path, dst_wav_path: Path) -> float:
    ffmpeg = get_ffmpeg_exe()
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(src_path),
        "-ar",
        "44100",
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        str(dst_wav_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Erro ao converter áudio com FFmpeg: {res.stderr[-400:]}")
    return _wav_duration_seconds(dst_wav_path)


def _generate_silence_wav(dst_wav_path: Path, duration_sec: float = 0.20) -> None:
    sample_rate = 44100
    channels = 2
    n_frames = max(1, int(sample_rate * duration_sec))
    with wave.open(str(dst_wav_path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00" * (n_frames * channels * 2))


def _concat_wavs(wav_paths: list[Path], out_wav: Path) -> float:
    with wave.open(str(out_wav), "wb") as out_wf:
        out_wf.setnchannels(2)
        out_wf.setsampwidth(2)
        out_wf.setframerate(44100)
        for p in wav_paths:
            with wave.open(str(p), "rb") as in_wf:
                out_wf.writeframes(in_wf.readframes(in_wf.getnframes()))
    return _wav_duration_seconds(out_wav)


def _proportional_word_timings(text: str, start_offset: float, duration: float) -> list[WordTiming]:
    words = [w for w in re.split(r"\s+", text.strip()) if w]
    if not words:
        return []
    weights = [max(2, len(re.sub(r"[^\w]", "", w))) + (2 if w[-1] in ".!?,;:" else 0) for w in words]
    total_w = float(sum(weights) or len(words))
    # Leave tiny margin at start/end of scene clip
    usable = max(0.2, duration * 0.94)
    cursor = start_offset + duration * 0.03
    timings: list[WordTiming] = []
    for word, w in zip(words, weights):
        dur = usable * (w / total_w)
        timings.append(WordTiming(word=word, start=round(cursor, 3), end=round(cursor + dur, 3)))
        cursor += dur
    return timings


async def _edge_tts_scene(text: str, voice: str, out_mp3: Path, rate: str = "+6%") -> list[WordTiming]:
    import edge_tts

    attempts: list[tuple[str, str, bool]] = [
        (voice, rate, True),
        (voice, rate, True),
        (voice, "+0%", True),
        ("pt-BR-FranciscaNeural" if voice != "pt-BR-FranciscaNeural" else "pt-BR-AntonioNeural", "+0%", False),
    ]
    last_exc: Exception | None = None

    for idx, (cur_voice, cur_rate, use_boundary) in enumerate(attempts):
        if idx > 0:
            await asyncio.sleep(0.6 * idx)
        try:
            kwargs: dict[str, str] = {"voice": cur_voice, "rate": cur_rate}
            if use_boundary:
                kwargs["boundary"] = "WordBoundary"
            try:
                communicate = edge_tts.Communicate(text, **kwargs)
            except TypeError:
                kwargs.pop("boundary", None)
                communicate = edge_tts.Communicate(text, **kwargs)

            audio_buf = bytearray()
            word_timings: list[WordTiming] = []
            async for chunk in communicate.stream():
                ctype = chunk.get("type")
                if ctype == "audio":
                    audio_buf.extend(chunk["data"])
                elif ctype == "WordBoundary":
                    start = float(chunk["offset"]) / 10_000_000.0
                    dur = float(chunk.get("duration", 0)) / 10_000_000.0
                    word_text = str(chunk.get("text", "")).strip()
                    if word_text:
                        word_timings.append(
                            WordTiming(
                                word=word_text,
                                start=start,
                                end=max(start + 0.08, start + dur),
                            )
                        )
            if len(audio_buf) > 512:
                out_mp3.write_bytes(bytes(audio_buf))
                return word_timings
        except Exception as exc:
            last_exc = exc
            continue

    raise RuntimeError(f"edge-tts falhou após {len(attempts)} tentativas: {last_exc}")


def _gemini_tts_scene(text: str, voice: str, out_wav: Path, api_key: str) -> None:
    from google import genai

    client = genai.Client(api_key=api_key)
    interaction = client.interactions.create(
        model=get_tts_model(),
        input=[
            {
                "type": "user_input",
                "content": [
                    {
                        "type": "text",
                        "text": text,
                        "annotations": [
                            {
                                "type": "speech_metadata",
                                "style": "Engaging Brazilian Portuguese documentary storyteller, clear, energetic and expressive",
                            }
                        ],
                    }
                ],
            }
        ],
        response_format={"type": "audio", "mime_type": "audio/wav"},
        generation_config={"speech_config": [{"voice": voice}]},
    )
    if not interaction.output_audio or not interaction.output_audio.data:
        raise RuntimeError("Gemini TTS não retornou áudio.")
    out_wav.write_bytes(base64.b64decode(interaction.output_audio.data))


def synthesize_narration_segments(
    scenes: list[SceneItem],
    work_dir: Path,
    tts_engine: str = "edge_tts",
    voice_name: str = "pt-BR-AntonioNeural",
    api_key_override: str | None = None,
    rate: str = "+6%",
) -> tuple[list[dict[str, Any]], str]:
    """
    Synthesize each non-empty scene into its own 44.1kHz stereo WAV alongside scene-local WordTimings.
    Supports both sequential Reel 1 assembly and interleaved music-break timelines.
    """
    tts_dir = work_dir / "tts"
    tts_dir.mkdir(parents=True, exist_ok=True)

    api_key = get_gemini_api_key(api_key_override)
    use_gemini = tts_engine == "gemini_tts" and bool(api_key)
    actual_engine = "gemini_tts" if use_gemini else "edge_tts"

    # Normalize voice when switching engines
    edge_voice = voice_name if voice_name.startswith("pt-BR-") else "pt-BR-AntonioNeural"
    gemini_voice = voice_name if not voice_name.startswith("pt-BR-") else "Charon"

    segments: list[dict[str, Any]] = []
    synthesized_idx = 0

    for scene in scenes:
        text = scene.narration_text.strip()
        if not text:
            continue

        scene_wav = tts_dir / f"scene_{scene.index:02d}.wav"
        local_words: list[WordTiming] = []

        if use_gemini:
            raw_wav = tts_dir / f"scene_{scene.index:02d}_raw.wav"
            try:
                _gemini_tts_scene(text, gemini_voice, raw_wav, api_key)
                dur = _convert_to_standard_wav(raw_wav, scene_wav)
                local_words = _proportional_word_timings(text, 0.0, dur)
            except Exception:
                # Automatic graceful fallback to edge-tts
                actual_engine = "edge_tts_fallback"
                if synthesized_idx > 0:
                    time.sleep(0.25)
                raw_mp3 = tts_dir / f"scene_{scene.index:02d}.mp3"
                local_words = asyncio.run(_edge_tts_scene(text, edge_voice, raw_mp3, rate=rate))
                dur = _convert_to_standard_wav(raw_mp3, scene_wav)
        else:
            if synthesized_idx > 0:
                time.sleep(0.25)
            raw_mp3 = tts_dir / f"scene_{scene.index:02d}.mp3"
            local_words = asyncio.run(_edge_tts_scene(text, edge_voice, raw_mp3, rate=rate))
            dur = _convert_to_standard_wav(raw_mp3, scene_wav)

        synthesized_idx += 1
        if not local_words:
            local_words = _proportional_word_timings(text, 0.0, dur)

        scene.duration_sec = round(dur, 3)
        segments.append(
            {
                "scene_index": scene.index,
                "scene": scene,
                "wav_path": scene_wav,
                "duration_sec": round(dur, 3),
                "local_words": local_words,
            }
        )

    return segments, actual_engine


def synthesize_narration(
    scenes: list[SceneItem],
    work_dir: Path,
    tts_engine: str = "edge_tts",
    voice_name: str = "pt-BR-AntonioNeural",
    api_key_override: str | None = None,
    rate: str = "+6%",
    max_duration_sec: float = 58.0,
) -> tuple[Path, list[tuple[int, float, float]], list[WordTiming], str]:
    """
    Synthesize each scene separately for exact scene-to-visual synchronization,
    then concatenate into narration_full.wav, strictly enforcing real duration <= max_duration_sec (Tarefa 2.2).
    Returns: (full_wav_path, [(scene_index, start_sec, end_sec), ...], global_word_timings, actual_engine_used)
    """
    tts_dir = work_dir / "tts"
    tts_dir.mkdir(parents=True, exist_ok=True)
    pause_wav = tts_dir / "pause.wav"
    pause_dur = 0.16
    _generate_silence_wav(pause_wav, duration_sec=pause_dur)

    cur_rate = rate or "+6%"
    fallback_rates = ["+12%", "+16%", "+20%", "+24%", "+28%"]
    rate_candidates = [cur_rate] + [r for r in fallback_rates if r != cur_rate]

    segments: list[dict[str, Any]] = []
    actual_engine = tts_engine
    for r in rate_candidates:
        segments, actual_engine = synthesize_narration_segments(
            scenes=scenes,
            work_dir=work_dir,
            tts_engine=tts_engine,
            voice_name=voice_name,
            api_key_override=api_key_override,
            rate=r,
        )
        total_voice = sum(float(seg["duration_sec"]) for seg in segments)
        total_time = total_voice + pause_dur * max(0, len(segments) - 1)
        if total_time <= max_duration_sec:
            break

    # If still exceeding, compact middle sentences proportionally
    total_voice = sum(float(seg["duration_sec"]) for seg in segments)
    total_time = total_voice + pause_dur * max(0, len(segments) - 1)
    if total_time > max_duration_sec:
        from app.gaming_scriptwriter import enforce_script_duration_budget
        sc_dicts = [s.model_dump() for s in scenes]
        total_w = sum(len(s.narration_text.split()) for s in scenes)
        scale_target = max(60, int(total_w * ((max_duration_sec - 1.0) / max(1.0, total_time))))
        trimmed = enforce_script_duration_budget(sc_dicts, max_words=scale_target)
        for orig, td in zip(scenes, trimmed):
            orig.narration_text = str(td.get("narration_text", orig.narration_text))
        segments, actual_engine = synthesize_narration_segments(
            scenes=scenes,
            work_dir=work_dir,
            tts_engine=tts_engine,
            voice_name=voice_name,
            api_key_override=api_key_override,
            rate=rate_candidates[-1],
        )

    wav_parts: list[Path] = []
    scene_spans: list[tuple[int, float, float]] = []
    all_words: list[WordTiming] = []
    cursor = 0.0

    for idx, seg in enumerate(segments):
        dur = float(seg["duration_sec"])
        scene: SceneItem = seg["scene"]
        scene_start = cursor
        scene_end = cursor + dur

        for wt in seg["local_words"]:
            all_words.append(
                WordTiming(
                    word=wt.word,
                    start=round(scene_start + wt.start, 3),
                    end=round(scene_start + wt.end, 3),
                )
            )

        wav_parts.append(Path(seg["wav_path"]))
        if idx < len(segments) - 1:
            wav_parts.append(pause_wav)
            scene_end += pause_dur

        scene.duration_sec = round(scene_end - scene_start, 3)
        scene_spans.append((scene.index, round(scene_start, 3), round(scene_end, 3)))
        cursor = scene_end

    full_wav = tts_dir / "narration_full.wav"
    final_dur = _concat_wavs(wav_parts, full_wav)
    logger.info("Narração concatenada em %s: %.2fs (máx %.1fs, motor: %s).", full_wav, final_dur, max_duration_sec, actual_engine)
    return full_wav, scene_spans, all_words, actual_engine
