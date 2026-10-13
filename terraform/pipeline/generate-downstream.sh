#!/bin/sh
# Writes generated-ci.yml: one multi-project trigger per tenant in TENANT_LIST.
set -eu

case "${PIPELINE_TRIGGER:-}" in
  tenant_baseline|kasm_vdi|tenant_services) ;;
  *)
    echo "PIPELINE_TRIGGER must be tenant_baseline, kasm_vdi, or tenant_services" >&2
    exit 1
    ;;
esac

if [ -z "${TENANT_LIST:-}" ]; then
  echo "TENANT_LIST is empty" >&2
  exit 1
fi

yaml_quote() {
  if printf '%s' "$1" | grep -q '["\\[:cntrl:]]'; then
    echo "refusing to forward a variable that is not plain text" >&2
    exit 1
  fi
  printf '"%s"' "$1"
}

cat > generated-ci.yml <<EOF
stages:
  - downstream

.portal-variables:
  variables:
    TARGET_ENV: $(yaml_quote "${TARGET_ENV:-}")
    TENANT_LIST: $(yaml_quote "${TENANT_LIST:-}")
    DEPLOY_ALL: $(yaml_quote "${DEPLOY_ALL:-}")
    PIPELINE_TRIGGER: $(yaml_quote "${PIPELINE_TRIGGER:-}")
    TAINT: $(yaml_quote "${TAINT:-}")
    TRIGGERED_BY: $(yaml_quote "${TRIGGERED_BY:-}")
    CR_NUMBER: $(yaml_quote "${CR_NUMBER:-}")
    DRY_RUN: $(yaml_quote "${DRY_RUN:-}")
    REPLACE_RESOURCE: $(yaml_quote "${REPLACE_RESOURCE:-}")
    TF_REFRESH: $(yaml_quote "${TF_REFRESH:-}")
    REPLACE_STATE: $(yaml_quote "${REPLACE_STATE:-}")
    SERVICES_LIST: $(yaml_quote "${SERVICES_LIST:-}")
    SINGLE_SERVICE_DEPLOY: $(yaml_quote "${SINGLE_SERVICE_DEPLOY:-}")
    SELECTED_SERVICE: $(yaml_quote "${SELECTED_SERVICE:-}")
EOF

is_skipped() {
  tenant=$1
  file=$(dirname "$0")/skip-tenants.txt
  [ "${DEPLOY_ALL:-}" = "true" ] || return 1
  [ -f "$file" ] || return 1
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ""|\#*) continue ;;
    esac
    first=${line%% *}
    rest=${line#* }
    second=${rest%% *}
    if [ "$first" = "${TARGET_ENV:-}" ] && [ "$second" = "$tenant" ]; then
      return 0
    fi
  done < "$file"
  return 1
}

found=0
seen=" "
old_ifs=$IFS
IFS=','
for raw in $TENANT_LIST; do
  IFS=$old_ifs
  tenant=$(printf '%s' "$raw" | tr -d '[:space:]')
  if [ -z "$tenant" ]; then
    IFS=','
    continue
  fi
  if is_skipped "$tenant"; then
    echo "skipped ${tenant} for ${TARGET_ENV:-}"
    IFS=','
    continue
  fi
  printf '%s' "$tenant" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9_-]{0,62}(/[A-Za-z0-9][A-Za-z0-9_-]{0,62}){0,5}$' || {
    echo "invalid tenant path: $tenant" >&2
    exit 1
  }
  case "$seen" in
    *" $tenant "*)
      IFS=','
      continue
      ;;
  esac
  seen="$seen$tenant "
  found=1
  cat >> generated-ci.yml <<EOF
"${tenant}":
  extends: .portal-variables
  stage: downstream
  trigger:
    project: tenants/${tenant}
    branch: main
    strategy: depend
    forward:
      yaml_variables: true
      pipeline_variables: true
EOF
  IFS=','
done
IFS=$old_ifs

if [ "$found" -ne 1 ]; then
  echo "TENANT_LIST did not contain a tenant" >&2
  exit 1
fi
