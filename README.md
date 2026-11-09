# Controle de Estoque

API REST em Python para controlar o estoque de produtos considerando os estados
**disponível**, **reservado** e **bloqueado**, com entrada, saída, reserva,
bloqueio, liberação e consulta.

- **Stack:** Python 3.10+, FastAPI, Pydantic v2, SQLite (biblioteca padrão), pytest + Hypothesis
- **Testes:** 123 testes (unidade, propriedades, integração, concorrência e API), cobertura de 99%

## Mapa da entrega

| Item pedido no case | Onde está |
|---|---|
| Código-fonte e instruções de execução local | `inventory/` · [seção 6](#6-instruções-para-execução) |
| Testes automatizados | `tests/` · [seção 8](#8-estratégia-de-testes) |
| Visão geral da solução | [seção 1](#1-visão-geral-da-solução) |
| Arquitetura/estrutura adotada | [seção 2](#2-arquitetura-e-estrutura) |
| Principais decisões técnicas | [seção 3](#3-principais-decisões-técnicas) |
| Premissas consideradas | [seção 4](#4-premissas) |
| Limitações conhecidas | [seção 5](#5-limitações-conhecidas) |
| O que faria diferente em produção | [seção 7](#7-o-que-eu-faria-diferente-em-produção) |
| Visão de engenharia (5 aspectos) | [seção 9](#9-visão-de-engenharia-5-aspectos-mais-importantes) |
| Uso de IA (prompts e análise) | [`docs/USO_DE_IA.md`](docs/USO_DE_IA.md) |

### Funcionalidades pedidas

| Funcionalidade do case | Endpoint |
|---|---|
| 1. Entrada | `POST /products/{sku}/entries` |
| 2. Saída (só com disponível suficiente) | `POST /products/{sku}/exits` |
| 3. Reservar (com id para consulta e liberação) | `POST /products/{sku}/reservations` · `GET /reservations/{id}` |
| 4. Bloquear (com id para consulta e liberação) | `POST /products/{sku}/blocks` · `GET /blocks/{id}` |
| 5. Liberar reserva ou bloqueio | `POST /reservations/{id}/release` · `POST /blocks/{id}/release` |
| 6. Consultar total, disponível, reservado e bloqueado | `GET /products/{sku}` |

---

## 1. Visão geral da solução

A solução é uma API HTTP. Cada produto tem um saldo total dividido em três estados.
Entradas e saídas mexem no total (a saída só usa o disponível), e reservas e bloqueios
movem unidades do disponível para um estado retido até serem liberados. Toda operação
é atômica: ou é aplicada por inteiro, ou é recusada sem alterar nada.

```
total = disponível + reservado + bloqueado

                   reservar ─────────►  RESERVADO
 entrada ─►  DISPONÍVEL                     │
 saída   ◄─       ▲  ◄─────── liberar ──────┘
                  │  ◄─────── liberar ──────┐
                   bloquear ─────────►  BLOQUEADO
```

- **Produto** guarda três contadores: `total`, `reserved`, `blocked`.
  O **disponível nunca é armazenado**: é sempre `total - reserved - blocked`, então
  não existe a possibilidade de ele "descolar" dos outros números.
- **Retenção (Hold)** é cada reserva ou bloqueio individual, com `id` (UUID), quantidade,
  referência (pedido/solicitação ou motivo) e status `ACTIVE` → `RELEASED`.
  Um produto pode ter várias retenções ativas ao mesmo tempo, inclusive várias do mesmo pedido.
- **Movimentação** é o registro imutável de cada operação bem-sucedida, com o saldo
  resultante (trilha de auditoria).

**Invariantes** (verificadas no domínio *e* garantidas pelo banco):
`total ≥ 0`, `reserved ≥ 0`, `blocked ≥ 0`, `reserved + blocked ≤ total` (ou seja, `disponível ≥ 0`),
e `reserved`/`blocked` são iguais à soma das retenções ativas de cada tipo.

---

## 2. Arquitetura e estrutura

Camadas simples, com dependências apontando para dentro (o domínio não conhece banco nem HTTP):

```
inventory/
├── domain.py        Regras de negócio puras: Product, Hold, Movement, validações e invariantes
├── errors.py        Erros de negócio com código estável e detalhes estruturados
├── service.py       Casos de uso: abre transação, chama o domínio, persiste, registra movimentação
├── ports.py         Interfaces (Protocols) de repositório e unidade de trabalho
├── sqlite_store.py  Implementação SQLite das interfaces + schema com CHECK constraints
├── schemas.py       Contratos HTTP (Pydantic): validação de entrada e formato de saída
└── api.py           Rotas FastAPI e tradução de erros de negócio para HTTP
tests/
├── unit/            Domínio isolado + teste de propriedades (Hypothesis)
├── integration/     Serviço + SQLite real, atomicidade, constraints e concorrência
└── api/             Contrato HTTP: status, formato de erro, validações
```

Fluxo de uma reserva: `api` valida o JSON → `service` abre uma transação →
carrega o produto → `Product.reserve()` valida e altera os contadores →
grava produto, retenção e movimentação → `COMMIT`. Qualquer exceção em qualquer
ponto → `ROLLBACK`.

---

## 3. Principais decisões técnicas

| Decisão | Por quê | Alternativa considerada |
|---|---|---|
| **Disponível calculado, não armazenado** | Elimina uma classe inteira de inconsistências: não há como gravar um disponível errado. | Armazenar os 4 números e manter sincronizados (mais pontos de falha). |
| **Contadores no produto + registro de cada retenção** | Consulta e validação em O(1), sem somar reservas a cada operação; as retenções dão rastreabilidade e permitem liberar por id. Os dois são gravados na mesma transação. | Calcular reservado/bloqueado somando as retenções toda vez (mais simples, mas fica lento com muitas retenções). |
| **Regras no domínio puro, validando antes de alterar** | Uma operação inválida lança exceção antes de mexer em qualquer contador, então o objeto nunca fica pela metade. É também a parte mais fácil de testar. | Regras espalhadas nas rotas ou em SQL. |
| **Transação por operação com `BEGIN IMMEDIATE`** | O lock de escrita é obtido no início, então "ler saldo → validar → gravar" não se intercala com outra escrita. Testado com 50 reservas simultâneas disputando o mesmo saldo. Com `BEGIN` comum, os mesmos testes falham com `database is locked`. | Lock otimista com coluna `version` + retry; `UPDATE ... WHERE disponível >= x` condicional. |
| **CHECK constraints no banco** | Defesa em profundidade: mesmo que um bug ou script manual tente gravar saldo negativo, o banco recusa e a transação é desfeita. | Confiar só no código. |
| **SQLite** | Zero infraestrutura para rodar o case, transações ACID reais, schema portável para PostgreSQL. | PostgreSQL via Docker (melhor para produção, mais atrito para avaliar). Em memória (não persistiria e não testaria transações). |
| **SQL direto em vez de ORM** | São poucas tabelas e queries simples; o SQL explícito deixa claro onde está a transação e o lock. | SQLAlchemy (faria sentido se o modelo crescesse). |
| **Repositórios atrás de `Protocol`** | O serviço não depende do SQLite; trocar por PostgreSQL é escrever outra implementação de `ports.py`. | Serviço acessando o SQLite direto (menos código, mais acoplamento). |
| **Reservas e bloqueios como o mesmo conceito (`Hold`)** | Têm exatamente o mesmo ciclo de vida (criar → consultar → liberar); só muda o contador afetado. Evita duplicar código. As rotas continuam separadas (`/reservations`, `/blocks`) para a API ficar explícita. | Duas entidades e dois fluxos quase idênticos. |
| **Liberar é `POST .../release`, não `DELETE`** | A retenção não some: muda para `RELEASED` e fica no histórico. | `DELETE /reservations/{id}` (perderia o registro). |
| **Liberação idempotente** | Se o cliente repetir a liberação (ex.: retry após timeout), recebe 200 com a mesma retenção, e o estoque não é devolvido duas vezes nem é gerada nova movimentação. O resultado desejado ("esta reserva está liberada") já foi atingido. O domínio continua recusando liberar uma retenção inativa; a idempotência é uma política do caso de uso. | Retornar 409 na segunda chamada (expõe erro do cliente, mas complica retries). |
| **Validação estrita na API** (`strict`, `extra="forbid"`) | `"5"`, `5.5`, `true` e campos com nome errado (ex.: `quantidade`) são recusados com 422, em vez de convertidos ou ignorados em silêncio. | Validação permissiva padrão do Pydantic. |
| **Histórico de movimentações** | Auditoria barata: cada operação grava tipo, quantidade, referência e saldo resultante, na mesma transação. | Sem histórico (não daria para explicar como o saldo chegou ao valor atual). |

---

## 4. Premissas

- A **identificação do produto** é um SKU alfanumérico (`A-Z a-z 0-9 . _ -`, até 64 caracteres), e o produto precisa ser **cadastrado antes** de receber movimentações. Assim um SKU digitado errado gera 404, em vez de criar um produto fantasma.
- Quantidades são **unidades inteiras**, maiores que zero, com limite de 1 bilhão por operação.
- **Saída** retira apenas do **disponível**; unidades reservadas ou bloqueadas não podem sair.
- **Bloqueio** também só pode usar unidades disponíveis (ex.: avaria, inspeção, inventário).
- **Liberar** devolve a quantidade ao disponível. Uma retenção é liberada **inteira** (sem liberação parcial).
- Não existe "baixa de reserva" (transformar uma reserva em saída). No escopo pedido, isso se faz liberando a reserva e registrando a saída. Ver evolução abaixo.
- Reservas **não expiram** automaticamente.
- A `reference` é livre e opcional: serve para associar a retenção a um pedido/solicitação ou motivo. Um mesmo pedido pode ter várias retenções.
- Datas em UTC.

---

## 5. Limitações conhecidas

- **SQLite serializa todas as escritas** (um lock para o banco inteiro). Correto, mas operações em produtos diferentes esperam umas pelas outras. Serve para o case, não para alto volume.
- **Idempotência só na liberação:** se o cliente repetir uma entrada, saída, reserva ou bloqueio por timeout, a operação é aplicada duas vezes.
- **Sem autenticação/autorização** e sem identificação de quem fez cada operação.
- **Sem liberação parcial, expiração de reservas nem baixa de reserva**.
- **Sem migrações de schema** (o schema é criado com `CREATE TABLE IF NOT EXISTS`).
- Histórico sem paginação por cursor (apenas `limit`).
- Um único depósito/local por produto.

---

## 6. Instruções para execução

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

# Subir a API (cria o banco em data/estoque.db)
uvicorn inventory.api:create_app --factory --reload

# Rodar os testes
pytest
pytest --cov=inventory --cov-report=term-missing   # com cobertura
```

A documentação interativa (Swagger) fica em **http://localhost:8000/docs**: dá para
executar todas as operações pelo navegador.

O caminho do banco pode ser trocado com a variável `INVENTORY_DB_PATH`.

### Exemplo rápido

```bash
curl -X POST localhost:8000/products -H 'Content-Type: application/json' \
     -d '{"sku": "SKU-001", "name": "Camiseta Azul M"}'
curl -X POST localhost:8000/products/SKU-001/entries -H 'Content-Type: application/json' \
     -d '{"quantity": 10, "reference": "NF-123"}'
curl -X POST localhost:8000/products/SKU-001/reservations -H 'Content-Type: application/json' \
     -d '{"quantity": 3, "reference": "PEDIDO-42"}'
# -> {"id": "8f1c...", "type": "RESERVATION", "status": "ACTIVE", ...}
curl localhost:8000/products/SKU-001
# -> {"sku": "SKU-001", "total": 10, "available": 7, "reserved": 3, "blocked": 0, ...}
curl -X POST localhost:8000/reservations/8f1c.../release
```

### Endpoints

| Funcionalidade | Método e rota | Sucesso |
|---|---|---|
| Cadastrar produto | `POST /products` | 201 |
| **Consultar** situação | `GET /products/{sku}` | 200 |
| **Entrada** | `POST /products/{sku}/entries` | 200 |
| **Saída** | `POST /products/{sku}/exits` | 200 |
| **Reservar** | `POST /products/{sku}/reservations` | 201 |
| **Bloquear** | `POST /products/{sku}/blocks` | 201 |
| Consultar reserva / bloqueio | `GET /reservations/{id}` · `GET /blocks/{id}` | 200 |
| **Liberar** reserva / bloqueio | `POST /reservations/{id}/release` · `POST /blocks/{id}/release` | 200 |
| Listar reservas / bloqueios do produto | `GET /products/{sku}/reservations?status=ACTIVE` (idem `blocks`) | 200 |
| Histórico de movimentações | `GET /products/{sku}/movements` | 200 |

Erros seguem sempre o mesmo formato, com um `code` estável para o cliente tratar:

```json
{ "error": { "code": "INSUFFICIENT_AVAILABLE_STOCK",
             "message": "Quantidade disponível insuficiente para o produto 'SKU-001': solicitado 11, disponível 10.",
             "details": { "sku": "SKU-001", "requested": 11, "available": 10 } } }
```

| Status | Quando | Códigos |
|---|---|---|
| 404 | Recurso não existe | `PRODUCT_NOT_FOUND`, `HOLD_NOT_FOUND` |
| 409 | Operação conflita com o estado atual | `INSUFFICIENT_AVAILABLE_STOCK`, `PRODUCT_ALREADY_EXISTS` |
| 422 | Entrada inválida | `VALIDATION_ERROR` |
| 500 | Erro inesperado (detalhes só no log) | `INTERNAL_ERROR` |

---

## 7. O que eu faria diferente em produção

1. **PostgreSQL** com lock por linha (`SELECT ... FOR UPDATE` no produto) ou `UPDATE` condicional (`... WHERE total - reserved - blocked >= :qtd`), para que produtos diferentes não disputem o mesmo lock. Mantendo as CHECK constraints.
2. **Idempotência:** header `Idempotency-Key` nas operações de escrita, guardado com a resposta, para retries seguros. Hoje só a liberação é idempotente, porque o próprio id da retenção identifica a operação; entradas, saídas e novas retenções precisariam de uma chave enviada pelo cliente.
3. **Migrações** versionadas (Alembic ou Flyway) e **ORM/query builder** se o modelo crescer.
4. **Novos fluxos de negócio:** baixa de reserva (reserva → saída numa única operação atômica), liberação parcial, expiração de reservas com job agendado e múltiplos depósitos.
5. **Eventos:** publicar `StockReserved`, `StockReleased` etc. via *transactional outbox* (gravados na mesma transação), para que outros sistemas reajam sem consultar a API.
6. **Segurança:** autenticação (OAuth2/JWT), autorização por papel (ex.: só o time de qualidade bloqueia) e registro do usuário em cada movimentação.
7. **Observabilidade:** logs estruturados com correlation id, métricas (latência, taxa de 409, conflitos de lock) e tracing; alertas para erros 5xx.
8. **Entrega:** Dockerfile, pipeline de CI com testes, lint (ruff) e checagem de tipos (mypy), e testes de carga para validar o comportamento sob concorrência.

---

## 8. Estratégia de testes

| Camada | O que garante | Arquivo |
|---|---|---|
| Unidade (domínio) | Cada regra de negócio, inclusive que **operação recusada não altera o estado** e quantidades inválidas (`0`, negativo, `1.5`, `"3"`, `True`, acima do limite) em todas as operações. | `tests/unit/test_product.py` |
| Propriedades (Hypothesis) | Gera centenas de sequências aleatórias de entradas, saídas, reservas, bloqueios e liberações e verifica as invariantes **após cada passo**. Pega combinações que eu não teria pensado em escrever à mão. | `tests/unit/test_invariants_property.py` |
| Integração (SQLite) | Persistência, produto inexistente, liberação dupla, id de bloqueio usado como reserva, histórico, **rollback** quando uma falha acontece no meio da operação, e CHECK constraints do banco. | `tests/integration/test_service_sqlite.py` |
| Concorrência | 50 reservas simultâneas disputando 100 unidades: exatamente 33 passam e o disponível termina em 1. E 200 operações mistas simultâneas conferidas contra o histórico. | `tests/integration/test_concurrency.py` |
| API | Status HTTP, formato de erro padronizado, validações de entrada e que erro inesperado não vaza detalhes internos. | `tests/api/test_api.py` |

---

## 9. Visão de engenharia: 5 aspectos mais importantes

1. **Consistência garantida em mais de uma camada.** Estoque errado gera venda sem produto ou produto parado. Por isso a regra existe no domínio (valida antes de alterar), na transação (tudo ou nada) e no banco (CHECK constraints). Se uma camada falhar, a outra segura.
2. **Controle de concorrência explícito.** O erro clássico de estoque é duas requisições lerem o mesmo saldo e ambas reservarem. Isso não aparece em testes sequenciais, só sob carga. Por isso o lock é uma decisão consciente e há testes que disparam operações simultâneas.
3. **Regras de negócio isoladas e testáveis.** Com o domínio em Python puro, cada regra pode ser testada em milissegundos e evoluir (nova regra, novo estado) sem mexer em HTTP ou banco. É o que mais facilita mudanças futuras.
4. **Rastreabilidade.** Em estoque, a pergunta "por que o saldo está assim?" sempre aparece. O histórico de movimentações com saldo resultante e as retenções com referência ao pedido respondem isso sem investigação manual.
5. **Contrato de API previsível.** Validação estrita, status HTTP coerentes e erros com `code` estável permitem que os clientes tratem cada situação de forma automática (ex.: 409 de saldo insuficiente → avisar o usuário), em vez de interpretar mensagens de texto.

---

## 10. Uso de Inteligência Artificial

Os principais prompts, onde a IA mais ajudou, a sugestão que modifiquei e os erros
encontrados nas respostas estão em [`docs/USO_DE_IA.md`](docs/USO_DE_IA.md).
