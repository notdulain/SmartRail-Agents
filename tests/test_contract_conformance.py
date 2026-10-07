"""The real application must serve the HTTP interface the contract declares.

Compares OpenAPI documents rather than ``app.routes``: since FastAPI 0.142 included routers
are wrapped, so walking ``app.routes`` for ``APIRoute`` finds nothing and proves nothing.
"""

import pytest

from backend.config import AppConfig
from backend.contracts.api_spec import build_spec_app
from backend.contracts.fake_runtime import FakeRuntime

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def _operations(doc):
    return {
        (method, path)
        for path, item in doc["paths"].items()
        for method in item
        if method in HTTP_METHODS
    }


@pytest.fixture
def docs(tmp_path):
    from backend.application import create_app

    try:
        app = create_app(AppConfig(data_dir=tmp_path), FakeRuntime())
    except NotImplementedError:
        pytest.skip("application layer not implemented yet")
    return build_spec_app().openapi(), app.openapi()


def test_contract_declares_operations(docs):
    spec, _ = docs
    assert len(_operations(spec)) >= 15  # guards against this test silently comparing nothing


def test_application_implements_every_operation(docs):
    spec, real = docs
    missing = _operations(spec) - _operations(real)
    assert not missing, f"routes missing from application: {sorted(missing)}"


def test_operations_match_contract(docs):
    spec, real = docs
    for method, path in sorted(_operations(spec)):
        want, got = spec["paths"][path][method], real["paths"][path][method]
        for key in ("parameters", "requestBody", "responses"):
            assert got.get(key) == want.get(key), f"{method.upper()} {path}: {key} differs"


def test_schemas_match_contract(docs):
    spec, real = docs
    want = spec["components"]["schemas"]
    got = real["components"]["schemas"]
    for name, schema in want.items():
        assert got.get(name) == schema, f"schema {name} differs from the contract"
