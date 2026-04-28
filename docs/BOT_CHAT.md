# Conversacion autonoma entre bots

Fecha: 2026-04-24

## Spec

El servicio de Telegram debe permitir que `codex`, `claude` y `gemini` mantengan una conversacion entre ellos despues de un mensaje humano inicial. La conversacion no debe bloquear el polling del servicio, para que pueda recibir comandos nuevos y cortar la charla cuando un humano escriba `silencio`.

## Comportamiento

- Un mensaje libre o `/chat ...` inicia una conversacion autonoma.
- Los tres bots responden una primera vuelta en orden.
- Si llega otro mensaje humano mientras la charla esta activa, dispara una nueva vuelta inmediata de los tres bots usando el transcript existente como contexto.
- Ese nuevo mensaje libre pasa a ser la semilla que guia los siguientes turnos autonomos.
- El estado queda persistido en `telegram_state.bot_chat`.
- En cada ciclo posterior de polling, avanza un turno mas con el siguiente bot.
- Cada respuesta recibe el transcript reciente como contexto.
- Si un proveedor falla en una vuelta, se avisa en el chat de servicio y los demas bots siguen respondiendo.
- `silencio`, `/silencio`, `silence`, `/silence` o `/stop-chat` detienen la conversacion activa.

## Criterios de aceptacion

- La conversacion sobrevive entre ciclos del servicio porque vive en el archivo de estado.
- El servicio puede procesar `/status`, tareas, aprobaciones y `silencio` entre turnos.
- No se depende de que Telegram entregue mensajes enviados por otros bots; el transcript interno alimenta los turnos.
- Los tests cubren inicio, avance de un turno sin updates nuevos y corte con `silencio`.
