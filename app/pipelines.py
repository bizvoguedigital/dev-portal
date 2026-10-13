"""Pipeline status for releases this portal started."""

from fastapi import APIRouter, HTTPException

from app import startup
from gitlab_client import get_pipeline_status, tenant_result_counts
from logging_utils import logger
from releases_store import (
    TERMINAL_PIPELINE_STATUSES,
    clear_environment_lock,
    portal_started_pipeline,
    set_pipeline_status,
    skipped_tenants_for_pipeline,
)
from skip_list import read_skipped

router = APIRouter()


@router.get("/api/pipelines/{pipeline_id}")
async def pipeline_status(pipeline_id: int):
    if pipeline_id < 1 or not portal_started_pipeline(pipeline_id):
        raise HTTPException(status_code=404, detail="Pipeline not found.")
    result = await get_pipeline_status(startup.gitlab_token, pipeline_id)
    status = result.get("status")
    if status:
        set_pipeline_status(pipeline_id, status)
        if status in TERMINAL_PIPELINE_STATUSES:
            clear_environment_lock(pipeline_id)
            result["skipped"] = read_skipped(skipped_tenants_for_pipeline(pipeline_id))
            try:
                result.update(await tenant_result_counts(startup.gitlab_token, pipeline_id))
            except HTTPException:
                logger.warning("Tenant result counts were not available for pipeline %s", pipeline_id)
    return result
