"""Lista fixa de ações que o agente pode executar: controlar a casa e olhar a rede.

O agente nunca recebe um terminal: só pode chamar o que está em FERRAMENTAS,
e nenhum comando é montado a partir de texto vindo do modelo.
"""
from __future__ import annotations

import ipaddress
import json
import shutil
import socket
import subprocess

import banco
import casa
import servidor

LIMITE_SAIDA = 20_000  # caracteres devolvidos ao modelo por ferramenta


def ip_local() -> str:
    """IP do Pi na rede local (não envia nada; só descobre a rota de saída)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]


def _rede_local() -> ipaddress.IPv4Network:
    ip = ip_local()
    saida = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True, timeout=5).stdout
    for linha in saida.splitlines():
        partes = linha.split()
        if "inet" in partes:
            interface = ipaddress.ip_interface(partes[partes.index("inet") + 1])
            if str(interface.ip) == ip:
                return interface.network
    return ipaddress.ip_network(f"{ip}/24", strict=False)


def _rodar(comando: list[str], tempo: int) -> str:
    if not shutil.which(comando[0]):
        return f"ERRO: '{comando[0]}' não está instalado. Rode: sudo apt install -y nmap avahi-utils"
    try:
        r = subprocess.run(comando, capture_output=True, text=True, timeout=tempo)
    except subprocess.TimeoutExpired:
        return f"ERRO: '{comando[0]}' passou de {tempo}s e foi interrompido."
    saida = (r.stdout + r.stderr).strip() or "(sem saída)"
    if len(saida) > LIMITE_SAIDA:
        saida = saida[:LIMITE_SAIDA] + f"\n[saída cortada: {len(saida)} caracteres no total]"
    return saida


def escanear_rede() -> str:
    rede = _rede_local()
    if not rede.is_private or rede.num_addresses > 1024:
        return f"ERRO: a rede {rede} não é uma rede local pequena; varredura recusada."
    ativos = _rodar(["nmap", "-sn", str(rede)], tempo=90)
    vizinhos = _rodar(["ip", "neigh"], tempo=5)
    return f"Rede: {rede}\n\n{ativos}\n\nTabela de vizinhos (IP -> MAC):\n{vizinhos}"


def descobrir_servicos() -> str:
    return _rodar(["avahi-browse", "--all", "--terminate", "--resolve", "--parsable"], tempo=30)


def celulares_conectados() -> str:
    return json.dumps(servidor.celulares_conectados(), ensure_ascii=False)


def enviar_aviso(texto: str) -> str:
    servidor.publicar("aviso", texto)
    return f"Aviso mostrado em {len(servidor.celulares_conectados())} celular(es) conectado(s)."


def _ler(caminho: str) -> str:
    try:
        with open(caminho) as arquivo:
            return arquivo.read().strip()
    except OSError:
        return ""


def status_do_pi() -> str:
    memoria = {}
    for linha in _ler("/proc/meminfo").splitlines():
        chave, _, valor = linha.partition(":")
        memoria[chave] = int(valor.split()[0]) // 1024  # MB
    disco = shutil.disk_usage("/")
    temperatura = _ler("/sys/class/thermal/thermal_zone0/temp")
    segundos_ligado = _ler("/proc/uptime").split()
    return json.dumps({
        "temperatura_c": round(int(temperatura) / 1000, 1) if temperatura else None,
        "carga_1_5_15_min": _ler("/proc/loadavg").split()[:3],
        "memoria_total_mb": memoria.get("MemTotal"),
        "memoria_disponivel_mb": memoria.get("MemAvailable"),
        "disco_total_gb": round(disco.total / 1e9, 1),
        "disco_livre_gb": round(disco.free / 1e9, 1),
        "horas_ligado": round(float(segundos_ligado[0]) / 3600, 1) if segundos_ligado else None,
        "ip": ip_local(),
    }, ensure_ascii=False)


def arquivos_recebidos() -> str:
    return json.dumps(servidor.arquivos_recebidos(), ensure_ascii=False)


def controlar_luz(ligada: bool) -> str:
    casa.definir_luz(ligada, "agente")
    return "Luz ligada." if ligada else "Luz desligada."


def armar_alarme(armado: bool) -> str:
    casa.armar_alarme(armado, "agente")
    return "Alarme armado." if armado else "Alarme desarmado."


def estado_da_casa() -> str:
    return json.dumps(casa.estado(), ensure_ascii=False)


def historico_da_casa(horas: int) -> str:
    return json.dumps(banco.resumo(min(max(horas, 1), 24 * 7)), ensure_ascii=False)


def criar_regra(condicao: str, valor: float, acao: str, texto: str) -> str:
    id_regra = banco.criar_regra(condicao, valor, acao, texto)
    return f"Regra {id_regra} criada."


def listar_regras() -> str:
    regras = [{"id": r["id"], "descricao": casa.descrever_regra(r)} for r in banco.regras()]
    return json.dumps(regras, ensure_ascii=False)


def apagar_regra(id: int) -> str:
    return f"Regra {id} apagada." if banco.apagar_regra(id) else f"Não existe regra {id}."


def _esquema(propriedades: dict) -> dict:
    return {
        "type": "object",
        "properties": propriedades,
        "required": list(propriedades),
        "additionalProperties": False,
    }


# nome -> (função, pede confirmação?, definição enviada ao modelo)
FERRAMENTAS = {
    "controlar_luz": (controlar_luz, False, {
        "description": "Liga ou desliga a luz da casa (o LED ligado ao Pi).",
        "input_schema": _esquema({"ligada": {"type": "boolean", "description": "true liga, false desliga."}}),
    }),
    "armar_alarme": (armar_alarme, False, {
        "description": "Arma ou desarma o alarme. Armado, qualquer movimento toca o buzzer e avisa os celulares.",
        "input_schema": _esquema({"armado": {"type": "boolean", "description": "true arma, false desarma."}}),
    }),
    "estado_da_casa": (estado_da_casa, False, {
        "description": "Estado atual da casa: luz, alarme, última temperatura (°C) e umidade (%), e o momento do "
                       "último movimento. Os momentos são segundos desde 1970. 'simulado' true significa que não há "
                       "sensores de verdade e os valores são inventados; avise o usuário nesse caso.",
        "input_schema": _esquema({}),
    }),
    "historico_da_casa": (historico_da_casa, False, {
        "description": "Resumo das leituras gravadas no banco nas últimas horas: mínima, máxima e média de "
                       "temperatura e umidade, e quantos movimentos houve.",
        "input_schema": _esquema({"horas": {"type": "integer", "description": "Tamanho do período, de 1 a 168."}}),
    }),
    "criar_regra": (criar_regra, True, {
        "description": "Cria uma regra de automação que o Pi passa a avaliar sozinho. Regras de temperatura e "
                       "umidade disparam uma vez, no momento em que o limite é cruzado. O usuário confirma no celular.",
        "input_schema": _esquema({
            "condicao": {"type": "string", "enum": list(banco.CONDICOES), "description": "O que dispara a regra."},
            "valor": {"type": "number", "description": "Limite em °C ou %. Use 0 quando a condição for movimento."},
            "acao": {"type": "string", "enum": list(banco.ACOES), "description": "O que o Pi faz quando dispara."},
            "texto": {"type": "string", "description": "Texto do aviso quando a ação é avisar; vazio nas demais."},
        }),
    }),
    "listar_regras": (listar_regras, False, {
        "description": "Lista as regras de automação existentes, com o id de cada uma.",
        "input_schema": _esquema({}),
    }),
    "apagar_regra": (apagar_regra, True, {
        "description": "Apaga uma regra de automação pelo id. O usuário confirma no celular.",
        "input_schema": _esquema({"id": {"type": "integer", "description": "Id da regra, vindo de listar_regras."}}),
    }),
    "escanear_rede": (escanear_rede, False, {
        "description": "Lista os dispositivos ativos na rede Wi-Fi do Pi (IP, nome e MAC quando disponíveis). "
                       "Demora até um minuto. Use para encontrar o celular ou saber o que está na rede.",
        "input_schema": _esquema({}),
    }),
    "descobrir_servicos": (descobrir_servicos, False, {
        "description": "Lista serviços anunciados na rede por mDNS/Bonjour (AirPlay, Chromecast, impressoras, "
                       "KDE Connect etc.), com nome e IP de quem anuncia.",
        "input_schema": _esquema({}),
    }),
    "status_do_pi": (status_do_pi, False, {
        "description": "Mostra a saúde do Pi: temperatura, carga da CPU, memória, disco, tempo ligado e IP.",
        "input_schema": _esquema({}),
    }),
    "celulares_conectados": (celulares_conectados, False, {
        "description": "Lista os celulares que estão com o app do Pi aberto neste momento.",
        "input_schema": _esquema({}),
    }),
    "enviar_aviso": (enviar_aviso, True, {
        "description": "Mostra um aviso em destaque no app de todos os celulares conectados (útil quando há mais "
                       "de um). O usuário confirma no celular antes do envio.",
        "input_schema": _esquema({"texto": {"type": "string", "description": "Texto do aviso."}}),
    }),
    "arquivos_recebidos": (arquivos_recebidos, False, {
        "description": "Lista os arquivos que os celulares enviaram para o Pi (nome e tamanho).",
        "input_schema": _esquema({}),
    }),
}


def definicoes() -> list[dict]:
    return [{"name": nome, "strict": True, **definicao} for nome, (_, _, definicao) in FERRAMENTAS.items()]


def executar(nome: str, entrada: dict, confirmar) -> tuple[str, bool]:
    """Executa uma ferramenta; devolve (resultado, houve_erro).

    confirmar(pergunta) -> bool é chamado antes das ações que pedem autorização.
    """
    if nome not in FERRAMENTAS:
        return f"Ferramenta desconhecida: {nome}", True
    funcao, pede_confirmacao, _ = FERRAMENTAS[nome]
    if pede_confirmacao:
        detalhes = ", ".join(f"{chave}: {valor}" for chave, valor in entrada.items())
        if not confirmar(f"O agente quer executar {nome} ({detalhes}). Permitir?"):
            return "O usuário não autorizou esta ação.", True
    try:
        return funcao(**entrada), False
    except Exception as erro:  # o modelo recebe o erro e decide o que fazer
        return f"ERRO: {erro}", True
