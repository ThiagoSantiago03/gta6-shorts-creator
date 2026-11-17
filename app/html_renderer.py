from __future__ import annotations

import asyncio
import base64
import functools
import http.server
import json
import logging
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("gta6_html")

import httpx
import numpy as np
import websockets

from app.config import get_ffmpeg_exe
from app.models import MusicStoryProject, SceneItem


W, H = 1080, 1920


def find_headless_browser() -> str | None:
    """Locate Chrome or Microsoft Edge locally on Windows for zero-download HTML rendering."""
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        shutil.which("chrome") or "",
        shutil.which("msedge") or "",
    ]
    for path in candidates:
        if path and Path(path).exists():
            return str(path)
    return None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return


async def _cdp_render_frames(
    url: str,
    width: int,
    height: int,
    fps: int,
    n_frames: int,
    out_mp4: Path,
    browser_exe: str,
) -> None:
    ffmpeg = get_ffmpeg_exe()
    cdp_port = _free_port()
    prof_dir = Path(tempfile.mkdtemp(prefix="mh_cdp_"))

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
            raise RuntimeError("Não foi possível conectar ao navegador headless via CDP.")

        ff_cmd = [
            ffmpeg,
            "-y",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "-framerate",
            str(fps),
            "-i",
            "pipe:0",
            "-vf",
            "format=yuv420p,setsar=1",
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
        ]
        ff_proc = subprocess.Popen(
            ff_cmd,
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
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
            await call("Page.navigate", {"url": url})

            # Wait up to 8s for window.film & ready promise
            for _ in range(80):
                probe = await call(
                    "Runtime.evaluate",
                    {"expression": "Boolean(window.film && typeof window.film.frame === 'function')"},
                )
                if probe.get("result", {}).get("value") is True:
                    break
                await asyncio.sleep(0.1)

            await call(
                "Runtime.evaluate",
                {
                    "expression": (
                        "(async () => {"
                        "  window.filmHost = { version: '0.1', name: 'MusicHistoryStudio' };"
                        "  if (document.fonts && document.fonts.ready) await document.fonts.ready;"
                        "  if (window.film && window.film.ready) {"
                        "    const r = typeof window.film.ready === 'function' ? window.film.ready() : window.film.ready;"
                        "    await Promise.resolve(r);"
                        "  }"
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
                shot = await call(
                    "Page.captureScreenshot",
                    {"format": "jpeg", "quality": 93},
                )
                ff_proc.stdin.write(base64.b64decode(shot["data"]))

            try:
                await call("Browser.close")
            except Exception as exc:
                logger.debug("Falha ao fechar conexão com browser via CDP: %s", exc)

        ff_proc.stdin.close()
        stderr_txt = ff_proc.stderr.read().decode("utf-8", errors="replace") if ff_proc.stderr else ""
        ret = ff_proc.wait()
        if ret != 0:
            raise RuntimeError(f"FFmpeg falhou ao codificar frames HTML: {stderr_txt[-400:]}")
    finally:
        if ff_proc and ff_proc.poll() is None:
            ff_proc.kill()
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(browser_proc.pid)],
                    capture_output=True,
                )
            else:
                browser_proc.terminate()
            browser_proc.wait(timeout=3)
        except Exception:
            try:
                browser_proc.kill()
            except Exception as exc:
                logger.debug("Falha ao matar processo de browser headless: %s", exc)
        shutil.rmtree(prof_dir, ignore_errors=True)


def render_html_film_to_mp4(
    html_path: Path,
    serve_dir: Path,
    out_mp4: Path,
    duration_sec: float,
    fps: int = 30,
    width: int = W,
    height: int = H,
) -> Path:
    """
    Render any HTML scene implementing the OpenFilm protocol (`window.film = { frame(t), ready }`)
    deterministically frame-by-frame to an MP4 using the local headless browser over localhost HTTP.
    """
    browser = find_headless_browser()
    if not browser:
        raise RuntimeError("Nenhum navegador Chromium (Chrome/Edge) encontrado para render HTML.")

    rel_path = html_path.resolve().relative_to(serve_dir.resolve()).as_posix()
    port = _free_port()
    handler = functools.partial(_QuietHandler, directory=str(serve_dir.resolve()))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    t_srv = threading.Thread(target=srv.serve_forever, daemon=True)
    t_srv.start()

    try:
        n_frames = max(15, int(round(duration_sec * fps)))
        url = f"http://127.0.0.1:{port}/{rel_path}"
        asyncio.run(
            _cdp_render_frames(
                url=url,
                width=width,
                height=height,
                fps=fps,
                n_frames=n_frames,
                out_mp4=out_mp4,
                browser_exe=browser,
            )
        )
    finally:
        srv.shutdown()
        srv.server_close()
    return out_mp4


def _write_track_data_json(
    spectrum: np.ndarray,
    beat_flash: np.ndarray,
    bpm: float,
    duration_sec: float,
    fps: int,
    out_json_path: Path,
) -> Path:
    n_frames = max(1, int(round(duration_sec * fps)))
    if len(spectrum) == 0:
        spec_slice = np.full((n_frames, 36), 0.25, dtype=np.float32)
    elif len(spectrum) >= n_frames:
        spec_slice = spectrum[:n_frames]
    else:
        pad_len = n_frames - len(spectrum)
        spec_slice = np.vstack([spectrum, np.repeat(spectrum[-1:], pad_len, axis=0)])

    if len(beat_flash) >= n_frames:
        bf_slice = beat_flash[:n_frames]
    else:
        bf_slice = np.pad(beat_flash, (0, n_frames - len(beat_flash)))

    bass_arr = np.mean(spec_slice[:, :6], axis=1)
    cum_bass = np.cumsum(bass_arr * 0.8 + bf_slice * 0.6).astype(np.float32)
    spec_u8 = np.clip(np.round(spec_slice * 255.0), 0, 255).astype(int).tolist()

    track_payload = {
        "fps": fps,
        "duration": round(duration_sec, 3),
        "bpm": round(float(bpm), 1),
        "bands": spec_u8,
        "beatFlash": [round(float(x), 3) for x in bf_slice.tolist()],
        "bass": [round(float(x), 3) for x in bass_arr.tolist()],
        "cumBass": [round(float(x), 3) for x in cum_bass.tolist()],
    }
    out_json_path.write_text(json.dumps(track_payload), encoding="utf-8")
    return out_json_path


def generate_reel1_hook_html(
    project: MusicStoryProject,
    scene: SceneItem,
    image_rel_url: str,
    duration_sec: float,
    out_html: Path,
    intro_music_sec: float = 0.0,
    spectrum: np.ndarray | None = None,
    beat_flash: np.ndarray | None = None,
    fps: int = 30,
    bpm: float = 120.0,
    track_data_json_path: Path | None = None,
) -> Path:
    """
    Generate a bespoke OpenFilm-compatible deterministic HTML5/Canvas2D documentary opener
    for Scene 1 (applied with HyperFrames Motion Doctrine: opaque stage ground,
    mirrored quartic velocity entry, purposeful Ken Burns push, kinetic typography cascade).
    When `intro_music_sec > 0.25`, renders an intro musical pulse + mini spectrum strip
    during [0, intro_music_sec] that glides out before narration & subtitles enter.
    """
    data_rel = ""
    if intro_music_sec > 0.25 and spectrum is not None and beat_flash is not None and track_data_json_path is not None:
        _write_track_data_json(spectrum, beat_flash, bpm, duration_sec, fps, track_data_json_path)
        data_rel = track_data_json_path.name

    is_gaming = getattr(project, "niche", "music_history") == "gaming_news" or project.style_preset == "vice_city_neon"
    top_pill_default = (
        f"RADAR {project.artist.upper()} • {(getattr(project, 'fact_confidence', '') or 'DOCUMENTADO').upper()}"
        if is_gaming
        else f"HISTÓRIA DA MÚSICA • {project.artist.upper()}"
    )
    footer_prefix = f"{project.artist.upper()} • SHORTS" if is_gaming else f"ARQUIVO MUSICAL • {project.artist.upper()}"

    payload = {
        "song": project.song_title,
        "artist": project.artist,
        "year": project.year or ("2026" if is_gaming else "CLÁSSICO"),
        "genre": project.genre,
        "hookTitle": (project.hook_title or f"A HISTÓRIA DE {project.song_title}").upper(),
        "chapterLabel": f"{project.year or '2026'} • {(project.genre.split('/')[0]).strip().upper()}",
        "topPillText": top_pill_default,
        "footerPrefix": footer_prefix,
        "isGaming": is_gaming,
        "accent": project.accent_color,
        "imageUrl": image_rel_url,
        "dataUrl": data_rel,
        "duration": round(duration_sec, 3),
        "introMusicSec": round(max(0.0, float(intro_music_sec)), 3),
        "stylePreset": project.style_preset,
        "overlayStyle": project.overlay_style,
    }
    data_json = json.dumps(payload, ensure_ascii=False)
    html = f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=1080, height=1920">
<title>Hook Scene — {project.song_title}</title>
<style>
  html, body {{
    margin: 0; width: 1080px; height: 1920px; overflow: hidden;
    background: #060609;
  }}
  canvas {{ display: block; width: 1080px; height: 1920px; }}
</style>
</head>
<body>
<canvas id="c" width="1080" height="1920"></canvas>
<script>
const CFG = {data_json};
const canvas = document.getElementById('c');
const ctx = canvas.getContext('2d');
const W = 1080, H = 1920;

function easeOutQuart(x) {{ return 1 - Math.pow(1 - Math.max(0, Math.min(1, x)), 4); }}
function easeInOutCubic(x) {{
  x = Math.max(0, Math.min(1, x));
  return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
}}
function hexToRgb(hex) {{
  const m = String(hex || '#FF3B30').replace('#', '');
  if (m.length !== 6) return [255, 59, 48];
  return [parseInt(m.slice(0,2),16), parseInt(m.slice(2,4),16), parseInt(m.slice(4,6),16)];
}}
const [AR, AG, AB] = hexToRgb(CFG.accent);

let TRACK = null;
let heroImg = new Image();
heroImg.crossOrigin = 'anonymous';
const readyJobs = [
  new Promise((resolve) => {{
    heroImg.onload = () => resolve();
    heroImg.onerror = () => resolve();
    heroImg.src = CFG.imageUrl;
  }}),
];
if (CFG.dataUrl) {{
  readyJobs.push(fetch(CFG.dataUrl).then(r => r.json()).then(d => {{ TRACK = d; }}).catch(() => {{}}));
}}
const readyPromise = Promise.all(readyJobs);

window.film = {{
  duration: CFG.duration,
  width: W,
  height: H,
  ready: readyPromise,
  frame(t) {{
    const dur = Math.max(1.0, CFG.duration);
    const p = Math.max(0, Math.min(1, t / dur));

    let bass = 0.0, flash = 0.0, bands = null;
    if (TRACK && TRACK.bands && TRACK.bands.length) {{
      const fi = Math.max(0, Math.min(TRACK.bands.length - 1, Math.floor(t * TRACK.fps)));
      bands = TRACK.bands[fi];
      bass = TRACK.bass[fi] || 0;
      flash = TRACK.beatFlash[fi] || 0;
    }} else if (CFG.introMusicSec > 0.2 && t < CFG.introMusicSec) {{
      flash = Math.pow(Math.max(0, Math.sin(t * Math.PI * 2.2)), 4);
      bass = 0.35 + 0.35 * flash;
    }}

    // Opaque deep stage ground
    ctx.fillStyle = '#07070c';
    ctx.fillRect(0, 0, W, H);

    // 1. Background blurred/graded photo moving with intentional camera push + intro pulse
    if (heroImg && heroImg.complete && heroImg.naturalWidth > 0) {{
      const entryP = easeOutQuart(t / 0.45);
      const slideX = (1 - entryP) * 160;
      const introBump = (CFG.introMusicSec > 0.2 && t < CFG.introMusicSec) ? (flash * 0.024 + bass * 0.015) : 0;
      const pushScale = 1.03 + p * 0.09 + introBump;
      ctx.save();
      ctx.globalAlpha = 0.25 + 0.75 * easeOutQuart(t / 0.28);
      ctx.translate(W / 2 + slideX, H / 2);
      ctx.scale(pushScale, pushScale);
      const scale = Math.max(W / heroImg.naturalWidth, H / heroImg.naturalHeight);
      const dw = heroImg.naturalWidth * scale;
      const dh = heroImg.naturalHeight * scale;
      if (CFG.stylePreset === 'zine_punk_underground') {{
        ctx.filter = 'contrast(1.22) saturate(0.45)';
      }} else if (CFG.stylePreset === 'documental_rock') {{
        ctx.filter = 'contrast(1.10) saturate(0.82)';
      }}
      ctx.drawImage(heroImg, -dw / 2, -dh / 2, dw, dh);
      ctx.restore();
    }}

    // Radial accent atmosphere glow (with Vice City neon dual-bloom when gaming/vice_city_neon)
    if (CFG.isGaming) {{
      const neonTop = ctx.createRadialGradient(W * 0.25, H * 0.26, 30, W * 0.25, H * 0.26, 720);
      neonTop.addColorStop(0, 'rgba(255, 46, 147, 0.34)');
      neonTop.addColorStop(1, 'rgba(7,7,12,0)');
      ctx.fillStyle = neonTop;
      ctx.fillRect(0, 0, W, H);

      const neonBot = ctx.createRadialGradient(W * 0.78, H * 0.62, 30, W * 0.78, H * 0.62, 680);
      neonBot.addColorStop(0, 'rgba(0, 229, 255, 0.26)');
      neonBot.addColorStop(1, 'rgba(7,7,12,0)');
      ctx.fillStyle = neonBot;
      ctx.fillRect(0, 0, W, H);

      // Subtle synthwave retro horizon scanlines
      ctx.fillStyle = 'rgba(0, 229, 255, 0.035)';
      for (let sy = 120; sy < H - 180; sy += 14) {{
        ctx.fillRect(0, sy, W, 2);
      }}
    }} else {{
      const rad = ctx.createRadialGradient(W * 0.5, H * 0.42, 40, W * 0.5, H * 0.42, 760);
      rad.addColorStop(0, CFG.accent + '36');
      rad.addColorStop(1, 'rgba(7,7,12,0)');
      ctx.fillStyle = rad;
      ctx.fillRect(0, 0, W, H);
    }}

    // Editorial dark vignette top & bottom (ensures crystal-clear captions LAST)
    const topV = ctx.createLinearGradient(0, 0, 0, 520);
    topV.addColorStop(0, 'rgba(6,6,9,0.92)');
    topV.addColorStop(1, 'rgba(6,6,9,0)');
    ctx.fillStyle = topV;
    ctx.fillRect(0, 0, W, 520);

    const botV = ctx.createLinearGradient(0, H - 720, 0, H);
    botV.addColorStop(0, 'rgba(6,6,9,0)');
    botV.addColorStop(1, 'rgba(6,6,9,0.94)');
    ctx.fillStyle = botV;
    ctx.fillRect(0, H - 720, W, 720);

    // 2. Clean Reels opening hook header (Scene 1 only) vs classic banner_top frame
    if (CFG.overlayStyle === 'clean_reels') {{
      const hRev = easeOutQuart(t / 0.28);
      const hOut = t > dur - 0.35 ? Math.max(0, 1.0 - (t - (dur - 0.35)) / 0.35) : 1.0;
      ctx.save();
      ctx.globalAlpha = hRev * hOut;
      ctx.translate(0, (1 - hRev) * 18);

      // Top red/accent pill (or neon Vice City gradient pill)
      const topPill = CFG.topPillText || `HISTÓRIA DA MÚSICA • ${{CFG.artist.toUpperCase()}}`;
      ctx.font = '800 24px "Segoe UI", Arial, sans-serif';
      const tpw = Math.min(880, ctx.measureText(topPill).width + 52);
      if (CFG.isGaming) {{
        const pGrad = ctx.createLinearGradient((W - tpw) / 2, 148, (W + tpw) / 2, 194);
        pGrad.addColorStop(0, '#FF2E93');
        pGrad.addColorStop(1, '#FF8C42');
        ctx.fillStyle = pGrad;
      }} else {{
        ctx.fillStyle = `rgb(${{AR}}, ${{AG}}, ${{AB}})`;
      }}
      ctx.beginPath();
      ctx.roundRect((W - tpw) / 2, 148, tpw, 46, 23);
      ctx.fill();
      if (CFG.isGaming) {{
        ctx.strokeStyle = 'rgba(0, 229, 255, 0.85)';
        ctx.lineWidth = 2;
        ctx.stroke();
      }}
      ctx.fillStyle = '#ffffff';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(topPill.slice(0, 48), W / 2, 171);

      // Bold 2-line wrapped opening hook title with crisp drop shadow
      ctx.font = '800 38px "Segoe UI", Arial, sans-serif';
      const wordsH = CFG.hookTitle.split(/\\s+/);
      const hLines = [];
      let curH = '';
      for (const w of wordsH) {{
        const trial = curH ? `${{curH}} ${{w}}` : w;
        if (ctx.measureText(trial).width <= 900 || !curH) {{
          curH = trial;
        }} else {{
          hLines.push(curH);
          curH = w;
        }}
      }}
      if (curH) hLines.push(curH);
      let hy = 232;
      ctx.shadowColor = 'rgba(0,0,0,0.88)';
      ctx.shadowBlur = 14;
      ctx.shadowOffsetY = 3;
      ctx.fillStyle = '#ffffff';
      for (const ln of hLines.slice(0, 2)) {{
        ctx.fillText(ln, W / 2, hy);
        hy += 46;
      }}
      ctx.shadowBlur = 0;

      const barW = easeOutQuart((t - 0.08) / 0.45) * 280;
      ctx.fillStyle = CFG.accent;
      ctx.fillRect((W - barW) / 2, hy - 10, barW, 5);

      ctx.font = '700 24px "Segoe UI", Arial, sans-serif';
      ctx.fillStyle = 'rgba(229, 231, 235, 0.90)';
      ctx.fillText(CFG.chapterLabel, W / 2, hy + 26);
      ctx.restore();
    }} else {{
      ctx.strokeStyle = 'rgba(255,255,255,0.12)';
      ctx.lineWidth = 2;
      ctx.strokeRect(42, 118, W - 84, H - 236);

      const barW = easeOutQuart((t - 0.08) / 0.50) * 320;
      ctx.fillStyle = CFG.accent;
      ctx.fillRect((W - barW) / 2, 336, barW, 5);

      const tagReveal = easeOutQuart((t - 0.14) / 0.30);
      ctx.save();
      ctx.globalAlpha = tagReveal * 0.92;
      ctx.translate(0, (1 - tagReveal) * 28);
      ctx.font = '700 26px "Segoe UI", Arial, sans-serif';
      ctx.fillStyle = '#e5e7eb';
      ctx.textAlign = 'center';
      ctx.fillText(CFG.chapterLabel, W / 2, 382);
      ctx.restore();
    }}

    // 3. Dedicated Musical Intro Pulse Badge & Equalizer Strip during [0, introMusicSec] (glides cleanly away before captions enter)
    if (CFG.introMusicSec > 0.25 && t < CFG.introMusicSec + 0.10) {{
      const fadeIn = easeOutQuart(t / 0.25);
      const fadeOut = t <= CFG.introMusicSec - 0.28 ? 1.0 : Math.max(0, 1.0 - (t - (CFG.introMusicSec - 0.28)) / 0.32);
      const alpha = fadeIn * fadeOut;
      if (alpha > 0.02) {{
        ctx.save();
        ctx.globalAlpha = alpha;
        // Intro pill badge
        const introTxt = `♪ INTRO MUSICAL • ${{CFG.song.toUpperCase()}}`;
        ctx.font = '700 25px "Segoe UI", Arial, sans-serif';
        const pw = Math.min(820, ctx.measureText(introTxt).width + 64);
        ctx.fillStyle = 'rgba(14, 15, 22, 0.88)';
        ctx.strokeStyle = `rgba(${{AR}}, ${{AG}}, ${{AB}}, 0.9)`;
        ctx.lineWidth = 2.5;
        ctx.beginPath();
        ctx.roundRect((W - pw) / 2, 1440, pw, 52, 26);
        ctx.fill();
        ctx.stroke();
        ctx.fillStyle = '#ffffff';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(introTxt.slice(0, 44), W / 2, 1466);

        // Mini reactive equalizer strip underneath intro pill
        const nBars = 24;
        const areaW = 520;
        const step = areaW / nBars;
        const x0 = (W - areaW) / 2;
        const baseY = 1585;
        for (let b = 0; b < nBars; b++) {{
          const v = bands ? ((bands[Math.floor((b / nBars) * bands.length)] || 0) / 255.0) : (0.28 + 0.52 * Math.abs(Math.sin(t * 7.0 + b * 0.45)));
          const bh = 8 + v * 58;
          ctx.fillStyle = `rgba(${{AR}}, ${{AG}}, ${{AB}}, ${{(0.55 + 0.45 * v).toFixed(2)}})`;
          ctx.beginPath();
          ctx.roundRect(x0 + b * step, baseY - bh, Math.max(8, step - 6), bh, 4);
          ctx.fill();
        }}
        ctx.restore();
      }}
    }}

    // Micro documentary metadata footer safely above Shorts/IG bottom UI
    const metaReveal = easeInOutCubic((t - 0.35) / 0.45);
    ctx.save();
    ctx.globalAlpha = metaReveal * (CFG.overlayStyle === 'clean_reels' ? 0.35 : 0.78);
    ctx.fillStyle = CFG.isGaming ? '#7dd3fc' : '#d4d8e4';
    ctx.font = '600 22px Consolas, monospace';
    ctx.textAlign = 'center';
    const footLbl = CFG.footerPrefix || `ARQUIVO MUSICAL • ${{CFG.artist.toUpperCase()}}`;
    ctx.fillText(`${{footLbl}} • REC ${{t.toFixed(1)}}S`, W / 2, CFG.isGaming ? (H - 220) : (H - 168));
    ctx.restore();
  }}
}};

if (!window.filmHost) {{
  window.film.ready.then(() => {{
    const t0 = performance.now();
    const step = () => {{
      const t = ((performance.now() - t0) / 1000) % CFG.duration;
      window.film.frame(t);
      requestAnimationFrame(step);
    }};
    requestAnimationFrame(step);
  }});
}}
</script>
</body>
</html>"""
    out_html.write_text(html, encoding="utf-8")
    return out_html


def generate_reel2_visualizer_html(
    project: MusicStoryProject,
    centerpiece_rel_url: str,
    spectrum: np.ndarray,
    beat_flash: np.ndarray,
    bpm: float,
    duration_sec: float,
    fps: int,
    out_html: Path,
    track_data_json_path: Path,
) -> Path:
    """
    Generate OpenFilm-compatible deterministic HTML5 Canvas2D visualizer for Reel 2 ('Ouça a Música').
    Follows OpenFilm SPEC §1 ('Timing that follows sound is data precomputed into JSON loaded during ready')
    and §3 ('frame(t) is pure in t using precomputed cumulative beat drive').
    """
    n_frames = min(len(spectrum), max(1, int(round(duration_sec * fps))))
    spec_slice = spectrum[:n_frames]
    bass_arr = np.mean(spec_slice[:, :6], axis=1)
    bf_slice = beat_flash[:n_frames] if len(beat_flash) >= n_frames else np.pad(beat_flash, (0, n_frames - len(beat_flash)))

    # Precompute cumulative bass integral so particle upward boost is 100% deterministic at any timestamp t
    cum_bass = np.cumsum(bass_arr * 0.8 + bf_slice * 0.6).astype(np.float32)

    # Quantize spectrum cleanly (0..255 ints) for ultra-compact fast JSON parse in Chrome
    spec_u8 = np.clip(np.round(spec_slice * 255.0), 0, 255).astype(int).tolist()
    track_payload = {
        "fps": fps,
        "duration": round(duration_sec, 3),
        "bpm": bpm,
        "bands": spec_u8,
        "beatFlash": [round(float(x), 3) for x in bf_slice.tolist()],
        "bass": [round(float(x), 3) for x in bass_arr.tolist()],
        "cumBass": [round(float(x), 3) for x in cum_bass.tolist()],
    }
    track_data_json_path.write_text(json.dumps(track_payload), encoding="utf-8")

    cfg = {
        "song": project.song_title,
        "artist": project.artist,
        "title": (project.reel2_title or project.song_title).strip(),
        "subtitle": (project.reel2_subtitle or f"{project.artist} • {project.year}").strip(" •"),
        "style": project.reel2_visual_style,
        "accent": project.accent_color,
        "centerUrl": centerpiece_rel_url,
        "dataUrl": track_data_json_path.name,
        "duration": round(duration_sec, 3),
        "bpm": bpm,
    }
    cfg_json = json.dumps(cfg, ensure_ascii=False)

    html = f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=1080, height=1920">
<title>Ouça a Música — {project.song_title}</title>
<style>
  html, body {{
    margin: 0; width: 1080px; height: 1920px; overflow: hidden;
    background: #050508;
  }}
  canvas {{ display: block; width: 1080px; height: 1920px; }}
</style>
</head>
<body>
<canvas id="c" width="1080" height="1920"></canvas>
<script>
const CFG = {cfg_json};
const canvas = document.getElementById('c');
const ctx = canvas.getContext('2d');
const W = 1080, H = 1920;

let TRACK = null;
const artImg = new Image();
artImg.crossOrigin = 'anonymous';

const readyPromise = Promise.all([
  fetch(CFG.dataUrl).then(r => r.json()).then(d => {{ TRACK = d; }}),
  new Promise(res => {{
    artImg.onload = () => res();
    artImg.onerror = () => res();
    artImg.src = CFG.centerUrl;
  }}),
]);

// Deterministic PRNG embers (100% pure function of t + cumBass)
let seed = 1337;
const rnd = () => {{ seed = (seed * 16807) % 2147483647; return seed / 2147483647; }};
const embers = Array.from({{ length: 75 }}, () => ({{
  x0: rnd(),
  y0: rnd(),
  speed: 0.055 + rnd() * 0.14,
  swayAmp: 12 + rnd() * 32,
  swayFreq: 0.8 + rnd() * 2.2,
  phase: rnd() * 6.283,
  r: 2.2 + rnd() * 6.2,
  warmth: rnd(),
}}));

function hexToRgb(hex) {{
  const m = String(hex || '#FF3B30').replace('#', '');
  if (m.length !== 6) return [255, 59, 48];
  return [parseInt(m.slice(0,2),16), parseInt(m.slice(2,4),16), parseInt(m.slice(4,6),16)];
}}
const [AR, AG, AB] = hexToRgb(CFG.accent);

window.film = {{
  duration: CFG.duration,
  width: W,
  height: H,
  ready: readyPromise,
  frame(t) {{
    if (!TRACK) return;
    const fIdx = Math.max(0, Math.min(TRACK.bands.length - 1, Math.floor(t * TRACK.fps)));
    const bands = TRACK.bands[fIdx];
    const bass = TRACK.bass[fIdx] || 0;
    const flash = TRACK.beatFlash[fIdx] || 0;
    const cumB = TRACK.cumBass[fIdx] || 0;
    const dur = Math.max(1.0, TRACK.duration);
    const prog = Math.max(0, Math.min(1, t / dur));

    // 1. Deep Obsidian Atmosphere + Beat-Reactive Radial Aura
    ctx.fillStyle = '#050508';
    ctx.fillRect(0, 0, W, H);

    const cx = W / 2, cy = H * 0.42;
    const auraR = 460 + bass * 130 + flash * 85;
    const aura = ctx.createRadialGradient(cx, cy, 40, cx, cy, auraR);
    aura.addColorStop(0, `rgba(${{AR}}, ${{AG}}, ${{AB}}, ${{(0.22 + bass * 0.16 + flash * 0.14).toFixed(3)}})`);
    aura.addColorStop(0.55, `rgba(${{Math.floor(AR*0.5)}}, ${{Math.floor(AG*0.35)}}, ${{Math.floor(AB*0.5)}}, 0.08)`);
    aura.addColorStop(1, 'rgba(5,5,8,0)');
    ctx.fillStyle = aura;
    ctx.fillRect(0, 0, W, H);

    // 2. Deterministic Floating Fire/Light Embers boosted cleanly by precomputed beat integral
    const travelDrive = t + cumB * 0.025;
    for (const e of embers) {{
      const ey = (1 - ((e.y0 + travelDrive * e.speed) % 1)) * (H + 80) - 40;
      const ex = (e.x0 * W + Math.sin(t * e.swayFreq + e.phase) * e.swayAmp + W) % W;
      const alpha = (0.28 + 0.50 * e.warmth) * (0.65 + 0.35 * flash);
      const er = e.r * (1.0 + flash * 0.35);
      const grad = ctx.createRadialGradient(ex, ey, 0, ex, ey, er * 3.2);
      grad.addColorStop(0, `rgba(255, ${{Math.min(255, AG + 115)}}, ${{Math.max(40, Math.floor(AB * 0.6))}}, ${{alpha.toFixed(3)}})`);
      grad.addColorStop(1, 'rgba(255, 140, 40, 0)');
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(ex, ey, er * 3.2, 0, Math.PI * 2);
      ctx.fill();
    }}

    // 3. Top Header Pill Badge ("OUÇA A MÚSICA • PARTE 2")
    const pill = 'OUÇA A MÚSICA • PARTE 2';
    ctx.font = '700 25px "Segoe UI", Arial, sans-serif';
    const pw = ctx.measureText(pill).width + 64;
    ctx.fillStyle = 'rgba(20, 20, 28, 0.85)';
    ctx.strokeStyle = `rgba(${{AR}}, ${{AG}}, ${{AB}}, 0.85)`;
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.roundRect((W - pw) / 2, 160, pw, 52, 26);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = '#ffffff';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(pill, cx, 186);

    // 4. Centerpiece Artwork / Vinyl Pulse / Beat Shockwave Ring
    const ringScale = 315 + flash * 46;
    ctx.strokeStyle = `rgba(${{AR}}, ${{AG}}, ${{AB}}, ${{(0.15 + flash * 0.45).toFixed(3)}})`;
    ctx.lineWidth = 2.5 + flash * 3.0;
    ctx.beginPath();
    ctx.arc(cx, cy, ringScale, 0, Math.PI * 2);
    ctx.stroke();

    const pulseScale = 1.0 + bass * 0.045 + flash * 0.032;
    ctx.save();
    ctx.translate(cx, cy);
    ctx.scale(pulseScale, pulseScale);

    if (CFG.style === 'vinyl_pulse') {{
      const rot = -t * 0.62;
      ctx.rotate(rot);
      // Vinyl disc platter backing
      ctx.fillStyle = '#111116';
      ctx.beginPath();
      ctx.arc(0, 0, 326, 0, Math.PI * 2);
      ctx.fill();
      for (let rr = 180; rr <= 310; rr += 14) {{
        ctx.strokeStyle = 'rgba(255,255,255,0.06)';
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(0, 0, rr, 0, Math.PI * 2);
        ctx.stroke();
      }}
      if (artImg && artImg.complete && artImg.naturalWidth > 0) {{
        ctx.save();
        ctx.beginPath();
        ctx.arc(0, 0, 238, 0, Math.PI * 2);
        ctx.clip();
        ctx.drawImage(artImg, -238, -238, 476, 476);
        ctx.restore();
      }}
      // Center spindle hole
      ctx.fillStyle = '#07070b';
      ctx.beginPath();
      ctx.arc(0, 0, 24, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = `rgba(${{AR}}, ${{AG}}, ${{AB}}, 0.85)`;
      ctx.lineWidth = 3;
      ctx.stroke();
    }} else {{
      // Embers Emblem / Neon Artwork Sleeve with deep shadow
      ctx.shadowColor = `rgba(${{AR}}, ${{AG}}, ${{AB}}, ${{(0.35 + flash * 0.35).toFixed(2)}})`;
      ctx.shadowBlur = 45 + flash * 35;
      if (artImg && artImg.complete && artImg.naturalWidth > 0) {{
        const aw = 600, ah = 600;
        ctx.save();
        ctx.beginPath();
        ctx.roundRect(-aw / 2, -ah / 2, aw, ah, 36);
        ctx.clip();
        ctx.drawImage(artImg, -aw / 2, -ah / 2, aw, ah);
        ctx.restore();
      }}
    }}
    ctx.restore();

    // 5. Track Info (Safely placed at y = 1275..1345 above IG Reels UI)
    ctx.fillStyle = '#ffffff';
    ctx.font = '800 52px "Segoe UI", Arial, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(CFG.title.slice(0, 32), cx, 1280);

    ctx.fillStyle = 'rgba(205, 212, 226, 0.88)';
    ctx.font = '500 30px "Segoe UI", Arial, sans-serif';
    ctx.fillText(CFG.subtitle.slice(0, 44), cx, 1340);

    // 6. Real Audio Spectrum Equalizer (36 bands)
    const barCount = bands.length;
    const areaW = 840;
    const step = areaW / barCount;
    const barW = Math.max(10, step - 8);
    const xStart = (W - areaW) / 2;
    const baseY = 1525;

    for (let i = 0; i < barCount; i++) {{
      const v = (bands[i] || 0) / 255.0;
      const bx = xStart + i * step;
      if (CFG.style === 'neon_wave') {{
        const hh = Math.max(5, v * 58);
        const midY = baseY - 58;
        ctx.fillStyle = `rgba(${{Math.min(255, AR + Math.round(v*90))}}, ${{Math.min(255, AG + Math.round(v*110))}}, ${{Math.min(255, AB + Math.round(v*95))}}, 0.94)`;
        ctx.beginPath();
        ctx.roundRect(bx, midY - hh, barW, hh * 2, barW / 2);
        ctx.fill();
      }} else {{
        const bh = 12 + v * 108;
        const gradBar = ctx.createLinearGradient(0, baseY - bh, 0, baseY);
        gradBar.addColorStop(0, `rgba(255, 255, 255, ${{(0.75 + 0.25 * v).toFixed(2)}})`);
        gradBar.addColorStop(0.35, `rgb(${{AR}}, ${{AG}}, ${{AB}})`);
        gradBar.addColorStop(1, `rgba(${{AR}}, ${{AG}}, ${{AB}}, 0.35)`);
        ctx.fillStyle = gradBar;
        ctx.beginPath();
        ctx.roundRect(bx, baseY - bh, barW, bh, 5);
        ctx.fill();
      }}
    }}

    // 7. Track Progress Bar & Timestamps
    const px0 = 120, px1 = W - 120, py = 1575;
    ctx.fillStyle = 'rgba(255, 255, 255, 0.16)';
    ctx.beginPath();
    ctx.roundRect(px0, py, px1 - px0, 7, 3.5);
    ctx.fill();

    const fillW = Math.max(8, (px1 - px0) * prog);
    ctx.fillStyle = `rgb(${{AR}}, ${{AG}}, ${{AB}})`;
    ctx.beginPath();
    ctx.roundRect(px0, py, fillW, 7, 3.5);
    ctx.fill();

    // Glowing playhead dot
    ctx.fillStyle = '#ffffff';
    ctx.beginPath();
    ctx.arc(px0 + fillW, py + 3.5, 9 + flash * 3, 0, Math.PI * 2);
    ctx.fill();

    const fmt = (sec) => {{
      const s = Math.max(0, Math.floor(sec));
      return `${{Math.floor(s/60)}}:${{String(s%60).padStart(2,'0')}}`;
    }};
    ctx.fillStyle = 'rgba(200, 206, 220, 0.72)';
    ctx.font = '600 22px Consolas, monospace';
    ctx.textAlign = 'left';
    ctx.fillText(fmt(t), px0, py + 34);
    ctx.textAlign = 'right';
    ctx.fillText(`${{fmt(dur)}} • ${{Math.round(CFG.bpm)}} BPM`, px1, py + 34);
  }}
}};

if (!window.filmHost) {{
  window.film.ready.then(() => {{
    const t0 = performance.now();
    const loop = () => {{
      const t = ((performance.now() - t0) / 1000) % CFG.duration;
      window.film.frame(t);
      requestAnimationFrame(loop);
    }};
    requestAnimationFrame(loop);
  }});
}}
</script>
</body>
</html>"""
    out_html.write_text(html, encoding="utf-8")
    return out_html


def generate_music_break_html(
    project: MusicStoryProject,
    break_num: int,
    image_rel_url: str,
    artwork_rel_url: str,
    spectrum: np.ndarray,
    beat_flash: np.ndarray,
    bpm: float,
    duration_sec: float,
    fps: int,
    out_html: Path,
    track_data_json_path: Path,
    has_lyric_caption: bool = False,
    overlay_style: str = "clean_reels",
) -> Path:
    """
    Generate an OpenFilm-compatible deterministic Musical Break (~3.2s):
    Full-bleed animated concert photo moving on beat + warm embers + clean lower
    glass HUD with live FFT spectrum bars ('♪ TRECHO ORIGINAL').
    When `has_lyric_caption=True`, uses a sleek compact bottom bar at y=1635..1709
    so translated sung LyricBreak subtitles at y=1370..1515 never collide with the HUD!
    """
    _write_track_data_json(spectrum, beat_flash, bpm, duration_sec, fps, track_data_json_path)

    cfg = {
        "song": project.song_title,
        "artist": project.artist,
        "breakNum": break_num,
        "accent": project.accent_color,
        "imageUrl": image_rel_url,
        "artUrl": artwork_rel_url,
        "dataUrl": track_data_json_path.name,
        "duration": round(duration_sec, 3),
        "bpm": round(float(bpm), 1),
        "hasLyricCaption": bool(has_lyric_caption),
        "overlayStyle": overlay_style,
    }
    cfg_json = json.dumps(cfg, ensure_ascii=False)

    html = f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=1080, height=1920">
<title>Respiro Musical {break_num} — {project.song_title}</title>
<style>
  html, body {{ margin: 0; width: 1080px; height: 1920px; overflow: hidden; background: #060609; }}
  canvas {{ display: block; width: 1080px; height: 1920px; }}
</style>
</head>
<body>
<canvas id="c" width="1080" height="1920"></canvas>
<script>
const CFG = {cfg_json};
const canvas = document.getElementById('c');
const ctx = canvas.getContext('2d');
const W = 1080, H = 1920;

let TRACK = null;
const bgImg = new Image(); bgImg.crossOrigin = 'anonymous';
const artImg = new Image(); artImg.crossOrigin = 'anonymous';

const readyPromise = Promise.all([
  fetch(CFG.dataUrl).then(r => r.json()).then(d => {{ TRACK = d; }}),
  new Promise(res => {{ bgImg.onload = () => res(); bgImg.onerror = () => res(); bgImg.src = CFG.imageUrl; }}),
  new Promise(res => {{ artImg.onload = () => res(); artImg.onerror = () => res(); artImg.src = CFG.artUrl; }}),
]);

let seed = 4099 + CFG.breakNum * 131;
const rnd = () => {{ seed = (seed * 16807) % 2147483647; return seed / 2147483647; }};
const embers = Array.from({{ length: 48 }}, () => ({{
  x0: rnd(), y0: rnd(), speed: 0.08 + rnd() * 0.16,
  swayAmp: 10 + rnd() * 24, swayFreq: 1.0 + rnd() * 2.0,
  phase: rnd() * 6.283, r: 2.0 + rnd() * 5.0, warmth: rnd(),
}}));

function hexToRgb(hex) {{
  const m = String(hex || '#FF3B30').replace('#', '');
  if (m.length !== 6) return [255, 59, 48];
  return [parseInt(m.slice(0,2),16), parseInt(m.slice(2,4),16), parseInt(m.slice(4,6),16)];
}}
const [AR, AG, AB] = hexToRgb(CFG.accent);
function easeOutQuart(x) {{ return 1 - Math.pow(1 - Math.max(0, Math.min(1, x)), 4); }}

window.film = {{
  duration: CFG.duration,
  width: W,
  height: H,
  ready: readyPromise,
  frame(t) {{
    if (!TRACK) return;
    const fi = Math.max(0, Math.min(TRACK.bands.length - 1, Math.floor(t * TRACK.fps)));
    const bands = TRACK.bands[fi];
    const bass = TRACK.bass[fi] || 0;
    const flash = TRACK.beatFlash[fi] || 0;
    const cumB = TRACK.cumBass[fi] || 0;
    const dur = Math.max(0.5, CFG.duration);
    const p = Math.max(0, Math.min(1, t / dur));

    ctx.fillStyle = '#060609';
    ctx.fillRect(0, 0, W, H);

    // 1. Energetic Ken Burns concert backdrop synced to beat transients
    if (bgImg && bgImg.complete && bgImg.naturalWidth > 0) {{
      const mode = CFG.breakNum % 4;
      let sc = 1.08 + bass * 0.03 + flash * 0.028;
      let ox = 0, oy = 0;
      if (mode === 0) {{ sc += p * 0.09; }}
      else if (mode === 1) {{ sc += 0.06; ox = (0.5 - p) * 70; }}
      else if (mode === 2) {{ sc += (1 - p) * 0.09; }}
      else {{ sc += 0.06; ox = (p - 0.5) * 70; oy = (0.5 - p) * 30; }}

      ctx.save();
      ctx.translate(W / 2 + ox, H / 2 + oy);
      ctx.scale(sc, sc);
      const fitScale = Math.max(W / bgImg.naturalWidth, H / bgImg.naturalHeight);
      const dw = bgImg.naturalWidth * fitScale;
      const dh = bgImg.naturalHeight * fitScale;
      ctx.filter = `contrast(1.12) brightness(${{(0.84 + flash * 0.14).toFixed(2)}})`;
      ctx.drawImage(bgImg, -dw / 2, -dh / 2, dw, dh);
      ctx.restore();
    }}

    // 2. Smooth top/bottom vertical vignette + beat rim aura & floating embers
    const topV = ctx.createLinearGradient(0, 0, 0, 510);
    topV.addColorStop(0, 'rgba(6,6,10,0.92)');
    topV.addColorStop(1, 'rgba(6,6,10,0)');
    ctx.fillStyle = topV;
    ctx.fillRect(0, 0, W, 510);

    const botV = ctx.createLinearGradient(0, H - 760, 0, H);
    botV.addColorStop(0, 'rgba(6,6,10,0)');
    botV.addColorStop(0.5, 'rgba(6,6,10,0.72)');
    botV.addColorStop(1, 'rgba(6,6,10,0.96)');
    ctx.fillStyle = botV;
    ctx.fillRect(0, H - 760, W, 760);

    const aura = ctx.createRadialGradient(W / 2, H * 0.48, 40, W / 2, H * 0.48, 760);
    aura.addColorStop(0, `rgba(${{AR}}, ${{AG}}, ${{AB}}, ${{(0.10 + flash * 0.16).toFixed(3)}})`);
    aura.addColorStop(1, 'rgba(6,6,9,0.40)');
    ctx.fillStyle = aura;
    ctx.fillRect(0, 0, W, H);

    const drive = t + cumB * 0.03;
    for (const e of embers) {{
      const ey = (1 - ((e.y0 + drive * e.speed) % 1)) * (H + 60) - 30;
      const ex = (e.x0 * W + Math.sin(t * e.swayFreq + e.phase) * e.swayAmp + W) % W;
      const alpha = (0.30 + 0.45 * e.warmth) * (0.65 + 0.35 * flash);
      const er = e.r * (1.0 + flash * 0.35);
      const grad = ctx.createRadialGradient(ex, ey, 0, ex, ey, er * 3.0);
      grad.addColorStop(0, `rgba(255, ${{Math.min(255, AG + 115)}}, ${{Math.max(40, Math.floor(AB * 0.6))}}, ${{alpha.toFixed(3)}})`);
      grad.addColorStop(1, 'rgba(255,140,40,0)');
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(ex, ey, er * 3.0, 0, Math.PI * 2);
      ctx.fill();
    }}

    // 3. Top Break indicator (y=148 in clean_reels, y=344 under banner_top)
    const rev = easeOutQuart(t / 0.25);
    const pillTop = CFG.overlayStyle === 'clean_reels' ? 148 : 344;
    ctx.save();
    ctx.globalAlpha = rev;
    const topTag = `♪ TRECHO ORIGINAL • PARTE 0${{CFG.breakNum}}`;
    ctx.font = '700 23px "Segoe UI", Arial, sans-serif';
    const tpw = ctx.measureText(topTag).width + 52;
    ctx.fillStyle = 'rgba(14, 15, 22, 0.85)';
    ctx.strokeStyle = `rgba(${{AR}}, ${{AG}}, ${{AB}}, 0.90)`;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.roundRect((W - tpw) / 2, pillTop, tpw, 44, 22);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = '#ffffff';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(topTag, W / 2, pillTop + 22);
    ctx.restore();

    // 4. Lower Music HUD — compact bar at y=1635 when LyricBreak caption is active, rich card otherwise
    if (CFG.hasLyricCaption) {{
      const barW = 860, barH = 74, barX = (W - barW) / 2, barY = 1635;
      ctx.fillStyle = 'rgba(10, 11, 18, 0.80)';
      ctx.strokeStyle = `rgba(${{AR}}, ${{AG}}, ${{AB}}, ${{(0.55 + flash * 0.35).toFixed(2)}})`;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.roundRect(barX, barY, barW, barH, 20);
      ctx.fill();
      ctx.stroke();

      if (artImg && artImg.complete && artImg.naturalWidth > 0) {{
        ctx.save();
        ctx.beginPath();
        ctx.roundRect(barX + 12, barY + 12, 50, 50, 10);
        ctx.clip();
        ctx.drawImage(artImg, barX + 12, barY + 12, 50, 50);
        ctx.restore();
      }}

      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = '#f8fafc';
      ctx.font = '700 22px "Segoe UI", Arial, sans-serif';
      ctx.fillText(`♪ ${{CFG.song}} — ${{CFG.artist}}`.slice(0, 30), barX + 76, barY + 37);

      const nBars = 20;
      const eqW = 310, eqX0 = barX + barW - eqW - 22, eqBaseY = barY + 56;
      const step = eqW / nBars, bw = Math.max(7, step - 6);
      for (let i = 0; i < nBars; i++) {{
        const bi = Math.min(bands.length - 1, Math.floor((i / nBars) * bands.length));
        const v = (bands[bi] || 0) / 255.0;
        const bh = 6 + v * 34;
        ctx.fillStyle = `rgba(${{Math.min(255, AR + Math.round(v*90))}}, ${{Math.min(255, AG + Math.round(v*110))}}, ${{Math.min(255, AB + Math.round(v*90))}}, 0.95)`;
        ctx.beginPath();
        ctx.roundRect(eqX0 + i * step, eqBaseY - bh, bw, bh, 3);
        ctx.fill();
      }}
    }} else {{
      const cardW = 890, cardH = 196, cardX = (W - cardW) / 2, cardY = 1410;
      ctx.fillStyle = 'rgba(10, 11, 18, 0.82)';
      ctx.strokeStyle = `rgba(${{AR}}, ${{AG}}, ${{AB}}, ${{(0.55 + flash * 0.35).toFixed(2)}})`;
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      ctx.roundRect(cardX, cardY, cardW, cardH, 26);
      ctx.fill();
      ctx.stroke();

      if (artImg && artImg.complete && artImg.naturalWidth > 0) {{
        const s = 128 + flash * 8;
        const ax = cardX + 28 + (128 - s) / 2;
        const ay = cardY + (cardH - s) / 2;
        ctx.save();
        ctx.beginPath();
        ctx.roundRect(ax, ay, s, s, 18);
        ctx.clip();
        ctx.drawImage(artImg, ax, ay, s, s);
        ctx.restore();
      }}

      const tx = cardX + 184;
      ctx.textAlign = 'left';
      ctx.fillStyle = '#ffffff';
      ctx.font = '800 32px "Segoe UI", Arial, sans-serif';
      ctx.fillText(`${{CFG.song}} — ${{CFG.artist}}`.slice(0, 32), tx, cardY + 52);

      ctx.fillStyle = `rgb(${{AR}}, ${{AG}}, ${{AB}})`;
      ctx.font = '700 20px "Segoe UI", Arial, sans-serif';
      ctx.fillText('▶ OUÇA O SOM ORIGINAL', tx, cardY + 86);

      const nBars = 28;
      const eqW = 665, eqX0 = tx, eqBaseY = cardY + 168;
      const step = eqW / nBars, bw = Math.max(10, step - 7);
      for (let i = 0; i < nBars; i++) {{
        const bi = Math.min(bands.length - 1, Math.floor((i / nBars) * bands.length));
        const v = (bands[bi] || 0) / 255.0;
        const bh = 8 + v * 58;
        ctx.fillStyle = `rgba(${{Math.min(255, AR + Math.round(v*90))}}, ${{Math.min(255, AG + Math.round(v*110))}}, ${{Math.min(255, AB + Math.round(v*90))}}, 0.95)`;
        ctx.beginPath();
        ctx.roundRect(eqX0 + i * step, eqBaseY - bh, bw, bh, 4);
        ctx.fill();
      }}
    }}
  }}
}};

if (!window.filmHost) {{
  window.film.ready.then(() => {{
    const t0 = performance.now();
    const loop = () => {{
      const t = ((performance.now() - t0) / 1000) % CFG.duration;
      window.film.frame(t);
      requestAnimationFrame(loop);
    }};
    requestAnimationFrame(loop);
  }});
}}
</script>
</body>
</html>"""
    out_html.write_text(html, encoding="utf-8")
    return out_html


def generate_allinone_finale_html(
    project: MusicStoryProject,
    gallery_rel_urls: list[str],
    centerpiece_rel_url: str,
    spectrum: np.ndarray,
    beat_flash: np.ndarray,
    bpm: float,
    duration_sec: float,
    fps: int,
    out_html: Path,
    track_data_json_path: Path,
) -> Path:
    """
    Generate the OpenFilm deterministic Finale Climax showcase (10-15s):
    - Multi-shot animated concert/band photo montage cycling continuously with smooth dissolves + beat-synced camera drift
    - Embers glow driven purely by precomputed cumulative bass
    - Clean bottom Reels player card with spinning vinyl / album art, 36-band equalizer, and progress bar
    """
    _write_track_data_json(spectrum, beat_flash, bpm, duration_sec, fps, track_data_json_path)

    cfg = {
        "song": project.song_title,
        "artist": project.artist,
        "title": (project.reel2_title or project.song_title).strip(),
        "subtitle": (project.reel2_subtitle or f"{project.artist} • {project.year}").strip(" •"),
        "style": project.reel2_visual_style,
        "accent": project.accent_color,
        "galleryUrls": gallery_rel_urls or [centerpiece_rel_url],
        "centerUrl": centerpiece_rel_url,
        "dataUrl": track_data_json_path.name,
        "duration": round(duration_sec, 3),
        "bpm": round(float(bpm), 1),
        "overlayStyle": project.overlay_style,
    }
    cfg_json = json.dumps(cfg, ensure_ascii=False)

    html = f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=1080, height=1920">
<title>Clímax Musical Final — {project.song_title}</title>
<style>
  html, body {{ margin: 0; width: 1080px; height: 1920px; overflow: hidden; background: #050508; }}
  canvas {{ display: block; width: 1080px; height: 1920px; }}
</style>
</head>
<body>
<canvas id="c" width="1080" height="1920"></canvas>
<script>
const CFG = {cfg_json};
const canvas = document.getElementById('c');
const ctx = canvas.getContext('2d');
const W = 1080, H = 1920;

let TRACK = null;
const galleryImgs = CFG.galleryUrls.map(() => {{ const im = new Image(); im.crossOrigin = 'anonymous'; return im; }});
const artImg = new Image(); artImg.crossOrigin = 'anonymous';

const readyPromise = Promise.all([
  fetch(CFG.dataUrl).then(r => r.json()).then(d => {{ TRACK = d; }}),
  ...CFG.galleryUrls.map((u, idx) => new Promise(res => {{
    galleryImgs[idx].onload = () => res();
    galleryImgs[idx].onerror = () => res();
    galleryImgs[idx].src = u;
  }})),
  new Promise(res => {{ artImg.onload = () => res(); artImg.onerror = () => res(); artImg.src = CFG.centerUrl; }}),
]);

let seed = 8821;
const rnd = () => {{ seed = (seed * 16807) % 2147483647; return seed / 2147483647; }};
const embers = Array.from({{ length: 65 }}, () => ({{
  x0: rnd(), y0: rnd(), speed: 0.06 + rnd() * 0.14,
  swayAmp: 12 + rnd() * 30, swayFreq: 0.9 + rnd() * 2.1,
  phase: rnd() * 6.283, r: 2.2 + rnd() * 5.5, warmth: rnd(),
}}));

function hexToRgb(hex) {{
  const m = String(hex || '#FF3B30').replace('#', '');
  if (m.length !== 6) return [255, 59, 48];
  return [parseInt(m.slice(0,2),16), parseInt(m.slice(2,4),16), parseInt(m.slice(4,6),16)];
}}
const [AR, AG, AB] = hexToRgb(CFG.accent);
function easeInOutCubic(x) {{ x = Math.max(0, Math.min(1, x)); return x < 0.5 ? 4*x*x*x : 1 - Math.pow(-2*x + 2, 3) / 2; }}

function drawMovingShot(im, idx, localP, alpha, bass, flash) {{
  if (!im || !im.complete || im.naturalWidth <= 0 || alpha <= 0.01) return;
  const dir = idx % 4;
  let sc = 1.06 + bass * 0.025 + flash * 0.022;
  let ox = 0, oy = 0;
  if (dir === 0) {{ sc += localP * 0.08; }}
  else if (dir === 1) {{ sc += 0.06; ox = (0.5 - localP) * 75; }}
  else if (dir === 2) {{ sc += (1 - localP) * 0.08; }}
  else {{ sc += 0.06; ox = (localP - 0.5) * 75; }}

  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.translate(W / 2 + ox, H / 2 + oy);
  ctx.scale(sc, sc);
  const fit = Math.max(W / im.naturalWidth, H / im.naturalHeight);
  const dw = im.naturalWidth * fit;
  const dh = im.naturalHeight * fit;
  ctx.filter = `contrast(1.10) brightness(${{(0.80 + flash * 0.14).toFixed(2)}})`;
  ctx.drawImage(im, -dw / 2, -dh / 2, dw, dh);
  ctx.restore();
}}

window.film = {{
  duration: CFG.duration,
  width: W,
  height: H,
  ready: readyPromise,
  frame(t) {{
    if (!TRACK) return;
    const fi = Math.max(0, Math.min(TRACK.bands.length - 1, Math.floor(t * TRACK.fps)));
    const bands = TRACK.bands[fi];
    const bass = TRACK.bass[fi] || 0;
    const flash = TRACK.beatFlash[fi] || 0;
    const cumB = TRACK.cumBass[fi] || 0;
    const dur = Math.max(1.0, CFG.duration);
    const prog = Math.max(0, Math.min(1, t / dur));

    ctx.fillStyle = '#050508';
    ctx.fillRect(0, 0, W, H);

    // 1. Multi-shot concert montage in continuous motion
    const nShots = Math.max(1, galleryImgs.length);
    const shotFloat = (t / dur) * Math.max(nShots, Math.floor(dur / 2.8));
    const curStep = Math.floor(shotFloat);
    const idxA = curStep % nShots;
    const idxB = (curStep + 1) % nShots;
    const localP = shotFloat - curStep;
    const blend = localP > 0.74 ? easeInOutCubic((localP - 0.74) / 0.26) : 0.0;

    drawMovingShot(galleryImgs[idxA], curStep, localP, 1.0 - blend * 0.4, bass, flash);
    if (blend > 0.01) {{
      drawMovingShot(galleryImgs[idxB], curStep + 1, localP * 0.25, blend, bass, flash);
    }}

    // Atmospheric vignette + beat aura
    const topV = ctx.createLinearGradient(0, 0, 0, 520);
    topV.addColorStop(0, 'rgba(5,5,9,0.92)');
    topV.addColorStop(1, 'rgba(5,5,9,0)');
    ctx.fillStyle = topV;
    ctx.fillRect(0, 0, W, 520);

    const botV = ctx.createLinearGradient(0, H - 860, 0, H);
    botV.addColorStop(0, 'rgba(5,5,9,0)');
    botV.addColorStop(0.55, 'rgba(5,5,9,0.72)');
    botV.addColorStop(1, 'rgba(5,5,9,0.96)');
    ctx.fillStyle = botV;
    ctx.fillRect(0, H - 860, W, 860);

    // Embers boosted by bass integral
    const drive = t + cumB * 0.025;
    for (const e of embers) {{
      const ey = (1 - ((e.y0 + drive * e.speed) % 1)) * (H + 80) - 40;
      const ex = (e.x0 * W + Math.sin(t * e.swayFreq + e.phase) * e.swayAmp + W) % W;
      const alpha = (0.28 + 0.48 * e.warmth) * (0.65 + 0.35 * flash);
      const er = e.r * (1.0 + flash * 0.35);
      const grad = ctx.createRadialGradient(ex, ey, 0, ex, ey, er * 3.0);
      grad.addColorStop(0, `rgba(255, ${{Math.min(255, AG + 115)}}, ${{Math.max(40, Math.floor(AB * 0.6))}}, ${{alpha.toFixed(3)}})`);
      grad.addColorStop(1, 'rgba(255,140,40,0)');
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(ex, ey, er * 3.0, 0, Math.PI * 2);
      ctx.fill();
    }}

    // Top status pill (y=148 in clean_reels, y=344 under banner_top)
    const pill = '🎧 CLÍMAX FINAL • SALVE NOS DESTAQUES';
    const finPillY = CFG.overlayStyle === 'clean_reels' ? 148 : 344;
    ctx.font = '700 23px "Segoe UI", Arial, sans-serif';
    const pw = ctx.measureText(pill).width + 52;
    ctx.fillStyle = 'rgba(14, 15, 22, 0.85)';
    ctx.strokeStyle = `rgba(${{AR}}, ${{AG}}, ${{AB}}, 0.90)`;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.roundRect((W - pw) / 2, finPillY, pw, 44, 22);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = '#ffffff';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(pill, W / 2, finPillY + 22);

    // Lower Interactive Player Card (y=1240..1670)
    const cw = 920, ch = 430, cx0 = (W - cw) / 2, cy0 = 1235;
    ctx.fillStyle = 'rgba(9, 10, 16, 0.84)';
    ctx.strokeStyle = `rgba(${{AR}}, ${{AG}}, ${{AB}}, ${{(0.55 + flash * 0.35).toFixed(2)}})`;
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.roundRect(cx0, cy0, cw, ch, 30);
    ctx.fill();
    ctx.stroke();

    // Artwork / Spinning Vinyl on left
    const artCx = cx0 + 116, artCy = cy0 + 112;
    ctx.save();
    ctx.translate(artCx, artCy);
    const pulse = 1.0 + bass * 0.04 + flash * 0.03;
    ctx.scale(pulse, pulse);
    if (CFG.style === 'vinyl_pulse') {{
      ctx.rotate(-t * 0.72);
      ctx.fillStyle = '#121218';
      ctx.beginPath(); ctx.arc(0, 0, 74, 0, Math.PI * 2); ctx.fill();
      if (artImg && artImg.complete && artImg.naturalWidth > 0) {{
        ctx.beginPath(); ctx.arc(0, 0, 56, 0, Math.PI * 2); ctx.clip();
        ctx.drawImage(artImg, -56, -56, 112, 112);
      }}
    }} else if (artImg && artImg.complete && artImg.naturalWidth > 0) {{
      ctx.beginPath(); ctx.roundRect(-72, -72, 144, 144, 20); ctx.clip();
      ctx.drawImage(artImg, -72, -72, 144, 144);
    }}
    ctx.restore();

    // Song + Artist Info
    const tx = cx0 + 216;
    ctx.textAlign = 'left';
    ctx.fillStyle = '#ffffff';
    ctx.font = '800 40px "Segoe UI", Arial, sans-serif';
    ctx.fillText(CFG.title.slice(0, 28), tx, cy0 + 88);

    ctx.fillStyle = 'rgba(205, 212, 226, 0.88)';
    ctx.font = '500 26px "Segoe UI", Arial, sans-serif';
    ctx.fillText(CFG.subtitle.slice(0, 42), tx, cy0 + 136);

    // 36-band Equalizer inside card
    const barCount = bands.length;
    const eqX0 = cx0 + 42, eqW = cw - 84, eqBaseY = cy0 + 320;
    const step = eqW / barCount, bw = Math.max(9, step - 7);
    for (let i = 0; i < barCount; i++) {{
      const v = (bands[i] || 0) / 255.0;
      const bh = 10 + v * 98;
      const gradBar = ctx.createLinearGradient(0, eqBaseY - bh, 0, eqBaseY);
      gradBar.addColorStop(0, '#ffffff');
      gradBar.addColorStop(0.35, `rgb(${{AR}}, ${{AG}}, ${{AB}})`);
      gradBar.addColorStop(1, `rgba(${{AR}}, ${{AG}}, ${{AB}}, 0.35)`);
      ctx.fillStyle = gradBar;
      ctx.beginPath();
      ctx.roundRect(eqX0 + i * step, eqBaseY - bh, bw, bh, 4);
      ctx.fill();
    }}

    // Progress Bar + Timestamps
    const px0 = cx0 + 42, px1 = cx0 + cw - 42, py = cy0 + 362;
    ctx.fillStyle = 'rgba(255,255,255,0.16)';
    ctx.beginPath(); ctx.roundRect(px0, py, px1 - px0, 7, 3.5); ctx.fill();
    const fillW = Math.max(8, (px1 - px0) * prog);
    ctx.fillStyle = `rgb(${{AR}}, ${{AG}}, ${{AB}})`;
    ctx.beginPath(); ctx.roundRect(px0, py, fillW, 7, 3.5); ctx.fill();
    ctx.fillStyle = '#ffffff';
    ctx.beginPath(); ctx.arc(px0 + fillW, py + 3.5, 9 + flash * 3, 0, Math.PI * 2); ctx.fill();

    const fmt = (sec) => {{
      const s = Math.max(0, Math.floor(sec));
      return `${{Math.floor(s/60)}}:${{String(s%60).padStart(2,'0')}}`;
    }};
    ctx.fillStyle = 'rgba(205, 212, 226, 0.78)';
    ctx.font = '600 22px Consolas, monospace';
    ctx.textAlign = 'left'; ctx.fillText(fmt(t), px0, py + 34);
    ctx.textAlign = 'right'; ctx.fillText(`${{fmt(dur)}} • ${{Math.round(CFG.bpm)}} BPM`, px1, py + 34);
  }}
}};

if (!window.filmHost) {{
  window.film.ready.then(() => {{
    const t0 = performance.now();
    const loop = () => {{
      const t = ((performance.now() - t0) / 1000) % CFG.duration;
      window.film.frame(t);
      requestAnimationFrame(loop);
    }};
    requestAnimationFrame(loop);
  }});
}}
</script>
</body>
</html>"""
    out_html.write_text(html, encoding="utf-8")
    return out_html
