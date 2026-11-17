from __future__ import annotations

import asyncio
import base64
import functools
import http.server
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

import httpx
from PIL import Image
import websockets

from app.config import get_ffmpeg_exe
from app.html_renderer import find_headless_browser
from app.models import MusicStoryProject, SceneItem


W = 1080
STAGE_Y_OFFSET = 80  # Stage starts below status bar safe margin; ends well above y=1280 subtitles

MOCKUP_STYLES = (
    "auto",
    "smartphone_reels",
    "news_portal",
    "social_post",
    "hud_radar",
    "floating_glass",
    "none",
)

CONCRETE_MOCKUPS = (
    "smartphone_reels",
    "news_portal",
    "social_post",
    "hud_radar",
    "floating_glass",
)

MOTION_PRESETS = (
    "auto",
    "spring_pop_tilt",
    "whip_pan_card",
    "kinetic_slam",
    "float_3d_orbit",
    "basic_fade",
)

CONCRETE_MOTIONS = (
    "spring_pop_tilt",
    "whip_pan_card",
    "kinetic_slam",
    "float_3d_orbit",
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return


def _hex_to_rgb(hex_color: str, default: tuple[int, int, int] = (255, 46, 147)) -> tuple[int, int, int]:
    cleaned = (hex_color or "").strip().lstrip("#")
    if len(cleaned) == 6:
        try:
            return int(cleaned[0:2], 16), int(cleaned[2:4], 16), int(cleaned[4:6], 16)
        except ValueError:
            pass
    return default


def resolve_scene_mockup_and_motion(
    scene: SceneItem,
    project: MusicStoryProject | None = None,
    layout_mode: str = "pip_card",
) -> tuple[str, str]:
    """
    Resolve `'auto'` into a concrete Mockup Style and Motion Design 2.0 preset
    matched semantically to the scene's narration while rotating cleanly across scenes.
    """
    req_mockup = (getattr(scene, "mockup_style", "auto") or "auto").strip()
    req_motion = (getattr(scene, "motion_preset", "auto") or "auto").strip()

    idx = max(0, int(getattr(scene, "index", 1) or 1) - 1)
    niche = getattr(project, "niche", "gaming_news") if project else "gaming_news"
    blob = " ".join([
        scene.title or "",
        scene.narration_text or "",
        getattr(scene, "overlay_label", "") or "",
        getattr(scene, "shot_intent", "") or "",
    ]).lower()

    if req_mockup in MOCKUP_STYLES and req_mockup != "auto":
        mockup = req_mockup
    else:
        if any(k in blob for k in ("twitter", "post", "reddit", "rede social", "comunidade", "fã", "viraliz", "coment", "threads")):
            mockup = "social_post"
        elif any(k in blob for k in ("deepfake", "falso", "ia ", "mapa", "leonida", "ps5", "xbox", "pc", "fps", "gráfico", "vazam", "leak", "radar", "motor", "engine")):
            mockup = "hud_radar"
        elif any(k in blob for k in ("trailer", "celular", "shorts", "reels", "clipe", "tiktok", "gameplay")):
            mockup = "smartphone_reels"
        elif any(k in blob for k in ("notícia", "matéria", "imprensa", "anúncio", "data", "adiamento", "take-two", "rockstar", "lançamento", "bilhão")):
            mockup = "news_portal"
        elif niche == "music_history":
            cycle_m = ("floating_glass", "news_portal", "smartphone_reels", "hud_radar", "social_post")
            mockup = cycle_m[idx % len(cycle_m)]
        else:
            # Rotate deterministically so consecutive scenes look distinct
            cycle_g = ("smartphone_reels", "news_portal", "hud_radar", "social_post", "floating_glass")
            mockup = cycle_g[idx % len(cycle_g)]

        # If split_top_image & wide card, prefer horizontal/landscape-friendly mockups over vertical phone frame
        if layout_mode == "split_top_image" and mockup == "smartphone_reels":
            mockup = "news_portal" if idx % 2 == 0 else "hud_radar"

    if req_motion in MOTION_PRESETS and req_motion != "auto":
        motion = req_motion
    else:
        default_by_mockup = {
            "smartphone_reels": ("spring_pop_tilt", "float_3d_orbit"),
            "news_portal": ("whip_pan_card", "kinetic_slam"),
            "social_post": ("spring_pop_tilt", "whip_pan_card"),
            "hud_radar": ("kinetic_slam", "float_3d_orbit"),
            "floating_glass": ("float_3d_orbit", "spring_pop_tilt"),
        }
        pair = default_by_mockup.get(mockup, CONCRETE_MOTIONS[:2])
        motion = pair[idx % len(pair)]

    return mockup, motion


def _compute_stage_box(
    layout_mode: str,
    overlay_pos: str,
    overlay_scale: float,
    img_w: int,
    img_h: int,
    mockup_style: str,
) -> tuple[int, int, int, int, int]:
    """
    Compute `(stage_w, stage_h, card_w, left_px, top_px)` within the transparent capture viewport.
    Everything sits safely above y=1260 so bottom YouTube/IG subtitles remain completely unobstructed.
    """
    scale_pct = max(0.28, min(0.88, float(overlay_scale or 0.56)))
    use_tall = (layout_mode == "broll_cutaway") or (overlay_pos == "center")
    stage_h = 1140 if use_tall else 760

    if mockup_style == "smartphone_reels":
        card_w = int(min(500, max(330, W * (scale_pct * 0.72))))
        media_h = int(min(540 if use_tall else 420, round(card_w * 1.15)))
    elif layout_mode == "split_top_image":
        card_w = int(min(980, max(620, W * (0.48 + scale_pct * 0.68))))
        media_h = int(min(375, round(card_w * 0.45)))
    elif layout_mode == "broll_cutaway":
        card_w = int(min(980, max(640, W * (0.50 + scale_pct * 0.64))))
        media_h = int(min(520, round(card_w * 0.54)))
    else:  # pip_card
        card_w = int(min(940, max(410, W * scale_pct)))
        aspect = img_h / float(max(1, img_w))
        raw_h = int(round(card_w * min(0.85, max(0.48, aspect))))
        media_h = int(min(400 if not use_tall else 520, max(230, raw_h)))

    extra_chrome_h = 122 if mockup_style in {"news_portal", "social_post"} else (88 if mockup_style == "smartphone_reels" else 76)
    est_card_h = media_h + extra_chrome_h

    if overlay_pos == "top_left":
        left_px = 42
    elif overlay_pos == "top_right":
        left_px = max(42, W - card_w - 42)
    else:
        left_px = (W - card_w) // 2

    if layout_mode == "split_top_image":
        # Anchor right above gameplay at y=655 (so stage bottom y = 655 - STAGE_Y_OFFSET - 14 = 561)
        top_px = max(56, 558 - est_card_h)
    elif use_tall:
        top_px = max(140, min(stage_h - est_card_h - 45, (stage_h - est_card_h) // 2 + 18))
    else:
        top_px = max(72, min(stage_h - est_card_h - 45, 92))

    return stage_h, card_w, media_h, left_px, top_px


def build_motion_overlay_html(
    image_rel_url: str,
    label_text: str,
    accent_color: str,
    duration_sec: float,
    mockup_style: str,
    motion_preset: str,
    layout_mode: str = "pip_card",
    overlay_pos: str = "top_center",
    overlay_scale: float = 0.56,
    img_w: int = 960,
    img_h: int = 540,
    niche: str = "gaming_news",
    hide_at_end: bool = True,
    fps: int = 30,
) -> tuple[str, int]:
    """
    Build deterministic HyperFrames-compliant transparent HTML5/CSS3D overlay animation
    for Mockup + PromptMotion compositing.
    Returns `(html_string, stage_height)`.
    """
    r, g, b = _hex_to_rgb(accent_color)
    stage_h, card_w, media_h, left_px, top_px = _compute_stage_box(
        layout_mode=layout_mode,
        overlay_pos=overlay_pos,
        overlay_scale=overlay_scale,
        img_w=img_w,
        img_h=img_h,
        mockup_style=mockup_style,
    )

    safe_label = (label_text or ("RADAR • VICE CITY" if niche == "gaming_news" else "ARQUIVO HISTÓRICO")).strip().upper()[:38]
    handle_txt = "@RadarGamesBR" if niche == "gaming_news" else "@HistoriaRockBR"
    portal_domain = "gamesradar.br/exclusivo" if niche == "gaming_news" else "arquivo.musica.br"
    kicker_pill = "PLANTÃO URGENTE" if niche == "gaming_news" else "DOCUMENTO HISTÓRICO"
    whip_dir = -1 if overlay_pos == "top_left" else 1

    css_accent = f"rgb({r}, {g}, {b})"
    css_accent_soft = f"rgba({r}, {g}, {b}, 0.28)"
    css_accent_glow = f"rgba({r}, {g}, {b}, 0.52)"

    esc_img = image_rel_url.replace('"', "%22")
    esc_label = (
        safe_label.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    esc_handle = handle_txt.replace("&", "&amp;").replace("<", "&lt;")

    if mockup_style == "smartphone_reels":
        mockup_inner = f"""
        <div class="phone-shell">
          <div class="phone-bezel">
            <div class="dynamic-island">
              <span class="cam-dot"></span>
              <span class="live-tag">REC • HD</span>
            </div>
            <div class="media-stage">
              <img id="heroImg" src="{esc_img}" alt="" />
              <div class="vignette-overlay"></div>
              <div class="sheen-beam" id="sheenBeam"></div>
              <div class="phone-bottom-bar">
                <div class="phone-badge-pill">{esc_label}</div>
                <div class="phone-likes">
                  <svg viewBox="0 0 24 24" width="18" height="18"><path fill="{css_accent}" d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>
                  <span id="metricVal">14.2K</span>
                </div>
              </div>
            </div>
            <div class="home-indicator"></div>
          </div>
        </div>
        """
    elif mockup_style == "news_portal":
        mockup_inner = f"""
        <div class="portal-shell">
          <div class="portal-topbar">
            <div class="portal-live">
              <span class="pulse-dot"></span>
              <span>{kicker_pill}</span>
            </div>
            <span class="portal-domain">{portal_domain}</span>
          </div>
          <div class="media-stage">
            <img id="heroImg" src="{esc_img}" alt="" />
            <div class="sheen-beam" id="sheenBeam"></div>
            <div class="progress-rail"><div class="progress-bar" id="progressBar"></div></div>
          </div>
          <div class="portal-footer">
            <span class="portal-kicker-tag">EVIDÊNCIA VISUAL</span>
            <div class="portal-headline">{esc_label}</div>
          </div>
        </div>
        """
    elif mockup_style == "social_post":
        mockup_inner = f"""
        <div class="social-shell">
          <div class="social-head">
            <div class="avatar-ring"><span>{esc_handle[1:3].upper()}</span></div>
            <div class="author-meta">
              <div class="author-row">
                <strong>{esc_handle}</strong>
                <svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#00E5FF"/><path d="M9.5 12.5l1.8 1.8 3.6-4.2" stroke="#061018" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>
                <span class="post-time">• agora</span>
              </div>
              <div class="social-headline">{esc_label}</div>
            </div>
          </div>
          <div class="media-stage social-media">
            <img id="heroImg" src="{esc_img}" alt="" />
            <div class="sheen-beam" id="sheenBeam"></div>
          </div>
          <div class="social-foot">
            <span class="soc-metric"><svg viewBox="0 0 24 24" width="16" height="16"><path fill="none" stroke="#94a3b8" stroke-width="2" d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg><b id="replyVal">418</b></span>
            <span class="soc-metric"><svg viewBox="0 0 24 24" width="16" height="16"><path fill="none" stroke="#22c55e" stroke-width="2.2" d="M17 1l4 4-4 4"/><path fill="none" stroke="#22c55e" stroke-width="2" d="M3 11V9a4 4 0 0 1 4-4h14"/><path fill="none" stroke="#22c55e" stroke-width="2.2" d="M7 23l-4-4 4-4"/><path fill="none" stroke="#22c55e" stroke-width="2" d="M21 13v2a4 4 0 0 1-4 4H3"/></svg><b id="repostVal">1.9K</b></span>
            <span class="soc-metric"><svg viewBox="0 0 24 24" width="16" height="16"><path fill="{css_accent}" d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg><b id="metricVal">28.4K</b></span>
            <span class="social-tag">VIRAL</span>
          </div>
        </div>
        """
    elif mockup_style == "hud_radar":
        mockup_inner = f"""
        <div class="hud-shell">
          <div class="hud-corner tl"></div>
          <div class="hud-corner tr"></div>
          <div class="hud-corner bl"></div>
          <div class="hud-corner br"></div>
          <div class="hud-top">
            <span class="hud-tag">SCAN // ALVO DETECTADO</span>
            <span class="hud-coords" id="hudCoords">CAM-04 • 60FPS</span>
          </div>
          <div class="media-stage hud-media">
            <img id="heroImg" src="{esc_img}" alt="" />
            <div class="hud-grid"></div>
            <div class="hud-scanline" id="hudScanline"></div>
          </div>
          <div class="hud-bottom">
            <span class="hud-title">{esc_label}</span>
            <div class="hud-bars"><i></i><i></i><i></i><i></i></div>
          </div>
        </div>
        """
    else:  # floating_glass
        mockup_inner = f"""
        <div class="glass-shell">
          <div class="glass-rim" id="glassRim"></div>
          <div class="glass-inner">
            <div class="media-stage glass-media">
              <img id="heroImg" src="{esc_img}" alt="" />
              <div class="vignette-overlay"></div>
              <div class="sheen-beam" id="sheenBeam"></div>
              <div class="glass-floating-pill">
                <span class="pulse-dot"></span>
                <span>{esc_label}</span>
              </div>
            </div>
          </div>
        </div>
        """

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8" />
<style>
  :root {{
    --accent: {css_accent};
    --accent-soft: {css_accent_soft};
    --accent-glow: {css_accent_glow};
    --cyan: #00E5FF;
    --card-w: {card_w}px;
    --media-h: {media_h}px;
    --rim-deg: 35deg;
  }}
  * {{
    box-sizing: border-box;
    margin: 0;
    padding: 0;
  }}
  html, body {{
    width: {W}px;
    height: {stage_h}px;
    overflow: hidden;
    background: transparent !important;
    font-family: "Segoe UI", "Inter", system-ui, -apple-system, sans-serif;
    -webkit-font-smoothing: antialiased;
  }}
  #stage {{
    position: relative;
    width: {W}px;
    height: {stage_h}px;
    background: transparent;
  }}
  #motionWrapper {{
    position: absolute;
    left: {left_px}px;
    top: {top_px}px;
    width: var(--card-w);
    transform-origin: center center;
    will-change: transform, opacity;
  }}
  #shockwave {{
    position: absolute;
    left: 50%;
    top: 50%;
    width: 100%;
    height: 100%;
    border-radius: 28px;
    border: 5px solid var(--cyan);
    box-shadow: 0 0 38px var(--accent-glow), inset 0 0 28px rgba(0,229,255,0.45);
    transform: translate(-50%, -50%) scale(0.8);
    opacity: 0;
    pointer-events: none;
  }}
  .media-stage {{
    position: relative;
    width: 100%;
    height: var(--media-h);
    overflow: hidden;
    background: #080A12;
  }}
  .media-stage img {{
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
    transform-origin: center center;
  }}
  .vignette-overlay {{
    position: absolute;
    inset: 0;
    background: linear-gradient(180deg, rgba(0,0,0,0.08) 0%, rgba(0,0,0,0) 45%, rgba(0,0,0,0.78) 100%);
    pointer-events: none;
  }}
  .sheen-beam {{
    position: absolute;
    top: -20%;
    left: -60%;
    width: 42%;
    height: 140%;
    background: linear-gradient(105deg, transparent 0%, rgba(255,255,255,0.22) 50%, transparent 100%);
    transform: skewX(-18deg);
    pointer-events: none;
  }}
  .pulse-dot {{
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--accent);
    box-shadow: 0 0 12px var(--accent);
    display: inline-block;
    flex-shrink: 0;
  }}

  /* 1. SMARTPHONE REELS MOCKUP */
  .phone-shell {{
    background: linear-gradient(145deg, #2b3040 0%, #10131b 55%, #232736 100%);
    border-radius: 36px;
    padding: 7px;
    box-shadow: 0 24px 55px rgba(0,0,0,0.78), 0 0 28px var(--accent-soft);
    border: 1px solid rgba(255,255,255,0.24);
  }}
  .phone-bezel {{
    background: #07080d;
    border-radius: 30px;
    padding: 10px 10px 12px;
    border: 1px solid rgba(255,255,255,0.09);
  }}
  .dynamic-island {{
    margin: 0 auto 8px;
    width: 132px;
    height: 24px;
    border-radius: 999px;
    background: #000;
    border: 1px solid rgba(255,255,255,0.12);
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 7px;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 0.7px;
    color: #cbd5e1;
  }}
  .cam-dot {{
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #22c55e;
    box-shadow: 0 0 8px #22c55e;
  }}
  .phone-bezel .media-stage {{
    border-radius: 20px;
  }}
  .phone-bottom-bar {{
    position: absolute;
    left: 12px;
    right: 12px;
    bottom: 12px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }}
  .phone-badge-pill {{
    background: rgba(10, 12, 20, 0.86);
    border: 1.5px solid var(--accent);
    color: #fff;
    padding: 5px 11px;
    border-radius: 999px;
    font-size: 12.5px;
    font-weight: 800;
    letter-spacing: 0.4px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}
  .phone-likes {{
    display: flex;
    align-items: center;
    gap: 4px;
    background: rgba(10, 12, 20, 0.85);
    padding: 5px 10px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 800;
    color: #fff;
  }}
  .home-indicator {{
    width: 108px;
    height: 4px;
    border-radius: 999px;
    background: rgba(255,255,255,0.34);
    margin: 9px auto 0;
  }}

  /* 2. NEWS PORTAL MOCKUP */
  .portal-shell {{
    background: rgba(11, 14, 23, 0.94);
    border-radius: 22px;
    overflow: hidden;
    border: 2px solid var(--accent);
    box-shadow: 0 24px 52px rgba(0,0,0,0.78), 0 0 26px var(--accent-soft);
  }}
  .portal-topbar {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 9px 15px;
    background: linear-gradient(90deg, rgba(20, 24, 38, 0.98) 0%, rgba(11, 14, 23, 0.98) 100%);
    border-bottom: 1px solid rgba(255,255,255,0.1);
  }}
  .portal-live {{
    display: flex;
    align-items: center;
    gap: 7px;
    font-size: 12.5px;
    font-weight: 900;
    letter-spacing: 0.7px;
    color: #fff;
  }}
  .portal-domain {{
    font-size: 11.5px;
    font-weight: 700;
    color: var(--cyan);
    background: rgba(0, 229, 255, 0.12);
    border: 1px solid rgba(0, 229, 255, 0.35);
    border-radius: 999px;
    padding: 3px 10px;
  }}
  .progress-rail {{
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    height: 4px;
    background: rgba(255,255,255,0.14);
  }}
  .progress-bar {{
    height: 100%;
    width: 0%;
    background: linear-gradient(90deg, var(--accent), var(--cyan));
  }}
  .portal-footer {{
    padding: 11px 16px 13px;
    display: flex;
    align-items: center;
    gap: 11px;
  }}
  .portal-kicker-tag {{
    background: var(--accent);
    color: #fff;
    font-size: 10.5px;
    font-weight: 900;
    padding: 4px 8px;
    border-radius: 6px;
    letter-spacing: 0.5px;
    flex-shrink: 0;
  }}
  .portal-headline {{
    font-size: 18px;
    font-weight: 900;
    color: #f8fafc;
    letter-spacing: 0.3px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}

  /* 3. SOCIAL POST MOCKUP */
  .social-shell {{
    background: rgba(13, 16, 26, 0.94);
    border-radius: 22px;
    padding: 13px 15px;
    border: 1.5px solid rgba(255,255,255,0.18);
    box-shadow: 0 24px 55px rgba(0,0,0,0.78), 0 0 24px var(--accent-soft);
  }}
  .social-head {{
    display: flex;
    align-items: center;
    gap: 11px;
    margin-bottom: 10px;
  }}
  .avatar-ring {{
    width: 42px;
    height: 42px;
    border-radius: 50%;
    background: conic-gradient(from var(--rim-deg), var(--accent), var(--cyan), var(--accent));
    padding: 2.5px;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }}
  .avatar-ring span {{
    width: 100%;
    height: 100%;
    border-radius: 50%;
    background: #0d101a;
    color: #fff;
    font-size: 13px;
    font-weight: 900;
    display: flex;
    align-items: center;
    justify-content: center;
  }}
  .author-meta {{
    min-width: 0;
    flex: 1;
  }}
  .author-row {{
    display: flex;
    align-items: center;
    gap: 5px;
    color: #fff;
    font-size: 14px;
  }}
  .post-time {{
    color: #94a3b8;
    font-size: 12px;
  }}
  .social-headline {{
    color: #e2e8f0;
    font-size: 16px;
    font-weight: 800;
    margin-top: 2px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}
  .social-media {{
    border-radius: 14px;
    border: 1px solid rgba(255,255,255,0.12);
  }}
  .social-foot {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding-top: 9px;
    color: #cbd5e1;
    font-size: 13px;
    font-weight: 700;
  }}
  .soc-metric {{
    display: inline-flex;
    align-items: center;
    gap: 5px;
  }}
  .social-tag {{
    background: var(--accent-soft);
    color: #fff;
    border: 1px solid var(--accent);
    padding: 2px 9px;
    border-radius: 999px;
    font-size: 10.5px;
    font-weight: 900;
  }}

  /* 4. HUD RADAR MOCKUP */
  .hud-shell {{
    position: relative;
    background: rgba(8, 12, 22, 0.91);
    border-radius: 18px;
    padding: 10px 12px;
    border: 1.5px solid rgba(0, 229, 255, 0.55);
    box-shadow: 0 24px 55px rgba(0,0,0,0.82), 0 0 30px rgba(0, 229, 255, 0.25);
  }}
  .hud-corner {{
    position: absolute;
    width: 22px;
    height: 22px;
    border-color: var(--accent);
    border-style: solid;
    z-index: 4;
  }}
  .hud-corner.tl {{ top: -3px; left: -3px; border-width: 4px 0 0 4px; border-radius: 6px 0 0 0; }}
  .hud-corner.tr {{ top: -3px; right: -3px; border-width: 4px 4px 0 0; border-radius: 0 6px 0 0; }}
  .hud-corner.bl {{ bottom: -3px; left: -3px; border-width: 0 0 4px 4px; border-radius: 0 0 0 6px; }}
  .hud-corner.br {{ bottom: -3px; right: -3px; border-width: 0 4px 4px 0; border-radius: 0 0 6px 0; }}
  .hud-top {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 7px;
    font-family: "Consolas", "Courier New", monospace;
    font-size: 12px;
    font-weight: 800;
  }}
  .hud-tag {{ color: var(--cyan); letter-spacing: 0.8px; }}
  .hud-coords {{ color: #f9a8d4; }}
  .hud-media {{
    border-radius: 10px;
    border: 1px solid rgba(0,229,255,0.35);
  }}
  .hud-grid {{
    position: absolute;
    inset: 0;
    background-image: linear-gradient(rgba(0,229,255,0.08) 1px, transparent 1px),
                      linear-gradient(90deg, rgba(0,229,255,0.08) 1px, transparent 1px);
    background-size: 36px 36px;
    pointer-events: none;
  }}
  .hud-scanline {{
    position: absolute;
    left: 0;
    right: 0;
    top: 0;
    height: 6px;
    background: linear-gradient(90deg, transparent, var(--cyan), var(--accent), transparent);
    box-shadow: 0 0 22px 4px rgba(0, 229, 255, 0.72);
    pointer-events: none;
  }}
  .hud-bottom {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-top: 8px;
  }}
  .hud-title {{
    color: #fff;
    font-size: 16.5px;
    font-weight: 900;
    letter-spacing: 0.6px;
  }}
  .hud-bars {{
    display: flex;
    gap: 4px;
    align-items: flex-end;
    height: 16px;
  }}
  .hud-bars i {{
    width: 5px;
    border-radius: 2px;
    background: var(--cyan);
    display: block;
    height: 10px;
  }}

  /* 5. FLOATING GLASS 3D MOCKUP */
  .glass-shell {{
    position: relative;
    border-radius: 24px;
    padding: 3.5px;
    background: conic-gradient(from var(--rim-deg), var(--accent), var(--cyan), #ffffff, var(--accent));
    box-shadow: 0 28px 60px rgba(0,0,0,0.82), 0 0 32px var(--accent-soft);
  }}
  .glass-inner {{
    background: rgba(11, 13, 21, 0.95);
    border-radius: 21px;
    padding: 8px;
  }}
  .glass-media {{
    border-radius: 16px;
  }}
  .glass-floating-pill {{
    position: absolute;
    left: 14px;
    bottom: 14px;
    display: inline-flex;
    align-items: center;
    gap: 8px;
    background: rgba(10, 12, 20, 0.88);
    border: 1.5px solid rgba(255,255,255,0.28);
    padding: 7px 14px;
    border-radius: 999px;
    color: #fff;
    font-size: 15px;
    font-weight: 900;
    letter-spacing: 0.4px;
  }}
</style>
</head>
<body>
  <div id="stage">
    <div id="motionWrapper">
      <div id="shockwave"></div>
      {mockup_inner}
    </div>
  </div>

<script>
  const DURATION = {float(duration_sec):.4f};
  const PRESET = {json.dumps(motion_preset)};
  const WHIP_DIR = {whip_dir};
  const HIDE_AT_END = {"true" if hide_at_end else "false"};

  function clamp01(x) {{ return Math.max(0, Math.min(1, x)); }}
  function easeOutCubic(u) {{ return 1 - Math.pow(1 - clamp01(u), 3); }}
  function easeInCubic(u) {{ const c = clamp01(u); return c * c * c; }}

  window.film = {{
    width: {W},
    height: {stage_h},
    duration: DURATION,
    fps: {int(fps)},
    ready: async () => {{
      const img = document.getElementById("heroImg");
      if (img && img.decode) {{
        try {{ await img.decode(); }} catch (_) {{}}
      }}
      if (document.fonts && document.fonts.ready) {{
        await document.fonts.ready;
      }}
    }},
    frame: (t) => {{
      const wrapper = document.getElementById("motionWrapper");
      const heroImg = document.getElementById("heroImg");
      const sheen = document.getElementById("sheenBeam");
      const shock = document.getElementById("shockwave");
      const progress = document.getElementById("progressBar");
      const scanline = document.getElementById("hudScanline");
      const root = document.documentElement;

      const uNorm = clamp01(t / Math.max(0.2, DURATION));

      // Continuous conic neon border angle (PromptMotion / 3D glow)
      root.style.setProperty("--rim-deg", `${{(t * 95) % 360}}deg`);

      // Smooth Ken Burns zoom inside mockup media screen
      if (heroImg) {{
        const zoom = 1.0 + 0.10 * uNorm;
        const panX = Math.sin(t * 0.9) * 1.4;
        heroImg.style.transform = `scale(${{zoom.toFixed(4)}}) translateX(${{panX.toFixed(2)}}%)`;
      }}

      // Repeating diagonal specular light sweep every ~2.1s
      if (sheen) {{
        const phase = (t % 2.15) / 0.85;
        if (phase <= 1.0) {{
          const sx = -70 + phase * 310;
          sheen.style.left = `${{sx.toFixed(1)}}%`;
          sheen.style.opacity = String(Math.sin(phase * Math.PI) * 0.85);
        }} else {{
          sheen.style.opacity = "0";
        }}
      }}

      // News progress bar
      if (progress) {{
        progress.style.width = `${{(uNorm * 100).toFixed(2)}}%`;
      }}

      // HUD laser scanline & pulsing mini telemetry bars
      if (scanline) {{
        const scanU = 0.5 - 0.5 * Math.cos(t * 2.2);
        scanline.style.top = `${{(scanU * 94).toFixed(2)}}%`;
        document.querySelectorAll(".hud-bars i").forEach((bar, bIdx) => {{
          const h = 5 + 11 * (0.5 + 0.5 * Math.sin(t * 6.5 + bIdx * 1.3));
          bar.style.height = `${{h.toFixed(1)}}px`;
        }});
      }}

      // Animated live engagement counter for social / phone mockups
      const mVal = document.getElementById("metricVal");
      if (mVal) {{
        const kVal = 28.4 + t * 0.65;
        mVal.textContent = `${{kVal.toFixed(1)}}K`;
      }}

      let tx = 0, ty = 0, sc = 1, sk = 0, rx = 0, ry = 0, op = 1;

      if (PRESET === "spring_pop_tilt") {{
        const uIn = clamp01(t / 0.46);
        const spring = uIn >= 1 ? 1 : (1 - Math.exp(-7.2 * uIn) * Math.cos(11.5 * uIn));
        ty = (1 - spring) * 76 + Math.sin(t * 2.2) * 6;
        sc = 0.62 + 0.38 * spring;
        rx = (1 - spring) * 16 + Math.cos(t * 1.4) * 3.0;
        ry = (1 - spring) * -12 + Math.sin(t * 1.6) * 5.2;
        op = clamp01(t / 0.14);
      }} else if (PRESET === "whip_pan_card") {{
        const uIn = easeOutCubic(t / 0.34);
        tx = WHIP_DIR * (1 - uIn) * 480;
        sk = WHIP_DIR * (1 - uIn) * -14;
        ty = Math.sin(t * 2.0) * 5;
        ry = WHIP_DIR * (1 - uIn) * 18 + Math.sin(t * 1.5) * 3.5;
        sc = 0.90 + 0.10 * uIn;
        op = clamp01(t / 0.10);
      }} else if (PRESET === "kinetic_slam") {{
        const slamT = clamp01(t / 0.24);
        if (slamT < 1) {{
          const e = slamT * slamT;
          sc = 1.46 - 0.46 * e;
          op = clamp01(t / 0.10);
          rx = (1 - e) * -14;
        }} else {{
          const settle = clamp01((t - 0.24) / 0.22);
          const bounce = Math.sin(settle * Math.PI * 2) * Math.exp(-4.5 * settle);
          sc = 1.0 - bounce * 0.04;
          ty = bounce * 8 + Math.sin(t * 2.0) * 4;
          ry = Math.sin(t * 1.5) * 3.5;
        }}
        if (shock) {{
          if (t >= 0.20 && t <= 0.72) {{
            const su = clamp01((t - 0.20) / 0.52);
            shock.style.transform = `translate(-50%, -50%) scale(${{(0.95 + su * 0.42).toFixed(3)}})`;
            shock.style.opacity = String((1 - su) * 0.92);
          }} else {{
            shock.style.opacity = "0";
          }}
        }}
      }} else {{
        // float_3d_orbit
        const uIn = easeOutCubic(t / 0.42);
        sc = 0.84 + 0.16 * uIn;
        ty = (1 - uIn) * 42 + Math.sin(t * 1.9) * 8;
        ry = Math.sin(t * 1.75) * 8.5;
        rx = Math.cos(t * 1.35) * 4.2;
        op = clamp01(t / 0.18);
      }}

      // Smooth exit fade+drop if card hides before scene ends
      const rem = DURATION - t;
      if (HIDE_AT_END && DURATION > 1.0 && rem < 0.24) {{
        const uOut = easeInCubic(1 - clamp01(rem / 0.24));
        op *= (1 - uOut);
        sc *= (1 - 0.09 * uOut);
        ty += 26 * uOut;
      }}

      wrapper.style.opacity = op.toFixed(3);
      wrapper.style.transform =
        `perspective(1100px) translate3d(${{tx.toFixed(2)}}px, ${{ty.toFixed(2)}}px, 0px) ` +
        `scale(${{sc.toFixed(4)}}) skewX(${{sk.toFixed(2)}}deg) ` +
        `rotateX(${{rx.toFixed(2)}}deg) rotateY(${{ry.toFixed(2)}}deg)`;
    }},
  }};
</script>
</body>
</html>
"""
    return html, stage_h


async def _cdp_capture_transparent_mov(
    url: str,
    width: int,
    height: int,
    fps: int,
    n_frames: int,
    out_mov: Path,
    browser_exe: str,
) -> None:
    ffmpeg = get_ffmpeg_exe()
    cdp_port = _free_port()
    prof_dir = Path(tempfile.mkdtemp(prefix="md2_cdp_"))

    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW

    browser_proc = subprocess.Popen(
        [
            browser_exe,
            "--headless=new",
            "--disable-gpu-sandbox",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-crash-reporter",
            "--mute-audio",
            f"--remote-debugging-port={cdp_port}",
            f"--user-data-dir={prof_dir}",
            f"--window-size={width},{height}",
            "about:blank",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=flags,
    )

    ff_proc: subprocess.Popen[bytes] | None = None
    try:
        ws_url = None
        async with httpx.AsyncClient(timeout=1.5) as client:
            for _ in range(60):
                try:
                    resp = await client.get(f"http://127.0.0.1:{cdp_port}/json/list")
                    tabs = [t for t in resp.json() if t.get("type") == "page"]
                    if tabs:
                        ws_url = tabs[0]["webSocketDebuggerUrl"]
                        break
                except Exception:
                    await asyncio.sleep(0.08)
        if not ws_url:
            raise RuntimeError("Navegador headless CDP indisponível para Motion Design 2.0.")

        out_mov.parent.mkdir(parents=True, exist_ok=True)
        ff_cmd = [
            ffmpeg,
            "-y",
            "-f", "image2pipe",
            "-vcodec", "png",
            "-framerate", str(fps),
            "-i", "pipe:0",
            "-c:v", "png",
            "-pix_fmt", "rgba",
            "-an",
            str(out_mov),
        ]
        ff_proc = subprocess.Popen(ff_cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        assert ff_proc.stdin is not None

        async with websockets.connect(ws_url, max_size=64_000_000) as ws:
            mid = 0

            async def call(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
                nonlocal mid
                mid += 1
                req_id = mid
                await ws.send(json.dumps({"id": req_id, "method": method, "params": params or {}}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == req_id:
                        if "error" in msg:
                            raise RuntimeError(f"CDP {method}: {msg['error']}")
                        return msg.get("result", {})

            await call("Page.enable")
            await call(
                "Emulation.setDeviceMetricsOverride",
                {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False},
            )
            await call(
                "Emulation.setDefaultBackgroundColorOverride",
                {"color": {"r": 0, "g": 0, "b": 0, "a": 0}},
            )
            await call("Page.navigate", {"url": url})

            for _ in range(80):
                probe = await call(
                    "Runtime.evaluate",
                    {"expression": "Boolean(window.film && typeof window.film.frame === 'function')"},
                )
                if probe.get("result", {}).get("value") is True:
                    break
                await asyncio.sleep(0.08)

            await call(
                "Runtime.evaluate",
                {
                    "expression": (
                        "(async () => {"
                        "  if (window.film && window.film.ready) await window.film.ready();"
                        "  return true;"
                        "})()"
                    ),
                    "awaitPromise": True,
                },
            )

            for f_idx in range(n_frames):
                t = f_idx / float(fps)
                await call(
                    "Runtime.evaluate",
                    {
                        "expression": f"Promise.resolve(window.film.frame({t:.5f}))",
                        "awaitPromise": True,
                    },
                )
                snap = await call(
                    "Page.captureScreenshot",
                    {
                        "format": "png",
                        "omitBackground": True,
                        "clip": {"x": 0, "y": 0, "width": width, "height": height, "scale": 1},
                    },
                )
                ff_proc.stdin.write(base64.b64decode(snap["data"]))

        ff_proc.stdin.close()
        stderr_b = ff_proc.stderr.read() if ff_proc.stderr else b""
        rc = ff_proc.wait()
        if rc != 0:
            raise RuntimeError(f"FFmpeg overlay RGBA falhou: {stderr_b.decode('utf-8', errors='ignore')[-400:]}")
    finally:
        if ff_proc and ff_proc.poll() is None:
            try:
                ff_proc.kill()
            except Exception:
                pass
        try:
            browser_proc.terminate()
            browser_proc.wait(timeout=3)
        except Exception:
            try:
                browser_proc.kill()
            except Exception:
                pass
        shutil.rmtree(prof_dir, ignore_errors=True)


def render_motion_overlay_mov(
    overlay_img_path: Path,
    scene: SceneItem,
    project: MusicStoryProject | None,
    out_mov: Path,
    work_dir: Path,
    duration_sec: float,
    layout_mode: str = "pip_card",
    accent_color: str = "#FF2E93",
    fps: int = 30,
    hide_at_end: bool = True,
) -> tuple[Path, str, str, int]:
    """
    Render a transparent RGBA Motion Design 2.0 + Mockup animation `.mov` for the scene overlay.
    Returns `(out_mov, resolved_mockup, resolved_motion, stage_y_offset)`.
    """
    browser_exe = find_headless_browser()
    if not browser_exe:
        raise RuntimeError("Navegador headless Chrome/Edge não encontrado para Motion Design 2.0.")

    with Image.open(overlay_img_path) as im:
        img_w, img_h = im.size

    mockup_style, motion_preset = resolve_scene_mockup_and_motion(
        scene=scene,
        project=project,
        layout_mode=layout_mode,
    )

    stage_dir = work_dir / f"_motion_stage_s{scene.index:02d}"
    stage_dir.mkdir(parents=True, exist_ok=True)
    ext = overlay_img_path.suffix.lower() or ".png"
    staged_img = stage_dir / f"asset{ext}"
    shutil.copy2(overlay_img_path, staged_img)

    label_txt = (getattr(scene, "overlay_label", "") or scene.title or "").strip()
    niche = getattr(project, "niche", "gaming_news") if project else "gaming_news"

    html_content, stage_h = build_motion_overlay_html(
        image_rel_url=staged_img.name,
        label_text=label_txt,
        accent_color=accent_color,
        duration_sec=max(0.6, float(duration_sec)),
        mockup_style=mockup_style,
        motion_preset=motion_preset,
        layout_mode=layout_mode,
        overlay_pos=getattr(scene, "overlay_pos", "top_center") or "top_center",
        overlay_scale=float(getattr(scene, "overlay_scale", 0.56) or 0.56),
        img_w=img_w,
        img_h=img_h,
        niche=niche,
        hide_at_end=hide_at_end,
        fps=fps,
    )
    html_file = stage_dir / "overlay.html"
    html_file.write_text(html_content, encoding="utf-8")

    port = _free_port()
    handler = functools.partial(_QuietHandler, directory=str(stage_dir.resolve()))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    srv_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    srv_thread.start()

    n_frames = max(12, int(round(max(0.6, float(duration_sec)) * fps)))
    try:
        asyncio.run(
            _cdp_capture_transparent_mov(
                url=f"http://127.0.0.1:{port}/{html_file.name}",
                width=W,
                height=stage_h,
                fps=fps,
                n_frames=n_frames,
                out_mov=out_mov,
                browser_exe=browser_exe,
            )
        )
    finally:
        httpd.shutdown()
        httpd.server_close()

    shutil.rmtree(stage_dir, ignore_errors=True)
    return out_mov, mockup_style, motion_preset, STAGE_Y_OFFSET
