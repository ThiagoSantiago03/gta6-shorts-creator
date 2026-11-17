from pathlib import Path
from app.gaming_media import load_media_catalog, get_media_catalog_entry, record_project_used_media
from app.models import MusicStoryProject, SceneItem


def test_media_catalog_loading():
    catalog = load_media_catalog()
    assert "items" in catalog
    items = catalog["items"]
    assert len(items) >= 8

    # Verificar trailer oficial
    t1 = get_media_catalog_entry("GTA Trailer 1.mp4")
    assert t1 is not None
    assert t1["copyright_owner"] == "Rockstar Games / Take-Two Interactive"
    assert t1["license_type"] == "promotional_fair_use"
    assert t1["risk_level"] == "medio"

    # Verificar áudio não verificado
    a1 = get_media_catalog_entry("Miami Night Highway.mp3")
    assert a1 is not None
    assert a1["risk_level"] == "alto"
    assert a1["license_type"] == "royalty_free_unverified"


def test_project_media_audit_recording():
    project = MusicStoryProject(
        id="test-audit-proj",
        title="Auditoria de Midias",
        audio_file="inputs/gta6/Neon Shadows.mp3",
        scenes=[
            SceneItem(index=1, narration_text="Cena 1", video_path="inputs/gta6/GTA Trailer 1.mp4"),
            SceneItem(index=2, narration_text="Cena 2", video_path="inputs/gta6/ReelGTARealista.mp4"),
        ]
    )
    used = record_project_used_media(project)
    assert len(used) == 3
    roles = {u["role"] for u in used}
    assert "soundtrack" in roles
    assert "scene_video" in roles

    high_risk = [u for u in used if u.get("risk_level") == "alto"]
    assert len(high_risk) >= 2  # Neon Shadows + ReelGTARealista
