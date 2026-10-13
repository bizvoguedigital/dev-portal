"""Per-environment tenants a full release leaves out.

A hand-picked tenant still runs. Only an "all" release consults this list.
"""

from model import TENANT_PATH

_ENVIRONMENTS = ("dev", "test", "preprod", "prod")


def parse_skip_list(text: str) -> dict[str, dict[str, str]]:
    """Map environment to tenant path to reason. Bad lines are ignored."""
    found = {environment: {} for environment in _ENVIRONMENTS}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue
        environment, path = parts[0], parts[1]
        reason = parts[2].strip() if len(parts) > 2 else "skipped"
        if environment not in found or not TENANT_PATH.fullmatch(path):
            continue
        if any(ord(char) < 32 or ord(char) == 127 for char in reason):
            continue
        found[environment][path] = reason[:200]
    return found


def partition_tenants(paths: list[str], rules: dict[str, str]) -> tuple[list[str], list[tuple[str, str]]]:
    running: list[str] = []
    skipped: list[tuple[str, str]] = []
    for path in paths:
        reason = rules.get(path)
        if reason:
            skipped.append((path, reason))
        else:
            running.append(path)
    return running, skipped


def format_skipped(pairs: list[tuple[str, str]]) -> str:
    return "\n".join(f"{path}\t{reason}" for path, reason in pairs)


def read_skipped(text: str | None) -> list[dict]:
    rows = []
    for line in (text or "").splitlines():
        path, separator, reason = line.partition("\t")
        if separator and path:
            rows.append({"path": path, "reason": reason})
    return rows
