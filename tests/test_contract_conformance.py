"""The real application must serve every route the contract declares."""

import pytest
from fastapi.routing import APIRoute

from backend.config import AppConfig
from backend.contracts.api_spec import build_spec_app
from backend.contracts.fake_runtime import FakeRuntime


def _routes(app):
    return {
        (method.lower(), route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
        if method not in {"HEAD", "OPTIONS"}
    }


def test_application_implements_contract(tmp_path):
    from backend.application import create_app

    try:
        app = create_app(AppConfig(data_dir=tmp_path), FakeRuntime())
    except NotImplementedError:
        pytest.skip("application layer not implemented yet")
    missing = _routes(build_spec_app()) - _routes(app)
    assert not missing, f"routes missing from application: {sorted(missing)}"
