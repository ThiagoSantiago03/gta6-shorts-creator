import shutil
from pathlib import Path
from app.config import PROJECTS_DIR
from app.models import MusicStoryProject, SceneItem
from app.web_server import _save_project, _load_project


def test_atomic_persistence_and_backup():
    pid = "test-atomic-persist-unit"
    pdir = PROJECTS_DIR / pid
    try:
        p = MusicStoryProject(
            id=pid,
            title="Versão 1 - Teste",
            narration_full="Narração teste",
            scenes=[SceneItem(index=1, narration_text="Cena inicial")]
        )
        # 1. Primeira gravação: deve criar project.json
        saved = _save_project(p)
        assert (pdir / "project.json").exists()
        assert not (pdir / "project.json.bak").exists()

        # 2. Segunda gravação: deve criar project.json.bak com a versão anterior
        saved.title = "Versão 2 - Atualizada"
        _save_project(saved)
        assert (pdir / "project.json.bak").exists()
        assert "Versão 1 - Teste" in (pdir / "project.json.bak").read_text(encoding="utf-8")
        assert "Versão 2 - Atualizada" in (pdir / "project.json").read_text(encoding="utf-8")

        # 3. Leitura normal
        loaded = _load_project(pid)
        assert loaded.title == "Versão 2 - Atualizada"

        # 4. Falha induzida (arquivo primário corrompido/vazio) -> fallback para .bak
        (pdir / "project.json").write_text("", encoding="utf-8")
        recovered = _load_project(pid)
        assert recovered.title == "Versão 1 - Teste"
    finally:
        shutil.rmtree(pdir, ignore_errors=True)
