import uvicorn
import os
import re
import httpx
import asyncio
import logging
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from datetime import datetime
from contextlib import asynccontextmanager
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
GITHUB_EVENT_URL = "https://api.github.com/repos/{}/events"
POLL_INTERVAL_SECONDS = 61
poll_state = {}
events = []
seen_event_ids = set()

def extract_repo(url: str) -> str | None:
    match = re.search(r"github\.com/([\w-]+/[\w.-]+)", url)
    if not match:
        return None
    return match.group(1).rstrip('/')

def process_event_payload(event: dict) -> dict:
    details = {}
    e_type = event.get("type")
    payload = event.get("payload", {})

    if e_type == "PushEvent":
        commits = payload.get("commits", [])
        details = {
            "branch": payload.get("ref", "").split('/')[-1],
            "commit_count": len(commits),
            "messages": [c.get("message", "No message").split('\n')[0] for c in commits],
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
        }
    elif e_type == "DeleteEvent":
        details = {
            "ref_type": payload.get("ref_type"),
            "ref": payload.get("ref"),
        }

    return {
        "repo": event.get("repo", {}).get("name"),
        "id": event.get("id"),
        "type": e_type,
        "user": event.get("actor", {}).get("login"),
        "details": details,
        "created_at": event.get("created_at"),
        "recorded_at": datetime.utcnow().isoformat() + "Z",
    }

async def poll_repo_events(repo: str, client: httpx.AsyncClient):

    headers = {"Accept": "application/vnd.github.v3+json"}

    try:
        url = GITHUB_EVENT_URL.format(repo)
        resp = await client.get(url, headers=headers, timeout=10)
        poll_state[repo] = {"last_check": datetime.utcnow().isoformat()}
        if resp.status_code == 200:
            logger.info(f"[{repo}] New events found (200).")
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
        elif resp.status_code == 403:
            logger.error(f"[{repo}] Rate limit exceeded (403). Polling interval is {POLL_INTERVAL_SECONDS}s.")
        else:
            logger.error(f"[{repo}] Error fetching events: {resp.status_code}")

    except httpx.RequestError as e:
        logger.error(f"[{repo}] Network error: {e}")
    except Exception as e:
        logger.error(f"[{repo}] Unexpected error polling: {e}")

    return []

async def poller_manager():
    global events  
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

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting poller manager task...")
    asyncio.create_task(poller_manager())
    yield
    logger.info("Shutting down.")

app = FastAPI(lifespan=lifespan)

class Repo(BaseModel):
    repo_url: str

@app.post("/subscribe")
async def subscribe_repo(repo: Repo):
    repo_name = extract_repo(repo.repo_url)
    if not repo_name:
        raise HTTPException(status_code=400, detail="Invalid GitHub repo URL. Format: https://github.com/user/repo")
    
    if repo_name in poll_state:
        return {"status": "already_subscribed", "repo": repo_name}
    poll_state[repo_name] = {"last_check": None}
    logger.info(f"Subscribed to {repo_name}")
    return {"status": "subscribed", "repo": repo_name}

@app.get("/inspect")
def get_events():
    return {"count": len(events), "data": events[:20]}

@app.delete("/clear")
def clear_events():
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
            font-family: 'Inter', system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans', sans-serif, 'Apple Color Emoji', 'Segoe UI Emoji', 'Segoe UI Symbol', 'Noto Color Emoji';
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
            display: none; /* Hidden by default */
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
              repoInput.value = ''; // Clear input on success
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
                // Simple formatting for details
                details = details.replace(/\{\\n/g, '{ \\n  ')
                                 .replace(/\\n\}/g, '\\n}');
                                 
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

          setInterval(loadEvents, 5000); // Refresh events every 5 seconds
          loadEvents(); // Initial load
        </script>
      </body>
    </html>
    """

if __name__ == "__main__":
    logger.info(f"Running without GITHUB_TOKEN. Poll interval set to {POLL_INTERVAL_SECONDS}s to avoid rate limits (60 req/hr).")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

