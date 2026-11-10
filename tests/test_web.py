import pytest
from fastapi.testclient import TestClient

from explainer.web.app import create_app


@pytest.fixture
def client(ws):
    out = ws.project("acme").output_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / "demo.mp4").write_bytes(b"\x00" * 4096)
    (out / ".cache").mkdir()
    (out / ".cache" / "x.mp4").write_bytes(b"secret")
    return TestClient(create_app(ws.root))


def test_pages(client):
    assert "Acme" in client.get("/").text
    r = client.get("/p/acme")
    assert r.status_code == 200 and "Demo video" in r.text and "photo.jpg" in r.text
    r = client.get("/p/acme/v/demo")
    assert r.status_code == 200 and "points" in r.text and "Hello world" in r.text


def test_not_found(client):
    assert client.get("/p/nope").status_code == 404
    assert client.get("/p/acme/v/nope").status_code == 404
    assert client.get("/p/acme/v/demo/render.json").status_code == 404


@pytest.mark.parametrize("url", [
    "/media/acme/output/../project.yaml",
    "/media/acme/output/..%2fproject.yaml",
    "/media/acme/output/%2e%2e/%2e%2e/acme/project.yaml",
    "/media/acme/videos/demo.yaml",
    "/media/acme/output/.cache/x.mp4",
    "/media/..%2f..%2fetc/output/passwd",
])
def test_media_traversal_blocked(client, url):
    assert client.get(url).status_code == 404


def test_media_range(client):
    r = client.get("/media/acme/output/demo.mp4", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100


def test_read_only(client):
    for url in ["/", "/p/acme", "/p/acme/v/demo", "/media/acme/output/demo.mp4"]:
        assert client.post(url).status_code == 405
        assert client.delete(url).status_code == 405
