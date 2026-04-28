# Mejoras ejecutadas

Fecha: 2026-04-24

Este documento registra las 20 tareas implementadas, con spec breve y criterio de aceptacion.

| # | Tarea | Spec | Criterio de aceptacion | Estado |
|---|---|---|---|---|
| 1 | Dry-run sin CLIs externos | `run` y `service --dry-run` deben aceptar proveedores configurados aunque `codex`, `claude` o `gemini` no esten instalados, y permitir smoke tests sobre directorios no Git. | `prepare_provider_args` no consulta binarios cuando `dry_run=True`; `build_repo_context` usa fallback no-Git solo en dry-run. | Hecho |
| 2 | Validacion de proveedores | La lista de proveedores debe rechazar nombres desconocidos o duplicados antes de ejecutar. | Errores claros para desconocidos, duplicados o cantidad distinta de 3. | Hecho |
| 3 | Parser de `.env` mas tolerante | Cargar lineas `export KEY=value` e ignorar comentarios inline fuera de comillas. | Tokens y valores con `#` entre comillas se conservan. | Hecho |
| 4 | Validacion de `TELEGRAM_TOPIC_ID` | El topic debe ser entero no negativo o fallar temprano. | Valores invalidos producen `MulticodersError`. | Hecho |
| 5 | Comandos `/task` con comillas | Permitir rutas con espacios y texto quoted en comandos de Telegram. | `shlex.split` parsea `repo="/tmp/my repo"`. | Hecho |
| 6 | Tipos de tarea normalizados | `type=Feature` debe aceptarse como `feature`; valores no soportados se rechazan. | Solo `bugfix` y `feature` pasan. | Hecho |
| 7 | Filtrado por topic en servicio | El servicio no debe procesar mensajes de otros topics cuando el bot esta atado a uno. | `process_service_commands` valida `chat_id` y `message_thread_id`. | Hecho |
| 8 | Filtrado por topic en feedback humano | La ventana de feedback debe escuchar solo el topic configurado. | `TelegramBot.wait_for_human_messages` usa `message_matches_scope`. | Hecho |
| 9 | Particionado de mensajes largos | Telegram no debe recibir mensajes por encima del limite de 4096 caracteres. | `send_message` divide texto largo en chunks. | Hecho |
| 10 | Soporte de `edited_message` | Updates editados de Telegram deben parsearse como mensajes cuando tienen texto. | `get_updates` acepta `message` y `edited_message`. | Hecho |
| 11 | Parseo JSON mas robusto de proveedores | El extractor debe soportar texto con llaves dentro de strings sin cortar candidatos invalidos. | Scanner balanceado respeta strings y escapes. | Hecho |
| 12 | Normalizacion de JSON fenced | JSON dentro de markdown fences debe pasar por la misma normalizacion que el JSON directo. | `response/content/text/result` nested se desenvuelve tambien en fences. | Hecho |
| 13 | Prompts con contexto acotado | Payloads previos demasiado grandes no deben inflar el prompt sin limite. | `render_prior_payloads` trunca a 12000 caracteres. | Hecho |
| 14 | Reglas de implementacion mas seguras | El prompt debe pedir no tocar caches, lockfiles ni archivos no relacionados salvo necesidad. | La regla aparece en `build_discussion_prompt`. | Hecho |
| 15 | Validacion Python con fallback stdlib | Si no hay `pytest`, el validador debe correr `python3 -m unittest discover -s tests`. | `select_validation_commands` cubre proyectos con `tests/`. | Hecho |
| 16 | Resultado con `validation_ok` | El resultado de una sesion debe exponer si todas las validaciones pasaron. | `execute_council_session` retorna e imprime `validation_ok`. | Hecho |
| 17 | Servicio falla si falla validacion | Una tarea no debe quedar `done` cuando las validaciones automáticas fallan. | `run_service` marca `failed` si `validation_ok=False`. | Hecho |
| 18 | SQLite mas resiliente | La base debe usar `busy_timeout`, foreign keys y claim atomico de tareas aprobadas. | `connect_db` configura PRAGMAs y `claim_next_approved_task` usa `BEGIN IMMEDIATE`. | Hecho |
| 19 | Estados de tarea validados | No se deben persistir estados desconocidos. | `update_task_status` valida contra `TASK_STATUSES`. | Hecho |
| 20 | Higiene de proyecto y tooling | Ignorar caches/estado local y declarar dependencias de desarrollo. | `.gitignore`, extras `dev` y `pytest` config agregados. | Hecho |

Verificacion local usada:

```bash
python3 -m unittest discover -s tests
python3 -m compileall multicoders tests
```
