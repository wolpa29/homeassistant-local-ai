# Bare-metal / systemd install (advanced)

The Home Assistant add-on is the recommended way to run this project (see the
[README](../README.md)). If you'd rather run the services directly on a Linux host,
for example you don't use HA Supervisor / HAOS, or you want them on a separate machine,
use the systemd installer.

All services share a single `.env` file at the repo root (see [`.env.example`](../.env.example)).

## Install

```bash
chmod +x deploy/systemd/install.sh
sudo deploy/systemd/install.sh                       # all services: venvs + deps + enable + start
sudo deploy/systemd/install.sh --no-start            # install only, don't enable/start
sudo deploy/systemd/install.sh voice_gateway notify_gateway   # subset
```

For each selected service the script:

1. creates `services/<svc>/<svc>_env/` (if missing)
2. installs `services/<svc>/requirements.txt`
3. copies the matching `.service` file into `/etc/systemd/system/`
4. runs `systemctl daemon-reload` and (unless `--no-start`) `enable` + `restart`

Re-running is safe.

## Units

| Service dir | Unit |
| --- | --- |
| `telegram_bot` | `telegram-bot.service` |
| `voice_gateway` | `voice-gateway.service` |
| `notify_gateway` | `notify-gateway.service` |

## Manual control

```bash
systemctl status notify-gateway
journalctl -u notify-gateway -f
systemctl restart notify-gateway
```

## Infrastructure

You still need Whisper and TTS running (one command, see [`infra/README.md`](../infra/README.md))
and LM Studio. Point the matching `.env` variables at them:

```env
WHISPER_EXTERNAL_URL=http://<host>:10300/v1/audio/transcriptions
TTS_EXTERNAL_URL=http://<host>:10400/tts
LMSTUDIO_URL=http://<host>:1234
```
