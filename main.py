from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel
from fastapi.responses import HTMLResponse
import httpx, asyncio, re
from datetime import datetime

app = FastAPI()
subscriptions = {}
events = []

GITHUB_EVENT_URL = "https://api.github.com/repos/{}/events"


def extract_repo(url: str) -> str:
    match = re.search(r"github\.com/([\w-]+/[\w.-]+)", url)
    if not match:
        raise HTTPException(status_code=400, detail="Invalid GitHub repo URL")
    return match.group(1)


async def poll_repo_events(repo: str):
    seen_ids = set()
    while True:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(GITHUB_EVENT_URL.format(repo))
                if resp.status_code == 200:
                    data = resp.json()
                    for e in data:
                        if e["id"] not in seen_ids:
                            seen_ids.add(e["id"])

                            details = {}
                            if e["type"] == "PushEvent":
                                commits = e.get("payload", {}).get("commits", [])
                                details = {
                                    "branch": e["payload"].get("ref"),
                                    "commit_count": len(commits),
                                    "messages": [c["message"] for c in commits],
                                }
                            elif e["type"] == "IssuesEvent":
                                issue = e.get("payload", {}).get("issue", {})
                                details = {
                                    "action": e["payload"].get("action"),
                                    "title": issue.get("title"),
                                    "url": issue.get("html_url"),
                                }
                            elif e["type"] == "PullRequestEvent":
                                pr = e.get("payload", {}).get("pull_request", {})
                                details = {
                                    "action": e["payload"].get("action"),
                                    "title": pr.get("title"),
                                    "url": pr.get("html_url"),
                                }

                            events.append({
                                "repo": repo,
                                "type": e["type"],
                                "user": e["actor"]["login"],
                                "details": details,
                                "created_at": e["created_at"],
                                "recorded_at": datetime.utcnow().isoformat(),
                            })
        except Exception:
            pass
        await asyncio.sleep(30)


class Repo(BaseModel):
    repo_url: str


@app.post("/subscribe")
async def subscribe_repo(repo: Repo, background_tasks: BackgroundTasks):
    repo_name = extract_repo(repo.repo_url)
    if repo_name in subscriptions:
        return {"status": "already_subscribed"}
    subscriptions[repo_name] = True
    background_tasks.add_task(poll_repo_events, repo_name)
    return {"status": "subscribed", "repo": repo_name}


@app.get("/inspect")
def get_events():
    return {"count": len(events), "data": events[-20:]}


@app.delete("/clear")
def clear_events():
    events.clear()
    return {"status": "cleared"}


@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html>
      <head>
        <title>GitHub Webhook Inspector</title>
        <meta charset="UTF-8" />
        <style>
          body { font-family: monospace; padding: 20px; background: #111; color: #0f0; }
          pre { white-space: pre-wrap; word-wrap: break-word; }
          input, button { padding: 6px 10px; margin-right: 4px; font-family: monospace; }
          input { width: 350px; }
          button { background: #0f0; color: #111; border: none; cursor: pointer; }
          hr { margin: 20px 0; border: 1px solid #0f0; }
        </style>
      </head>
      <body>
        <h2>GitHub Webhook Inspector</h2>
        <div>
          <input type="text" id="repoInput" placeholder="https://github.com/user/repo" />
          <button onclick="subscribeRepo()">Subscribe</button>
          <button onclick="clearEvents()">Clear</button>
        </div>
        <hr>
        <pre id="events">Loading...</pre>
        <script>
          async function subscribeRepo() {
            const url = document.getElementById('repoInput').value.trim();
            if (!url) return alert('Enter a GitHub repo URL');
            const res = await fetch('/subscribe', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ repo_url: url })
            });
            const data = await res.json();
            alert(`Subscribed: ${data.repo || data.detail}`);
          }

          async function loadEvents() {
            const res = await fetch('/inspect');
            const data = await res.json();
            const lines = data.data.map(e => {
              let info = `[${e.recorded_at}] ${e.repo} | ${e.type} by ${e.user}`;
              if (e.details && Object.keys(e.details).length)
                info += "\\n   " + JSON.stringify(e.details, null, 2);
              return info;
            }).join('\\n\\n');
            document.getElementById('events').innerText = lines || "No events yet.";
          }

          async function clearEvents() {
            await fetch('/clear', { method: 'DELETE' });
            document.getElementById('events').innerText = "Cleared.";
          }

          setInterval(loadEvents, 5000);
          loadEvents();
        </script>
      </body>
    </html>
    """
