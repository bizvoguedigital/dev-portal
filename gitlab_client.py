import httpx
from urllib.parse import quote

from fastapi import HTTPException
from model import TENANT_PATH, DeploymentRequest
from config import Config
from logging_utils import logger

async def trigger_pipeline(
    gitlab_token: str,
    req: DeploymentRequest,
    username: str,
    tenant_list: str,
    deploy_all: bool,
):
    # Every key is always sent so the tenant job can echo the full portal payload.
    gitlab_variables = [
        {"key": "TARGET_ENV", "value": req.targetEnvironment},
        {"key": "TENANT_LIST", "value": tenant_list},
        {"key": "DEPLOY_ALL", "value": "true" if deploy_all else "false"},
        {"key": "PIPELINE_TRIGGER", "value": req.pipelineTrigger},
        {"key": "TAINT", "value": str(req.taint).lower()},
        {"key": "TRIGGERED_BY", "value": username},
        {"key": "CR_NUMBER", "value": req.crNumber or ""},
        {"key": "DRY_RUN", "value": "true" if req.dryRun else "false"},
        {"key": "REPLACE_RESOURCE", "value": (req.replaceResource or "") if req.taint else ""},
        {"key": "TF_REFRESH", "value": "" if req.tfRefresh is None or not req.taint else str(req.tfRefresh).lower()},
        {"key": "REPLACE_STATE", "value": (req.replaceState or "") if req.taint else ""},
        {"key": "SERVICES_LIST", "value": ",".join(req.services) if req.taint and req.services else ""},
        {"key": "SINGLE_SERVICE_DEPLOY", "value": "true" if req.singleServiceDeploy else "false"},
        {"key": "SELECTED_SERVICE", "value": req.selectedService or ""},
    ]

    payload = {
        "ref": "main",
        "variables": gitlab_variables
    }

    headers = {
        "PRIVATE-TOKEN": gitlab_token,
        "Content-Type": "application/json"
    }

    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        try:
            # Using the URL from config.py. Redirects are not followed: the token is on this request.
            response = await client.post(Config.GITLAB_API_URL, json=payload, headers=headers)

            if response.status_code not in (200, 201):
                logger.warning("GitLab API error: status %s", response.status_code)
                raise HTTPException(status_code=502, detail="GitLab rejected the pipeline trigger request.")

            return response.json()
        except httpx.RequestError as exc:
            logger.warning("Failed to connect to GitLab: %s", exc)
            raise HTTPException(status_code=502, detail="Failed to connect to GitLab.")


async def get_pipeline_status(gitlab_token: str, pipeline_id: int) -> dict:
    url = f"{Config.GITLAB_PROJECT_API}/pipelines/{pipeline_id}"
    headers = {"PRIVATE-TOKEN": gitlab_token}
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        try:
            response = await client.get(url, headers=headers)
        except httpx.RequestError as exc:
            logger.warning("Failed to read pipeline %s: %s", pipeline_id, exc)
            raise HTTPException(status_code=502, detail="Failed to connect to GitLab.")

    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="Pipeline not found.")
    if response.status_code != 200:
        logger.warning("GitLab pipeline status error: status %s", response.status_code)
        raise HTTPException(status_code=502, detail="GitLab did not return the pipeline status.")

    data = response.json()
    return {"id": data.get("id"), "status": data.get("status")}


async def fetch_skip_list(gitlab_token: str) -> str:
    """Raw skip file from the orchestrator repo. Missing file means nobody is skipped."""
    path = quote("scripts/skip-tenants.txt", safe="")
    url = f"{Config.GITLAB_PROJECT_API}/repository/files/{path}/raw?ref=main"
    headers = {"PRIVATE-TOKEN": gitlab_token}
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        try:
            response = await client.get(url, headers=headers)
        except httpx.RequestError as exc:
            logger.warning("Failed to read the skip list: %s", exc)
            raise HTTPException(status_code=502, detail="Failed to connect to GitLab.")
    if response.status_code == 404:
        return ""
    if response.status_code != 200:
        logger.warning("GitLab skip list error: status %s", response.status_code)
        raise HTTPException(status_code=502, detail="GitLab did not return the skip list.")
    return response.text


async def _pipeline_bridges(gitlab_token: str, pipeline_id: int) -> list[dict]:
    url = f"{Config.GITLAB_PROJECT_API}/pipelines/{pipeline_id}/bridges"
    headers = {"PRIVATE-TOKEN": gitlab_token}
    found: list[dict] = []
    page = "1"
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        for _ in range(5):
            try:
                response = await client.get(
                    url,
                    headers=headers,
                    params={"per_page": "100", "page": page},
                )
            except httpx.RequestError as exc:
                logger.warning("Failed to read pipeline bridges: %s", exc)
                raise HTTPException(status_code=502, detail="Failed to connect to GitLab.")
            if response.status_code != 200:
                logger.warning("GitLab bridge list error: status %s", response.status_code)
                raise HTTPException(status_code=502, detail="GitLab did not return the tenant results.")
            payload = response.json()
            if not isinstance(payload, list):
                raise HTTPException(status_code=502, detail="GitLab did not return the tenant results.")
            found.extend(item for item in payload if isinstance(item, dict))
            page = (response.headers.get("x-next-page") or "").strip()
            if not page:
                break
    return found


async def tenant_result_counts(gitlab_token: str, pipeline_id: int) -> dict:
    """How many tenant pipelines ran or failed under this orchestrator pipeline."""
    bridges = await _pipeline_bridges(gitlab_token, pipeline_id)
    child_id = None
    for bridge in bridges:
        if bridge.get("name") != "trigger-downstream":
            continue
        downstream = bridge.get("downstream_pipeline") or {}
        if isinstance(downstream, dict) and downstream.get("id"):
            child_id = int(downstream["id"])
    if not child_id:
        return {}
    ran = 0
    failed = 0
    for bridge in await _pipeline_bridges(gitlab_token, child_id):
        status = bridge.get("status") or ""
        if status == "success":
            ran += 1
        elif status in {"failed", "canceled", "skipped"}:
            failed += 1
    return {"ran": ran, "failed": failed}


def cicd_enabled(project: dict) -> bool:
    """False when this GitLab project has CI/CD turned off.

    Settings → General → Visibility, project features, permissions → CI/CD
    set to Disabled is builds_access_level "disabled". Older API payloads
    say the same thing with jobs_enabled false. A project that does not
    report either field stays in the list.
    """
    access = project.get("builds_access_level")
    if isinstance(access, str) and access.strip():
        return access.strip().lower() != "disabled"
    jobs = project.get("jobs_enabled")
    if isinstance(jobs, bool):
        return jobs
    return True


def relative_tenant_path(path_with_namespace: str, group: str) -> str | None:
    """Path under the tenants group, or None when it is not a selectable tenant."""
    prefix = group.strip().strip("/") + "/"
    if not isinstance(path_with_namespace, str) or not path_with_namespace.startswith(prefix):
        return None
    relative = path_with_namespace[len(prefix):]
    if not TENANT_PATH.fullmatch(relative):
        return None
    return relative


async def list_tenant_paths(gitlab_token: str) -> list[str]:
    """Projects under the tenants group whose CI/CD feature is still on."""
    group = Config.GITLAB_TENANTS_GROUP
    url = f"{Config.GITLAB_BASE_URL.rstrip('/')}/api/v4/groups/{quote(group, safe='')}/projects"
    headers = {"PRIVATE-TOKEN": gitlab_token}
    found: list[str] = []
    seen: set[str] = set()
    page = "1"

    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        for _ in range(20):
            try:
                response = await client.get(
                    url,
                    headers=headers,
                    params={
                        "include_subgroups": "true",
                        "archived": "false",
                        "with_shared": "false",
                        "per_page": "100",
                        "page": page,
                        "order_by": "path",
                        "sort": "asc",
                    },
                )
            except httpx.RequestError as exc:
                logger.warning("Failed to list tenants: %s", exc)
                raise HTTPException(status_code=502, detail="Failed to connect to GitLab.")

            if response.status_code != 200:
                logger.warning("GitLab tenant list error: status %s", response.status_code)
                raise HTTPException(status_code=502, detail="GitLab did not return the tenant list.")

            payload = response.json()
            if not isinstance(payload, list):
                raise HTTPException(status_code=502, detail="GitLab did not return the tenant list.")

            for project in payload:
                if not isinstance(project, dict):
                    continue
                relative = relative_tenant_path(str(project.get("path_with_namespace") or ""), group)
                if not relative or relative in seen:
                    continue
                if not cicd_enabled(project):
                    logger.info("Skipping tenant with CI/CD turned off: %s", relative)
                    continue
                seen.add(relative)
                found.append(relative)

            page = (response.headers.get("x-next-page") or "").strip()
            if not page:
                break

    return found