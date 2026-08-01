import os

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["SECRET_KEY"] = "test-secret-that-is-longer-than-thirty-two-bytes"

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from biologix_ai.platform.database import Base, get_db
from biologix_ai.platform.router import router


engine = create_engine(
    "sqlite+pysqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSession = sessionmaker(bind=engine, expire_on_commit=False)
Base.metadata.create_all(engine)
app = FastAPI()
app.include_router(router)


def override_db():
    with TestingSession() as session:
        yield session


app.dependency_overrides[get_db] = override_db


def test_signup_create_and_list_experiment():
    client = TestClient(app)
    response = client.post("/api/platform/auth/signup", json={"email": "ada@example.com", "password": "secure-pass"})
    assert response.status_code == 201
    assert response.cookies.get("session")

    response = client.post(
        "/api/platform/experiments",
        json={"name": "Insulin screen", "biologic_target": "insulin", "parameters": {}},
    )
    assert response.status_code == 201
    experiment_id = response.json()["id"]

    response = client.get("/api/platform/experiments")
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [experiment_id]


def test_experiments_are_isolated_by_account():
    first = TestClient(app)
    second = TestClient(app)
    first.post("/api/platform/auth/signup", json={"email": "first@example.com", "password": "secure-pass"})
    created = first.post("/api/platform/experiments", json={"name": "Private", "biologic_target": "mAb"}).json()
    second.post("/api/platform/auth/signup", json={"email": "second@example.com", "password": "secure-pass"})

    assert second.get("/api/platform/experiments").json() == []
    assert second.get(f"/api/platform/experiments/{created['id']}").status_code == 404


def test_login_and_logout():
    client = TestClient(app)
    client.post("/api/platform/auth/signup", json={"email": "login@example.com", "password": "secure-pass"})
    client.post("/api/platform/auth/logout")
    assert client.get("/api/platform/auth/me").status_code == 401
    assert client.post("/api/platform/auth/login", json={"email": "login@example.com", "password": "wrong"}).status_code == 401
    assert client.post("/api/platform/auth/login", json={"email": "login@example.com", "password": "secure-pass"}).status_code == 200
