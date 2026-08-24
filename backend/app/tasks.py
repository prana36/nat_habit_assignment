from datetime import UTC, datetime

from celery import Celery
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.database import SessionLocal
from app.models import Notification, NotificationType, Task, TaskStatus


settings = get_settings()
celery_app = Celery(
    "taskflow",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
celery_app.conf.beat_schedule = {
    "create-overdue-task-notifications": {
        "task": "app.tasks.create_overdue_notifications",
        "schedule": 60.0,
    }
}
celery_app.conf.timezone = "UTC"


def _commit_notification(notification: Notification) -> bool:
    db = SessionLocal()
    try:
        db.add(notification)
        db.commit()
        print(f"notification created: {notification.event_type} task={notification.task_id}")
        return True
    except IntegrityError:
        db.rollback()
        return False
    finally:
        db.close()


@celery_app.task(name="app.tasks.create_reassignment_notification")
def create_reassignment_notification(task_id: int, user_id: int) -> bool:
    notification = Notification(
        task_id=task_id,
        user_id=user_id,
        event_type=NotificationType.reassigned,
        message=f"Task {task_id} was reassigned to you.",
    )
    return _commit_notification(notification)


@celery_app.task(name="app.tasks.create_overdue_notifications")
def create_overdue_notifications() -> int:
    db = SessionLocal()
    created = 0
    try:
        overdue_tasks = (
            db.query(Task)
            .filter(Task.due_date.is_not(None))
            .filter(Task.due_date < datetime.now(UTC))
            .filter(Task.status != TaskStatus.done)
            .filter(Task.assignee_id.is_not(None))
            .all()
        )
        for task in overdue_tasks:
            notification = Notification(
                task_id=task.id,
                user_id=task.assignee_id,
                event_type=NotificationType.overdue,
                message=f"Task {task.id} is overdue.",
            )
            db.add(notification)
            try:
                db.commit()
                created += 1
                print(f"notification created: overdue task={task.id}")
            except IntegrityError:
                db.rollback()
        return created
    finally:
        db.close()


def enqueue_reassignment_notification(task_id: int, user_id: int) -> None:
    celery_app.send_task("app.tasks.create_reassignment_notification", args=[task_id, user_id])
