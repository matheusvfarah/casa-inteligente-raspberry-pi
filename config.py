"""Configuração do projeto: pinos, intervalos e arquivos.

Os pinos usam a numeração BCM (GPIOxx), não a posição física no conector.
"""
from pathlib import Path

RAIZ = Path(__file__).parent

# --- Ligações no Raspberry Pi (numeração BCM) ---
PINO_LUZ = 17        # LED que representa a luz da casa   (pino físico 11)
PINO_BUZZER = 27     # buzzer ativo do alarme             (pino físico 13)
PINO_PRESENCA = 22   # saída do sensor de presença PIR    (pino físico 15)
PINO_DHT = 4         # dados do sensor DHT11/DHT22        (pino físico 7)
# O DHT é lido pelo driver do kernel; o pino dele é definido em
# /boot/firmware/config.txt (dtoverlay=dht11,gpiopin=4), não aqui.

# --- Tempos, em segundos ---
INTERVALO_LEITURA = 30   # de quanto em quanto tempo o sensor é lido e gravado
PAUSA_MOVIMENTO = 10     # movimentos mais próximos que isso contam como um só
SEGUNDOS_BUZZER = 3      # quanto tempo o buzzer toca quando o alarme dispara

# --- Arquivos ---
ARQUIVO_BANCO = RAIZ / "casa.db"
PASTA_RECEBIDOS = RAIZ / "recebidos"
ARQUIVO_TOKEN = RAIZ / ".token"

PORTA = 8000
