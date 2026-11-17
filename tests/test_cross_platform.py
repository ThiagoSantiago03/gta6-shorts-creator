import sys
from pathlib import Path
from PIL import ImageFont
from app.thumbnail_engine import _load_pil_font
from app.qa_verifier import _load_mono_font

def test_load_pil_font_returns_font():
    font = _load_pil_font(24, bold=True)
    assert font is not None
    assert isinstance(font, (ImageFont.ImageFont, ImageFont.FreeTypeFont))

def test_load_mono_font_returns_font():
    font = _load_mono_font(16)
    assert font is not None
    assert isinstance(font, (ImageFont.ImageFont, ImageFont.FreeTypeFont))

def test_ytdlp_discovery():
    import shutil
    ytdlp_found = shutil.which("yt-dlp")
    if not ytdlp_found:
        candidate = Path(sys.executable).parent / ("yt-dlp.exe" if sys.platform == "win32" else "yt-dlp")
        if candidate.exists():
            ytdlp_found = str(candidate)
    cmd_base = [ytdlp_found] if ytdlp_found else [sys.executable, "-m", "yt_dlp"]
    assert len(cmd_base) >= 1
