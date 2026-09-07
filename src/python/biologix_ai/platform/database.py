import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://biologix:biologix@localhost:5432/biologix",
)

# RQ forks a child for each scientific job.  A live psycopg connection inherited
# from the parent is not process-safe and can reuse prepared-statement names.
# Short platform transactions are better served by fresh connections per session.
engine = create_engine(DATABASE_URL, pool_pre_ping=True, poolclass=NullPool)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    with SessionLocal() as session:
        yield session
