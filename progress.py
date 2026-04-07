"""Thread-safe in-memory store for tracking scrape job progress."""

import threading
import time
import uuid


class ProgressStore:
    """Thread-safe in-memory store for tracking scrape job progress."""

    def __init__(self):
        self._jobs = {}
        self._lock = threading.Lock()

    def create_job(self) -> str:
        """Create a new job, return its ID."""
        self.cleanup_old()  # Remove jobs older than 5 minutes
        job_id = str(uuid.uuid4())[:8]
        with self._lock:
            self._jobs[job_id] = {
                "status": "pending",      # pending | running | complete | error
                "percent": 0,
                "stage": "",
                "label": "Waiting to start...",
                "error": None,
                "started_at": time.time(),
                "completed_at": None,
            }
        return job_id

    def update(self, job_id: str, stage: str, percent: int, label: str):
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update({
                    "status": "running",
                    "stage": stage,
                    "percent": min(percent, 100),
                    "label": label,
                })

    def complete(self, job_id: str):
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update({
                    "status": "complete",
                    "percent": 100,
                    "label": "Scrape complete",
                    "completed_at": time.time(),
                })

    def fail(self, job_id: str, error: str):
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update({
                    "status": "error",
                    "label": f"Error: {error}",
                    "error": error,
                    "completed_at": time.time(),
                })

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def cleanup_old(self, max_age_seconds=300):
        """Remove jobs older than max_age_seconds."""
        now = time.time()
        with self._lock:
            expired = [jid for jid, j in self._jobs.items()
                       if now - j["started_at"] > max_age_seconds]
            for jid in expired:
                del self._jobs[jid]


# Singleton instance — import this in admin.py and report_builder.py
progress_store = ProgressStore()
