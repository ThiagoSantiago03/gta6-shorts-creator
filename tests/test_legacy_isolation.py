from __future__ import annotations

import ast
import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LEGACY_MODULE_NAMES = {
    "app.legacy",
    "app.music_researcher",
    "app.lyrics_engine",
    "app.script_generator",
    "app.html_renderer",
    "app.motion_design",
}

GTA6_CORE_FILES = [
    "app/config.py",
    "app/models.py",
    "app/rights.py",
    "app/gaming_radar.py",
    "app/gaming_scriptwriter.py",
    "app/gaming_media.py",
    "app/tts_engine.py",
    "app/subtitle_engine.py",
    "app/audio_processor.py",
    "app/thumbnail_engine.py",
    "app/qa_verifier.py",
    "app/job_queue.py",
    "cli.py",
]


def _extract_imports(tree: ast.AST, top_level_only: bool = False) -> list[str]:
    nodes = tree.body if (top_level_only and isinstance(tree, ast.Module)) else list(ast.walk(tree))
    found: list[str] = []
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return found


def _is_legacy_import(mod_name: str) -> bool:
    return any(mod_name == leg or mod_name.startswith(leg + ".") for leg in LEGACY_MODULE_NAMES)


def test_gta6_core_modules_never_import_legacy():
    """T13: Fluxo principal de GTA 6 não pode importar nada de app/legacy/ ou dos 5 módulos legados."""
    violations: list[str] = []
    for rel in GTA6_CORE_FILES:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"), filename=rel)
        for imp in _extract_imports(tree, top_level_only=False):
            if _is_legacy_import(imp):
                violations.append(f"{rel} -> {imp}")
    assert not violations, f"Imports de legado encontrados no núcleo GTA 6: {violations}"


def test_web_server_and_video_renderer_have_no_top_level_legacy_imports():
    """T13: web_server.py e video_renderer.py não devem importar módulos legados no nível superior."""
    violations: list[str] = []
    for rel in ("app/web_server.py", "app/video_renderer.py"):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"), filename=rel)
        for imp in _extract_imports(tree, top_level_only=True):
            if _is_legacy_import(imp):
                violations.append(f"{rel} (top-level) -> {imp}")
    assert not violations, f"Imports de legado no top-level: {violations}"


def test_legacy_shims_remain_importable():
    """T13: Shims de compatibilidade em app/<modulo>.py continuam importáveis sem quebrar código antigo."""
    for mod in (
        "app.music_researcher",
        "app.lyrics_engine",
        "app.script_generator",
        "app.html_renderer",
        "app.motion_design",
    ):
        loaded = importlib.import_module(mod)
        assert loaded is not None


def test_legacy_music_routes_marked_deprecated():
    """T13: Rotas legadas do nicho de música no FastAPI devem estar marcadas com deprecated=True."""
    from app.web_server import app

    expected_deprecated = {
        "/api/projects/{project_id}/sync-lyrics",
        "/api/projects/{project_id}/upload-band-clip",
        "/api/projects/{project_id}/band-clips-info",
        "/api/projects/{project_id}/band-clip-thumb/{clip_idx}",
        "/api/projects/{project_id}/remove-band-clip/{clip_idx}",
        "/api/projects/{project_id}/clear-band-clips",
        "/api/projects/{project_id}/upload-reel2-image",
        "/api/projects/{project_id}/reel2-preview",
    }
    deprecated_paths = {
        getattr(route, "path", "")
        for route in app.routes
        if getattr(route, "deprecated", False) is True
    }
    missing = expected_deprecated - deprecated_paths
    assert not missing, f"Rotas legadas sem deprecated=True: {missing}"
