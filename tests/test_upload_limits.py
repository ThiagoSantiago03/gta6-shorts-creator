from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.web_server as ws


def test_upload_clip_rejects_oversized_file_with_413_and_leaves_no_residue(tmp_path: Path, monkeypatch):
    """T3: Upload acima do limite deve retornar 413 e não deixar arquivo nem .part em disco."""
    monkeypatch.setattr(ws, "GTA6_INPUTS_DIR", tmp_path)
    monkeypatch.setitem(ws.MAX_UPLOAD_BYTES, "video", 1024)

    client = TestClient(ws.app, base_url="http://127.0.0.1:8765")
    oversized = b"x" * 2048
    resp = client.post(
        "/api/gaming/upload-clip",
        files={"file": ("grande.mp4", oversized, "video/mp4")},
    )
    assert resp.status_code == 413
    assert list(tmp_path.iterdir()) == []


def test_upload_clip_accepts_file_within_limit(tmp_path: Path, monkeypatch):
    """T3: Upload dentro do limite continua funcionando normalmente."""
    monkeypatch.setattr(ws, "GTA6_INPUTS_DIR", tmp_path)
    monkeypatch.setitem(ws.MAX_UPLOAD_BYTES, "video", 4096)

    client = TestClient(ws.app, base_url="http://127.0.0.1:8765")
    small = b"0" * 512
    with patch("app.web_server.index_trailer_scenes", return_value=[]), \
         patch("app.web_server.list_gta6_library_videos", return_value=[]):
        resp = client.post(
            "/api/gaming/upload-clip",
            files={"file": ("pequeno.mp4", small, "video/mp4")},
        )
    assert resp.status_code == 200
    saved = tmp_path / resp.json()["filename"]
    assert saved.exists()
    assert saved.stat().st_size == 512
    assert not any(p.name.endswith(".part") for p in tmp_path.iterdir())
