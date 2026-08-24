from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.cache import get_cached_tasks, invalidate_task_cache, set_cached_tasks, task_cache_key
from app.database import Base, engine, get_db
from app.models import Notification, Project, Task, TaskStatus, User
from app.schemas import (
    LoginRequest,
    NotificationRead,
    PaginatedTasks,
    ProjectCreate,
    ProjectRead,
    ProjectUpdate,
    TaskCreate,
    TaskRead,
    TaskUpdate,
    Token,
    UserCreate,
    UserRead,
)
from app.security import create_access_token, get_current_user, hash_password, verify_password
from app.tasks import enqueue_reassignment_notification


app = FastAPI(title="TaskFlow")
metrics = {"requests_total": 0, "errors_total": 0}


@app.on_event("startup")
def create_tables() -> None:
    Base.metadata.create_all(bind=engine)


@app.middleware("http")
async def count_requests(request: Request, call_next) -> Response:
    metrics["requests_total"] += 1
    response = await call_next(request)
    if response.status_code >= 500:
        metrics["errors_total"] += 1
    return response


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
def get_metrics() -> str:
    return "\n".join(f"taskflow_{key} {value}" for key, value in metrics.items()) + "\n"


@app.post("/auth/signup", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def signup(payload: UserCreate, db: Session = Depends(get_db)) -> User:
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=409, detail="Email already registered")
    user = User(email=payload.email, password_hash=hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.post("/auth/login", response_model=Token)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> Token:
    user = db.query(User).filter(User.email == payload.email).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return Token(access_token=create_access_token(user.id))


@app.get("/me", response_model=UserRead)
def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@app.post("/projects", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Project:
    project = Project(**payload.model_dump(), owner_id=current_user.id)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@app.get("/projects", response_model=list[ProjectRead])
def list_projects(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> list[Project]:
    return db.query(Project).filter(Project.owner_id == current_user.id).order_by(Project.id).all()


def get_owned_project(project_id: int, db: Session, user_id: int) -> Project:
    project = db.query(Project).filter(Project.id == project_id, Project.owner_id == user_id).first()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@app.get("/projects/{project_id}", response_model=ProjectRead)
def get_project(
    project_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> Project:
    return get_owned_project(project_id, db, current_user.id)


@app.patch("/projects/{project_id}", response_model=ProjectRead)
def update_project(
    project_id: int,
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Project:
    project = get_owned_project(project_id, db, current_user.id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    db.commit()
    db.refresh(project)
    return project


@app.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> None:
    project = get_owned_project(project_id, db, current_user.id)
    db.delete(project)
    db.commit()
    invalidate_task_cache(current_user.id)


def get_owned_task(task_id: int, db: Session, user_id: int) -> Task:
    task = db.query(Task).join(Project).filter(Task.id == task_id, Project.owner_id == user_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.post("/tasks", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
def create_task(
    payload: TaskCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Task:
    get_owned_project(payload.project_id, db, current_user.id)
    task = Task(**payload.model_dump())
    db.add(task)
    db.commit()
    db.refresh(task)
    invalidate_task_cache(current_user.id)
    return task


@app.get("/tasks", response_model=PaginatedTasks)
def list_tasks(
    status_filter: Annotated[TaskStatus | None, Query(alias="status")] = None,
    assignee_id: int | None = None,
    due_from: datetime | None = None,
    due_to: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    params = {
        "status": status_filter,
        "assignee_id": assignee_id,
        "due_from": due_from,
        "due_to": due_to,
        "limit": limit,
        "offset": offset,
    }
    cache_key = task_cache_key(current_user.id, params)
    cached = get_cached_tasks(cache_key)
    if cached is not None:
        return cached

    query = db.query(Task).join(Project).filter(Project.owner_id == current_user.id)
    if status_filter:
        query = query.filter(Task.status == status_filter)
    if assignee_id:
        query = query.filter(Task.assignee_id == assignee_id)
    if due_from:
        query = query.filter(Task.due_date >= due_from)
    if due_to:
        query = query.filter(Task.due_date <= due_to)

    total = query.with_entities(func.count(Task.id)).scalar() or 0
    items = query.order_by(Task.id).limit(limit).offset(offset).all()
    payload = {
        "items": [TaskRead.model_validate(item).model_dump(mode="json") for item in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
    set_cached_tasks(cache_key, payload)
    return payload


@app.get("/tasks/{task_id}", response_model=TaskRead)
def get_task(
    task_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> Task:
    return get_owned_task(task_id, db, current_user.id)


@app.patch("/tasks/{task_id}", response_model=TaskRead)
def update_task(
    task_id: int,
    payload: TaskUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Task:
    task = get_owned_task(task_id, db, current_user.id)
    old_assignee_id = task.assignee_id
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(task, field, value)
    db.commit()
    db.refresh(task)
    invalidate_task_cache(current_user.id)

    if "assignee_id" in updates and task.assignee_id and task.assignee_id != old_assignee_id:
        enqueue_reassignment_notification(task.id, task.assignee_id)
    return task


@app.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> None:
    task = get_owned_task(task_id, db, current_user.id)
    db.delete(task)
    db.commit()
    invalidate_task_cache(current_user.id)


@app.get("/notifications", response_model=list[NotificationRead])
def list_notifications(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> list[Notification]:
    return (
        db.query(Notification)
        .filter(Notification.user_id == current_user.id)
        .order_by(Notification.created_at.desc())
        .all()
    )
