# Casa Inteligente com Raspberry Pi

Painel de automação residencial que roda em um Raspberry Pi 3B e é controlado
pelo celular. O Pi acende uma luz, mede temperatura e umidade, vigia a casa com
um sensor de presença e guarda tudo em um banco de dados. Um agente de IA
(Claude) entende pedidos em português e cria regras de automação.

Trabalho prático da disciplina de Engenharia de Dados — UEMG, Unidade Passos.
Professor: Marcelo Nogueira de Almeida.

Integrantes: _preencher com os nomes completos do grupo_

<img src="docs/app-casa.png" alt="Aba Casa do app, em modo simulado" width="320">

## O que o sistema faz

- **Luz:** liga e desliga um LED pelo app, pelo agente ou por uma regra.
- **Clima:** lê temperatura e umidade a cada 30 segundos, grava no banco e mostra em gráfico.
- **Alarme:** com o alarme armado, um movimento toca o buzzer e manda um aviso para o celular.
- **Regras:** "quando a temperatura passar de 30 °C, me avise" vira uma regra que o Pi avalia sozinho.
- **Agente:** conversa em português e executa as ações acima, além de olhar a rede e o estado do Pi.

A aba **Casa** do app fala direto com o Pi e funciona sem internet. Só a aba
**Conversa** depende de internet, porque o agente usa a API da Anthropic.

## Fluxo: entrada → processamento → saída

| Entrada | Processamento | Saída |
|---|---|---|
| Toque no app (rede) | `servidor.py` recebe e chama `casa.py` | LED acende ou apaga; evento gravado no banco |
| Sensor DHT11 (temperatura e umidade) | `casa.py` lê a cada 30 s e avalia as regras | Leitura no SQLite, gráfico no app, ação da regra |
| Sensor de presença PIR | `casa.py` verifica se o alarme está armado | Buzzer toca, aviso no celular, evento gravado |
| Pedido em português (rede) | `agente.py` envia ao modelo, que escolhe uma ação de `ferramentas.py` | Ação executada e resposta no app |

## Materiais

- Raspberry Pi 3B (ou 3B+) com Raspberry Pi OS e cartão microSD
- 1 LED e 1 resistor de 330 Ω
- 1 sensor de temperatura e umidade DHT11 (módulo de 3 pinos)
- 1 sensor de presença PIR HC-SR501
- 1 buzzer ativo de 5 V ou 3,3 V
- Protoboard e jumpers
- Celular na mesma rede Wi-Fi

## Montagem e conexões

Os números GPIO são da numeração BCM; entre parênteses está o pino físico do conector.

| Componente | Pino do componente | Liga em |
|---|---|---|
| LED | anodo (perna longa), através do resistor de 330 Ω | GPIO17 (pino 11) |
| LED | catodo (perna curta) | GND (pino 9) |
| Buzzer | positivo | GPIO27 (pino 13) |
| Buzzer | negativo | GND (pino 14) |
| PIR HC-SR501 | VCC | 5 V (pino 2) |
| PIR HC-SR501 | OUT | GPIO22 (pino 15) |
| PIR HC-SR501 | GND | GND (pino 6) |
| DHT11 | VCC | 3,3 V (pino 1) |
| DHT11 | DATA | GPIO4 (pino 7) |
| DHT11 | GND | GND (pino 20) |

Os pinos ficam em `config.py`. Se o DHT11 for o sensor solto de 4 pernas, e não
o módulo, ligue também um resistor de 10 kΩ entre DATA e VCC.

## Instalação no Raspberry Pi

1. Copie o projeto para o Pi:
   ```bash
   git clone https://github.com/matheusvfarah/casa-inteligente-raspberry-pi.git ~/uemg
   cd ~/uemg
   ```
2. Instale os programas do sistema:
   ```bash
   sudo apt install -y python3-venv python3-gpiozero python3-lgpio nmap avahi-utils
   ```
3. Ative o driver do sensor DHT11 e reinicie o Pi:
   ```bash
   echo "dtoverlay=dht11,gpiopin=4" | sudo tee -a /boot/firmware/config.txt
   sudo reboot
   ```
4. Crie o ambiente Python. A opção `--system-site-packages` deixa o ambiente
   usar o `gpiozero` instalado pelo sistema:
   ```bash
   cd ~/uemg
   python3 -m venv --system-site-packages .venv
   .venv/bin/pip install -r requirements.txt
   ```
5. Inicie:
   ```bash
   export ANTHROPIC_API_KEY="sua-chave"   # opcional: só o agente precisa dela
   .venv/bin/python agente.py
   ```

O programa mostra um link como `http://192.168.15.99:8000/?t=AbC123...`. Abra
no celular, na mesma Wi-Fi. O link é o mesmo a cada reinício, então dá para
fixar o app na tela inicial do celular. Para trocar o link, apague o arquivo
`.token` e reinicie.

### Testar sem o hardware

Em um notebook, ou no Pi sem os componentes, rode em modo simulado. O app mostra
uma faixa avisando que os valores são inventados e um botão para simular movimento.

```bash
CASA_SIMULAR=1 python agente.py
```

### Subir junto com o Pi

Crie `/etc/systemd/system/casa.service` (troque `uemg` pelo seu usuário):

```ini
[Unit]
Description=Casa Inteligente
After=network-online.target
Wants=network-online.target

[Service]
User=uemg
WorkingDirectory=/home/uemg/uemg
Environment=ANTHROPIC_API_KEY=sua-chave
ExecStart=/home/uemg/uemg/.venv/bin/python agente.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Depois: `sudo systemctl enable --now casa`.

## Como o código está organizado

| Arquivo | Papel |
|---|---|
| `config.py` | Pinos, intervalos e caminhos dos arquivos. |
| `banco.py` | Banco SQLite: tabelas `leituras`, `eventos` e `regras`. |
| `casa.py` | Hardware e lógica da casa: LED, buzzer, PIR, DHT11 e avaliação das regras. |
| `servidor.py` | Servidor web (biblioteca padrão) que atende o app do celular. |
| `ferramentas.py` | Lista fixa de ações que o agente pode executar. |
| `agente.py` | Ponto de entrada; conversa com o modelo e executa as ações pedidas. |
| `pagina.html` | O app do celular: abas Casa, Conversa e Arquivos. |

### Banco de dados

| Tabela | Colunas | Uso |
|---|---|---|
| `leituras` | `momento`, `temperatura`, `umidade` | Uma linha a cada leitura do sensor. |
| `eventos` | `momento`, `tipo`, `detalhe` | Luz, alarme, movimento e regras disparadas. |
| `regras` | `id`, `condicao`, `valor`, `acao`, `texto` | Regras de automação criadas pelo agente. |

`momento` é guardado em segundos desde 1970. Para o gráfico, `banco.historico()`
agrupa as leituras em médias, de modo que 24 horas viram cerca de 240 pontos.

### Regras de automação

Uma regra tem uma condição (`temperatura_acima`, `temperatura_abaixo`,
`umidade_acima`, `umidade_abaixo` ou `movimento`), um valor limite e uma ação
(`avisar`, `ligar_luz`, `desligar_luz` ou `tocar_buzzer`). Regras de
temperatura e umidade disparam uma vez, quando o limite é cruzado, e só voltam
a disparar depois que a medida retorna ao lado normal. As regras ficam no banco
e continuam valendo sem internet.

### Ações do agente

| Ação | O que faz | Pede confirmação |
|---|---|---|
| `controlar_luz` | Liga ou desliga a luz | não |
| `armar_alarme` | Arma ou desarma o alarme | não |
| `estado_da_casa` | Luz, alarme, última leitura e último movimento | não |
| `historico_da_casa` | Mínima, máxima e média do período | não |
| `criar_regra` | Cria uma regra de automação | sim |
| `listar_regras` | Lista as regras | não |
| `apagar_regra` | Apaga uma regra | sim |
| `escanear_rede` | Lista os dispositivos na Wi-Fi do Pi | não |
| `descobrir_servicos` | Lista serviços mDNS (AirPlay, Chromecast, impressoras) | não |
| `status_do_pi` | Temperatura da CPU, carga, memória e disco | não |
| `celulares_conectados` | Celulares com o app aberto | não |
| `enviar_aviso` | Mostra um aviso em todos os celulares | sim |
| `arquivos_recebidos` | Arquivos enviados pelos celulares | não |

As confirmações aparecem no celular como um cartão com Permitir e Negar.

## Segurança

- O agente não tem terminal: só executa as ações de `ferramentas.py`.
- O app exige o token do link; quem tem o link controla a casa, então não o compartilhe.
- A varredura de rede só roda na rede local do próprio Pi.
- A conexão é HTTP sem criptografia; use só em uma Wi-Fi de confiança.
- A chave da API fica só no Pi, em variável de ambiente, e não entra no repositório.

## Limitações

- O app só funciona na mesma rede Wi-Fi do Pi.
- A conversa com o agente se perde quando o programa reinicia; leituras, eventos e regras ficam no banco.
- O DHT11 tem precisão de cerca de 2 °C e 5 % de umidade e falha em parte das leituras; o código tenta de novo.
