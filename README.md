# TaskFlow

TaskFlow is a FastAPI backend for project/task management with simulated notifications,
Redis-backed task search caching, Celery background jobs, PostgreSQL, Docker Compose, and CI.

## Local Setup

Run the full stack from a clean clone:

```sh
docker compose up --build
```

The API starts at `http://localhost:8000`. Interactive docs are available at
`http://localhost:8000/docs`.

Useful local endpoints:

```sh
GET /health
GET /metrics
POST /auth/signup
POST /auth/login
GET /projects
GET /tasks?status=todo&limit=20&offset=0
GET /notifications
```

For local tests without Docker:

```sh
cd backend
pip install -e ".[dev]"
pytest
ruff check .
```

## Architecture

- FastAPI exposes the REST API and in-memory request/error counters.
- SQLAlchemy models persist users, projects, tasks, and notifications.
- JWT bearer auth protects project/task/notification routes.
- Authorization is project-owner based: users cannot view or mutate another user's projects or tasks.
- Redis caches `GET /tasks` responses per user and filter set.
- Task cache entries are invalidated on task create/update/delete and project delete.
- Celery workers create notification records outside the request cycle.
- Celery beat runs an overdue-task scan every minute.

## Notification Behavior

- Reassigning a task enqueues a Celery job that creates a `reassigned` notification for the new assignee.
- Overdue notifications are created by the periodic Celery beat job for tasks whose due date is in the past and whose status is not `done`.
- Delivery is simulated through persisted notification records and worker logs.

## Deployment Path

I chose the documented/scripted deployment path instead of a live hosted URL. The included
`backend/scripts/deploy.sh` runs the Docker Compose stack. For a real host, the same compose file can
be used on a VM or adapted to Render/Railway services: one web process, one worker process, one beat
process, Postgres, and Redis.

## Tradeoffs And More Time

- I used `Base.metadata.create_all` on startup to keep setup fast. In production I would add Alembic
  migrations and run them as an explicit release step.
- Metrics are intentionally basic and in-memory. I would switch `/metrics` to `prometheus-client`
  counters/histograms for multi-instance deployments.
- Cache invalidation is conservative: any task mutation clears all task-list cache entries for that
  user. This is simple and correct; with higher traffic I would track affected filter keys or use
  versioned cache namespaces.
- Assignment allows an assignee by user id. With more time I would add project membership and restrict
  assignees to project members.
- The test suite covers the core flow, authorization boundaries, cache-sensitive status changes, and
  overdue background job behavior. I would add more tests around pagination edges and duplicate
  notification prevention.
