"""REST API over the resolver.

    uvicorn legislink.api:app          # seed registry, no remote lookups
    legislink serve --port 8000        # same thing, via the CLI

Requires the ``api`` extra (``pip install legislink[api]``).
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from . import __version__
from .models import Version
from .normalize import FUZZY_BACKEND
from .pipeline import annotate as annotate_text
from .registry import Registry
from .resolve import Resolver


class ResolveRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2_000_000,
                      description="Document text to scan for citations.")
    include_text: bool = Field(False, description="Echo the source text back.")
    version: str | None = Field(None, description="Version selector: 'as-at:YYYY-MM-DD', 'original', or 'latest'.")


class AnnotateRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2_000_000)
    format: Literal["markdown", "html"] = "markdown"
    min_confidence: float = Field(0.0, ge=0.0, le=1.0,
                                  description="Leave weaker citations unlinked.")
    version: str | None = Field(None, description="Version selector: 'as-at:YYYY-MM-DD', 'original', or 'latest'.")


def _parse_version(raw: str | None) -> Version | None:
    if raw is None:
        return None
    if raw == "original":
        return Version(kind="original")
    if raw == "latest" or raw == "prospective":
        return Version(kind="latest")
    if raw.startswith("as-at:"):
        date = raw.split(":", 1)[1].strip()
        return Version(kind="point-in-time", date=date)
    raise ValueError("version must be 'latest', 'original', 'prospective', or 'as-at:YYYY-MM-DD'")


def create_app(resolver: Resolver | None = None) -> FastAPI:
    """Build the application, optionally around a pre-configured resolver."""
    resolver = resolver or Resolver(Registry.default())

    app = FastAPI(
        title="legislink",
        version=__version__,
        description="Extract, resolve and link UK legislation citations to legislation.gov.uk.",
    )
    app.state.resolver = resolver

    @app.get("/health", summary="Liveness and registry size")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "registry_size": len(app.state.resolver.registry),
            "fuzzy_backend": FUZZY_BACKEND,
            "remote_lookups": app.state.resolver.remote is not None,
        }

    @app.post("/resolve", summary="Find and resolve citations in text")
    def resolve(request: ResolveRequest) -> dict[str, Any]:
        version = _parse_version(request.version) if request.version else None
        result = app.state.resolver.resolve_document(request.text, version=version)
        return result.to_dict(include_text=request.include_text)

    @app.post("/annotate", summary="Rewrite text with citation links")
    def annotate(request: AnnotateRequest) -> dict[str, Any]:
        version = _parse_version(request.version) if request.version else None
        result = app.state.resolver.resolve_document(request.text, version=version)
        return {
            "format": request.format,
            "annotated": annotate_text(
                result, fmt=request.format, min_confidence=request.min_confidence
            ),
            "stats": result.to_dict()["stats"],
        }

    @app.get("/works", summary="List or search the registry")
    def works(
        q: str | None = Query(None, description="Fuzzy title search."),
        year: int | None = Query(None, ge=1200, le=2200),
        limit: int = Query(20, ge=1, le=200),
    ) -> dict[str, Any]:
        registry: Registry = app.state.resolver.registry
        if q:
            hits = registry.search(q, year, limit=limit, cutoff=0.3)
            return {"query": q, "results": [
                {**w.to_dict(), "score": round(s, 4)} for w, s in hits
            ]}
        found = [w for w in registry if year is None or w.year == year]
        found.sort(key=lambda w: (w.year, w.title))
        return {"total": len(found), "results": [w.to_dict() for w in found[:limit]]}

    @app.get("/works/{work_id}", summary="Fetch one work by id")
    def work(work_id: str) -> dict[str, Any]:
        found = app.state.resolver.registry.get(work_id)
        if found is None:
            raise HTTPException(status_code=404, detail=f"no such work: {work_id}")
        return found.to_dict()

    return app


app = create_app()
