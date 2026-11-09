from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from inventory.api import create_app
from inventory.service import InventoryService
from inventory.sqlite_store import SqliteDatabase


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "estoque-test.db"


@pytest.fixture
def db(db_path) -> SqliteDatabase:
    return SqliteDatabase(db_path)


@pytest.fixture
def service(db) -> InventoryService:
    return InventoryService(db)


@pytest.fixture
def client(db_path) -> TestClient:
    return TestClient(create_app(str(db_path)))
