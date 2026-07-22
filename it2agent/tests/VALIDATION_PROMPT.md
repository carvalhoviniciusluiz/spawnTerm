# Prompt de validação do it2agent (cole no Claude)

> **Como usar:** abra o Claude (Claude Code) numa aba deste terminal e cole TUDO que está
> dentro do bloco `--- PROMPT ---` abaixo. O Claude vai executar a validação e te devolver um
> relatório com ✅/❌ por capacidade. (Se preferir, rode: `claude "$(sed -n '/^--- PROMPT ---$/,/^--- FIM ---$/p' it2agent/tests/VALIDATION_PROMPT.md)"`.)

--- PROMPT ---

Você está rodando dentro do **it2agent**, um fork pessoal do iTerm2 que adiciona orquestração de
agentes de IA via ferramentas de linha de comando (escape codes do iTerm2 + um broker externo
file-based com ack + Python API + tmux). Sua tarefa é **validar o suporte que foi adicionado** e me
devolver um relatório honesto com ✅/❌ por item. Responda em português.

Regras:
- **Toda capacidade é um feature-flag, default OFF.** Habilite o que precisar para testar e
  desabilite no fim (deixe o estado como encontrou). Use um config isolado para não sujar o meu:
  `export IT2AGENT_CONFIG="$(mktemp -d)/config.toml"`.
- Não invente resultado: se um comando falhar, cole o erro real e marque ❌.
- Diferencie o que é **verificável agora no terminal (CLI)** do que **precisa do app compilado (GUI)** —
  a aba de Settings, os componentes de status-bar e o item de menu-bar só aparecem no iTerm2 compilado.

## 0. Setup (rode primeiro)
```sh
export REPO="/Users/vinicius.carvalho/Developments/Workspaces/carvalhotech/spawnterm"
export ST="$REPO/it2agent"
export PATH="$ST:$ST/flags:$ST/emit:$ST/spawn:$ST/broker:$ST/review:$ST/janitor:$ST/cost:$ST/inbox:$ST/mcp:$ST/tmux:$PATH"
export IT2AGENT_CONFIG="$(mktemp -d)/config.toml"
```

## 1. Descoberta (o guia do agente)
```sh
it2agent-help | head -40
```
✅ se imprime a cheat-sheet (intro + capacidades → flag → comando → exemplo).

## 2. Feature-flags (default OFF, toggle)
```sh
it2agent-flag list                      # tudo off
it2agent-flag enable agent.status_board
it2agent-flag agent.status_board; echo "exit=$?"   # imprime 1, exit 0
it2agent-flag list | grep status_board  # on
```
✅ se lista mostra 14 flags off; enable liga; query retorna 1/exit 0.

## 3. Status board — escape codes (VISÍVEL nesta aba do iTerm2)
Com `agent.status_board` ON:
```sh
it2agent-emit role reviewer
it2agent-emit status busy
it2agent-emit task "validando o it2agent"
it2agent-emit color busy        # cor da aba muda (azul Okabe-Ito 0072B2)
it2agent-emit badge             # badge da sessão mostra role · task
it2agent-emit progress 1 42     # barra de progresso na aba
it2agent-emit attention "precisa de humano"   # RequestAttention + notificação
```
Verifique **visualmente na aba** (cor/badge/atenção) E os bytes:
```sh
IT2AGENT_FORCE=1 it2agent-emit status running | od -c | head -2   # SetUserVar=agent_status=... (SEM ponto)
```
✅ se a aba reage (cor/badge/atenção) e o user-var é `agent_status` (sem ponto — ver hotfix #23).

## 4. Broker durável (o diferenciador: file-based + ack)
```sh
it2agent-flag enable agent.broker
export IT2AGENT_BROKER_DB="$(mktemp -d)/broker.db"
export IT2AGENT_BROKER_SOCK="$(mktemp -d)/broker.sock"
python3 "$ST/broker/it2agent_broker.py" serve --no-gate &   # sobe o broker
sleep 1
python3 "$ST/broker/it2agent_broker.py" health --no-gate 2>/dev/null || \
  python3 - <<'PY'
import socket,os,json
s=socket.socket(socket.AF_UNIX); s.connect(os.environ["IT2AGENT_BROKER_SOCK"])
def rpc(o): s.sendall((json.dumps(o)+"\n").encode()); return s.makefile().readline()
print("health:", rpc({"op":"health"}))
print("send:  ", rpc({"op":"send","to":"b","from":"a","body":"olá durável"}))
print("poll:  ", rpc({"op":"poll","agent":"b"}))
print("ack:   ", rpc({"op":"ack","agent":"b","msg_id":1}))
print("replay:", rpc({"op":"poll","agent":"b"}))   # vazio após ack
PY
```
✅ se `send`→`poll` entrega a mensagem, `ack` confirma, e o `poll` seguinte não a re-entrega
(exactly-once por cursor+ack). ❌ se qualquer passo falhar.

## 5. Registry + handoff (persistentes)
No mesmo broker:
```sh
python3 - <<'PY'
import socket,os,json
s=socket.socket(socket.AF_UNIX); s.connect(os.environ["IT2AGENT_BROKER_SOCK"])
def rpc(o): s.sendall((json.dumps(o)+"\n").encode()); return s.makefile().readline()
print(rpc({"op":"register","session_id":"s1","role":"backend","task":"api","alive":True}))
print(rpc({"op":"query","role":"backend"}))
print(rpc({"op":"handoff_put","agent_id":"s1","goal":"g","context_ptr":"notes.md","verification_status":"pending"}))
print(rpc({"op":"handoff_get","agent_id":"s1","goal":"g"}))
PY
```
✅ se registra/consulta e o handoff volta a última versão.

## 6. Worktree + $PORT (isolamento por agente)
```sh
it2agent-flag enable agent.worktree_isolation
( cd "$REPO" && IT2AGENT_FORCE=1 it2agent-worktree plan --id demo --role backend )
```
✅ se imprime branch `it2agent/backend-demo-<hash>`, worktree fora do repo, port em 41000–41999, namespace.

## 7. Spawn com identidade (dry-run; abrir aba real precisa do iTerm2 rodando)
```sh
( cd "$REPO" && IT2AGENT_FORCE=1 it2agent-spawn --dry-run --role backend --id demo -- claude )
```
✅ se o plano mostra cwd herdado, os `it2agent-emit` de identidade, o header do guia e (se worktree ON) o plano de isolamento.

## 8. MCP surface (auto-orquestração)
```sh
it2agent-flag enable agent.mcp
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
             '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  | IT2AGENT_FORCE=1 python3 "$ST/mcp/it2agent_mcp.py" | python3 -m json.tool 2>/dev/null | head -60
```
✅ se `tools/list` retorna 7 tools (spawn, assign, handoff, send_message, status, list_agents, help).

## 9. Review / janitor / cost / inbox (smoke)
```sh
it2agent-flag enable agent.review agent.janitor agent.cost_dashboard agent.inbox 2>/dev/null
( cd "$REPO" && IT2AGENT_FORCE=1 it2agent-review resolve --id demo --role backend )   # resolve branch/base
( cd "$REPO" && IT2AGENT_FORCE=1 it2agent-janitor resolve --id demo --role backend )  # gate config/resolve
IT2AGENT_FORCE=1 it2agent-cost --source "$ST/cost/tests" 2>/dev/null | head -8         # tabela de custo (fixtures)
IT2AGENT_FORCE=1 it2agent-inbox list 2>&1 | head -3
```
✅ se cada um responde sem crash (o de cost mostra tabela por-agente a partir dos fixtures).

## 10. GUI (só no app compilado — marque como "pendente de build" se ainda não compilou)
- Settings → General → AI → **AI Agents**: checkboxes das 14 capacidades + os 3 imports de IA
  (Claude status-bar, menu-bar de agentes, Codex tab status).
- Ligar `claude_statusbar`/`menubar`/`codex_status` e ver o componente aparecer.

## Relatório final
Devolva uma tabela ✅/❌ por seção (1–10), citando a evidência real (bytes/JSON/saída). No fim,
liste o que passou no CLI agora vs. o que ficou pendente de build (GUI). Restaure meus flags
(o `IT2AGENT_CONFIG` era temporário, então nada foi tocado no meu config real) e mate o broker
que subiu (`kill %1` ou pelo PID).

--- FIM ---
