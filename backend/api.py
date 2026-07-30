"""
SuperBrain Instagram Content Analysis API
Version: 1.02
FastAPI REST endpoints for analyzing Instagram content with MongoDB caching
With request queuing, live progress logging, and API key authentication
"""

from fastapi import FastAPI, HTTPException, Query, Header, Depends, Request, UploadFile, File, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, RedirectResponse
from pydantic import BaseModel
from typing import Optional, List
import subprocess
import asyncio
import sys
import os
import json
import zipfile
import io
import hashlib
from datetime import datetime
import logging
import secrets
import string
import threading
import time
import tempfile
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlsplit

# Import database module
from core.database import get_db
from core.link_checker import validate_link

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

def generate_api_token(length=8):
    """Generate an 8-character alphanumeric Access Token."""
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def is_valid_api_token_format(token: str) -> bool:
    """Validate token format: exactly 8 alphanumeric chars."""
    return len(token) == 8 and token.isalnum()

TOKEN_FILE = Path(os.getenv("TOKEN_FILE", str(Path(__file__).parent / "token.txt")))

def load_or_create_api_token():
    """Load existing API token or create one if missing."""
    if TOKEN_FILE.exists():
        content = TOKEN_FILE.read_text(encoding="utf-8", errors="ignore").strip()
        if content and is_valid_api_token_format(content):
            logger.info("🔐 Loaded API access token from token.txt")
            return content

        if content:
            logger.warning("Existing token format is legacy/invalid. Regenerating 8-character Access Token.")

    token = generate_api_token()
    TOKEN_FILE.write_text(token)

    logger.info("🔐 Generated a new API access token in token.txt")
    return token

API_TOKEN = load_or_create_api_token()

async def verify_token(request: Request, x_api_key: str = Header(None, description="Access Token for authentication")):
    """
    Verify authentication using Access Token.
    Can be passed in X-API-Key header or token query parameter.
    """
    actual_token = x_api_key or request.query_params.get("token")
    if not secrets.compare_digest(actual_token or "", API_TOKEN):
        logger.warning("Invalid Access Token attempt from IP: %s", request.client.host if hasattr(request, 'client') and request.client else 'unknown')
        raise HTTPException(
            status_code=401,
            detail="Invalid Access Token. Use the token from backend/token.txt."
        )
    return actual_token

@asynccontextmanager
async def app_lifespan(application: FastAPI):
    """Run one non-blocking WebSub renewal leader across Uvicorn workers."""
    lock_file = _try_acquire_websub_leader_lock()
    renewal_task = None
    if lock_file is not None:
        renewal_task = asyncio.create_task(_websub_renewal_loop())
    try:
        yield
    finally:
        if renewal_task is not None:
            renewal_task.cancel()
            with suppress(asyncio.CancelledError):
                await renewal_task
        _release_websub_leader_lock(lock_file)


# Initialize FastAPI app
app = FastAPI(
    title="SuperBrain",
    description="AI-powered Instagram content analysis with caching",
    version="1.02",
    lifespan=app_lifespan,
)

# CORS configuration
import os

# Get allowed origins from environment variable or allow all for development
allowed_origins_env = os.getenv('ALLOWED_ORIGINS', '')
if allowed_origins_env:
    allowed_origins = [origin.strip() for origin in allowed_origins_env.split(',')]
else:
    # Development: allow all origins so phones on same WiFi can connect
    allowed_origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False if allowed_origins == ["*"] else True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key"],
)

# Security headers middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # CSP - adjust based on your needs
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'"
    return response

# Request queue management (persistent)
max_concurrent = 1  # Process one post at a time - queue others sequentially

# Track active analysis subprocesses so they can be killed on delete
_active_processes: dict = {}        # shortcode -> subprocess.Popen
_active_processes_lock = threading.Lock()

_STATIC_DIR = Path(__file__).parent / "static"
_THUMBNAILS_DIR = _STATIC_DIR / "thumbnails"
_THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)

from fastapi.staticfiles import StaticFiles
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    ico = _STATIC_DIR / "favicon.ico"
    if ico.exists():
        return FileResponse(str(ico), media_type="image/x-icon")
    from fastapi.responses import Response
    return Response(status_code=204)

# Shared Instaloader instance for caption fetching (reuse session to avoid rate limits)
caption_loader = None
caption_loader_lock = threading.Lock()

def get_caption_loader():
    """Get or create shared Instaloader instance for caption fetching"""
    global caption_loader
    with caption_loader_lock:
        if caption_loader is None:
            import instaloader
            caption_loader = instaloader.Instaloader(
                download_pictures=False,
                download_videos=False,
                download_video_thumbnails=False,
                download_geotags=False,
                download_comments=False,
                save_metadata=False,
                compress_json=False,
                max_connection_attempts=1  # Fail fast
            )
        return caption_loader

# Initialize database and recover interrupted items on startup
db = get_db()
if db.is_connected():
    recovered = db.recover_interrupted_items()
    if recovered > 0:
        logger.info(f"🔄 Recovered {recovered} interrupted items from previous session")

# Background worker to process queue
def queue_worker():
    """Background thread that processes queued items automatically"""
    logger.info("🔧 Queue worker thread started")
    _retry_check_counter = 0

    while True:
        try:
            # ── Periodic retry-queue drain (every ~2.5 min) ─────────────────
            _retry_check_counter += 1
            if _retry_check_counter >= 30:
                _retry_check_counter = 0
                recovered = db.recover_interrupted_items()
                if recovered:
                    logger.info(
                        "🔄 Recovered %d stale processing item(s)", recovered
                    )
                ready = db.get_retry_ready()
                if ready:
                    logger.info(f"🔄 Promoting {len(ready)} retry-ready item(s) back to queue")
                    for r_item in ready:
                        logger.info(
                            f"   ↩ {r_item['shortcode']} "
                            f"(reason={r_item['reason']}, attempts={r_item['attempts']})"
                        )
                        db.add_to_queue(r_item['shortcode'], r_item['url'])

            item = db.claim_next_queue_item(max_concurrent=max_concurrent)
            if item:
                queue = db.get_queue()
                shortcode = item['shortcode']
                url = item['url']

                logger.info("📋 Queue alert: Processing next in queue")
                logger.info(
                    f"📊 Queue status: {len(queue)} remaining after this | "
                    f"Starting: {shortcode}"
                )
                logger.info(f"📤 [{shortcode}] Starting analysis from queue...")

                # Run analysis
                try:
                    process = subprocess.Popen(
                        [sys.executable, "main.py", url],
                        cwd=Path(__file__).parent,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding="utf-8",
                        bufsize=1
                    )

                    # Register so delete_post can kill it
                    with _active_processes_lock:
                        _active_processes[shortcode] = process

                    # Wait for completion
                    try:
                        process.wait(timeout=600)
                    except subprocess.TimeoutExpired:
                        logger.error(
                            f"❌ [{shortcode}] Process timed out after 10 minutes. "
                            "Killing..."
                        )
                        process.kill()
                        process.wait()
                        db.remove_from_queue(shortcode)
                        continue

                    with _active_processes_lock:
                        _active_processes.pop(shortcode, None)

                    # If the post was deleted while processing, skip queue cleanup
                    # (delete_post already called remove_from_queue)
                    if process.returncode in (-9, -15):
                        logger.info(
                            f"🛑 [{shortcode}] Analysis killed (post was deleted)"
                        )
                    elif process.returncode == 0:
                        logger.info(f"✅ Queue item completed: {shortcode}")
                        db.remove_from_queue(shortcode)
                    elif process.returncode == 2:
                        # main.py queued this item for retry; status is already in DB.
                        logger.info(
                            f"⏰ [{shortcode}] Quota exhausted — moved to retry queue"
                        )
                    else:
                        logger.error(
                            f"❌ Queue item failed (rc={process.returncode}): "
                            f"{shortcode}"
                        )
                        db.remove_from_queue(shortcode)

                except Exception as e:
                    with _active_processes_lock:
                        _active_processes.pop(shortcode, None)
                    logger.error(f"❌ Error processing queue item {shortcode}: {e}")
                    db.remove_from_queue(shortcode)

            # Sleep before next check
            time.sleep(5)

        except Exception as e:
            logger.error(f"Queue worker error: {e}")
            time.sleep(10)

# Start worker thread
worker_thread = threading.Thread(target=queue_worker, daemon=True)
worker_thread.start()
logger.info("✅ Background queue worker initialized")

# Request/Response models
class AnalyzeRequest(BaseModel):
    url: str
    force: bool = False
    use_youtube_transcripts: bool = False
    transcribe_seconds: int = 0

    class Config:
        json_schema_extra = {
            "example": {
                "url": "https://www.youtube.com/watch?v=Mqr2wO_Vap8",
                "force": False,
                "use_youtube_transcripts": False,
                "transcribe_seconds": 0
            }
        }

class AnalysisResponse(BaseModel):
    success: bool
    cached: bool
    data: Optional[dict] = None
    error: Optional[str] = None
    processing_time: Optional[float] = None


# API Endpoints

@app.get("/")
async def root():
    """API information and health check (no authentication required)"""

    backend_id = "unknown"
    backend_id_path = get_config_path("backend_id.txt")
    if backend_id_path.exists():
        backend_id = backend_id_path.read_text().strip()

    return {
        "backendId": backend_id,

        "name": "SuperBrain Instagram Analyzer API",
        "version": "1.02",
        "status": "operational",
        "authentication": "Required - Use Access Token with X-API-Key header",
        "message": "Run start.py on the server and use the token from token.txt.",
        "endpoints": {
            "POST /analyze": "Analyze content (requires auth)",
            "GET /caption": "Get post caption quickly (requires auth)",
            "GET /cache/{shortcode}": "Check cache (requires auth)",
            "GET /recent": "Get recent analyses (requires auth)",
            "GET /stats": "Database statistics (requires auth)",
            "GET /category/{category}": "Get by category (requires auth)",
            "GET /search": "Search by tags (requires auth)"
        }
    }


@app.get("/caption")
async def get_caption(url: str, token: str = Depends(verify_token)):
    """
    Quick caption fetch - calls caption.py as subprocess
    Simple and works every time
    """
    try:
        logger.info(f"🔍 Quick caption fetch for: {url}")

        # Extract shortcode for logging
        validation = validate_link(url)
        shortcode = validation['shortcode']

        # Run caption.py as subprocess - simple and reliable
        import subprocess
        import sys

        loop = asyncio.get_event_loop()

        def run_caption_script():
            # Use the same Python interpreter as the API (with all packages)
            python_exe = sys.executable
            print(f"[API] Using Python: {python_exe}")

            result = subprocess.run(
                [python_exe, 'analyzers/caption.py', url],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=15,
                cwd=str(Path(__file__).parent)
            )
            print(f"[API] Subprocess stdout: {repr(result.stdout[:200])}")
            print(f"[API] Subprocess stderr: {repr(result.stderr[:200])}")
            print(f"[API] Subprocess returncode: {result.returncode}")
            return result.stdout.strip() if result.stdout else ""

        try:
            caption_text = await asyncio.wait_for(
                loop.run_in_executor(None, run_caption_script),
                timeout=20.0
            )
        except asyncio.TimeoutError:
            logger.error(f"❌ [{shortcode}] Caption fetch timed out")
            return {
                "success": True,
                "shortcode": shortcode,
                "username": "",
                "title": "Instagram Post",
                "full_caption": ""
            }

        print(f"[API] Final caption_text: {repr(caption_text)}")

        # Check if it's an error message
        if caption_text.startswith('❌') or caption_text.startswith('ℹ️'):
            logger.warning(f"⚠️ [{shortcode}] {caption_text}")
            return {
                "success": True,
                "shortcode": shortcode,
                "username": "",
                "title": "Instagram Post",
                "full_caption": ""
            }

        # Limit to 100 chars for title
        title = caption_text[:100] if len(caption_text) > 100 else caption_text
        title = title if title else "Instagram Post"

        logger.info(f"✅ [{shortcode}] Caption: {title}")

        return {
            "success": True,
            "shortcode": shortcode,
            "username": "",
            "title": title,
            "full_caption": caption_text
        }

    except Exception as e:
        logger.error(f"❌ Caption fetch failed: {str(e)}", exc_info=True)
        return {
            "success": True,
            "shortcode": "",
            "username": "",
            "title": "Instagram Post",
            "full_caption": "",
            "error": str(e)
        }


@app.post("/analyze", response_model=AnalysisResponse)
async def analyze_instagram(request: AnalyzeRequest, token: str = Depends(verify_token)):
    """
    Analyze content from URL (Instagram, YouTube, or web page)

    - Checks cache first for instant retrieval
    - If not cached, adds to processing queue
    - Handles multiple concurrent requests with queuing
    - Returns comprehensive summary with title, tags, music, category
    """
    start_time = datetime.now()

    # Detect content type and extract primary key
    try:
        url_str = str(request.url)
        validation = validate_link(url_str)
        if not validation['valid']:
            raise ValueError(validation['error'])
        shortcode    = validation['shortcode']
        content_type = validation['content_type']
        # Use the normalised URL (e.g. canonical YouTube URL)
        url_str = validation['url']
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid URL: {str(e)}")

    logger.info(f"📥 New request: {shortcode}")

    # Initialize database connection
    db = get_db()

    try:
        # Step 1: Check database cache first
        logger.info(f"🔍 [{shortcode}] Checking database cache...")
        cached_result = db.check_cache(shortcode)

        if cached_result:
            # Force re-analyze: hard-delete existing record and proceed with fresh analysis
            if request.force:
                logger.info(f"🔄 [{shortcode}] Force re-analyze requested — clearing cached data")
                db.hard_delete_post(shortcode)
                cached_result = None  # fall through to fresh analysis
            # Restore soft-deleted post if user is re-adding it
            elif cached_result.get('is_hidden') == 1:
                db.restore_post(shortcode)
                cached_result['is_hidden'] = 0
                logger.info(f"♻️ [{shortcode}] Restored from soft-delete. Returning cached data.")
            else:
                logger.info(f"⚡ [{shortcode}] Found in cache! Returning instantly.")

        if cached_result:
            title_cached = cached_result.get('title', '')
            if not title_cached or title_cached.strip() == "":
                from analyzers.youtube_analyzer import get_youtube_title
                title_cached = get_youtube_title(cached_result.get('url', ''))
                if title_cached:
                    try:
                        db._conn.execute("UPDATE analyses SET title = ? WHERE shortcode = ?", (title_cached, shortcode))
                        db._conn.commit()
                    except Exception:
                        pass

            # Filter response
            filtered_data = {
                'url': cached_result.get('url', ''),
                'username': cached_result.get('username', ''),
                'content_type': cached_result.get('content_type', content_type),
                'thumbnail': cached_result.get('thumbnail', ''),
                'title': title_cached,
                'summary': cached_result.get('summary', ''),
                'tags': cached_result.get('tags', []),
                'music': cached_result.get('music', ''),
                'category': cached_result.get('category', ''),
                'transcript_mode': cached_result.get('transcript_mode', 'native')
            }

            processing_time = (datetime.now() - start_time).total_seconds()
            logger.info(f"✅ [{shortcode}] Response sent ({processing_time:.2f}s)")

            return AnalysisResponse(
                success=True,
                cached=True,
                data=filtered_data,
                processing_time=processing_time
            )

        # Step 2: Not in cache - check if already being processed
        logger.info(f"💾 [{shortcode}] Not in cache")

        # Check if already in queue or processing
        processing_items = db.get_processing()
        if shortcode in processing_items:
            logger.warning(f"⏳ [{shortcode}] Already being processed. Please wait...")
            raise HTTPException(
                status_code=409,
                detail="This URL is already being analyzed. Please wait and try again in a moment."
            )

        # Check if URL is already in queue - if so, remove the old queued item
        queue_items = db.get_queue()
        for item in queue_items:
            if item['shortcode'] == shortcode:
                logger.info(f"🔄 [{shortcode}] Duplicate found in queue - removing old entry and processing fresh request")
                db.remove_from_queue(shortcode)
                break

        # Step 3: Check queue size (re-fetch after potential duplicate removal)
        queue_items = db.get_queue()
        if len(processing_items) >= max_concurrent:
            logger.warning(f"🚦 [{shortcode}] Server busy - 1 post analyzing. Adding to queue...")
            queue_position = db.add_to_queue(shortcode, request.url)
            if queue_position > 0:
                logger.info(f"📝 [{shortcode}] ✅ Added to queue at position {queue_position}")
                logger.info(f"📊 Queue status: {len(queue_items) + 1} waiting | 1 analyzing")
                raise HTTPException(
                    status_code=503,
                    detail=f"Server busy analyzing 1 post. Your request is queued (position: {queue_position}). It will be processed automatically. Check back in a few minutes."
                )
            else:
                raise HTTPException(
                    status_code=500,
                    detail="Failed to add to queue. Please try again."
                )

        # Step 4: Start processing
        if len(queue_items) > 0:
            logger.info(f"📊 Queue status: {len(queue_items)} waiting | Starting: {shortcode}")
        logger.info(f"🚀 [{shortcode}] Starting analysis...")
        db.mark_processing(shortcode)

        # Run main.py as subprocess — executed in a thread pool so the asyncio
        # event loop stays free to serve /ping and other requests during analysis.
        logger.info(f"📊 [{shortcode}] Phase 1: Downloading content...")

        def _run_subprocess() -> tuple:
            cmd = [sys.executable, "main.py", url_str]
            if request.use_youtube_transcripts:
                cmd.append("--youtube-transcripts")
            if request.transcribe_seconds and request.transcribe_seconds > 0:
                cmd.extend(["--transcribe-seconds", str(request.transcribe_seconds)])
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
                cwd=str(Path(__file__).parent),
                bufsize=1
            )
            with _active_processes_lock:
                _active_processes[shortcode] = proc
            lines = []
            for line in proc.stdout:
                lines.append(line)
                lc = line.strip()
                if "[TRANSCRIPT] Used existing YouTube transcript" in lc:
                    logger.info(f"📜 [{shortcode}] Transcript: Using native YouTube transcript")
                elif "[TRANSCRIPT] No native transcript found" in lc:
                    logger.info(f"🎙️ [{shortcode}] Transcript: Using 60s Whisper audio transcription")
                elif "Step 4: Visual Analysis" in lc:
                    logger.info(f"🎬 [{shortcode}] Phase 2: Visual analysis (AI processing)...")
                elif "Step 5: Audio Transcription" in lc or "Phase 2: Audio" in lc:
                    logger.info(f"🎙️ [{shortcode}] Phase 3: Audio transcription (Whisper)...")
                elif "Phase 3: Light Tasks" in lc:
                    logger.info(f"⚡ [{shortcode}] Phase 4: Music ID + Text (parallel)...")
                elif "GENERATING COMPREHENSIVE SUMMARY" in lc:
                    logger.info(f"🧠 [{shortcode}] Phase 5: Generating AI summary...")
                elif "Saving to Database" in lc:
                    logger.info(f"💾 [{shortcode}] Phase 6: Saving to database...")
                elif "Cleaned up temp folder" in lc:
                    logger.info(f"🗑️ [{shortcode}] Phase 7: Cleanup complete")
            proc.wait()
            with _active_processes_lock:
                _active_processes.pop(shortcode, None)
            return proc.returncode, ''.join(lines), proc.stderr.read()

        try:
            returncode, stdout, stderr = await asyncio.wait_for(
                asyncio.to_thread(_run_subprocess),
                timeout=600
            )
        except asyncio.TimeoutError:
            logger.error(f"❌ [{shortcode}] Analysis reached 10-minute timeout. Forcing termination.")
            with _active_processes_lock:
                proc = _active_processes.pop(shortcode, None)
            if proc:
                try:
                    proc.kill()
                    proc.wait()
                except Exception:
                    pass
            db.remove_from_queue(shortcode)
            raise HTTPException(
                status_code=504,
                detail="Analysis timed out. The process took longer than the 10-minute allowed maximum."
            )

        if stderr.strip():
            # Log stderr from main.py to help diagnose issues
            logger.warning(f"⚠️  [{shortcode}] main.py stderr:\n{stderr[:1000]}")

        if returncode == 2:
            # main.py detected quota exhaustion (Instagram block or AI) and queued item for retry.
            # NOTE: Do NOT remove from queue here — main.py already called
            # queue_for_retry() which set status='retry'. Removing would lose it.
            logger.info(f"⏰ [{shortcode}] Rate limit or quota exhausted (often Instagram blocking the download). Your request has been queued for automatic retry in 24 hours.")
            raise HTTPException(
                status_code=202,
                detail="Rate limit or quota exhausted (often Instagram blocking the download). Your request has been queued for automatic retry in 24 hours."
            )

        if returncode != 0:
            # Extract last meaningful error line from stdout for the error message
            error_lines = [l.strip() for l in stdout.splitlines() if l.strip() and ('❌' in l or 'Error' in l or 'failed' in l.lower())]
            error_detail = error_lines[-1] if error_lines else (stderr.strip()[:200] or "Analysis failed")
            logger.error(f"❌ [{shortcode}] Analysis failed: {error_detail}")
            logger.debug(f"[{shortcode}] stdout tail:\n{stdout[-800:]}")
            raise HTTPException(
                status_code=400,
                detail=error_detail
            )

        logger.info(f"✅ [{shortcode}] Analysis complete! Fetching from database...")

        # Get result from database — retry up to 4 times in case the SQLite write
        # hasn't flushed yet (race condition between subprocess write and our read).
        analysis = None
        for _attempt in range(4):
            analysis = db.check_cache(shortcode)
            if analysis:
                if _attempt > 0:
                    logger.info(f"🔄 [{shortcode}] Found in database on retry {_attempt}")
                break
            if _attempt < 3:
                logger.warning(f"⏳ [{shortcode}] Not in DB yet (attempt {_attempt+1}/4), retrying in 1s…")
                await asyncio.sleep(1)

        if not analysis:
            logger.error(f"❌ [{shortcode}] Not found in database after 4 attempts!")
            raise HTTPException(
                status_code=500,
                detail="Analysis completed but result not found in database"
            )

        t_mode = analysis.get("transcript_mode", "")

        title_val = analysis.get('title', '')
        if not title_val or title_val.strip() == "":
            from analyzers.youtube_analyzer import get_youtube_title
            title_val = get_youtube_title(analysis.get('url', ''))
            if title_val:
                try:
                    db._conn.execute("UPDATE analyses SET title = ? WHERE shortcode = ?", (title_val, shortcode))
                    db._conn.commit()
                except Exception:
                    pass

        # Filter response
        filtered_data = {
            'url': analysis.get('url', ''),
            'username': analysis.get('username', ''),
            'content_type': analysis.get('content_type', content_type),
            'thumbnail': analysis.get('thumbnail', ''),
            'title': title_val,
            'summary': analysis.get('summary', ''),
            'tags': analysis.get('tags', []),
            'music': analysis.get('music', ''),
            'category': analysis.get('category', ''),
            'transcript_mode': t_mode
        }

        processing_time = (datetime.now() - start_time).total_seconds()
        logger.info(f"✅ [{shortcode}] Response sent ({processing_time:.2f}s total)")

        # Remove from processing queue
        db.remove_from_queue(shortcode)
        logger.info(f"🔓 [{shortcode}] Released from processing queue")

        return AnalysisResponse(
            success=True,
            cached=False,
            data=filtered_data,
            processing_time=processing_time
        )

    except HTTPException as he:
        # Don't remove from queue for 202 (retry-queued) — the item was
        # intentionally kept in the retry queue by main.py.
        if he.status_code != 202:
            db.remove_from_queue(shortcode)
        raise
    except subprocess.SubprocessError as e:
        logger.error(f"❌ [{shortcode}] Subprocess error: {str(e)}")
        db.remove_from_queue(shortcode)
        raise HTTPException(status_code=500, detail=f"Processing error: {str(e)}")
    except Exception as e:
        logger.error(f"❌ [{shortcode}] Unexpected error: {str(e)}")
        db.remove_from_queue(shortcode)
        raise HTTPException(status_code=500, detail=f"Server error: {str(e)}")


@app.get("/cache/{shortcode}")
async def check_cache(shortcode: str, token: str = Depends(verify_token)):
    """
    Check if Instagram post is already analyzed and cached

    - Returns cached analysis if available
    - Returns 404 if not found
    - Requires API authentication
    """
    try:
        db = get_db()
        result = db.check_cache(shortcode)

        if not result:
            raise HTTPException(status_code=404, detail="Not found in cache")

        # Filter response to only include essential fields
        filtered_data = {
            'url': result.get('url', ''),
            'username': result.get('username', ''),
            'title': result.get('title', ''),
            'summary': result.get('summary', ''),
            'tags': result.get('tags', []),
            'music': result.get('music', ''),
            'category': result.get('category', '')
        }

        return {
            "success": True,
            "cached": True,
            "data": filtered_data
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/recent")
async def get_recent_analyses(
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    token: str = Depends(verify_token),
):
    """
    Get recent analyses from database (lightweight — no analysis blobs)

    - Returns most recently analyzed content with UI-essential fields only
    - Supports pagination via offset
    - Default limit: 50, max: 1000
    - Requires API authentication
    """
    try:
        db = get_db()
        results = db.get_recent_light(limit=limit, offset=offset)
        total = db.get_total_count()

        return {
            "success": True,
            "count": len(results),
            "total": total,
            "offset": offset,
            "limit": limit,
            "next_offset": offset + len(results) if offset + len(results) < total else None,
            "has_more": offset + len(results) < total,
            "data": results
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sync")
async def sync_posts(
    since: str = Query(..., description="ISO timestamp — return posts updated after this time"),
    limit: int = Query(default=500, ge=1, le=1000),
    offset: int = Query(default=0, ge=0, description="Number of matching rows to skip"),
    token: str = Depends(verify_token),
):
    """
    Delta sync endpoint — returns posts modified after the given timestamp.
    Used by the mobile app to incrementally update its local SQLite database.
    Only returns lightweight fields (no analysis blobs).
    """
    try:
        db = get_db()
        results = db.get_posts_since(since, limit=limit, offset=offset)

        return {
            "success": True,
            "count": len(results),
            "since": since,
            "offset": offset,
            "next_offset": offset + len(results) if len(results) == limit else None,
            "has_more": len(results) == limit,
            "data": results
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sync/deleted")
async def sync_deleted(
    since: str = Query(..., description="ISO timestamp — return posts deleted after this time"),
    token: str = Depends(verify_token),
):
    """
    Returns shortcodes of posts that were soft-deleted after the given timestamp.
    The mobile app uses this to remove posts from its local database.
    """
    try:
        db = get_db()
        results = db.get_deleted_since(since)

        return {
            "success": True,
            "count": len(results),
            "since": since,
            "data": results
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stats")
async def get_database_stats(token: str = Depends(verify_token)):
    """
    Get database statistics

    - Total documents
    - Storage usage
    - Category breakdown
    - Capacity information
    - Requires API authentication
    """
    try:
        db = get_db()
        stats = db.get_stats()

        return {
            "success": True,
            "data": stats
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/categories")
async def get_all_categories(token: str = Depends(verify_token)):
    """
    Get all categories with post counts
    - Requires API authentication
    """
    try:
        db = get_db()
        stats = db.get_stats()
        category_counts = stats.get('categories', {})

        # Convert to list format with icons
        categories = []
        for cat, count in sorted(category_counts.items(), key=lambda x: -x[1]):
            categories.append({
                'id': cat.lower(),
                'name': cat,
                'count': count
            })

        return {
            "success": True,
            "categories": categories,
            "total": sum(c['count'] for c in categories)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/taxonomy")
async def get_taxonomy_config(token: str = Depends(verify_token)):
    """
    Return the effective configured category taxonomy.

    Used by mobile clients to render filter/edit options even when some
    categories currently have zero assigned posts.
    """
    try:
        from core.taxonomy import get_taxonomy

        tax = get_taxonomy()
        return {
            "success": True,
            "use_default_categories": tax.use_default_categories,
            "allow_multiple_categories": tax.allow_multiple_categories,
            "fallback_category": tax.fallback_category,
            "confidence_threshold": tax.confidence_threshold,
            "taxonomy_version": tax.version,
            "categories": [
                {
                    "id": c.name.lower(),
                    "name": c.name,
                    "precedence": c.precedence,
                    "guidance": c.guidance,
                    "source": c.source,
                }
                for c in sorted(tax.categories, key=lambda x: x.precedence)
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/category/{category}")
async def get_by_category(
    category: str,
    limit: int = Query(default=20, ge=1, le=100),
    token: str = Depends(verify_token)
):
    """
    Get analyses by category.

    Category names come from the configured user taxonomy
    (`config/categories.toml`) and/or historically assigned values still
    present in the database.
    - Requires API authentication
    """
    try:
        db = get_db()
        results = db.get_by_category(category, limit=limit)
        # Case-insensitive fallback when the path uses a lowercase id.
        if not results and category != category.lower():
            results = db.get_by_category(category.lower(), limit=limit)
        if not results:
            # Match configured display name when client sends lowercase id.
            from core.taxonomy import get_taxonomy
            resolved = get_taxonomy().resolve_name(category)
            if resolved and resolved != category:
                results = db.get_by_category(resolved, limit=limit)

        return {
            "success": True,
            "category": category,
            "count": len(results),
            "data": results
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/search")
async def search_by_tags(
    tags: str = Query(..., description="Comma-separated tags to search"),
    limit: int = Query(default=20, ge=1, le=100),
    token: str = Depends(verify_token)
):
    """
    Search analyses by tags

    - Provide comma-separated tags
    - Example: travel,sikkim,budget
    - Requires API authentication
    """
    try:
        tag_list = [tag.strip() for tag in tags.split(',')]

        db = get_db()
        results = db.search_tags(tag_list, limit=limit)

        return {
            "success": True,
            "tags": tag_list,
            "count": len(results),
            "data": results
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ping")
async def ping():
    """Ultra-lightweight liveness check — no DB, no auth, instant response."""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.get("/status")
async def status():
    """
    Server status check - no auth required.
    """
    return {
        "status": "online",
        "version": "2.0.0",
        "message": "Server is running. Configure app with server URL and Access Token from token.txt."
    }


@app.get("/connect-info")
async def connect_info(
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """
    Returns connection details for QR code scanning.
    Used by the mobile app to auto-fill settings.
    The URL is built from the request so it matches whatever address the client used.

    Returning the bearer token to an unauthenticated network caller defeats all
    other API authorization. Legacy unauthenticated QR pairing can be explicitly
    enabled only on trusted networks with ALLOW_UNAUTHENTICATED_CONNECT_INFO=1.
    """
    allow_unauthenticated = os.getenv(
        "ALLOW_UNAUTHENTICATED_CONNECT_INFO", ""
    ).strip().lower() in {"1", "true", "yes"}
    if not allow_unauthenticated and not secrets.compare_digest(
        x_api_key or "", API_TOKEN
    ):
        raise HTTPException(
            status_code=401,
            detail="X-API-Key is required to retrieve connection credentials",
        )

    # Build the base URL from the incoming request
    scheme = request.headers.get('x-forwarded-proto', request.url.scheme)
    host = request.headers.get('x-forwarded-host', request.headers.get('host', 'localhost:5000'))
    base_url = f"{scheme}://{host}"

    return JSONResponse(
        content={
            "url": base_url,
            "token": API_TOKEN,
            "version": "2.0.0",
            "name": "SuperBrain",
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/analysis-status/{shortcode}")
async def analysis_status(shortcode: str, token: str = Depends(verify_token)):
    """
    Check if a post has been analyzed yet.
    Returns status: 'complete', 'processing', 'queued', or 'not_found'.
    Used by the app to poll for completion after sharing a URL.
    """
    try:
        db = get_db()

        # Check if fully analyzed
        cached = db.check_cache(shortcode)
        if cached:
            return {
                "status": "complete",
                "shortcode": shortcode,
                "title": cached.get('title', ''),
                "category": cached.get('category', ''),
                "data": {
                    'title': cached.get('title', ''),
                    'summary': cached.get('summary', ''),
                    'tags': cached.get('tags', []),
                    'category': cached.get('category', ''),
                    'content_type': cached.get('content_type', ''),
                    'thumbnail': cached.get('thumbnail', ''),
                }
            }

        # Check if currently processing
        processing = db.get_processing()
        if shortcode in processing:
            return {"status": "processing", "shortcode": shortcode}

        # Check if in queue
        queue = db.get_queue()
        for i, item in enumerate(queue):
            if item['shortcode'] == shortcode:
                return {"status": "queued", "shortcode": shortcode, "position": i + 1}

        return {"status": "not_found", "shortcode": shortcode}

    except Exception as e:
        logger.error(f"Error checking analysis status for {shortcode}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class ConnectRequest(BaseModel):
    """Request model for deprecated /connect endpoint."""
    api_key: Optional[str] = None


@app.post("/connect")
async def connect(request: ConnectRequest):
    """
    Deprecated endpoint kept for backward compatibility.
    Use direct Server URL + API key configuration in app settings.
    """
    raise HTTPException(
        status_code=410,
        detail="Deprecated. Configure Server URL and Access Token directly in app Settings."
    )


# ─────────────────────────────────────────────────────────────────
# Collections endpoints
# ──────────────────────────────────────────────────────────────────

class CollectionUpsertRequest(BaseModel):
    id: str
    name: str
    icon: str = "📁"
    post_ids: List[str] = []
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class CollectionPostsRequest(BaseModel):
    post_ids: List[str]


# Allowed fields for import validation
ALLOWED_POST_FIELDS = {
    'shortcode', 'url', 'username', 'content_type', 'post_date',
    'likes', 'thumbnail', 'title', 'summary', 'tags', 'music', 'category',
    'visual_analysis', 'audio_transcription', 'text_analysis'
}

class ImportData(BaseModel):
    version: Optional[str] = None
    posts: List[dict] = []
    collections: List[dict] = []


@app.get("/collections")
async def get_collections(token: str = Depends(verify_token)):
    """Return all collections stored on the server."""
    try:
        db = get_db()
        return {"success": True, "data": db.get_collections()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/collections")
async def upsert_collection(req: CollectionUpsertRequest, token: str = Depends(verify_token)):
    """Create or fully replace a collection (upsert by id)."""
    try:
        db = get_db()
        saved = db.upsert_collection(
            req.id, req.name, req.icon, req.post_ids,
            req.created_at, req.updated_at
        )
        if saved:
            return {"success": True, "data": saved}
        raise HTTPException(status_code=500, detail="Failed to save collection")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/collections/{collection_id}/posts")
async def update_collection_posts(collection_id: str, req: CollectionPostsRequest,
                                   token: str = Depends(verify_token)):
    """Replace the post_ids list for a collection."""
    try:
        db = get_db()
        ok = db.update_collection_posts(collection_id, req.post_ids)
        if ok:
            return {"success": True, "data": db.get_collection(collection_id)}
        raise HTTPException(status_code=404, detail="Collection not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/collections/{collection_id}")
async def delete_collection(collection_id: str, token: str = Depends(verify_token)):
    """Delete a collection by id. The default Watch Later cannot be deleted."""
    if collection_id == "default_watch_later":
        raise HTTPException(status_code=403, detail="Cannot delete the default Watch Later collection")
    try:
        db = get_db()
        ok = db.delete_collection(collection_id)
        if ok:
            return {"success": True, "message": "Collection deleted"}
        raise HTTPException(status_code=404, detail="Collection not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check(token: str = Depends(verify_token)):
    """API health check with database connectivity test (requires auth)"""
    try:
        db = get_db()
        stats = db.get_stats()

        return {
            "status": "healthy",
            "database": "connected",
            "documents": stats.get('document_count', 0),
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        return {
            "status": "unhealthy",
            "database": "disconnected",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


@app.get("/queue-status")
async def queue_status(token: str = Depends(verify_token)):
    """Get current queue and processing status"""
    try:
        db = get_db()
        processing = db.get_processing()
        queue = db.get_queue()

        retry_queue = db.get_retry_queue()
        return {
            "currently_processing": processing,
            "processing_count": len(processing),
            "queue": queue,
            "queue_count": len(queue),
            "retry_queue": retry_queue,
            "retry_count": len(retry_queue),
            "max_concurrent": max_concurrent,
            "available_slots": max(0, max_concurrent - len(processing))
        }
    except Exception as e:
        logger.error(f"Error getting queue status: {e}")
        return {
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


@app.delete("/post/{shortcode}")
async def delete_post(shortcode: str, token: str = Depends(verify_token)):
    """Delete a post by shortcode, killing any active analysis subprocess"""
    try:
        db = get_db()

        # Kill active analysis subprocess if this post is currently being processed
        with _active_processes_lock:
            proc = _active_processes.pop(shortcode, None)
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            logger.info(f"🛑 Killed active analysis for: {shortcode}")

        # Remove from queue (handles both 'queued' and 'processing' states)
        db.remove_from_queue(shortcode)

        result = db.delete_post(shortcode)

        if result:
            logger.info(f"✅ Deleted post: {shortcode}")
            return {
                "success": True,
                "message": "Post deleted successfully",
                "shortcode": shortcode
            }
        else:
            raise HTTPException(status_code=404, detail="Post not found")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting post: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/post/{shortcode}")
async def update_post(shortcode: str, updates: dict, token: str = Depends(verify_token)):
    """Update a post's category, title, or summary"""
    try:
        db = get_db()

        # Only allow specific fields to be updated
        allowed_fields = {'category', 'title', 'summary'}
        filtered_updates = {k: v for k, v in updates.items() if k in allowed_fields}

        if not filtered_updates:
            raise HTTPException(status_code=400, detail="No valid fields to update")

        result = db.update_post(shortcode, filtered_updates)

        if result:
            logger.info(f"✅ Updated post: {shortcode} - {list(filtered_updates.keys())}")
            return {
                "success": True,
                "message": "Post updated successfully",
                "shortcode": shortcode,
                "updated_fields": list(filtered_updates.keys())
            }
        else:
            raise HTTPException(status_code=404, detail="Post not found")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating post: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/queue/retry")
async def get_retry_queue(token: str = Depends(verify_token)):
    """Show all items currently scheduled for automatic retry"""
    try:
        items = db.get_retry_queue()
        return {
            "retry_queue": items,
            "count": len(items)
        }
    except Exception as e:
        logger.error(f"Error fetching retry queue: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/queue/retry/flush")
async def flush_retry_queue(token: str = Depends(verify_token)):
    """Immediately promote all retry-ready items to the active queue"""
    try:
        db = get_db()
        ready = db.get_retry_ready()
        for item in ready:
            db.add_to_queue(item['shortcode'], item['url'])
            logger.info(f"🔄 Flushed retry item: {item['shortcode']} ({item['reason']})")
        return {
            "flushed": len(ready),
            "items": [i['shortcode'] for i in ready]
        }
    except Exception as e:
        logger.error(f"Error flushing retry queue: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/queue/{shortcode}")
async def delete_queue_item(shortcode: str, token: str = Depends(verify_token)):
    """Remove an item from the processing or retry queue"""
    try:
        db = get_db()
        db.remove_from_queue(shortcode)
        return {"success": True, "message": f"Removed {shortcode} from queue"}
    except Exception as e:
        logger.error(f"Error removing {shortcode} from queue: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────
# Reset endpoints (admin only)
# ─────────────────────────────────────────────────────────────────

@app.post("/reset/api-token")
async def reset_api_token(token: str = Depends(verify_token)):
    """
    Reset the API token. A new token will be generated.
    - Requires API authentication
    - Returns the new token
    """
    global API_TOKEN
    try:
        # Generate new 8-character alphanumeric token
        new_token = generate_api_token()

        # Save to file
        TOKEN_FILE.write_text(new_token)

        # Update in-memory token so it takes effect immediately
        API_TOKEN = new_token

        logger.warning("API token was reset by a client")

        return {
            "success": True,
            "new_token": new_token,
            "message": "API token has been reset. Update this token in your mobile app settings."
        }
    except Exception as e:
        logger.error(f"Error resetting API token: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/reset/database")
async def reset_database(
    token: str = Depends(verify_token),
    confirm: str = Body(..., description="Must be 'DELETE_ALL' to confirm")
):
    """
    Reset (clear) the database. This will delete all posts and collections.
    - Requires API authentication
    - Requires confirm='DELETE_ALL' in body
    """
    if confirm != "DELETE_ALL":
        raise HTTPException(status_code=400, detail="Confirmation required: pass confirm='DELETE_ALL'")

    try:
        db = get_db()

        # Delete all posts/collections/queue entries in SQLite
        conn = db._conn
        cur_posts = conn.execute("DELETE FROM analyses")
        conn.execute("DELETE FROM collections")
        conn.execute("DELETE FROM processing_queue")

        # Recreate default Watch Later collection
        now = datetime.utcnow().isoformat()
        conn.execute(
            "INSERT INTO collections (id, name, icon, post_ids, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ('default_watch_later', 'Watch Later', 'time', '[]', now, now)
        )
        conn.commit()

        deleted_count = cur_posts.rowcount

        logger.warning(f"🗑️ Database was reset by a client. Deleted {deleted_count} posts.")

        return {
            "success": True,
            "deleted_count": deleted_count,
            "message": f"Database cleared. {deleted_count} posts deleted."
        }
    except Exception as e:
        logger.error(f"Error resetting database: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────
# Import/Export endpoints
# ─────────────────────────────────────────────────────────────────

@app.get("/export")
async def export_data(
    background_tasks: __import__('fastapi').BackgroundTasks,
    token: str = Depends(verify_token),
    limit: int = Query(default=10000, ge=1, le=50000, description="Max posts to export"),
    offset: int = Query(default=0, ge=0, description="Offset for pagination"),
    format: str = Query(default="json", description="Export format: json or zip")
):
    """
    Export data as JSON or ZIP (posts, collections, settings).
    - Requires API authentication
    - Supports pagination with limit and offset
    """
    import tempfile
    import os
    import httpx

    try:
        db = get_db()

        # Get posts with pagination using SQLite
        posts = db.get_all_posts(limit=limit, offset=offset)
        # Convert sqlite3.Row objects to fully mutable dictionaries
        posts_list = [dict(p) for p in posts]

        collections = db.get_all_collections()
        stats = db.get_stats()

        export_payload = {
            "version": "1.0",
            "exported_at": datetime.now().isoformat(),
            "posts": posts_list,
            "collections": collections,
            "stats": stats
        }

        if format.lower() == "zip":
            # Create a zip file on disk to avoid memory explosion
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
            zip_path = tmp.name
            tmp.close()

            async def download_image(client, sem, post_dict):
                url = post_dict.get('thumbnail_url') or post_dict.get('thumbnail')
                if not url:
                    return None

                shortcode = post_dict.get('shortcode')
                if not shortcode: return None

                if url.startswith("/static/"):
                    local_path = url.replace("/static/", "", 1)
                    full_path = _STATIC_DIR / local_path
                    try:
                        if full_path.exists():
                            with open(full_path, "rb") as f:
                                return (local_path, f.read())
                    except Exception as e:
                        logger.error(f"Failed bundling local image {url}: {e}")
                    return None

                # Handle base64 encoded data URIs commonly saved by main.py
                if url.startswith("data:image/"):
                    try:
                        import base64
                        header, encoded = url.split(',', 1)
                        ext = "jpg"
                        if "png" in header.lower(): ext = "png"
                        elif "webp" in header.lower(): ext = "webp"

                        img_data = base64.b64decode(encoded)
                        path_in_zip = f"thumbnails/{shortcode}.{ext}"
                        post_dict['thumbnail'] = f"/static/{path_in_zip}"
                        return (path_in_zip, img_data)
                    except Exception as e:
                        logger.error(f"Failed decoding base64 image for {shortcode}: {e}")
                        return None

                ext = "jpg"
                if ".png" in url.lower(): ext = "png"
                elif ".webp" in url.lower(): ext = "webp"

                path_in_zip = f"thumbnails/{shortcode}.{ext}"
                try:
                    async with sem:
                        resp = await client.get(url, follow_redirects=True, timeout=12.0)
                        if resp.status_code == 200:
                            post_dict['thumbnail'] = f"/static/{path_in_zip}"
                            return (path_in_zip, resp.content)
                except Exception as e:
                    logger.warning(f"Failed to fetch thumbnail for {shortcode}: {e}")
                return None

            # Fetch all thumbnails concurrently in batches
            sem = asyncio.Semaphore(15)
            tasks = []
            async with httpx.AsyncClient(verify=False) as client:
                for post in export_payload["posts"]:
                    tasks.append(download_image(client, sem, post))

                downloaded_images = await asyncio.gather(*tasks)

            # Write everything to the zip sequentially to save RAM
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
                # The export_payload now has updated 'thumbnail' fields pointing locally
                zip_file.writestr("superbrain_export.json", json.dumps(export_payload, default=str))
                for res in downloaded_images:
                    if res:
                        path_in_zip, content = res
                        zip_file.writestr(path_in_zip, content)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"superbrain_export_{timestamp}.zip"

            background_tasks.add_task(os.remove, zip_path)
            return FileResponse(zip_path, media_type="application/zip", filename=filename)

        # Default JSON response
        return export_payload
    except Exception as e:
        logger.error(f"Error exporting data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/import")
async def import_data(
    data: dict,
    token: str = Depends(verify_token),
    mode: str = Query(default="merge", description="merge or replace")
):
    """
    Import data from JSON.
    - Requires API authentication
    - mode=merge: Add to existing data (skip duplicates by shortcode)
    - mode=replace: Replace all data (will clear database first)
    """
    return await _process_import_data(data, mode)

@app.post("/import/file")
async def import_data_file(
    file: UploadFile = File(...),
    token: str = Depends(verify_token),
    mode: str = Query(default="merge", description="merge or replace")
):
    """
    Import data from a ZIP or JSON file.
    - Requires API authentication
    - mode=merge or replace
    - Extracts thumbnails from ZIP archive if present
    """
    try:
        content = await file.read()

        # Check if it's a ZIP file
        if file.filename.endswith('.zip') or file.content_type == 'application/zip' or content.startswith(b'PK'):
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as z:
                    # Look for superbrain_export.json or any json file
                    json_files = [name for name in z.namelist() if name.endswith('.json')]

                    if not json_files:
                        raise HTTPException(status_code=400, detail="No JSON file found in the ZIP archive")

                    # Prefer superbrain_export.json if it exists
                    target_file = "superbrain_export.json" if "superbrain_export.json" in json_files else json_files[0]

                    with z.open(target_file) as f:
                        data = json.load(f)

                    # Extract any custom image thumbnails from /thumbnails directory to the static folder mapped statically
                    thumbnails_dir = _STATIC_DIR / "thumbnails"
                    thumbnails_dir.mkdir(parents=True, exist_ok=True)

                    for name in z.namelist():
                        if name.startswith("thumbnails/") and not name.endswith("/"):
                            img_filename = name.split("/")[-1]
                            with z.open(name) as zf, open(thumbnails_dir / img_filename, "wb") as out_f:
                                out_f.write(zf.read())

            except zipfile.BadZipFile:
                raise HTTPException(status_code=400, detail="Invalid ZIP file")
        else:
            # Assume it's a direct JSON file
            try:
                data = json.loads(content.decode('utf-8'))
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid JSON file")

        return await _process_import_data(data, mode)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing import file: {e}")
        raise HTTPException(status_code=500, detail=f"Error processing file: {str(e)}")

async def _process_import_data(data: dict, mode: str):
    try:
        # Validate input data structure
        validated = ImportData.model_validate(data)

        if mode not in {"merge", "replace"}:
            raise HTTPException(status_code=400, detail="Invalid import mode. Use 'merge' or 'replace'.")

        db = get_db()

        imported_posts = 0
        skipped_posts = 0

        # Handle mode=replace
        if mode == "replace":
            logger.warning("Import mode=replace: clearing database first")
            conn = db._conn
            conn.execute("DELETE FROM analyses")
            conn.execute("DELETE FROM collections")
            conn.execute("DELETE FROM processing_queue")
            # Ensure default Watch Later exists even if import has no collections
            now = datetime.utcnow().isoformat()
            conn.execute(
                "INSERT INTO collections (id, name, icon, post_ids, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                ('default_watch_later', 'Watch Later', 'time', '[]', now, now)
            )
            conn.commit()

        # Import posts - validate and filter allowed fields
        posts = validated.posts or []
        for post in posts:
            shortcode = post.get("shortcode")
            if not shortcode:
                continue

            # Filter to only allowed fields (prevent arbitrary field injection)
            filtered_post = {k: v for k, v in post.items() if k in ALLOWED_POST_FIELDS}

            # Check if exists (for merge mode)
            existing = db.check_cache(shortcode)
            if existing and mode == "merge":
                skipped_posts += 1
                continue

            db.save_analysis(
                shortcode=shortcode,
                url=filtered_post.get("url", ""),
                username=filtered_post.get("username", ""),
                title=filtered_post.get("title", ""),
                summary=filtered_post.get("summary", ""),
                tags=filtered_post.get("tags", []),
                music=filtered_post.get("music", ""),
                category=filtered_post.get("category", "other"),
                visual_analysis=filtered_post.get("visual_analysis", ""),
                audio_transcription=filtered_post.get("audio_transcription", ""),
                text_analysis=filtered_post.get("text_analysis", ""),
                likes=filtered_post.get("likes", 0),
                post_date=filtered_post.get("post_date"),
                content_type=filtered_post.get("content_type", "instagram"),
                thumbnail=filtered_post.get("thumbnail", ""),
            )
            imported_posts += 1

        # Import collections - validate and filter allowed fields
        collections = validated.collections or []
        imported_collections = 0
        for coll in collections:
            coll_id = coll.get("id")
            if not coll_id:
                continue

            post_ids = coll.get("post_ids")
            if post_ids is None:
                post_ids = coll.get("postIds", [])

            db.upsert_collection(
                collection_id=coll_id,
                name=coll.get("name", "Untitled"),
                icon=coll.get("icon", "folder"),
                post_ids=post_ids if isinstance(post_ids, list) else [],
                created_at=coll.get("created_at") or coll.get("createdAt"),
                updated_at=coll.get("updated_at") or coll.get("updatedAt"),
            )
            imported_collections += 1

        logger.info(f"📥 Import complete: {imported_posts} posts, {skipped_posts} skipped, {imported_collections} collections")

        return {
            "success": True,
            "imported_posts": imported_posts,
            "skipped_posts": skipped_posts,
            "imported_collections": imported_collections,
            "mode": mode
        }
    except Exception as e:
        logger.error(f"Error importing data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Settings endpoints for AI Providers and Instagram configuration
# These manage the .api_keys file and dynamically update the ModelRouter

class ProviderKeyUpdate(BaseModel):
    provider: str
    api_key: str

class InstagramCredentials(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
    sessionid: Optional[str] = None

def get_config_path(filename: str) -> Path:
    """Get path to config directory files."""
    return Path(__file__).parent / "config" / filename

@app.get("/settings/ai-providers")
async def get_ai_providers(token: str = Depends(verify_token)):
    """
    Get available AI providers and their key status.
    - Requires API authentication
    """
    from core.model_router import get_router
    router = get_router()
    providers = router.get_available_providers()

    return {
        "success": True,
        "providers": {
            "groq": {
                "name": "Groq",
                "has_key": providers.get("groq", False),
                "key_hint": "gsk_..." if providers.get("groq") else None
            },
            "gemini": {
                "name": "Google Gemini",
                "has_key": providers.get("gemini", False),
                "key_hint": "AIza..." if providers.get("gemini") else None
            },
            "openrouter": {
                "name": "OpenRouter",
                "has_key": providers.get("openrouter", False),
                "key_hint": "sk-or-..." if providers.get("openrouter") else None
            },
            "ollama": {
                "name": "Ollama (Local)",
                "has_key": providers.get("ollama", False),
                "key_hint": None
            }
        }
    }

@app.post("/settings/ai-providers")
async def set_ai_provider_key(
    data: ProviderKeyUpdate,
    token: str = Depends(verify_token)
):
    """
    Set an API key for an AI provider.
    - Requires API authentication
    - provider: groq, gemini, or openrouter
    """
    from core.model_router import get_router
    import httpx

    valid_providers = ["groq", "gemini", "openrouter"]
    provider_slug = data.provider.lower()
    if provider_slug not in valid_providers:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid provider. Must be one of: {', '.join(valid_providers)}"
        )

    if not data.api_key or len(data.api_key.strip()) < 5:
        raise HTTPException(status_code=400, detail="Invalid API key format")

    # Validate API key explicitly
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if provider_slug == "groq":
                resp = await client.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {data.api_key.strip()}"})
                if resp.status_code != 200:
                    raise HTTPException(status_code=401, detail="Invalid Groq API Key")
            elif provider_slug == "gemini":
                resp = await client.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={data.api_key.strip()}")
                if resp.status_code != 200:
                    raise HTTPException(status_code=401, detail="Invalid Gemini API Key")
            elif provider_slug == "openrouter":
                resp = await client.get("https://openrouter.ai/api/v1/auth/key", headers={"Authorization": f"Bearer {data.api_key.strip()}"})
                if resp.status_code != 200:
                    raise HTTPException(status_code=401, detail="Invalid OpenRouter API Key")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Network error validating API key: {e}")

    router = get_router()
    success = router.set_api_key(data.provider.lower(), data.api_key.strip())

    if success:
        logger.info(f"🔑 API key updated for {data.provider}")
        return {"success": True, "provider": data.provider, "message": "API key updated"}
    else:
        raise HTTPException(status_code=500, detail="Failed to save API key")

@app.delete("/settings/ai-providers/{provider}")
async def delete_ai_provider_key(
    provider: str,
    token: str = Depends(verify_token)
):
    """
    Delete an API key for an AI provider.
    - Requires API authentication
    """
    from core.model_router import get_router

    valid_providers = ["groq", "gemini", "openrouter"]
    if provider.lower() not in valid_providers:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid provider. Must be one of: {', '.join(valid_providers)}"
        )

    router = get_router()
    success = router.delete_api_key(provider.lower())

    if success:
        logger.info(f"🔑 API key deleted for {provider}")
        return {"success": True, "provider": provider, "message": "API key deleted"}
    else:
        raise HTTPException(status_code=500, detail="Failed to delete API key")

@app.get("/settings/instagram")
async def get_instagram_credentials(token: str = Depends(verify_token)):
    """
    Get Instagram credentials (masked).
    - Requires API authentication
    """
    api_keys_file = get_config_path(".api_keys")

    username = None
    has_password = False

    if api_keys_file.exists():
        with open(api_keys_file, "r") as f:
            for line in f:
                if line.startswith("INSTAGRAM_USERNAME="):
                    username = line.split("=", 1)[1].strip()
                elif line.startswith("INSTAGRAM_PASSWORD="):
                    has_password = bool(line.split("=", 1)[1].strip())

    return {
        "success": True,
        "configured": username is not None and username != "",
        "username": username if username else None,
        "has_password": has_password
    }

@app.post("/settings/instagram")
async def set_instagram_credentials(
    data: InstagramCredentials,
    token: str = Depends(verify_token)
):
    """
    Set Instagram credentials.
    - Requires API authentication
    """
    sessionid = getattr(data, "sessionid", None)

    if not sessionid and (not data.username or not data.password):
        raise HTTPException(status_code=400, detail="Either login (username + password) OR Session ID is required")

    username_to_use = data.username or "session_user"

    api_keys_file = get_config_path(".api_keys")

    # Authenticate with Instagram first
    try:
        import instaloader
        L = instaloader.Instaloader()
        session_file = Path(__file__).parent / ".instaloader_session"
        if sessionid:
            L.context._session.cookies.set("sessionid", sessionid, domain=".instagram.com")
            L.context.username = username_to_use
            # To verify sessionid, attempt to get a profile to see if block is lifted
            try:
                L.get_profile("instagram")
            except Exception:
                # Some sessionids might have minor issues fetching profiles but work for posts,
                # but getting "instagram" is generally safe. Ignore non-fatal if possible
                pass
        else:
            L.login(data.username, data.password)
            username_to_use = data.username

        L.save_session_to_file(str(session_file))
        logger.info(f"🍪 Instagram session successfully verified and saved for {username_to_use}")
    except instaloader.exceptions.BadCredentialsException:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    except instaloader.exceptions.TwoFactorAuthRequiredException:
        raise HTTPException(status_code=401, detail="Two-factor auth blocked login. Please use Session ID cookie instead.")
    except instaloader.exceptions.ConnectionException as e:
        error_msg = str(e).lower()
        if "checkpoint" in error_msg or "challenge" in error_msg:
            raise HTTPException(status_code=401, detail="Instagram Checkpoint block. Please use Session ID cookie instead.")
        raise HTTPException(status_code=400, detail=f"Instagram connection error: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to authenticate with Instagram: {e}")

    # Read existing content
    lines = []
    username_found = False
    password_found = False

    if api_keys_file.exists():
        with open(api_keys_file, "r") as f:
            for line in f:
                if line.startswith("INSTAGRAM_USERNAME="):
                    lines.append(f"INSTAGRAM_USERNAME={username_to_use}\n")
                    username_found = True
                elif line.startswith("INSTAGRAM_PASSWORD="):
                    if data.password:
                        lines.append(f"INSTAGRAM_PASSWORD={data.password}\n")
                    else:
                        lines.append("INSTAGRAM_PASSWORD=\n")
                    password_found = True
                else:
                    lines.append(line)

    if not username_found:
        lines.append(f"INSTAGRAM_USERNAME={username_to_use}\n")
    if not password_found:
        if data.password:
            lines.append(f"INSTAGRAM_PASSWORD={data.password}\n")
        else:
            lines.append("INSTAGRAM_PASSWORD=\n")

    with open(api_keys_file, "w") as f:
        f.writelines(lines)

    logger.info(f"📸 Instagram credentials updated for {username_to_use}")

    return {
        "success": True,
        "username": username_to_use,
        "message": "Instagram credentials updated"
    }

@app.delete("/settings/instagram")
async def delete_instagram_credentials(token: str = Depends(verify_token)):
    """
    Delete Instagram credentials.
    - Requires API authentication
    """
    api_keys_file = get_config_path(".api_keys")

    if api_keys_file.exists():
        lines = []
        with open(api_keys_file, "r") as f:
            for line in f:
                if not line.startswith("INSTAGRAM_USERNAME=") and not line.startswith("INSTAGRAM_PASSWORD="):
                    lines.append(line)

        with open(api_keys_file, "w") as f:
            f.writelines(lines)

    logger.info("📸 Instagram credentials deleted")

    return {"success": True, "message": "Instagram credentials deleted"}


# ── YouTube WebSub (PubSubHubbub) Server Startup & Webhook Endpoints ───────

WEBSUB_MAX_BODY_BYTES = int(os.getenv("WEBSUB_MAX_BODY_BYTES", str(1024 * 1024)))
WEBSUB_RENEW_INTERVAL_SECONDS = int(
    os.getenv("WEBSUB_RENEW_INTERVAL_SECONDS", str(6 * 60 * 60))
)
WEBSUB_RECONCILE_INTERVAL_SECONDS = int(
    os.getenv("WEBSUB_RECONCILE_INTERVAL_SECONDS", str(60 * 60))
)
WEBSUB_RECONCILE_IDLE_SECONDS = int(
    os.getenv("WEBSUB_RECONCILE_IDLE_SECONDS", str(30 * 60))
)
WEBSUB_RECONCILE_MAX_WORKERS = int(
    os.getenv("WEBSUB_RECONCILE_MAX_WORKERS", "4")
)
_websub_last_delivery_monotonic = time.monotonic()


def _websub_secret() -> str:
    return os.getenv("WEBSUB_HMAC_SECRET", "")


def _try_acquire_websub_leader_lock():
    """Elect one Uvicorn worker to perform periodic lease renewal."""
    try:
        import fcntl

        database_identity = str(Path(get_db().db_path).resolve())
        lock_suffix = hashlib.sha256(
            database_identity.encode("utf-8")
        ).hexdigest()[:24]
        path = (
            Path(tempfile.gettempdir())
            / f"superbrain_websub_renewer_{lock_suffix}.lock"
        )
        lock_file = path.open("a+")
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock_file.close()
            return None
        return lock_file
    except Exception as exc:
        logger.warning("WebSub renewal leader election unavailable: %s", exc)
        return None


def _release_websub_leader_lock(lock_file):
    if lock_file is None:
        return
    try:
        import fcntl

        fcntl.flock(lock_file, fcntl.LOCK_UN)
    finally:
        lock_file.close()


async def _renew_websub_subscriptions_once():
    secret = _websub_secret()
    if not secret:
        logger.warning(
            "WebSub renewal disabled: WEBSUB_HMAC_SECRET is not configured"
        )
        return
    database = get_db()
    candidates = database.get_websub_renewal_candidates(within_hours=48)
    if not candidates:
        return
    from core.websub_notifier import build_topic_url, subscribe_channels

    grouped = {}
    for row in candidates:
        callback = row.get("callback_url")
        channel_id = row.get("channel_id")
        if callback and channel_id:
            grouped.setdefault(callback, []).append(channel_id)
            database.upsert_websub_subscription(
                channel_id=channel_id,
                channel_title=row.get("channel_title") or "",
                callback_url=callback,
                topic_url=row.get("topic_url") or build_topic_url(channel_id),
                lease_seconds=row.get("lease_seconds") or 864000,
                status="pending",
            )

    for callback, channel_ids in grouped.items():
        logger.info(
            "🔄 [WebSub] Renewing %d subscription(s) for %s",
            len(channel_ids),
            callback,
        )
        results = await asyncio.to_thread(
            subscribe_channels,
            channel_ids,
            callback,
            864000,
            secret,
        )
        for detail in results["details"]:
            if not detail["success"]:
                database.mark_websub_failed(
                    detail["channel_id"], detail["message"]
                )


def _parse_websub_timestamp(value: str):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        return None


def _fetch_active_websub_feeds(subscriptions):
    """Fetch active feeds concurrently without sharing SQLite connections."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from core.websub_notifier import fetch_youtube_feed_entries

    results = []
    if not subscriptions:
        return results
    worker_count = max(1, min(WEBSUB_RECONCILE_MAX_WORKERS, len(subscriptions)))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(fetch_youtube_feed_entries, subscription["channel_id"]): subscription
            for subscription in subscriptions
        }
        for future in as_completed(futures):
            subscription = futures[future]
            try:
                results.append((subscription, future.result(), None))
            except Exception as exc:
                results.append((subscription, [], str(exc)))
    return results


async def _reconcile_websub_subscriptions_once():
    """Queue uploads published after verification when the hub misses a push."""
    database = get_db()
    subscriptions = database.list_websub_subscriptions(status="active")
    fetched = await asyncio.to_thread(_fetch_active_websub_feeds, subscriptions)
    stats = {"channels": len(subscriptions), "fetched": 0, "failed": 0, "queued": 0}
    for subscription, entries, error in fetched:
        if error:
            stats["failed"] += 1
            logger.warning("[WebSub] Feed reconciliation failed for %s: %s", subscription["channel_id"], error)
            continue
        stats["fetched"] += 1
        not_before = _parse_websub_timestamp(
            subscription.get("verified_at") or subscription.get("subscribed_at") or ""
        )
        if not_before is None:
            continue
        for entry in entries:
            published = _parse_websub_timestamp(entry.get("published", ""))
            if not published or published <= not_before:
                continue
            validation = validate_link(entry["video_url"])
            if not validation["valid"] or database.check_cache(validation["shortcode"]):
                continue
            position = database.add_to_queue(validation["shortcode"], entry["video_url"])
            if position >= 0:
                stats["queued"] += 1
                logger.info(
                    "📹 [WebSub Reconcile] Queued missed upload: '%s' (%s)",
                    entry.get("title", ""), entry["video_url"],
                )
    logger.info(
        "[WebSub] Feed reconciliation: %d/%d fetched, %d queued, %d failed",
        stats["fetched"], stats["channels"], stats["queued"], stats["failed"],
    )
    return stats


async def _websub_renewal_loop():
    """Renew leases and reconcile feeds on independent, low-load schedules."""
    last_renewal_check = 0.0
    last_reconcile_check = 0.0
    while True:
        now = time.monotonic()
        if now - last_renewal_check >= WEBSUB_RENEW_INTERVAL_SECONDS:
            try:
                await _renew_websub_subscriptions_once()
            except Exception as exc:
                logger.warning("⚠️ [WebSub] Renewal pass failed: %s", exc)
            last_renewal_check = time.monotonic()

        if now - last_reconcile_check >= WEBSUB_RECONCILE_INTERVAL_SECONDS:
            if now - _websub_last_delivery_monotonic >= WEBSUB_RECONCILE_IDLE_SECONDS:
                try:
                    await _reconcile_websub_subscriptions_once()
                except Exception as exc:
                    logger.warning("⚠️ [WebSub] Feed reconciliation pass failed: %s", exc)
            else:
                logger.info("[WebSub] Skipping reconciliation; the hub is delivering normally")
            last_reconcile_check = time.monotonic()

        next_renewal = last_renewal_check + WEBSUB_RENEW_INTERVAL_SECONDS
        next_reconcile = last_reconcile_check + WEBSUB_RECONCILE_INTERVAL_SECONDS
        await asyncio.sleep(max(1, min(next_renewal, next_reconcile) - time.monotonic()))


@app.get("/api/youtube/webhook")
async def youtube_websub_verification(request: Request):
    """
    Google WebSub GET verification challenge callback endpoint.
    Google sends a GET request with hub.challenge, hub.mode, and hub.topic.
    SuperBrain echoes back hub.challenge to verify subscription.
    """
    params = dict(request.query_params)
    mode = params.get("hub.mode")
    topic = params.get("hub.topic", "")
    challenge = params.get("hub.challenge")
    lease = params.get("hub.lease_seconds")

    logger.info(f"🌐 [WebSub] Received GET verification challenge for topic: {topic} (mode={mode})")
    database = get_db()
    subscription = database.get_websub_subscription_by_topic(topic)
    if mode == "denied":
        if subscription:
            database.mark_websub_failed(
                subscription["channel_id"],
                params.get("hub.reason", "Hub denied subscription"),
            )
        return Response(status_code=204)
    if not subscription:
        raise HTTPException(
            status_code=404,
            detail="No pending WebSub operation matches this topic",
        )
    try:
        from core.websub_notifier import verify_websub_challenge
        resp_challenge = verify_websub_challenge(
            mode,
            topic,
            challenge,
            lease,
            expected_topic=subscription["topic_url"],
            expected_mode=subscription.get("pending_mode"),
        )
        if not database.mark_websub_verified(topic, mode, lease):
            raise ValueError("Pending WebSub operation changed before verification")
        return Response(content=resp_challenge, media_type="text/plain")
    except Exception as e:
        logger.error(f"❌ [WebSub] Verification challenge failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/youtube/webhook")
async def youtube_websub_notification(request: Request):
    """
    Google WebSub POST push notification endpoint.
    Google sends an Atom XML payload when a subscribed YouTube channel uploads a video.
    Parses the payload and triggers automatic YouTube video analysis.
    """
    body_bytes = await request.body()
    if len(body_bytes) > WEBSUB_MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="WebSub payload too large")
    secret = _websub_secret()
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="WebSub HMAC verification is not configured",
        )
    from core.websub_notifier import verify_websub_signature

    signature = request.headers.get("x-hub-signature", "")
    if not verify_websub_signature(body_bytes, signature, secret):
        logger.warning("Rejected WebSub notification with invalid signature")
        raise HTTPException(status_code=401, detail="Invalid WebSub signature")

    global _websub_last_delivery_monotonic
    _websub_last_delivery_monotonic = time.monotonic()
    logger.info("🔔 [WebSub] Received real-time YouTube upload push notification from Google Hub!")
    try:
        from core.websub_notifier import parse_websub_atom_payload
        data = parse_websub_atom_payload(body_bytes)
        video_url = data["video_url"]
        title = data.get("title", "")
        channel_id = data.get("channel_id", "")

        logger.info(f"📹 [WebSub Push] New Video Upload: '{title}' ({video_url}) [Channel: {channel_id}]")
        database = get_db()
        subscription = database.get_websub_subscription(channel_id)
        if not subscription or subscription.get("status") != "active":
            raise HTTPException(
                status_code=403,
                detail="Notification channel is not actively subscribed",
            )
        validation = validate_link(video_url)
        if not validation["valid"]:
            raise HTTPException(status_code=400, detail="Invalid YouTube video ID")
        shortcode = validation["shortcode"]
        if database.check_cache(shortcode):
            return JSONResponse(
                status_code=200,
                content={
                    "status": "duplicate",
                    "video_url": video_url,
                    "title": title,
                },
            )
        position = database.add_to_queue(shortcode, video_url)
        if position < 0:
            raise HTTPException(
                status_code=500, detail="Could not enqueue WebSub notification"
            )
        return JSONResponse(
            status_code=202,
            content={
                "status": "queued" if position > 0 else "processing",
                "video_url": video_url,
                "title": title,
                "queue_position": position,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"⚠️ [WebSub Push] Failed to parse Atom XML payload: {e}")
        return Response(content=f"Error parsing XML: {e}", status_code=400)


@app.post("/api/youtube/subscribe")
async def subscribe_youtube_channels(
    request: Request,
    callback_url: Optional[str] = Query(None, description="Public HTTPS callback URL of SuperBrain"),
    token: str = Depends(verify_token)
):
    """
    Subscribe SuperBrain to 200+ YouTube channel feeds via Google WebSub.
    Accepts OPML XML file upload (from Google Takeout) or a list of channel IDs.
    """
    if not callback_url:
        # Fallback to request host
        host = request.headers.get("host", "localhost:5000")
        scheme = request.headers.get("x-forwarded-proto", "https")
        callback_url = f"{scheme}://{host}/api/youtube/webhook"

    parsed_callback = urlsplit(callback_url)
    if parsed_callback.scheme != "https" and parsed_callback.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise HTTPException(
            status_code=400,
            detail="WebSub callback_url must use HTTPS",
        )
    secret = _websub_secret()
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="Configure WEBSUB_HMAC_SECRET before subscribing",
        )

    from core.websub_notifier import (
        build_topic_url,
        parse_opml_subscriptions,
        subscribe_channels,
    )

    extracted_channels = []
    channel_ids = []
    content_type = request.headers.get("content-type", "")
    try:
        if content_type.startswith("multipart/form-data"):
            form = await request.form()
            upload = form.get("file")
            if upload is not None and hasattr(upload, "read"):
                content = (await upload.read()).decode("utf-8", errors="strict")
                extracted_channels = parse_opml_subscriptions(content)
                channel_ids = [item["channel_id"] for item in extracted_channels]
            else:
                channel_ids = list(form.getlist("channel_ids"))
        else:
            payload = await request.json()
            if isinstance(payload, list):
                channel_ids = payload
            elif isinstance(payload, dict):
                channel_ids = payload.get("channel_ids") or []
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not channel_ids:
        raise HTTPException(
            status_code=400,
            detail="Provide an OPML file upload or channel_ids list",
        )
    try:
        c_ids = list(dict.fromkeys(str(cid).strip() for cid in channel_ids))
        topics = {cid: build_topic_url(cid) for cid in c_ids}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    titles = {
        item["channel_id"]: item.get("title", "") for item in extracted_channels
    }

    logger.info(f"🚀 [WebSub] Batch subscribing to {len(c_ids)} channels with callback: {callback_url}")
    database = get_db()
    for cid in c_ids:
        database.upsert_websub_subscription(
            channel_id=cid,
            channel_title=titles.get(cid, ""),
            callback_url=callback_url,
            topic_url=topics[cid],
            status="pending",
        )
    results = await asyncio.to_thread(
        subscribe_channels,
        c_ids,
        callback_url,
        864000,
        secret,
    )
    for detail in results["details"]:
        if not detail["success"]:
            database.mark_websub_failed(
                detail["channel_id"], detail["message"]
            )

    return {
        "success": results["failed"] == 0,
        "callback_url": callback_url,
        "pending_verification_count": results["success"],
        "failed_count": results["failed"],
        "total": results["total"],
        "details": results["details"],
    }


@app.get("/api/youtube/subscriptions")
async def list_youtube_subscriptions(token: str = Depends(verify_token)):
    """List YouTube WebSub subscriptions and their verification status."""
    database = get_db()
    subs = database.list_websub_subscriptions()
    return {"count": len(subs), "subscriptions": subs}


@app.post("/api/youtube/subscriptions/reconcile")
async def reconcile_youtube_subscriptions(token: str = Depends(verify_token)):
    """Run the missed-delivery catch-up pass immediately."""
    return await _reconcile_websub_subscriptions_once()


# OAuth is deliberately local-only: the Google desktop client redirects to this
# loopback endpoint, while refresh tokens remain in SecretSpec rather than SQLite.
_YOUTUBE_OAUTH_PENDING: dict[str, tuple[str, float]] = {}
_YOUTUBE_OAUTH_REDIRECT_URI = "http://localhost:5000/api/youtube/oauth/callback"


@app.get("/api/youtube/oauth/status")
async def youtube_oauth_status(token: str = Depends(verify_token)):
    from core import youtube_oauth
    return {
        "configured": youtube_oauth.configured(),
        "authorized": bool(os.getenv("YOUTUBE_OAUTH_REFRESH_TOKEN")),
        "redirect_uri": _YOUTUBE_OAUTH_REDIRECT_URI,
    }


@app.post("/api/youtube/oauth/start")
async def youtube_oauth_start(token: str = Depends(verify_token)):
    from core import youtube_oauth
    state, verifier, challenge = youtube_oauth.new_pkce()
    _YOUTUBE_OAUTH_PENDING[state] = (verifier, time.monotonic() + 600)
    return {"authorization_url": youtube_oauth.authorization_url(_YOUTUBE_OAUTH_REDIRECT_URI, state, challenge)}


@app.get("/api/youtube/oauth/callback")
async def youtube_oauth_callback(code: str = "", state: str = "", error: str = ""):
    pending = _YOUTUBE_OAUTH_PENDING.pop(state, None)
    if error:
        raise HTTPException(status_code=400, detail=f"Google authorization failed: {error}")
    if not code or not pending or pending[1] < time.monotonic():
        raise HTTPException(status_code=400, detail="OAuth state is missing or expired; start authorization again")
    from core import youtube_oauth
    tokens = await asyncio.to_thread(youtube_oauth.exchange_code, code, _YOUTUBE_OAUTH_REDIRECT_URI, pending[0])
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=400, detail="Google did not return a refresh token; revoke access and retry")
    await asyncio.to_thread(youtube_oauth.persist_refresh_token, refresh_token)
    return Response("YouTube connected. Return to SuperBrain and discover subscriptions.", media_type="text/plain")


@app.post("/api/youtube/subscriptions/discover")
async def discover_youtube_subscriptions(token: str = Depends(verify_token)):
    from core import youtube_oauth
    try:
        channels = await asyncio.to_thread(youtube_oauth.list_subscriptions)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not fetch YouTube subscriptions; authorize again if testing credentials have expired") from exc
    return {"count": len(channels), "channels": channels}


if __name__ == "__main__":
    import uvicorn
    print("🚀 Starting SuperBrain API...")
    print("📖 API Docs: http://localhost:5000/docs")
    print("🔍 Interactive: http://localhost:5000/redoc")
    uvicorn.run("api:app", host="0.0.0.0", port=5000, reload=False)
