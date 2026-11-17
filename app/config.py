from __future__ import annotations

import os
import shutil
import logging
from pathlib import Path
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
