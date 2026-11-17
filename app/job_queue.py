from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

JOBS_DIR = Path("inputs") / "jobs"
STAGE_NAMES = ["pesquisa", "roteiro", "tts", "render", "qa"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class JobItem:
    id: str
    project_id: str | None = None
    topic_title: str = ""
    status: str = "pendente"  # "pendente", "executando", "ok", "erro"
    current_stage: str = "pesquisa"
    stages: dict[str, dict[str, Any]] = field(default_factory=dict)
    error_message: str | None = None
    logs: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    input_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobItem:
        return cls(
            id=data["id"],
            project_id=data.get("project_id"),
            topic_title=data.get("topic_title", ""),
            status=data.get("status", "pendente"),
            current_stage=data.get("current_stage", "pesquisa"),
            stages=data.get("stages", {}),
            error_message=data.get("error_message"),
            logs=data.get("logs", []),
            created_at=data.get("created_at", _now_iso()),
            updated_at=data.get("updated_at", _now_iso()),
            input_data=data.get("input_data", {}),
        )


class JobQueueManager:
    """
    Sequential, robust FIFO job execution queue for GTA 6 Shorts batches (Tarefa 4.2).
    - Executes 1 job at a time (zero CPU/disk contention)
    - 5 explicit stages: pesquisa -> roteiro -> tts -> render -> qa
    - Isolates failures: failed job does not crash or stop subsequent jobs
    - Allows resuming failed jobs from the exact failed stage without re-running completed stages
    - Logs every event per job and persists states to inputs/jobs/{job_id}.json
    """

    def __init__(self, storage_dir: Path | None = None, auto_start_worker: bool = True):
        self.storage_dir = storage_dir or JOBS_DIR
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._queue: queue.Queue[str] = queue.Queue()
        self._jobs: dict[str, JobItem] = {}
        self._lock = threading.RLock()
        self._stage_handlers: dict[str, Callable[[JobItem], None]] = {}
        self._worker_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        self._load_persisted_jobs()
        if auto_start_worker:
            self.start_worker()

    def register_stage_handler(self, stage: str, handler: Callable[[JobItem], None]) -> None:
        with self._lock:
            self._stage_handlers[stage] = handler

    def start_worker(self) -> None:
        with self._lock:
            if self._worker_thread and self._worker_thread.is_alive():
                return
            self._stop_event.clear()
            self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
            self._worker_thread.start()

    def stop_worker(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=timeout)

    def create_job(self, topic_title: str, input_data: dict[str, Any] | None = None) -> JobItem:
        with self._lock:
            jid = f"job-{uuid.uuid4().hex[:8]}"
            initial_stages = {
                st: {
                    "name": st,
                    "status": "pendente",
                    "started_at": None,
                    "ended_at": None,
                    "error": None,
                }
                for st in STAGE_NAMES
            }
            job = JobItem(
                id=jid,
                topic_title=topic_title,
                status="pendente",
                current_stage=STAGE_NAMES[0],
                stages=initial_stages,
                logs=[f"[{_now_iso()}] Job criado com pauta '{topic_title}'."],
                input_data=input_data or {},
            )
            self._jobs[jid] = job
            self._save_job_to_disk(job)
            self._queue.put(jid)
            logger.info("Job %s ('%s') enfileirado.", jid, topic_title)
            return job

    def create_batch(self, topics: list[str | dict[str, Any]]) -> list[JobItem]:
        created: list[JobItem] = []
        for item in topics:
            if isinstance(item, dict):
                title = str(item.get("title", "")).strip()
                job = self.create_job(topic_title=title, input_data=item)
            else:
                job = self.create_job(topic_title=str(item).strip())
            created.append(job)
        return created

    def get_job(self, job_id: str) -> JobItem | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                # Try reading from disk
                p = self.storage_dir / f"{job_id}.json"
                if p.exists():
                    try:
                        data = json.loads(p.read_text(encoding="utf-8"))
                        job = JobItem.from_dict(data)
                        self._jobs[job_id] = job
                    except Exception as exc:
                        logger.warning("Falha ao ler job persistido %s: %s", job_id, exc)
            return job

    def list_jobs(self) -> list[JobItem]:
        with self._lock:
            return list(self._jobs.values())

    def resume_job(self, job_id: str) -> JobItem:
        with self._lock:
            job = self.get_job(job_id)
            if not job:
                raise ValueError(f"Job {job_id} não encontrado.")

            if job.status == "executando":
                logger.warning("Job %s já está em execução.", job_id)
                return job

            # Find first stage that is not 'ok'
            first_pending = None
            for st_name in STAGE_NAMES:
                st_info = job.stages.get(st_name, {})
                if st_info.get("status") != "ok":
                    if first_pending is None:
                        first_pending = st_name
                    # Reset erro/executando to pendente for resumption
                    st_info["status"] = "pendente"
                    st_info["error"] = None
                    job.stages[st_name] = st_info

            job.current_stage = first_pending or STAGE_NAMES[-1]
            job.status = "pendente"
            job.error_message = None
            job.updated_at = _now_iso()
            job.logs.append(f"[{job.updated_at}] Job retomado a partir da etapa '{job.current_stage}'.")
            self._save_job_to_disk(job)
            self._queue.put(job.id)
            logger.info("Job %s retomado a partir da etapa '%s'.", job.id, job.current_stage)
            return job

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                job_id = self._queue.get(timeout=0.4)
            except queue.Empty:
                continue

            try:
                self._process_single_job(job_id)
            except Exception as exc:
                logger.exception("Erro inesperado no loop de execução do job %s: %s", job_id, exc)
            finally:
                self._queue.task_done()

    def _process_single_job(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if not job:
            return

        with self._lock:
            job.status = "executando"
            job.updated_at = _now_iso()
            job.logs.append(f"[{job.updated_at}] Iniciando processamento do job.")
            self._save_job_to_disk(job)

        failed = False
        for st_name in STAGE_NAMES:
            st_info = job.stages.get(st_name, {})
            # Skip stages that already completed successfully
            if st_info.get("status") == "ok":
                continue

            with self._lock:
                job.current_stage = st_name
                st_info["status"] = "executando"
                st_info["started_at"] = _now_iso()
                job.stages[st_name] = st_info
                job.logs.append(f"[{st_info['started_at']}] Etapa '{st_name}' iniciada.")
                self._save_job_to_disk(job)

            handler = self._stage_handlers.get(st_name, self._default_stage_handler)
            try:
                handler(job)
                with self._lock:
                    st_info["status"] = "ok"
                    st_info["ended_at"] = _now_iso()
                    job.stages[st_name] = st_info
                    job.logs.append(f"[{st_info['ended_at']}] Etapa '{st_name}' concluída com sucesso.")
                    self._save_job_to_disk(job)
            except Exception as exc:
                logger.error("Falha na etapa '%s' do job %s: %s", st_name, job.id, exc)
                with self._lock:
                    st_info["status"] = "erro"
                    st_info["ended_at"] = _now_iso()
                    st_info["error"] = str(exc)
                    job.stages[st_name] = st_info
                    job.status = "erro"
                    job.error_message = f"Falha na etapa '{st_name}': {exc}"
                    job.updated_at = _now_iso()
                    job.logs.append(f"[{job.updated_at}] ERRO na etapa '{st_name}': {exc}")
                    self._save_job_to_disk(job)
                failed = True
                break

        if not failed:
            with self._lock:
                job.status = "ok"
                job.updated_at = _now_iso()
                job.logs.append(f"[{job.updated_at}] Todas as etapas concluídas com sucesso. Short pronto!")
                self._save_job_to_disk(job)

    def _default_stage_handler(self, job: JobItem) -> None:
        """Default handler when no custom handler is registered (simulates work)."""
        time.sleep(0.05)

    def _save_job_to_disk(self, job: JobItem) -> None:
        try:
            target = self.storage_dir / f"{job.id}.json"
            tmp = self.storage_dir / f"{job.id}.tmp"
            tmp.write_text(json.dumps(job.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(target)
        except Exception as exc:
            logger.warning("Falha ao salvar estado do job %s no disco: %s", job.id, exc)

    def _load_persisted_jobs(self) -> None:
        for p in self.storage_dir.glob("job-*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                job = JobItem.from_dict(data)
                self._jobs[job.id] = job
            except Exception as exc:
                logger.warning("Falha ao carregar job %s: %s", p.name, exc)


def setup_production_stage_handlers(manager: JobQueueManager) -> None:
    """Wire real production functions to each stage of the job queue."""
    from app.gaming_scriptwriter import generate_gaming_script
    from app.models import MusicStoryProject, SceneItem
    from app.tts_engine import synthesize_narration
    from app.video_renderer import render_full_project
    from app.qa_verifier import run_project_qa
    from app.gaming_media import auto_blend_gaming_clips_and_images, auto_select_gaming_soundtrack
    from app.web_server import _save_project, _load_project, _project_dir, _slugify, OUTPUTS_DIR

    def _pesquisa(job: JobItem) -> None:
        # Research is provided or validated
        if not job.topic_title:
            raise ValueError("Pauta sem título informado.")
        job.logs.append(f"[{_now_iso()}] Pauta validada: '{job.topic_title}'.")

    def _roteiro(job: JobItem) -> None:
        topic_title = job.topic_title
        pid = f"gta6-{_slugify(topic_title)}-{uuid.uuid4().hex[:6]}"
        g_data = generate_gaming_script(
            topic_title=topic_title,
            game_name="GTA 6",
            topic_angle=job.input_data.get("suggested_angle", ""),
            source_url=job.input_data.get("url", ""),
            fact_confidence=job.input_data.get("confidence", "Notícia / Imprensa"),
            reference_notes="",
            script_engine="free_research",
            accent_color="#00F0FF",
            viral_archetype="auto",
            speech_rate="+14%",
        )
        resolved_topic = str(g_data.get("resolved_topic") or topic_title)
        scenes_objs = [
            s if isinstance(s, SceneItem) else SceneItem(**s)
            for s in g_data["scenes"]
        ]
        project = MusicStoryProject(
            id=pid,
            niche="gaming_news",
            title=resolved_topic,
            game="GTA 6",
            song_title=resolved_topic,
            artist="GTA 6",
            year=g_data.get("resolved_year", "2026"),
            genre="Games / GTA 6",
            topic_angle=str(g_data.get("resolved_angle") or job.input_data.get("suggested_angle") or ""),
            topic_source_url=job.input_data.get("url", ""),
            fonte_primaria=job.input_data.get("source", ""),
            data_publicacao=job.input_data.get("published_at", ""),
            fact_confidence=job.input_data.get("confidence", "Notícia / Imprensa"),
            status_confiabilidade=job.input_data.get("status_confiabilidade", "reportado"),
            max_duration_sec=58.0,
            speech_rate="+14%",
            viral_archetype=str(g_data.get("viral_archetype") or "auto"),
            youtube_title=g_data["youtube_title"],
            youtube_description=g_data["youtube_description"],
            youtube_tags=g_data["youtube_tags"],
            tiktok_caption=g_data["tiktok_caption"],
            hook_title=g_data["hook_title"],
            narration_full=g_data["narration_full"],
            scenes=scenes_objs,
            instagram_caption=g_data["instagram_caption"],
            hashtags=g_data["hashtags"],
            accent_color="#00F0FF",
            style_preset="vice_city_neon",
            subtitle_style="vice_city",
            tts_engine="edge_tts",
            voice_name="pt-BR-AntonioNeural",
            status="job_roteiro_ok",
        )
        auto_track, auto_start = auto_select_gaming_soundtrack(project)
        if auto_track is not None and auto_track.exists():
            project.audio_file = str(auto_track)
            project.audio_clip_start = auto_start
        pdir = _project_dir(pid)
        try:
            auto_blend_gaming_clips_and_images(project, pdir, force_reassign=False)
        except Exception as exc_blend:
            logger.warning("Falha ao selecionar mídias preliminares: %s", exc_blend)
        _save_project(project)
        job.project_id = pid
        job.logs.append(f"[{_now_iso()}] Roteiro gerado e projeto '{pid}' criado.")

    def _tts(job: JobItem) -> None:
        if not job.project_id:
            raise ValueError("Project ID ausente para síntese de voz.")
        project = _load_project(job.project_id)
        pdir = _project_dir(job.project_id)
        full_wav, scene_spans, all_words, actual_engine = synthesize_narration(
            scenes=project.scenes,
            work_dir=pdir,
            tts_engine=project.tts_engine,
            voice_name=project.voice_name,
            rate=project.speech_rate,
            max_duration_sec=project.max_duration_sec,
        )
        project.status = f"job_tts_ok ({actual_engine})"
        _save_project(project)
        job.logs.append(f"[{_now_iso()}] Narração TTS concluída com sucesso via {actual_engine}.")

    def _render(job: JobItem) -> None:
        if not job.project_id:
            raise ValueError("Project ID ausente para renderização.")
        project = _load_project(job.project_id)
        pdir = _project_dir(job.project_id)
        odir = OUTPUTS_DIR / job.project_id
        rendered = render_full_project(project, pdir, odir)
        _save_project(rendered)
        job.logs.append(f"[{_now_iso()}] Renderização de vídeo concluída em {odir}.")

    def _qa(job: JobItem) -> None:
        if not job.project_id:
            raise ValueError("Project ID ausente para QA.")
        project = _load_project(job.project_id)
        pdir = _project_dir(job.project_id)
        odir = OUTPUTS_DIR / job.project_id
        qa_rep = run_project_qa(project, pdir, odir)
        project.qa_report = qa_rep
        _save_project(project)
        qa_status = "APROVADO" if qa_rep.get("qa_passed") else "ATENÇÃO"
        job.logs.append(f"[{_now_iso()}] QA concluído com veredito: {qa_status}.")

    manager.register_stage_handler("pesquisa", _pesquisa)
    manager.register_stage_handler("roteiro", _roteiro)
    manager.register_stage_handler("tts", _tts)
    manager.register_stage_handler("render", _render)
    manager.register_stage_handler("qa", _qa)


# Global singleton instance for the app
JOB_MANAGER = JobQueueManager()
setup_production_stage_handlers(JOB_MANAGER)

