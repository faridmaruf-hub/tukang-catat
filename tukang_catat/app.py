"""Server lokal Tukang Catat (FastAPI). Hanya mendengarkan di 127.0.0.1."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from datetime import datetime
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .session import Models, Session
from .transcribe import OUTPUT_JSON, OUTPUT_MD, transcribe_session

ROOT = Path(__file__).resolve().parent.parent
WEB = Path(__file__).resolve().parent / "web"
RECORDINGS = Path(os.environ.get("TUKANG_CATAT_DATA", ROOT / "recordings"))
DOWNLOADABLE = {OUTPUT_MD, OUTPUT_JSON, "audio.wav", "session.json"}

app = FastAPI(title="Tukang Catat")
app.mount("/static", StaticFiles(directory=WEB), name="static")

_models: Models | None = None
SESSIONS: dict[str, Session] = {}
JOBS: dict[str, dict] = {}


def models() -> Models:
    global _models
    if _models is None:
        _models = Models(ROOT / "models")
    return _models


def get_session(sid: str) -> Session:
    sess = SESSIONS.get(sid)
    if sess is None:
        raise HTTPException(404, "Sesi tidak ditemukan")
    return sess


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


class NewSession(BaseModel):
    title: str = ""
    participants: list[str] = []
    threshold: float = 0.65


@app.post("/api/sessions")
def create_session(body: NewSession) -> dict:
    sid = datetime.now().strftime("%Y%m%d-%H%M%S")
    SESSIONS[sid] = Session(sid, RECORDINGS, models(), body.title, body.participants,
                            threshold=min(max(body.threshold, 0.3), 0.95))
    return {"sid": sid}


@app.websocket("/ws/{sid}")
async def ws_session(ws: WebSocket, sid: str) -> None:
    await ws.accept()
    sess = SESSIONS.get(sid)
    if sess is None:
        await ws.close(code=4404)
        return
    await ws.send_json(sess.state())
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                samples = np.frombuffer(msg["bytes"], dtype="<f4")
                for ev in await asyncio.to_thread(sess.feed, samples):
                    await ws.send_json(ev)
                continue
            data = json.loads(msg.get("text") or "{}")
            kind = data.get("type")
            if kind == "label":
                sess.label(int(data["speaker_id"]), str(data["name"]))
            elif kind == "reassign":
                sess.reassign(int(data["segment_id"]), str(data["name"]))
            elif kind == "threshold":
                sess.tracker.threshold = min(max(float(data["value"]), 0.3), 0.95)
            elif kind == "stop":
                await asyncio.to_thread(sess.stop)
            await ws.send_json(sess.state())
    except WebSocketDisconnect:
        pass
    finally:
        # Jika browser tertutup di tengah rapat, simpan apa yang sudah terekam.
        if sess.recording:
            await asyncio.to_thread(sess.stop)


@app.get("/api/sessions/{sid}/clip/{speaker_id}")
def speaker_clip(sid: str, speaker_id: int) -> Response:
    data = get_session(sid).clip_wav(speaker_id)
    if data is None:
        raise HTTPException(404, "Tidak ada contoh suara")
    return Response(data, media_type="audio/wav")


class TranscribeRequest(BaseModel):
    model: str = "medium"
    language: str = "auto"


@app.post("/api/sessions/{sid}/transcribe")
def start_transcribe(sid: str, body: TranscribeRequest) -> dict:
    sess = get_session(sid)
    if sess.recording:
        raise HTTPException(409, "Hentikan rekaman dulu")
    job = JOBS.get(sid)
    if job and job["status"] == "running":
        return job
    job = {"status": "running", "progress": 0.0, "message": "Menyiapkan...", "error": None}
    JOBS[sid] = job

    def progress(p: float, message: str) -> None:
        job["progress"], job["message"] = round(p, 3), message

    def work() -> None:
        try:
            lang = None if body.language == "auto" else body.language
            transcribe_session(sess.dir, body.model, lang, progress)
            job["status"] = "done"
        except Exception as e:  # noqa: BLE001
            job["status"], job["error"] = "error", str(e)

    threading.Thread(target=work, daemon=True).start()
    return job


@app.get("/api/sessions/{sid}/transcribe")
def transcribe_status(sid: str) -> dict:
    return JOBS.get(sid) or {"status": "idle"}


@app.get("/api/sessions/{sid}/files/{name}")
def download(sid: str, name: str) -> FileResponse:
    if name not in DOWNLOADABLE:
        raise HTTPException(404)
    path = RECORDINGS / get_session(sid).sid / name
    if not path.exists():
        raise HTTPException(404, "Berkas belum ada")
    return FileResponse(path, filename=f"{sid}-{name}")
