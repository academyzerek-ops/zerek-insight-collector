# ZEREK Insight Collector

YouTube + Gemini pipeline for extracting business insights across 33 niches in Kazakhstan.

## How it works

1. Searches YouTube for niche-specific business videos (Russian language)
2. Extracts transcripts via YouTube Transcript API
3. Sends transcripts to Gemini 2.5 Flash for structured insight extraction
4. Outputs markdown files with actionable business data

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | /collect | Collect insights for one niche |
| POST | /collect-batch | Collect for multiple niches |
| GET | /niches | List all 33 supported niches |
| GET | /status | Check running tasks |
| GET | /results | List generated insight files |
| GET | /results/{niche} | Get specific insight markdown |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| YOUTUBE_API_KEY | Yes | YouTube Data API v3 key |
| GEMINI_API_KEY | Yes | Google Gemini API key |
| OUTPUT_DIR | No | Output directory (default: ./output) |

## Run locally

```bash
pip install -r requirements.txt
YOUTUBE_API_KEY=... GEMINI_API_KEY=... uvicorn server:app --port 8000
```

## Run with Docker

```bash
docker build -t zerek-insight .
docker run -p 8000:8000 -e YOUTUBE_API_KEY=... -e GEMINI_API_KEY=... zerek-insight
```

## CLI

```bash
python collect_insights.py --niche BARBER
python collect_insights.py --batch
python collect_insights.py --list
```
