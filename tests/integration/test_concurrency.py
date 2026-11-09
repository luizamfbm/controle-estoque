"""Testes de concorrência.

Simulam muitas requisições simultâneas disputando o mesmo saldo. Sem
controle de concorrência, duas threads poderiam ler "disponível = 3" ao
mesmo tempo e ambas reservarem 3, deixando o disponível negativo.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from inventory.domain import HoldKind
from inventory.errors import InsufficientAvailableStockError
from inventory.service import InventoryService
from inventory.sqlite_store import SqliteDatabase

WORKERS = 16


def run_concurrently(n: int, fn):
    """Dispara n chamadas a fn ao mesmo tempo (uma barreira segura todas
    até que estejam prontas) e devolve (sucessos, recusas)."""
    barrier = threading.Barrier(min(n, WORKERS))
    results: list[object] = []
    refused = 0
    lock = threading.Lock()

    def task(i: int):
        nonlocal refused
        if i < WORKERS:
            barrier.wait()
        try:
            r = fn(i)
            with lock:
                results.append(r)
        except InsufficientAvailableStockError:
            with lock:
                refused += 1

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(task, range(n)))
    return results, refused


def new_service(db_path) -> InventoryService:
    # Cada thread usa conexões próprias, como requisições HTTP reais.
    return InventoryService(SqliteDatabase(db_path, busy_timeout_seconds=30))


def test_reservas_concorrentes_nunca_ultrapassam_o_disponivel(db_path):
    service = new_service(db_path)
    service.register_product("SKU-C", "Concorrido")
    service.receive("SKU-C", 100)

    # 50 pedidos de 3 unidades disputando 100 unidades: só 33 cabem.
    reservas, recusadas = run_concurrently(50, lambda i: service.reserve("SKU-C", 3, f"PEDIDO-{i}"))

    p = service.get_product("SKU-C")
    assert len(reservas) == 33
    assert recusadas == 17
    assert (p.total, p.reserved, p.available) == (100, 99, 1)
    assert len(service.list_holds("SKU-C", HoldKind.RESERVATION)) == 33


def test_operacoes_mistas_concorrentes_mantem_consistencia(db_path):
    service = new_service(db_path)
    service.register_product("SKU-M", "Misto")
    service.receive("SKU-M", 50)

    def op(i: int):
        match i % 4:
            case 0:
                return service.receive("SKU-M", 2)
            case 1:
                return service.dispatch("SKU-M", 3)
            case 2:
                h = service.reserve("SKU-M", 2)
                return service.release(h.id, HoldKind.RESERVATION)
            case 3:
                return service.block("SKU-M", 1)

    run_concurrently(200, op)

    p = service.get_product("SKU-M")
    movs = service.list_movements("SKU-M", limit=1000)
    entradas = sum(m.quantity for m in movs if m.type.value == "ENTRY")
    saidas = sum(m.quantity for m in movs if m.type.value == "EXIT")
    bloqueios_ativos = sum(h.quantity for h in service.list_holds("SKU-M", HoldKind.BLOCK))

    assert p.total == entradas - saidas
    assert p.reserved == 0  # toda reserva criada foi liberada
    assert p.blocked == bloqueios_ativos
    assert p.available == p.total - p.reserved - p.blocked >= 0
