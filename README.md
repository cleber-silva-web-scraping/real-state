# Zillow Rental Scraper

Coletor de aluguéis do Zillow rodando um **Chrome real** dentro de um container
(LXDE + VNC), driblando o anti-bot **PerimeterX**. Todo o caminho quente (URLs e
detalhes) é **fetch/POST de JSON** — sem renderizar página (sem SSR). A página só é
renderizada em 2 casos: **captar o hash do GraphQL** e **resolver captcha**.

---

## Índice
1. [Como funciona (visão geral)](#como-funciona)
2. [Processo passo a passo](#processo-passo-a-passo)
3. [Estrutura do projeto](#estrutura-do-projeto)
4. [Como rodar](#como-rodar)
5. [Banco de dados](#banco-de-dados)
6. [Variáveis de ambiente](#variáveis-de-ambiente)
7. [Métricas](#métricas)

---

## Como funciona

Três peças:

- **Container** (`Dockerfile` + `entrypoint.sh`): Ubuntu + LXDE + VNC/noVNC + Chrome
  real + backend Python + 2 solvers (captcha e hash-clicker).
- **Extensão Chrome MV3** (`chrome-agents/`): faz polling no backend pedindo tarefas,
  executa os fetches **same-origin** na página já autenticada (herda cookies do PX,
  TLS e UA do Chrome → sem fingerprint mismatch) e devolve o resultado.
- **Backend** (`zillow_scraper/`): máquina de estados que orquestra tudo
  (coleta de URLs → captura de hash → detalhes), persiste em SQLite e expõe uma API
  HTTP local pra extensão.

### Por que browser real?
O PerimeterX bloqueia requests por fingerprint TLS/JA3. Fazendo o `fetch` **dentro da
página autenticada** (via extensão), o request sai com o fingerprint legítimo do
Chrome + os cookies `_px3` → não é bloqueado.

### O truque do hash (o problema difícil)
O detalhe da casa vem de um GraphQL **persistedQuery** (`sha256Hash`). Esse hash:
- **muda em cada deploy do Zillow** (expira) → não pode ficar hardcoded;
- a query da **property completa** (`zpid` + `deviceType`) **só dispara quando se
  CLICA numa casa** (transição SPA), nunca no load direto (que é SSR).

Solução: navegar em `/{estado}/rent-houses/`, **clicar numa casa de verdade** (clique
do SO via `xtest`, não JS — o JS é detectado), interceptar o GraphQL que a página
dispara e extrair o hash. O hash fica **só em memória** (é volátil) e é **re-capturado
automaticamente** quando expira (`PERSISTED_QUERY_NOT_IN_LIST`).

---

## Processo passo a passo

```
┌─ 1. COLETA DE URLs (POST, sem render) ────────────────────────────┐
│ async-create-search-page-state com faceting:                      │
│ estado → categoria → faixa de preço → sqft → flip de ordenação    │
│ (fura o teto de ~20 páginas/busca). Salva em urls (SQLite) + CSV.  │
└───────────────────────────────────────────────────────────────────┘
                              ↓
┌─ 2. CAPTURA DO HASH (render — só 1x por run / no expiry) ─────────┐
│ navega /{estado}/rent-houses/ → rola o container da lista (é       │
│ virtualizada) até casas (/homedetails/) aparecerem → manda as      │
│ coords pro hash-clicker → CLIQUE REAL (xtest) → a SPA dispara o     │
│ GET /graphql da property → inject_capture.js intercepta → o backend │
│ guarda o hash da casa (deviceType) e do apê (BuildingQuery).        │
└───────────────────────────────────────────────────────────────────┘
                              ↓
┌─ 3. DETALHES (POST/GET, sem render) ──────────────────────────────┐
│ p/ cada zpid que precisa de detalhe:                               │
│   • Casa  → GET  /graphql        (hash capturado, var zpid)        │
│   • Apê   → POST /zg-graph       (BuildingQuery, var buildingKey)  │
│ resposta = data.property / data.building → salva em details.       │
│ Se o hash expirou → volta ao passo 2 (re-captura) e retoma.        │
└───────────────────────────────────────────────────────────────────┘
```

**Sistema de 2 hashes (não re-raspar):** a tabela `urls` guarda um `state_hash` dos
campos voláteis (preço/endereço/fotos/quartos/área). Na próxima rodada, se o
`state_hash` do imóvel bate com o salvo, o detalhe é **pulado** (economiza a 2ª batida).

**Captcha:** se um fetch é bloqueado pelo PX, a página de busca é recarregada pra
forçar o "Press & Hold" a renderizar; o `captcha_solver` (processo separado, OCR +
clique real) resolve na tela e o fluxo retoma.

---

## Estrutura do projeto

```
zillow_scraper/            # pacote Python (backend)
├── __main__.py            # entrada: python -m zillow_scraper
├── config.py              # constantes / variáveis de ambiente
├── util.py                # helpers puros (now_iso, parse_int)
├── notifications.py       # Telegram
├── server.py              # servidor HTTP (API p/ a extensão) + main()
├── search.py              # Zillow: busca de URLs (faceting) + detalhe (GraphQL)
├── storage.py             # SQLite (urls, details, run_metrics)
├── crawl/                 # máquina de estados, 1 arquivo por responsabilidade
│   ├── state.py           #   orquestrador (CrawlState = composição dos mixins)
│   ├── urls.py            #   estágio 1: coleta de URLs
│   ├── capture.py         #   estágio 2: captura do hash (clique real)
│   ├── detail.py          #   estágio 3: detalhe via GraphQL
│   ├── persistence.py     #   checkpoint / finalização / snapshot
│   └── metrics.py         #   métricas
└── solvers/
    ├── captcha.py         # OCR + clique real do "Press & Hold"
    └── clicker.py         # clique real (xtest) p/ captar o hash

chrome-agents/             # extensão Chrome MV3
├── manifest.json
├── background.js          # loop de polling, dispatch das ações, relay de cliques
├── content.js             # fetch same-origin, captura de coords, extração
└── inject_capture.js      # interceptor MAIN-world do GraphQL (pega o hash)

Dockerfile · entrypoint.sh · dev.sh · scale_test.sh
```

---

## Como rodar

Backend no host na porta **8010** (8000 costuma estar em uso). noVNC em **6911**.

### Rodada diária (o jeito normal)

```bash
bash dev.sh            # sobe o container em daemon coletando WY, acumulando no
                       # mesmo banco; encerra sozinho ao terminar
```
`bash dev.sh` (sem argumento) = `daily`: build + remove só o checkpoint (força
re-coleta → acha imóveis novos e mudanças; o 2-hash pula os inalterados) + sobe em
daemon. **Mantém o `out/zillow.db`** (acumula dia após dia). Muda a região com
`REGION=CA bash dev.sh`.

Agendar todo dia (cron):
```cron
0 3 * * *  cd /caminho/new-zillow && REGION=WY bash dev.sh >> out/cron.log 2>&1
```

### Agendado (cron) — liga, roda 1 estado, desliga a máquina

`scheduled_run.sh <ESTADO>`: atualiza um estado (acumulando), espera terminar e
**desliga a máquina** (`poweroff`). Pensado pra uma máquina **Debian** que liga
agendada (RTC/BIOS) no topo da hora; o cron dispara 15min depois.

A imagem é **buildada na própria máquina** pelo `setup.sh` (build local; não
transferir imagem entre sistemas). Build **não apaga o banco**.

**Setup 1× na máquina** (permissões do `out/` + instala o cron):
```bash
sudo bash setup.sh
```
Idempotente. Faz `chown 1000` no `out/`, deixa os scripts executáveis e instala o
cron no **root**. Cada linha pode ter **1 estado** ou **vários agrupados** (rodam em
**sequência** no mesmo boot, com **1 start listando todos + relatório por estado**):
```cron
# pequenos juntos de manhã, grande sozinho à noite (menos liga/desliga):
15 6  * * *  /CAMINHO/new-zillow/scheduled_run.sh "WY,SD,KY"
15 18 * * *  /CAMINHO/new-zillow/scheduled_run.sh KS
```
Agrupar estados pequenos (atualizam em 3-5min cada) permite **mais atualizações/dia**
(ex: manhã + noite) com menos ciclos de liga/desliga. A captura do hash roda **1×**
e serve pra todos os estados da sequência.
(pra trocar estados/horários, edite o bloco no `setup.sh` e rode de novo, ou
`sudo crontab -e`).
- **root** porque usa `docker` + `poweroff`.
- `timeout 6h` (failsafe): trava → desliga mesmo assim.
- Sem build no agendado (imagem fixa). Log em `out/cron.log`.
- A máquina precisa estar de pé antes do HH:15 (cron não recupera minuto perdido;
  os 15min cobrem o boot).

### Outros comandos (`dev.sh`)

```bash
REGION=CA bash dev.sh run        # sobe coletando (banco se cria/acumula, nunca apaga)
REGION=WY MAXURLS=5 bash dev.sh diag   # teste rápido (5 imóveis, acumula)
bash dev.sh status              # progresso (JSON)
bash dev.sh logs                # logs do container
bash dev.sh stop                # para
```

| comando | o que faz |
|---|---|
| `build` | builda a imagem `local/zillow:1.0.0` (**não** toca no banco) |
| `run`   | sobe o container (banco se cria se não existe; **nunca apaga**) |
| `diag` | build + run com 5 imóveis (teste; acumula) |
| `resume` | build + run (retoma do checkpoint) |
| `daily` (ou sem arg) | re-coleta + acumula (remove só checkpoint) |
| `status` / `logs` / `stop` / `exec '<cmd>'` | utilitários |

> **Sem comando destrutivo.** O banco se cria sozinho (`init_db`) e **nunca é apagado
> pelo tooling**. Pra zerar de propósito (raro): `rm out/zillow.db` na mão.

Variáveis: `REGION` (estado, ex `CA`/`WY`/`TX`; aceita CSV), `MAXURLS` (0=todos),
`HPORT` (porta host, default 8010).

### Na mão (`docker`)

```bash
docker build -t local/zillow:1.0.0 .

docker run -d --name zillow --privileged \
  -p 6911:6901 -p 5911:5901 -p 8010:8000 -p 9232:9222 \
  -v "$(pwd)/out:/home/rpa/out" -v "$(pwd)/.env:/home/rpa/.env:ro" \
  -v /dev/shm:/dev/shm -v /run/dbus:/run/dbus --shm-size=2g \
  -e POC_MODE=1 -e POC_BROWSER_SEQUENCE=chrome -e POC_BROWSER_ROTATION_ENABLED=0 \
  -e POC_BROWSER_DISABLE_SANDBOX=1 -e POC_BROWSER_DISABLE_GPU=1 \
  -e POC_CAPTCHA_DRYRUN=0 -e POC_EXIT_AFTER_FINISH=1 \
  -e POC_COLLECT_MODE=api -e REGION=CA -e POC_COLLECT_MAX_URLS=0 \
  local/zillow:1.0.0

curl -s http://localhost:8010/status | python3 -m json.tool
```

| host | container | serviço |
|---|---|---|
| 6911 | 6901 | noVNC → `http://localhost:6911/vnc.html` |
| 8010 | 8000 | backend (`/status`, `/result`) |
| 9232 | 9222 | chrome remote debug |

---

## Banco de dados

SQLite em `out/zillow.db` (env `POC_DB_FILE`). 3 tabelas:

### `urls` — imóveis descobertos na busca
| coluna | tipo | descrição |
|---|---|---|
| `zpid` | TEXT PK | id do imóvel no Zillow |
| `url` | TEXT | URL do detalhe (`/homedetails/...` ou `/apartments/...`) |
| `address` | TEXT | endereço |
| `beds` / `baths` / `area` | TEXT | quartos / banheiros / área |
| `price` | TEXT | preço do aluguel |
| `state` | TEXT | estado (ex `CA`) |
| `category` | TEXT | faceta (`isSingleFamily`, `isCondo`, ...) |
| `listing_type` | TEXT | tipo (casa/apê) |
| `state_hash` | TEXT | hash dos campos voláteis (skip de re-raspagem) |
| `first_seen` / `last_seen` | TEXT | timestamps |
| `active` | INTEGER | **delete lógico**: 1=ativo, 0=removido (sumiu da busca) |
| `removed_at` | TEXT | quando foi marcado removido (linha + detalhe ficam) |

> Imóvel que some da busca → `active=0` + `removed_at` (nada apagado). Se reaparece,
> volta `active=1`. Escopado por estado: rodar SD não mexe no WY. Imóveis vivos =
> `WHERE active=1`.

### `details` — JSON completo do detalhe
| coluna | tipo | descrição |
|---|---|---|
| `zpid` | TEXT PK | id do imóvel |
| `json` | TEXT | resposta GraphQL completa (`data.property`/`data.building`: resoFacts, fotos, amenities, preço, etc) |
| `state_hash` | TEXT | hash com que foi raspado (compara na próxima rodada) |
| `updated_at` | TEXT | timestamp |

### `run_metrics` — métricas por execução
| coluna | tipo | descrição |
|---|---|---|
| `run_id` | TEXT | id da execução |
| `saved_at` | TEXT | timestamp |
| `json` | TEXT | métricas (tempo, bytes, page_loads, etc) |

Consulta rápida:
```bash
python3 -c "import sqlite3;c=sqlite3.connect('out/zillow.db');\
print('urls',c.execute('SELECT COUNT(*) FROM urls').fetchone()[0],\
'details',c.execute('SELECT COUNT(*) FROM details').fetchone()[0])"
```

---

## Variáveis de ambiente

| env | default | uso |
|---|---|---|
| `REGION` | — | estado a coletar (`CA`/`WY`/`TX`...). Atalho p/ `POC_COLLECT_STATES` |
| `POC_COLLECT_MODE` | — | `api` ativa o fluxo Zillow |
| `POC_COLLECT_MAX_URLS` | `0` | teto de imóveis (0 = todos) |
| `POC_CAPTCHA_DRYRUN` | `1` | `0` = captcha resolve de verdade |
| `POC_HOUSE_HASH` / `POC_APT_HASH` | hash atual | semente do persistedQuery (auto-capturado; isto é só fallback) |
| `POC_DB_FILE` | `/home/rpa/out/zillow.db` | arquivo SQLite |
| `POC_COLLECT_MAX_ITEMS_FACET` | `400` | subdivide a busca por preço quando o total passa disto. **menor = +seguro/lento**, maior = +rápido |
| `POC_COLLECT_SORT_TAKE_PAGES` | `20` | páginas por ponta no fallback irredutível (20 ≈ 100% do acessível) |
| `POC_PAYMENT_MIN_STEP` | `25` | granularidade mín. da faixa de preço (1 = fatia ao máximo, +completo +lento) |

> **Cobertura máxima (~100%):** defaults já são seguros. Pra o extremo:
> `POC_COLLECT_MAX_ITEMS_FACET=200 POC_PAYMENT_MIN_STEP=1`. Pra **velocidade**:
> `POC_COLLECT_MAX_ITEMS_FACET=700`.

---

## Métricas

Medido (1 container, CA): **~0,9 s/registro** e **~235 KB/registro** (linear). A captura
do hash é fixa (~60 s, 1× por run; amortiza na escala). Banda real total via
`docker stats zillow`. Harness de medição: `scale_test.sh`.
