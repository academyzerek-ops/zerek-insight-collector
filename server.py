"""
ZEREK Insight Collector — API для Railway.

POST /collect          {"niche": "BARBER"}
POST /collect-batch    {"niches": ["COFFEE","DONER","BARBER"]}
GET  /niches
GET  /status
GET  /results
GET  /results/{niche}
"""

import os, json
from datetime import datetime
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from collect_insights import process_niche, load_config, OUTPUT_DIR, log

tasks: dict = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log.info("🚀 ZEREK Insight Collector API v2")
    yield

app = FastAPI(title="ZEREK Insight Collector", version="2.0", lifespan=lifespan)

class CollectReq(BaseModel):
    niche: str

class BatchReq(BaseModel):
    niches: list[str]

def run(niche_id: str):
    tasks[niche_id] = {"status": "running", "started": datetime.now().isoformat()}
    try:
        r = process_niche(niche_id, load_config())
        tasks[niche_id] = {**r, "finished": datetime.now().isoformat()}
    except Exception as e:
        tasks[niche_id] = {"status": "error", "msg": str(e), "finished": datetime.now().isoformat()}

@app.get("/status")
def status():
    return {"service": "ZEREK Insight Collector", "v": "2.0", "tasks": tasks}

@app.get("/niches")
def niches():
    c = load_config()
    return {"total": len(c), "niches": {k: v["name"] for k,v in sorted(c.items())}}

@app.post("/collect")
def collect(req: CollectReq, bg: BackgroundTasks):
    nid = req.niche.upper()
    c = load_config()
    if nid not in c: raise HTTPException(404, f"Ниша «{nid}» не найдена")
    if tasks.get(nid, {}).get("status") == "running":
        return {"msg": f"«{nid}» уже запущена", "task": tasks[nid]}
    bg.add_task(run, nid)
    return {"msg": f"«{nid}» ({c[nid]['name']}) запущена", "check": "/status"}

@app.post("/collect-batch")
def batch(req: BatchReq, bg: BackgroundTasks):
    c = load_config()
    ok, err = [], []
    for n in req.niches:
        n = n.upper()
        if n not in c: err.append(n); continue
        bg.add_task(run, n); ok.append(n)
    return {"started": ok, "errors": err}

@app.get("/results")
def list_results():
    if not OUTPUT_DIR.exists(): return {"files": []}
    return {"files": [
        {"name": f.name, "niche": f.stem.replace("_insight","").lower(),
         "kb": round(f.stat().st_size/1024,1)}
        for f in sorted(OUTPUT_DIR.glob("*_insight.md"))
    ]}

@app.get("/results/{niche_id}")
def get_result(niche_id: str):
    fp = OUTPUT_DIR / f"{niche_id.upper()}_insight.md"
    if not fp.exists(): raise HTTPException(404, f"{fp.name} не найден")
    return PlainTextResponse(fp.read_text(encoding="utf-8"), media_type="text/markdown")
