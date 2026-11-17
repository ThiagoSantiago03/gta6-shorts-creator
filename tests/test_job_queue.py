import time
from pathlib import Path
import pytest

from app.job_queue import JobQueueManager, STAGE_NAMES


def test_job_queue_processes_5_jobs_with_failure_isolation_and_resumption(tmp_path: Path):
    """
    Critério de Aceite Tarefa 4.2:
    Em um lote de 5 vídeos, forçar uma falha no 3º vídeo:
    os outros 4 terminam com status 'ok', e o 3º pode ser retomado a partir da etapa que falhou.
    """
    manager = JobQueueManager(storage_dir=tmp_path / "jobs", auto_start_worker=False)

    topics = [
        "Pauta 1: Trailer 2 e gráficos de GTA 6",
        "Pauta 2: Física de água e animais em Leonida",
        "Pauta 3: Suposto preço de 80 dólares (vai falhar no tts)",
        "Pauta 4: Lucia e Jason assaltos e gameplay",
        "Pauta 5: Cidade de Vice City e mapa expandido",
    ]

    # Flag to simulate failure on Job 3 specifically in the 'tts' stage
    should_fail_job_3 = True

    def mock_pesquisa(job):
        time.sleep(0.01)

    def mock_roteiro(job):
        time.sleep(0.01)
        job.project_id = f"proj-{job.id}"

    def mock_tts(job):
        time.sleep(0.01)
        if "Pauta 3" in job.topic_title and should_fail_job_3:
            raise RuntimeError("Simulação de erro na síntese de voz (TTS indisponível)")

    def mock_render(job):
        time.sleep(0.01)

    def mock_qa(job):
        time.sleep(0.01)

    manager.register_stage_handler("pesquisa", mock_pesquisa)
    manager.register_stage_handler("roteiro", mock_roteiro)
    manager.register_stage_handler("tts", mock_tts)
    manager.register_stage_handler("render", mock_render)
    manager.register_stage_handler("qa", mock_qa)

    # Enqueue the 5 jobs
    jobs = manager.create_batch(topics)
    assert len(jobs) == 5

    # Start sequential worker and wait for batch to process
    manager.start_worker()
    manager._queue.join()  # waits until all 5 jobs in queue have finished their attempt

    # Verify results of initial batch run
    job_1 = manager.get_job(jobs[0].id)
    job_2 = manager.get_job(jobs[1].id)
    job_3 = manager.get_job(jobs[2].id)
    job_4 = manager.get_job(jobs[3].id)
    job_5 = manager.get_job(jobs[4].id)

    # Jobs 1, 2, 4, 5 must be completed with 'ok'
    assert job_1.status == "ok"
    assert job_2.status == "ok"
    assert job_4.status == "ok"
    assert job_5.status == "ok"

    for j in [job_1, job_2, job_4, job_5]:
        for st in STAGE_NAMES:
            assert j.stages[st]["status"] == "ok", f"Stage {st} in job {j.id} was not ok"

    # Job 3 must have failed at 'tts', with 'pesquisa' and 'roteiro' saved as 'ok'
    assert job_3.status == "erro"
    assert job_3.stages["pesquisa"]["status"] == "ok"
    assert job_3.stages["roteiro"]["status"] == "ok"
    assert job_3.stages["tts"]["status"] == "erro"
    assert job_3.stages["render"]["status"] == "pendente"
    assert job_3.stages["qa"]["status"] == "pendente"
    assert "Simulação de erro na síntese de voz" in job_3.error_message
    assert any("ERRO na etapa 'tts'" in log for log in job_3.logs)

    # NOW TEST RESUMPTION:
    # "Permitir retomar da etapa que falhou, sem refazer o que já deu certo."
    should_fail_job_3 = False  # The transient issue is resolved

    # Verify that pesquisa and roteiro handlers are NOT called again when resuming
    rerun_pesquisa_called = False

    def guard_pesquisa(job):
        nonlocal rerun_pesquisa_called
        rerun_pesquisa_called = True

    manager.register_stage_handler("pesquisa", guard_pesquisa)

    resumed_job = manager.resume_job(job_3.id)
    assert resumed_job.status in ("pendente", "executando")
    assert resumed_job.current_stage == "tts"  # resumes from tts!

    # Wait for resumed job to finish
    manager._queue.join()

    final_job_3 = manager.get_job(job_3.id)
    assert final_job_3.status == "ok"
    assert final_job_3.stages["tts"]["status"] == "ok"
    assert final_job_3.stages["render"]["status"] == "ok"
    assert final_job_3.stages["qa"]["status"] == "ok"
    # Ensure stages already completed were not re-run
    assert rerun_pesquisa_called is False

    manager.stop_worker()


def test_job_persistence_and_reload(tmp_path: Path):
    """Verify job states are persisted to JSON on disk and can be reloaded."""
    storage = tmp_path / "jobs"
    manager1 = JobQueueManager(storage_dir=storage, auto_start_worker=False)

    job = manager1.create_job("Teste de Persistência")
    assert (storage / f"{job.id}.json").exists()

    # Load in new manager instance
    manager2 = JobQueueManager(storage_dir=storage, auto_start_worker=False)
    loaded = manager2.get_job(job.id)

    assert loaded is not None
    assert loaded.id == job.id
    assert loaded.topic_title == "Teste de Persistência"
    assert loaded.status == "pendente"


def test_recovery_requeues_interrupted_and_pending_jobs(tmp_path: Path):
    """T1: Jobs 'executando' (interrompidos por crash/reinício) e 'pendente' devem ser reenfileirados e concluídos."""
    storage = tmp_path / "jobs"
    m1 = JobQueueManager(storage_dir=storage, auto_start_worker=False)
    running = m1.create_job("interrompido no meio")
    pending = m1.create_job("ainda na fila")
    running.status = "executando"
    running.stages["pesquisa"]["status"] = "ok"
    running.stages["roteiro"]["status"] = "ok"
    running.stages["tts"]["status"] = "executando"
    m1._save_job_to_disk(running)

    executed: list[tuple[str, str]] = []
    m2 = JobQueueManager(storage_dir=storage, auto_start_worker=False)
    for st in STAGE_NAMES:
        m2.register_stage_handler(st, lambda job, st=st: executed.append((job.id, st)))

    recovered = m2.recover_interrupted_jobs()
    assert set(recovered) == {running.id, pending.id}

    m2.start_worker()
    m2._queue.join()
    m2.stop_worker()

    assert m2.get_job(running.id).status == "ok"
    assert m2.get_job(pending.id).status == "ok"
    # Etapas já concluídas NÃO rodam de novo; a interrompida sim
    assert (running.id, "pesquisa") not in executed
    assert (running.id, "roteiro") not in executed
    assert (running.id, "tts") in executed
    assert (running.id, "render") in executed
    assert (running.id, "qa") in executed


def test_resume_allows_orphan_executando_job(tmp_path: Path):
    """T1: resume_job deve permitir retomar um job órfão com status 'executando' após reinício."""
    storage = tmp_path / "jobs"
    m1 = JobQueueManager(storage_dir=storage, auto_start_worker=False)
    job = m1.create_job("órfão")
    job.status = "executando"
    job.stages["pesquisa"]["status"] = "executando"
    m1._save_job_to_disk(job)

    m2 = JobQueueManager(storage_dir=storage, auto_start_worker=False)
    for st in STAGE_NAMES:
        m2.register_stage_handler(st, lambda j: None)
    resumed = m2.resume_job(job.id)
    assert resumed.status == "pendente"
    m2.start_worker()
    m2._queue.join()
    m2.stop_worker()
    assert m2.get_job(job.id).status == "ok"


def test_enqueue_is_deduplicated(tmp_path: Path):
    """T1: _enqueue deve deduplicar job_id já presente na fila."""
    m = JobQueueManager(storage_dir=tmp_path / "jobs", auto_start_worker=False)
    job = m.create_job("dup")
    assert m._enqueue(job.id) is False


def test_jobs_dir_is_absolute_and_independent_of_cwd(tmp_path: Path, monkeypatch):
    """T2: JOBS_DIR deve ser absoluto e não depender do diretório atual (cwd)."""
    from app.job_queue import JOBS_DIR

    assert JOBS_DIR.is_absolute()
    resolved_before = JOBS_DIR.resolve()
    monkeypatch.chdir(tmp_path)
    assert JOBS_DIR.resolve() == resolved_before


def test_pesquisa_editorial_verification_rules(tmp_path: Path):
    """T8: Etapa pesquisa bloqueia pauta sem status (ou nao_verificado) sem manual_approved e exige url/source em rumor/reportado."""
    from app.job_queue import setup_production_stage_handlers

    mgr = JobQueueManager(storage_dir=tmp_path / "jobs", auto_start_worker=False)
    setup_production_stage_handlers(mgr)
    # Substitui etapas posteriores por no-op para testar apenas a etapa pesquisa
    for st in STAGE_NAMES[1:]:
        mgr.register_stage_handler(st, lambda j: None)

    j_no_status = mgr.create_job("Pauta sem classificação", input_data={})
    j_manual_ok = mgr.create_job("Pauta sem classificação aprovada", input_data={"manual_approved": True})
    j_rumor_no_src = mgr.create_job(
        "Suposto vazamento sem fonte",
        input_data={"status_confiabilidade": "rumor"},
    )
    j_rumor_with_src = mgr.create_job(
        "Suposto vazamento com fonte",
        input_data={"status_confiabilidade": "rumor", "source": "Bloomberg"},
    )
    j_reportado_no_src = mgr.create_job(
        "Matéria reportada sem link",
        input_data={"status_confiabilidade": "reportado"},
    )

    mgr.start_worker()
    mgr._queue.join()
    mgr.stop_worker()

    res_no_status = mgr.get_job(j_no_status.id)
    assert res_no_status.status == "erro"
    assert res_no_status.stages["pesquisa"]["status"] == "erro"
    assert "Pauta não verificada: aprove manualmente para produzir." in (res_no_status.error_message or "")

    res_manual = mgr.get_job(j_manual_ok.id)
    assert res_manual.status == "ok"
    assert res_manual.stages["pesquisa"]["status"] == "ok"

    res_rumor_fail = mgr.get_job(j_rumor_no_src.id)
    assert res_rumor_fail.status == "erro"
    assert res_rumor_fail.stages["pesquisa"]["status"] == "erro"

    res_rumor_ok = mgr.get_job(j_rumor_with_src.id)
    assert res_rumor_ok.status == "ok"

    res_rep_fail = mgr.get_job(j_reportado_no_src.id)
    assert res_rep_fail.status == "erro"
    assert res_rep_fail.stages["pesquisa"]["status"] == "erro"



