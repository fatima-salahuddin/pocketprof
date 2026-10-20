"""RFC 7807 problem details (SDS 5.2, 11). Every error leaves the API in this one shape."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_JSON = "application/problem+json"


class Problem(Exception):
    """Raise anywhere in a request to return a problem document."""

    def __init__(self, status: int, title: str, detail: str = "", type_: str = "about:blank",
                 errors: list[dict[str, Any]] | None = None):
        super().__init__(detail or title)
        self.status, self.title, self.detail, self.type, self.errors = status, title, detail, type_, errors


def not_found(what: str) -> Problem:
    return Problem(404, "Not found", f"{what} was not found.", "/problems/not-found")


def forbidden(detail: str = "You do not have access to this resource.") -> Problem:
    return Problem(403, "Forbidden", detail, "/problems/forbidden")


def conflict(detail: str) -> Problem:
    return Problem(409, "Conflict", detail, "/problems/conflict")


def bad_request(detail: str, errors: list[dict[str, Any]] | None = None) -> Problem:
    return Problem(422, "Invalid request", detail, "/problems/invalid", errors)


def _doc(request: Request, status: int, title: str, detail: str, type_: str,
         errors: list[dict[str, Any]] | None = None) -> JSONResponse:
    body: dict[str, Any] = {"type": type_, "title": title, "status": status, "detail": detail,
                            "instance": request.url.path}
    rid = getattr(request.state, "request_id", None)
    if rid:
        body["request_id"] = rid
    if errors:
        body["errors"] = errors
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON)


def install(app: FastAPI) -> None:
    @app.exception_handler(Problem)
    async def _problem(request: Request, exc: Problem):
        return _doc(request, exc.status, exc.title, exc.detail, exc.type, exc.errors)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        title = {401: "Unauthorized", 403: "Forbidden", 404: "Not found", 405: "Method not allowed",
                 429: "Too many requests"}.get(exc.status_code, "Error")
        return _doc(request, exc.status_code, title, str(exc.detail or title), "about:blank")

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        errors = [{"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]}
                  for e in exc.errors()]
        return _doc(request, 422, "Invalid request", "Some fields are missing or invalid.",
                    "/problems/invalid", errors)
