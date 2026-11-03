# orderdesk-api

Django + DRF + Channels backend (HTTP API and WebSocket) for the order management system.
Split out of the former single repo `desi-order` (this repo = old `backend/` + shared docs/scripts).

Related repos:
- `orderdesk-web`: React frontend, talks to this API.
- `orderdesk-worker`: Celery workers + beat; runs THIS code (tasks live in `apps/*/tasks.py`), pinned as a git submodule.

## Run locally
```bash
cp deploy/legacy/.env.example .env   # fill values yourself
docker compose -f deploy/legacy/docker-compose.yml up -d postgres redis minio
pip install -r requirements-dev.txt && python manage.py migrate && python manage.py runserver
```
## Notes
- `deploy/legacy/` holds the old monolith compose/nginx/monitoring/GitLab CI files, UNCHANGED, as reference. Paths inside still point to the old layout (`./backend`, `./frontend`) and must be rewired per repo.
- Architecture rules: `docs/architecture-standard.md`, import layers in `.importlinter`.
- Git hooks: `git config core.hooksPath .githooks`.
