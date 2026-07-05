"""
Blipx open-source Indian-language voice server.

Pipeline (all OSS, runs on a single GPU):
  Twilio μ-law 8k  ──▶ IndicWhisper (STT, 22 Indian langs + English)
                  ──▶ Sarvam-1 2B (LLM, Indic-tuned, Apache-2.0)
                  ──▶ Indic-Parler-TTS (TTS, 21 langs)
                  ──▶ μ-law 8k back to Twilio

Exposes:
  GET  /health
  WS   /ws?callId=<uuid>&lang=<hi|ta|te|bn|mr|en|...|auto>
       ↔ receives Twilio Media Streams JSON frames, returns the same.

Env:
  HF_TOKEN            HuggingFace token (models are gated for some).
  MODEL_STT           default: ai4bharat/indic-conformer-600m-multilingual
  MODEL_LLM           default: sarvamai/sarvam-1
  MODEL_TTS           default: ai4bharat/indic-parler-tts
  DEVICE              cuda | cpu   (cpu = demo only, will be slow)
"""

from __future__ import annotations

import asyncio
import audioop
import base64
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import torch
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("voice")

DEVICE = os.getenv("DEVICE", "cuda" if torch.cuda.is_available() else "cpu")
MODEL_STT = os.getenv("MODEL_STT", "ai4bharat/indic-conformer-600m-multilingual")
MODEL_LLM = os.getenv("MODEL_LLM", "sarvamai/sarvam-1")
MODEL_TTS = os.getenv("MODEL_TTS", "ai4bharat/indic-parler-tts")
HF_TOKEN = os.getenv("HF_TOKEN")

# ─────────── model loading (lazy, at first request) ───────────
_models: dict = {}


def _load_models():
    if _models:
        return _models
    log.info("Loading models on %s ...", DEVICE)
    from transformers import (
        AutoModelForCausalLM,
        AutoModelForSpeechSeq2Seq,
        AutoProcessor,
        AutoTokenizer,
        pipeline,
    )

    stt = pipeline(
        "automatic-speech-recognition",
        model=MODEL_STT,
        device=0 if DEVICE == "cuda" else -1,
        torch_dtype=torch.float16 if DEVICE == "cuda" else torch.float32,
        token=HF_TOKEN,
    )
    llm_tok = AutoTokenizer.from_pretrained(MODEL_LLM, token=HF_TOKEN)
    llm = AutoModelForCausalLM.from_pretrained(
        MODEL_LLM,
        torch_dtype=torch.float16 if DEVICE == "cuda" else torch.float32,
        device_map="auto" if DEVICE == "cuda" else None,
        token=HF_TOKEN,
    )
    tts_tok = AutoTokenizer.from_pretrained(MODEL_TTS, token=HF_TOKEN)
    from parler_tts import ParlerTTSForConditionalGeneration  # type: ignore

    tts = ParlerTTSForConditionalGeneration.from_pretrained(
        MODEL_TTS,
        torch_dtype=torch.float16 if DEVICE == "cuda" else torch.float32,
        token=HF_TOKEN,
    ).to(DEVICE)
    _models.update(stt=stt, llm=llm, llm_tok=llm_tok, tts=tts, tts_tok=tts_tok)
    log.info("Models ready.")
    return _models


# ─────────── audio helpers ───────────

def mulaw_b64_to_pcm16(b64: str) -> np.ndarray:
    raw = base64.b64decode(b64)
    pcm = audioop.ulaw2lin(raw, 2)  # μ-law → linear PCM16
    return np.frombuffer(pcm, dtype=np.int16)


def pcm16_to_mulaw_b64(pcm: np.ndarray, src_rate: int = 22050) -> list[str]:
    """Resample to 8k and encode to base64 μ-law frames (~20 ms each)."""
    pcm_bytes = pcm.astype(np.int16).tobytes()
    pcm_8k, _ = audioop.ratecv(pcm_bytes, 2, 1, src_rate, 8000, None)
    mulaw = audioop.lin2ulaw(pcm_8k, 2)
    # Twilio expects ~160-byte frames (20 ms @ 8k μ-law).
    frames = [mulaw[i : i + 160] for i in range(0, len(mulaw), 160)]
    return [base64.b64encode(f).decode() for f in frames]


# ─────────── per-call state ───────────
@dataclass
class Call:
    ws: WebSocket
    stream_sid: Optional[str] = None
    lang: str = "auto"
    history: list[dict] = field(default_factory=list)
    audio_buf: bytearray = field(default_factory=bytearray)
    speaking: bool = False
    last_voice_ms: float = 0.0


SILENCE_MS = 700  # end-of-turn if this much silence after speech


def _has_voice(pcm16_chunk: np.ndarray) -> bool:
    return float(np.abs(pcm16_chunk).mean()) > 350  # simple energy VAD


# ─────────── LLM + TTS turn ───────────
async def run_turn(call: Call, user_text: str):
    m = _load_models()
    call.history.append({"role": "user", "content": user_text})
    prompt = (
        "You are a helpful Indian voice assistant. Reply in ONE short sentence "
        f"(<= 15 words) in the same language as the user. Language hint: {call.lang}.\n"
        + "\n".join(f"{t['role']}: {t['content']}" for t in call.history[-8:])
        + "\nassistant:"
    )
    inputs = m["llm_tok"](prompt, return_tensors="pt").to(DEVICE)
    with torch.no_grad():
        out = m["llm"].generate(**inputs, max_new_tokens=80, do_sample=True, temperature=0.6)
    reply = m["llm_tok"].decode(out[0][inputs.input_ids.shape[1] :], skip_special_tokens=True).strip()
    reply = reply.split("\n")[0][:240]
    call.history.append({"role": "assistant", "content": reply})
    log.info("[%s] user=%r  bot=%r", call.stream_sid, user_text, reply)

    # TTS
    style = "A warm, friendly Indian voice speaking clearly and expressively."
    tts_in = m["tts_tok"](style, return_tensors="pt").to(DEVICE)
    prompt_in = m["tts_tok"](reply, return_tensors="pt").to(DEVICE)
    with torch.no_grad():
        audio = m["tts"].generate(
            input_ids=tts_in.input_ids,
            prompt_input_ids=prompt_in.input_ids,
        )
    pcm = audio.cpu().numpy().squeeze()
    pcm16 = (pcm * 32767).astype(np.int16)
    sr = m["tts"].config.sampling_rate
    for frame_b64 in pcm16_to_mulaw_b64(pcm16, src_rate=sr):
        await call.ws.send_text(json.dumps({
            "event": "media",
            "streamSid": call.stream_sid,
            "media": {"payload": frame_b64},
        }))


async def process_utterance(call: Call):
    if not call.audio_buf:
        return
    pcm = np.frombuffer(bytes(call.audio_buf), dtype=np.int16).astype(np.float32) / 32768.0
    call.audio_buf.clear()
    if pcm.size < 8000 * 0.3:  # < 300 ms → ignore
        return
    m = _load_models()
    # Resample 8k → 16k for the STT model.
    pcm_bytes = (pcm * 32767).astype(np.int16).tobytes()
    pcm_16k, _ = audioop.ratecv(pcm_bytes, 2, 1, 8000, 16000, None)
    arr = np.frombuffer(pcm_16k, dtype=np.int16).astype(np.float32) / 32768.0
    result = m["stt"]({"array": arr, "sampling_rate": 16000},
                     generate_kwargs={"language": None if call.lang == "auto" else call.lang})
    text = (result.get("text") or "").strip()
    if not text:
        return
    await run_turn(call, text)


# ─────────── FastAPI app ───────────
app = FastAPI(title="Blipx OSS Indic Voice Server")


@app.get("/health")
def health():
    return JSONResponse({
        "ok": True,
        "device": DEVICE,
        "stt": MODEL_STT,
        "llm": MODEL_LLM,
        "tts": MODEL_TTS,
        "loaded": bool(_models),
    })


@app.on_event("startup")
async def _preload():
    if os.getenv("PRELOAD", "1") == "1":
        try:
            _load_models()
        except Exception as e:
            log.exception("preload failed: %s", e)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    qp = dict(ws.query_params)
    call = Call(ws=ws, lang=qp.get("lang", "auto"))
    log.info("WS open callId=%s lang=%s", qp.get("callId"), call.lang)
    silence_task: Optional[asyncio.Task] = None

    async def silence_watchdog():
        while True:
            await asyncio.sleep(0.2)
            if call.speaking and (asyncio.get_event_loop().time() * 1000 - call.last_voice_ms) > SILENCE_MS:
                call.speaking = False
                await process_utterance(call)

    try:
        silence_task = asyncio.create_task(silence_watchdog())
        while True:
            msg = json.loads(await ws.receive_text())
            evt = msg.get("event")
            if evt == "start":
                call.stream_sid = msg["start"]["streamSid"]
            elif evt == "media":
                pcm16 = mulaw_b64_to_pcm16(msg["media"]["payload"])
                call.audio_buf.extend(pcm16.tobytes())
                if _has_voice(pcm16):
                    call.speaking = True
                    call.last_voice_ms = asyncio.get_event_loop().time() * 1000
            elif evt == "stop":
                break
    except WebSocketDisconnect:
        pass
    finally:
        if silence_task:
            silence_task.cancel()
        log.info("WS close callId=%s", qp.get("callId"))
