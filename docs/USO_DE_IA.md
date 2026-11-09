# Uso de Inteligência Artificial

**Ferramenta:** Claude (Anthropic), em modo agente. Ele lê arquivos, escreve código e
executa os testes num ambiente isolado.

**Como usei:** tratei a IA como um par de desenvolvimento. Ela gerou a implementação, os
testes e a primeira versão da documentação. Eu defini o escopo e a linguagem, tomei a
decisão de comportamento da liberação, usei a IA para revisar as decisões em formato de
simulação de entrevista e pedi o alinhamento final com os requisitos do case.

## 1. Principais prompts e interações

### 1.1 Ponto de partida

> **Prompt:** *(envio do PDF do case)* "Me ajuda a fazer esse case técnico de uma vaga."

Antes de escrever código, a IA perguntou em qual linguagem eu tinha mais segurança,
porque eu precisaria defender o código depois, e se eu queria o projeto completo, um
passo a passo ou só o planejamento. Escolhi **Python** e **projeto completo com a
explicação de cada decisão**, para usar bem o tempo recomendado de 3 horas.

### 1.2 Implementação

A partir disso, a IA propôs e implementou:
- a modelagem: disponível calculado, cada reserva/bloqueio com id próprio, histórico de movimentações;
- a arquitetura em camadas: domínio puro, serviço, repositórios e API;
- a persistência em SQLite com transação e `BEGIN IMMEDIATE`;
- a suíte de testes.

Os testes foram executados a cada etapa.

Para comprovar que o controle de concorrência era necessário, a IA trocou `BEGIN IMMEDIATE`
por `BEGIN` comum e rodou os testes de concorrência: os dois falharam com
`sqlite3.OperationalError: database is locked`. Com isso, a decisão ficou comprovada por um
experimento, e não só pela teoria.

### 1.3 Mudança de comportamento da liberação (decisão minha)

> **Prompt:** "Quero que, se repetir [a liberação], não dê erro."

Na versão gerada, liberar uma reserva que já tinha sido liberada retornava **409**. Pedi
para mudar e implementamos a nova regra: repetir a liberação retorna **200** com a mesma
reserva, sem devolver as unidades ao estoque de novo e sem criar uma nova movimentação.
Os testes foram atualizados para reservas e bloqueios.

### 1.4 Revisão das decisões em formato de entrevista

> **Prompt:** "Bora pra simulação."

A IA fez perguntas sobre a solução: por que o disponível é calculado, o que acontece com
duas reservas simultâneas, como seria a baixa de reserva. Respondi com o que eu sabia, e
isso mostrou os pontos que eu precisava estudar melhor antes da entrevista.

### 1.5 Alinhamento final

> **Prompt:** "Leia novamente os requisitos do documento e deixe TUDO alinhado."

A IA reorganizou o README na mesma ordem dos itens pedidos no case e acrescentou um mapa
que liga cada entregável e cada funcionalidade ao local onde está na solução.

## 2. Análise

### Onde a IA mais ajudou

- **Testes:** a suíte de 123 testes seria a parte mais demorada de escrever à mão em 3
  horas. Ela inclui teste baseado em propriedades (Hypothesis), que gera sequências
  aleatórias de operações, e testes de concorrência com threads.
- **Casos de borda que eu não teria listado sozinha:** quantidade enviada como `"3"`, `1.5`
  ou `true` (em Python, `True` é um `int`), campo com nome errado, id de bloqueio usado na
  rota de reserva e falha no meio de uma transação.
- **Documentação das decisões**, com as alternativas consideradas em cada uma.

### Sugestão que modifiquei

A IA tinha feito a liberação dupla retornar **409 (conflito)**, com o argumento de que isso
deixa visível um erro de quem chama a API. Preferi que **repetir não dê erro**. Se o
processo que chama a API rodar de novo (por exemplo, depois de uma falha de rede), ele
deve funcionar, e não ser travado por uma regra que só bloqueia algo que já aconteceu. A
proteção que importa continua garantida: as unidades não voltam ao estoque duas vezes.

### Erros e limitações encontrados nas respostas da IA

1. **Vazamento de conexão com o banco.** A IA usou `with sqlite3.connect(...) as conn:`
   assumindo que isso fecharia a conexão. Em Python, esse `with` só faz commit/rollback e
   **não fecha** a conexão. Todos os testes passavam, mas apareceram dezenas de
   `ResourceWarning: unclosed database`. A correção foi usar `contextlib.closing`.
   Lição: teste passando não basta, os avisos também precisam ser lidos.
2. **API depreciada.** A primeira versão usou `status.HTTP_422_UNPROCESSABLE_ENTITY`, que
   gera aviso de depreciação na versão atual do Starlette. Foi trocada pelo código 422.
3. **Mensagens de erro genéricas.** As primeiras mensagens diziam "não encontrado(a)", o
   que soava artificial em português. Foram ajustadas para concordar com "Reserva" e
   "Bloqueio".

### Como o resultado foi validado

- Todos os testes executados a cada mudança, com cobertura de 99%.
- Experimento quebrando o controle de concorrência de propósito para confirmar que o teste detecta o problema.
- Execução da API de ponta a ponta numa instalação limpa, com os exemplos do README e o Swagger (`/docs`).
- Revisão das decisões por meio da simulação de entrevista.
