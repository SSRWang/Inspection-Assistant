from __future__ import annotations
from typing import Annotated
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import APIKeyHeader
from fastapi.templating import Jinja2Templates
from inspector.config import Settings
from inspector.store import SqliteStore

API_KEY_HEADER = APIKeyHeader(name="X-Inspect-Token", auto_error=False)


def create_app(cfg: Settings, store: SqliteStore) -> FastAPI:
    app = FastAPI(title="GPU Node Inspector")
    templates = Jinja2Templates(directory="templates")

    expected_token = cfg.dashboard.resolve_token()

    async def verify_token(
        token: Annotated[str | None, Depends(API_KEY_HEADER)],
        request: Request,
    ):
        if not expected_token:
            return True
        # Accept token from either header or ?token= query parameter (for browser access)
        query_token = request.query_params.get("token")
        effective_token = token or query_token
        if effective_token != expected_token:
            raise HTTPException(status_code=401, detail="Invalid or missing token")
        return True

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, _=Depends(verify_token)):
        statuses = await store.list_node_status()
        alerts = await store.list_alert_states()
        return templates.TemplateResponse(request=request, name="index.html", context={
            "request": request,
            "statuses": statuses,
            "alerts": alerts,
            "node_count": len(statuses),
            "online_count": sum(1 for s in statuses if s["reachable"]),
        })

    @app.get("/api/status")
    async def api_status(_=Depends(verify_token)):
        return {"nodes": await store.list_node_status()}

    @app.get("/api/alerts")
    async def api_alerts(_=Depends(verify_token)):
        return {"alerts": await store.list_alert_states()}

    @app.get("/api/history")
    async def api_history(node: str | None = None, rule: str | None = None, limit: int = 100, _=Depends(verify_token)):
        return {"metrics": await store.list_metrics(node=node, rule=rule, limit=limit)}

    return app
