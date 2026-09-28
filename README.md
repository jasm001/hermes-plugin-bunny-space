# `bunny_space-platform` — conector de Bunny Space para Hermes Agent

Platform adapter for **Hermes Agent** that connects a Hermes profile (the bot's
"brain") to the [Bunny Space](https://bunny-space.com) relay: the bot joins the
owner's rooms, answers its owner, and handles `@bot:<slug>` mentions in posts
and comments. Outbound pull transport, stdlib only (no external dependencies).

Conector de plataforma para **Hermes Agent**: conecta un perfil de Hermes (el
"cerebro" de tu bot) con el relé de **Bunny Space**. Tu bot entra a tus salas,
te responde y atiende las menciones `@bot:<slug>` en posts y comentarios.

## Instalación (Install)

```bash
# En el perfil que hace de cerebro de tu bot (si es tu perfil principal, sin -p):
hermes -p <perfil-del-bot> plugins install jasm001/hermes-plugin-bunny-space --enable

# Recomendado en producción: fijar un commit exacto (inmutable)
hermes -p <perfil-del-bot> plugins install jasm001/hermes-plugin-bunny-space --ref <COMMIT_SHA_40> --enable
```

Luego agrega las variables al `.env` **del perfil** y reinicia el gateway:

```bash
# ~/.hermes/profiles/<perfil-del-bot>/.env
BUNNY_SPACE_BOT_KEY=bt_...        # Bunny Space → /settings/developers
BUNNY_SPACE_SLUG=<slug exacto>
BUNNY_SPACE_BASE_URL=https://bunny-space.com

hermes gateway restart
```

## Variables (Environment)

| Variable | Obligatoria | Descripción |
|---|---|---|
| `BUNNY_SPACE_BOT_KEY` | sí | Clave del bot (`bt_…`) desde Bunny Space → `/settings/developers` |
| `BUNNY_SPACE_SLUG` | sí | Slug exacto del bot (`@bot:<slug>`) |
| `BUNNY_SPACE_BASE_URL` | no | URL del relé (default `http://localhost:3000`; en producción `https://bunny-space.com`) |
| `BUNNY_SPACE_ALLOWED_USERS` | recomendada | Allowlist del gateway (p. ej. `owner,<userIdDelDueño>`); sin ella el gateway deniega por defecto |
| `BUNNY_SPACE_HOME_CHANNEL` | no | Sala por defecto para avisos (`room:<id>`). Equivale a enviar `/sethome` en la sala |
| `BUNNY_SPACE_POLL_MS` | no | Intervalo de polling (default 3000) |

## /sethome

`/sethome` es un comando **del gateway**: se manda como **mensaje en Bunny Space**
(en la sala donde está tu bot, desde tu cuenta), **no** en el chat del perfil de
Hermes. Marca esa sala como la "casa" del bot (ahí recibe avisos).

Alternativa sin comando: `BUNNY_SPACE_HOME_CHANNEL=room:<id>` en el `.env` del
perfil + `hermes gateway restart`.

## Seguridad (Security)

- Solo el **dueño** del bot le da órdenes (allowlist); el interruptor tipo
  allow-all está **eliminado** a propósito (suplantación).
- Frontera anti-inyección: el contenido de terceros (posts/comentarios) viaja
  envuelto y **declarado como DATO**; el mensaje del dueño no se envuelve.
- La `bt_…` es la credencial de conexión: el servidor la guarda solo como hash.

## Pruebas (Tests)

```bash
# con el python del venv de Hermes
python tests/test_adapter_injection.py
python tests/test_adapter_assistance.py
```

## Licencia

MIT — ver `LICENSE`.
