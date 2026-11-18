from __future__ import annotations

import os
import shutil
import subprocess
import logging
from pathlib import Path
from typing import Any
from dotenv import load_dotenv

logger = logging.getLogger("gta6_config")

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH, override=False)

INPUTS_DIR = BASE_DIR / "inputs"
GTA6_INPUTS_DIR = INPUTS_DIR / "gta6"
PROJECTS_DIR = BASE_DIR / "projects"
OUTPUTS_DIR = BASE_DIR / "outputs"
STATIC_DIR = BASE_DIR / "static"

for directory in (INPUTS_DIR, GTA6_INPUTS_DIR, PROJECTS_DIR, OUTPUTS_DIR, STATIC_DIR):
    directory.mkdir(parents=True, exist_ok=True)


def get_gemini_api_key(override_key: str | None = None) -> str:
    if override_key and override_key.strip():
        return override_key.strip()
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH, override=True)
    return os.getenv("GEMINI_API_KEY", "").strip()


def save_gemini_api_key(api_key: str) -> None:
    key = api_key.strip()
    lines: list[str] = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    elif (BASE_DIR / ".env.example").exists():
        lines = (BASE_DIR / ".env.example").read_text(encoding="utf-8").splitlines()

    updated = False
    new_lines: list[str] = []
    for line in lines:
        if line.startswith("GEMINI_API_KEY="):
            new_lines.append(f"GEMINI_API_KEY={key}")
            updated = True
        else:
            new_lines.append(line)
    if not updated:
        new_lines.insert(0, f"GEMINI_API_KEY={key}")

    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    os.environ["GEMINI_API_KEY"] = key


def get_text_model() -> str:
    val = os.getenv("GEMINI_TEXT_MODEL", "").strip()
    if not val:
        return "gemini-2.5-flash"
    if val in ("gemini-3.8-flash", "gemini-3.0-flash", "gemini-3.5-flash"):
        logger.warning(
            "GEMINI_TEXT_MODEL configurado como '%s' (modelo não suportado/fictício). Fazendo fallback para 'gemini-2.5-flash'. Corrija seu .env.",
            val
        )
        return "gemini-2.5-flash"
    return val


def get_image_model() -> str:
    return os.getenv("GEMINI_IMAGE_MODEL", "gemini-nano-banana-2.1").strip()


def get_tts_model() -> str:
    return os.getenv("GEMINI_TTS_MODEL", "gemini-3.8-flash-tts").strip()


def get_default_edge_voice() -> str:
    return os.getenv("DEFAULT_EDGE_VOICE", "pt-BR-AntonioNeural").strip()


def get_max_shorts_per_day() -> int:
    """T14.2: Limite diário configurável para lotes automáticos (padrão seguro: 3)."""
    raw = os.getenv("MAX_SHORTS_PER_DAY", "3").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning("MAX_SHORTS_PER_DAY inválido ('%s'); usando padrão 3.", raw)
        return 3


def get_ffmpeg_exe() -> str:
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise RuntimeError(
            "FFmpeg não encontrado no sistema nem via imageio-ffmpeg. "
            "Instale com: pip install imageio-ffmpeg"
        ) from exc


# Safe Zones para YouTube Shorts vertical (1080x1920) — Tarefa 4.4
SAFE_ZONE_TOP_PX: int = int(os.getenv("SAFE_ZONE_TOP_PX", "250"))
SAFE_ZONE_BOTTOM_PX: int = int(os.getenv("SAFE_ZONE_BOTTOM_PX", "320"))
SAFE_ZONE_LEFT_PX: int = int(os.getenv("SAFE_ZONE_LEFT_PX", "60"))
SAFE_ZONE_RIGHT_PX: int = int(os.getenv("SAFE_ZONE_RIGHT_PX", "60"))


def get_safe_zones() -> dict[str, int]:
    """Retorna os limites configuráveis da zona segura em pixels para 1080x1920."""
    return {
        "top": int(os.getenv("SAFE_ZONE_TOP_PX", str(SAFE_ZONE_TOP_PX))),
        "bottom": int(os.getenv("SAFE_ZONE_BOTTOM_PX", str(SAFE_ZONE_BOTTOM_PX))),
        "left": int(os.getenv("SAFE_ZONE_LEFT_PX", str(SAFE_ZONE_LEFT_PX))),
        "right": int(os.getenv("SAFE_ZONE_RIGHT_PX", str(SAFE_ZONE_RIGHT_PX))),
    }


def verify_ffmpeg_installation() -> dict[str, Any]:
    """Verifica se o FFmpeg está instalado e possui os filtros essenciais para o pipeline."""
    exe = get_ffmpeg_exe()
    try:
        proc = subprocess.run([exe, "-version"], capture_output=True, text=True, check=True)
        version_line = proc.stdout.splitlines()[0] if proc.stdout else "unknown"
        filters_proc = subprocess.run([exe, "-filters"], capture_output=True, text=True)
        filters_txt = filters_proc.stdout.lower()
        required_filters = ["loudnorm", "sidechaincompress", "scale", "crop", "ass"]
        missing = [f for f in required_filters if f not in filters_txt]
        if missing:
            logger.warning("FFmpeg detectado (%s), mas filtros ausentes: %s", exe, missing)
        return {
            "path": exe,
            "version": version_line,
            "ok": len(missing) == 0,
            "missing_filters": missing,
        }
    except Exception as exc:
        logger.error("Erro na verificação do FFmpeg em '%s': %s", exe, exc)
        raise RuntimeError(f"FFmpeg inválido ou não executável em '{exe}': {exc}") from exc


def find_headless_browser() -> str | None:
    """Localiza Chrome/Chromium/Edge localmente (Windows, Linux e macOS) para render HTML/thumbnail."""
    candidates = [
        # Windows
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        # macOS
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        # PATH (Windows/Linux/macOS)
        shutil.which("google-chrome") or "",
        shutil.which("chromium") or "",
        shutil.which("chromium-browser") or "",
        shutil.which("chrome") or "",
        shutil.which("msedge") or "",
    ]
    for path in candidates:
        if path and Path(path).exists():
            return str(path)
    return None


