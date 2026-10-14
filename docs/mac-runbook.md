# Run the dev portal on a Mac

Follow these steps in order. Each one depends on the one before it. When you finish, you can sign in, pick one tenant, and see that tenant's job print the variables the portal sent.

You need a terminal. The commands below are bash, which is what Terminal and iTerm use.

## 1. Install the tools

Install these before you clone anything else:

- [Docker Desktop for Mac](https://www.docker.com/products/docker-desktop/). Open it and wait until it says it is running.
- In Docker Desktop, open Settings → Resources and give Docker at least **6 GB of memory**. GitLab needs about 4 GB by itself. On Apple Silicon the GitLab image runs under emulation, so the first boot is slow.
- [Terraform](https://developer.hashicorp.com/terraform/install) 1.5 or newer. `brew install terraform` is enough.
- Python 3, for the ledger tests. `python3 --version` should print 3.9 or newer.
- `curl` and `bash` are already on macOS.

Check Docker from the repo directory later:

```bash
docker info
```

That command should print server information. If it says it cannot connect, Docker Desktop is not running.

## 2. Get the code

```bash
git clone https://github.com/mangatohjunior/dev-portal.git
cd dev-portal
```

Stay in this folder for every command below. The Docker network is named from this folder, `dev-portal_default`. The runner setup in step 7 uses that name.

## 3. Start GitLab and wait until it answers

GitLab is the slow part. Start it on its own first:

```bash
docker compose -f docker-compose.yaml -f docker-compose.gitlab.yaml up -d gitlab gitlab-runner
```

GitLab listens on http://localhost:8929. The first start often takes 5 to 15 minutes, longer on Apple Silicon.

Wait until this prints `401`:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8929/api/v4/version
```

`401` means GitLab is up and is asking for a token. `000` or a connection error means it is still starting. Run the same command again in a minute.

If it stays down, read the log:

```bash
docker compose -f docker-compose.yaml -f docker-compose.gitlab.yaml logs -f gitlab
```

Stop following the log with Control-C. That does not stop GitLab.

## 4. Get the GitLab root password and create a token

The GitLab user is `root`. GitLab writes the first password into the container on first boot and deletes that file after the container has been running for 24 hours.

GitLab must be running. If `docker compose ps` shows it exited, start it again and wait until the version check from step 3 prints `401`:

```bash
docker compose -f docker-compose.yaml -f docker-compose.gitlab.yaml up -d gitlab
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8929/api/v4/version
```

Read the password:

```bash
docker compose -f docker-compose.yaml -f docker-compose.gitlab.yaml exec gitlab grep Password: /etc/gitlab/initial_root_password
```

Sign in at http://localhost:8929 with `root` and that password.

If `grep` says the file is missing, the 24-hour copy is already gone. Set a new password. The command asks you to type it twice:

```bash
docker compose -f docker-compose.yaml -f docker-compose.gitlab.yaml exec gitlab gitlab-rake "gitlab:password:reset[root]"
```

Then create the API token:

1. Open the user menu → **Edit profile** → **Access tokens**.
2. Create a token with the **api** scope. Copy it once. It looks like `glpat-...`.

Save it only in the local file from the next step. The file `mock-secrets/gitlab-token` that is already in the repo is a fake token. The portal will get `401` from GitLab if it uses that fake.

## 5. Point the portal at the real token

Create the real token file. Paste your token in place of the placeholder. There should be no spaces and no blank line after it.

```bash
printf '%s' 'glpat-paste-your-token-here' > mock-secrets/gitlab-token.local
```

Create `docker-compose.override.yaml` in the repo root:

```yaml
services:
  portal:
    volumes:
      - ./mock-secrets/gitlab-token.local:/mnt/secrets/gitlab-token:ro
```

That file is gitignored. When you pass compose files with `-f`, Docker does not load the override on its own. Every portal command below includes `-f docker-compose.override.yaml` so the container sees the real token.

## 6. Start Keycloak and the portal

```bash
docker compose -f docker-compose.yaml -f docker-compose.gitlab.yaml -f docker-compose.override.yaml up -d --build portal
```

Wait until the login page answers `200`:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/login
```

Open http://localhost:8000.

| What | Value |
|---|---|
| Portal sign-in | `devportal-user` / `devportal-pass` |
| Account that must be refused | `outsider-user` / `outsider-pass` |
| Keycloak admin, http://localhost:8080 | `admin` / `admin` |
| GitLab | http://localhost:8929, user `root` |

Sign in once as `devportal-user` so you know Keycloak imported the realm. The deploy form can load, but triggering a pipeline still needs the GitLab projects from the next step.

## 7. Create the GitLab projects

From the repo root:

```bash
cd terraform
terraform init
export TF_VAR_gitlab_base_url="http://localhost:8929/api/v4"
export TF_VAR_gitlab_token="$(cat ../mock-secrets/gitlab-token.local)"
terraform apply -auto-approve
cd ..
```

Apply creates the `tenants` group, `tenant_orchestrator`, the car tenants, the Ops projects (`tenant_baseline`, `tenant_services`, `Kasm_VDI`), and the shared CI template. It takes several minutes. At the end you should see `Apply complete`.

Confirm the orchestrator exists:

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  --header "PRIVATE-TOKEN: $(cat mock-secrets/gitlab-token.local)" \
  "http://localhost:8929/api/v4/projects/root%2Ftenant_orchestrator"
```

`200` means the project is there.

## 8. Register the runner

Jobs do not run until a runner is registered and can reach GitLab by the name `gitlab`.

1. In GitLab, as root, open **Admin area** → **CI/CD** → **Runners** → **New instance runner**.
2. Leave the defaults, create the runner, and copy the runner authentication token (`glrt-...`).
3. Register it:

   ```bash
   docker compose -f docker-compose.yaml -f docker-compose.gitlab.yaml exec gitlab-runner \
     gitlab-runner register --non-interactive \
     --url "http://gitlab:8929" \
     --token "glrt-paste-the-runner-token" \
     --executor "docker" \
     --docker-image "alpine:3.20" \
     --description "local-docker"
   ```

4. Open the runner config:

   ```bash
   docker compose -f docker-compose.yaml -f docker-compose.gitlab.yaml exec gitlab-runner \
     cat /etc/gitlab-runner/config.toml
   ```

   `clone_url` tells jobs to clone from the GitLab container. `network_mode` puts those jobs on the same Docker network. The finished file should look like this. Keep the `token` line the register command already wrote.

   ```toml
   concurrent = 1

   [[runners]]
     name = "local-docker"
     url = "http://gitlab:8929"
     token = "glrt-the-token-register-wrote"
     executor = "docker"
     clone_url = "http://gitlab:8929"
     [runners.docker]
       image = "alpine:3.20"
       network_mode = "dev-portal_default"
   ```

   Write that file from your Mac. Replace the token with the one already in `config.toml` before you run this:

   ```bash
   docker compose -f docker-compose.yaml -f docker-compose.gitlab.yaml exec -T gitlab-runner \
     sh -c 'cat > /etc/gitlab-runner/config.toml' <<'EOF'
   concurrent = 1

   [[runners]]
     name = "local-docker"
     url = "http://gitlab:8929"
     token = "glrt-paste-the-token-from-the-existing-file"
     executor = "docker"
     clone_url = "http://gitlab:8929"
     [runners.docker]
       image = "alpine:3.20"
       network_mode = "dev-portal_default"
   EOF
   ```

5. Restart the runner:

   ```bash
   docker compose -f docker-compose.yaml -f docker-compose.gitlab.yaml restart gitlab-runner
   ```

In GitLab the runner should show as online.

## 9. Trigger one tenant

1. Open http://localhost:8000 and sign in as `devportal-user` / `devportal-pass`.
2. Leave the environment on **Dev**.
3. Open the tenant list and tick **atlas** only. A full release starts every tenant and will take a long time.
4. Choose pipeline **Tenant baseline**.
5. Leave **Replace existing resources** unchecked.
6. Press **Trigger pipeline**.

The popup follows the orchestrator pipeline. On success it links to http://localhost:8929/root/tenant_orchestrator/-/pipelines.

Open that pipeline. You should see:

1. `generate-downstream` prints `TARGET_ENV`, `TENANT_LIST`, `PIPELINE_TRIGGER`, `DRY_RUN`, and the rest.
2. A child pipeline starts `tenants/atlas`.
3. The atlas job prints those same values, then starts `Ops/tenant_baseline`.
4. The Ops job prints the same values again, including `TRIGGERED_BY=devportal-user`.

`DRY_RUN` is `false` on Dev. It is `true` only when you select Prod and leave **Allow this production trigger** unchecked. Checking that box, and entering a change request such as `CHG0012345`, sends `DRY_RUN=false`.

## 10. Optional checks

Ledger tests do not need Docker:

```bash
python3 -m unittest tests.test_releases_store -v
```

Login and header checks need the portal and Keycloak running:

```bash
bash ./tests/test-security-hardening.sh
```

## Day to day

Start everything after a reboot:

```bash
docker compose -f docker-compose.yaml -f docker-compose.gitlab.yaml -f docker-compose.override.yaml up -d
```

After you change Python or the pages under `public/`, rebuild the portal. Those files are copied into the image at build time.

```bash
docker compose -f docker-compose.yaml -f docker-compose.gitlab.yaml -f docker-compose.override.yaml up -d --build portal
```

Stop the portal and Keycloak and leave GitLab running:

```bash
docker compose -f docker-compose.yaml stop
```

To stop GitLab as well, name that compose file too. Leave off `--remove-orphans`.

```bash
docker compose -f docker-compose.yaml -f docker-compose.gitlab.yaml stop
```

## If something fails

| What you see | What to do |
|---|---|
| Portal log says GitLab status 401 | The container is using the fake token. Check that `mock-secrets/gitlab-token.local` exists and that the up command includes `-f docker-compose.override.yaml`. Then recreate the portal with the command in step 6. |
| `http://localhost:8929/api/v4/version` never returns 401 | GitLab is still booting, or Docker does not have enough memory. Read `docker compose ... logs gitlab`. |
| Trigger says GitLab rejected the pipeline | Terraform apply has not created `root/tenant_orchestrator`, or the token lacks the `api` scope. |
| The orchestrator pipeline stays pending | The runner is offline, or it was registered against the wrong URL. Step 8 should show it online. |
| The tenant job cannot clone | `clone_url` or `network_mode` is missing from `config.toml`. Repeat step 8 and restart the runner. |
| Sign-in returns you to the login page | You used `outsider-user`, or Keycloak has not finished importing the realm. Use `devportal-user`. |
| Popup says a dev release is already in flight | Wait for that pipeline to finish, or open it in GitLab. One environment can have only one release at a time. |
