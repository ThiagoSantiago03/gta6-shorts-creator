from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
import wave
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app.config import get_ffmpeg_exe
from app.models import MusicStoryProject, WordTiming

logger = logging.getLogger("gta6_qa")


def _load_mono_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        # Windows
        r"C:\Windows\Fonts\consolab.ttf",
        r"C:\Windows\Fonts\consola.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
        # macOS
        "/System/Library/Fonts/SFNSMono.ttf",
        "/System/Library/Fonts/Monaco.dfont",
        "/System/Library/Fonts/Supplemental/Courier New.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                continue
    return ImageFont.load_default()


def write_packed_timeline(
    project: MusicStoryProject,
    scene_spans: list[tuple[int, float, float]] | None = None,
    word_timings: list[WordTiming] | None = None,
    out_path: Path | None = None,
    beat_bpm: float | None = None,
    visual_segments: list[dict[str, Any]] | None = None,
    audio_timeline: list[dict[str, Any]] | None = None,
) -> Path:
    """
    Create compact `project_packed.md` (video-use pattern: 1/10th the tokens of raw JSON)
    listing exact [start-end] timestamps, asset sources, effects, and spoken phrases.
    """
    span_map = {idx: (st, en) for idx, st, en in (scene_spans or [])}
    fin_src_tag = (
        f" | Finale@{project.finale_audio_start_sec:.1f}s"
        if getattr(project, "finale_audio_start_sec", None) is not None
        else ""
    )
    lines = [
        f"# Packed Timeline: {project.song_title} — {project.artist} ({project.year})",
        "",
        f"- Mode: `{project.video_mode}` | Opening: `{getattr(project, 'opening_mode', 'lyric_hook')}` | Overlay: `{getattr(project, 'overlay_style', 'clean_reels')}`",
        f"- Style: `{project.style_preset}` | Subtitle: `{project.subtitle_style}` | Visual Engine: `{project.visual_engine}`",
        f"- Motion Renderer: `{project.reel2_renderer}` (`{project.reel2_visual_style}`) | Beat Sync: `{project.beat_sync}`"
        + (f" ({beat_bpm:.1f} BPM)" if beat_bpm else ""),
    ]
    if project.video_mode == "single_all_in_one":
        lines.append(
            f"- Interleaved Timings: Intro `{project.intro_music_sec}s` | Break `{project.music_break_sec}s` | Clímax Final `{project.finale_music_sec}s`{fin_src_tag}"
            + (f" | Band Clips: {len(project.band_video_clips)}" if project.band_video_clips else "")
        )

    if audio_timeline and visual_segments:
        brk_src_map = {
            int(item["break_index"]): item.get("src_start_sec")
            for item in audio_timeline
            if item.get("kind") == "break" and item.get("break_index") is not None
        }
        fin_src_val = next(
            (item.get("src_start_sec") for item in audio_timeline if item.get("kind") == "finale"),
            None,
        )
        lines.extend(["", "## Linha do Tempo Intercalada (Música + Narração)"])
        for seg in visual_segments:
            st = float(seg.get("start", 0.0))
            en = float(seg.get("end", 0.0))
            dur = float(seg.get("duration_sec", max(0.0, en - st)))
            kind = seg.get("kind", "")
            src = seg.get("media_source", "-")
            if kind == "hook_scene":
                lines.append(f"  [{st:05.2f}-{en:05.2f}] ({dur:.2f}s) INTRO+C1 [{src}] intro={seg.get('intro_embedded_sec', 0):.2f}s")
            elif kind == "music_break":
                bi = int(seg.get("break_index", 1) or 1)
                s_cue = seg.get("audio_src_start_sec")
                if s_cue is None:
                    s_cue = brk_src_map.get(bi)
                cue_tag = f" @{float(s_cue):.2f}s" if s_cue is not None else ""
                lyr = str(seg.get("lyric_pt") or seg.get("lyric_en") or "").strip()
                lyr_tag = f" | letra: {lyr}" if lyr else ""
                lines.append(f"  [{st:05.2f}-{en:05.2f}] ({dur:.2f}s) RESPIRO B{bi}{cue_tag} [{src} | som alto{lyr_tag}]")
            elif kind == "narration_scene":
                lines.append(f"  [{st:05.2f}-{en:05.2f}] ({dur:.2f}s) C{seg.get('scene_index', '?')} [{src} | som baixo]")
            elif kind == "music_finale":
                f_cue = seg.get("audio_src_start_sec")
                if f_cue is None:
                    f_cue = fin_src_val
                fin_at = f" @{float(f_cue):.2f}s" if f_cue is not None else ""
                lines.append(f"  [{st:05.2f}-{en:05.2f}] ({dur:.2f}s) CLÍMAX FINAL{fin_at} [{src} | som cheio]")

    lines.extend(["", "## Cenas / Blocos Narrados"])
    for sc in project.scenes:
        st, en = span_map.get(sc.index, (0.0, sc.duration_sec or 0.0))
        dur = max(0.0, en - st)
        asset_tag = sc.asset_type
        if sc.html_path and Path(sc.html_path).exists():
            asset_tag = f"html:{Path(sc.html_path).name}"
        elif sc.video_path and Path(sc.video_path).exists():
            asset_tag = f"video:{Path(sc.video_path).name}"
        elif sc.image_path:
            asset_tag = f"img:{Path(sc.image_path).name}"
        brk_tag = f" | brk:{sc.music_break_after_sec:.1f}s" if sc.music_break_after_sec >= 1.0 else ""
        if getattr(sc, "music_break_audio_start_sec", None) is not None:
            brk_tag += f"@{float(sc.music_break_audio_start_sec):.1f}s"
        if getattr(sc, "quote_text", "").strip():
            brk_tag += " | quote"
        lines.append(
            f"  [{st:05.2f}-{en:05.2f}] ({dur:.2f}s) C{sc.index} [{asset_tag} | {sc.effect} | intent:{sc.shot_intent or 'beat'}{brk_tag}] {sc.narration_text}"
        )

    if word_timings:
        lines.extend(["", f"## Sincronia de Palavras ({len(word_timings)} tokens)"])
        sample_words = [f"{w.word}@{w.start:.2f}" for w in word_timings[:18]]
        lines.append("  " + " ".join(sample_words) + (" ..." if len(word_timings) > 18 else ""))

    media_list = getattr(project, "used_media", []) or []
    if media_list:
        lines.extend(["", "## Procedência & Licenças das Mídias (Tarefa 1.1)"])
        for m in media_list:
            risk_flag = f" ⚠️ [RISCO: {m.get('risk_level', '').upper()}]" if m.get("risk_level") == "alto" else ""
            lines.append(
                f"- **{m.get('role', 'media').upper()}**: `{m.get('filename')}` | Origem: {m.get('source_url')} | "
                f"Detentor: {m.get('copyright_owner')} | Licença: `{m.get('license_type')}`{risk_flag}"
            )

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def score_project_slideshow_risk(
    project: MusicStoryProject,
    visual_segments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Adapted from OpenMontage `lib/slideshow_risk.py`:
    Score 0..5 (lower is better) across effect repetition, media variety,
    visual/video diversity, and communicative shot intent.
    """
    scenes = project.scenes
    if not scenes:
        return {"average": 5.0, "verdict": "fail", "dimensions": {}}

    effects = Counter(s.effect for s in scenes)
    dominant_eff_ratio = effects.most_common(1)[0][1] / len(scenes)
    rep_score = 3.0 if dominant_eff_ratio > 0.65 else (1.5 if dominant_eff_ratio > 0.4 else 0.4)

    asset_kinds = [s.asset_type for s in scenes]
    has_video_or_html = sum(1 for k in asset_kinds if k in {"video", "html"})
    extra_motion = 0
    if visual_segments:
        for seg in visual_segments:
            if seg.get("kind") in {"music_break", "music_finale"}:
                src = str(seg.get("media_source", ""))
                if src.startswith(("html:", "video:", "montage:")):
                    extra_motion += 1
    total_motion = has_video_or_html + extra_motion
    total_slots = max(len(scenes), len(visual_segments or scenes))
    diversity_score = 0.25 if total_motion >= 3 else (0.6 if total_motion >= 2 else (1.2 if total_motion == 1 else 2.0))

    with_intent = sum(1 for s in scenes if s.shot_intent)
    intent_score = round(max(0.0, (1.0 - with_intent / len(scenes)) * 4.0), 2)

    hook_ok = bool(project.hook_title.strip()) and (
        len((scenes[0].narration_text or "").split()) <= 28
    )
    hook_score = 0.2 if hook_ok else 2.2

    dims = {
        "effect_repetition": {"score": round(rep_score, 2), "detail": f"Dominant effect share {dominant_eff_ratio:.0%}"},
        "motion_media_mix": {"score": round(diversity_score, 2), "detail": f"{total_motion}/{total_slots} video/HTML/montage motion blocks"},
        "shot_intent_coverage": {"score": intent_score, "detail": f"{with_intent}/{len(scenes)} scenes have shot_intent"},
        "hook_punch": {"score": hook_score, "detail": "Strong <28w hook" if hook_ok else "Hook too wordy or empty"},
    }
    avg = round(sum(d["score"] for d in dims.values()) / len(dims), 2)
    verdict = "strong" if avg < 1.6 else ("acceptable" if avg < 2.6 else ("revise" if avg < 3.6 else "fail"))
    return {"average": avg, "verdict": verdict, "dimensions": dims}


def probe_video_and_loudness(video_path: Path, expect_audio: bool = True) -> dict[str, Any]:
    """
    Run FFmpeg metadata inspection + ebur128 integrated loudness / true peak verification
    (never assume audio levels — measure them directly).
    """
    ffmpeg = get_ffmpeg_exe()
    if not video_path.exists():
        return {"exists": False, "passed": False, "issues": ["File missing"]}

    info_proc = subprocess.run([ffmpeg, "-i", str(video_path)], capture_output=True, text=True)
    stderr = info_proc.stderr

    dur = 0.0
    m_dur = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", stderr)
    if m_dur:
        dur = int(m_dur.group(1)) * 3600 + int(m_dur.group(2)) * 60 + float(m_dur.group(3))

    width, height = 0, 0
    m_res = re.search(r"Video:[^\n]*?,\s*(\d{3,4})x(\d{3,4})\b", stderr)
    if m_res:
        width, height = int(m_res.group(1)), int(m_res.group(2))

    has_h264 = "h264" in stderr.lower()
    has_yuv420p = "yuv420p" in stderr.lower() or "yuvj420p" in stderr.lower()
    has_audio = bool(re.search(r"Stream #0:\d+.*Audio:", stderr))

    integrated_lufs: float | None = None
    true_peak_db: float | None = None
    if has_audio:
        eb_proc = subprocess.run(
            [ffmpeg, "-i", str(video_path), "-af", "ebur128=peak=true", "-f", "null", "-"],
            capture_output=True,
            text=True,
        )
        m_i = re.findall(r"\bI:\s*(-?[\d.]+)\s*LUFS", eb_proc.stderr)
        m_pk = re.findall(r"Peak:\s*(-?[\d.]+)\s*dBFS", eb_proc.stderr)
        if m_i:
            integrated_lufs = float(m_i[-1])
        if m_pk:
            true_peak_db = float(m_pk[-1])

    issues: list[str] = []
    if width != 1080 or height != 1920:
        issues.append(f"Unexpected resolution {width}x{height} (expected 1080x1920)")
    if dur < 3.0:
        issues.append(f"Too short ({dur:.2f}s)")
    if dur > 58.05:
        issues.append(f"Exceeds max Shorts duration 58.0s ({dur:.2f}s)")
    if not has_h264 or not has_yuv420p:
        issues.append("Codec/pix_fmt not h264+yuv420p")
    if expect_audio and not has_audio:
        issues.append("Expected audio stream missing")
    if expect_audio and integrated_lufs is not None:
        if integrated_lufs < -24.0:
            issues.append(f"Audio too quiet ({integrated_lufs:.1f} LUFS, target -14 LUFS)")
        elif integrated_lufs > -10.0:
            issues.append(f"Audio too loud ({integrated_lufs:.1f} LUFS, target -14 LUFS)")
        if true_peak_db is not None and true_peak_db > -1.0:
            issues.append(f"True peak exceeds -1.0 dBTP limit (Peak {true_peak_db:.1f} dBTP)")

    return {
        "file": video_path.name,
        "exists": True,
        "size_mb": round(video_path.stat().st_size / (1024 * 1024), 2),
        "duration_sec": round(dur, 2),
        "resolution": f"{width}x{height}",
        "has_audio": has_audio,
        "integrated_lufs": integrated_lufs,
        "true_peak_dbfs": true_peak_db,
        "issues": issues,
        "passed": len(issues) == 0,
    }


def _extract_audio_envelope(video_path: Path, dur: float, samples: int = 1200) -> np.ndarray:
    ffmpeg = get_ffmpeg_exe()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        wav = Path(tf.name)
    try:
        r = subprocess.run(
            [ffmpeg, "-y", "-i", str(video_path), "-vn", "-ac", "1", "-ar", "12000", "-c:a", "pcm_s16le", str(wav)],
            capture_output=True,
        )
        if r.returncode != 0 or not wav.exists() or wav.stat().st_size < 100:
            return np.zeros(samples, dtype=np.float32)
        with wave.open(str(wav), "rb") as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        if pcm.size < samples:
            return np.zeros(samples, dtype=np.float32)
        win = max(1, pcm.size // samples)
        usable = (pcm.size // win) * win
        env = np.sqrt(np.mean(pcm[:usable].reshape(-1, win) ** 2, axis=1))
        if len(env) > samples:
            env = env[:samples]
        elif len(env) < samples:
            env = np.pad(env, (0, samples - len(env)))
        mx = float(env.max())
        return (env / mx) if mx > 0 else env
    finally:
        wav.unlink(missing_ok=True)


def build_timeline_contact_sheet(
    video_path: Path,
    out_png: Path,
    dur: float,
    n_frames: int = 8,
    scene_spans: list[tuple[int, float, float]] | None = None,
    cut_markers: list[tuple[str, float]] | None = None,
    word_timings: list[WordTiming] | None = None,
    title: str = "",
) -> Path:
    """
    Generate a `timeline_view`-style filmstrip + audio waveform + word-marker composite PNG
    (learned from browser-use/video-use `helpers/timeline_view.py`).
    """
    ffmpeg = get_ffmpeg_exe()
    dur = max(1.0, dur)
    n_frames = max(4, min(12, n_frames))

    # Pick evenly spaced timestamps slightly inset from 0 and end
    step = (dur - 0.5) / max(1, n_frames - 1)
    times = [round(0.25 + i * step, 2) for i in range(n_frames)]

    thumbs: list[Image.Image] = []
    with tempfile.TemporaryDirectory(prefix="csheet_") as tmp:
        for i, ts in enumerate(times):
            jpg = Path(tmp) / f"f_{i:02d}.jpg"
            subprocess.run(
                [ffmpeg, "-y", "-ss", f"{ts:.2f}", "-i", str(video_path), "-frames:v", "1", "-vf", "scale=216:384", "-q:v", "3", str(jpg)],
                capture_output=True,
            )
            if jpg.exists():
                with Image.open(jpg) as im:
                    thumbs.append(im.convert("RGB"))
            else:
                thumbs.append(Image.new("RGB", (216, 384), (20, 20, 28)))

    gap = 10
    margin_x = 40
    thumb_w, thumb_h = 216, 384
    strip_w = n_frames * thumb_w + (n_frames - 1) * gap
    canvas_w = strip_w + margin_x * 2
    wave_y = 66 + thumb_h + 36
    wave_h = 150
    canvas_h = wave_y + wave_h + 62

    canvas = Image.new("RGB", (canvas_w, canvas_h), (14, 15, 22))
    draw = ImageDraw.Draw(canvas, "RGBA")
    f_head = _load_mono_font(20)
    f_small = _load_mono_font(13)

    head_txt = title or f"{video_path.name}  |  {dur:.2f}s  |  {n_frames} keyframes (timeline_view)"
    draw.text((margin_x, 20), head_txt, fill=(235, 238, 245), font=f_head)

    # Paste thumbnails with timestamp pills
    for i, (im, ts) in enumerate(zip(thumbs, times)):
        x = margin_x + i * (thumb_w + gap)
        canvas.paste(im, (x, 58))
        draw.rectangle((x + 6, 64, x + 76, 86), fill=(0, 0, 0, 185))
        draw.text((x + 12, 68), f"{ts:05.2f}s", fill=(255, 190, 90), font=f_small)

    # Waveform box
    draw.rectangle((margin_x, wave_y, margin_x + strip_w, wave_y + wave_h), fill=(22, 24, 34))

    def t_to_x(sec: float) -> int:
        return margin_x + int(max(0.0, min(1.0, sec / dur)) * strip_w)

    # Cut vertical markers (supports rich Intro/Break/Finale labels when cut_markers provided)
    markers = cut_markers if cut_markers is not None else [(f"C{sc_idx}", st) for sc_idx, st, _ in (scene_spans or [])]
    for label, st in markers:
        cx = t_to_x(st)
        is_music = label.startswith(("B", "FIN", "INT"))
        line_col = (250, 204, 21, 165) if is_music else (255, 90, 60, 140)
        lbl_col = (253, 224, 71) if is_music else (255, 130, 95)
        draw.line((cx, wave_y, cx, wave_y + wave_h), fill=line_col, width=2)
        draw.text((cx + 3, wave_y + (wave_h - 18 if is_music else 4)), label, fill=lbl_col, font=f_small)

    # Audio RMS waveform
    env = _extract_audio_envelope(video_path, dur, samples=max(400, strip_w // 2))
    mid_y = wave_y + wave_h // 2
    max_amp = wave_h // 2 - 12
    pts_top: list[tuple[int, int]] = []
    pts_bot: list[tuple[int, int]] = []
    for idx, val in enumerate(env):
        px = margin_x + int((idx / max(1, len(env) - 1)) * strip_w)
        amp = int(float(val) * max_amp)
        pts_top.append((px, mid_y - amp))
        pts_bot.append((px, mid_y + amp))
    if pts_top:
        poly = pts_top + list(reversed(pts_bot))
        draw.polygon(poly, fill=(100, 175, 255, 95))
        draw.line(pts_top, fill=(135, 200, 255, 220), width=1)
        draw.line(pts_bot, fill=(135, 200, 255, 220), width=1)

    # Spoken word labels along the top edge of waveform
    if word_timings:
        last_x = -999
        for w in word_timings:
            wx = t_to_x(w.start)
            if wx - last_x < 52:
                continue
            draw.line((wx, wave_y - 4, wx, wave_y + 2), fill=(180, 190, 210), width=1)
            draw.text((wx + 2, wave_y - 18), w.word[:12], fill=(225, 228, 236), font=f_small)
            last_x = wx

    # Time ruler
    ry = wave_y + wave_h + 6
    for i in range(7):
        frac = i / 6.0
        tx = margin_x + int(frac * strip_w)
        draw.line((tx, ry - 4, tx, ry + 4), fill=(130, 136, 150), width=1)
        draw.text((max(margin_x, tx - 18), ry + 8), f"{frac * dur:.1f}s", fill=(160, 168, 185), font=f_small)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_png, format="PNG", optimize=True)
    return out_png


def run_project_qa(
    project: MusicStoryProject,
    work_dir: Path,
    out_dir: Path,
    scene_spans: list[tuple[int, float, float]] | None = None,
    word_timings: list[WordTiming] | None = None,
    beat_bpm: float | None = None,
    visual_segments: list[dict[str, Any]] | None = None,
    audio_timeline: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Full OpenMontage/video-use self-evaluation suite: packed MD + slideshow risk + probe/ebur128 + contact sheets."""
    verify_dir = work_dir / "verify"
    verify_dir.mkdir(parents=True, exist_ok=True)

    packed_path = write_packed_timeline(
        project=project,
        scene_spans=scene_spans,
        word_timings=word_timings,
        out_path=work_dir / "project_packed.md",
        beat_bpm=beat_bpm,
        visual_segments=visual_segments,
        audio_timeline=audio_timeline,
    )
    risk = score_project_slideshow_risk(project, visual_segments=visual_segments)

    probes: dict[str, Any] = {}
    contact_sheets: dict[str, str] = {}

    # 0. Gaming YouTube Shorts verification (with strict <= max_duration_sec enforcement)
    yt_str = project.outputs.get("youtube_short")
    yt_primary = Path(yt_str) if yt_str else None
    if yt_primary and yt_primary.is_file():
        p_yt = probe_video_and_loudness(yt_primary, expect_audio=True)
        max_cap = float(getattr(project, "max_duration_sec", 58.0) or 58.0)
        if p_yt.get("duration_sec", 0.0) > max_cap + 0.05:
            p_yt["issues"].append(
                f"Ultrapassa o teto de Shorts ({p_yt['duration_sec']}s > {max_cap:.1f}s)"
            )
            p_yt["passed"] = False
        probes["youtube_short"] = p_yt
        cs_yt = out_dir / "contact_sheet_youtube_short.png"
        build_timeline_contact_sheet(
            yt_primary,
            cs_yt,
            dur=p_yt["duration_sec"] or 48.0,
            n_frames=10,
            scene_spans=scene_spans,
            word_timings=word_timings,
            title=f"QA YouTube Short ({p_yt.get('duration_sec')}s / máx {max_cap:.0f}s) — {project.song_title} | {p_yt.get('integrated_lufs')} LUFS",
        )
        contact_sheets["contact_sheet_youtube_short"] = str(cs_yt)

    # 1. Single All-in-One video verification
    all_str = project.outputs.get("reel_completo_with_music") or project.outputs.get("reel_completo_clean")
    all_primary = Path(all_str) if all_str else None
    if all_primary and all_primary.is_file():
        p_all = probe_video_and_loudness(all_primary, expect_audio=True)
        probes["reel_completo"] = p_all
        cut_markers: list[tuple[str, float]] = []
        if audio_timeline:
            for item in audio_timeline:
                k = item.get("kind")
                st = float(item.get("start", 0.0))
                if k == "intro":
                    cut_markers.append(("INT", st))
                elif k == "speech":
                    cut_markers.append((f"C{item.get('scene_index', '?')}", st))
                elif k == "break":
                    cut_markers.append((f"B{item.get('break_index', '?')}", st))
                elif k == "finale":
                    cut_markers.append(("FIN", st))
        cs_all = out_dir / "contact_sheet_reel_completo.png"
        build_timeline_contact_sheet(
            all_primary,
            cs_all,
            dur=p_all["duration_sec"] or 45.0,
            n_frames=10,
            scene_spans=scene_spans,
            cut_markers=cut_markers or None,
            word_timings=word_timings,
            title=f"QA Contact Sheet Vídeo Único — {project.song_title} ({project.artist}) | {p_all.get('integrated_lufs')} LUFS",
        )
        contact_sheets["contact_sheet_reel_completo"] = str(cs_all)

    # 2. Classic Two-Reels Combo verification
    r1_str = project.outputs.get("reel1_with_music") or project.outputs.get("reel1_clean")
    r1_primary = Path(r1_str) if r1_str else None
    if r1_primary and r1_primary.is_file():
        p1 = probe_video_and_loudness(r1_primary, expect_audio=True)
        probes["reel1"] = p1
        cs1 = out_dir / "contact_sheet_reel1.png"
        build_timeline_contact_sheet(
            r1_primary,
            cs1,
            dur=p1["duration_sec"] or 40.0,
            n_frames=8,
            scene_spans=scene_spans,
            word_timings=word_timings,
            title=f"QA Contact Sheet Reel 1 — {project.song_title} ({project.artist}) | {p1.get('integrated_lufs')} LUFS",
        )
        contact_sheets["contact_sheet_reel1"] = str(cs1)

    r2_str = project.outputs.get("reel2_with_music") or project.outputs.get("reel2_clean_instagram")
    r2_primary = Path(r2_str) if r2_str else None
    r2_has_audio = bool(project.outputs.get("reel2_with_music"))
    if r2_primary and r2_primary.is_file():
        p2 = probe_video_and_loudness(r2_primary, expect_audio=r2_has_audio)
        probes["reel2"] = p2
        cs2 = out_dir / "contact_sheet_reel2.png"
        build_timeline_contact_sheet(
            r2_primary,
            cs2,
            dur=p2["duration_sec"] or 30.0,
            n_frames=6,
            scene_spans=None,
            word_timings=None,
            title=f"QA Contact Sheet Reel 2 — {project.song_title} ({project.reel2_renderer}) | {p2.get('integrated_lufs') or 'silent IG'}",
        )
        contact_sheets["contact_sheet_reel2"] = str(cs2)

    all_passed = all(v.get("passed", False) for v in probes.values()) if probes else False

    # Media Licensing & Provenance Check (Tarefa 1.1 / M-01 / C-07)
    media_list = getattr(project, "used_media", []) or []
    if not media_list:
        try:
            from app.gaming_media import record_project_used_media
            media_list = record_project_used_media(project)
        except Exception as exc:
            logger.warning("Falha ao levantar mídias usadas no QA: %s", exc)
            media_list = []

    publish_intent = getattr(project, "publish_intent", "commercial") or "commercial"
    media_warnings: list[str] = []
    blocking_reasons: list[str] = []
    high_risk_media = 0
    unverified_media = 0

    for med in media_list:
        fn = med.get("filename", "")
        role = med.get("role", "")
        lic = med.get("license_type", "desconhecido")
        risk_lvl = med.get("risk_level", "alto")
        auth_status = med.get("authorization_status", "unknown")
        comm_allowed = med.get("commercial_use_allowed", False)

        if lic in ("desconhecido", "royalty_free_unverified"):
            unverified_media += 1
            media_warnings.append(
                f"Mídia '{fn}' ({role}) sem licença formal verificada ({lic})."
            )
        if risk_lvl == "alto":
            high_risk_media += 1

        # M-01 Gate de Direitos
        if publish_intent == "commercial":
            if not comm_allowed or auth_status in ("unknown", "restricted", "review_required", "non_commercial_only"):
                blocking_reasons.append(
                    f"Mídia '{fn}' ({role}) bloqueada para uso comercial (status: {auth_status}, licença: {lic})."
                )
        elif publish_intent == "editorial":
            if auth_status in ("unknown", "restricted"):
                blocking_reasons.append(
                    f"Mídia '{fn}' ({role}) desconhecida/restrita bloqueada para publicação."
                )

    is_blocked = len(blocking_reasons) > 0
    media_status = "blocked" if is_blocked else ("warning" if media_warnings else "ok")

    # Tarefa 1.2 — Verificação de Uso Transformativo dos Trailers
    max_trailer_sec = float(getattr(project, "max_continuous_trailer_sec", 6.0) or 6.0)
    transformative_trailer_issues: list[str] = []

    trailer_cuts_used = set()
    total_video_scenes = 0

    for sc in getattr(project, "scenes", []):
        vp = getattr(sc, "video_path", None)
        if vp and Path(vp).exists() and getattr(sc, "asset_type", "auto") == "video":
            total_video_scenes += 1
            vstart = float(getattr(sc, "video_start_sec", 0.0) or 0.0)
            vend = float(getattr(sc, "video_end_sec", 0.0) or 0.0)

            trailer_cuts_used.add(f"{Path(vp).name}@{vstart:.1f}")

            if vend > vstart and (vend - vstart) > max_trailer_sec + 0.15:
                transformative_trailer_issues.append(
                    f"Cena {sc.index}: trecho contínuo de trailer excede {max_trailer_sec:.1f}s ({vend - vstart:.1f}s)."
                )

    if total_video_scenes >= 2 and len(trailer_cuts_used) == 1:
        transformative_trailer_issues.append(
            "Uso não-transformativo: o mesmo trecho de trailer é usado como fundo contínuo sem cortes entre as cenas."
        )

    if transformative_trailer_issues and "youtube_short" in probes:
        probes["youtube_short"]["issues"].extend(transformative_trailer_issues)
        probes["youtube_short"]["passed"] = False

    media_audit = {
        "status": media_status,
        "publish_intent": publish_intent,
        "total_media_used": len(media_list),
        "unverified_count": unverified_media,
        "high_risk_count": high_risk_media,
        "media_items": media_list,
        "warnings": media_warnings,
        "blocking_reasons": blocking_reasons,
    }

    report = {
        "qa_passed": all_passed and risk.get("verdict") != "fail" and not is_blocked and not transformative_trailer_issues,
        "slideshow_risk": risk,
        "transformative_trailer_issues": transformative_trailer_issues,
        "probes": probes,
        "media_audit": media_audit,
        "packed_timeline_path": str(packed_path),
        "contact_sheets": contact_sheets,
    }
    (verify_dir / "qa_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
