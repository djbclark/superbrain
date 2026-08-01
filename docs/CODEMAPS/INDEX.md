# SuperBrain Codemap

**Last Updated:** 2026-07-28
**Project Type:** Full-stack Mobile Application (React Native + Python Backend)

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           SUPERBRAIN ARCHITECTURE                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────────┐         ┌──────────────────────────────────────────┐  │
│  │   React Native   │         │              Python Backend              │  │
│  │      Mobile      │         │              (FastAPI)                   │  │
│  │      App         │         │                                          │  │
│  └────────┬─────────┘         └──────────────────┬───────────────────────┘  │
│           │                                      │                          │
│           │            ┌─────────────────────────▼───────────────────────┐  │
│           │            │         SQLite Database (superbrain.db)         │  │
│           │            │   analyses, queue, collections, WebSub state    │  │
│           │            └─────────────────────────┬───────────────────────┘  │
│           │                                      │                          │
│           │            ┌─────────────────────────▼───────────────────────┐  │
│           │            │              AI Services                        │  │
│           │            │   (Groq, Gemini, OpenRouter, Ollama, Whisper)   │  │
│           └───────────►└─────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Key Areas

| Area | Description | Location |
|------|-------------|----------|
| **Frontend** | React Native/Expo mobile app | `superbrain-app/` |
| **Backend** | FastAPI REST API server | `backend/` |
| **Database** | SQLite (`superbrain.db`) with WAL | `backend/core/database.py` |
| **Analyzers** | AI content analysis modules | `backend/analyzers/` |
| **API** | REST endpoints and authentication | `backend/api.py` |
| **Local runtime** | Deploy target for this fork | `~/.superbrain-server` via `superbrain --deploy-local` |

## Directory Structure

```
superbrain/
├── superbrain-app/           # React Native (Expo) Mobile App
├── backend/                  # Python FastAPI Backend
│   ├── api.py
│   ├── main.py               # Content analysis orchestrator
│   ├── core/
│   │   ├── database.py       # SQLite operations
│   │   ├── model_router.py   # AI model routing
│   │   ├── taxonomy.py       # Config-driven category taxonomy
│   │   ├── classifier.py     # Structured category assignment
│   │   ├── category_manager.py  # DEPRECATED (MongoDB-era; do not use)
│   │   ├── link_checker.py
│   │   └── websub_notifier.py
│   ├── analyzers/
│   ├── config/
│   │   ├── categories.toml.example   # Checked-in taxonomy example
│   │   ├── categories.toml           # Local only (gitignored)
│   │   └── .api_keys                 # Local only (gitignored)
│   ├── scripts/
│   │   ├── deploy-local.sh           # Sync code → ~/.superbrain-server
│   │   │                             # (operator: superbrain --deploy-local)
│   │   └── recategorize.py           # Metadata-only taxonomy migration
│   └── tests/
├── superbrain-cli/           # npm wrapper that installs into ~/.superbrain-server
└── docs/
```

## Data Flow

```
User Input (URL)
      │
      ▼
┌─────────────────┐
│  Frontend App   │ ◄─── Access Token Auth (X-API-Key)
│  (React Native) │
└────────┬────────┘
         │ HTTPS (Axios)
         ▼
┌──────────────────────────────────────────┐
│           FastAPI Backend                │
│  /analyze → download/analyze pipeline    │
│  taxonomy classify → analyses.category   │
│  SQLite cache + queue + collections     │
└──────────────────────────────────────────┘
```

## Related Documentation

- [Frontend Codemap](FRONTEND.md)
- [Backend Codemap](BACKEND.md)
- [Database Schema](DATABASE.md)
- [Category taxonomy proposal](../CATEGORY_TAXONOMY_PROPOSAL.md)
