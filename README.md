# multicoders

`multicoders` es un CLI en Python para coordinar un consejo técnico entre `codex`, `claude` y `gemini` sobre un repositorio Git específico.

También puede correr como servicio persistente y quedar a la espera de tareas nuevas aprobadas por humanos desde Telegram.

El flujo base es:

1. Cada agente inspecciona el repo y propone una solución.
2. Los tres revisan las propuestas del resto.
3. Los tres votan.
4. Solo gana una solución si al menos `2 de 3` agentes coinciden.
5. El agente autor de la solución ganadora implementa el cambio en el working tree.
6. Toda la discusión se espeja a un grupo de Telegram usando un bot distinto por agente.

## Variables esperadas en `.env`

```env
# nombre visible o username del bot
BOT_GEMINI=multicoders_geminitox
BOT_CODEX=multicoders_codexitox
BOT_CLAUDE=multicoders_clauditox

# tokens de cada bot
BOT_GEMINI_KEY=telegram-token-for-gemini-bot
BOT_CODEX_KEY=telegram-token-for-codex-bot
BOT_CLAUDE_KEY=telegram-token-for-claude-bot

# chat id numérico o @channel/group handle
TELEGRAM_GROUP=-1001234567890

# opcional: topic/thread del grupo si usas foros en Telegram
TELEGRAM_TOPIC_ID=77

# opcional: assets de memes/GIF/stickers por clave.
# Los valores pueden ser URL publica de Telegram-compatible o file_id de Telegram.
MULTICODERS_MEME_ASSETS=deploy=https://example.com/deploy.jpg,tests=https://example.com/tests.jpg
MULTICODERS_GIF_ASSETS=shipit=https://example.com/shipit.gif,thinking=https://example.com/thinking.gif
MULTICODERS_STICKER_ASSETS=ok=CAACAgQAAxkBAAIB...
```

## Instalación

```bash
cd /home/jelitox/repos/labs/multicoders
python3 -m pip install -e .
```

## Uso

Ejecución puntual:

Modo real:

```bash
multicoders \
  --repo /ruta/al/repo \
  --task-type bugfix \
  --task "Fix the login race condition when refreshing tokens"
```

Feature con sesgo SDD:

```bash
multicoders \
  --repo /ruta/al/repo \
  --task-type feature \
  --task "Add CSV export for the billing report with acceptance criteria and tests"
```

Dry run del pipeline sin tocar Telegram ni proveedores:

```bash
multicoders \
  --repo /ruta/al/repo \
  --task "Example task" \
  --feedback-wait-sec 15 \
  --telegram-state-file /tmp/multicoders-state.json \
  --dry-run
```

En `--dry-run`, `codex`, `claude` y `gemini` no necesitan estar instalados; el CLI usa payloads simulados para validar la orquestación. También permite smoke tests sobre directorios no Git.

Brainstorming de diseño sin implementación:

```bash
multicoders brainstorming \
  --repo /ruta/al/repo \
  --topic "construir un algoritmo isomorfico"
```

Modo servicio:

```bash
multicoders service \
  --db-file /home/jelitox/.local/state/multicoders/service.db \
  --telegram-state-file /home/jelitox/.local/state/multicoders/telegram-state.json \
  --log-level INFO \
  --log-file /home/jelitox/.local/state/multicoders/service.log
```

El servicio hace polling de Telegram, crea tareas en SQLite y solo ejecuta sesiones cuando una tarea pasa a `approved`.
Si configuras `TELEGRAM_TOPIC_ID`, todos los bots publican dentro de ese topic.

Probar conectividad real de los tres bots al grupo:

```bash
multicoders send-test-messages \
  --message "connectivity check from multicoders"
```

Sin enviar nada, solo mostrando el payload previsto:

```bash
multicoders send-test-messages \
  --message "connectivity check from multicoders" \
  --dry-run
```

Descubrir `chat_id` y `message_thread_id` a partir de updates recientes:

```bash
multicoders discover-telegram-chat --env-file .env
```

Sin red, con ejemplo simulado:

```bash
multicoders discover-telegram-chat --env-file .env --dry-run
```

## systemd

Hay una unit de ejemplo en [contrib/multicoders.service](/home/jelitox/repos/labs/multicoders/contrib/multicoders.service:1).

Ejemplo de instalación:

```bash
mkdir -p ~/.config/systemd/user
cp /home/jelitox/repos/labs/multicoders/contrib/multicoders.service ~/.config/systemd/user/multicoders.service
systemctl --user daemon-reload
systemctl --user enable --now multicoders.service
```

## Comandos de Telegram

Crear tarea:

```text
/task repo=/ruta/al/repo type=bugfix Fix token refresh race in auth middleware
```

En grupos, también funciona sin nombrar ningún bot. Si quieres, igualmente puedes usar la variante con `@bot`, pero no es obligatoria.

Crear tarea con `lead` aleatorio entre `codex`, `claude` y `gemini`:

```text
/random-task repo=/ruta/al/repo type=bugfix Fix token refresh race in auth middleware
```

Tipos válidos:

- `bugfix`
- `feature`

Aprobar tarea:

```text
/approve 12
```

Rechazar tarea:

```text
/reject 12
```

Reintentar tarea fallida o rechazada:

```text
/retry 12
```

Ver estado reciente:

```text
/status
```

Conversación libre entre bots:

```text
que es el sol?
```

Si el mensaje no empieza con `/`, el servicio abre una conversación autónoma de grupo: responden los tres bots en orden y luego siguen contestándose entre ellos, un turno por ciclo de polling del servicio, reutilizando el transcript persistido.
Si Telegram no les entrega texto libre por `privacy mode`, usa este fallback explícito:

```text
/chat que es el sol?
```

La spec de este modo está en [docs/BOT_CHAT.md](/home/jelitox/repos/labs/multicoders/docs/BOT_CHAT.md:1).

Detener la conversación autónoma:

```text
silencio
```

También funciona `/silencio`. El servicio corta el transcript activo y deja de avanzar turnos entre bots hasta que llegue otro mensaje libre o `/chat`.

Brainstorming de diseño:

```text
/brainstorming construir un algoritmo isomorfico
```

Control:

```text
/brainstorming-status
/brainstorming-cancel
```

La spec de este modo está en [docs/BRAINSTORMING.md](/home/jelitox/repos/labs/multicoders/docs/BRAINSTORMING.md:1).

Enviar media configurada desde un bot:

```text
/meme deploy esto compila en mi maquina
/gif shipit listo para prod
/sticker ok
```

Durante la conversación, los agentes también pueden pedir media usando directivas en su respuesta:

```text
[meme:deploy]
[gif:shipit]
[sticker:ok]
[emoji::)]
```

La spec de media está en [docs/MEDIA_MESSAGES.md](/home/jelitox/repos/labs/multicoders/docs/MEDIA_MESSAGES.md:1).

## Flags útiles

- `--env-file .env`: carga variables locales.
- `--telegram-state-file /ruta/state.json`: persiste `last_update_id` y metadata de discusión para reutilizar offsets entre corridas. Si no se pasa, usa `.multicoders/telegram-state.json` dentro del repo objetivo.
- `TELEGRAM_TOPIC_ID`: topic/thread opcional para aislar la conversación dentro de un grupo tipo foro.
- `MULTICODERS_MEME_ASSETS`, `MULTICODERS_GIF_ASSETS`, `MULTICODERS_STICKER_ASSETS`: catálogos `clave=url_o_file_id` separados por coma; también aceptan JSON object.
- `service --db-file /ruta/service.db`: base SQLite para cola de tareas y resultados.
- `service --poll-sec 5`: intervalo de polling cuando no hay trabajo en ejecución.
- `service --once`: procesa un ciclo de polling y termina; útil para smoke tests o cron.
- `--log-level INFO`: nivel de logs para servicio o corridas puntuales.
- `--log-file /ruta/log`: guarda logs además de stdout.
- `send-test-messages --message "..."`: envía un mensaje real con cada bot al grupo o topic configurado.
- `discover-telegram-chat`: inspecciona updates recientes y lista candidatos para `TELEGRAM_GROUP` y `TELEGRAM_TOPIC_ID`.
  Ahora consulta los 3 bots y muestra qué chats ve cada uno.
- `brainstorming --repo /ruta --topic "..."`: ejecuta la sesión de diseño y guarda el spec Markdown en `docs/brainstorming/` o en `--docs-dir`.
- `--providers codex,claude,gemini`: orden fijo de agentes.
- `--provider-timeout-sec 600`: timeout por invocación.
- `--tie-break-rounds 1`: rondas extra de desempate si la primera votación no logra `2/3`.
- `--feedback-wait-sec 30`: ventana para capturar feedback humano nuevo desde Telegram antes de votar.
- `--feedback-max-messages 6`: máximo de mensajes humanos que se reinyectan al council.
- `--codex-model`, `--claude-model`, `--gemini-model`: overrides opcionales.
- `--no-telegram`: ejecuta el consejo sin espejar mensajes a Telegram.
- `--dry-run`: prueba la orquestación sin llamadas de red ni a los CLIs.

## Alcance actual

- Las 20 mejoras de robustez ejecutadas el 2026-04-24 estan documentadas en [docs/IMPROVEMENTS.md](/home/jelitox/repos/labs/multicoders/docs/IMPROVEMENTS.md:1).
- El consenso es explícito y exige `2/3`.
- Si la votación inicial empata, hay una o más rondas de desempate configurables.
- La discusión se publica en Telegram con el bot correspondiente a cada agente.
- Los mensajes del council salen etiquetados con `run_id` y opcionalmente pueden ir a un `topic` de Telegram.
- Los mensajes del council ahora están formateados con un tono más natural y bullets para listas.
- `random-task` asigna aleatoriamente un `lead` inicial para abrir la discusión; el resto del council sigue participando igual.
- Los mensajes de texto libre en el grupo disparan una conversación autónoma entre los tres bots, usando el historial persistido como contexto, hasta recibir `silencio`.
- Los bots pueden enviar emoticones en texto y media configurada como memes, GIFs o stickers vía comandos o directivas de chat.
- Puede pedir una ventana breve de feedback humano en Telegram y reutilizar esos mensajes como contexto antes de votar.
- Persiste el offset de Telegram y un historial corto de corridas para no releer mensajes viejos entre ejecuciones.
- Tiene un modo `service` con cola persistente en SQLite y aprobación explícita por Telegram antes de ejecutar.
- El servicio ahora registra progreso por fase y proveedor en logs (`spec`, `review`, `vote`, `tie_break`, `implement`).
- Si no logra `2/3` tras las rondas de desempate pero hay una pluralidad clara de votos válidos, aplica `forced consensus` y lo deja registrado.
- La implementación la hace el agente cuya propuesta ganó la votación.
- Después de implementar, corre validaciones automáticas por stack cuando hay una opción razonable: `pytest` o `compileall` para Python, `npm test` o `npm run lint` para JS/TS, `go test ./...` para Go y `cargo test` para Rust.
- En Python, si `pytest` no esta disponible pero existe `tests/`, usa `python3 -m unittest discover -s tests` antes de `compileall`.
- El resultado impreso incluye `validation_ok`; el servicio marca una tarea como `failed` si la implementacion no pasa validaciones.
- No hay webhook; la lectura de feedback humano se hace por polling simple vía `getUpdates`.

Ya quedó soporte básico de `topic/thread`, etiquetado por `run_id` y una unit de `systemd`.
También dejé tests del bridge de Telegram en [tests/test_telegram.py](/home/jelitox/repos/labs/multicoders/tests/test_telegram.py:1) para validar el envío al grupo desde cada bot y el caso con `message_thread_id`.
También agregué [tests/test_cli.py](/home/jelitox/repos/labs/multicoders/tests/test_cli.py:1) para validar el subcomando `send-test-messages` en `dry-run`.
Ese mismo archivo también cubre `discover-telegram-chat` en `dry-run`.
También agregué [tests/test_consensus.py](/home/jelitox/repos/labs/multicoders/tests/test_consensus.py:1) para el fallback de consenso y el reset de tareas con `retry`.
