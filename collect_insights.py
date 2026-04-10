#!/usr/bin/env python3
"""
ZEREK Insight Collector v2.0
33 ниши · 3–7 видео · ≤5 лет · только русский · только относительные показатели

    python collect_insights.py --niche BARBER
    python collect_insights.py --batch
    python collect_insights.py --list
"""

import os, sys, json, argparse, re, time, logging
from datetime import datetime, timezone
from pathlib import Path

import requests
from youtube_transcript_api import YouTubeTranscriptApi
from google import genai

# ── Конфиг ───────────────────────────────────────────────────
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
GEMINI_API_KEY  = os.environ.get("GEMINI_API_KEY", "")
OUTPUT_DIR      = Path(os.environ.get("OUTPUT_DIR", "./output"))
CONFIG_PATH     = Path(__file__).parent / "niche_queries.json"

TARGET_VIDEOS   = 7        # берём до 7
MIN_VIDEOS      = 1        # минимум 1 (было 3)
MIN_DUR         = 180      # 3 мин (было 8)
MAX_AGE_YEARS   = 5
LANG            = "ru"
GEMINI_MODEL    = "gemini-2.5-flash"

YT_SEARCH = "https://www.googleapis.com/youtube/v3/search"
YT_VIDEOS = "https://www.googleapis.com/youtube/v3/videos"

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("zerek")

# ── Промпт ───────────────────────────────────────────────────
PROMPT = """Ты — аналитик бизнес-рисков. Аудитория — начинающие предприниматели.

Даны транскрипты {n} видеороликов о бизнесе «{name}».

## СТРОГИЕ ПРАВИЛА
1. Только универсальный практический опыт.
2. ЗАПРЕЩЕНО: конкретные валюты (₸, ₽, $), абсолютные цены и суммы, конкретные города, годы, имена, бренды, названия каналов.
3. Все показатели — ОТНОСИТЕЛЬНЫЕ: доли %, соотношения, пропорции, сроки в месяцах, коэффициенты.
4. Фокус: риски, ошибки, организация, от чего зависит успех.
5. Не мотивация, не реклама, не пересказ видео.
6. Русский язык. Перефразируй, не копируй.
7. Каждый пункт — конкретный и практичный.

## СТРУКТУРА (Markdown)

# Инсайты: {name}

## Ключевые принципы управления
- (3–7 пунктов)

## Типичные ошибки новичков
- (5–10 пунктов с пояснением 1–2 предложения)

## Операционные подводные камни
- (3–7 пунктов: невидимое до старта)

## Финансовые риски и ловушки
- (3–5 пунктов: юнит-экономика, структура затрат, точка безубыточности — в относительных показателях)

## Что отличает выживших от закрывшихся
- (3–5 факторов)

## Красные флаги (когда лучше не открывать)
- (3–5 стоп-сигналов)

---
*На основе анализа {n} видеообзоров практиков. Дата: {date}*

---

{transcripts}
"""

# ── Утилиты ──────────────────────────────────────────────────
def load_config() -> dict:
    if not CONFIG_PATH.exists():
        log.error(f"Нет конфига: {CONFIG_PATH}"); sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def iso_dur(s: str) -> int:
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s)
    return (int(m[1] or 0)*3600 + int(m[2] or 0)*60 + int(m[3] or 0)) if m else 0

def fresh(pub: str) -> bool:
    try:
        d = datetime.fromisoformat(pub.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - d).days <= MAX_AGE_YEARS * 365
    except: return True

# ── 1. Поиск ─────────────────────────────────────────────────
def search(queries: list[str]) -> list[dict]:
    seen, out = set(), []
    for q in queries:
        log.info(f"  🔍 «{q}»")
        try:
            r = requests.get(YT_SEARCH, params={
                "part": "snippet", "q": q, "type": "video",
                "videoDuration": "medium", "relevanceLanguage": LANG,
                "order": "relevance", "maxResults": 10, "key": YOUTUBE_API_KEY,
            }, timeout=15)
            r.raise_for_status()
        except Exception as e:
            log.warning(f"    ⚠ {e}"); continue
        for it in r.json().get("items", []):
            vid = it["id"].get("videoId")
            if not vid or vid in seen: continue
            seen.add(vid)
            s = it["snippet"]
            out.append({"video_id": vid, "title": s.get("title",""),
                        "channel": s.get("channelTitle",""), "published": s.get("publishedAt","")})
        time.sleep(0.3)
    log.info(f"  Уникальных: {len(out)}")
    return out

# ── 2. Фильтрация ────────────────────────────────────────────
def filt(videos: list[dict]) -> list[dict]:
    if not videos: return []
    ids = [v["video_id"] for v in videos[:50]]
    try:
        r = requests.get(YT_VIDEOS, params={
            "part": "contentDetails,statistics", "id": ",".join(ids), "key": YOUTUBE_API_KEY,
        }, timeout=15)
        r.raise_for_status()
        det = {it["id"]: (iso_dur(it["contentDetails"]["duration"]),
                          int(it["statistics"].get("viewCount",0)))
               for it in r.json().get("items",[])}
    except Exception as e:
        log.warning(f"  ⚠ {e}"); return videos[:TARGET_VIDEOS]

    ok = []
    for v in videos:
        d = det.get(v["video_id"])
        if not d: continue
        dur, views = d
        if dur < MIN_DUR: continue
        if not fresh(v["published"]): continue
        v["duration"], v["views"] = dur, views
        ok.append(v)
    ok.sort(key=lambda x: x["views"], reverse=True)
    res = ok[:TARGET_VIDEOS]
    log.info(f"  Фильтр: {len(res)} из {len(videos)}")
    return res

# ── 3. Транскрипты ────────────────────────────────────────────
def transcripts(videos: list[dict]) -> list[dict]:
    ytt = YouTubeTranscriptApi()
    out = []
    for v in videos:
        log.info(f"  📝 {v['title'][:55]}...")
        txt = None
        # Try multiple language codes
        for langs in [["ru"], ["ru", "a.ru", "ru-RU"], ["en", "a.en"]]:
            try:
                t = ytt.fetch(v["video_id"], languages=langs)
                txt = " ".join(s.text for s in t.snippets)
                break
            except:
                continue
        if not txt:
            log.info(f"     ✗ нет субтитров"); continue
        w = len(txt.split())
        if w < 500:
            log.info(f"     ✗ {w} слов (мало)"); continue
        out.append({**v, "transcript": txt, "words": w})
        log.info(f"     ✓ {w} слов")
        time.sleep(0.2)
    log.info(f"  Итого транскриптов: {len(out)}")
    return out

# ── 4. Gemini ─────────────────────────────────────────────────
def summarize(ts: list[dict], name: str) -> str:
    MAX_W = 60000
    sel, total = [], 0
    for t in ts:
        if total + t["words"] > MAX_W: break
        sel.append(t); total += t["words"]
    if not sel: return ""

    blocks = []
    for i, t in enumerate(sel, 1):
        blocks.append(f"### Видео {i}: {t['title']}\n{t['views']:,} просмотров | {t['duration']//60} мин\n\n{t['transcript'][:12000]}\n")

    prompt = PROMPT.format(n=len(sel), name=name,
                           date=datetime.now().strftime("%Y-%m-%d"),
                           transcripts="\n---\n".join(blocks))
    log.info(f"  🤖 Gemini: {len(sel)} видео, ~{total} слов...")
    try:
        c = genai.Client(api_key=GEMINI_API_KEY)
        r = c.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        log.info(f"  ✓ {len(r.text)} символов")
        return r.text
    except Exception as e:
        log.error(f"  ✗ {e}"); return ""

# ── Сохранение ────────────────────────────────────────────────
def save(niche_id: str, content: str, sources: list[dict]) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    fp = OUTPUT_DIR / f"{niche_id.upper()}_insight.md"
    meta = f"\n\n---\n<!-- ZEREK v2 | {niche_id} | {datetime.now().isoformat()} | {len(sources)} videos\n"
    for s in sources:
        meta += f"  youtube.com/watch?v={s['video_id']} | {s['views']:,} views\n"
    meta += "-->\n"
    fp.write_text(content + meta, encoding="utf-8")
    log.info(f"  💾 {fp}")
    return fp

# ── Пайплайн ─────────────────────────────────────────────────
def process_niche(niche_id: str, config: dict) -> dict:
    niche_id = niche_id.upper()
    niche = config.get(niche_id)
    if not niche:
        return {"status": "error", "niche": niche_id, "msg": "не найдена"}
    name = niche["name"]
    log.info(f"\n{'='*60}\n  {name} ({niche_id})\n{'='*60}")

    log.info("1/4 Поиск")
    vids = search(niche["queries"])
    log.info(f"  Найдено видео: {len(vids)}")
    if not vids: return {"status": "error", "niche": niche_id, "msg": "нет видео"}

    log.info("2/4 Фильтрация")
    good = filt(vids)
    log.info(f"  После фильтра: {len(good)}")
    if not good: return {"status": "error", "niche": niche_id, "msg": "нет подходящих"}

    log.info("3/4 Транскрипты")
    ts = transcripts(good)
    if len(ts) < MIN_VIDEOS:
        log.warning(f"  ⚠ Мало: {len(ts)}/{MIN_VIDEOS}")
        if not ts:
            titles = [v.get("title","?")[:60] for v in good[:5]]
            return {"status": "error", "niche": niche_id,
                    "msg": "нет транскриптов",
                    "found_videos": len(vids), "filtered": len(good),
                    "sample_titles": titles}

    log.info("4/4 Суммаризация")
    text = summarize(ts, name)
    if not text: return {"status": "error", "niche": niche_id, "msg": "Gemini fail"}

    fp = save(niche_id, text, ts)
    log.info(f"  ✅ {name} готово!")
    return {"status": "ok", "niche": niche_id, "name": name, "file": str(fp),
            "found": len(vids), "filtered": len(good), "transcripts": len(ts)}

# ── CLI ───────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="ZEREK Insight Collector v2")
    p.add_argument("--niche", help="ID ниши")
    p.add_argument("--batch", action="store_true", help="Все 33 ниши")
    p.add_argument("--list", action="store_true")
    p.add_argument("--output", help="Папка")
    a = p.parse_args()

    if not YOUTUBE_API_KEY: log.error("Нет YOUTUBE_API_KEY"); sys.exit(1)
    if not GEMINI_API_KEY:  log.error("Нет GEMINI_API_KEY"); sys.exit(1)

    cfg = load_config()
    if a.output:
        global OUTPUT_DIR; OUTPUT_DIR = Path(a.output)

    if a.list:
        print(f"\n{'ID':20s}  Название"); print("-"*55)
        for k,v in sorted(cfg.items()): print(f"  {k:18s}  {v['name']}")
        print(f"\nВсего: {len(cfg)}"); return

    if a.niche:
        r = process_niche(a.niche, cfg)
        print(json.dumps(r, ensure_ascii=False, indent=2)); return

    if a.batch:
        res = []
        for nid in cfg:
            res.append(process_niche(nid, cfg)); time.sleep(2)
        ok = sum(1 for r in res if r["status"]=="ok")
        log.info(f"\n{'='*60}\nИТОГО: {ok}/{len(res)}\n{'='*60}")
        rp = OUTPUT_DIR / "_report.json"
        rp.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        return

    p.print_help()

if __name__ == "__main__":
    main()
