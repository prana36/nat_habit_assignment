import os
import tempfile
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

os.environ["CACHE_ENABLED"] = "false"
os.environ["JWT_SECRET"] = "test-secret"
os.environ["DATABASE_URL"] = "sqlite:///./test.db"

from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
import app.tasks as task_module  # noqa: E402


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    original_task_session = task_module.SessionLocal
    task_module.SessionLocal = TestingSessionLocal
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()

    def override_get_db() -> Generator[Session, None, None]:
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield db
    finally:
        db.close()
        task_module.SessionLocal = original_task_session
        app.dependency_overrides.clear()
        engine.dispose()
        os.remove(path)


@pytest.fixture
def client(db_session: Session) -> TestClient:
    return TestClient(app)


def signup_and_login(client: TestClient, email: str = "owner@example.com") -> dict[str, str]:
    password = "correct horse"
    client.post("/auth/signup", json={"email": email, "password": password})
    response = client.post("/auth/login", json={"email": email, "password": password})
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
