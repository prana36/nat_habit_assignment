from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.models import Notification, NotificationType, Task, TaskStatus
from app.tasks import create_overdue_notifications
from tests.conftest import signup_and_login


def test_auth_project_task_core_flow(client: TestClient) -> None:
    headers = signup_and_login(client)
    project = client.post(
        "/projects", json={"name": "Launch", "description": "Ship v1"}, headers=headers
    ).json()

    due_date = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    task = client.post(
        "/tasks",
        json={
            "project_id": project["id"],
            "title": "Write API",
            "status": "todo",
            "due_date": due_date,
        },
        headers=headers,
    ).json()

    response = client.get("/tasks?status=todo&limit=10&offset=0", headers=headers)
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == task["id"]


def test_authorization_boundary_between_users(client: TestClient) -> None:
    owner_headers = signup_and_login(client, "owner@example.com")
    other_headers = signup_and_login(client, "other@example.com")
    project = client.post("/projects", json={"name": "Private"}, headers=owner_headers).json()

    forbidden = client.get(f"/projects/{project['id']}", headers=other_headers)
    assert forbidden.status_code == 404


def test_task_update_invalidates_cache_and_triggers_reassignment(
    client: TestClient, monkeypatch
) -> None:
    owner_headers = signup_and_login(client, "owner@example.com")
    assignee_headers = signup_and_login(client, "assignee@example.com")
    assignee = client.get("/me", headers=assignee_headers).json()
    project = client.post("/projects", json={"name": "Cached"}, headers=owner_headers).json()
    task = client.post(
        "/tasks", json={"project_id": project["id"], "title": "Cache me"}, headers=owner_headers
    ).json()

    calls = []
    monkeypatch.setattr("app.main.enqueue_reassignment_notification", lambda *args: calls.append(args))
    assert client.get("/tasks?status=todo", headers=owner_headers).json()["total"] == 1
    client.patch(
        f"/tasks/{task['id']}",
        json={"status": "done", "assignee_id": assignee["id"]},
        headers=owner_headers,
    )

    done_list = client.get("/tasks?status=done", headers=owner_headers).json()
    todo_list = client.get("/tasks?status=todo", headers=owner_headers).json()
    assert done_list["total"] == 1
    assert todo_list["total"] == 0
    assert calls == [(task["id"], assignee["id"])]


def test_overdue_notification_job(client: TestClient, db_session) -> None:
    headers = signup_and_login(client)
    user = client.get("/me", headers=headers).json()
    project = client.post("/projects", json={"name": "Ops"}, headers=headers).json()
    overdue = datetime.now(UTC) - timedelta(minutes=5)
    task = Task(
        project_id=project["id"],
        title="Missed deadline",
        status=TaskStatus.todo,
        assignee_id=user["id"],
        due_date=overdue,
    )
    db_session.add(task)
    db_session.commit()

    assert create_overdue_notifications() == 1
    notification = db_session.query(Notification).one()
    assert notification.event_type == NotificationType.overdue
    assert notification.user_id == user["id"]
