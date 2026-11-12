import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from api.app.main import SecurityHeadersMiddleware, app


@pytest.mark.parametrize(("path", "status_code"), [
    ("/login", 200),
    ("/health", 200),
    ("/static/login.js", 200),
    ("/", 303),
    ("/__missing_security_headers_test__", 404),
    ("/static/__missing_security_headers_test__", 404),
])
def test_security_headers_on_application_responses(path, status_code):
    # Use the real routes and middleware without starting database or background services.
    response = TestClient(app).get(path, follow_redirects=False)

    assert response.status_code == status_code
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_security_headers_preserve_existing_values_on_streaming_response():
    test_app = FastAPI()
    test_app.add_middleware(SecurityHeadersMiddleware)

    @test_app.get("/")
    def download():
        return StreamingResponse(iter([b"first", b"second"]), headers={
            "X-Frame-Options": "SAMEORIGIN",
            "Referrer-Policy": "origin",
        })

    response = TestClient(test_app).get("/")

    assert response.content == b"firstsecond"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert response.headers["referrer-policy"] == "origin"
