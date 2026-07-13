"""FastAPI application factory."""

from fastapi import FastAPI

from pitchprob import __version__
from pitchprob.api.routes import router


def create_app() -> FastAPI:
    app = FastAPI(
        title="pitchprob",
        version=__version__,
        description=(
            "Football probability engine. Estimates calibrated market "
            "probabilities and expected value; does not promise profit."
        ),
    )
    app.include_router(router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
