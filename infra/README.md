# Infra: Whisper (STT) and TTS

Speech-to-text and text-to-speech run as two Docker containers. They power the
voice features (voice messages in Telegram, Raspberry Pi / ESP32 voice clients).

## One command

```bash
bash infra/start.sh
```

This starts **both** containers, waits until they are healthy, and prints the exact
URLs to paste into the Home Assistant add-on configuration:

| Service | Host port | Add-on field | URL |
|---------|-----------|--------------|-----|
| Whisper STT | `10300` | `whisper.url` | `http://<host>:10300/v1/audio/transcriptions` |
| TTS | `10400` | `tts.url` | `http://<host>:10400/tts` |

Models are downloaded automatically on first start and cached on the host
(`faster_whisper/model-cache/`, `tts_server/models/`), so later starts are fast.

## Requirements

- **Docker** with the Compose plugin, see <https://docs.docker.com/get-docker/>
- **Whisper needs an NVIDIA GPU** + the
  [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)
  (one-time host setup). For CPU-only, set `WHISPER__INFERENCE_DEVICE=cpu` and
  `WHISPER__COMPUTE_TYPE=int8` in [docker-compose.yml](docker-compose.yml).

Recommended: run this on the same machine as LM Studio to share the GPU.

## Everyday commands

```bash
docker compose up -d        # start (from the infra/ folder)
docker compose logs -f      # follow logs
docker compose restart      # restart
docker compose down         # stop
```

`restart: unless-stopped` is set, so both containers come back after a host reboot
(ensure the Docker daemon starts on boot: `sudo systemctl enable docker`).

## Running them separately

Each service also ships its own compose file if you want to run only one, or run
them on different hosts:

- [faster_whisper/](faster_whisper/): Whisper STT, see its README for config knobs
  (model, language, VAD, CPU mode).
- [tts_server/](tts_server/): Piper TTS, see its README for voices and resampling.
