from fastapi import FastAPI


def create_app(cfg, store):
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app
