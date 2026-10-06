"""Servidor web do app que roda no celular.

Só usa a biblioteca padrão. Tudo exige o token do link, então quem está na
mesma Wi-Fi mas não recebeu o link não consegue usar.

A aba "Casa" do app fala direto com casa.py e banco.py, sem passar pelo
agente, então continua funcionando sem internet.

A conversa é uma lista de eventos numerados. O celular fica pendurado em
/api/eventos (long polling) e recebe cada evento assim que ele é publicado.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import banco
import casa
import config

RAIZ = config.RAIZ
PASTA_RECEBIDOS = config.PASTA_RECEBIDOS
ARQUIVO_TOKEN = config.ARQUIVO_TOKEN
PORTA = config.PORTA
TAMANHO_MAXIMO = 50 * 1024 * 1024  # 50 MB por arquivo
TAMANHO_PEDIDO = 4000  # caracteres por mensagem do chat
ESPERA_EVENTOS = 25  # segundos que /api/eventos segura a conexão
ATIVO_SEGUNDOS = ESPERA_EVENTOS + 10  # celular conta como conectado nesse intervalo
ESPERA_CONFIRMACAO = 120


def _carregar_token() -> str:
    """O token fica em disco para o link (e o atalho na tela inicial) continuar valendo."""
    if ARQUIVO_TOKEN.exists():
        return ARQUIVO_TOKEN.read_text().strip()
    token = secrets.token_urlsafe(12)
    ARQUIVO_TOKEN.write_text(token)
    os.chmod(ARQUIVO_TOKEN, 0o600)
    return token


TOKEN = _carregar_token()

_condicao = threading.Condition()
_eventos: list[dict] = []
_proximo_id = 1
_celulares: dict[str, dict] = {}
_confirmacoes: dict[int, dict] = {}

# Preenchidos pelo agente. ao_pedir(texto) e ao_reiniciar() devolvem False se ele estiver ocupado.
ao_pedir = None
ao_reiniciar = None


def publicar(tipo: str, texto: str = "", **extra) -> int:
    """Acrescenta um evento à conversa e acorda os celulares que estão esperando."""
    global _proximo_id
    with _condicao:
        evento = {"id": _proximo_id, "tipo": tipo, "texto": texto, "hora": time.strftime("%H:%M"), **extra}
        _proximo_id += 1
        _eventos.append(evento)
        _condicao.notify_all()
        return evento["id"]


def limpar() -> None:
    """Começa uma conversa nova: apaga os eventos antigos e avisa os celulares."""
    with _condicao:
        _eventos.clear()
    publicar("limpar")


def pedir_confirmacao(texto: str) -> bool:
    """Mostra um pedido de confirmação no celular e espera a resposta."""
    espera = {"respondido": threading.Event(), "ok": False}
    with _condicao:  # registra antes que o celular consiga responder
        ref = publicar("confirmar", texto)
        _confirmacoes[ref] = espera
    espera["respondido"].wait(ESPERA_CONFIRMACAO)
    del _confirmacoes[ref]
    publicar("confirmado", ref=ref, ok=espera["ok"])
    return espera["ok"]


def celulares_conectados() -> list[dict]:
    agora = time.time()
    with _condicao:
        return [
            {"ip": ip, "nome": c["nome"], "navegador": c["navegador"]}
            for ip, c in _celulares.items()
            if agora - c["visto"] < ATIVO_SEGUNDOS
        ]


def arquivos_recebidos() -> list[dict]:
    if not PASTA_RECEBIDOS.exists():
        return []
    return [
        {"nome": p.name, "bytes": p.stat().st_size}
        for p in sorted(PASTA_RECEBIDOS.iterdir(), reverse=True)
        if p.is_file()
    ]


def _eventos_desde(desde: int, ip: str, nome: str, navegador: str) -> list[dict]:
    limite = time.time() + ESPERA_EVENTOS
    with _condicao:
        while True:
            _celulares[ip] = {"nome": nome, "navegador": navegador, "visto": time.time()}
            novos = [e for e in _eventos if e["id"] > desde]
            restante = limite - time.time()
            if novos or restante <= 0:
                return novos
            _condicao.wait(restante)


class _Tratador(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _responder(self, status: int, corpo: bytes, tipo: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def _json(self, status: int, dados) -> None:
        self._responder(status, json.dumps(dados).encode())

    def _autorizado(self, consulta: dict) -> bool:
        return hmac.compare_digest(consulta.get("t", [""])[0], TOKEN)

    def _corpo_json(self) -> dict | None:
        try:
            tamanho = int(self.headers.get("Content-Length", "0"))
            if tamanho > 64 * 1024:
                return None
            dados = json.loads(self.rfile.read(tamanho) or b"{}")
        except ValueError:
            return None
        return dados if isinstance(dados, dict) else None

    def do_GET(self):
        url = urlparse(self.path)
        consulta = parse_qs(url.query)
        if not self._autorizado(consulta):
            return self._json(403, {"erro": "token inválido"})

        if url.path == "/":
            pagina = (RAIZ / "pagina.html").read_bytes()
            return self._responder(200, pagina, "text/html; charset=utf-8")

        if url.path == "/api/eventos":
            try:
                desde = int(consulta.get("desde", ["0"])[0])
            except ValueError:
                desde = 0
            nome = consulta.get("nome", [""])[0][:40] or "sem nome"
            navegador = self.headers.get("User-Agent", "")[:120]
            try:
                return self._json(200, _eventos_desde(desde, self.client_address[0], nome, navegador))
            except (BrokenPipeError, ConnectionResetError):
                return  # o celular fechou a página enquanto esperava

        if url.path == "/api/arquivos":
            return self._json(200, arquivos_recebidos())

        if url.path == "/api/casa":
            regras = [{"id": r["id"], "descricao": casa.descrever_regra(r)} for r in banco.regras()]
            return self._json(200, {**casa.estado(), "regras": regras, "eventos": banco.eventos(8)})

        if url.path == "/api/historico":
            try:
                horas = min(max(int(consulta.get("horas", ["24"])[0]), 1), 24 * 7)
            except ValueError:
                horas = 24
            return self._json(200, banco.historico(horas))

        self._json(404, {"erro": "não encontrado"})

    def do_POST(self):
        url = urlparse(self.path)
        consulta = parse_qs(url.query)
        if not self._autorizado(consulta):
            return self._json(403, {"erro": "token inválido"})

        if url.path == "/api/arquivo":
            return self._receber_arquivo(consulta)

        dados = self._corpo_json()
        if dados is None:
            return self._json(400, {"erro": "corpo inválido"})

        if url.path == "/api/chat":
            texto = str(dados.get("texto", "")).strip()
            if not texto or len(texto) > TAMANHO_PEDIDO:
                return self._json(400, {"erro": f"a mensagem precisa ter de 1 a {TAMANHO_PEDIDO} caracteres"})
            if not ao_pedir(texto):
                return self._json(409, {"erro": "o agente ainda está respondendo"})
            return self._json(200, {"ok": True})

        if url.path == "/api/confirmar":
            with _condicao:
                espera = _confirmacoes.get(dados.get("id"))
            if espera is None:
                return self._json(404, {"erro": "esse pedido já expirou"})
            espera["ok"] = dados.get("ok") is True
            espera["respondido"].set()
            return self._json(200, {"ok": True})

        if url.path == "/api/luz":
            casa.definir_luz(dados.get("ligada") is True, "app")
            return self._json(200, {"ok": True})

        if url.path == "/api/alarme":
            casa.armar_alarme(dados.get("armado") is True, "app")
            return self._json(200, {"ok": True})

        if url.path == "/api/regra/apagar":
            if not isinstance(dados.get("id"), int) or not banco.apagar_regra(dados["id"]):
                return self._json(404, {"erro": "regra não encontrada"})
            return self._json(200, {"ok": True})

        if url.path == "/api/simular-movimento":
            if not casa.SIMULADO:
                return self._json(403, {"erro": "disponível só no modo simulado"})
            casa.ao_detectar_movimento()
            return self._json(200, {"ok": True})

        if url.path == "/api/nova":
            if not ao_reiniciar():
                return self._json(409, {"erro": "o agente ainda está respondendo"})
            return self._json(200, {"ok": True})

        self._json(404, {"erro": "não encontrado"})

    def _receber_arquivo(self, consulta: dict) -> None:
        try:
            tamanho = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._json(411, {"erro": "tamanho ausente"})
        if tamanho > TAMANHO_MAXIMO:
            return self._json(413, {"erro": "arquivo maior que 50 MB"})

        # Path(...).name descarta qualquer diretório vindo do celular (../ etc.)
        nome = Path(consulta.get("nome", ["arquivo"])[0]).name or "arquivo"
        PASTA_RECEBIDOS.mkdir(exist_ok=True)
        destino = PASTA_RECEBIDOS / f"{time.strftime('%Y%m%d-%H%M%S')}-{nome}"
        restante = tamanho
        with open(destino, "wb") as saida:
            while restante > 0:
                bloco = self.rfile.read(min(restante, 64 * 1024))
                if not bloco:
                    break
                saida.write(bloco)
                restante -= len(bloco)
        if restante:
            destino.unlink()
            return self._json(400, {"erro": "envio interrompido"})
        self._json(200, {"salvo": destino.name})


def criar() -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("0.0.0.0", PORTA), _Tratador)
