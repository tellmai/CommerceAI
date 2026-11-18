"""Single-concurrency PostgreSQL queue with leases and fencing tokens."""

from datetime import timedelta
import logging
import signal
import time

from sqlalchemy import and_, or_, select

from app.db import ImportBatch, Job, Session, new_id, now
from app.errors import BusinessError

log = logging.getLogger("commerceai.worker")


def claim():
    with Session.begin() as db:
        expired = list(
            db.scalars(
                select(Job)
                .where(Job.status == "RUNNING", Job.attempts >= 3, Job.lease_until < now())
                .with_for_update(skip_locked=True)
            )
        )
        for old in expired:
            old.status, old.error = "FAILED", "LEASE_EXPIRED: 任务重试次数已用尽"
            if old.kind == "import":
                batch = db.get(ImportBatch, old.payload["batch_id"])
                batch.status = "FAILED"
            elif old.kind == "market_search":
                from app.market_service import record_search_failure

                record_search_failure(db, old)
        job = db.scalar(
            select(Job)
            .where(
                Job.attempts < 3,
                or_(
                    and_(Job.status == "QUEUED", Job.run_after <= now()),
                    and_(Job.status == "RUNNING", Job.lease_until < now()),
                ),
            )
            .order_by(Job.run_after)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not job:
            return None
        job.status, job.lease_token = "RUNNING", new_id()
        job.attempts += 1
        job.lease_until = now() + timedelta(seconds=120)
        return job.id, job.lease_token


def execute(job_id, token):
    try:
        with Session() as db:
            job = db.get(Job, job_id)
            kind, workspace, payload = job.kind, job.workspace, job.payload
        if kind == "import":
            from app.ingestion import execute_import

            execute_import(job_id, token)
        elif kind == "score":
            from app.ingestion import assert_lease
            from app.selection import run_score

            with Session.begin() as db:
                job = assert_lease(db, job_id, token)
                score = run_score(db, workspace, payload["candidate_id"], payload["strategy_id"])
                job.status, job.progress, job.result = "SUCCEEDED", 100, {"score_id": score.id}
        elif kind == "market_search":
            from app.market_service import execute_search

            execute_search(job_id, token)
        else:
            raise BusinessError("UNKNOWN_JOB", "未知任务类型")
    except Exception as exc:
        with Session.begin() as db:
            job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if not job or job.lease_token != token or job.status != "RUNNING":
                return
            code = getattr(exc, "code", "INTERNAL_ERROR")
            job.error = (
                code
                + ": "
                + (exc.message if isinstance(exc, BusinessError) else "任务执行失败，请检查服务日志")
            )
            retry = not isinstance(exc, (BusinessError, ValueError)) and job.attempts < 3
            job.status = "QUEUED" if retry else "FAILED"
            job.run_after = now() + timedelta(seconds=2**job.attempts)
            if job.kind == "import":
                batch = db.get(ImportBatch, job.payload["batch_id"])
                batch.status = job.status
            elif job.kind == "market_search":
                from app.market_service import record_search_failure

                record_search_failure(db, job)
        log.error("job_failed job_id=%s error_code=%s exception_type=%s", job_id, code, type(exc).__name__)


def run_once():
    claimed = claim()
    if claimed:
        execute(*claimed)
        return True
    return False


def main():
    running = True

    def stop(_signal, _frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logging.basicConfig(level=logging.INFO)
    while running:
        if not run_once():
            time.sleep(1)


if __name__ == "__main__":
    main()
