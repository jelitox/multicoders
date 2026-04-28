# Memes, GIFs y emoticones

Fecha: 2026-04-24

## Spec

Los bots pueden complementar una respuesta de chat con emoticones en texto y, cuando hay assets configurados, enviar memes, GIFs o stickers a Telegram.

## Configuracion

Los catálogos se cargan desde variables de entorno:

```env
MULTICODERS_MEME_ASSETS=deploy=https://example.com/deploy.jpg,tests=https://example.com/tests.jpg
MULTICODERS_GIF_ASSETS=shipit=https://example.com/shipit.gif,thinking=https://example.com/thinking.gif
MULTICODERS_STICKER_ASSETS=ok=CAACAgQAAxkBAAIB...
```

Tambien se acepta JSON object:

```env
MULTICODERS_GIF_ASSETS={"shipit":"https://example.com/shipit.gif","thinking":"https://example.com/thinking.gif"}
```

Los valores pueden ser URLs publicas aceptadas por Telegram o `file_id` de Telegram.

## Uso por comandos

```text
/meme deploy esto compila en mi maquina
/gif shipit listo para prod
/sticker ok
```

El servicio elige uno de los agentes para enviar la media configurada.

## Uso por agentes

El prompt de chat informa las claves disponibles. Un agente puede poner una directiva en una linea propia:

```text
[meme:deploy]
[gif:shipit]
[sticker:ok]
[emoji::)]
```

Las directivas de media se quitan del texto y se transforman en llamadas de Telegram:

- `meme` usa `sendPhoto`.
- `gif` usa `sendAnimation`.
- `sticker` usa `sendSticker`.
- `emoji` se inserta como texto normal.

## Criterios de aceptacion

- Si no hay asset configurado para una clave, no se intenta llamar a Telegram con una URL inventada.
- Las captions de media se recortan al limite de Telegram.
- El topic/thread configurado se respeta porque todos los envios usan el mismo `TelegramBot`.
- Los tests cubren comandos, directivas de chat y llamadas `sendPhoto`, `sendAnimation`, `sendSticker`.
