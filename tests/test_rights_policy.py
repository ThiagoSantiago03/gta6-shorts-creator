from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch

from app.models import MusicStoryProject, SceneItem
from app.qa_verifier import run_project_qa
from app.rights import media_allowed


ENTRY_LICENSED_COMMERCIAL = {
    "authorization_status": "licensed",
    "commercial_use_allowed": True,
}
ENTRY_OFFICIAL_COMMERCIAL = {
    "authorization_status": "official_permission",
    "commercial_use_allowed": True,
    "proof_url": "https://example.com/license.pdf",
}
ENTRY_FAIR_USE = {
    "authorization_status": "fair_use_claimed",
    "commercial_use_allowed": False,
}
ENTRY_FAIR_USE_WITH_FLAG = {
    "authorization_status": "fair_use_claimed",
    "commercial_use_allowed": True,  # Mesmo com flag errada, fair_use_claimed NUNCA passa em commercial
}
ENTRY_REVIEW_REQUIRED = {
    "authorization_status": "review_required",
    "commercial_use_allowed": False,
}
ENTRY_NON_COMMERCIAL_ONLY = {
    "authorization_status": "non_commercial_only",
    "commercial_use_allowed": False,
}
ENTRY_UNKNOWN = {
    "authorization_status": "unknown",
    "commercial_use_allowed": False,
}
ENTRY_RESTRICTED = {
    "authorization_status": "restricted",
    "commercial_use_allowed": False,
}


@pytest.mark.parametrize(
    "entry,intent,expected",
    [
        # Sem registro no catálogo: SEMPRE bloqueado em todas as 3 intenções
        (None, "commercial", False),
        (None, "editorial", False),
        (None, "non_commercial", False),
        # unknown / restricted: SEMPRE bloqueados nas 3 intenções
        (ENTRY_UNKNOWN, "commercial", False),
        (ENTRY_UNKNOWN, "editorial", False),
        (ENTRY_UNKNOWN, "non_commercial", False),
        (ENTRY_RESTRICTED, "commercial", False),
        (ENTRY_RESTRICTED, "editorial", False),
        (ENTRY_RESTRICTED, "non_commercial", False),
        # fair_use_claimed: NUNCA permitido em commercial; aceito em editorial e non_commercial
        (ENTRY_FAIR_USE, "commercial", False),
        (ENTRY_FAIR_USE_WITH_FLAG, "commercial", False),
        (ENTRY_FAIR_USE, "editorial", True),
        (ENTRY_FAIR_USE, "non_commercial", True),
        # review_required / non_commercial_only: bloqueados em commercial; aceitos em editorial/non_commercial
        (ENTRY_REVIEW_REQUIRED, "commercial", False),
        (ENTRY_REVIEW_REQUIRED, "editorial", True),
        (ENTRY_REVIEW_REQUIRED, "non_commercial", True),
        (ENTRY_NON_COMMERCIAL_ONLY, "commercial", False),
        (ENTRY_NON_COMMERCIAL_ONLY, "editorial", True),
        (ENTRY_NON_COMMERCIAL_ONLY, "non_commercial", True),
        # licensed / official_permission com commercial_use_allowed=True: permitidos em todas
        (ENTRY_LICENSED_COMMERCIAL, "commercial", True),
        (ENTRY_LICENSED_COMMERCIAL, "editorial", True),
        (ENTRY_LICENSED_COMMERCIAL, "non_commercial", True),
        (ENTRY_OFFICIAL_COMMERCIAL, "commercial", True),
        (ENTRY_OFFICIAL_COMMERCIAL, "editorial", True),
        (ENTRY_OFFICIAL_COMMERCIAL, "non_commercial", True),
    ],
)
def test_media_allowed_matrix(entry, intent, expected):
    """T5: Regra única media_allowed deve ser consistente nas 3 finalidades."""
    allowed, reason = media_allowed(entry, intent)
    assert allowed is expected
    if not expected:
        assert reason != ""


def test_qa_blocks_unknown_media_in_non_commercial_intent(tmp_path: Path):
    """T5: Bug corrigido — antes non_commercial não tinha ramo no QA e deixava mídia unknown passar."""
    pdir = tmp_path / "proj"
    pdir.mkdir()
    odir = tmp_path / "out"
    odir.mkdir()
    vfile = odir / "short.mp4"
    vfile.touch()

    project = MusicStoryProject(
        id="proj_non_comm",
        title="Teste Non-Commercial",
        publish_intent="non_commercial",
        outputs={"youtube_short": str(vfile)},
        scenes=[SceneItem(index=1, narration_text="Cena teste", video_path="inputs/gta6/ReelGTARealista.mp4")],
        used_media=[
            {
                "filename": "ReelGTARealista.mp4",
                "role": "scene_video",
                "license_type": "desconhecido",
                "authorization_status": "unknown",
                "commercial_use_allowed": False,
                "risk_level": "alto",
            }
        ],
    )
    probe = {
        "exists": True, "size_mb": 10.0, "duration_sec": 30.0, "resolution": "1080x1920",
        "fps": 30.0, "has_audio": True, "integrated_lufs": -14.0, "true_peak_dbfs": -1.5,
        "issues": [], "passed": True,
    }
    with patch("app.qa_verifier.probe_video_and_loudness", return_value=probe), \
         patch("app.qa_verifier.score_project_slideshow_risk", return_value={"verdict": "strong", "score": 1.0}), \
         patch("app.qa_verifier.write_packed_timeline", return_value=tmp_path / "t.txt"), \
         patch("app.qa_verifier.build_timeline_contact_sheet", return_value=tmp_path / "c.png"):
        rep = run_project_qa(project, pdir, odir)

    assert rep["qa_passed"] is False
    assert rep["media_audit"]["status"] == "blocked"
    assert len(rep["media_audit"]["blocking_reasons"]) >= 1
