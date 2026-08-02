"""Fixtures compartilhadas: banco SQLite temporario, semeado, por teste."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# parsers/base.py e outros modulos importam com "from core.xxx import ...",
# entao o diretorio financas/ (pai deste arquivo) precisa estar no sys.path.
RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import pytest

from core import db, seed


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    """Banco SQLite novo e semeado para cada teste."""
    caminho = tmp_path / "financas_teste.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{caminho}")
    db.resetar_engine()
    motor = db.get_engine()
    seed.semear(motor)
    yield motor
    db.resetar_engine()


@pytest.fixture()
def conn(engine):
    """Conexao aberta em transacao unica, para consultas diretas nos testes."""
    with engine.begin() as conexao:
        yield conexao
