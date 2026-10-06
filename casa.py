"""A casa: luz (LED), alarme (sensor de presença + buzzer) e sensor de temperatura.

Fluxo de cada parte:
  luz      comando do app/agente/regra -> liga ou desliga o pino do LED -> grava evento
  clima    sensor DHT -> leitura a cada INTERVALO_LEITURA -> grava no banco -> avalia regras
  presença sensor PIR -> grava evento -> se o alarme está armado, toca o buzzer e avisa

Sem o hardware (por exemplo, rodando num notebook) o módulo entra em modo
simulado: os pinos viram objetos de mentira e o sensor devolve valores inventados.
Force o modo simulado com a variável de ambiente CASA_SIMULAR=1.
"""
from __future__ import annotations

import glob
import math
import os
import random
import threading
import time

import banco
import config

try:
    from gpiozero import LED, Buzzer, MotionSensor
    SIMULADO = os.environ.get("CASA_SIMULAR") == "1"
except ImportError:  # gpiozero só existe no Raspberry Pi
    SIMULADO = True


class _SaidaSimulada:
    """Substitui LED e Buzzer quando não há pinos de verdade."""

    def on(self): pass
    def off(self): pass
    def beep(self, **opcoes): pass


_trava = threading.Lock()
_luz = None
_buzzer = None
_presenca = None
_luz_ligada = False
_alarme_armado = False
_ultima_leitura = {"temperatura": None, "umidade": None, "momento": None}
_ultimo_movimento = 0.0
_regras_verdadeiras: set[int] = set()  # regras cuja condição já valia na leitura anterior

# Definido por quem inicia o programa: recebe o texto de um aviso para os celulares.
ao_avisar = lambda texto: None


# --- Luz ---

def definir_luz(ligada: bool, origem: str) -> None:
    """Liga ou desliga a luz. 'origem' diz quem pediu (app, agente, regra) e vai para o histórico."""
    global _luz_ligada
    with _trava:
        if ligada == _luz_ligada:
            return
        _luz.on() if ligada else _luz.off()
        _luz_ligada = ligada
    banco.gravar_evento("luz", f"{'ligada' if ligada else 'desligada'} ({origem})")


# --- Alarme e presença ---

def armar_alarme(armado: bool, origem: str) -> None:
    global _alarme_armado
    with _trava:
        if armado == _alarme_armado:
            return
        _alarme_armado = armado
    banco.gravar_evento("alarme", f"{'armado' if armado else 'desarmado'} ({origem})")


def _tocar_buzzer() -> None:
    bipes = max(1, int(config.SEGUNDOS_BUZZER / 0.4))
    _buzzer.beep(on_time=0.2, off_time=0.2, n=bipes, background=True)


def ao_detectar_movimento() -> None:
    """Chamado pelo gpiozero quando a saída do PIR sobe (e pelo botão de teste no modo simulado)."""
    global _ultimo_movimento
    agora = time.time()
    with _trava:
        repetido = agora - _ultimo_movimento < config.PAUSA_MOVIMENTO
        _ultimo_movimento = agora
        armado = _alarme_armado
    if repetido:
        return

    banco.gravar_evento("movimento", "alarme armado" if armado else "alarme desarmado")
    if armado:
        _tocar_buzzer()
        ao_avisar("Alarme: movimento detectado em casa.")
    for regra in banco.regras():
        if regra["condicao"] == "movimento":
            _executar_acao(regra, "movimento detectado")


# --- Temperatura e umidade ---

def _ler_dht() -> tuple[float | None, float | None]:
    """Lê o DHT pelo driver do kernel, que expõe os valores como arquivos em /sys.

    O sensor falha em parte das leituras (é normal nesse modelo), então tentamos algumas vezes.
    """
    if SIMULADO:
        onda = math.sin(time.time() / 600)
        return round(24 + 3 * onda + random.uniform(-0.2, 0.2), 1), round(55 - 10 * onda + random.uniform(-1, 1), 1)

    for _ in range(4):
        for pasta in glob.glob("/sys/bus/iio/devices/iio:device*"):
            try:
                with open(pasta + "/in_temp_input") as arquivo:
                    temperatura = int(arquivo.read()) / 1000  # o kernel entrega em milésimos
                with open(pasta + "/in_humidityrelative_input") as arquivo:
                    umidade = int(arquivo.read()) / 1000
                return round(temperatura, 1), round(umidade, 1)
            except (OSError, ValueError):
                continue
        time.sleep(1)
    return None, None


def _ciclo_de_leitura() -> None:
    """Roda para sempre numa thread: lê o sensor, grava e avalia as regras."""
    while True:
        temperatura, umidade = _ler_dht()
        if temperatura is not None:
            with _trava:
                _ultima_leitura.update(temperatura=temperatura, umidade=umidade, momento=int(time.time()))
            banco.gravar_leitura(temperatura, umidade)
            _avaliar_regras(temperatura, umidade)
        time.sleep(config.INTERVALO_LEITURA)


# --- Regras ---

def _avaliar_regras(temperatura: float, umidade: float) -> None:
    """Dispara cada regra só no momento em que a condição passa a valer, não a cada leitura."""
    for regra in banco.regras():
        sensor, _, sentido = regra["condicao"].partition("_")
        if sensor == "movimento":
            continue
        medida = temperatura if sensor == "temperatura" else umidade
        verdadeira = medida > regra["valor"] if sentido == "acima" else medida < regra["valor"]
        if verdadeira and regra["id"] not in _regras_verdadeiras:
            unidade = "°C" if sensor == "temperatura" else "%"
            _executar_acao(regra, f"{sensor} em {medida} {unidade}")
        if verdadeira:
            _regras_verdadeiras.add(regra["id"])
        else:
            _regras_verdadeiras.discard(regra["id"])


def _executar_acao(regra: dict, motivo: str) -> None:
    acao = regra["acao"]
    if acao == "avisar":
        ao_avisar(regra["texto"] or f"Regra {regra['id']}: {motivo}.")
    elif acao == "ligar_luz":
        definir_luz(True, f"regra {regra['id']}")
    elif acao == "desligar_luz":
        definir_luz(False, f"regra {regra['id']}")
    elif acao == "tocar_buzzer":
        _tocar_buzzer()
    banco.gravar_evento("regra", f"regra {regra['id']} disparou: {motivo}")


def descrever_regra(regra: dict) -> str:
    """Texto legível de uma regra, usado no app e nas respostas do agente."""
    sensor, _, sentido = regra["condicao"].partition("_")
    if sensor == "movimento":
        condicao = "houver movimento"
    else:
        unidade = "°C" if sensor == "temperatura" else "%"
        condicao = f"a {sensor} ficar {sentido} de {regra['valor']:g} {unidade}"
    acoes = {
        "avisar": f"avisar: {regra['texto']}" if regra["texto"] else "avisar no celular",
        "ligar_luz": "ligar a luz",
        "desligar_luz": "desligar a luz",
        "tocar_buzzer": "tocar o buzzer",
    }
    return f"Quando {condicao}, {acoes[regra['acao']]}"


# --- Estado e início ---

def estado() -> dict:
    with _trava:
        return {
            "luz": _luz_ligada,
            "alarme": _alarme_armado,
            **_ultima_leitura,
            "ultimo_movimento": int(_ultimo_movimento) or None,
            "simulado": SIMULADO,
        }


def iniciar() -> None:
    """Prepara os pinos e começa a ler os sensores."""
    global _luz, _buzzer, _presenca
    banco.iniciar()
    if SIMULADO:
        _luz, _buzzer = _SaidaSimulada(), _SaidaSimulada()
    else:
        _luz = LED(config.PINO_LUZ)
        _buzzer = Buzzer(config.PINO_BUZZER)
        _presenca = MotionSensor(config.PINO_PRESENCA)
        _presenca.when_motion = ao_detectar_movimento
    threading.Thread(target=_ciclo_de_leitura, daemon=True).start()
