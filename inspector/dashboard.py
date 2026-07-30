from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from typing import Annotated
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
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

        WPS 验证流程：
        1. WPS 发送 GET 请求验证回调 URL，返回 {"result":"ok"}
        2. 用户 @机器人时，WPS 发送 POST 请求，包含消息内容
        """
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"result": "ok"})

        logger.info("WPS callback received: %s", json.dumps(body, ensure_ascii=False)[:200])

        # 提取消息内容
        content = body.get("content", "")

        # 判断是否需要触发汇报，并提取节点名
        should_report = False
        target_node = None  # 如果指定了节点名，只汇报该节点

        # WPS 的 content 格式: "@webhook机器人 汇报"
        # 去掉 @机器人名字，检查关键词
        text = content
        if "@" in text:
            # 移除 @机器人名字 部分
            parts = text.split()
            if len(parts) > 1:
                text = " ".join(parts[1:])  # 取 @ 后面的内容

        keywords = ["汇报", "状态", "巡检", "报告", "report", "status", "你好", "hi", "hello"]
        text_lower = text.lower()

        # 检查是否包含关键词
        for kw in keywords:
            if kw in text_lower:
                should_report = True
                # 提取关键词后面的节点名
                # 例如 "汇报 node-01" -> target_node = "node-01"
                kw_index = text_lower.find(kw)
                after_kw = text[kw_index + len(kw):].strip()
                if after_kw:
                    target_node = after_kw
                break

        # 如果没有匹配关键词但以 @ 开头，也触发汇报
        if not should_report and content.startswith("@"):
            should_report = True

        if should_report and notifier:
            try:
                # 获取所有节点状态
                all_statuses = await store.list_node_status()

                # 如果指定了节点名，过滤
                if target_node:
                    filtered = [s for s in all_statuses if s["node"].lower() == target_node.lower()]
                    if not filtered:
                        # 节点不存在，返回提示
                        await notifier.send({
                            "type": "periodic_report",
                            "title": "⚠️ 节点未找到",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "node_count": 0,
                            "online_count": 0,
                            "nodes": [],
                            "message": f"未找到节点 '{target_node}'，可用节点：{', '.join(s['node'] for s in all_statuses)}",
                        })
                        return JSONResponse({"result": "ok"})
                    statuses = filtered
                else:
                    statuses = all_statuses

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
                    logger.info("WPS callback: report sent for nodes: %s", [s["node"] for s in statuses])
            except Exception:
                logger.exception("WPS callback: failed to send report")

        # WPS 需要返回 {"result":"ok"}
        return JSONResponse({"result": "ok"})

    # ========== WPS 机器人 URL 验证接口 ==========
    # WPS 机器人配置回调时会验证此接口（GET 请求）

    @app.get("/api/wps/callback")
    async def wps_verify():
        """WPS 机器人回调 URL 验证接口

        WPS 验证流程：发送 GET 请求，返回 {"result":"ok"}
        """
        return JSONResponse({"result": "ok"})

    return app
