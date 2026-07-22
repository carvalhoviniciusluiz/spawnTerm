# Prompt de validação AVANÇADA do it2agent — dogfooding end-to-end (cole no Claude)

> **O que é isto:** o irmão avançado de `it2agent/tests/VALIDATION_PROMPT.md`. Aquele valida
> cada função isolada (smoke, seções 1–11); **este valida os fluxos reais do dia a dia** — os 8
> critérios de aceite (AC1–AC8) da issue #73 — rodando contra um **iTerm2 + tmux de verdade**
> (o build de dev). É a fase de **aceite ao vivo**.
>
> **Como usar:** abra o Claude (Claude Code) numa aba deste iTerm2 e cole TUDO dentro do bloco
> `--- PROMPT ---`. (Ou rode:
> `claude "$(sed -n '/^--- PROMPT ---$/,/^--- FIM ---$/p' it2agent/tests/ADVANCED_VALIDATION_PROMPT.md)"`.)
>
> **Legenda de verificação:** 🤖 o agente automatiza 100% · 👁 exige olho do operador (o agente
> não vê a GUI) · 🔴 exige infra viva (iTerm2/tmux rodando). Itens 🔴/👁 o agente **não deve
> fingir**: descreve a ação do operador e marca `pendente-ao-vivo` no relatório.

--- PROMPT ---

Você está rodando dentro do **it2agent**, um fork pessoal do iTerm2 para orquestração de agentes
de IA (escape codes do iTerm2 + broker externo durável com ack + Python API + tmux -CC). O smoke
por-função já passou. Sua tarefa agora é o **dogfooding end-to-end**: validar os 8 critérios de
aceite (AC1–AC8) da issue #73 — os fluxos que realmente usamos. Responda em português com um
relatório honesto ✅/❌ por AC, citando a evidência real (bytes/JSON/saída) e confirmação visual
onde marcado 👁.

Regras:
- **Toda capacidade é feature-flag, default OFF.** Habilite o que precisar e **restaure no fim**.
  Use config isolado para não sujar o meu.
- **Não invente resultado.** Se um comando falhar, cole o erro real e marque ❌. Para passos 🔴/👁
  que exigem iTerm2/tmux ou olho humano, **não simule** — descreva a ação do operador e marque
  `pendente-ao-vivo`.
- Cada AC tem uma **tag** (🤖/👁/🔴), um **Passo** (comandos exatos) e um **Esperado** (condição de
  aprovação concreta: bytes/JSON/visual).

## 0. Setup (rode primeiro)
```sh
export REPO="/Users/vinicius.carvalho/Developments/Workspaces/carvalhotech/spawnterm"
export ST="$REPO/it2agent"
export PATH="$ST:$ST/flags:$ST/emit:$ST/spawn:$ST/broker:$ST/review:$ST/janitor:$ST/cost:$ST/inbox:$ST/mcp:$ST/tmux:$ST/tests:$PATH"
export IT2AGENT_CONFIG="$(mktemp -d)/config.toml"
# Broker durável compartilhado pelos ACs de mensageria/handoff/MCP:
export IT2AGENT_BROKER_DB="$(mktemp -d)/broker.db"
export IT2AGENT_BROKER_SOCK="$(mktemp -d)/broker.sock"
echo "config=$IT2AGENT_CONFIG"; echo "db=$IT2AGENT_BROKER_DB"; echo "sock=$IT2AGENT_BROKER_SOCK"
```

---

## AC1 — Spawn real de N abas pelo daemon 🔴👁
Abrir 3 abas de agente com **identidade** e **cwd herdado**, roles distintos (backend/frontend/reviewer),
via o subcomando `spawn` do daemon Tier 1 (abre a aba de verdade com a Python API `async_create_tab`).

**Passo** (precisa do iTerm2 rodando com a Python API habilitada e `pip3 install iterm2`):
```sh
it2agent-flag enable agent.status_board
for r in backend frontend reviewer; do
  python3 "$ST/daemon/it2agent_daemon.py" spawn \
    --no-gate --role "$r" --id "demo-$r" --task "aceite ac1 ($r)" --status busy \
    --dir "$REPO" -- $SHELL
done
```
- Flags reais (confirmadas em `daemon/it2agent_daemon.py`): `--dir/--home/--role/--task/--id/--status/--no-gate -- <cmd>`.
- Alternativa sem daemon/Python API (iTerm2 stock via AppleScript): `it2agent-spawn --role backend --id demo-backend -- $SHELL`.

**Esperado:** 3 abas novas abrem, cada uma **na pasta certa** (`$REPO`/worktree), rodando o comando
certo, com user-vars `agent_role`/`agent_id`/`agent_task`/`agent_status` (dot-free) setados e o
badge/título refletindo a identidade.
> **OPERADOR: olhe** as 3 abas — confirme que cada título/badge mostra o role certo (backend/
> frontend/reviewer) e que o `pwd` em cada aba é `$REPO`. O agente NÃO enxerga isto.
- Se o iTerm2/`iterm2` não estiver disponível, o comando imprime a instrução de instalação e sai —
  marque **AC1 pendente-ao-vivo** (o plano de spawn em si é validável a seco pela Seção 7 do smoke).

## AC2 — Board de status reflete de relance 🔴👁
Cada agente pinta seu estado; dá pra ler a frota de relance (cor da aba Okabe-Ito + badge + status-bar).

**Passo** (rode em cada aba de agente, ou daqui com `IT2AGENT_FORCE=1` para ver nesta aba):
```sh
it2agent-emit role reviewer
it2agent-emit task "aceite ac2"
it2agent-emit status busy   && it2agent-emit color busy
it2agent-emit badge
it2agent-emit progress 1 42
# prova dos bytes (dot-free — hotfix #23): a chave é agent_status, SEM ponto:
IT2AGENT_FORCE=1 it2agent-emit status running | od -c | head -2
```
**Esperado:** o `od -c` mostra `...SetUserVar=agent_status=...` (base64 no valor, chave **sem
ponto**). 🤖 essa parte é automatizável.
> **OPERADOR: olhe** a(s) aba(s): a **cor da aba** muda (busy = azul Okabe-Ito `0072B2`), o **badge**
> mostra `role · task`, e a status-bar/menu-bar reflete busy/blocked/done. Troque para
> `blocked`/`done` e confirme a cor mudar. O agente NÃO enxerga a cor/badge.

## AC3 — Mensageria agente↔agente entre abas (o moat) 🔴🤖
Agente A (esta aba) manda mensagem para o agente B (outra aba, de verdade) pelo broker file-based;
B faz poll, responde e dá ack (exactly-once). Reusa o shim `e2e_agent_shim.py` (é o mesmo fluxo da
Seção 11 do smoke). **A mecânica é 100% automatizável e provada abaixo**; o único bit 🔴 é confirmar
que a aba B abriu de verdade.

**Passo:**
```sh
it2agent-flag enable agent.broker agent.status_board
RESULT="$(mktemp -d)/received.log"
python3 "$ST/broker/it2agent_broker.py" serve --no-gate &   # broker durável (sqlite)
sleep 1
# Abre uma ABA NOVA de verdade rodando o agente B. O socket vai EXPLÍCITO porque a aba nova é um
# login shell que NÃO herda o env exportado desta aba:
( cd "$REPO" && IT2AGENT_FORCE=1 it2agent-spawn --role backend --id tabB -- \
    python3 "$ST/tests/e2e_agent_shim.py" --sock "$IT2AGENT_BROKER_SOCK" --result "$RESULT" --me b --peer a --timeout 30 )
sleep 3
# A manda cross-tab para B:
python3 - "$IT2AGENT_BROKER_SOCK" <<'PY'
import socket,sys,json
sock=sys.argv[1]
def rpc(o):
    s=socket.socket(socket.AF_UNIX); s.connect(sock)
    s.sendall((json.dumps(o)+"\n").encode()); r=s.makefile().readline(); s.close(); return json.loads(r)
print("A send:", rpc({"op":"send","to":"b","from":"a","body":"ping cross-tab"}))
PY
sleep 3
echo "--- o que a ABA B recebeu (escrito pelo processo da outra aba): ---"; cat "$RESULT"
echo "--- A recebe a resposta de B (poll+ack): ---"
python3 - "$IT2AGENT_BROKER_SOCK" <<'PY'
import socket,sys,json
sock=sys.argv[1]
def rpc(o):
    s=socket.socket(socket.AF_UNIX); s.connect(sock)
    s.sendall((json.dumps(o)+"\n").encode()); r=s.makefile().readline(); s.close(); return json.loads(r)
r=rpc({"op":"poll","agent":"a"}); print("A poll:", json.dumps(r,sort_keys=True))
if r.get("messages"): rpc({"op":"ack","agent":"a","msg_id":max(m["id"] for m in r["messages"])})
PY
```
**Esperado:** `$RESULT` mostra `recv ... body='ping cross-tab'` → `acked up_to=1` → `done`; e o
`poll` de A traz `body: "pong: ping cross-tab"` de `from: "b"`. Isso prova mensageria durável A↔B
**entre abas** com ack — o que o iTerm2 nativo não tem.
> **OPERADOR: olhe** que a aba B **abriu** com identidade (role backend). 🔴
- **Durabilidade (🤖):** o AC4 prova que isso sobrevive a reinício do broker (mesmo db sqlite).

## AC4 — Handoff/continuidade durável 🤖
Um agente escreve `handoff_put`; o processo morre; o broker reinicia no **mesmo db**; um agente
**fresco** faz `handoff_get` e retoma do último estado. Há um driver que faz tudo isso e checa:

**Passo:**
```sh
python3 "$ST/tests/ac4_handoff_continuity.py"
```
O driver: sobe broker #1 em um db temporário → registra + `handoff_put` → **mata** o broker #1 →
sobe broker #2 no **mesmo db** (socket novo) → `handoff_get` + `query`.

**Esperado:** imprime `AC4 PASS: handoff + registry survived process death AND broker restart`, com
o `handoff_get` retornando o **mesmo `id`/`goal`/`context_ptr`/`verification_status`** de antes e o
registry ainda contendo o agente. ❌ se qualquer campo mudar ou o registro sumir.

## AC5 — Atenção humana 🔴👁
Agente bloqueado dispara atenção; iTerm2 faz RequestAttention (dock bounce) + notificação.

**Passo** (na aba do agente, ou aqui com force para ver nesta aba):
```sh
it2agent-flag enable agent.status_board
IT2AGENT_FORCE=1 it2agent-emit attention "preciso de decisão humana (aceite ac5)"
# prova dos bytes: RequestAttention + OSC 9 (notificação)
IT2AGENT_FORCE=1 it2agent-emit attention "ac5" | od -c | head -4
```
**Esperado:** o `od -c` mostra a sequência `RequestAttention=yes` e uma notificação OSC 9. 🤖 essa
parte (bytes) é automatizável.
> **OPERADOR: olhe/ouça:** o ícone do iTerm2 no Dock **quica** e aparece uma **notificação** do
> macOS. Clicar na notificação deve **puxar você para a aba/pane do agente** que pediu atenção. 🔴

## AC6 — Persistência tmux -CC (UNVALIDATED) 🔴👁
Rodar agentes sob `tmux -CC`; matar/detach o iTerm2; **reattach**; confirmar que sessões/processos
sobreviveram e que a Python API ainda controla os panes.

**Passo** (precisa de iTerm2 + `pip3 install iterm2` + a Python API ligada):
```sh
it2agent-flag enable agent.tmux
# 1) sobe um agente dentro de uma sessão tmux -CC nativa:
( cd "$REPO" && it2agent-tmux spawn --no-gate --role probe --task api --id ac6 -- $SHELL )
# 2) OPERADOR: mate o iTerm2 (ou detach: prefixo tmux + d) e reabra; reattach:
#    it2agent-tmux attach --no-gate --id ac6      (ou: tmux -CC attach -t st-ac6)
# 3) valide que a API iTerm2 ainda enxerga/controla o pane tmux-CC:
python3 "$ST/tmux/validate_api_over_tmux.py" --session st-ac6
```
**Esperado:** após kill/reattach as janelas/agentes **voltam vivos**; o harness imprime uma tabela
PASS/FAIL das 5 superfícies (new_session, custom_escape_sequence, prompt, screen read,
set/get user var). Superfícies 2/4/5 devem dar **PASS**; 1/3 são confirmadas por ação do operador.
Responde a pergunta aberta da Tier 3 (ver `it2agent/tmux/API_VALIDATION.md`).
> **OPERADOR:** você precisa (a) matar/detach o iTerm2 e reattachar, e (b) olhar a tabela do harness
> e confirmar que os agentes reapareceram nos panes. Sem um iTerm2 vivo isto é **pendente-ao-vivo**. 🔴
- Sem `iterm2`/iTerm2, `validate_api_over_tmux.py` imprime instruções e sai não-zero **sem inventar
  resultado** — marque **AC6 pendente-ao-vivo**.

## AC7 — Feature-flags viram no-op de verdade 🤖
Com a flag OFF e **sem** `--no-gate`/`IT2AGENT_FORCE`, a ferramenta é no-op silencioso: **exit 0,
zero bytes no stdout** (fail-safe). Religar restaura. Há um checker que prova para
status_board/broker/mcp/review:

**Passo:**
```sh
python3 "$ST/tests/ac7_flag_noop.py"
```
O checker, para cada capability: `disable` → roda o comando → afirma `exit=0` e `stdout_bytes=0`;
para `status_board` (emit) também `enable` → roda → afirma `stdout_bytes>0` (restaurou); para
broker/mcp/review confirma a mensagem de gate no stderr e que a flag liga/desliga limpo. Ele usa um
`IT2AGENT_CONFIG` isolado e deixa tudo OFF no fim.

**Esperado:** imprime `AC7 PASS: all four capabilities are silent no-ops when OFF and restore when
ON`. Exemplo real de uma linha: `[agent.status_board] OFF: exit=0 stdout_bytes=0 -> no-op OK` e
`[agent.status_board] ON: exit=0 stdout_bytes=40 -> restored OK`. ❌ se qualquer capability emitir
bytes com a flag OFF ou não restaurar.

## AC8 — MCP dirigido por um agente real 🔴🤖
Um agente MCP conectado ao `it2agent_mcp.py` chama `spawn → assign → handoff → send_message` e cada
tool tem **efeito real** no store durável, não só JSON de retorno. Há um driver que dirige o MCP por
stdin JSON-RPC e depois **checa o broker fora de banda**:

**Passo (🤖, prova a mecânica agora):**
```sh
python3 "$ST/tests/ac8_mcp_drive.py"
```
O driver sobe um broker, aponta o MCP para ele via `IT2AGENT_BROKER_SOCK`, envia `initialize` +
`tools/list` + os 4 `tools/call`, e então consulta o broker diretamente: `query` (spawn/assign
registrados), `handoff_get` (handoff no store), `poll`+`ack` (mensagem entregue e exactly-once).

**Esperado:** imprime `AC8 PASS: spawn/assign/handoff/send_message each produced a real durable side
effect`; `tools/list` retorna 7 tools; o registry contém `ac8-spawned` e `ac8-assigned`; o
`handoff_get` volta o registro; o `poll` entrega `"ac8 hello"` e o `ack` não re-entrega.

**Passo (🔴, o único bit que precisa de iTerm2):** dentro do Claude Code de verdade, conecte o MCP
server e chame o tool `spawn` com um `id` — a **aba deve abrir** no iTerm2. Headless, o launcher
falha com `launched=false` (`No module named 'iterm2'`) e **só** o efeito de registro é provado
(o driver já assere isso). Marque a **abertura da aba via MCP** como pendente-ao-vivo.
> **OPERADOR: olhe** (no fluxo 🔴) a aba que o tool `spawn` do MCP abriu. 🔴

---

## Relatório final
Devolva esta tabela, preenchida com **evidência real**:

| AC | Tag | Resultado | Evidência (bytes/JSON/saída) | Pendente? |
|----|-----|-----------|------------------------------|-----------|
| AC1 spawn N abas       | 🔴👁 | | | |
| AC2 board de status    | 🔴👁 | | | |
| AC3 mensageria X-tab   | 🔴🤖 | | | |
| AC4 handoff durável    | 🤖   | | | |
| AC5 atenção humana     | 🔴👁 | | | |
| AC6 tmux -CC           | 🔴👁 | | | |
| AC7 flags no-op        | 🤖   | | | |
| AC8 MCP com efeito     | 🔴🤖 | | | |

Depois da tabela:
1. **O que passou 🤖 agora** (AC3-mecânica/AC4/AC7/AC8-mecânica) vs. **o que ficou pendente-ao-vivo**
   (AC1/AC2/AC5/AC6 e a abertura de aba via MCP no AC8) — seja honesto, não finja GUI.
2. **Cleanup (obrigatório):**
   - Restaure meus flags (o `IT2AGENT_CONFIG` era temporário, então nada tocou no meu config real).
   - **Mate os brokers** que subiu: `jobs` e `kill %1 %2 ...` (ou pelo PID).
   - **Feche as abas** spawnadas nos AC1/AC3 (e a sessão tmux `st-ac6` do AC6 se abriu:
     `tmux kill-session -t st-ac6`).

--- FIM ---
