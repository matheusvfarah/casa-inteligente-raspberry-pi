"""Armazenamento em SQLite: leituras dos sensores, eventos da casa e regras.

Cada função abre e fecha a própria conexão, porque o banco é usado por várias
threads (servidor web, leitura do sensor, sensor de presença, agente).
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import closing

import config

CONDICOES = ("temperatura_acima", "temperatura_abaixo", "umidade_acima", "umidade_abaixo", "movimento")
ACOES = ("avisar", "ligar_luz", "desligar_luz", "tocar_buzzer")


def _conectar() -> sqlite3.Connection:
    conexao = sqlite3.connect(config.ARQUIVO_BANCO, timeout=10)
    conexao.row_factory = sqlite3.Row
    return conexao


def _gravar(sql: str, parametros: tuple = ()) -> int:
    with closing(_conectar()) as conexao, conexao:  # o segundo "with" faz o commit
        return conexao.execute(sql, parametros).lastrowid


def _consultar(sql: str, parametros: tuple = ()) -> list[dict]:
    with closing(_conectar()) as conexao:
        return [dict(linha) for linha in conexao.execute(sql, parametros)]


def iniciar() -> None:
    """Cria as tabelas na primeira execução."""
    with closing(_conectar()) as conexao, conexao:
        conexao.executescript("""
            CREATE TABLE IF NOT EXISTS leituras (
                momento     INTEGER NOT NULL,   -- segundos desde 1970
                temperatura REAL,               -- graus Celsius
                umidade     REAL                -- porcentagem
            );
            CREATE INDEX IF NOT EXISTS leituras_momento ON leituras (momento);

            CREATE TABLE IF NOT EXISTS eventos (
                momento INTEGER NOT NULL,
                tipo    TEXT NOT NULL,          -- luz, alarme, movimento, regra
                detalhe TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS eventos_momento ON eventos (momento);

            CREATE TABLE IF NOT EXISTS regras (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                condicao TEXT NOT NULL,         -- uma das CONDICOES
                valor    REAL NOT NULL,         -- limite da condição (ignorado em "movimento")
                acao     TEXT NOT NULL,         -- uma das ACOES
                texto    TEXT NOT NULL          -- texto do aviso, quando a ação é "avisar"
            );
        """)


# --- Leituras ---

def gravar_leitura(temperatura: float, umidade: float) -> None:
    _gravar("INSERT INTO leituras VALUES (?, ?, ?)", (int(time.time()), temperatura, umidade))


def historico(horas: int, pontos: int = 240) -> list[dict]:
    """Leituras das últimas horas, agrupadas em médias para o gráfico não ficar pesado."""
    intervalo = max(1, horas * 3600 // pontos)
    return _consultar(
        """SELECT (momento / ?) * ? AS momento,
                  ROUND(AVG(temperatura), 1) AS temperatura,
                  ROUND(AVG(umidade), 1) AS umidade
           FROM leituras WHERE momento >= ?
           GROUP BY momento / ? ORDER BY 1""",
        (intervalo, intervalo, int(time.time()) - horas * 3600, intervalo),
    )


def resumo(horas: int) -> dict:
    """Mínima, máxima e média do período, mais quantos movimentos foram registrados."""
    desde = int(time.time()) - horas * 3600
    dados = _consultar(
        """SELECT COUNT(*) AS leituras,
                  MIN(temperatura) AS temperatura_minima, MAX(temperatura) AS temperatura_maxima,
                  ROUND(AVG(temperatura), 1) AS temperatura_media,
                  MIN(umidade) AS umidade_minima, MAX(umidade) AS umidade_maxima,
                  ROUND(AVG(umidade), 1) AS umidade_media
           FROM leituras WHERE momento >= ?""",
        (desde,),
    )[0]
    dados["movimentos"] = _consultar(
        "SELECT COUNT(*) AS n FROM eventos WHERE tipo = 'movimento' AND momento >= ?", (desde,)
    )[0]["n"]
    dados["horas"] = horas
    return dados


# --- Eventos ---

def gravar_evento(tipo: str, detalhe: str) -> None:
    _gravar("INSERT INTO eventos VALUES (?, ?, ?)", (int(time.time()), tipo, detalhe))


def eventos(limite: int = 10) -> list[dict]:
    return _consultar("SELECT * FROM eventos ORDER BY momento DESC, rowid DESC LIMIT ?", (limite,))


# --- Regras ---

def criar_regra(condicao: str, valor: float, acao: str, texto: str) -> int:
    if condicao not in CONDICOES or acao not in ACOES:
        raise ValueError("condição ou ação desconhecida")
    return _gravar(
        "INSERT INTO regras (condicao, valor, acao, texto) VALUES (?, ?, ?, ?)", (condicao, valor, acao, texto)
    )


def regras() -> list[dict]:
    return _consultar("SELECT * FROM regras ORDER BY id")


def apagar_regra(id_regra: int) -> bool:
    with closing(_conectar()) as conexao, conexao:
        return conexao.execute("DELETE FROM regras WHERE id = ?", (id_regra,)).rowcount > 0
