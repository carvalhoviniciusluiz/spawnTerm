# Prompt de validação de COOPERAÇÃO + comportamento agêntico do it2agent (cole no Claude)

> **O que é isto:** o terceiro prompt da família, irmão de `it2agent/tests/VALIDATION_PROMPT.md`
> (smoke por-função) e `it2agent/tests/ADVANCED_VALIDATION_PROMPT.md` (os AC1–AC8 da #73). **Este
> valida o trabalho de REPOSICIONAMENTO + COOPERAÇÃO** (issue #1): cooperar com as superfícies
> NATIVAS do iTerm2 em vez de duplicá-las (status nativo OSC 21337 / Cockpit), o bridge de
> agent-teams do Claude Code (o **moat** durável), isolamento de runtime (portas/serviços) e a
> orquestração agêntica ponta-a-ponta via broker + MCP. É a fase de **aceite ao vivo** da cooperação.
>
> **Como usar:** abra o Claude (Claude Code) numa aba deste iTerm2 (o build de **dev, 3.7.dev**) e
> cole TUDO dentro do bloco `--- PROMPT ---`. (Ou rode:
> `claude "$(sed -n '/^--- PROMPT ---$/,/^--- FIM ---$/p' it2agent/tests/COOPERATION_VALIDATION_PROMPT.md)"`.)
>
> **Legenda de verificação:** 🤖 o agente automatiza 100% · 👁 exige olho do operador (o agente
> não vê a GUI) · 🔴 exige infra viva (iTerm2 3.7.dev / tmux / um team real do Claude Code). Itens
> 🔴/👁 o agente **não deve fingir**: descreve a ação do operador e marca `pendente-ao-vivo`.

--- PROMPT ---

Você está rodando dentro do **it2agent**, um fork pessoal do iTerm2 para orquestração de agentes de
IA. O foco da issue #1 é **cooperação, não duplicação**: em vez de reinventar o que o iTerm2 3.7 já
faz nativamente (status de aba/Cockpit via OSC 21337, cc-status do Claude Code), o it2agent
**alimenta** essas superfícies e adiciona o que o nativo NÃO tem — mensageria durável entre abas,
espelho durável de agent-teams (o moat), isolamento de runtime por agente e uma superfície MCP. Sua
tarefa é o **aceite ao vivo da cooperação**: validar os 9 critérios (AC1–AC9). Responda em português
com um relatório honesto ✅/❌ por AC, citando **evidência real** (bytes/JSON/lsof/saída) e a
confirmação visual onde marcado 👁.

Regras:
- **Toda capacidade é feature-flag, default OFF.** Habilite o que precisar e **restaure no fim**.
  Use config isolado para não sujar o meu.
- **Não invente resultado.** Se um comando falhar, cole o erro real e marque ❌. Para passos 🔴/👁
  que exigem iTerm2/tmux/um team real ou olho humano, **não simule** — descreva a ação do operador e
  marque `pendente-ao-vivo`.
- **NUNCA** instale nada no meu `~/.claude` real. Os testes usam repositórios git descartáveis e o
  override `IT2AGENT_CLAUDE_SETTINGS`. O bridge só toca um `settings.local.json` de repo temporário.
- Cada AC tem uma **tag** (🤖/👁/🔴), um **Passo** (comandos exatos) e um **Esperado** (condição de
  aprovação concreta: bytes/JSON/visual).

## 0. Setup (rode primeiro)
```sh
export REPO="/Users/vinicius.carvalho/Developments/Workspaces/carvalhotech/spawnterm"
export ST="$REPO/it2agent"
export PATH="$ST:$ST/flags:$ST/emit:$ST/spawn:$ST/broker:$ST/review:$ST/janitor:$ST/cost:$ST/inbox:$ST/mcp:$ST/tmux:$ST/team:$ST/tests:$PATH"
# Config isolado (não toca no meu config real):
export IT2AGENT_CONFIG="$(mktemp -d)/config.toml"
# Broker durável: coloque db + socket sob um dir CURTO em /tmp — o unix socket tem
# limite de ~104 bytes no path, e o mktemp do macOS (/var/folders/…) estoura fácil.
export IT2DIR="/tmp/it2c.$$"; mkdir -p "$IT2DIR"
export IT2AGENT_BROKER_DB="$IT2DIR/broker.db"
export IT2AGENT_BROKER_SOCK="$IT2DIR/broker.sock"
echo "config=$IT2AGENT_CONFIG"; echo "db=$IT2AGENT_BROKER_DB"; echo "sock=$IT2AGENT_BROKER_SOCK"
echo "sock_len=${#IT2AGENT_BROKER_SOCK} (precisa ser < ~100)"
```

## 0.5. Preflight (rode antes dos ACs 🔴) 🤖
**Por que isto existe:** os ACs de cooperação nativa e de team dependem de QUATRO coisas que "quase
sempre" alguém esquece. Este preflight checa as quatro e diz, honestamente, o que fica
`pendente-ao-vivo` se faltar algo. **Não é fatal** — as partes 🤖 (bytes, mecânica de broker/MCP,
no-ops de flag, espelho do bridge headless) rodam mesmo assim.

**Passo:**
```sh
# (a) é o build de DEV 3.7? (o status NATIVO/Cockpit e a GUI só existem no 3.7.dev)
VER="${TERM_PROGRAM_VERSION:-?}"
case "$VER" in 3.7*) echo "build=OK ($VER)";; *) echo "build=NÃO-3.7 ($VER) — AC1-nativo/AC9-GUI pendentes";; esac
# (b) módulo iterm2 instalado?
python3 -c 'import iterm2' 2>/dev/null && echo "modulo=OK" || echo "modulo=FALTA (pip3 install iterm2)"
# (c) o SERVIDOR da Python API está ligado?
APISRV="$(defaults read com.googlecode.iterm2 EnableAPIServer 2>/dev/null || echo 0)"
echo "EnableAPIServer=$APISRV"
# (d) o hook NATIVO do Claude Code (cc-status) está instalado no MEU ~/.claude/settings.json?
#     (é ele que faz o Claude Code reportar Working/Idle pro status nativo do iTerm2 3.7)
if grep -q "cc-status" "$HOME/.claude/settings.json" 2>/dev/null; then echo "cc-status=OK"; else echo "cc-status=FALTA"; fi

if [ "$APISRV" = "1" ] && python3 -c 'import iterm2' 2>/dev/null && case "$VER" in 3.7*) true;; *) false;; esac; then
  echo "✅ PREFLIGHT: dev 3.7 + Python API prontos — AC1-spawn/AC2-abas/AC6-team-live/AC7-launched validáveis ao vivo."
else
  echo "⚠️  PREFLIGHT: algo falta acima. As partes 🤖 rodam; as 🔴 dependentes ficam pendente-ao-vivo:"
  echo "    build≠3.7 → status NATIVO/Cockpit (AC1 👁) e a GUI (AC9 👁) não podem ser conferidos."
  echo "    API OFF   → Settings → General → Magic → Enable Python API (e aprove o diálogo). Sem isso:"
  echo "                AC1 (spawn acende status), AC2 (abas da frota), AC7 (launched=true) ficam pendentes."
  echo "    cc-status FALTA → o Claude Code não auto-reporta; o it2agent-emit ccstatus MANUAL ainda funciona."
fi
```
**Esperado:** idealmente as quatro linhas `build=OK / modulo=OK / EnableAPIServer=1 / cc-status=OK` e
o `✅ PREFLIGHT`. Se vier `⚠️`, **não finja** os ACs 🔴/👁 dependentes — marque `pendente-ao-vivo`.
> **OPERADOR:** se viu `⚠️`, ligue a Python API (Settings → General → Magic → Enable Python API,
> aprove o diálogo). Se `cc-status=FALTA` e você quiser o auto-report do Claude Code, instale o hook
> nativo do iTerm2 3.7 (menu do iTerm2). Rode o preflight de novo. 🔴

---

## AC1 — Cooperação com o status NATIVO (OSC 21337 → Cockpit) 🤖👁🔴
O agente NÃO pinta seu próprio board paralelo (isso é o legado `status_board`): ele **fala o canal
nativo** OSC 21337, e o iTerm2 3.7 mostra no Session Status / status da aba / Cockpit. A flag é
`agent.native_status`.

**Passo (🤖, prova os bytes exatos):**
```sh
it2agent-flag enable agent.native_status
IT2AGENT_FORCE=1 it2agent-emit ccstatus busy --detail "build #42" | od -c | head -3
IT2AGENT_FORCE=1 it2agent-emit ccstatus clear | od -c | head -2
```
**Esperado:** a sequência é **literal** (não base64), com `\;`/`\\` escapados e cor Okabe-Ito. Para
`busy --detail x` o byte-a-byte é exatamente (confirmado headless):
```
0000000  033   ]   2   1   3   3   7   ;   s   t   a   t   u   s   =   B
0000020    u   s   y   ;   i   n   d   i   c   a   t   o   r   =   #   0
0000040    0   7   2   B   2   ;   d   e   t   a   i   l   =   x  \a
```
ou seja `\e]21337;status=Busy;indicator=#0072B2;detail=x\a`. `clear` emite `status=;indicator=;detail=`
(esvazia cada chave). 🤖 essa parte é 100% automatizável.
> **OPERADOR: olhe** (build 3.7.dev) o **Session Status / status da aba / Cockpit** desta sessão
> mudar para “Busy” com o detalhe. Rode `it2agent-emit ccstatus clear` e confirme que some. O agente
> NÃO enxerga o Cockpit. 🔴👁

**Spawn wiring (🔴):** uma aba spawnada com a flag ON acende o status nativo sozinha. Com a Python API
ligada: `python3 "$ST/daemon/it2agent_daemon.py" spawn --no-gate --role backend --id ac1-native --task x --status busy --dir "$REPO" -- $SHELL` e o operador confere o status nativo na aba nova. Sem API,
marque **AC1-spawn pendente-ao-vivo**.

## AC2 — Frota isolada: N agentes, portas DISTINTAS 🤖👁
Três agentes no MESMO repo, cada um com sua porta arrendada (sem colisão TOCTOU), e a frota listável
por `ls` / `status --json` (para janitor/daemon/MCP). Há um driver que cria a frota **concorrente**
num repo git descartável e assere tudo:

**Passo (🤖):**
```sh
python3 "$ST/tests/coop_fleet_ports.py"
```
O driver cria 3 worktrees em paralelo (exercita o mutex de alocação), assere 3 portas distintas, e
valida que `status --json` traz as chaves estáveis (`branch/worktree/port/ports/canonical/changes/
clean/stale/stale_reason`) e que `ls` renderiza a tabela.

**Esperado:** `COOP-FLEET PASS: 3 agents in one repo got distinct leased ports…`. Exemplo real
(headless): `leased ports: [41226, 41619, 41785] -> distinct=True`, `status --json count=3`. ❌ se
duas portas colidirem ou o JSON não listar a frota.
> **OPERADOR (fluxo ao vivo, opcional 👁):** se você spawnar a frota de verdade
> (`it2agent-worktree create` + abrir abas), confirme que as 3 abas estão nos worktrees certos
> (`it2agent/<role>-<id>-<hash>`), uma por agente. 🔴

## AC3 — Multi-porta nomeada + porta canônica (singleton) 🤖
`--ports web,db,cache` arrenda uma porta POR NOME (a primeira também é a `IT2AGENT_PORT` nua,
back-compat). Com `agent.canonical_port` ON, o agente “focado” também segura a porta normal do
projeto (ex.: 3000) — **exatamente um** agente por repo por nome; `--release` devolve.

**Passo (🤖, multi-porta — pure plan/env, sem side effect):**
```sh
IT2AGENT_FORCE=1 it2agent-worktree env --repo "$REPO" --id ac3 --role backend --ports web,db,cache
```
**Esperado:** três exports `IT2AGENT_PORT_WEB/_DB/_CACHE` (a primeira também vira `IT2AGENT_PORT`).
Exemplo real:
```
export IT2AGENT_PORT=41298
export IT2AGENT_PORT_WEB=41298
export IT2AGENT_PORT_DB=41080
export IT2AGENT_PORT_CACHE=41951
```

**Passo (🤖, canônica singleton + release):**
```sh
python3 "$ST/tests/coop_canonical_singleton.py"
```
O driver (num repo git descartável): A `create` vira holder (`canonical_port_web=3000`,
`canonical_port_db=3001`) → B `create` NÃO recebe canônica (singleton, worktree de A vivo) → A
`canonical --release` → B agora consegue (handover só após release).

**Esperado:** `COOP-CANONICAL PASS: canonical port is a per-repo singleton…`. ❌ se B pegar a canônica
enquanto A a segura, ou se o release não liberar.

## AC4 — Isolamento de serviço (ENV-ONLY, por-flag) 🤖
`--isolate docker,db` **só exporta nomes** que o tooling do projeto já lê — nunca roda docker, nunca
conecta no Postgres. Cada modo tem sua própria flag (`agent.isolate_docker`/`agent.isolate_db`), e
`namespace` é rejeitado no macOS.

**Passo (🤖):**
```sh
python3 "$ST/tests/coop_isolate_exports.py"
```
O driver roda `create --dry-run` (zero side effect): flags ON → exporta `COMPOSE_PROJECT_NAME` +
`IT2AGENT_DB_SCHEMA` + `PGOPTIONS`; flags OFF → nada; só docker ON → só `COMPOSE_PROJECT_NAME`
(gating por-flag); `--isolate namespace` → erro claro (exit 2).

**Esperado:** `COOP-ISOLATE PASS: docker/db isolation exports are env-only, per-flag gated, inert
when OFF, and namespace is rejected on macOS`. Exemplo real dos exports ON:
`env_COMPOSE_PROJECT_NAME=backend_809c96`, `env_IT2AGENT_DB_SCHEMA=backend_809c96`,
`env_PGOPTIONS=-c search_path=backend_809c96`. ❌ se vazar export com flag OFF.

## AC5 — Mensageria agêntica entre abas + idempotência 🔴🤖
Agente A (esta aba) → agente B (outra aba real) pelo broker durável, com ack **exactly-once**; e
**idempotência (#95):** reenviar com a mesma `key` retorna `dedup:true`, sem duplicar. Reusa o shim
`e2e_agent_shim.py`. A mecânica é 100% automatizável; o único bit 🔴 é a aba B abrir de verdade.

**Passo (🤖, idempotência — prova agora, sem GUI):**
```sh
it2agent-flag enable agent.broker
python3 "$ST/broker/it2agent_broker.py" serve --no-gate &   # broker durável (sqlite)
sleep 1
python3 - "$IT2AGENT_BROKER_SOCK" <<'PY'
import socket,sys,json
sock=sys.argv[1]
def rpc(o):
    s=socket.socket(socket.AF_UNIX); s.connect(sock)
    s.sendall((json.dumps(o)+"\n").encode()); r=s.makefile().readline(); s.close(); return json.loads(r)
print("send#1 key=k1:", rpc({"op":"send","to":"z","from":"a","body":"hi","key":"k1"}))
print("send#2 key=k1:", rpc({"op":"send","to":"z","from":"a","body":"hi","key":"k1"}))
print("poll z count:", len((rpc({"op":"poll","agent":"z"}).get("messages") or [])))
PY
```
**Esperado (real, headless):** `send#1 key=k1: {'id': 1, 'ok': True}` → `send#2 key=k1: {'dedup':
True, 'id': 1, 'ok': True}` → `poll z count: 1`. O reenvio com a mesma key **não** cria segunda
mensagem.

**Passo (🔴, cross-tab de verdade — abre uma aba B):**
```sh
RESULT="$IT2DIR/received.log"
( cd "$REPO" && IT2AGENT_FORCE=1 it2agent-spawn --role backend --id tabB -- \
    python3 "$ST/tests/e2e_agent_shim.py" --sock "$IT2AGENT_BROKER_SOCK" --result "$RESULT" --me b --peer a --timeout 30 )
sleep 3
python3 - "$IT2AGENT_BROKER_SOCK" <<'PY'
import socket,sys,json
sock=sys.argv[1]
def rpc(o):
    s=socket.socket(socket.AF_UNIX); s.connect(sock)
    s.sendall((json.dumps(o)+"\n").encode()); r=s.makefile().readline(); s.close(); return json.loads(r)
print("A send:", rpc({"op":"send","to":"b","from":"a","body":"ping cross-tab"}))
PY
sleep 3
echo "--- ABA B recebeu: ---"; cat "$RESULT"
```
**Esperado:** `$RESULT` mostra `recv … body='ping cross-tab'` → `acked up_to=…` → `done`. O nativo do
iTerm2 não tem mensageria durável A↔B com ack.
> **OPERADOR: olhe** que a aba B abriu com identidade (role backend). 🔴

## AC6 — Team Bridge: o MOAT (headline agêntico) 🔴🤖
Claude Code agent-teams guardam a lista de tarefas + coordenação sob `~/.claude/teams/{team}/` e
**apagam ao fim da sessão** (“coordination state is lost” quando o lead morre). O bridge é um **hook
observador** que espelha esse estado no broker durável — e sobrevive à morte do lead. Isto é o moat.

**Passo (🤖, prova mecânica + durabilidade + segurança + install, tudo headless):**
```sh
python3 "$ST/tests/coop_team_bridge_mirror.py"
```
O driver: sobe broker → dispara os eventos EXATOS que o Claude Code entrega (`TeammateIdle` /
`TaskCreated` / `TaskCompleted`) no stdin do hook → assere **observer-safe** (SEMPRE exit 0, ZERO
stdout, inclusive com stdin vazio/corrompido e evento desconhecido — pois exit 2 BLOQUEARIA o team) →
verifica o **espelho** (register + handoff pending→completed + send pro lead) → prova **idempotência**
(re-disparar TaskCompleted não duplica a notificação do lead) → **mata o broker e reinicia no MESMO
db**: o registro do team + o ciclo de vida da tarefa **continuam lá** → e faz **install/uninstall** dos
3 hooks num `settings.local.json` de um repo git DESCARTÁVEL (+ entrada no `.gitignore`), removendo só
as entradas dele.

**Esperado:** `COOP-TEAM-BRIDGE PASS: … mirrors register+handoff+send durably, dedups the completion
notification, and the mirror SURVIVES broker death — the moat`. Trechos reais:
`broker task lifecycle for team:session-coopsess -> ['pending', 'completed']`;
`after restart: lifecycle=['pending', 'completed', 'completed'] teammate_present=True` (o log de
handoff é append-only, então o re-disparo idempotente acrescenta outro `completed` — o **send** é que
deduplica; ambos corretos); `settings.local.json hooks: ['TaskCreated','TaskCompleted','TeammateIdle']`.

**Passo (🔴, um TEAM REAL do Claude Code — o único bit que precisa de gente):**
1. Crie um repo git **descartável** (NÃO use um repo meu) e entre nele:
   `D="$(mktemp -d)/proj"; mkdir -p "$D"; ( cd "$D" && git init -q && git commit -q --allow-empty -m init )`
2. Instale o hook **project-scoped** nesse repo (escreve no `.claude/settings.local.json` gitignored
   DELE, nunca no meu ~/.claude): `( cd "$D" && it2agent-team-hook install --scope project )`
3. Habilite o experimento e (opcional) tire o kill-switch:
   `export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1; it2agent-flag enable agent.team_bridge`
4. Dentro de `$D`, rode um **Claude Code com um team de 2 teammates** que cria e completa uma tarefa
   e fica idle (ação do OPERADOR — não dá pra dirigir um team real headless de forma confiável).
5. Consulte o espelho pelas tools MCP (sem o Claude Code precisar estar vivo):
   `read_messages`/`team_tasks` — veja o AC7 para como dirigir o MCP; o `team` é o `session_id` do
   team ou a chave derivada `team:session-<sid8>`.
6. **MATE a sessão lead** e re-consulte `team_tasks`: o espelho + o ciclo de vida da tarefa **têm que
   continuar intactos** (durabilidade = o moat).
> **OPERADOR:** os passos 1–4 e 6 são seus (criar o repo, rodar o team real, matar o lead). O agente
> automatiza a asserção do estado do broker depois. Sem um team real, marque a parte ao-vivo do AC6
> como **pendente-ao-vivo** — a mecânica/durabilidade/segurança já ficaram provadas pelo driver. 🔴

## AC7 — Orquestração dirigida por MCP (as 9 tools) 🔴🤖
Um cliente MCP dirige `spawn → assign → handoff → send_message → team_tasks → read_messages` e cada
tool tem **efeito durável real** (registry/handoff/mailbox), não só JSON. Há um driver que dirige o
MCP por stdin JSON-RPC e depois **checa o broker fora de banda**:

**Passo (🤖, prova a mecânica agora):**
```sh
python3 "$ST/tests/coop_mcp_orchestrate.py"
```
O driver sobe um broker, aponta o MCP pra ele, envia `initialize` + `tools/list` + os `tools/call`, e
então consulta o broker direto: `query` (spawn/assign registrados), `handoff_history` (ciclo de vida
da tarefa), `poll` (mensagem entregue). Assere que `team_tasks` agrupa o `task:` em pending→completed
e que `read_messages` é **não-destrutivo** (não dá ack — um `poll` posterior ainda vê a mensagem).

**Esperado:** `COOP-MCP PASS: the 9-tool MCP surface drives … with real durable side effects…`.
`tools/list` retorna exatamente as **9** tools (`spawn, assign, handoff, send_message, status,
list_agents, team_tasks, read_messages, help`); o registry contém `coop-spawned`/`coop-assigned`;
`team_tasks lifecycle for task:T1 -> ['pending','completed'] status= completed`; a mensagem sobrevive
ao `read_messages`.
> **Nota 🔴:** headless o `spawn` não abre a aba (`launch.launched=False`, `No module named 'iterm2'`
> ou API OFF) — só o efeito de registro é provado (o driver assere isso). No Claude Code de verdade,
> conecte o MCP e chame `spawn` com um `id`: a **aba deve abrir** e `launched=true`. Marque a abertura
> da aba via MCP como **pendente-ao-vivo** se a API estiver OFF. 🔴

## AC8 — Feature-flags default-OFF viram no-op de verdade 🤖
Cada flag NOVA (`native_status`, `team_bridge`, `canonical_port`, `isolate_docker`, `isolate_db`) OFF
⇒ capacidade inerte (zero bytes/exports/writes, exit 0); ON ⇒ funciona. Há um checker:

**Passo (🤖):**
```sh
python3 "$ST/tests/coop_flag_noop.py"
```
O checker usa `IT2AGENT_CONFIG` isolado e, por flag: OFF → afirma inerte; ON → afirma ativo. Cobre o
caso especial do `team_bridge` (gate INVERTIDO/kill-switch): o evento SEMPRE sai 0 sem stdout, mas um
`= false` explícito suprime o write durável, enquanto ausente/true deixa passar (provado contra um
broker vivo).

**Esperado:** `COOP-FLAG PASS: … inert when OFF and restore when ON; team_bridge always exits 0 with
no stdout and only writes when not kill-switched`. Linhas reais: `[agent.native_status] OFF … bytes=0
-> inert OK | ON … bytes=47 has21337=True -> active OK`; `[agent.team_bridge] EXPLICIT-false: …
broker_writes=0 -> inert OK`. ❌ se qualquer flag OFF emitir byte/export/write.

## AC9 — GUI: painel “AI Agents” 👁
O painel de Settings mostra **nome + descrição** por capacidade, e o checkbox **Team Bridge** é
project-local: mostra o caminho resolvido `<project>/.claude/settings.local.json` e fica
**desabilitado com orientação** quando não há projeto git em foco.

**Passo:** este é um AC 100% visual (o agente não vê a GUI). Referência do que conferir (fonte:
`sources/Settings/iTermAgentCapabilities.m`): as capacidades listadas incluem `native_status`
(“Native Tab Status”), `team_bridge` (“Team Bridge”), `canonical_port` (“Canonical Port”),
`isolate_docker` (“Docker Isolation”), `isolate_db` (“DB Isolation”), cada uma com sua descrição.
> **OPERADOR: abra** Settings → (painel **AI Agents**) e confirme: (a) cada capacidade tem nome +
> descrição legível; (b) o **Team Bridge** mostra o `settings.local.json` do projeto em foco e o
> instala/remove ali; (c) com uma aba cujo cwd **não** é um repo git, o Team Bridge fica desabilitado
> com uma mensagem explicando que precisa de um projeto git. Sem o build 3.7.dev, **pendente-ao-vivo**. 🔴👁

---

## AC10 — Descoberta: o Claude sabe as features ao abrir (autobrief + guia gerado) 🤖👁
O terminal se torna auto-descritível: um guia **gerado do schema** (sempre atual) e um hook
`SessionStart` que injeta um resumo no contexto do Claude.

**Passo (headless 🤖):**
```sh
it2agent brief                                   # resumo das capacidades ativas + como usar
it2agent guide --check ; echo "drift exit=$?"    # 0 = guia em sincronia com o schema/tools
# hook autobrief: OFF não injeta nada; ON injeta additionalContext (SessionStart)
printf '{}' | it2agent-autobrief-hook session-start   # flag OFF → sem stdout, exit 0
it2agent-flag enable agent.autobrief
printf '{}' | IT2AGENT_FORCE=1 it2agent-autobrief-hook session-start | python3 -m json.tool | head -5
```
✅ se `brief` lista as capacidades ativas; `guide --check` sai 0 (sem drift); o hook é **observador
seguro** (OFF→nada/exit 0; ON→JSON `hookSpecificOutput.additionalContext` com o brief).
**Instalação por projeto (👁, num repo TEMPORÁRIO — não no seu):** `it2agent-autobrief-hook install
--scope project` grava o hook no `<repo>/.claude/settings.local.json` (gitignored). Um Claude aberto
nesse projeto passa a **nascer sabendo** das features. **OPERADOR:** confirme, num projeto de teste,
que uma sessão nova do Claude recebe o brief. (Registro MCP é passo manual documentado — `claude mcp
add`, ver `it2agent/mcp/README.md`.)

## AC11 — Coastfile por projeto + `--assign` 🤖
Declarar o isolamento **uma vez** por projeto e não repetir opções a cada spawn.

**Passo (num repo git TEMPORÁRIO):**
```sh
mkdir -p "$TMPREPO/.it2agent" && cat > "$TMPREPO/.it2agent/isolation.toml" <<'TOML'
ports = ["web","db"]
canonical = true
isolate = ["docker","db"]
assign = "restart"
TOML
( cd "$TMPREPO" && IT2AGENT_FORCE=1 it2agent-worktree create --repo "$TMPREPO" --id demo --role backend --dry-run )   # aplica o arquivo (canonical/isolate/assign aparecem no create --dry-run, não no plan que é allocation-only)
( cd "$TMPREPO" && IT2AGENT_FORCE=1 it2agent-worktree create --repo "$TMPREPO" --id demo --role backend --ports api --assign none --dry-run )  # CLI sobrescreve o arquivo
```
✅ se o 1º plano reflete ports web/db + canônica + isolate docker,db + assign=restart; e o 2º mostra
que os flags de CLI **sobrescrevem** o arquivo (só `api`, sem assign).

## AC12 — Leitura inbound: nativo → registry (opcional) 🤖🔴
O daemon reflete o que o iTerm2 nativo sabe de cada sessão no **nosso registry** (mão única, read-only
do lado nativo).
**Passo (headless 🤖):** o mapeamento é puro e testável — `python3 -m unittest
it2agent.daemon.tests.test_inbound` (sessão→registro; user-vars `agent_*`, cc-status→status; API-off →
no-op). **Ao vivo 🔴:** com o daemon + Python API ligados, sessões nativas aparecem no `registry`
(consultável via broker/MCP `list_agents`). ✅ se os testes puros passam e (ao vivo) o registry reflete
as sessões nativas.

---

## AC13 — Instalador de wrappers (descoberta reproduzível) 🤖
Os CLIs do it2agent vivem no repo; `it2agent install` cria wrappers no PATH pra a GUI e o usuário
os acharem de qualquer lugar (fix do gap que deixava o Team Bridge desabilitado por falta de wrapper).

**Passo (headless, num bindir TEMPORÁRIO — não mexa no seu ~/.local/bin):**
```sh
BIN="$(mktemp -d)/bin"
it2agent install --dir "$BIN" | tail -3
ls "$BIN" | wc -l                                  # ~17 wrappers
IT2AGENT_CONFIG="$(mktemp -d)/c.toml" "$BIN/it2agent-flag" list | head -1   # wrapper funciona de fato
it2agent install --dir "$BIN" >/dev/null && echo "idempotente ok"           # 2ª run não duplica/erra
touch "$BIN/alheio"; it2agent uninstall --dir "$BIN" >/dev/null; ls "$BIN"  # remove só os nossos; 'alheio' sobrevive
```
✅ se: `install` cria um wrapper por CLI (executável, apontando pro alvo certo no repo) e o
`it2agent-flag list` via wrapper funciona; 2ª `install` é idempotente; `uninstall` remove **só** os
nossos (o arquivo `alheio` permanece). Enumeração é **dinâmica** (CLIs futuros entram sozinhos).

---

## AC14 — Harness de fumaça AO VIVO (1 comando — a camada iTerm-API) 🔴🤖
Atalho pra revalidar a camada onde os bugs live-only se escondem (spawn+cwd, tmux -CC, MCP launched,
ccstatus) sem rodar tudo na mão. **Rode primeiro** — é o smoke mais rápido:
```sh
python3 "$ST/tests/live_smoke.py"            # 4 superfícies: PASS/FAIL/SKIP + evidência
python3 "$ST/tests/live_smoke.py" --json     # saída machine-readable (futuro CI)
python3 "$ST/tests/live_smoke.py" --only ccstatus   # escopar uma superfície
```
✅ se, com a **Python API ligada** no 3.7.dev: **spawn** (cwd real via `lsof` == repo + identidade),
**tmux** (superfícies 2/4/5 PASS), **mcp** (`launched=true`), **ccstatus** (bytes OSC 21337) → todas
**PASS**. Sem a API, as 3 superfícies vivas dão **SKIP honesto** (exit≠0, nada fingido) e o `ccstatus`
ainda **PASS**. O harness **limpa tudo** (mata broker/tmux, remove repos/worktrees temporários, fecha
só as abas que abriu). É o item "harness de fumaça ao vivo" do go-live.

---

## Relatório final
Devolva esta tabela, preenchida com **evidência real**:

| AC | Tag | Resultado | Evidência (bytes/JSON/saída) | Pendente? |
|----|-----|-----------|------------------------------|-----------|
| AC1 status nativo (OSC 21337)     | 🤖👁🔴 | | | |
| AC2 frota isolada (portas)        | 🤖👁   | | | |
| AC3 multi-porta + canônica        | 🤖     | | | |
| AC4 isolamento de serviço (env)   | 🤖     | | | |
| AC5 mensageria X-tab + idempot.   | 🔴🤖   | | | |
| AC6 Team Bridge (o moat)          | 🔴🤖   | | | |
| AC7 orquestração MCP (9 tools)    | 🔴🤖   | | | |
| AC8 flags no-op                   | 🤖     | | | |
| AC9 GUI painel AI Agents          | 👁🔴   | | | |
| AC10 descoberta (autobrief/guia)  | 🤖👁   | | | |
| AC11 Coastfile + --assign         | 🤖     | | | |
| AC12 inbound nativo→registry      | 🤖🔴   | | | |
| AC13 instalador de wrappers       | 🤖     | | | |
| AC14 harness de fumaça ao vivo    | 🔴🤖   | | | |

Depois da tabela:
1. **O que passou 🤖 agora** (AC2/AC3/AC4/AC5-idempotência/AC6-mecânica+durabilidade/AC7-mecânica/AC8
   e os bytes do AC1) vs. **o que ficou pendente-ao-vivo** (AC1-Cockpit/spawn, AC2-abas, AC5-aba-B,
   AC6-team-real+matar-lead, AC7-launched, AC9-GUI) — seja honesto, não finja GUI/team.
2. **Cleanup (obrigatório):**
   - Restaure meus flags (o `IT2AGENT_CONFIG` era temporário, então nada tocou no meu config real).
     Confirme com `it2agent-flag list` (tudo `off`).
   - **Mate os brokers** que subiu: `jobs` e `kill %1 %2 …` (ou pelo PID).
   - **Remova os worktrees/branches descartáveis**: os drivers usam repos temporários próprios e se
     limpam; se você criou worktrees no MEU repo no fluxo ao vivo, rode
     `it2agent-worktree cleanup --repo "$REPO" --id <id>` para cada um, e confirme com
     `it2agent-worktree ls --repo "$REPO"` (sem sobras `it2agent/…`).
   - **Feche as abas** spawnadas (AC1-spawn, AC5-aba-B) e o repo/team temporário do AC6 (o hook estava
     só no `settings.local.json` DELE; o repo é descartável). **Diga explicitamente qual aba fechar**
     (ex.: “feche a aba `tabB` do AC5 e a aba `ac1-native` do AC1”).
   - Se instalou o hook em algum lugar via `--scope project`, rode `it2agent-team-hook uninstall
     --scope project` no MESMO repo. **Nunca** deixe nada no meu `~/.claude`.

--- FIM ---
