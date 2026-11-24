"""
Generic durable-job contracts shared by import, export and query-export managers.

These limits and the admission error have nothing to do with XLSX rendering:
they govern the shared PostgreSQL-backed job queue (`data_jobs`). They used to
live in ``export_jobs.py`` because the durable queue grew out of the export
runner; that forced import-only modules to import an export-specific module.
The values are unchanged — this is a dependency-direction cleanup only.
"""

# Finished artifacts stay addressable for an hour: long enough to retry a
# download, short enough that the disk does not grow without bound.
JOB_TTL_SECONDS = 3600.0
# Global admission caps (all job kinds together), enforced in
# ``JobRepository.create``. Per-kind worker slots are a separate limit.
MAX_ACTIVE_JOBS = 8
MAX_ACTIVE_JOBS_PER_USER = 2


class ExportQueueFullError(RuntimeError):
    """
    The shared job queue is full (HTTP 429).

    The name is kept for API/back-compat: it predates imports and query exports
    using the same queue.
    """
