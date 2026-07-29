from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from typing import Annotated
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.security import APIKeyHeader
from fastapi.templating import Jinja2Templates
from inspector.config import Settings
from inspector.notifier import BaseNotifier
from inspector.store import SqliteStore

logger = logging.getLogger(__name__)

API_KEY_HEADER = APIKeyHeader(name="X-Inspect-Token", auto_error=False)


def create_app(cfg: Settings, store: SqliteStore, notifier: BaseNotifier | None = None) -> FastAPI:
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
        # Parse raw_metrics JSON string into dict for template access
        parsed_statuses = []
        for s in statuses:
            raw = s.get("raw_metrics", "{}")
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    raw = {}
            parsed_statuses.append({**s, "raw": raw})
        return templates.TemplateResponse(request=request, name="index.html", context={
            "request": request,
            "statuses": parsed_statuses,
            "alerts": alerts,
            "node_count": len(parsed_statuses),
            "online_count": sum(1 for s in parsed_statuses if s["reachable"]),
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

    @app.post("/api/report")
    async def api_report(_=Depends(verify_token)):
        """手动触发一次巡检报告推送到群（用于 @机器人 或手动调用）"""
        if not notifier:
            raise HTTPException(status_code=503, detail="Notifier not configured")

        statuses = await store.list_node_status()
        if not statuses:
            raise HTTPException(status_code=404, detail="No node data available")

        # 构建汇报消息
        payload = {
            "type": "periodic_report",
            "title": "GPU 节点巡检报告",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "node_count": len(statuses),
            "online_count": sum(1 for s in statuses if s["reachable"]),
            "nodes": [
                {
                    "name": s["node"],
                    "reachable": bool(s["reachable"]),
                    "summary": s["summary"],
                    "last_check_at": s["last_check_at"],
                    "raw_metrics": s.get("raw_metrics"),
                }
                for s in statuses
            ],
        }

        ok = await notifier.send(payload)
        if not ok:
            await store.enqueue_webhook(payload)
            return {"status": "queued", "message": "Webhook send failed, queued for retry"}

        return {"status": "sent", "message": "Report sent successfully"}

    # ========== WPS 协作机器人回调接口 ==========
    # WPS 机器人配置回调 URL 指向此接口，用户 @机器人 时会触发汇报

    @app.post("/api/wps/callback")
    async def wps_callback(request: Request):
        """WPS 机器人回调接口 - 当用户 @机器人 时触发汇报

        配置方法：
        1. 进入 WPS 协作群 → 设置 → 群机器人 → 你的机器人 → 设置
        2. 开启"接收消息"功能
        3. 回调 URL 填写: http://你的服务器IP:8080/api/wps/callback
        """
        try:
            body = await request.json()
        except Exception:
            return PlainTextResponse("ok")  # WPS 需要 200 响应

        logger.info("WPS callback received: %s", json.dumps(body, ensure_ascii=False)[:200])

        # 提取消息内容
        msg_type = body.get("msg_type", "")
        text = body.get("text", {}).get("text", "")

        # 判断是否需要触发汇报
        # 情况1: 明确的文本消息包含"汇报"或"状态"等关键词
        # 情况2: 任何 @消息 都触发汇报（简单策略）
        should_report = False

        if msg_type == "text":
            keywords = ["汇报", "状态", "巡检", "报告", "report", "status", "你好", "hi", "hello"]
            if any(kw in text.lower() for kw in keywords):
                should_report = True
            elif "@" in text:  # @消息
                should_report = True

        if should_report and notifier:
            try:
                # 触发汇报
                statuses = await store.list_node_status()
                if statuses:
                    payload = {
                        "type": "periodic_report",
                        "title": "GPU 节点巡检报告",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "node_count": len(statuses),
                        "online_count": sum(1 for s in statuses if s["reachable"]),
                        "nodes": [
                            {
                                "name": s["node"],
                                "reachable": bool(s["reachable"]),
                                "summary": s["summary"],
                                "last_check_at": s["last_check_at"],
                                "raw_metrics": s.get("raw_metrics"),
                            }
                            for s in statuses
                        ],
                    }
                    await notifier.send(payload)
                    logger.info("WPS callback: report sent")
            except Exception:
                logger.exception("WPS callback: failed to send report")

        # WPS 需要返回 200 状态码
        return PlainTextResponse("ok")

    # ========== WPS 机器人 URL 验证接口 ==========
    # WPS 机器人配置回调时会验证此接口

    @app.get("/api/wps/callback")
    async def wps_verify():
        """WPS 机器人回调 URL 验证接口"""
        return PlainTextResponse("ok")

    return app
