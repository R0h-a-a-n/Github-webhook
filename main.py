import uvicorn
import os
import re
import httpx
import asyncio
import logging
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from datetime import datetime
from contextlib import asynccontextmanager

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# --- GitHub Configuration ---
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_EVENT_URL = "https://api.github.com/repos/{}/events"
POLL_INTERVAL_SECONDS = 10

# --- Global State ---
poll_state = {}
events = []
seen_event_ids = set()

# --- Utility Functions ---

def extract_repo(url: str) -> Optional[str]:
    """Extracts 'user/repo' from a GitHub URL."""
    match = re.search(r"github\.com/([\w-]+/[\w.-]+)", url)
    if not match:
        return None
    return match.group(1).rstrip('/')

def process_event_payload(event: dict) -> dict:
    """Extracts relevant details from a GitHub event payload."""
    details = {}
    e_type = event.get("type")
    payload = event.get("payload", {})

    if e_type == "PushEvent":
        commits = payload.get("commits", [])
        ref_full = payload.get("ref", "")
        branch_or_tag = ref_full.split('/')[-1] if ref_full else "unknown"
        
        if "refs/tags/" in ref_full:
            details = {
                "action": "Pushed Tag",
                "tag": branch_or_tag,
                "commit_count": len(commits)
            }
        elif len(commits) > 0:
            details = {
                "action": "Pushed Commits",
                "branch": branch_or_tag,
                "commit_count": len(commits),
                "messages": [c.get("message", "No message").split('\n')[0] for c in commits],
            }
        else:
            details = {
                "action": "Pushed (No Commits)",
                "ref": ref_full,
                "note": "This is often a force-push, branch deletion, or other ref update."
            }
    elif e_type == "IssuesEvent":
        issue = payload.get("issue", {})
        details = {
            "action": payload.get("action"),
            "title": issue.get("title"),
            "url": issue.get("html_url"),
        }
    elif e_type == "PullRequestEvent":
        pr = payload.get("pull_request", {})
        details = {
            "action": payload.get("action"),
            "title": pr.get("title"),
            "url": pr.get("html_url"),
        }
    elif e_type == "WatchEvent":
        details = {"action": payload.get("action")}
    elif e_type == "ForkEvent":
        details = {"fork_url": payload.get("forkee", {}).get("html_url")}
    elif e_type == "CreateEvent":
        details = {
            "ref_type": payload.get("ref_type"),
            "ref": payload.get("ref"),
            "description": payload.get("description")
        }
    elif e_type == "DeleteEvent":
        details = {
            "ref_type": payload.get("ref_type"),
            "ref": payload.get("ref"),
        }
    else:
        action = payload.get("action")
        if action:
            details = {"action": action}
        else:
            details = {"unhandled_event": True, "payload_keys": list(payload.keys())}

    return {
        "repo": event.get("repo", {}).get("name"),
        "id": event.get("id"),
        "type": e_type,
        "user": event.get("actor", {}).get("login"),
        "details": details,
        "created_at": event.get("created_at"),
        "recorded_at": datetime.utcnow().isoformat() + "Z",
    }

# --- Core Polling Logic ---

async def poll_repo_events(repo: str, client: httpx.AsyncClient):
    """
    Polls a single repository for events using ETag for conditional requests.
    Returns a list of new, processed events.
    """
    repo_state = poll_state.get(repo, {})
    etag = repo_state.get("etag")

    headers = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"token {GITHUB_TOKEN}"
    }
    if etag:
        headers["If-None-Match"] = etag

    try:
        url = GITHUB_EVENT_URL.format(repo)
        resp = await client.get(url, headers=headers, timeout=10)
        
        repo_state["last_check"] = datetime.utcnow().isoformat()

        if resp.status_code == 304:
            logger.info(f"[{repo}] No changes (304).")
            return []

        if resp.status_code == 200:
            logger.info(f"[{repo}] New events found (200).")
            
            repo_state["etag"] = resp.headers.get("etag")
            
            new_events_data = resp.json()
            new_processed_events = []

            for event_data in reversed(new_events_data):
                if event_data["id"] not in seen_event_ids:
                    seen_event_ids.add(event_data["id"])
                    processed = process_event_payload(event_data)
                    new_processed_events.append(processed)
            
            return new_processed_events

        elif resp.status_code == 404:
            logger.warning(f"[{repo}] Repository not found (404). Removing from polling.")
            if repo in poll_state:
                del poll_state[repo]
        elif resp.status_code == 401:
             logger.error(f"[{repo}] Bad credentials (401). Check GITHUB_TOKEN.")
        elif resp.status_code == 403:
            logger.error(f"[{repo}] Rate limit exceeded (403) or token lacks 'repo' scope.")
        else:
            logger.error(f"[{repo}] Error fetching events: {resp.status_code}")

    except httpx.RequestError as e:
        logger.error(f"[{repo}] Network error: {e}")
    except Exception as e:
        logger.error(f"[{repo}] Unexpected error polling: {e}")

    return []

async def poller_manager():
    """
    A central background task that polls all subscribed repositories
    concurrently at a set interval.
    """
    global events
    if not GITHUB_TOKEN:
        logger.error("GITHUB_TOKEN not set. Poller will not start.")
        return

    async with httpx.AsyncClient() as client:
        while True:
            if not poll_state:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue
            
            logger.info(f"Polling {len(poll_state)} repositories...")
            
            tasks = [poll_repo_events(repo, client) for repo in list(poll_state.keys())]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            new_event_count = 0
            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"Poller task failed: {result}")
                elif result:
                    events.extend(result)
                    new_event_count += len(result)

            if new_event_count > 0:
                logger.info(f"Added {new_event_count} new events.")
                events = sorted(events, key=lambda x: x['recorded_at'], reverse=True)[:200]

            await asyncio.sleep(POLL_INTERVAL_SECONDS)

# --- FastAPI App Setup ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting poller manager task...")
    asyncio.create_task(poller_manager())
    yield
    logger.info("Shutting down.")

app = FastAPI(lifespan=lifespan)

class Repo(BaseModel):
    repo_url: str

# --- API Endpoints ---

@app.post("/subscribe")
async def subscribe_repo(repo: Repo):
    repo_name = extract_repo(repo.repo_url)
    if not repo_name:
        raise HTTPException(status_code=400, detail="Invalid GitHub repo URL. Format: https://github.com/user/repo")
    
    if repo_name in poll_state:
        return {"status": "already_subscribed", "repo": repo_name}
    
    poll_state[repo_name] = {"etag": None, "last_check": None}
    logger.info(f"Subscribed to {repo_name}")
    return {"status": "subscribed", "repo": repo_name}

@app.get("/inspect")
def get_events():
    """Returns the most recent events (up to 20)."""
    return {"count": len(events), "data": events[:20]}

@app.delete("/clear")
def clear_events():
    """Clears all recorded events."""
    events.clear()
    seen_event_ids.clear()
    logger.info("Cleared all events.")
    return {"status": "cleared"}

@app.get("/", response_class=HTMLResponse)
def home():
    """Serves the simple HTML frontend."""
    return """
    <html>
      <head>
        <title>GitHub Event Monitor</title>
        <meta charset="UTF-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
          body { 
            font-family: 'Inter', system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans', sans-serif;
            padding: 20px; 
            background: #111827; 
            color: #d1d5db; 
            line-height: 1.6;
          }
          h2 { color: #fff; }
          #controls { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 20px; }
          input[type="text"] { 
            flex-grow: 1;
            min-width: 250px;
            padding: 8px 12px; 
            font-family: monospace; 
            background: #1f2937; 
            color: #d1d5db;
            border: 1px solid #374151; 
            border-radius: 6px; 
          }
          button { 
            padding: 8px 14px; 
            font-family: inherit; 
            font-weight: 500;
            background: #4f46e5; 
            color: #fff; 
            border: none; 
            cursor: pointer; 
            border-radius: 6px;
            transition: background-color 0.2s ease;
          }
          button:hover { background: #4338ca; }
          button#clearBtn { background: #d9464f; }
          button#clearBtn:hover { background: #b91c1c; }
          #status { 
            margin-top: 10px; 
            padding: 8px 12px;
            background: #1f2937;
            border: 1px solid #374151;
            border-radius: 6px;
            color: #9ca3af;
            font-family: monospace;
            font-size: 0.9em;
            display: none;
          }
          hr { margin: 24px 0; border: 1px solid #374151; }
          pre { 
            white-space: pre-wrap; 
            word-wrap: break-word; 
            background: #000;
            padding: 16px;
            border-radius: 8px;
            font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
            font-size: 0.875rem;
          }
        </style>
      </head>
      <body>
        <h2>GitHub Event Monitor</h2>
        <div id="controls">
          <input type="text" id="repoInput" placeholder="https://github.com/user/repo" />
          <button onclick="subscribeRepo()">Subscribe</button>
          <button id="clearBtn" onclick="clearEvents()">Clear Events</button>
        </div>
        <div id="status"></div>
        <hr>
        <pre id="events">Loading...</pre>
        
        <script>
          const repoInput = document.getElementById('repoInput');
          const eventsPre = document.getElementById('events');
          const statusDiv = document.getElementById('status');

          function showStatus(message, isError = false) {
            statusDiv.textContent = message;
            statusDiv.style.color = isError ? '#f87171' : '#60a5fa';
            statusDiv.style.display = 'block';
            setTimeout(() => { statusDiv.style.display = 'none'; }, 4000);
          }

          async function subscribeRepo() {
            const url = repoInput.value.trim();
            if (!url) {
              showStatus('Please enter a GitHub repo URL', true);
              return;
            }
            try {
              const res = await fetch('/subscribe', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ repo_url: url })
              });
              const data = await res.json();
              if (!res.ok) {
                throw new Error(data.detail || 'Subscription failed');
              }
              showStatus(`Subscribed to: ${data.repo}`);
              repoInput.value = '';
            } catch (err) {
              showStatus(err.message, true);
            }
          }

          async function loadEvents() {
            try {
              const res = await fetch('/inspect');
              const data = await res.json();
              if (!res.ok) throw new Error('Failed to fetch events');

              const lines = data.data.map(e => {
                let details = JSON.stringify(e.details, null, 2);
                return `[${e.recorded_at}] ${e.repo} | ${e.type} by ${e.user}\\n  Details: ${details}`;
              }).join('\\n\\n');
              
              eventsPre.innerText = lines || "No events yet. Subscribe to a repository to begin.";
            } catch (err) {
              eventsPre.innerText = `Error loading events: ${err.message}`;
            }
          }

          async function clearEvents() {
            try {
              await fetch('/clear', { method: 'DELETE' });
              eventsPre.innerText = "Events cleared.";
              showStatus("Event log cleared.");
            } catch (err) {
              showStatus(err.message, true);
            }
          }

          setInterval(loadEvents, 5000);
          loadEvents();
        </script>
      </body>
    </html>
    """

if __name__ == "__main__":
    if not GITHUB_TOKEN:
        logger.warning("="*50)
        logger.warning("WARNING: GITHUB_TOKEN environment variable not set.")
        logger.warning("Polling will fail with a 401 error.")
        logger.warning("Please set the variable and restart.")
        logger.warning("="*50)
    else:
        logger.info(f"Using GITHUB_TOKEN. Poll interval set to {POLL_INTERVAL_SECONDS}s.")
    
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)