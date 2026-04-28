# Brainstorming de solucions

Fecha: 2026-04-24

## Objetivo

`/brainstorming` inicia una sesión de diseño entre `codex`, `claude` y `gemini` para producir la mejor solución posible sobre un tema dado, sin implementar cambios en el repo.

## Flujo

1. Cada bot propone una solución.
2. Cada bot se autoevalua con `self_score` y luego puntua las propuestas de los demas bots.
3. El sistema calcula una puntuacion agregada por propuesta.
4. Los bots votan por la mejor propuesta segun la puntuacion agregada.
5. Si hay unanimidad, cada bot propone una mejora a esa solución y vuelven a votar.
6. Si la mejora tampoco es unánime, se abre otra ronda con propuestas nuevas o refinadas.
7. El proceso se corta despues de 3 rondas máximas.
8. Cuando hay una solución definitiva, se genera un spec Markdown y se guarda en `docs/brainstorming/`.

## Puntuacion

La puntuacion computable por propuesta usa dos señales:

- `self_score` del autor de la propuesta.
- puntuacion media de los otros bots sobre esa misma propuesta.

Formula:

```text
aggregate_score = 0.4 * self_score + 0.6 * peer_average
```

La mejora usa la misma formula, reemplazando propuestas por mejoras.

## Comandos

Telegram:

```text
/brainstorming construir un algoritmo isomorfico
/brainstorming-status
/brainstorming-cancel
silencio
```

CLI:

```bash
multicoders brainstorming --repo /ruta/al/repo --topic "construir un algoritmo isomorfico"
```

## Criterios de aceptacion

- El sistema produce valores numericos comparables para cada propuesta y mejora.
- Cada bot se autoevalua y evalua a los demas.
- El resultado final es un spec, no una implementacion.
- El spec final se guarda como Markdown y tambien se reporta por Telegram cuando hay bot configurado.
