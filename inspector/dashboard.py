from fastapi import Depends, FastAPI, Header, HTTPException, status


def create_app(cfg, store):
    app = FastAPI()

    async def require_token(x_inspect_token: str | None = Header(default=None)):
        expected = cfg.dashboard.resolve_token()
        if x_inspect_token is None or x_inspect_token != expected:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing inspect token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/", dependencies=[Depends(require_token)])
    async def root():
        return {"message": "GPU Node Inspector Dashboard"}

    return app
