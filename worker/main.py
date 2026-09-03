"""Celery application.

A solve takes seconds to minutes, so it cannot run inside a request. Celery
handles dispatch; Postgres remains the authoritative store of run state, since
`solve_runs.status` is what the UI polls. Celery is not the source of truth
here -- it is the thing that gets work off the request thread.
"""

from __future__ import annotations

import os

from celery import Celery

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")

app = Celery("waypoint", broker=REDIS_URL, backend=REDIS_URL)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone=os.environ.get("TIMEZONE", "Asia/Kuala_Lumpur"),
    enable_utc=True,
    # A solve has a hard time limit of its own; these are the outer guards for
    # a task that hangs somewhere else (a wedged OSRM call, say).
    task_time_limit=900,
    task_soft_time_limit=840,
    worker_prefetch_multiplier=1,   # long tasks: do not hoard the queue
    task_acks_late=True,            # re-queue if a worker dies mid-solve
    task_track_started=True,
)

# Import for side effects: registers the tasks on this app.
import worker.tasks  # noqa: E402,F401
