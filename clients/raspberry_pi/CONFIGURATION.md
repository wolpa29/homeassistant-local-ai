# Raspberry Pi Configuration

This is a configuration guide for the raspberry pi hardware which I already tested:

---

## First Setup - Raspberry + Mic HAT

### Hardware
- RaspberryPi 3b+ with Raspberry Pi OS Lite (64-bit) (Debian Trixie with no Desktop environment)(Kernel 6.12+)
- ReSpeaker 2Mic HAT v1
- 4Ohm 3W Speaker connected to the HAT

### Installation

#### 1. Driver Installation (The Modern Way)
We avoid old installation scripts because they often fail on new kernels. Instead, we use the modern Device Tree Overlay method.

```Bash
# Clone the repository containing the modern overlay files
git clone https://github.com/Seeed-Studio/seeed-linux-dtoverlays.git
cd seeed-linux-dtoverlays

# Compile the overlay into machine code
make overlays/rpi/respeaker-2mic-v1_0-overlay.dtbo

# Copy it to the system boot folder
sudo cp overlays/rpi/respeaker-2mic-v1_0-overlay.dtbo /boot/firmware/overlays/respeaker-2mic-v1_0.dtbo
```

Why? The overlay tells the Pi’s CPU exactly which pins are connected to the WM8960 audio chip. Without it, the system cannot "see" the hardware.

#### 2. Enabling the Hardware
You must tell the Pi to load this overlay every time it starts up.

```Bash
# Add the instruction to your boot configuration
echo "dtoverlay=respeaker-2mic-v1_0" | sudo tee -a /boot/firmware/config.txt

# Reboot to activate the driver
sudo reboot
```

#### 3. Identifying Device IDs (The "Card Number")
Linux assigns numbers to audio devices. You need to know these numbers for your commands.

Find Recording ID:
```Bash
arecord -l
```
Find Playback ID:
```Bash
aplay -l
```

How to read it: Look for card X: seeed2micvoicec. Usually, it is Card 1.
In commands, we address this as plughw:1,0 (where 1 is the card and 0 is the device).

#### 4. Unmuting the Mixer (Crucial Step)
The WM8960 chip starts in a "muted" state to save power. You must manually open the internal routes.
If amixer says "Control not found", run that command to see the exact names your specific kernel uses for the switches.

```Bash
run amixer -c 1
```

Microphone (Input) Settings:
```Bash
# Enable the capture switch
amixer -c 1 sset 'Capture' 100%

# Connect the physical microphones (LINPUT1/RINPUT1) to the mixer
amixer -c 1 sset 'Left Boost Mixer LINPUT1' on
amixer -c 1 sset 'Right Boost Mixer RINPUT1' on

# Turn on the internal amplifiers (Boost)
amixer -c 1 sset 'Left Input Mixer Boost' on
amixer -c 1 sset 'Right Input Mixer Boost' on

# Set the digital volume to maximum
amixer -c 1 sset 'ADC PCM' 100%
```

Speaker (Output) Settings:
```Bash
# Set all output volumes to 100%
amixer -c 1 sset 'Playback' 100%
amixer -c 1 sset 'Speaker' 100%
amixer -c 1 sset 'Headphone' 100%

# Route the digital audio (PCM) to the physical outputs
amixer -c 1 sset 'Left Output Mixer PCM' on
amixer -c 1 sset 'Right Output Mixer PCM' on
```

#### 5. Saving Settings Permanently
Standard Linux resets these volumes on every reboot. Save them now:

```Bash
sudo alsactl store
```

#### 6. Usage & Testing
Now you can record and play back using the specific device address.

Record 5 seconds:

```Bash
arecord -D "plughw:1,0" -f S16_LE -r 16000 -d 5 -t wav test_mic.wav
```

THe file should has a size of ~157kB. To verify that run:
```Bash
ls -lh test_final.wav
```

Play back (on HAT speakers):

```Bash
aplay -D "plughw:1,0" test_mic.wav
```

Play back (on Raspberry Pi 3.5mm jack):

```Bash
aplay -D "plughw:0,0" test_mic.wav
```

Troubleshooting Tips
- Silent Recording: Re-run the amixer commands from Step 4. Usually, the "Boost" or "Capture" switches are the cause.
- Audio Open Error: The driver isn't loaded. Check if dtoverlay=respeaker-2mic-v1_0 is correctly spelled in /boot/firmware/config.txt.

---

## Python Voice Client Setup

This connects the Pi to the smart home assistant running on your PC.
The script (`voice_client.py`) listens for a wake word, records your command, sends it to the Voice Gateway service, and speaks the reply back.

### Quick Install (recommended)

`install.sh` automates steps 1–8 below in a single run. It is safe to re-run on an existing installation — it will ask whether to keep or replace the existing `.env`.

**From your PC** (inside the `homeassistant-local-ai` project directory):

```bash
PI_IP=<your-pi-ip>   # e.g. 192.168.1.50
scp clients/raspberry_pi/{voice_client.py,requirements.txt,install.sh} pi@$PI_IP:~/
```

**On the Pi:**

```bash
bash ~/install.sh
```

The script will:
- Install system packages (`portaudio19-dev`, `python3-venv`, `python3-pip`)
- Create `~/voice/` with a Python venv and all dependencies
- Walk you through all config values interactively (with defaults)
- Optionally download the Piper TTS model (`de_DE-thorsten-low`)
- Configure the ALSA mixer and save it persistently
- Install, enable, and start the `voice-client` systemd service

After the script finishes:

```bash
sudo systemctl status voice-client   # check it is running
journalctl -u voice-client -f        # watch live logs
```

To change settings later, edit `~/voice/.env` and restart the service:

```bash
nano ~/voice/.env
sudo systemctl restart voice-client
```

---

The steps below document the same process manually, in case you need to customize or troubleshoot individual parts.

### Architecture

```
[ReSpeaker HAT] --> voice_client.py --> POST /audio?tts=true --> [Voice Gateway :8765]
                                                                          |
                                                                  [Whisper transcription]
                                                                          |
                                                                   [LLM + Home Assistant]
                                                                          |
                                                               POST text --> [TTS Server :10400]
                                                                          |
                                                               WAV file <--
                                                                          |
                         aplay plays WAV directly <-- audio/wav response
```

> **Why external TTS?** Local Piper TTS on the Pi 3b+ was too slow. TTS now runs on
> the same external host as Whisper where synthesis is near-instant. The Pi only
> needs to play the received WAV with `aplay` — no model needed locally.

Two services must be running before starting the Pi client:
- Voice Gateway: `python services/voice_gateway/main.py`
- TTS Server: see **TTS Server Setup** section below

### 1. System Packages

```bash
sudo apt update
sudo apt install -y python3-pip python3-venv portaudio19-dev
```

- `portaudio19-dev` — required by sounddevice to talk to ALSA

### 2. Copy Files to the Pi

Run this from your PC inside the homeassistant-local-ai project directory:

```bash
PI_IP=<your-pi-ip>   # e.g. 192.168.1.50
ssh pi@$PI_IP "mkdir -p ~/voice"
scp clients/raspberry_pi/voice_client.py pi@$PI_IP:~/voice/
scp clients/raspberry_pi/requirements.txt pi@$PI_IP:~/voice/
```

### 3. Create a Virtual Environment & Install Dependencies

On the Pi:

```bash
cd ~/voice
python3 -m venv venv
source venv/bin/activate

# Install openwakeword without its tflite dependency first.
# tflite-runtime has no Python 3.13 wheel for ARM64.
# We use ONNX inference only, so tflite is never needed at runtime.
pip install openwakeword --no-deps

# Install everything else (includes the ONNX runtime deps openwakeword needs)
pip install -r requirements.txt
```

openwakeword will download its ONNX model files (~50 MB) on first run automatically.

### 4. Identify the ALSA Device

Both input and output use the same `plughw:X,0` format — the same device name you use in `arecord`/`aplay` commands.

Find the card number:
```bash
arecord -l
aplay -l
```

Look for `seeed2micvoicec` — usually card 1. This gives you `plughw:1,0` for both input and output. The script automatically derives the sounddevice index from this string internally.

### 5. Download a Piper Voice Model

Piper is a neural TTS engine that sounds significantly better than espeak. Voice models run fully offline on the Pi.

Recommended German voices:

| Model | Quality | Sample Rate | Size | Style |
|---|---|---|---|---|
| `de_DE-thorsten-low` | Good | 16000 Hz | ~16 MB | Male — **use this, works on ReSpeaker HAT** |
| `de_DE-thorsten-high` | High | 22050 Hz | ~108 MB | Male — does NOT work on ReSpeaker HAT (unsupported rate) |

> **Important:** The ReSpeaker HAT's WM8960 driver only supports sample rates that are multiples of 8000 Hz (8000, 16000, 32000, 48000). The `high` quality models output at 22050 Hz which is incompatible. Use `thorsten-low` (16000 Hz).

Download on the Pi:

```bash
mkdir -p ~/voice/models
wget -P ~/voice/models https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/thorsten/low/de_DE-thorsten-low.onnx
wget -P ~/voice/models https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/thorsten/low/de_DE-thorsten-low.onnx.json
```

Both files (`.onnx` + `.onnx.json`) must be in the same folder.

Test it before running the full client:

```bash
echo "Hallo, ich bin dein Sprachassistent." | \
  piper --model ~/voice/models/de_DE-thorsten-low.onnx --output-raw | \
  aplay -D plughw:1,0 -r 16000 -f S16_LE -c 1
```

### 6. Configure Environment Variables

Create a `.env` file in `~/voice/` on the Pi:

```bash
cat > ~/voice/.env << 'EOF'
GATEWAY_URL=http://10.1.10.78:8765
GATEWAY_API_KEY=
DEVICE_ID=rpi-wohnzimmer
WAKE_WORD=hey_jarvis
ALSA_INPUT_DEVICE=plughw:1,0
ALSA_OUTPUT_DEVICE=plughw:1,0
TTS_MODEL=/home/pi/voice/models/de_DE-thorsten-low.onnx
WAKE_THRESHOLD=0.5
EOF
```

- `GATEWAY_URL`: IP of your PC where the Voice Gateway runs (port 8765).
- `GATEWAY_API_KEY`: Leave empty if you did not set one in the gateway's `.env`.
- `DEVICE_ID`: A name for this Pi. Use your Telegram chat_id here (numeric) to share conversation history with the Telegram bot.
- `WAKE_WORD`: Built-in choices: `hey_jarvis`, `alexa`, `hey_mycroft`, `hey_rhasspy`.
- `ALSA_INPUT_DEVICE`: ALSA device for mic input. Same format as `arecord -D`. Usually `plughw:1,0`.
- `ALSA_OUTPUT_DEVICE`: ALSA device for all audio output (beep + TTS). Same format as `aplay -D`. Usually `plughw:1,0`.
- `TTS_MODEL`: Only used as local fallback if the TTS server is unreachable. Leave empty since TTS now runs externally.
- `FOLLOWUP_ENABLED`: `true` (default) — after a reply the Pi listens again without the wake word. `false` to always require the wake word.
- `FOLLOWUP_INITIAL_TIMEOUT`: Seconds of silence after the reply before returning to wake-word mode (default `1.5`). Raise if you need more reaction time.
- `FOLLOWUP_ONSET_CHUNKS`: Consecutive loud 80 ms chunks required to count as speech (default `3`, ≈ 240 ms). Raise to `4`–`5` if the assistant's own voice triggers a false start.
- `FOLLOWUP_DRAIN_SECONDS`: Seconds of mic audio discarded immediately after playback (default `0.5`). Absorbs echo/TTS tail.

The script loads `.env` automatically — just run it directly:

```bash
cd ~/voice
source venv/bin/activate
python3 voice_client.py
```

### 7. Verify the Connection

**Step 1 — Check the gateway is reachable from the Pi:**

```bash
curl http://10.1.10.78:8765/health
# Expected response: {"status":"ok"}
```

If this fails: the Voice Gateway is not running on your PC, or the IP/port is wrong.

**Step 2 — Send a text command to test end-to-end without audio:**

```bash
curl -X POST http://10.1.10.78:8765/text \
  -H "Content-Type: application/json" \
  -d '{"text": "Licht einschalten", "device_id": "rpi-wohnzimmer"}'
```

Expected: a JSON response like `{"reply": "Licht wurde eingeschaltet.", "actions_executed": [...]}`.

**Step 3 — Test audio capture + gateway with a real WAV:**

```bash
# Record 3 seconds
arecord -D "plughw:1,0" -f S16_LE -r 16000 -d 3 -t wav /tmp/cmd.wav

# POST the WAV to the gateway (same as voice_client.py does internally)
curl -X POST http://10.1.10.78:8765/audio \
  -F "file=@/tmp/cmd.wav;type=audio/wav" \
  -F "device_id=rpi-wohnzimmer"
```

If the gateway returns `{"error":"no_speech"}` the WAV was silent — re-run amixer Step 4.

### 8. Autostart with systemd

So the client starts automatically on boot:

> **Important:** systemd's `EnvironmentFile` does not strip inline comments. Make sure your `.env` on the Pi has no comments on the same line as a value (e.g. `KEY=value # comment` breaks it). Put comments on their own line or remove them entirely.

```bash
sudo nano /etc/systemd/system/voice-client.service
```

Paste the following (adjust `User` and paths if your user is not `pi`):

```ini
[Unit]
Description=Smart Home Voice Client
After=network.target sound.target
Wants=network.target

[Service]
User=pi
WorkingDirectory=/home/pi/voice
EnvironmentFile=/home/pi/voice/.env
ExecStart=/home/pi/voice/venv/bin/python3 voice_client.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable voice-client
sudo systemctl start voice-client

# Check it is running:
sudo systemctl status voice-client

# Watch live logs:
journalctl -u voice-client -f
```

### 9. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `curl /health` times out | Voice Gateway not running on PC | Start `python services/voice_gateway/main.py` on your PC |
| `{"error":"no_speech"}` from gateway | Mic not capturing audio | Re-run amixer commands from Section 4 above |
| `sounddevice.PortAudioError: Invalid device` | Wrong `ALSA_INPUT_DEVICE` card number | Run `arecord -l` to confirm the card number and update `ALSA_INPUT_DEVICE` in `.env` |
| No audio reply from Pi | TTS server not running or wrong URL | Check `TTS_EXTERNAL_URL` in gateway `.env` and verify TTS server is up |
| Wake word never fires | Threshold too high or wrong wake word | Lower `WAKE_THRESHOLD` to `0.3`, or check `WAKE_WORD` spelling |
| Records too long / cuts off early | VAD threshold wrong for room noise | Adjust `VAD_SILENCE_THRESHOLD` (default 500, raise in noisy rooms) |
| Follow-up triggered by assistant's own voice | TTS echo exceeds RMS threshold | Raise `FOLLOWUP_ONSET_CHUNKS` to `4`–`5` or increase `FOLLOWUP_DRAIN_SECONDS` to `0.8` |
| Follow-up ends too fast / no time to respond | `FOLLOWUP_INITIAL_TIMEOUT` too short | Raise to `2.5`–`3.0` |
| Follow-up never ends after conversation | Background noise above `VAD_SILENCE_THRESHOLD` | Raise `VAD_SILENCE_THRESHOLD` or set `FOLLOWUP_ENABLED=false` |

---

## TTS Server Setup (External Host)

See [tts_server/README.md](../../services/tts_server/README.md) for setup, Docker config, and API reference.
