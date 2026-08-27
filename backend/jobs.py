import uuid
import threading

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def create_job(question: str = "", organization_id: int = 1) -> str:
    job_id = str(uuid.uuid4())
    with _lock:
        _jobs[job_id] = {
            "organization_id": organization_id,
            "question": question,
            "status": "running",
            "stage": "starting",
            "sections": {},
            "report": None,
            "error": None,
            "messages": [],
        }
    return job_id


def update_stage(job_id: str, stage: str) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id]["stage"] = stage


def set_section(job_id: str, section: str, text: str) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id]["sections"][section] = text


def complete_job(job_id: str, report: str) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id]["status"] = "done"
            _jobs[job_id]["stage"] = "done"
            _jobs[job_id]["report"] = report


def fail_job(job_id: str, error: str) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = error


def get_job(job_id: str, organization_id: int = 1):
    with _lock:
        job = _jobs.get(job_id)
        if job is None or job["organization_id"] != organization_id:
            return None
        return job


def add_message(job_id: str, role: str, content: str) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id]["messages"].append({"role": role, "content": content})


def get_messages(job_id: str):
    with _lock:
        job = _jobs.get(job_id)
        return job["messages"] if job else []
