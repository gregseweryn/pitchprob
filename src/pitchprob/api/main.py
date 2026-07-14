"""FastAPI application factory."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pitchprob import __version__
from pitchprob.api.routes import router

#: Dashboard dev origins (ADR 0007). Production origins are deployment
#: configuration (M6), not code.
DASHBOARD_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]


def create_app() -> FastAPI:
    app = FastAPI(
        title="pitchprob",
        version=__version__,
        description=(
            "Football probability engine. Estimates calibrated market "
            "probabilities and expected value; does not promise profit."
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=DASHBOARD_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
