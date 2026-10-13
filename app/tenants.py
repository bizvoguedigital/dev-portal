"""Tenant picker: the projects currently under the GitLab tenants group."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import startup
from gitlab_client import list_tenant_paths

router = APIRouter()

_NO_STORE = {"Cache-Control": "no-store"}


@router.get("/api/tenants")
async def get_tenants():
    paths = await list_tenant_paths(startup.gitlab_token)
    return JSONResponse({"tenants": [{"path": path} for path in paths]}, headers=_NO_STORE)
