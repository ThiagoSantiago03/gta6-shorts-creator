from __future__ import annotations

import logging
import math
import random
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger("gta6_renderer")

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app.audio_processor import (
    analyze_beats_and_onsets,
    assemble_interleaved_audio,
    compute_audio_energy_envelope,
    find_best_highlight_start,
    generate_ambient_synthwave_bed,
    get_audio_duration,
    mix_narration_with_music,
    pad_narration_only,
    prepare_reel2_music_clip,
    snap_durations_to_beats,
)
from app.config import (
    SAFE_ZONE_BOTTOM_PX,
    SAFE_ZONE_TOP_PX,
    find_headless_browser,
    get_ffmpeg_exe,
    get_gemini_api_key,
)
from app.gaming_media import auto_select_gaming_soundtrack, prepare_gaming_project_visuals
from app.gaming_scriptwriter import enforce_script_duration_budget
from app.models import MusicStoryProject, SceneItem, WordTiming
from app.qa_verifier import run_project_qa
from app.subtitle_engine import generate_ass_subtitles
from app.thumbnail_engine import generate_project_thumbnail
from app.tts_engine import synthesize_narration, synthesize_narration_segments
from app.visual_assets import (
    fetch_artist_live_gallery,
    generate_reel2_centerpiece,
    prepare_project_visuals,
)


def _legacy_html_renderer():
    """Importação sob demanda do renderizador HTML legado (T13)."""
    from app.legacy import html_renderer

    return html_renderer


def generate_allinone_finale_html(*args, **kwargs):
    return _legacy_html_renderer().generate_allinone_finale_html(*args, **kwargs)


def generate_music_break_html(*args, **kwargs):
    return _legacy_html_renderer().generate_music_break_html(*args, **kwargs)


def generate_reel1_hook_html(*args, **kwargs):
    return _legacy_html_renderer().generate_reel1_hook_html(*args, **kwargs)


def generate_reel2_visualizer_html(*args, **kwargs):
    return _legacy_html_renderer().generate_reel2_visualizer_html(*args, **kwargs)


def render_html_film_to_mp4(*args, **kwargs):
    return _legacy_html_renderer().render_html_film_to_mp4(*args, **kwargs)


def render_motion_overlay_mov(*args, **kwargs):
    """Importação sob demanda do renderizador Motion Design legado (T13)."""
    from app.legacy.motion_design import render_motion_overlay_mov as _fn

    return _fn(*args, **kwargs)


W, H = 1080, 1920
FPS = 30


def _hex_to_rgb(hex_color: str, default: tuple[int, int, int] = (255, 59, 48)) -> tuple[int, int, int]:
    cleaned = hex_color.strip().lstrip("#")
    if len(cleaned) == 6:
        try:
            return (int(cleaned[0:2], 16), int(cleaned[2:4], 16), int(cleaned[4:6], 16))
        except ValueError as exc:
            logger.debug("Cor hex inválida '%s', usando padrão: %s", hex_color, exc)
    return default


def _load_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        # Windows
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\impact.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        # macOS
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except Exception as exc:
                logger.debug("Fonte indisponível (%s): %s", path, exc)
                continue
    return ImageFont.load_default()


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur: list[str] = []
    for w in words:
        trial = " ".join(cur + [w])
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] - bbox[0] <= max_width or not cur:
            cur.append(w)
        else:
            lines.append(" ".join(cur))
            cur = [w]
    if cur:
        lines.append(" ".join(cur))
    return lines


def _build_reel1_overlay(project: MusicStoryProject, out_png: Path) -> Path:
    """
    Transparent 1080x1920 overlay:
    - `clean_reels` (default): soft top/bottom atmospheric gradients only (chapter hook badge
      lives natively in Scene 1 HTML opener; Scenes 2..5, Breaks & Finale stay 100% uncluttered!).
    - `banner_top` (legacy): keeps persistent accent pill + headline at top.
    """
    arr = np.zeros((H, W, 4), dtype=np.uint8)

    y = np.arange(H, dtype=np.float32)[:, None]
    is_clean = getattr(project, "overlay_style", "clean_reels") == "clean_reels"
    top_h = 310.0 if is_clean else 400.0
    top_max = 140.0 if is_clean else 175.0
    bot_max = 195.0 if is_clean else 205.0
    top_alpha = np.clip((top_h - y) / top_h, 0.0, 1.0) ** 1.6 * top_max
    bot_alpha = np.clip((y - (H - 620.0)) / 620.0, 0.0, 1.0) ** 1.4 * bot_max
    arr[:, :, 3] = np.clip(np.maximum(top_alpha, bot_alpha), 0, 230).astype(np.uint8)
    overlay = Image.fromarray(arr, mode="RGBA")

    if not is_clean:
        draw = ImageDraw.Draw(overlay)
        accent = _hex_to_rgb(project.accent_color)

        tag_font = _load_font(26, bold=True)
        hook_font = _load_font(38, bold=True)

        badge_prefix = "RADAR" if getattr(project, "niche", "music_history") == "gaming_news" else "HISTÓRIA DA MÚSICA"
        badge_text = f"{badge_prefix} • {project.artist.upper()}"
        tb = draw.textbbox((0, 0), badge_text, font=tag_font)
        bw = (tb[2] - tb[0]) + 48
        bx0 = (W - bw) // 2
        by0 = 148
        draw.rounded_rectangle(
            (bx0, by0, bx0 + bw, by0 + 48),
            radius=24,
            fill=(accent[0], accent[1], accent[2], 235),
        )
        draw.text((W // 2, by0 + 24), badge_text, fill=(255, 255, 255, 255), font=tag_font, anchor="mm")

        hook_txt = (project.hook_title or f"A HISTÓRIA DE {project.song_title}").upper()
        lines = _wrap_text(draw, hook_txt, hook_font, max_width=900)[:2]
        hy = by0 + 82
        for line in lines:
            draw.text((W // 2 + 2, hy + 2), line, fill=(0, 0, 0, 210), font=hook_font, anchor="mm")
            draw.text((W // 2, hy), line, fill=(255, 255, 255, 255), font=hook_font, anchor="mm")
            hy += 46

    overlay.save(out_png, format="PNG")
    return out_png


def _build_quote_overlay(
    quote_text: str,
    artist: str,
    out_png: Path,
    accent_color: str = "#FF3B30",
    y_top: int = 215,
) -> Path:
    """
    Editorial magazine / interview quote card PNG (transparent 1080x1920):
    Rendered safely in upper-mid zone (y=215..480) well above subtitles (y=1350+)
    for documentary storytelling contrast inspired by `videoplayback (1).mp4`.
    """
    accent = _hex_to_rgb(accent_color)
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    q_font = _load_font(32, bold=True)
    attr_font = _load_font(22, bold=True)
    mark_font = _load_font(62, bold=True)

    clean_q = quote_text.strip().strip('“”"')
    lines = _wrap_text(draw, clean_q, q_font, max_width=770)[:4]
    if not lines:
        canvas.save(out_png, format="PNG")
        return out_png

    card_w = 880
    cx0 = (W - card_w) // 2
    line_h = 42
    card_h = 46 + len(lines) * line_h + 52
    cy0 = y_top

    draw.rounded_rectangle(
        (cx0, cy0, cx0 + card_w, cy0 + card_h),
        radius=22,
        fill=(10, 12, 18, 208),
        outline=(255, 255, 255, 38),
        width=2,
    )
    # Left accent bar
    draw.rounded_rectangle(
        (cx0 + 16, cy0 + 22, cx0 + 24, cy0 + card_h - 22),
        radius=4,
        fill=(accent[0], accent[1], accent[2], 240),
    )
    draw.text((cx0 + 44, cy0 + 26), "“", fill=(accent[0], accent[1], accent[2], 230), font=mark_font, anchor="lt")

    ty = cy0 + 42
    for ln in lines:
        draw.text((cx0 + 96, ty), ln, fill=(248, 250, 252, 255), font=q_font, anchor="lt")
        ty += line_h

    if artist.strip():
        draw.text(
            (cx0 + card_w - 32, cy0 + card_h - 24),
            f"— {artist.upper()} (ARQUIVO)",
            fill=(accent[0], accent[1], accent[2], 235),
            font=attr_font,
            anchor="rb",
        )

    out_png.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_png, format="PNG")
    return out_png


def _render_scene_video_clip(
    video_path: Path,
    duration_sec: float,
    effect: str,
    out_mp4: Path,
    accent_color: str,
    seek_sec: float = 0.0,
    hud_overlay_png: Path | None = None,
    framing_mode: str = "crop_center",
    focus_x: float = 0.5,
) -> Path:
    """
    Format an Archive.org / band / stock / trailer MP4 clip to crisp 1080x1920@30fps supporting:
    - crop_center: 9:16 full-bleed fill with center crop
    - crop_focus: 9:16 full-bleed fill with horizontal focus_x point (0.0=left, 1.0=right)
    - blur_fill: atmospheric blurred backdrop + sharp 16:9 framed center
    """
    ffmpeg = get_ffmpeg_exe()
    r, g, b = _hex_to_rgb(accent_color)
    grade = "eq=contrast=1.06:saturation=0.92"
    if effect == "ken_burns_duotone":
        rm, gm, bm = max(0.38, r / 255.0), max(0.32, g / 255.0), max(0.38, b / 255.0)
        grade = f"hue=s=0.38,colorchannelmixer=rr={rm:.2f}:gg={gm:.2f}:bb={bm:.2f},eq=contrast=1.10"
    elif effect == "vintage_grain":
        grade = "eq=contrast=1.08:saturation=0.75,noise=alls=8:allf=t"

    vid_dur = get_audio_duration(video_path)
    safe_seek = 0.0
    use_loop = True
    if seek_sec > 0.2 and vid_dur > duration_sec + 0.8:
        safe_seek = seek_sec % max(0.5, vid_dur - duration_sec - 0.3)
        use_loop = False

    has_hud = bool(hud_overlay_png and hud_overlay_png.exists())
    mode = framing_mode or "crop_center"

    if mode == "blur_fill":
        if has_hud:
            filter_complex = (
                f"[0:v]fps={FPS},split=2[bg_src][fg_src];"
                f"[bg_src]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"gblur=sigma=26,eq=brightness=-0.18:saturation=0.7[bg];"
                f"[fg_src]scale={W}:-2:force_original_aspect_ratio=decrease,{grade}[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2-30[comp];"
                f"[comp][1:v]overlay=0:0,format=yuv420p[v]"
            )
        else:
            filter_complex = (
                f"[0:v]fps={FPS},split=2[bg_src][fg_src];"
                f"[bg_src]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"gblur=sigma=26,eq=brightness=-0.18:saturation=0.7[bg];"
                f"[fg_src]scale={W}:-2:force_original_aspect_ratio=decrease,{grade}[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2-30,format=yuv420p[v]"
            )
    elif mode == "crop_focus":
        fx = max(0.0, min(1.0, float(focus_x if focus_x is not None else 0.5)))
        x_expr = f"(in_w-{W})*{fx:.3f}"
        if has_hud:
            filter_complex = (
                f"[0:v]fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H}:{x_expr}:(in_h-{H})/2,{grade}[base];"
                f"[base][1:v]overlay=0:0,format=yuv420p[v]"
            )
        else:
            filter_complex = (
                f"[0:v]fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H}:{x_expr}:(in_h-{H})/2,{grade},format=yuv420p[v]"
            )
    else:  # "crop_center" default
        if has_hud:
            filter_complex = (
                f"[0:v]fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H}:(in_w-{W})/2:(in_h-{H})/2,{grade}[base];"
                f"[base][1:v]overlay=0:0,format=yuv420p[v]"
            )
        else:
            filter_complex = (
                f"[0:v]fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H}:(in_w-{W})/2:(in_h-{H})/2,{grade},format=yuv420p[v]"
            )

    cmd = [ffmpeg, "-y"]
    if use_loop:
        cmd.extend(["-stream_loop", "-1", "-i", str(video_path)])
    else:
        cmd.extend(["-ss", f"{safe_seek:.2f}", "-i", str(video_path)])
    if has_hud and hud_overlay_png is not None:
        cmd.extend(["-i", str(hud_overlay_png)])

    cmd.extend([
        "-t",
        f"{duration_sec:.3f}",
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-an",
        str(out_mp4),
    ])
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Erro ao processar clipe de arquivo {video_path.name}: {res.stderr[-400:]}")
    return out_mp4


def _build_news_evidence_card_png(
    overlay_img_path: Path,
    label: str,
    accent_color: str,
    out_png: Path,
    card_w: int = 620,
    max_img_h: int = 440,
    layout_mode: str = "pip_card",
) -> tuple[Path, int, int]:
    """
    Build a crisp rounded glass/neon evidence photo card PNG (transparent margin + subtle drop shadow)
    for PiP floating cards, dual split-screen top frames, or spotlight B-roll cutaways.
    """
    from PIL import ImageFilter

    accent = _hex_to_rgb(accent_color, default=(255, 46, 147))
    cyan_vice = (0, 229, 255)
    pad_outer = 18
    pad_inner = 10
    header_h = 34 if (label or "").strip() else 0

    with Image.open(overlay_img_path) as im:
        src = im.convert("RGB")
        sw, sh = src.size
        avail_w = max(240, card_w - pad_inner * 2)
        avail_h = max(160, max_img_h)
        scale = min(avail_w / max(1, sw), avail_h / max(1, sh))
        iw = max(220, int(round(sw * scale)))
        ih = max(140, int(round(sh * scale)))
        if (iw, ih) != (sw, sh):
            src = src.resize((iw, ih), Image.Resampling.LANCZOS)

    inner_card_w = iw + pad_inner * 2
    inner_card_h = ih + pad_inner * 2 + (header_h + 6 if header_h else 0)
    total_w = inner_card_w + pad_outer * 2
    total_h = inner_card_h + pad_outer * 2

    # Soft shadow layer
    shadow = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(shadow)
    s_draw.rounded_rectangle(
        (pad_outer, pad_outer + 6, pad_outer + inner_card_w, pad_outer + inner_card_h + 6),
        radius=24,
        fill=(0, 0, 0, 175),
    )
    canvas = shadow.filter(ImageFilter.GaussianBlur(radius=9))
    draw = ImageDraw.Draw(canvas)

    bx0, by0 = pad_outer, pad_outer
    bx1, by1 = bx0 + inner_card_w, by0 + inner_card_h
    border_col = cyan_vice if layout_mode == "split_top_image" else accent

    draw.rounded_rectangle(
        (bx0, by0, bx1, by1),
        radius=22,
        fill=(10, 12, 22, 238),
        outline=(border_col[0], border_col[1], border_col[2], 235),
        width=3,
    )

    img_x = bx0 + pad_inner
    img_y = by0 + pad_inner + (header_h + 4 if header_h else 0)

    # Round mask for photo
    photo_rgba = src.convert("RGBA")
    mask = Image.new("L", (iw, ih), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, iw, ih), radius=15, fill=255)
    photo_rgba.putalpha(mask)
    canvas.alpha_composite(photo_rgba, (img_x, img_y))

    if header_h and (label or "").strip():
        lbl_txt = (label or "").strip()[:30].upper()
        f_lbl = _load_font(19, bold=True)
        draw.rounded_rectangle(
            (img_x, by0 + 8, bx1 - pad_inner, by0 + 8 + header_h - 2),
            radius=10,
            fill=(20, 22, 36, 220),
        )
        # Small live neon dot
        draw.ellipse(
            (img_x + 12, by0 + 18, img_x + 22, by0 + 28),
            fill=(accent[0], accent[1], accent[2], 255),
        )
        draw.text(
            (img_x + 30, by0 + 23),
            lbl_txt,
            fill=(248, 250, 252, 250),
            font=f_lbl,
            anchor="lm",
        )

    out_png.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_png, format="PNG")
    return out_png, total_w, total_h


def _apply_motion_overlay_to_mp4(
    base_mp4: Path,
    overlay_img_path: Path,
    scene: SceneItem,
    project: MusicStoryProject | None,
    duration_sec: float,
    out_mp4: Path,
    accent_color: str,
    render_work_dir: Path,
    layout_mode: str = "pip_card",
) -> tuple[bool, str]:
    """
    Layer a deterministic Chrome-rendered Motion Design 2.0 + Mockup RGBA overlay `.mov`
    on top of any existing `1080x1920` base scene clip (`base_mp4`).
    """
    if (
        getattr(scene, "mockup_style", "auto") == "none"
        or getattr(scene, "motion_preset", "auto") == "basic_fade"
        or not find_headless_browser()
    ):
        return False, ""

    t_in = max(0.0, min(max(0.0, duration_sec - 0.7), float(getattr(scene, "overlay_start_sec", 0.35) or 0.0)))
    req_dur = float(getattr(scene, "overlay_duration_sec", 0.0) or 0.0)
    if layout_mode == "broll_cutaway" and req_dur <= 0.1:
        req_dur = max(1.5, round(duration_sec * 0.52, 2))
    t_out = min(duration_sec, t_in + req_dur) if req_dur > 0.15 else duration_sec
    if t_out < min(duration_sec, t_in + 0.65):
        t_out = min(duration_sec, t_in + 0.65)
    ov_dur = max(0.75, t_out - t_in)
    hide_at_end = t_out < (duration_sec - 0.15)

    motion_mov = render_work_dir / f"_motion_s{scene.index:02d}.mov"
    try:
        _, resolved_mockup, resolved_motion, stage_y = render_motion_overlay_mov(
            overlay_img_path=overlay_img_path,
            scene=scene,
            project=project,
            out_mov=motion_mov,
            work_dir=render_work_dir,
            duration_sec=ov_dur,
            layout_mode=layout_mode,
            accent_color=accent_color,
            fps=FPS,
            hide_at_end=hide_at_end,
        )
    except Exception:
        motion_mov.unlink(missing_ok=True)
        return False, ""

    ffmpeg = get_ffmpeg_exe()
    enable_expr = f"between(t\\,{t_in:.2f}\\,{t_out:.2f})"
    fc = (
        f"[1:v]fps={FPS},setpts=PTS-STARTPTS+{t_in:.3f}/TB[card];"
        f"[0:v][card]overlay=0:{stage_y}:enable='{enable_expr}':eof_action=pass,format=yuv420p[v]"
    )
    res = subprocess.run(
        [
            ffmpeg, "-y",
            "-i", str(base_mp4),
            "-i", str(motion_mov),
            "-filter_complex", fc,
            "-map", "[v]",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "18",
            "-an",
            str(out_mp4),
        ],
        capture_output=True,
        text=True,
    )
    motion_mov.unlink(missing_ok=True)
    if res.returncode != 0:
        return False, ""
    return True, f"{resolved_mockup}/{resolved_motion}"


def _render_blended_scene_clip(
    video_path: Path,
    overlay_img_path: Path,
    scene: SceneItem,
    duration_sec: float,
    out_mp4: Path,
    accent_color: str,
    render_work_dir: Path,
    project: MusicStoryProject | None = None,
) -> tuple[Path, str]:
    """
    Composite a moving MP4 clip WITH an illustrative news/AI image overlay using:
    1. Motion Design 2.0 + Realistic Mockups (`app/motion_design.py` Chrome RGBA `.mov`)
    2. Graceful static PIL card fallback when `basic_fade` / `none` or headless browser unavailable.
    Returns `(out_mp4, resolved_layout)`.
    """
    ffmpeg = get_ffmpeg_exe()
    r, g, b = _hex_to_rgb(accent_color)
    effect = scene.effect
    grade = "eq=contrast=1.06:saturation=0.94"
    if effect == "ken_burns_duotone":
        rm, gm, bm = max(0.38, r / 255.0), max(0.32, g / 255.0), max(0.38, b / 255.0)
        grade = f"hue=s=0.38,colorchannelmixer=rr={rm:.2f}:gg={gm:.2f}:bb={bm:.2f},eq=contrast=1.10"
    elif effect == "vintage_grain":
        grade = "eq=contrast=1.08:saturation=0.75,noise=alls=8:allf=t"

    layout = getattr(scene, "visual_layout", "auto") or "auto"
    if layout == "auto":
        cycle = ["pip_card", "split_top_image", "broll_cutaway"]
        layout = cycle[max(0, int(scene.index) - 2) % len(cycle)]

    scale_pct = max(0.28, min(0.88, float(getattr(scene, "overlay_scale", 0.56) or 0.56)))
    t_in = max(0.0, min(max(0.0, duration_sec - 0.7), float(getattr(scene, "overlay_start_sec", 0.35) or 0.0)))
    req_dur = float(getattr(scene, "overlay_duration_sec", 0.0) or 0.0)
    if layout == "broll_cutaway" and req_dur <= 0.1:
        req_dur = max(1.5, round(duration_sec * 0.52, 2))
    t_out = min(duration_sec, t_in + req_dur) if req_dur > 0.15 else duration_sec
    if t_out < min(duration_sec, t_in + 0.55):
        t_out = min(duration_sec, t_in + 0.55)

    pos = getattr(scene, "overlay_pos", "top_center") or "top_center"
    label_txt = (getattr(scene, "overlay_label", "") or scene.title or "").strip()

    if layout == "split_top_image":
        clip_y_expr = "655"
    elif layout == "broll_cutaway":
        clip_y_expr = "(H-h)/2-20"
    else:
        layout = "pip_card"
        clip_y_expr = "640"

    seek_sec = float(getattr(scene, "video_start_sec", 0.0) or 0.0)
    vid_dur = get_audio_duration(video_path)
    safe_seek = 0.0
    use_loop = True
    if seek_sec > 0.2 and vid_dur > duration_sec + 0.8:
        safe_seek = seek_sec % max(0.5, vid_dur - duration_sec - 0.3)
        use_loop = False

    # Try Motion Design 2.0 + Realistic Mockup Overlay (HTML5/CSS3D Chrome RGBA `.mov`) first
    want_motion2 = (
        getattr(scene, "mockup_style", "auto") != "none"
        and getattr(scene, "motion_preset", "auto") != "basic_fade"
        and find_headless_browser() is not None
    )
    if want_motion2:
        ov_dur = max(0.75, t_out - t_in)
        hide_at_end = t_out < (duration_sec - 0.15)
        motion_mov = render_work_dir / f"_motion_s{scene.index:02d}.mov"
        try:
            _, res_mockup, res_motion, stage_y = render_motion_overlay_mov(
                overlay_img_path=overlay_img_path,
                scene=scene,
                project=project,
                out_mov=motion_mov,
                work_dir=render_work_dir,
                duration_sec=ov_dur,
                layout_mode=layout,
                accent_color=accent_color,
                fps=FPS,
                hide_at_end=hide_at_end,
            )
            enable_expr = f"between(t\\,{t_in:.2f}\\,{t_out:.2f})"
            fc_md2 = (
                f"[0:v]fps={FPS},split=2[bg_src][fg_src];"
                f"[bg_src]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"gblur=sigma=26,eq=brightness=-0.20:saturation=0.72[bg];"
                f"[fg_src]scale={W}:-2:force_original_aspect_ratio=decrease,{grade}[fg];"
                f"[bg][fg]overlay=(W-w)/2:{clip_y_expr}[base];"
                f"[1:v]fps={FPS},setpts=PTS-STARTPTS+{t_in:.3f}/TB[card];"
                f"[base][card]overlay=0:{stage_y}:enable='{enable_expr}':eof_action=pass,format=yuv420p[v]"
            )
            cmd_md2 = [ffmpeg, "-y"]
            if use_loop:
                cmd_md2.extend(["-stream_loop", "-1", "-i", str(video_path)])
            else:
                cmd_md2.extend(["-ss", f"{safe_seek:.2f}", "-i", str(video_path)])
            cmd_md2.extend([
                "-i", str(motion_mov),
                "-t", f"{duration_sec:.3f}",
                "-filter_complex", fc_md2,
                "-map", "[v]",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "18",
                "-an",
                str(out_mp4),
            ])
            res_md2 = subprocess.run(cmd_md2, capture_output=True, text=True)
            motion_mov.unlink(missing_ok=True)
            if res_md2.returncode == 0 and out_mp4.exists():
                return out_mp4, f"{layout}+{res_mockup}/{res_motion}"
        except Exception:
            motion_mov.unlink(missing_ok=True)

    # Fallback: Static PIL Evidence Card + FFmpeg Fade/Slide
    card_png_path = render_work_dir / f"_blend_card_s{scene.index:02d}.png"
    if layout == "split_top_image":
        target_w = int(min(1000, max(620, W * (0.48 + scale_pct * 0.68))))
        _, png_w, png_h = _build_news_evidence_card_png(
            overlay_img_path, label_txt, accent_color, card_png_path,
            card_w=target_w, max_img_h=445, layout_mode="split_top_image",
        )
        tx = 32 if pos == "top_left" else (max(32, W - png_w - 32) if pos == "top_right" else (W - png_w) // 2)
        ty = max(145, 645 - png_h)
    elif layout == "broll_cutaway":
        target_w = int(min(1010, max(660, W * (0.50 + scale_pct * 0.65))))
        _, png_w, png_h = _build_news_evidence_card_png(
            overlay_img_path, label_txt, accent_color, card_png_path,
            card_w=target_w, max_img_h=650, layout_mode="broll_cutaway",
        )
        tx = (W - png_w) // 2
        ty = max(175, min(1190 - png_h, (1210 - png_h) // 2))
    else:
        target_w = int(W * scale_pct)
        _, png_w, png_h = _build_news_evidence_card_png(
            overlay_img_path, label_txt, accent_color, card_png_path,
            card_w=target_w, max_img_h=430, layout_mode="pip_card",
        )
        if pos == "top_left":
            tx, ty = 36, 172
        elif pos == "top_right":
            tx, ty = max(36, W - png_w - 36), 172
        elif pos == "center":
            tx, ty = (W - png_w) // 2, max(175, min(1150 - png_h, (1180 - png_h) // 2))
        else:
            tx, ty = (W - png_w) // 2, 170

    fade_chain = f"fps={FPS},setpts=PTS-STARTPTS,format=rgba"
    if t_in > 0.05 or layout != "split_top_image":
        fade_chain += f",fade=t=in:st={t_in:.2f}:d=0.26:alpha=1"
    if t_out < duration_sec - 0.12:
        fade_out_st = max(t_in + 0.30, t_out - 0.26)
        fade_chain += f",fade=t=out:st={fade_out_st:.2f}:d=0.25:alpha=1"

    y_slide_expr = f"{ty}+36*max(0\\,1-(t-{t_in:.2f})/0.28)" if layout != "split_top_image" else str(ty)
    enable_expr = f"between(t\\,{t_in:.2f}\\,{t_out:.2f})"

    filter_complex = (
        f"[0:v]fps={FPS},split=2[bg_src][fg_src];"
        f"[bg_src]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
        f"gblur=sigma=26,eq=brightness=-0.20:saturation=0.72[bg];"
        f"[fg_src]scale={W}:-2:force_original_aspect_ratio=decrease,{grade}[fg];"
        f"[bg][fg]overlay=(W-w)/2:{clip_y_expr}[base];"
        f"[1:v]{fade_chain}[card];"
        f"[base][card]overlay=x={tx}:y='{y_slide_expr}':enable='{enable_expr}',format=yuv420p[v]"
    )

    cmd = [ffmpeg, "-y"]
    if use_loop:
        cmd.extend(["-stream_loop", "-1", "-i", str(video_path)])
    else:
        cmd.extend(["-ss", f"{safe_seek:.2f}", "-i", str(video_path)])
    cmd.extend(["-loop", "1", "-t", f"{duration_sec:.3f}", "-i", str(card_png_path)])
    cmd.extend([
        "-t", f"{duration_sec:.3f}",
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-an",
        str(out_mp4),
    ])
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Erro ao compor clipe+imagem ({layout}) em {video_path.name}: {res.stderr[-400:]}")
    return out_mp4, layout


def _build_track_hud_overlay(
    project: MusicStoryProject,
    centerpiece_png: Path,
    out_png: Path,
    pill_text: str,
    subtitle_label: str,
    compact_for_lyrics: bool = False,
    overlay_style: str = "clean_reels",
) -> Path:
    """Transparent 1080x1920 music card HUD overlay for live band / music-video cuts."""
    accent = _hex_to_rgb(project.accent_color)
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    # Respeita Safe Zone superior (SAFE_ZONE_TOP_PX = 250px) para não colidir com header do YouTube Shorts
    pill_y = max(SAFE_ZONE_TOP_PX + 12, 148 if overlay_style == "clean_reels" else 344)
    f_pill = _load_font(22, bold=True)
    pb = draw.textbbox((0, 0), pill_text, font=f_pill)
    pw = (pb[2] - pb[0]) + 46
    draw.rounded_rectangle(
        ((W - pw) // 2, pill_y, (W + pw) // 2, pill_y + 44),
        radius=22,
        fill=(14, 15, 22, 215),
        outline=(accent[0], accent[1], accent[2], 225),
        width=2,
    )
    draw.text((W // 2, pill_y + 22), pill_text, fill=(255, 255, 255, 245), font=f_pill, anchor="mm")

    if compact_for_lyrics:
        cw, ch = 860, 74
        cx0, cy0 = (W - cw) // 2, 1635
        draw.rounded_rectangle(
            (cx0, cy0, cx0 + cw, cy0 + ch),
            radius=20,
            fill=(10, 11, 18, 205),
            outline=(accent[0], accent[1], accent[2], 200),
            width=2,
        )
        if centerpiece_png.exists():
            with Image.open(centerpiece_png) as cp:
                art = cp.convert("RGBA")
                art.thumbnail((50, 50), Image.Resampling.LANCZOS)
                canvas.alpha_composite(art, (cx0 + 12, cy0 + (ch - art.height) // 2))
        f_title = _load_font(23, bold=True)
        draw.text(
            (cx0 + 76, cy0 + 37),
            f"♪ {project.song_title} — {project.artist}"[:42],
            fill=(248, 250, 252, 255),
            font=f_title,
            anchor="lm",
        )
    else:
        cw, ch = 890, 178
        cx0, cy0 = (W - cw) // 2, 1445
        draw.rounded_rectangle(
            (cx0, cy0, cx0 + cw, cy0 + ch),
            radius=26,
            fill=(10, 11, 18, 215),
            outline=(accent[0], accent[1], accent[2], 210),
            width=2,
        )
        if centerpiece_png.exists():
            with Image.open(centerpiece_png) as cp:
                art = cp.convert("RGBA")
                art.thumbnail((124, 124), Image.Resampling.LANCZOS)
                canvas.alpha_composite(art, (cx0 + 28, cy0 + (ch - art.height) // 2))

        f_title = _load_font(34, bold=True)
        f_sub = _load_font(23, bold=False)
        tx = cx0 + 180
        draw.text((tx, cy0 + 54), f"{project.song_title} — {project.artist}"[:34], fill=(255, 255, 255, 255), font=f_title, anchor="lm")
        draw.text((tx, cy0 + 114), subtitle_label[:48], fill=(accent[0], accent[1], accent[2], 245), font=f_sub, anchor="lm")

    out_png.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_png, format="PNG")
    return out_png


def _slice_spectrum_window(
    spectrum_full: np.ndarray,
    beat_flash_full: np.ndarray,
    start_sec: float,
    dur_sec: float,
    fps: int = FPS,
) -> tuple[np.ndarray, np.ndarray]:
    n_out = max(2, int(round(dur_sec * fps)))
    s0 = max(0, min(max(0, len(spectrum_full) - 1), int(round(start_sec * fps))))
    s1 = s0 + n_out
    spec_slice = spectrum_full[s0:s1]
    if len(spec_slice) < n_out and len(spec_slice) > 0:
        pad = n_out - len(spec_slice)
        spec_slice = np.vstack([spec_slice, np.repeat(spec_slice[-1:], pad, axis=0)])
    elif len(spec_slice) == 0:
        spec_slice = np.full((n_out, 36), 0.25, dtype=np.float32)

    bf_slice = beat_flash_full[s0:s1]
    if len(bf_slice) < n_out:
        bf_slice = np.pad(bf_slice, (0, n_out - len(bf_slice)))
    return spec_slice, bf_slice


def _render_scene_clip(
    image_path: Path,
    duration_sec: float,
    effect: str,
    out_mp4: Path,
    accent_color: str,
) -> Path:
    """Render one animated 1080x1920 Ken Burns scene clip using FFmpeg."""
    ffmpeg = get_ffmpeg_exe()
    n_frames = max(15, int(math.ceil(duration_sec * FPS)))
    if effect == "zoom_out":
        zp = f"scale=1620:2880,zoompan=z='max(1.0,1.14-0.14*(on/{n_frames}))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n_frames}:s={W}x{H}:fps={FPS}"
    elif effect == "pan_left":
        zp = f"scale=1620:2880,zoompan=z='1.12':x='(iw-iw/zoom)*(1-on/{n_frames})':y='ih/2-(ih/zoom/2)':d={n_frames}:s={W}x{H}:fps={FPS}"
    elif effect == "pan_right":
        zp = f"scale=1620:2880,zoompan=z='1.12':x='(iw-iw/zoom)*(on/{n_frames})':y='ih/2-(ih/zoom/2)':d={n_frames}:s={W}x{H}:fps={FPS}"
    elif effect == "ken_burns_duotone":
        r, g, b = _hex_to_rgb(accent_color)
        rm = max(0.35, r / 255.0)
        gm = max(0.30, g / 255.0)
        bm = max(0.35, b / 255.0)
        zp = (
            f"scale=1620:2880,zoompan=z='min(1.13,1.0+0.13*(on/{n_frames}))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n_frames}:s={W}x{H}:fps={FPS},"
            f"hue=s=0.35,colorchannelmixer=rr={rm:.2f}:gg={gm:.2f}:bb={bm:.2f},eq=contrast=1.12"
        )
    elif effect == "vintage_grain":
        zp = (
            f"scale=1620:2880,zoompan=z='max(1.0,1.12-0.12*(on/{n_frames}))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n_frames}:s={W}x{H}:fps={FPS},"
            "eq=contrast=1.08:saturation=0.78,noise=alls=9:allf=t"
        )
    else:
        zp = f"scale=1620:2880,zoompan=z='min(1.14,1.0+0.14*(on/{n_frames}))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n_frames}:s={W}x{H}:fps={FPS}"

    cmd = [
        ffmpeg,
        "-y",
        "-loop",
        "1",
        "-i",
        str(image_path),
        "-vf",
        f"{zp},format=yuv420p",
        "-t",
        f"{duration_sec:.3f}",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-an",
        str(out_mp4),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Erro ao animar cena {image_path.name}: {res.stderr[-400:]}")
    return out_mp4


def _concat_with_xfade(scene_clips: list[Path], scene_durations: list[float], out_mp4: Path, fade_dur: float = 0.28) -> Path:
    ffmpeg = get_ffmpeg_exe()
    if len(scene_clips) == 1:
        scene_clips[0].replace(out_mp4)
        return out_mp4

    cmd = [ffmpeg, "-y"]
    for clip in scene_clips:
        cmd.extend(["-i", str(clip)])

    transitions = ["fade", "dissolve", "wipeleft", "fadeblack", "wiperight"]
    filter_parts: list[str] = []
    cur_label = "[0:v]"
    cumulative = scene_durations[0]

    for i in range(1, len(scene_clips)):
        offset = max(0.1, cumulative - fade_dur)
        next_label = f"[v{i}]" if i < len(scene_clips) - 1 else "[vout]"
        trans = transitions[(i - 1) % len(transitions)]
        filter_parts.append(
            f"{cur_label}[{i}:v]xfade=transition={trans}:duration={fade_dur:.3f}:offset={offset:.3f}{next_label}"
        )
        cur_label = next_label
        cumulative = offset + scene_durations[i]

    cmd.extend([
        "-filter_complex",
        ";".join(filter_parts),
        "-map",
        "[vout]",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        str(out_mp4),
    ])
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Erro ao concatenar cenas com xfade: {res.stderr[-500:]}")
    return out_mp4


def _build_reel1_hook_chapter_html(
    project: MusicStoryProject,
    scene: SceneItem,
    image_rel_url: str,
    duration_sec: float,
    out_html: Path,
    intro_music_sec: float = 0.0,
    spectrum: np.ndarray | None = None,
    beat_flash: np.ndarray | None = None,
    fps: int = FPS,
    bpm: float = 120.0,
    track_data_json_path: Path | None = None,
) -> Path:
    """
    Generate OpenFilm Hook Card for Scene 1 that pairs cleanly with `_build_reel1_overlay`.
    In `clean_reels` mode preserves `project.hook_title` for the Scene 1 header;
    in `banner_top` swaps to the Scene 1 chapter sub-title so it doesn't duplicate the top overlay.
    """
    if getattr(project, "overlay_style", "clean_reels") == "clean_reels":
        hook_val = project.hook_title or f"{scene.title or 'A HISTÓRIA REAL'} • {project.year or 'CLÁSSICO'}"
    else:
        hook_val = f"{scene.title or 'A HISTÓRIA REAL'} • {project.year or 'CLÁSSICO'}"
    project_copy = project.model_copy(update={"hook_title": hook_val})
    return generate_reel1_hook_html(
        project=project_copy,
        scene=scene,
        image_rel_url=image_rel_url,
        duration_sec=duration_sec,
        out_html=out_html,
        intro_music_sec=intro_music_sec,
        spectrum=spectrum,
        beat_flash=beat_flash,
        fps=fps,
        bpm=bpm,
        track_data_json_path=track_data_json_path,
    )


def _overlay_png_on_mp4(in_mp4: Path, overlay_png: Path, out_mp4: Path) -> Path:
    ffmpeg = get_ffmpeg_exe()
    res = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(in_mp4),
            "-i",
            str(overlay_png),
            "-filter_complex",
            "[0:v][1:v]overlay=0:0,format=yuv420p[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(out_mp4),
        ],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"Erro ao aplicar HUD no clipe: {res.stderr[-400:]}")
    return out_mp4


def _build_cover_and_bg(
    project: MusicStoryProject,
    centerpiece_png: Path,
    out_cover_path: Path,
    pill_txt: str = "OUÇA A MÚSICA • PARTE 2",
) -> np.ndarray:
    accent = _hex_to_rgb(project.accent_color)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    cy_bg = H * 0.44
    cx_bg = W * 0.5
    dist = np.sqrt(((xx - cx_bg) / 520.0) ** 2 + ((yy - cy_bg) / 520.0) ** 2)
    glow = np.exp(-1.7 * (dist ** 2))
    bg_arr = np.zeros((H, W, 3), dtype=np.float32)
    bg_arr[:, :, 0] = 6.0 + glow * accent[0] * 0.28
    bg_arr[:, :, 1] = 6.0 + glow * accent[1] * 0.20
    bg_arr[:, :, 2] = 9.0 + glow * accent[2] * 0.28
    bg_img = Image.fromarray(np.clip(bg_arr, 0, 255).astype(np.uint8), mode="RGB").convert("RGBA")

    draw = ImageDraw.Draw(bg_img)
    f_badge = _load_font(26, bold=True)
    f_title = _load_font(54, bold=True)
    f_sub = _load_font(32, bold=False)

    pb = draw.textbbox((0, 0), pill_txt, font=f_badge)
    pw = (pb[2] - pb[0]) + 52
    py0 = max(SAFE_ZONE_TOP_PX + 12, 165)
    py1 = py0 + 50
    draw.rounded_rectangle(
        ((W - pw) // 2, py0, (W + pw) // 2, py1),
        radius=25,
        fill=(24, 24, 30, 220),
        outline=(accent[0], accent[1], accent[2], 210),
        width=2,
    )
    draw.text((W // 2, py0 + 25), pill_txt, fill=(255, 255, 255, 245), font=f_badge, anchor="mm")

    title_str = (project.reel2_title or f"{project.song_title}").strip()
    sub_str = (project.reel2_subtitle or f"{project.artist} • {project.year}").strip(" •")
    draw.text((W // 2, 1345), title_str[:30], fill=(255, 255, 255, 255), font=f_title, anchor="mm")
    draw.text((W // 2, 1412), sub_str[:42], fill=(195, 200, 212, 235), font=f_sub, anchor="mm")

    static_bg_rgb = np.asarray(bg_img.convert("RGB"), dtype=np.uint8)
    cover_img = bg_img.copy()
    if centerpiece_png.exists():
        with Image.open(centerpiece_png) as cp:
            cp_rgba = cp.convert("RGBA")
            cp_rgba.thumbnail((680, 680), Image.Resampling.LANCZOS)
            cw, ch = cp_rgba.size
            cover_img.alpha_composite(cp_rgba, ((W - cw) // 2, int(H * 0.43) - ch // 2))
    cover_img.convert("RGB").save(out_cover_path, format="PNG")
    return static_bg_rgb


def render_reel1(
    project: MusicStoryProject,
    work_dir: Path,
    out_dir: Path,
    narration_wav: Path,
    scene_spans: list[tuple[int, float, float]],
    word_timings: list[WordTiming],
    music_start_sec: float = 0.0,
    beat_times: list[float] | None = None,
) -> dict[str, str]:
    """Render Reel 1 ('Fatos / História da Música') combining HTML hook, archival video B-roll, Ken Burns photos, and subtitles LAST."""
    ffmpeg = get_ffmpeg_exe()
    render_dir = work_dir / "render_reel1"
    render_dir.mkdir(parents=True, exist_ok=True)

    overlay_png = _build_reel1_overlay(project, render_dir / "overlay.png")
    generate_ass_subtitles(
        word_timings=word_timings,
        scenes=project.scenes,
        scene_spans=scene_spans,
        out_ass_path=render_dir / "subtitles.ass",
        subtitle_style=project.subtitle_style,
        accent_color=project.accent_color,
    )

    fade_dur = 0.28
    outro_tail = 2.0 if project.audio_file and Path(project.audio_file).exists() else 1.4
    active_scenes = [s for s in project.scenes if s.narration_text.strip() and (s.image_path or s.video_path or s.html_path)]
    has_browser = find_headless_browser() is not None

    scene_clips: list[Path] = []
    raw_durs = [float(sc.duration_sec or 6.0) for sc in active_scenes]
    if project.beat_sync and beat_times and len(raw_durs) > 1:
        raw_durs = snap_durations_to_beats(raw_durs, beat_times, max_shift_sec=0.14)

    scene_durs: list[float] = []
    for idx, scene in enumerate(active_scenes):
        base_dur = raw_durs[idx]
        extra = outro_tail if idx == len(active_scenes) - 1 else fade_dur
        clip_dur = base_dur + extra
        clip_path = render_dir / f"scene_{scene.index:02d}.mp4"

        rendered = False

        # 1. Agent-authored custom HTML scene takes top precedence
        if scene.html_path and Path(scene.html_path).exists() and has_browser:
            try:
                html_p = Path(scene.html_path)
                render_html_film_to_mp4(
                    html_path=html_p,
                    serve_dir=work_dir,
                    out_mp4=clip_path,
                    duration_sec=clip_dur,
                    fps=FPS,
                )
                scene.asset_type = "html"
                rendered = True
            except Exception:
                rendered = False

        # 2. Built-in OpenFilm Motion Hook for Scene 1 (when html_hook_card is True and user hasn't forced a video clip)
        if not rendered and idx == 0 and project.html_hook_card and scene.asset_type != "video" and has_browser and scene.image_path and Path(scene.image_path).exists():
            try:
                hook_html = work_dir / "hook_scene.html"
                rel_img = Path(scene.image_path).resolve().relative_to(work_dir.resolve()).as_posix()
                _build_reel1_hook_chapter_html(project, scene, rel_img, clip_dur, hook_html)
                render_html_film_to_mp4(
                    html_path=hook_html,
                    serve_dir=work_dir,
                    out_mp4=clip_path,
                    duration_sec=clip_dur,
                    fps=FPS,
                )
                scene.html_path = str(hook_html)
                scene.asset_type = "html"
                rendered = True
            except Exception:
                rendered = False

        # 3. Archive.org / local stock MP4 video B-roll (with optional illustrative image overlay blend)
        if not rendered and scene.video_path and Path(scene.video_path).exists():
            ov_p = Path(scene.overlay_image_path) if getattr(scene, "overlay_image_path", None) else None
            use_blend = bool(ov_p and ov_p.exists() and getattr(scene, "visual_layout", "auto") != "full_media")
            try:
                if use_blend and ov_p is not None:
                    _render_blended_scene_clip(
                        video_path=scene.video_path if isinstance(scene.video_path, Path) else Path(scene.video_path),
                        overlay_img_path=ov_p,
                        scene=scene,
                        duration_sec=clip_dur,
                        out_mp4=clip_path,
                        accent_color=project.accent_color,
                        render_work_dir=render_dir,
                        project=project,
                    )
                else:
                    _render_scene_video_clip(
                        video_path=Path(scene.video_path),
                        duration_sec=clip_dur,
                        effect=scene.effect,
                        out_mp4=clip_path,
                        accent_color=project.accent_color,
                        seek_sec=float(getattr(scene, "video_start_sec", 0.0) or 0.0),
                    )
                scene.asset_type = "video"
                rendered = True
            except Exception:
                rendered = False

        # 4. Archival image + Ken Burns animation (+ optional Motion Design 2.0 overlay)
        if not rendered and scene.image_path:
            ov_p = Path(scene.overlay_image_path) if getattr(scene, "overlay_image_path", None) else None
            use_img_ov = bool(
                ov_p and ov_p.exists()
                and getattr(scene, "visual_layout", "auto") != "full_media"
                and getattr(scene, "mockup_style", "auto") != "none"
            )
            if use_img_ov and ov_p is not None:
                raw_img_mp4 = render_dir / f"_raw_img_{scene.index:02d}.mp4"
                _render_scene_clip(
                    image_path=Path(scene.image_path),
                    duration_sec=clip_dur,
                    effect=scene.effect,
                    out_mp4=raw_img_mp4,
                    accent_color=project.accent_color,
                )
                ok_ov, _ = _apply_motion_overlay_to_mp4(
                    base_mp4=raw_img_mp4,
                    overlay_img_path=ov_p,
                    scene=scene,
                    project=project,
                    duration_sec=clip_dur,
                    out_mp4=clip_path,
                    accent_color=project.accent_color,
                    render_work_dir=render_dir,
                    layout_mode="pip_card" if (scene.visual_layout or "auto") == "auto" else scene.visual_layout,
                )
                if ok_ov:
                    raw_img_mp4.unlink(missing_ok=True)
                else:
                    raw_img_mp4.replace(clip_path)
            else:
                _render_scene_clip(
                    image_path=Path(scene.image_path),
                    duration_sec=clip_dur,
                    effect=scene.effect,
                    out_mp4=clip_path,
                    accent_color=project.accent_color,
                )
            scene.asset_type = "image"

        scene_clips.append(clip_path)
        scene_durs.append(clip_dur)

    concat_mp4 = render_dir / "visual_concat.mp4"
    _concat_with_xfade(scene_clips, scene_durs, concat_mp4, fade_dur=fade_dur)

    # Hard Rule 1 (`video-use`): Vignette + badge overlay FIRST, burn ASS subtitles strictly LAST
    styled_silent_mp4 = render_dir / "visual_styled.mp4"
    cmd_style = [
        ffmpeg,
        "-y",
        "-i",
        concat_mp4.name,
        "-i",
        overlay_png.name,
        "-filter_complex",
        "[0:v][1:v]overlay=0:0,ass='subtitles.ass',scale=out_range=tv,format=yuv420p[v]",
        "-map",
        "[v]",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        styled_silent_mp4.name,
    ]
    res = subprocess.run(cmd_style, cwd=str(render_dir), capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Erro ao aplicar legendas e overlay no Reel 1: {res.stderr[-500:]}")

    outputs: dict[str, str] = {}
    has_music = bool(project.audio_file and Path(project.audio_file).exists())
    mode = project.audio_export_mode

    if has_music and mode in {"both", "with_music"}:
        mixed_wav, total_dur = mix_narration_with_music(
            narration_wav=narration_wav,
            music_source=Path(project.audio_file),  # type: ignore[arg-type]
            out_wav=render_dir / "audio_mixed.wav",
            music_start_sec=music_start_sec,
            outro_tail_sec=outro_tail,
        )
        out_mp4 = out_dir / "reel1_fatos_com_musica.mp4"
        cmd_mux = [
            ffmpeg,
            "-y",
            "-i",
            str(styled_silent_mp4),
            "-i",
            str(mixed_wav),
            "-t",
            f"{total_dur:.3f}",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(out_mp4),
        ]
        res_mux = subprocess.run(cmd_mux, capture_output=True, text=True)
        if res_mux.returncode != 0:
            raise RuntimeError(f"Erro ao muxar Reel 1 com música: {res_mux.stderr[-400:]}")
        outputs["reel1_with_music"] = str(out_mp4)

    if mode in {"both", "clean_for_instagram"} or not has_music:
        clean_wav, total_dur = pad_narration_only(
            narration_wav=narration_wav,
            out_wav=render_dir / "audio_clean.wav",
            outro_tail_sec=outro_tail,
        )
        out_clean = out_dir / "reel1_fatos_narracao_limpa.mp4"
        cmd_clean = [
            ffmpeg,
            "-y",
            "-i",
            str(styled_silent_mp4),
            "-i",
            str(clean_wav),
            "-t",
            f"{total_dur:.3f}",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(out_clean),
        ]
        res_clean = subprocess.run(cmd_clean, capture_output=True, text=True)
        if res_clean.returncode != 0:
            raise RuntimeError(f"Erro ao muxar Reel 1 narração limpa: {res_clean.stderr[-400:]}")
        outputs["reel1_clean"] = str(out_clean)

    return outputs


def _build_ember_sprite(size: int = 24, color: tuple[int, int, int] = (255, 145, 55)) -> np.ndarray:
    """Create a soft radial glow RGBA sprite as float32 [H, W, 4] in 0..1."""
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    c = (size - 1) / 2.0
    dist = np.sqrt(((xx - c) / (c + 0.1)) ** 2 + ((yy - c) / (c + 0.1)) ** 2)
    alpha = np.exp(-3.4 * (dist ** 2)) * (dist <= 1.0)
    sprite = np.zeros((size, size, 4), dtype=np.float32)
    sprite[:, :, 0] = np.clip(color[0] / 255.0 + 0.25 * alpha, 0.0, 1.0)
    sprite[:, :, 1] = np.clip(color[1] / 255.0 + 0.20 * alpha, 0.0, 1.0)
    sprite[:, :, 2] = color[2] / 255.0
    sprite[:, :, 3] = alpha
    return sprite


def render_reel2_and_cover(
    project: MusicStoryProject,
    work_dir: Path,
    out_dir: Path,
    api_key_override: str | None = None,
) -> tuple[dict[str, str], float, float]:
    """
    Render Reel 2 ('Ouça a Música') + Capa de Destaque PNG:
    - Uses OpenFilm deterministic HTML5 Canvas2D renderer (`reel2_renderer='html_canvas'`)
      when Chrome/Edge is available, falling back smoothly to NumPy+PIL pipe (`ffmpeg_numpy`).
    - Precomputes exact 36-band spectrum + beat onsets/BPM (`beat_flash`).
    """
    ffmpeg = get_ffmpeg_exe()
    render_dir = work_dir / "render_reel2"
    render_dir.mkdir(parents=True, exist_ok=True)
    api_key = get_gemini_api_key(api_key_override)

    centerpiece_png = generate_reel2_centerpiece(project, work_dir, api_key)
    accent = _hex_to_rgb(project.accent_color)

    has_music = bool(project.audio_file and Path(project.audio_file).exists())
    music_clip_wav = render_dir / "reel2_clip.wav"
    chosen_start = 0.0
    duration_sec = float(project.audio_clip_duration or 45.0)
    bpm = 118.0

    if has_music:
        _, chosen_start, duration_sec = prepare_reel2_music_clip(
            audio_path=Path(project.audio_file),  # type: ignore[arg-type]
            out_wav=music_clip_wav,
            start_sec=project.audio_clip_start,
            duration_sec=duration_sec,
        )
        spectrum = compute_audio_energy_envelope(music_clip_wav, fps=FPS, num_bands=36)
        beat_flash = np.zeros(len(spectrum), dtype=np.float32)
        if project.beat_sync:
            beat_info = analyze_beats_and_onsets(music_clip_wav, fps=FPS)
            bpm = float(beat_info["bpm"])
            beat_flash = beat_info["beat_flash"]
    else:
        n_frames_synth = max(1, int(round(duration_sec * FPS)))
        t_arr = np.linspace(0, duration_sec, n_frames_synth, dtype=np.float32)[:, None]
        b_idx = np.linspace(0, 1, 36, dtype=np.float32)[None, :]
        beat = 0.5 + 0.5 * np.sin(2 * math.pi * (bpm / 60.0) * t_arr)
        wave = 0.35 + 0.35 * np.sin(t_arr * 3.1 + b_idx * 8.0) + 0.25 * beat * np.exp(-3.0 * b_idx)
        spectrum = np.clip(wave, 0.08, 0.95).astype(np.float32)
        beat_flash = (
            (np.maximum(0.0, np.sin(2 * math.pi * (bpm / 60.0) * t_arr[:, 0])) ** 6).astype(np.float32)
            if project.beat_sync
            else np.zeros(n_frames_synth, dtype=np.float32)
        )

    n_frames = min(len(spectrum), max(FPS * 5, int(round(duration_sec * FPS))))
    duration_sec = n_frames / float(FPS)

    cover_path = out_dir / "capa_destaque.png"
    static_bg_rgb = _build_cover_and_bg(
        project=project,
        centerpiece_png=centerpiece_png,
        out_cover_path=cover_path,
        pill_txt="OUÇA A MÚSICA • PARTE 2",
    )
    generate_project_thumbnail(
        project=project,
        work_dir=work_dir,
        out_cover_path=cover_path,
    )

    silent_mp4 = render_dir / "reel2_silent.mp4"
    html_rendered = False

    if project.reel2_renderer == "html_canvas" and find_headless_browser():
        try:
            # Copy centerpiece cleanly beside the HTML inside render_dir so relative path works cleanly
            cp_local = render_dir / "centerpiece.png"
            shutil.copy2(centerpiece_png, cp_local)
            html_file = render_dir / "reel2_stage.html"
            track_json = render_dir / "track_data.json"
            generate_reel2_visualizer_html(
                project=project,
                centerpiece_rel_url=cp_local.name,
                spectrum=spectrum[:n_frames],
                beat_flash=beat_flash[:n_frames],
                bpm=bpm,
                duration_sec=duration_sec,
                fps=FPS,
                out_html=html_file,
                track_data_json_path=track_json,
            )
            render_html_film_to_mp4(
                html_path=html_file,
                serve_dir=render_dir,
                out_mp4=silent_mp4,
                duration_sec=duration_sec,
                fps=FPS,
            )
            html_rendered = True
        except Exception:
            html_rendered = False

    if not html_rendered:
        with Image.open(centerpiece_png) as cp:
            cp_base = cp.convert("RGBA")
            cp_base.thumbnail((620, 620), Image.Resampling.LANCZOS)
            cp_w, cp_h = cp_base.size

        rng = random.Random(42)
        embers = [
            {
                "x": rng.uniform(40, W - 40),
                "y": rng.uniform(0, H),
                "vy": rng.uniform(2.0, 5.4),
                "vx": rng.uniform(-0.7, 0.7),
                "phase": rng.uniform(0, 6.28),
                "size": rng.choice([14, 18, 22, 28]),
            }
            for _ in range(55)
        ]
        sprites = {
            sz: _build_ember_sprite(sz, color=(255, min(255, accent[1] + 95), max(35, accent[2] // 2 + 30)))
            for sz in (14, 18, 22, 28)
        }

        cmd_pipe = [
            ffmpeg,
            "-y",
            "-f",
            "rawvideo",
            "-vcodec",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{W}x{H}",
            "-r",
            str(FPS),
            "-i",
            "pipe:0",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "19",
            "-pix_fmt",
            "yuv420p",
            str(silent_mp4),
        ]
        proc = subprocess.Popen(cmd_pipe, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        assert proc.stdin is not None

        bar_count = spectrum.shape[1]
        bar_area_w = 820
        bar_step = bar_area_w // bar_count
        bar_w = max(8, bar_step - 8)
        bar_x0 = (W - bar_count * bar_step) // 2
        base_y = 1565
        prog_x0, prog_x1, prog_y = 140, W - 140, 1615
        center_y = int(H * 0.43)

        cp_arr_base = np.asarray(cp_base, dtype=np.float32)
        cp_rgb_base = cp_arr_base[:, :, :3]
        cp_a_base = cp_arr_base[:, :, 3:4] / 255.0

        for f_idx in range(n_frames):
            frame = static_bg_rgb.astype(np.float32)
            bands = spectrum[f_idx]
            bass = float(np.mean(bands[:6]))
            flash = float(beat_flash[f_idx]) if f_idx < len(beat_flash) else 0.0

            for emb in embers:
                emb["y"] -= emb["vy"] * (1.0 + 0.6 * bass + 0.7 * flash)
                emb["x"] += emb["vx"] + math.sin(f_idx * 0.05 + emb["phase"]) * 0.6
                if emb["y"] < -30:
                    emb["y"] = H + 25
                    emb["x"] = rng.uniform(40, W - 40)
                sz = emb["size"]
                x0 = int(emb["x"] - sz // 2)
                y0 = int(emb["y"] - sz // 2)
                if 0 <= x0 and x0 + sz <= W and 0 <= y0 and y0 + sz <= H:
                    sp = sprites[sz]
                    alpha = sp[:, :, 3:4] * min(1.0, 0.45 + 0.40 * bass + 0.35 * flash)
                    patch = frame[y0 : y0 + sz, x0 : x0 + sz]
                    frame[y0 : y0 + sz, x0 : x0 + sz] = np.clip(
                        patch + sp[:, :, :3] * 255.0 * alpha, 0.0, 255.0
                    )

            if project.reel2_visual_style == "vinyl_pulse":
                angle = -(f_idx * 0.45) % 360.0
                scale = 1.0 + 0.035 * bass + 0.025 * flash
                rot = cp_base.rotate(angle, resample=Image.Resampling.BILINEAR)
                nw, nh = int(cp_w * scale), int(cp_h * scale)
                scaled = rot.resize((nw, nh), Image.Resampling.BILINEAR)
                arr = np.asarray(scaled, dtype=np.float32)
                rgb, alp = arr[:, :, :3], arr[:, :, 3:4] / 255.0
                x0, y0 = (W - nw) // 2, center_y - nh // 2
                frame[y0 : y0 + nh, x0 : x0 + nw] = (
                    frame[y0 : y0 + nh, x0 : x0 + nw] * (1.0 - alp) + rgb * alp
                )
            else:
                pulse_step = int(round(bass * 16 + flash * 12))
                if pulse_step <= 1:
                    x0, y0 = (W - cp_w) // 2, center_y - cp_h // 2
                    frame[y0 : y0 + cp_h, x0 : x0 + cp_w] = (
                        frame[y0 : y0 + cp_h, x0 : x0 + cp_w] * (1.0 - cp_a_base)
                        + cp_rgb_base * cp_a_base
                    )
                else:
                    nw, nh = cp_w + pulse_step, cp_h + pulse_step
                    scaled = cp_base.resize((nw, nh), Image.Resampling.BILINEAR)
                    arr = np.asarray(scaled, dtype=np.float32)
                    rgb, alp = arr[:, :, :3], arr[:, :, 3:4] / 255.0
                    x0, y0 = (W - nw) // 2, center_y - nh // 2
                    frame[y0 : y0 + nh, x0 : x0 + nw] = (
                        frame[y0 : y0 + nh, x0 : x0 + nw] * (1.0 - alp) + rgb * alp
                    )

            if project.reel2_visual_style == "neon_wave":
                mid_y = base_y - 52
                for b in range(bar_count):
                    val = float(bands[b])
                    half_h = max(4, int(val * 54))
                    bx0 = bar_x0 + b * bar_step
                    c_r = min(255.0, accent[0] + val * 110.0)
                    c_g = min(255.0, accent[1] + val * 125.0)
                    c_b = min(255.0, accent[2] + val * 110.0)
                    frame[mid_y - half_h : mid_y + half_h, bx0 : bx0 + bar_w, 0] = c_r
                    frame[mid_y - half_h : mid_y + half_h, bx0 : bx0 + bar_w, 1] = c_g
                    frame[mid_y - half_h : mid_y + half_h, bx0 : bx0 + bar_w, 2] = c_b
            else:
                for b in range(bar_count):
                    val = float(bands[b])
                    bh = int(10 + val * 96)
                    bx0 = bar_x0 + b * bar_step
                    by0 = base_y - bh
                    c_r = min(255.0, accent[0] + val * 85.0)
                    c_g = min(255.0, accent[1] + val * 110.0)
                    c_b = min(255.0, accent[2] + val * 90.0)
                    frame[by0:base_y, bx0 : bx0 + bar_w, 0] = c_r
                    frame[by0:base_y, bx0 : bx0 + bar_w, 1] = c_g
                    frame[by0:base_y, bx0 : bx0 + bar_w, 2] = c_b

            frame[prog_y : prog_y + 5, prog_x0:prog_x1] = [55.0, 58.0, 68.0]
            prog_px = prog_x0 + int((prog_x1 - prog_x0) * ((f_idx + 1) / float(n_frames)))
            frame[prog_y : prog_y + 5, prog_x0:prog_px] = [float(accent[0]), float(accent[1]), float(accent[2])]

            proc.stdin.write(np.clip(frame, 0, 255).astype(np.uint8).tobytes())

        proc.stdin.close()
        stderr_out = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
        ret = proc.wait()
        if ret != 0:
            raise RuntimeError(f"Erro ao renderizar Reel 2: {stderr_out[-400:]}")

    outputs: dict[str, str] = {"highlight_cover": str(cover_path)}
    mode = project.audio_export_mode

    if has_music and mode in {"both", "with_music"}:
        out_music = out_dir / "reel2_ouca_com_musica.mp4"
        cmd_mux = [
            ffmpeg,
            "-y",
            "-i",
            str(silent_mp4),
            "-i",
            str(music_clip_wav),
            "-t",
            f"{duration_sec:.3f}",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "256k",
            "-movflags",
            "+faststart",
            str(out_music),
        ]
        res_mux = subprocess.run(cmd_mux, capture_output=True, text=True)
        if res_mux.returncode != 0:
            raise RuntimeError(f"Erro ao muxar Reel 2 com música: {res_mux.stderr[-400:]}")
        outputs["reel2_with_music"] = str(out_music)

    if mode in {"both", "clean_for_instagram"} or not has_music:
        out_ig = out_dir / "reel2_ouca_instagram_mudo.mp4"
        cmd_ig = [
            ffmpeg,
            "-y",
            "-i",
            str(silent_mp4),
            "-c:v",
            "copy",
            "-an",
            "-movflags",
            "+faststart",
            str(out_ig),
        ]
        res_ig = subprocess.run(cmd_ig, capture_output=True, text=True)
        if res_ig.returncode != 0:
            raise RuntimeError(f"Erro ao exportar Reel 2 mudo para Instagram: {res_ig.stderr[-400:]}")
        outputs["reel2_clean_instagram"] = str(out_ig)

    return outputs, chosen_start, bpm


def render_single_all_in_one_reel(
    project: MusicStoryProject,
    work_dir: Path,
    out_dir: Path,
    api_key_override: str | None = None,
) -> MusicStoryProject:
    """
    Single All-in-One Reel pipeline:
    1. Intro Musical (~1.8s loud music hook)
    2. Narrator explains historical fact over smoothly ducked music + synchronized .ass subtitles
    3. Musical Break (~2.8s loud music swell with moving band/concert visual + equalizer HUD)
    4. Repeat alternation through all curated beats
    5. Climax Musical Finale (~12s moving band clips or multi-shot concert montage + equalizer + highlight cover)
    """
    ffmpeg = get_ffmpeg_exe()
    work_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    render_dir = work_dir / "render_allinone"
    render_dir.mkdir(parents=True, exist_ok=True)
    api_key = get_gemini_api_key(api_key_override)

    prepare_project_visuals(project, work_dir, api_key_override=api_key_override)
    segments, actual_tts = synthesize_narration_segments(
        scenes=project.scenes,
        work_dir=work_dir,
        tts_engine=project.tts_engine,
        voice_name=project.voice_name,
        api_key_override=api_key_override,
    )

    music_source = Path(project.audio_file) if project.audio_file and Path(project.audio_file).exists() else None
    has_music = music_source is not None

    audio_plan = assemble_interleaved_audio(
        segments=segments,
        work_dir=work_dir,
        music_source=music_source,
        music_start_sec=project.audio_clip_start,
        intro_music_sec=project.intro_music_sec,
        finale_music_sec=project.finale_music_sec,
        finale_audio_start_sec=getattr(project, "finale_audio_start_sec", None),
    )
    visual_segments: list[dict[str, object]] = audio_plan["visual_segments"]
    scene_spans: list[tuple[int, float, float]] = audio_plan["scene_spans"]
    word_timings: list[WordTiming] = audio_plan["word_timings"]
    total_dur = float(audio_plan["total_dur"])
    chosen_start = float(audio_plan["chosen_start"])
    music_raw_wav: Path | None = audio_plan.get("music_bed_raw_wav")

    bpm = 118.0
    n_total_frames = max(30, int(math.ceil(total_dur * FPS)) + 15)
    if music_raw_wav and music_raw_wav.exists():
        spectrum_full = compute_audio_energy_envelope(music_raw_wav, fps=FPS, num_bands=36)
        beat_info = analyze_beats_and_onsets(music_raw_wav, fps=FPS)
        bpm = float(beat_info["bpm"])
        beat_flash_full = (
            beat_info["beat_flash"]
            if project.beat_sync
            else np.zeros(len(spectrum_full), dtype=np.float32)
        )
    else:
        t_arr = np.linspace(0, total_dur, n_total_frames, dtype=np.float32)[:, None]
        b_idx = np.linspace(0, 1, 36, dtype=np.float32)[None, :]
        beat = 0.5 + 0.5 * np.sin(2 * math.pi * (bpm / 60.0) * t_arr)
        wave_s = 0.35 + 0.35 * np.sin(t_arr * 3.1 + b_idx * 8.0) + 0.25 * beat * np.exp(-3.0 * b_idx)
        spectrum_full = np.clip(wave_s, 0.08, 0.95).astype(np.float32)
        beat_flash_full = (
            (np.maximum(0.0, np.sin(2 * math.pi * (bpm / 60.0) * t_arr[:, 0])) ** 6).astype(np.float32)
            if project.beat_sync
            else np.zeros(n_total_frames, dtype=np.float32)
        )

    centerpiece_png = generate_reel2_centerpiece(project, work_dir, api_key)
    cp_local = render_dir / "centerpiece.png"
    shutil.copy2(centerpiece_png, cp_local)

    gallery_imgs = fetch_artist_live_gallery(project, work_dir, max_images=5)
    gallery_local_names: list[str] = []
    for g_idx, g_path in enumerate(gallery_imgs, start=1):
        local_name = f"gallery_{g_idx:02d}.png"
        shutil.copy2(g_path, render_dir / local_name)
        gallery_local_names.append(local_name)

    cover_path = out_dir / "capa_destaque.png"
    generate_project_thumbnail(
        project=project,
        work_dir=work_dir,
        out_cover_path=cover_path,
    )

    overlay_png = _build_reel1_overlay(project, render_dir / "overlay.png")
    generate_ass_subtitles(
        word_timings=word_timings,
        scenes=project.scenes,
        scene_spans=scene_spans,
        out_ass_path=render_dir / "subtitles.ass",
        subtitle_style=project.subtitle_style,
        accent_color=project.accent_color,
        break_cues=audio_plan.get("break_cues"),
    )

    video_exts = {".mp4", ".mov", ".webm", ".mkv"}
    band_clips = [Path(p) for p in (project.band_video_clips or []) if p and Path(p).exists()]
    source_is_video = bool(music_source and music_source.suffix.lower() in video_exts)
    if not band_clips and source_is_video and music_source is not None:
        band_clips = [music_source]

    has_browser = find_headless_browser() is not None
    use_html_motion = project.reel2_renderer == "html_canvas" and has_browser
    scene_map = {s.index: s for s in project.scenes}

    # Centered crossfade math: every audio boundary sits dead-center in the 0.22s transition!
    fade_dur = 0.22
    n_segs = len(visual_segments)
    clip_durs: list[float] = []
    for k, vseg in enumerate(visual_segments):
        base_d = float(vseg["duration_sec"])  # type: ignore[arg-type]
        if n_segs == 1:
            clip_durs.append(base_d)
        elif k == 0 or k == n_segs - 1:
            clip_durs.append(round(base_d + fade_dur / 2.0, 3))
        else:
            clip_durs.append(round(base_d + fade_dur, 3))

    rendered_clips: list[Path] = []
    eff_cycle = ["zoom_in", "pan_right", "zoom_out", "pan_left"]

    for k, vseg in enumerate(visual_segments):
        kind = str(vseg["kind"])
        seg_start = float(vseg["start"])  # type: ignore[arg-type]
        clip_dur = clip_durs[k]
        seg_mp4 = render_dir / f"seg_{k:02d}_{kind}.mp4"

        if kind == "hook_scene":
            sc = scene_map[int(vseg["scene_index"])]  # type: ignore[arg-type]
            done = False
            if sc.html_path and Path(sc.html_path).exists() and has_browser:
                try:
                    render_html_film_to_mp4(Path(sc.html_path), work_dir, seg_mp4, clip_dur, FPS)
                    vseg["media_source"] = f"html:{Path(sc.html_path).name}"
                    sc.asset_type = "html"
                    done = True
                except Exception:
                    done = False
            if not done and project.html_hook_card and sc.asset_type != "video" and has_browser and sc.image_path and Path(sc.image_path).exists():
                try:
                    hook_html = work_dir / "hook_scene.html"
                    rel_img = Path(sc.image_path).resolve().relative_to(work_dir.resolve()).as_posix()
                    sp_win, bf_win = _slice_spectrum_window(spectrum_full, beat_flash_full, 0.0, clip_dur, FPS)
                    _build_reel1_hook_chapter_html(
                        project=project,
                        scene=sc,
                        image_rel_url=rel_img,
                        duration_sec=clip_dur,
                        out_html=hook_html,
                        intro_music_sec=float(vseg.get("intro_embedded_sec", 0.0) or 0.0),
                        spectrum=sp_win,
                        beat_flash=bf_win,
                        fps=FPS,
                        bpm=bpm,
                        track_data_json_path=work_dir / "hook_track_data.json",
                    )
                    render_html_film_to_mp4(hook_html, work_dir, seg_mp4, clip_dur, FPS)
                    sc.html_path = str(hook_html)
                    sc.asset_type = "html"
                    vseg["media_source"] = "html:hook_scene.html"
                    done = True
                except Exception:
                    done = False
            if not done and sc.video_path and Path(sc.video_path).exists():
                _render_scene_video_clip(
                    Path(sc.video_path),
                    clip_dur,
                    sc.effect,
                    seg_mp4,
                    project.accent_color,
                    seek_sec=float(getattr(sc, "video_start_sec", 0.0) or 0.0),
                )
                sc.asset_type = "video"
                vseg["media_source"] = f"video:{Path(sc.video_path).name}"
                done = True
            if not done and sc.image_path:
                _render_scene_clip(Path(sc.image_path), clip_dur, sc.effect, seg_mp4, project.accent_color)
                sc.asset_type = "image"
                vseg["media_source"] = f"img:{Path(sc.image_path).name}"
            if (sc.quote_text or "").strip() and seg_mp4.exists():
                quote_png = _build_quote_overlay(
                    quote_text=sc.quote_text,
                    artist=project.artist,
                    out_png=render_dir / f"quote_{sc.index:02d}.png",
                    accent_color=project.accent_color,
                    y_top=425 if getattr(project, "overlay_style", "clean_reels") == "clean_reels" else 215,
                )
                raw_q = render_dir / f"_raw_q_{k:02d}.mp4"
                seg_mp4.replace(raw_q)
                _overlay_png_on_mp4(raw_q, quote_png, seg_mp4)
                raw_q.unlink(missing_ok=True)

        elif kind == "music_break":
            brk_num = int(vseg.get("break_index", 1) or 1)
            has_lyric = bool(vseg.get("has_lyric_caption"))
            ov_style = getattr(project, "overlay_style", "clean_reels")
            done = False
            if band_clips:
                try:
                    vc = band_clips[(brk_num - 1) % len(band_clips)]
                    hud_png = _build_track_hud_overlay(
                        project=project,
                        centerpiece_png=centerpiece_png,
                        out_png=render_dir / f"hud_break_{brk_num:02d}.png",
                        pill_text=f"♪ TRECHO ORIGINAL • PARTE 0{brk_num}",
                        subtitle_label="▶ OUÇA O SOM ORIGINAL",
                        compact_for_lyrics=has_lyric,
                        overlay_style=ov_style,
                    )
                    src_cue = vseg.get("audio_src_start_sec")
                    seek_t = (
                        float(src_cue)
                        if (source_is_video and vc == music_source and src_cue is not None)
                        else ((chosen_start + seg_start) if (source_is_video and vc == music_source) else (brk_num * 4.2))
                    )
                    _render_scene_video_clip(
                        video_path=vc,
                        duration_sec=clip_dur,
                        effect="zoom_in",
                        out_mp4=seg_mp4,
                        accent_color=project.accent_color,
                        seek_sec=seek_t,
                        hud_overlay_png=hud_png,
                    )
                    vseg["media_source"] = f"video:{vc.name}"
                    done = True
                except Exception:
                    done = False
            if not done and use_html_motion:
                try:
                    sp_win, bf_win = _slice_spectrum_window(spectrum_full, beat_flash_full, seg_start, clip_dur, FPS)
                    gal_name = gallery_local_names[(brk_num - 1) % len(gallery_local_names)]
                    brk_html = render_dir / f"break_{brk_num:02d}.html"
                    brk_json = render_dir / f"break_{brk_num:02d}_data.json"
                    generate_music_break_html(
                        project=project,
                        break_num=brk_num,
                        image_rel_url=gal_name,
                        artwork_rel_url=cp_local.name,
                        spectrum=sp_win,
                        beat_flash=bf_win,
                        bpm=bpm,
                        duration_sec=clip_dur,
                        fps=FPS,
                        out_html=brk_html,
                        track_data_json_path=brk_json,
                        has_lyric_caption=has_lyric,
                        overlay_style=ov_style,
                    )
                    render_html_film_to_mp4(brk_html, render_dir, seg_mp4, clip_dur, FPS)
                    vseg["media_source"] = f"html:{brk_html.name}"
                    done = True
                except Exception:
                    done = False
            if not done:
                raw_brk_mp4 = render_dir / f"_raw_brk_{brk_num:02d}.mp4"
                img_src = gallery_imgs[(brk_num - 1) % len(gallery_imgs)]
                _render_scene_clip(
                    image_path=img_src,
                    duration_sec=clip_dur,
                    effect=eff_cycle[(brk_num - 1) % len(eff_cycle)],
                    out_mp4=raw_brk_mp4,
                    accent_color=project.accent_color,
                )
                hud_png = _build_track_hud_overlay(
                    project=project,
                    centerpiece_png=centerpiece_png,
                    out_png=render_dir / f"hud_break_{brk_num:02d}.png",
                    pill_text=f"♪ TRECHO ORIGINAL • PARTE 0{brk_num}",
                    subtitle_label="▶ OUÇA O SOM ORIGINAL",
                    compact_for_lyrics=has_lyric,
                    overlay_style=ov_style,
                )
                _overlay_png_on_mp4(raw_brk_mp4, hud_png, seg_mp4)
                raw_brk_mp4.unlink(missing_ok=True)
                vseg["media_source"] = f"img:{img_src.name}"

        elif kind == "narration_scene":
            sc = scene_map[int(vseg["scene_index"])]  # type: ignore[arg-type]
            done = False
            if sc.html_path and Path(sc.html_path).exists() and has_browser:
                try:
                    render_html_film_to_mp4(Path(sc.html_path), work_dir, seg_mp4, clip_dur, FPS)
                    sc.asset_type = "html"
                    vseg["media_source"] = f"html:{Path(sc.html_path).name}"
                    done = True
                except Exception:
                    done = False
            if not done and sc.video_path and Path(sc.video_path).exists():
                ov_p = Path(sc.overlay_image_path) if getattr(sc, "overlay_image_path", None) else None
                use_blend = bool(ov_p and ov_p.exists() and getattr(sc, "visual_layout", "auto") != "full_media")
                try:
                    if use_blend and ov_p is not None:
                        _, used_layout = _render_blended_scene_clip(
                            video_path=Path(sc.video_path),
                            overlay_img_path=ov_p,
                            scene=sc,
                            duration_sec=clip_dur,
                            out_mp4=seg_mp4,
                            accent_color=project.accent_color,
                            render_work_dir=render_dir,
                            project=project,
                        )
                        vseg["media_source"] = f"video+img({used_layout}):{Path(sc.video_path).name}"
                    else:
                        _render_scene_video_clip(
                            Path(sc.video_path),
                            clip_dur,
                            sc.effect,
                            seg_mp4,
                            project.accent_color,
                            seek_sec=float(getattr(sc, "video_start_sec", 0.0) or 0.0),
                        )
                        vseg["media_source"] = f"video:{Path(sc.video_path).name}"
                    sc.asset_type = "video"
                    done = True
                except Exception:
                    done = False
            if not done and sc.image_path:
                ov_p = Path(sc.overlay_image_path) if getattr(sc, "overlay_image_path", None) else None
                use_img_ov = bool(
                    ov_p and ov_p.exists()
                    and getattr(sc, "visual_layout", "auto") != "full_media"
                    and getattr(sc, "mockup_style", "auto") != "none"
                )
                if use_img_ov and ov_p is not None:
                    raw_img_mp4 = render_dir / f"_raw_img_{k:02d}.mp4"
                    _render_scene_clip(Path(sc.image_path), clip_dur, sc.effect, raw_img_mp4, project.accent_color)
                    ok_ov, used_m = _apply_motion_overlay_to_mp4(
                        base_mp4=raw_img_mp4,
                        overlay_img_path=ov_p,
                        scene=sc,
                        project=project,
                        duration_sec=clip_dur,
                        out_mp4=seg_mp4,
                        accent_color=project.accent_color,
                        render_work_dir=render_dir,
                        layout_mode="pip_card" if (sc.visual_layout or "auto") == "auto" else sc.visual_layout,
                    )
                    if ok_ov:
                        raw_img_mp4.unlink(missing_ok=True)
                        vseg["media_source"] = f"img+motion({used_m}):{Path(sc.image_path).name}"
                    else:
                        raw_img_mp4.replace(seg_mp4)
                        vseg["media_source"] = f"img:{Path(sc.image_path).name}"
                else:
                    _render_scene_clip(Path(sc.image_path), clip_dur, sc.effect, seg_mp4, project.accent_color)
                    vseg["media_source"] = f"img:{Path(sc.image_path).name}"
                sc.asset_type = "image"
            if (sc.quote_text or "").strip() and seg_mp4.exists():
                quote_png = _build_quote_overlay(
                    quote_text=sc.quote_text,
                    artist=project.artist,
                    out_png=render_dir / f"quote_{sc.index:02d}.png",
                    accent_color=project.accent_color,
                    y_top=215,
                )
                raw_q = render_dir / f"_raw_q_{k:02d}.mp4"
                seg_mp4.replace(raw_q)
                _overlay_png_on_mp4(raw_q, quote_png, seg_mp4)
                raw_q.unlink(missing_ok=True)

        elif kind == "music_finale":
            ov_style = getattr(project, "overlay_style", "clean_reels")
            done = False
            if band_clips:
                try:
                    n_cuts = max(2, min(4, int(round(clip_dur / 3.0))))
                    sub_fade = 0.16
                    sub_target = clip_dur / n_cuts
                    sub_clips: list[Path] = []
                    sub_durs: list[float] = []
                    fin_src = vseg.get("audio_src_start_sec")
                    fin_base_seek = float(fin_src) if fin_src is not None else (chosen_start + seg_start)
                    for c_i in range(n_cuts):
                        vc = band_clips[c_i % len(band_clips)]
                        if n_cuts == 1:
                            sd = clip_dur
                        elif c_i == 0 or c_i == n_cuts - 1:
                            sd = sub_target + sub_fade / 2.0
                        else:
                            sd = sub_target + sub_fade
                        sub_mp4 = render_dir / f"_fin_sub_{c_i:02d}.mp4"
                        seek_t = (
                            fin_base_seek + c_i * sub_target
                            if (source_is_video and vc == music_source)
                            else c_i * 5.0
                        )
                        _render_scene_video_clip(
                            video_path=vc,
                            duration_sec=sd,
                            effect="zoom_in" if c_i % 2 == 0 else "vintage_grain",
                            out_mp4=sub_mp4,
                            accent_color=project.accent_color,
                            seek_sec=seek_t,
                        )
                        sub_clips.append(sub_mp4)
                        sub_durs.append(sd)

                    fin_raw_mp4 = render_dir / "_finale_raw.mp4"
                    _concat_with_xfade(sub_clips, sub_durs, fin_raw_mp4, fade_dur=sub_fade)
                    hud_fin = _build_track_hud_overlay(
                        project=project,
                        centerpiece_png=centerpiece_png,
                        out_png=render_dir / "hud_finale.png",
                        pill_text="🎧 CLÍMAX FINAL • SALVE NOS DESTAQUES",
                        subtitle_label=project.reel2_subtitle or f"{project.artist} • {project.year}",
                        compact_for_lyrics=False,
                        overlay_style=ov_style,
                    )
                    _overlay_png_on_mp4(fin_raw_mp4, hud_fin, seg_mp4)
                    fin_raw_mp4.unlink(missing_ok=True)
                    vseg["media_source"] = f"video:{band_clips[0].name}"
                    done = True
                except Exception:
                    done = False

            if not done and use_html_motion:
                try:
                    sp_win, bf_win = _slice_spectrum_window(spectrum_full, beat_flash_full, seg_start, clip_dur, FPS)
                    fin_html = render_dir / "finale_stage.html"
                    fin_json = render_dir / "finale_data.json"
                    generate_allinone_finale_html(
                        project=project,
                        gallery_rel_urls=gallery_local_names,
                        centerpiece_rel_url=cp_local.name,
                        spectrum=sp_win,
                        beat_flash=bf_win,
                        bpm=bpm,
                        duration_sec=clip_dur,
                        fps=FPS,
                        out_html=fin_html,
                        track_data_json_path=fin_json,
                    )
                    render_html_film_to_mp4(fin_html, render_dir, seg_mp4, clip_dur, FPS)
                    vseg["media_source"] = "html:finale_stage.html"
                    done = True
                except Exception:
                    done = False

            if not done:
                n_shots = max(2, min(len(gallery_imgs), max(2, int(round(clip_dur / 3.5)))))
                sub_fade = 0.20
                sub_target = clip_dur / n_shots
                sub_clips = []
                sub_durs = []
                for s_i in range(n_shots):
                    if n_shots == 1:
                        sd = clip_dur
                    elif s_i == 0 or s_i == n_shots - 1:
                        sd = sub_target + sub_fade / 2.0
                    else:
                        sd = sub_target + sub_fade
                    sub_mp4 = render_dir / f"_fin_img_{s_i:02d}.mp4"
                    _render_scene_clip(
                        image_path=gallery_imgs[s_i % len(gallery_imgs)],
                        duration_sec=sd,
                        effect=eff_cycle[s_i % len(eff_cycle)],
                        out_mp4=sub_mp4,
                        accent_color=project.accent_color,
                    )
                    sub_clips.append(sub_mp4)
                    sub_durs.append(sd)
                fin_raw_mp4 = render_dir / "_finale_raw.mp4"
                _concat_with_xfade(sub_clips, sub_durs, fin_raw_mp4, fade_dur=sub_fade)
                hud_fin = _build_track_hud_overlay(
                    project=project,
                    centerpiece_png=centerpiece_png,
                    out_png=render_dir / "hud_finale.png",
                    pill_text="🎧 CLÍMAX FINAL • SALVE NOS DESTAQUES",
                    subtitle_label=project.reel2_subtitle or f"{project.artist} • {project.year}",
                    compact_for_lyrics=False,
                    overlay_style=ov_style,
                )
                _overlay_png_on_mp4(fin_raw_mp4, hud_fin, seg_mp4)
                fin_raw_mp4.unlink(missing_ok=True)
                vseg["media_source"] = "montage:live_gallery"

        rendered_clips.append(seg_mp4)

    concat_mp4 = render_dir / "visual_concat.mp4"
    _concat_with_xfade(rendered_clips, clip_durs, concat_mp4, fade_dur=fade_dur)

    # Hard Rule 1 (`video-use`): Vignette + badge overlay FIRST, burn ASS subtitles strictly LAST
    styled_silent_mp4 = render_dir / "visual_styled.mp4"
    res_style = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            concat_mp4.name,
            "-i",
            overlay_png.name,
            "-filter_complex",
            "[0:v][1:v]overlay=0:0,ass='subtitles.ass',scale=out_range=tv,format=yuv420p[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            styled_silent_mp4.name,
        ],
        cwd=str(render_dir),
        capture_output=True,
        text=True,
    )
    if res_style.returncode != 0:
        raise RuntimeError(f"Erro ao aplicar legendas e overlay no Vídeo Único: {res_style.stderr[-500:]}")

    outputs: dict[str, str] = {"highlight_cover": str(cover_path)}
    mode = project.audio_export_mode
    mixed_wav: Path | None = audio_plan.get("mixed_wav")
    clean_wav: Path = audio_plan["clean_wav"]

    if has_music and mixed_wav and mixed_wav.exists() and mode in {"both", "with_music"}:
        out_full = out_dir / "reel_completo_musica_e_fatos.mp4"
        res_mux = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(styled_silent_mp4),
                "-i",
                str(mixed_wav),
                "-t",
                f"{total_dur:.3f}",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "224k",
                "-movflags",
                "+faststart",
                str(out_full),
            ],
            capture_output=True,
            text=True,
        )
        if res_mux.returncode != 0:
            raise RuntimeError(f"Erro ao muxar Vídeo Único com música: {res_mux.stderr[-400:]}")
        outputs["reel_completo_with_music"] = str(out_full)

    if mode in {"both", "clean_for_instagram"} or not has_music:
        out_clean = out_dir / "reel_completo_narracao_limpa.mp4"
        res_clean = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(styled_silent_mp4),
                "-i",
                str(clean_wav),
                "-t",
                f"{total_dur:.3f}",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(out_clean),
            ],
            capture_output=True,
            text=True,
        )
        if res_clean.returncode != 0:
            raise RuntimeError(f"Erro ao muxar Vídeo Único versão limpa: {res_clean.stderr[-400:]}")
        outputs["reel_completo_clean"] = str(out_clean)

    caption_text = project.instagram_caption.strip()
    if project.hashtags:
        caption_text += "\n\n" + " ".join(project.hashtags)
    caption_path = out_dir / "legenda_instagram.txt"
    caption_path.write_text(caption_text + "\n", encoding="utf-8")
    outputs["caption_txt"] = str(caption_path)

    project.outputs = outputs
    qa_report = run_project_qa(
        project=project,
        work_dir=work_dir,
        out_dir=out_dir,
        scene_spans=scene_spans,
        word_timings=word_timings,
        beat_bpm=bpm,
        visual_segments=visual_segments,
        audio_timeline=audio_plan["timeline"],
    )
    project.outputs.update(qa_report.get("contact_sheets", {}))
    project.qa_report = qa_report
    qa_tag = "QA OK" if qa_report.get("qa_passed") else "QA Atenção"
    project.status = f"rendered ({actual_tts} • Único • {qa_tag})"
    return project


def _synthesize_gaming_short_narration(
    project: MusicStoryProject,
    work_dir: Path,
    max_cap: float,
    intro_sec: float,
    finale_tail_sec: float,
    api_key_override: str | None = None,
) -> tuple[list[dict[str, object]], str, str]:
    """Etapa 1 do Short GTA 6: sintetiza narração TTS e ajusta taxa/palavras para caber no teto <= 58s."""
    pause_overhead = 0.16 * max(0, len(project.scenes) - 1)
    voice_budget = max(18.0, max_cap - intro_sec - finale_tail_sec - pause_overhead - 0.35)

    cur_rate = getattr(project, "speech_rate", "+14%") or "+14%"
    segments, actual_tts = synthesize_narration_segments(
        scenes=project.scenes,
        work_dir=work_dir,
        tts_engine=project.tts_engine,
        voice_name=project.voice_name,
        api_key_override=api_key_override,
        rate=cur_rate,
    )
    voice_sum = sum(float(s["duration_sec"]) for s in segments)

    for fallback_rate in ("+16%", "+20%", "+24%"):
        if voice_sum <= voice_budget:
            break
        cur_rate = fallback_rate
        segments, actual_tts = synthesize_narration_segments(
            scenes=project.scenes,
            work_dir=work_dir,
            tts_engine=project.tts_engine,
            voice_name=project.voice_name,
            api_key_override=api_key_override,
            rate=cur_rate,
        )
        voice_sum = sum(float(s["duration_sec"]) for s in segments)

    if voice_sum > voice_budget:
        sc_dicts = [s.model_dump() for s in project.scenes]
        max_w = max(
            75,
            int(
                sum(len(s.narration_text.split()) for s in project.scenes)
                * (voice_budget / max(1.0, voice_sum))
            ),
        )
        trimmed = enforce_script_duration_budget(sc_dicts, max_words=max_w)
        for orig, td in zip(project.scenes, trimmed):
            orig.narration_text = str(td.get("narration_text", orig.narration_text))
        project.narration_full = " ".join(s.narration_text for s in project.scenes if s.narration_text.strip())
        segments, actual_tts = synthesize_narration_segments(
            scenes=project.scenes,
            work_dir=work_dir,
            tts_engine=project.tts_engine,
            voice_name=project.voice_name,
            api_key_override=api_key_override,
            rate=cur_rate,
        )

    for sc in project.scenes:
        sc.music_break_after_sec = 0.0

    return segments, actual_tts, cur_rate


def _build_gaming_short_audio_plan(
    project: MusicStoryProject,
    segments: list[dict[str, object]],
    work_dir: Path,
    render_dir: Path,
    max_cap: float,
    intro_sec: float,
    finale_tail_sec: float,
) -> tuple[dict[str, object], float]:
    """Etapa 2 do Short GTA 6: seleciona/gera trilha sonora e monta o master de áudio intercalado com ducking."""
    music_source: Path | None = (
        Path(project.audio_file)
        if project.audio_file and Path(project.audio_file).exists()
        else None
    )
    if music_source is None and project.audio_export_mode != "clean_for_instagram":
        auto_track, auto_start = auto_select_gaming_soundtrack(project)
        if auto_track is not None and auto_track.exists():
            music_source = auto_track
            project.audio_file = str(auto_track)
            if project.audio_clip_start is None:
                project.audio_clip_start = auto_start
        else:
            synth_bed = render_dir / "vice_city_bed.wav"
            generate_ambient_synthwave_bed(synth_bed, duration_sec=max_cap + 4.0)
            music_source = synth_bed

    if music_source is not None and project.audio_clip_start is None and music_source.name != "vice_city_bed.wav":
        try:
            project.audio_clip_start = round(
                find_best_highlight_start(music_source, clip_duration=min(54.0, max_cap)), 1
            )
        except Exception as exc:
            logger.warning("Falha ao localizar highlight inicial da trilha (%s): %s", music_source, exc)
            project.audio_clip_start = 0.0

    audio_plan = assemble_interleaved_audio(
        segments=segments,
        work_dir=work_dir,
        music_source=music_source,
        music_start_sec=project.audio_clip_start or 0.0,
        intro_music_sec=intro_sec,
        finale_music_sec=finale_tail_sec,
        duck_level=0.14,
        full_level=0.45,
        allow_short_finale=True,
    )
    visual_segments: list[dict[str, object]] = audio_plan["visual_segments"]
    total_dur = min(max_cap, float(audio_plan["total_dur"]))

    # Mescla a cauda curta do finale na última cena narrada para evitar corte seco
    if len(visual_segments) >= 2 and visual_segments[-1].get("kind") == "music_finale":
        fin_seg = visual_segments.pop()
        prev_seg = visual_segments[-1]
        prev_seg["end"] = min(max_cap, float(fin_seg["end"]))
        prev_seg["duration_sec"] = round(float(prev_seg["end"]) - float(prev_seg["start"]), 3)

    return audio_plan, total_dur


def _render_gaming_short_scenes(
    project: MusicStoryProject,
    visual_segments: list[dict[str, object]],
    clip_durs: list[float],
    work_dir: Path,
    render_dir: Path,
    spectrum_full: np.ndarray,
    beat_flash_full: np.ndarray,
    bpm: float,
    has_browser: bool,
) -> list[Path]:
    """Etapa 3 do Short GTA 6: renderiza cada cena (HTML, vídeo+overlay, vídeo ou imagem+Ken Burns + quote)."""
    scene_map = {s.index: s for s in project.scenes}
    rendered_clips: list[Path] = []

    for k, vseg in enumerate(visual_segments):
        kind = str(vseg["kind"])
        clip_dur = clip_durs[k]
        seg_mp4 = render_dir / f"seg_{k:02d}_{kind}.mp4"
        sc = scene_map[int(vseg["scene_index"])]  # type: ignore[arg-type]
        done = False

        if sc.html_path and Path(sc.html_path).exists() and has_browser:
            try:
                render_html_film_to_mp4(Path(sc.html_path), work_dir, seg_mp4, clip_dur, FPS)
                sc.asset_type = "html"
                vseg["media_source"] = f"html:{Path(sc.html_path).name}"
                done = True
            except Exception as exc:
                logger.warning("Fallback de render HTML na cena %s: %s", sc.index, exc)
                done = False

        if (
            not done
            and kind == "hook_scene"
            and project.html_hook_card
            and sc.asset_type != "video"
            and has_browser
            and sc.image_path
            and Path(sc.image_path).exists()
        ):
            try:
                hook_html = work_dir / "hook_scene.html"
                rel_img = Path(sc.image_path).resolve().relative_to(work_dir.resolve()).as_posix()
                sp_win, bf_win = _slice_spectrum_window(spectrum_full, beat_flash_full, 0.0, clip_dur, FPS)
                _build_reel1_hook_chapter_html(
                    project=project,
                    scene=sc,
                    image_rel_url=rel_img,
                    duration_sec=clip_dur,
                    out_html=hook_html,
                    intro_music_sec=0.0,
                    spectrum=sp_win,
                    beat_flash=bf_win,
                    fps=FPS,
                    bpm=bpm,
                    track_data_json_path=work_dir / "hook_track_data.json",
                )
                render_html_film_to_mp4(hook_html, work_dir, seg_mp4, clip_dur, FPS)
                sc.html_path = str(hook_html)
                sc.asset_type = "html"
                vseg["media_source"] = "html:hook_scene.html"
                done = True
            except Exception as exc:
                logger.warning("Fallback de hook card HTML na cena %s: %s", sc.index, exc)
                done = False

        if not done and sc.video_path and Path(sc.video_path).exists():
            ov_p = Path(sc.overlay_image_path) if getattr(sc, "overlay_image_path", None) else None
            use_blend = bool(ov_p and ov_p.exists() and getattr(sc, "visual_layout", "auto") != "full_media")
            try:
                if use_blend and ov_p is not None:
                    _, used_layout = _render_blended_scene_clip(
                        video_path=Path(sc.video_path),
                        overlay_img_path=ov_p,
                        scene=sc,
                        duration_sec=clip_dur,
                        out_mp4=seg_mp4,
                        accent_color=project.accent_color,
                        render_work_dir=render_dir,
                        project=project,
                    )
                    vseg["media_source"] = f"video+img({used_layout}):{Path(sc.video_path).name}"
                else:
                    _render_scene_video_clip(
                        Path(sc.video_path),
                        clip_dur,
                        sc.effect,
                        seg_mp4,
                        project.accent_color,
                        seek_sec=float(getattr(sc, "video_start_sec", 0.0) or 0.0),
                        framing_mode=getattr(sc, "framing_mode", "crop_center") or "crop_center",
                        focus_x=float(getattr(sc, "focus_x", 0.5) if getattr(sc, "focus_x", None) is not None else 0.5),
                    )
                    vseg["media_source"] = f"video:{Path(sc.video_path).name}"
                sc.asset_type = "video"
                done = True
            except Exception as exc:
                logger.warning("Fallback de cena vídeo+overlay na cena %s: %s", sc.index, exc)
                try:
                    _render_scene_video_clip(
                        Path(sc.video_path),
                        clip_dur,
                        sc.effect,
                        seg_mp4,
                        project.accent_color,
                        seek_sec=float(getattr(sc, "video_start_sec", 0.0) or 0.0),
                        framing_mode=getattr(sc, "framing_mode", "crop_center") or "crop_center",
                        focus_x=float(getattr(sc, "focus_x", 0.5) if getattr(sc, "focus_x", None) is not None else 0.5),
                    )
                    sc.asset_type = "video"
                    vseg["media_source"] = f"video:{Path(sc.video_path).name}"
                    done = True
                except Exception as exc2:
                    logger.warning("Falha também no fallback de vídeo puro da cena %s: %s", sc.index, exc2)
                    done = False

        if not done and sc.image_path and Path(sc.image_path).exists():
            ov_p = Path(sc.overlay_image_path) if getattr(sc, "overlay_image_path", None) else None
            use_img_ov = bool(
                ov_p and ov_p.exists()
                and getattr(sc, "visual_layout", "auto") != "full_media"
                and getattr(sc, "mockup_style", "auto") != "none"
            )
            if use_img_ov and ov_p is not None:
                raw_img_mp4 = render_dir / f"_raw_yt_img_{k:02d}.mp4"
                _render_scene_clip(Path(sc.image_path), clip_dur, sc.effect, raw_img_mp4, project.accent_color)
                ok_ov, used_m = _apply_motion_overlay_to_mp4(
                    base_mp4=raw_img_mp4,
                    overlay_img_path=ov_p,
                    scene=sc,
                    project=project,
                    duration_sec=clip_dur,
                    out_mp4=seg_mp4,
                    accent_color=project.accent_color,
                    render_work_dir=render_dir,
                    layout_mode="pip_card" if (sc.visual_layout or "auto") == "auto" else sc.visual_layout,
                )
                if ok_ov:
                    raw_img_mp4.unlink(missing_ok=True)
                    vseg["media_source"] = f"img+motion({used_m}):{Path(sc.image_path).name}"
                else:
                    raw_img_mp4.replace(seg_mp4)
                    vseg["media_source"] = f"img:{Path(sc.image_path).name}"
            else:
                _render_scene_clip(Path(sc.image_path), clip_dur, sc.effect, seg_mp4, project.accent_color)
                vseg["media_source"] = f"img:{Path(sc.image_path).name}"
            sc.asset_type = "image"

        if (sc.quote_text or "").strip() and seg_mp4.exists() and kind != "hook_scene":
            quote_png = _build_quote_overlay(
                quote_text=sc.quote_text,
                artist=project.artist,
                out_png=render_dir / f"quote_{sc.index:02d}.png",
                accent_color=project.accent_color,
                y_top=235,
            )
            raw_q = render_dir / f"_raw_q_{k:02d}.mp4"
            seg_mp4.replace(raw_q)
            _overlay_png_on_mp4(raw_q, quote_png, seg_mp4)
            raw_q.unlink(missing_ok=True)

        rendered_clips.append(seg_mp4)

    return rendered_clips


def _composite_mux_and_qa_gaming_short(
    project: MusicStoryProject,
    rendered_clips: list[Path],
    clip_durs: list[float],
    fade_dur: float,
    overlay_png: Path,
    audio_plan: dict[str, object],
    total_dur: float,
    bpm: float,
    visual_segments: list[dict[str, object]],
    scene_spans: list[tuple[int, float, float]],
    word_timings: list[WordTiming],
    cover_path: Path,
    actual_tts: str,
    cur_rate: str,
    work_dir: Path,
    render_dir: Path,
    out_dir: Path,
) -> MusicStoryProject:
    """Etapa 4 do Short GTA 6: concatena com xfade, aplica overlay+legendas ASS, muxa áudio, gera pacote e roda QA."""
    ffmpeg = get_ffmpeg_exe()
    concat_mp4 = render_dir / "visual_concat.mp4"
    _concat_with_xfade(rendered_clips, clip_durs, concat_mp4, fade_dur=fade_dur)

    styled_silent_mp4 = render_dir / "visual_styled.mp4"
    res_style = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            concat_mp4.name,
            "-i",
            overlay_png.name,
            "-filter_complex",
            "[0:v][1:v]overlay=0:0,ass='subtitles.ass',scale=out_range=tv,format=yuv420p[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            styled_silent_mp4.name,
        ],
        cwd=str(render_dir),
        capture_output=True,
        text=True,
    )
    if res_style.returncode != 0:
        raise RuntimeError(f"Erro ao aplicar legendas no Short YouTube: {res_style.stderr[-500:]}")

    audio_chosen: Path = (
        audio_plan["mixed_wav"]
        if (
            project.audio_export_mode != "clean_for_instagram"
            and audio_plan.get("mixed_wav")
            and Path(audio_plan["mixed_wav"]).exists()
        )
        else audio_plan["clean_wav"]
    )
    out_short = out_dir / "short_youtube_gta6.mp4"
    res_mux = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(styled_silent_mp4),
            "-i",
            str(audio_chosen),
            "-t",
            f"{total_dur:.3f}",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(out_short),
        ],
        capture_output=True,
        text=True,
    )
    if res_mux.returncode != 0:
        raise RuntimeError(f"Erro ao muxar áudio do Short YouTube: {res_mux.stderr[-400:]}")

    yt_title = (project.youtube_title or f"{project.song_title} #shorts")[:95]
    yt_desc = project.youtube_description or project.instagram_caption
    yt_tags = ", ".join(project.youtube_tags or ["GTA 6", "Shorts"])
    tk_cap = project.tiktok_caption or project.instagram_caption
    meta_txt = (
        f"=== YOUTUBE SHORTS ===\n"
        f"TÍTULO:\n{yt_title}\n\n"
        f"DESCRIÇÃO:\n{yt_desc}\n\n"
        f"TAGS:\n{yt_tags}\n\n"
        f"=== TIKTOK (POSTAGEM MANUAL) ===\n"
        f"{tk_cap}\n"
    )
    meta_path = out_dir / "youtube_tiktok_pacote.txt"
    meta_path.write_text(meta_txt, encoding="utf-8")

    project.outputs = {
        "youtube_short": str(out_short),
        "highlight_cover": str(cover_path),
        "caption_txt": str(meta_path),
    }
    qa_report = run_project_qa(
        project=project,
        work_dir=work_dir,
        out_dir=out_dir,
        scene_spans=scene_spans,
        word_timings=word_timings,
        beat_bpm=bpm,
        visual_segments=visual_segments,
        audio_timeline=audio_plan["timeline"],
    )
    project.outputs.update(qa_report.get("contact_sheets", {}))
    project.qa_report = qa_report
    qa_verdict = qa_report.get("verdict") or ("Pronto" if qa_report.get("qa_passed") else "QA Atenção")
    dur_measured = qa_report.get("probes", {}).get("youtube_short", {}).get("duration_sec", round(total_dur, 1))
    project.status = f"rendered ({actual_tts} @ {cur_rate} • Short {dur_measured}s • {qa_verdict})"
    return project


def render_gaming_youtube_short(
    project: MusicStoryProject,
    work_dir: Path,
    out_dir: Path,
    api_key_override: str | None = None,
) -> MusicStoryProject:
    """
    Pipeline principal de YouTube Shorts (`niche == "gaming_news"`), decomposto em 4 etapas nomeadas:
    1. `_synthesize_gaming_short_narration` (TTS + controle real <= 58s)
    2. `_build_gaming_short_audio_plan` (trilha/cama synthwave + ducking intercalado)
    3. `_render_gaming_short_scenes` (cortes de trailer, overlays, Ken Burns e quotes)
    4. `_composite_mux_and_qa_gaming_short` (xfade, legendas ASS, mux AAC, pacote e QA objetivo)
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    render_dir = work_dir / "render_allinone"
    render_dir.mkdir(parents=True, exist_ok=True)

    prepare_gaming_project_visuals(project, work_dir)

    max_cap = min(58.0, float(getattr(project, "max_duration_sec", 58.0) or 58.0))
    intro_sec = 0.0
    finale_tail_sec = 0.25

    segments, actual_tts, cur_rate = _synthesize_gaming_short_narration(
        project=project,
        work_dir=work_dir,
        max_cap=max_cap,
        intro_sec=intro_sec,
        finale_tail_sec=finale_tail_sec,
        api_key_override=api_key_override,
    )
    audio_plan, total_dur = _build_gaming_short_audio_plan(
        project=project,
        segments=segments,
        work_dir=work_dir,
        render_dir=render_dir,
        max_cap=max_cap,
        intro_sec=intro_sec,
        finale_tail_sec=finale_tail_sec,
    )

    visual_segments: list[dict[str, object]] = audio_plan["visual_segments"]
    scene_spans: list[tuple[int, float, float]] = audio_plan["scene_spans"]
    word_timings: list[WordTiming] = audio_plan["word_timings"]

    bpm = 118.0
    n_total_frames = max(30, int(math.ceil(total_dur * FPS)) + 15)
    t_arr = np.linspace(0, total_dur, n_total_frames, dtype=np.float32)[:, None]
    b_idx = np.linspace(0, 1, 36, dtype=np.float32)[None, :]
    beat = 0.5 + 0.5 * np.sin(2 * math.pi * (bpm / 60.0) * t_arr)
    spectrum_full = np.clip(
        0.32 + 0.34 * np.sin(t_arr * 3.1 + b_idx * 8.0) + 0.24 * beat * np.exp(-3.0 * b_idx),
        0.08,
        0.95,
    ).astype(np.float32)
    beat_flash_full = (np.maximum(0.0, np.sin(2 * math.pi * (bpm / 60.0) * t_arr[:, 0])) ** 6).astype(np.float32)

    cover_path = out_dir / "capa_destaque.png"
    generate_project_thumbnail(project=project, work_dir=work_dir, out_cover_path=cover_path)

    overlay_png = _build_reel1_overlay(project, render_dir / "overlay.png")
    generate_ass_subtitles(
        word_timings=word_timings,
        scenes=project.scenes,
        scene_spans=scene_spans,
        out_ass_path=render_dir / "subtitles.ass",
        subtitle_style=project.subtitle_style or "vice_city",
        accent_color=project.accent_color,
        safe_zone="youtube_shorts",
    )

    has_browser = find_headless_browser() is not None
    fade_dur = 0.20
    n_segs = len(visual_segments)
    clip_durs: list[float] = []
    for k, vseg in enumerate(visual_segments):
        base_d = float(vseg["duration_sec"])  # type: ignore[arg-type]
        if n_segs == 1:
            clip_durs.append(base_d)
        elif k == 0 or k == n_segs - 1:
            clip_durs.append(round(base_d + fade_dur / 2.0, 3))
        else:
            clip_durs.append(round(base_d + fade_dur, 3))

    rendered_clips = _render_gaming_short_scenes(
        project=project,
        visual_segments=visual_segments,
        clip_durs=clip_durs,
        work_dir=work_dir,
        render_dir=render_dir,
        spectrum_full=spectrum_full,
        beat_flash_full=beat_flash_full,
        bpm=bpm,
        has_browser=has_browser,
    )
    return _composite_mux_and_qa_gaming_short(
        project=project,
        rendered_clips=rendered_clips,
        clip_durs=clip_durs,
        fade_dur=fade_dur,
        overlay_png=overlay_png,
        audio_plan=audio_plan,
        total_dur=total_dur,
        bpm=bpm,
        visual_segments=visual_segments,
        scene_spans=scene_spans,
        word_timings=word_timings,
        cover_path=cover_path,
        actual_tts=actual_tts,
        cur_rate=cur_rate,
        work_dir=work_dir,
        render_dir=render_dir,
        out_dir=out_dir,
    )


def render_full_project(
    project: MusicStoryProject,
    work_dir: Path,
    out_dir: Path,
    api_key_override: str | None = None,
) -> MusicStoryProject:
    """
    Full production pipeline dispatching cleanly between:
    - `niche == "gaming_news"`: YouTube Shorts pipeline strictly capped at `<= 58s`
    - `single_all_in_one` (default music): 1 integrated Reel alternating music highlights & narrated facts
    - `two_reels_combo`: 2 separated Reels (Reel 1 Facts + Reel 2 Listen to Music)
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    if getattr(project, "niche", "music_history") == "gaming_news":
        return render_gaming_youtube_short(
            project=project,
            work_dir=work_dir,
            out_dir=out_dir,
            api_key_override=api_key_override,
        )

    if project.video_mode == "single_all_in_one":
        return render_single_all_in_one_reel(
            project=project,
            work_dir=work_dir,
            out_dir=out_dir,
            api_key_override=api_key_override,
        )

    prepare_project_visuals(project, work_dir, api_key_override=api_key_override)

    narr_wav, scene_spans, word_timings, actual_tts = synthesize_narration(
        scenes=project.scenes,
        work_dir=work_dir,
        tts_engine=project.tts_engine,
        voice_name=project.voice_name,
        api_key_override=api_key_override,
    )

    reel2_outputs, chosen_start, bpm = render_reel2_and_cover(
        project=project,
        work_dir=work_dir,
        out_dir=out_dir,
        api_key_override=api_key_override,
    )

    beat_times_for_r1: list[float] | None = None
    music_clip_wav = work_dir / "render_reel2" / "reel2_clip.wav"
    if project.beat_sync and music_clip_wav.exists():
        beat_times_for_r1 = analyze_beats_and_onsets(music_clip_wav, fps=FPS).get("beat_times")

    reel1_outputs = render_reel1(
        project=project,
        work_dir=work_dir,
        out_dir=out_dir,
        narration_wav=narr_wav,
        scene_spans=scene_spans,
        word_timings=word_timings,
        music_start_sec=chosen_start,
        beat_times=beat_times_for_r1,
    )

    caption_text = project.instagram_caption.strip()
    if project.hashtags:
        caption_text += "\n\n" + " ".join(project.hashtags)
    caption_path = out_dir / "legenda_instagram.txt"
    caption_path.write_text(caption_text + "\n", encoding="utf-8")

    project.outputs = {
        **reel1_outputs,
        **reel2_outputs,
        "caption_txt": str(caption_path),
    }

    # Automated self-eval QA + filmstrip contact sheets
    qa_report = run_project_qa(
        project=project,
        work_dir=work_dir,
        out_dir=out_dir,
        scene_spans=scene_spans,
        word_timings=word_timings,
        beat_bpm=bpm,
    )
    project.outputs.update(qa_report.get("contact_sheets", {}))
    project.qa_report = qa_report
    qa_tag = "QA OK" if qa_report.get("qa_passed") else "QA Atenção"
    project.status = f"rendered ({actual_tts} • {qa_tag})"
    return project


def render_single_scene_preview_mp4(
    project: MusicStoryProject,
    scene_index: int,
    work_dir: Path,
    api_key_override: str | None = None,
) -> Path:
    """
    Fast ~6-10s single-scene FFmpeg preview render:
    Synthesizes TTS for one scene, renders its exact 9:16 video/image + Motion 2.0 overlay + .ass subtitles
    + audio bed mix into `scenes/scene_{idx:02d}_preview.mp4` for rapid live verification in Step 4.
    """
    ffmpeg = get_ffmpeg_exe()
    scenes_dir = work_dir / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    out_mp4 = scenes_dir / f"scene_{scene_index:02d}_preview.mp4"

    sc = next((s for s in project.scenes if s.index == scene_index), None)
    if sc is None:
        raise ValueError(f"Cena {scene_index} não encontrada.")

    if not ((sc.video_path and Path(sc.video_path).exists()) or (sc.image_path and Path(sc.image_path).exists())):
        if getattr(project, "niche", "music_history") == "gaming_news":
            prepare_gaming_project_visuals(project, work_dir)
        else:
            prepare_project_visuals(project, work_dir, api_key_override=api_key_override)

    tmp_dir = scenes_dir / f"_preview_tmp_{scene_index:02d}"
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        cur_rate = getattr(project, "speech_rate", "+14%") or "+14%"
        is_gaming = getattr(project, "niche", "music_history") == "gaming_news"
        if not is_gaming and cur_rate == "+14%":
            cur_rate = "+6%"

        segments, _ = synthesize_narration_segments(
            scenes=[sc],
            work_dir=tmp_dir,
            tts_engine=project.tts_engine,
            voice_name=project.voice_name,
            api_key_override=api_key_override,
            rate=cur_rate,
        )
        if not segments:
            raise RuntimeError("Cena sem texto de narração para gerar prévia com voz.")

        dur = max(2.2, float(segments[0]["duration_sec"]))
        music_source = (
            Path(project.audio_file)
            if project.audio_file and Path(project.audio_file).exists()
            else None
        )
        if music_source is None and is_gaming and project.audio_export_mode != "clean_for_instagram":
            auto_track, _ = auto_select_gaming_soundtrack(project)
            if auto_track and auto_track.exists():
                music_source = auto_track

        audio_plan = assemble_interleaved_audio(
            segments=segments,
            work_dir=tmp_dir,
            music_source=music_source,
            music_start_sec=project.audio_clip_start or 0.0,
            intro_music_sec=0.0,
            finale_music_sec=0.0,
            duck_level=0.14,
            full_level=0.45,
            allow_short_finale=True,
        )
        audio_chosen = (
            audio_plan["mixed_wav"]
            if (project.audio_export_mode != "clean_for_instagram" and audio_plan.get("mixed_wav") and Path(audio_plan["mixed_wav"]).exists())
            else audio_plan["clean_wav"]
        )

        raw_clip = tmp_dir / "scene_raw.mp4"
        ov_p = Path(sc.overlay_image_path) if getattr(sc, "overlay_image_path", None) else None
        use_ov = bool(
            ov_p and ov_p.exists()
            and getattr(sc, "visual_layout", "auto") != "full_media"
            and getattr(sc, "mockup_style", "auto") != "none"
        )

        if sc.video_path and Path(sc.video_path).exists():
            if use_ov and ov_p is not None:
                _render_blended_scene_clip(
                    video_path=Path(sc.video_path),
                    overlay_img_path=ov_p,
                    scene=sc,
                    duration_sec=dur,
                    out_mp4=raw_clip,
                    accent_color=project.accent_color,
                    render_work_dir=tmp_dir,
                    project=project,
                )
            else:
                _render_scene_video_clip(
                    Path(sc.video_path),
                    dur,
                    sc.effect,
                    raw_clip,
                    project.accent_color,
                    seek_sec=float(getattr(sc, "video_start_sec", 0.0) or 0.0),
                )
        elif sc.image_path and Path(sc.image_path).exists():
            if use_ov and ov_p is not None:
                raw_img = tmp_dir / "scene_img.mp4"
                _render_scene_clip(Path(sc.image_path), dur, sc.effect, raw_img, project.accent_color)
                ok_ov, _ = _apply_motion_overlay_to_mp4(
                    base_mp4=raw_img,
                    overlay_img_path=ov_p,
                    scene=sc,
                    project=project,
                    duration_sec=dur,
                    out_mp4=raw_clip,
                    accent_color=project.accent_color,
                    render_work_dir=tmp_dir,
                    layout_mode="pip_card" if (sc.visual_layout or "auto") == "auto" else sc.visual_layout,
                )
                if not ok_ov:
                    raw_img.replace(raw_clip)
            else:
                _render_scene_clip(Path(sc.image_path), dur, sc.effect, raw_clip, project.accent_color)
        else:
            raise RuntimeError("Cena sem vídeo nem imagem de fundo para prévia.")

        overlay_png = _build_reel1_overlay(project, tmp_dir / "overlay.png")
        generate_ass_subtitles(
            word_timings=audio_plan["word_timings"],
            scenes=[sc],
            scene_spans=audio_plan["scene_spans"],
            out_ass_path=tmp_dir / "subtitles.ass",
            subtitle_style=project.subtitle_style or ("vice_city" if is_gaming else "gold_essay"),
            accent_color=project.accent_color,
            safe_zone="youtube_shorts" if is_gaming else "reels",
        )
        styled_mp4 = tmp_dir / "scene_styled.mp4"
        res_st = subprocess.run(
            [
                ffmpeg, "-y",
                "-i", raw_clip.name,
                "-i", overlay_png.name,
                "-filter_complex", "[0:v][1:v]overlay=0:0,ass='subtitles.ass',scale=out_range=tv,format=yuv420p[v]",
                "-map", "[v]",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                styled_mp4.name,
            ],
            cwd=str(tmp_dir),
            capture_output=True,
            text=True,
        )
        if res_st.returncode != 0:
            raise RuntimeError(f"Erro ao aplicar legendas na prévia: {res_st.stderr[-300:]}")

        res_mux = subprocess.run(
            [
                ffmpeg, "-y",
                "-i", str(styled_mp4),
                "-i", str(audio_chosen),
                "-t", f"{dur:.3f}",
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                str(out_mp4),
            ],
            capture_output=True,
            text=True,
        )
        if res_mux.returncode != 0:
            raise RuntimeError(f"Erro ao muxar áudio na prévia: {res_mux.stderr[-300:]}")
        return out_mp4
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
