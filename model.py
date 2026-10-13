import re
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Change-request ids such as CHG0012345 or CR-1024. First character is
# alphanumeric so the value cannot be turned into a spreadsheet formula.
_CR_NUMBER = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{2,31}")
# One path segment, then up to five more. Flat names such as atlas still match.
# CARs/Hyundai/kona is the nested shape under the tenants group.
_SEGMENT = r"[A-Za-z0-9][A-Za-z0-9_-]{0,62}"
TENANT_PATH = re.compile(rf"{_SEGMENT}(?:/{_SEGMENT}){{0,5}}")
# Terraform address. No quotes, spaces, or shell metacharacters.
_RESOURCE = re.compile(r"[A-Za-z0-9_.\-\[\]]{1,128}")
_STATE = re.compile(r"[A-Za-z0-9_.-]{1,64}")

# Must stay in step with the checkboxes on the deploy form.
ALLOWED_SERVICES = (
    "airflow",
    "cloudbeaver",
    "gitlab_runner",
    "immuta",
    "jupyterhub",
    "mlflow",
    "s3uploader",
    "superset",
)
_ALLOWED_SERVICES = frozenset(ALLOWED_SERVICES)


class DeploymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targetEnvironment: Literal["dev", "test", "preprod", "prod"]
    allowProd: bool
    tenantList: str = Field(..., min_length=1, max_length=2000)
    pipelineTrigger: Literal["tenant_baseline", "kasm_vdi", "tenant_services"]
    taint: bool

    # Optional fields depending on the trigger
    replaceResource: Optional[str] = Field(default=None, max_length=128)
    tfRefresh: Optional[bool] = None
    replaceState: Optional[str] = Field(default=None, max_length=64)
    services: Optional[List[str]] = None
    # Off means every service. On requires one name from ALLOWED_SERVICES.
    singleServiceDeploy: bool = False
    selectedService: Optional[str] = Field(default=None, max_length=64)
    # Required for a real prod ship. Ignored for every other environment.
    crNumber: Optional[str] = Field(default=None, max_length=32)
    # Derived. Production with the confirmation off is plan-only.
    dryRun: bool = False

    @model_validator(mode="after")
    def constrain_pipeline_inputs(self):
        if self.targetEnvironment == "prod" and not self.allowProd:
            self.dryRun = True
            self.crNumber = None
        elif self.targetEnvironment == "prod":
            self.dryRun = False
            cr = (self.crNumber or "").strip().upper()
            if not _CR_NUMBER.fullmatch(cr):
                raise ValueError("Prod releases require a change request number (e.g. CHG0012345).")
            self.crNumber = cr
        else:
            self.dryRun = False
            self.crNumber = None

        self._constrain_single_service()

        if self.tenantList.strip() == "*":
            self.tenantList = "*"
            if not self.taint:
                self.replaceResource = None
                self.tfRefresh = None
                self.replaceState = None
                self.services = None
                return self
            return self._constrain_taint()

        names: list[str] = []
        seen: set[str] = set()
        for raw in self.tenantList.split(","):
            name = raw.strip()
            if not name or name in seen:
                continue
            if not TENANT_PATH.fullmatch(name):
                raise ValueError(
                    "Each tenant is a path such as CARs/Hyundai/kona. "
                    "Segments use letters, numbers, '_' or '-'."
                )
            seen.add(name)
            names.append(name)
        if not names:
            raise ValueError("At least one tenant is required.")
        if len(names) > 20:
            raise ValueError("A release can include at most 20 tenants.")
        self.tenantList = ",".join(names)

        if not self.taint:
            self.replaceResource = None
            self.tfRefresh = None
            self.replaceState = None
            self.services = None
            return self
        return self._constrain_taint()

    def _constrain_single_service(self):
        if self.pipelineTrigger != "tenant_services" or not self.singleServiceDeploy:
            self.singleServiceDeploy = False
            self.selectedService = None
            return
        service = (self.selectedService or "").strip()
        if service not in _ALLOWED_SERVICES:
            raise ValueError("Choose a service to deploy.")
        self.selectedService = service

    def _constrain_taint(self):
        if self.pipelineTrigger == "tenant_baseline":
            resource = (self.replaceResource or "").strip()
            if not _RESOURCE.fullmatch(resource):
                raise ValueError("Replacement resource must be a Terraform address such as module.database.")
            self.replaceResource = resource
            self.tfRefresh = bool(self.tfRefresh)
            self.replaceState = None
            self.services = None
        elif self.pipelineTrigger == "kasm_vdi":
            state = (self.replaceState or "").strip()
            if not _STATE.fullmatch(state):
                raise ValueError("Replace state must be letters, numbers, '.' or '-'.")
            self.replaceState = state
            self.replaceResource = None
            self.tfRefresh = None
            self.services = None
        else:
            chosen: list[str] = []
            for service in self.services or []:
                if service not in _ALLOWED_SERVICES:
                    raise ValueError("One or more selected services are not allowed.")
                if service not in chosen:
                    chosen.append(service)
            if not chosen:
                raise ValueError("At least one service must be selected for Tenant Services.")
            self.services = chosen
            self.replaceResource = None
            self.tfRefresh = None
            self.replaceState = None
        return self