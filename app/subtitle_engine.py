from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.config import (
    SAFE_ZONE_BOTTOM_PX,
    SAFE_ZONE_LEFT_PX,
    SAFE_ZONE_RIGHT_PX,
    SAFE_ZONE_TOP_PX,
)
from app.models import SceneItem, WordTiming


def _ass_color_from_hex(hex_color: str, alpha: int = 0) -> str:
    """Convert #RRGGBB to ASS &HAABBGGRR format (alpha 0=opaque, 255=transparent)."""
    cleaned = hex_color.strip().lstrip("#")
    if len(cleaned) != 6:
        r, g, b = 255, 59, 48
    else:
        try:
            r = int(cleaned[0:2], 16)
            g = int(cleaned[2:4], 16)
            b = int(cleaned[4:6], 16)
        except ValueError:
            r, g, b = 255, 59, 48
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def _format_ass_time(seconds: float) -> str:
    s = max(0.0, seconds)
    hrs = int(s // 3600)
    mins = int((s % 3600) // 60)
    secs = s % 60.0
    cs = int(round((secs - int(secs)) * 100))
    whole_secs = int(secs)
    if cs >= 100:
        cs -= 100
        whole_secs += 1
    return f"{hrs}:{mins:02d}:{whole_secs:02d}.{cs:02d}"


def _clean_token(word: str) -> str:
    return re.sub(r"[^\wÀ-ÿ]", "", word.lower())


def generate_ass_subtitles(
    word_timings: list[WordTiming],
    scenes: list[SceneItem],
    scene_spans: list[tuple[int, float, float]],
    out_ass_path: Path,
    subtitle_style: str = "viral_pop",
    accent_color: str = "#FF3B30",
    break_cues: list[dict[str, Any]] | None = None,
    safe_zone: str = "reels",
) -> Path:
    """
    Build dynamic word-highlighted .ass subtitles styled after top Shorts/Reels:
    - Positioned safely above Instagram Reels (`MarginV>=420`) or YouTube Shorts/TikTok (`MarginV>=485`, `MarginR=105`) UI
    - Active spoken word & scene keywords highlighted in accent/neon color
    - Subtle pop-in scale animation per phrase
    - Translated/original sung lyric captions (`LyricBreak` style) during lyric-cued musical breaks!
    """
    accent_ass = _ass_color_from_hex(accent_color, alpha=0)
    cyan_vice_ass = "&H00FFE500"   # #00E5FF Vice City electric cyan in BGR
    pink_vice_ass = "&H00932EFF"   # #FF2E93 Vice City neon pink in BGR
    white_ass = "&H00FFFFFF"
    gold_ass = "&H0024BFFA"  # warm amber/gold in BGR
    lyric_ass = "&H00F8FAFC"  # crisp warm ice-white for sung break translations
    black_ass = "&H00000000"
    shadow_ass = "&H90000000"

    if subtitle_style == "vice_city":
        font_name = "Impact"
        font_size = 64
        primary = white_ass
        highlight = cyan_vice_ass if accent_color.upper() == "#FF2E93" else accent_ass
        keyword_col = pink_vice_ass
        outline = 6
        margin_v = 485
        words_per_chunk = 3
    elif subtitle_style == "gold_essay":
        font_name = "Arial Black"
        font_size = 54
        primary = gold_ass
        highlight = white_ass
        keyword_col = white_ass
        outline = 5
        margin_v = 410
        words_per_chunk = 4
    elif subtitle_style == "clean_bold":
        font_name = "Arial"
        font_size = 58
        primary = white_ass
        highlight = accent_ass
        keyword_col = accent_ass
        outline = 5
        margin_v = 420
        words_per_chunk = 4
    else:  # viral_pop (default)
        font_name = "Impact"
        font_size = 66
        primary = white_ass
        highlight = accent_ass
        keyword_col = accent_ass
        outline = 6
        margin_v = 430
        words_per_chunk = 3

    margin_v = max(margin_v, SAFE_ZONE_BOTTOM_PX + 90)
    margin_l = max(64, SAFE_ZONE_LEFT_PX)
    margin_r = max(64, SAFE_ZONE_RIGHT_PX + 4)
    if safe_zone == "youtube_shorts" or subtitle_style == "vice_city":
        margin_v = max(margin_v, SAFE_ZONE_BOTTOM_PX + 120, 485)
        margin_l = max(72, SAFE_ZONE_LEFT_PX + 12)
        margin_r = max(108, SAFE_ZONE_RIGHT_PX + 48)  # Clears YouTube Shorts / TikTok right-edge Like/Comment/Share column

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},{primary},{accent_ass},{black_ass},{shadow_ass},-1,0,0,0,100,100,1,0,1,{outline},3,2,{margin_l},{margin_r},{margin_v},1
Style: LyricBreak,Arial,48,{lyric_ass},{gold_ass},{black_ass},{shadow_ass},-1,-1,0,0,100,100,0.5,0,1,5,2,2,72,72,415,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    # Build lookup of scene highlight keywords per time span
    scene_map = {s.index: {_clean_token(w) for w in s.highlight_words if _clean_token(w)} for s in scenes}

    def keywords_at(t: float) -> set[str]:
        for idx, s0, s1 in scene_spans:
            if s0 <= t <= s1:
                return scene_map.get(idx, set())
        return set()

    # Group words into punchy 2-4 word display chunks, breaking on punctuation or pauses
    chunks: list[list[WordTiming]] = []
    current: list[WordTiming] = []
    for wt in word_timings:
        if current:
            gap = wt.start - current[-1].end
            prev_ends_sentence = current[-1].word.endswith((".", "!", "?", ",", ";", ":"))
            if len(current) >= words_per_chunk or gap > 0.35 or prev_ends_sentence:
                chunks.append(current)
                current = []
        current.append(wt)
    if current:
        chunks.append(current)

    events: list[str] = []
    for c_idx, chunk in enumerate(chunks):
        chunk_end_cap = chunks[c_idx + 1][0].start if c_idx + 1 < len(chunks) else chunk[-1].end + 0.15
        kw_set = keywords_at(chunk[0].start)

        for active_i, active_wt in enumerate(chunk):
            seg_start = active_wt.start
            if active_i + 1 < len(chunk):
                seg_end = max(seg_start + 0.05, chunk[active_i + 1].start)
            else:
                seg_end = max(seg_start + 0.08, min(active_wt.end + 0.08, chunk_end_cap))

            styled_words: list[str] = []
            for j, w in enumerate(chunk):
                display = w.word.upper() if subtitle_style in {"viral_pop", "vice_city"} else w.word
                # Escape ASS braces
                display = display.replace("{", "(").replace("}", ")")
                is_active = j == active_i
                is_kw = _clean_token(w.word) in kw_set
                if is_active:
                    styled_words.append(f"{{\\c{highlight}\\fscx108\\fscy108}}{display}{{\\c{primary}\\fscx100\\fscy100}}")
                elif is_kw:
                    styled_words.append(f"{{\\c{keyword_col}}}{display}{{\\c{primary}}}")
                else:
                    styled_words.append(display)

            line_text = " ".join(styled_words)
            if active_i == 0 and subtitle_style in {"viral_pop", "vice_city"}:
                line_text = "{\\fscx92\\fscy92\\t(0,85,\\fscx100\\fscy100)}" + line_text

            events.append(
                f"Dialogue: 0,{_format_ass_time(seg_start)},{_format_ass_time(seg_end)},Default,,0,0,0,,{line_text}"
            )

    # Render translated / bracketed sung lyric lines smoothly during vocal Musical Breaks
    for bc in break_cues or []:
        raw_lyric = str(bc.get("lyric_pt") or bc.get("lyric_en") or "").strip()
        if not raw_lyric:
            continue
        b_st = float(bc.get("start", 0.0)) + 0.12
        b_en = float(bc.get("end", 0.0)) - 0.10
        if b_en - b_st < 0.6:
            continue

        # Split combined LRC lines cleanly on "/" or long comma phrasing
        inner = raw_lyric.strip()
        has_parens = inner.startswith("(") and inner.endswith(")")
        body = inner[1:-1].strip() if has_parens else inner
        parts = [p.strip() for p in re.split(r"\s*/\s*", body) if p.strip()]
        if len(parts) == 1 and len(parts[0].split()) > 7:
            words_p = parts[0].split()
            mid = len(words_p) // 2
            parts = [" ".join(words_p[:mid]), " ".join(words_p[mid:])]

        n_parts = max(1, len(parts))
        total_span = b_en - b_st
        # Proportionally time each lyric phrase by character length
        weights = [max(4, len(p)) for p in parts]
        w_sum = float(sum(weights) or n_parts)
        cur_t = b_st
        for p_idx, (phrase, w_val) in enumerate(zip(parts, weights)):
            p_dur = total_span * (w_val / w_sum)
            t0 = cur_t
            t1 = b_en if p_idx == n_parts - 1 else min(b_en, cur_t + p_dur)
            cur_t = t1
            if not phrase:
                continue
            disp = f"({phrase})" if (has_parens or not phrase.startswith("(")) else phrase
            disp = disp.replace("{", "(").replace("}", ")")
            # Auto-wrap lines longer than ~34 chars cleanly onto 2 lines
            if len(disp) > 34:
                ws = disp.split()
                half = max(1, len(ws) // 2)
                disp = " ".join(ws[:half]) + r"\N" + " ".join(ws[half:])
            line_ass = "{\\fad(110,110)}" + disp
            events.append(
                f"Dialogue: 1,{_format_ass_time(t0)},{_format_ass_time(t1)},LyricBreak,,0,0,0,,{line_ass}"
            )

    out_ass_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return out_ass_path
