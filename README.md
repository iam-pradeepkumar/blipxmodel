# Blipx OSS Indic Voice Server

Fully open-source speech-to-speech server for **all 22 scheduled Indian languages + English**, deployable on a single Render GPU box. Speaks to Twilio Media Streams directly, so your Lovable app just points its `<Stream>` at it.

## The stack (all Apache-2.0 / MIT)

| Layer | Model | Notes |
|---|---|---|
| STT | [`ai4bharat/indic-conformer-600m-multilingual`](https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual) | 22 Indic langs + English, auto language ID |
| LLM | [`sarvamai/sarvam-1`](https://huggingface.co/sarvamai/sarvam-1) | 2B params, Indic-tuned, Apache-2.0 |
| TTS | [`ai4bharat/indic-parler-tts`](https://huggingface.co/ai4bharat/indic-parler-tts) | 21 langs, natural prosody, voice-prompt controllable |

Everything runs in one process, one Docker image, one GPU. Nothing calls out to a paid API.

## Deploy to Render (5 min)

1. Push this `render-voice-server/` folder to a new Git repo (or a subdir).
2. In Render → **New → Blueprint**, point at the repo. It reads `render.yaml`.
3. Pick a **GPU plan** (L4 = ~$0.90/hr, fine for 5-10 concurrent calls). CPU works for dev but adds ~10 s per turn.
4. Set env var `HF_TOKEN` (get one at https://huggingface.co/settings/tokens — the Parler-TTS repo is gated, you must click "Agree" on the model page once).
5. Deploy. First boot downloads ~8 GB of weights into the mounted `/models` disk (~4-6 min). Subsequent restarts are instant.

Check it's up:
```bash
curl https://YOUR-SERVICE.onrender.com/health
```

## Wire it to your Blipx app

In this Lovable project, set a secret:
```
INDIC_VOICE_WS_URL = wss://YOUR-SERVICE.onrender.com/ws
```
Then in `src/lib/realtime-voice.server.ts`, swap the Deepgram/ElevenLabs pipeline for a passthrough that forwards Twilio frames to `INDIC_VOICE_WS_URL` (ask Lovable to do it when you're ready — this scaffold is server-only).

## Twilio TwiML

Point the call at the server:
```xml
<Response>
  <Connect>
    <Stream url="wss://YOUR-SERVICE.onrender.com/ws?callId={{CALL_ID}}&amp;lang=auto"/>
  </Connect>
</Response>
```
`lang=auto` lets the STT model detect. Force a language with `lang=hi`, `ta`, `te`, `bn`, `mr`, `gu`, `kn`, `ml`, `pa`, `or`, `as`, `ur`, `en`, etc.

## Local test (CPU, slow but works)

```bash
docker build -t indic-voice .
docker run --rm -p 8000:8000 -e DEVICE=cpu -e PRELOAD=0 -e HF_TOKEN=hf_xxx indic-voice
```

## Cost sanity

- Render L4 GPU: ~$0.90/hr → **~$650/mo** if always-on. Use Render's **auto-suspend** or scale to zero between calls.
- HuggingFace weights: free.
- Zero per-minute STT/LLM/TTS fees.

## Known limits

- Latency ~1.5–2.5 s per turn on L4 (no barge-in yet — this scaffold uses simple silence-based endpointing, not full-duplex VAD like Deepgram).
- Sarvam-1 is a text LLM, not instruction-tuned as heavily as GPT-4o; expect shorter, blunter replies.
- Indic-Parler-TTS voice is fixed by the style prompt in `app.py` — tweak the `style` string in `run_turn()` to change voice character.

For sub-second barge-in you'll want to add [Silero VAD](https://github.com/snakers4/silero-vad) and stream Parler audio incrementally — happy to scaffold that next.
