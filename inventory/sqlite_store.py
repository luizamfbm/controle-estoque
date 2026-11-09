"""Persistência em SQLite.

Decisões importantes:

- Cada unidade de trabalho abre sua própria conexão e uma transação.
- Operações de escrita usam `BEGIN IMMEDIATE`: o SQLite adquire o lock de
  escrita já no início da transação. Assim, "ler o saldo -> validar ->
  gravar" acontece sem que outra escrita se intercale. Isso impede, por
  exemplo, que duas reservas simultâneas consumam o mesmo saldo.
- O modo WAL permite leituras concorrentes enquanto há uma escrita.
- O schema tem CHECK constraints que repetem as regras de consistência
  (nunca saldo negativo, reservado + bloqueado <= total). Se um bug passar
  pelo domínio, o banco recusa o dado e a transação é desfeita.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

from inventory.domain import Hold, HoldKind, HoldStatus, Movement, MovementType, Product
from inventory.errors import ProductAlreadyExistsError

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    sku         TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    total       INTEGER NOT NULL CHECK (total >= 0),
    reserved    INTEGER NOT NULL CHECK (reserved >= 0),
    blocked     INTEGER NOT NULL CHECK (blocked >= 0),
    created_at  TEXT NOT NULL,
    CHECK (reserved + blocked <= total)
);

CREATE TABLE IF NOT EXISTS holds (
    id          TEXT PRIMARY KEY,
    sku         TEXT NOT NULL REFERENCES products(sku),
    kind        TEXT NOT NULL CHECK (kind IN ('RESERVATION', 'BLOCK')),
    quantity    INTEGER NOT NULL CHECK (quantity > 0),
    reference   TEXT,
    status      TEXT NOT NULL CHECK (status IN ('ACTIVE', 'RELEASED')),
    created_at  TEXT NOT NULL,
    released_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_holds_sku_kind_status ON holds (sku, kind, status);

CREATE TABLE IF NOT EXISTS movements (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sku             TEXT NOT NULL REFERENCES products(sku),
    type            TEXT NOT NULL,
    quantity        INTEGER NOT NULL CHECK (quantity > 0),
    reference       TEXT,
    hold_id         TEXT REFERENCES holds(id),
    occurred_at     TEXT NOT NULL,
    total_after     INTEGER NOT NULL,
    reserved_after  INTEGER NOT NULL,
    blocked_after   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_movements_sku ON movements (sku, id);
"""


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class SqliteProductRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, sku: str) -> Product | None:
        row = self._conn.execute(
            "SELECT sku, name, total, reserved, blocked, created_at FROM products WHERE sku = ?",
            (sku,),
        ).fetchone()
        if row is None:
            return None
        return Product(
            sku=row["sku"],
            name=row["name"],
            total=row["total"],
            reserved=row["reserved"],
            blocked=row["blocked"],
            created_at=_dt(row["created_at"]),
        )

    def add(self, product: Product) -> None:
        try:
            self._conn.execute(
                "INSERT INTO products (sku, name, total, reserved, blocked, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (product.sku, product.name, product.total, product.reserved, product.blocked,
                 product.created_at.isoformat()),
            )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc) or "PRIMARY KEY" in str(exc):
                raise ProductAlreadyExistsError(product.sku) from exc
            raise

    def update(self, product: Product) -> None:
        self._conn.execute(
            "UPDATE products SET total = ?, reserved = ?, blocked = ? WHERE sku = ?",
            (product.total, product.reserved, product.blocked, product.sku),
        )


class SqliteHoldRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @staticmethod
    def _to_hold(row: sqlite3.Row) -> Hold:
        return Hold(
            id=row["id"],
            sku=row["sku"],
            kind=HoldKind(row["kind"]),
            quantity=row["quantity"],
            reference=row["reference"],
            status=HoldStatus(row["status"]),
            created_at=_dt(row["created_at"]),
            released_at=_dt(row["released_at"]),
        )

    def get(self, hold_id: str) -> Hold | None:
        row = self._conn.execute("SELECT * FROM holds WHERE id = ?", (hold_id,)).fetchone()
        return self._to_hold(row) if row else None

    def add(self, hold: Hold) -> None:
        self._conn.execute(
            "INSERT INTO holds (id, sku, kind, quantity, reference, status, created_at, released_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (hold.id, hold.sku, hold.kind.value, hold.quantity, hold.reference, hold.status.value,
             hold.created_at.isoformat(), hold.released_at.isoformat() if hold.released_at else None),
        )

    def update(self, hold: Hold) -> None:
        self._conn.execute(
            "UPDATE holds SET status = ?, released_at = ? WHERE id = ?",
            (hold.status.value, hold.released_at.isoformat() if hold.released_at else None, hold.id),
        )

    def list_by_sku(self, sku: str, kind: HoldKind, status: HoldStatus | None = None) -> list[Hold]:
        sql = "SELECT * FROM holds WHERE sku = ? AND kind = ?"
        params: list[object] = [sku, kind.value]
        if status is not None:
            sql += " AND status = ?"
            params.append(status.value)
        sql += " ORDER BY created_at, id"
        return [self._to_hold(r) for r in self._conn.execute(sql, params).fetchall()]


class SqliteMovementRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, movement: Movement) -> None:
        self._conn.execute(
            "INSERT INTO movements (sku, type, quantity, reference, hold_id, occurred_at, "
            "total_after, reserved_after, blocked_after) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (movement.sku, movement.type.value, movement.quantity, movement.reference, movement.hold_id,
             movement.occurred_at.isoformat(), movement.total_after, movement.reserved_after,
             movement.blocked_after),
        )

    def list_by_sku(self, sku: str, limit: int = 100) -> list[Movement]:
        rows = self._conn.execute(
            "SELECT * FROM movements WHERE sku = ? ORDER BY id DESC LIMIT ?", (sku, limit)
        ).fetchall()
        return [
            Movement(
                sku=r["sku"],
                type=MovementType(r["type"]),
                quantity=r["quantity"],
                reference=r["reference"],
                hold_id=r["hold_id"],
                occurred_at=_dt(r["occurred_at"]),
                total_after=r["total_after"],
                reserved_after=r["reserved_after"],
                blocked_after=r["blocked_after"],
            )
            for r in rows
        ]


class SqliteUnitOfWork:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.products = SqliteProductRepository(conn)
        self.holds = SqliteHoldRepository(conn)
        self.movements = SqliteMovementRepository(conn)


class SqliteDatabase:
    def __init__(self, path: str | Path, *, busy_timeout_seconds: float = 10.0) -> None:
        if str(path) == ":memory:":
            # Cada conexão em ":memory:" seria um banco diferente e vazio.
            raise ValueError("Use um arquivo (ex.: em diretório temporário) em vez de ':memory:'.")
        self._path = str(path)
        self._timeout = busy_timeout_seconds
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        # Atenção: `with sqlite3.connect(...)` NÃO fecha a conexão (só faz
        # commit/rollback). `closing` garante o close().
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        # isolation_level=None: controlamos BEGIN/COMMIT/ROLLBACK manualmente.
        conn = sqlite3.connect(self._path, timeout=self._timeout, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def unit_of_work(self, *, read_only: bool = False) -> Iterator[SqliteUnitOfWork]:
        conn = self._connect()
        try:
            conn.execute("BEGIN" if read_only else "BEGIN IMMEDIATE")
            try:
                yield SqliteUnitOfWork(conn)
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            if read_only:
                conn.execute("ROLLBACK")  # nada a confirmar
            else:
                conn.execute("COMMIT")
        finally:
            conn.close()
