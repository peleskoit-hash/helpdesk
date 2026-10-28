import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
import random
import struct
from types import SimpleNamespace
from uuid import UUID, uuid4
import zlib

from fastapi import FastAPI
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from PIL import Image, ImageDraw, PngImagePlugin
import pytest
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from api.app.auth import CurrentUser
from api.app.db import Base, get_db
from api.app.models import BackgroundContour, Company, SavedFile, User
from api.app.routers.background_contours import router
from api.app.schemas import BackgroundContourComposition
from api.app.services import background_contours as contours, schema_evolution


URL = "/api/v1/background-contours"


def image_bytes(kind="PNG", mode="RGBA", size=(32, 32), color=(0, 0, 0, 0), animated=False):
    image = Image.new(mode, size, color)
    ImageDraw.Draw(image).rectangle((1, 1, min(12, size[0] - 1), min(12, size[1] - 1)), fill="black")
    output = BytesIO()
    options = {}
    if animated:
        second = Image.new(mode, size, color)
        ImageDraw.Draw(second).ellipse((2, 2, 20, 20), fill="white")
        options = {"save_all": True, "append_images": [second], "duration": 100}
    image.save(output, format=kind, **options)
    return output.getvalue()


@pytest.fixture
def case(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'contours.sqlite').as_posix()}")
    @event.listens_for(engine, "connect")
    def enable_fk(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    ids = {name: str(uuid4()) for name in ("author", "other", "admin", "trainee", "readonly", "disabled")}
    roles = {"author": "agent", "other": "agent", "admin": "admin", "trainee": "trainee", "readonly": "chiki_piki", "disabled": "agent"}
    with factory() as db:
        db.add_all([Company(id="first", name="First"), Company(id="second", name="Second")])
        db.flush()
        db.add_all([User(id=value, company_id="second" if name == "other" else "first", full_name=name,
                         login=name, role=roles[name], is_active=name != "disabled") for name, value in ids.items()])
        db.commit()
    storage = tmp_path / "storage" / "background_contours"
    monkeypatch.setattr(contours.settings, "background_contour_storage_dir", str(storage))
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="contour-test-only")
    app.include_router(router)
    def database():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = database
    with TestClient(app) as client:
        def login(name):
            client.cookies.clear()
            data = base64.b64encode(json.dumps({"auth_user_id": ids[name]}).encode())
            client.cookies.set("session", TimestampSigner("contour-test-only").sign(data).decode())
        login("author")
        yield SimpleNamespace(client=client, login=login, factory=factory, ids=ids, storage=storage, app=app)
    engine.dispose()


def upload(case, content=None, **fields):
    return case.client.post(URL, data={"name": "Контур", **fields}, files={
        "file": ("../../original-photo.jpg", image_bytes() if content is None else content, "image/jpeg"),
    })


def saved(case):
    response = upload(case)
    assert response.status_code == 201, response.text
    return response.json()


def stored_paths(case, contour):
    with case.factory() as db:
        return contours._paths(db.get(BackgroundContour, contour["id"]))


@pytest.mark.parametrize("kind", ["PNG", "WEBP"])
def test_create_list_image_and_author_snapshot(case, kind):
    source = Image.open(BytesIO(image_bytes()))
    output = BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("source-photo", "private source")
    source.save(output, format=kind, **({"pnginfo": metadata} if kind == "PNG" else {"exif": b"private source"}))
    response = upload(case, output.getvalue() + b"private appended original", name="  Мой контур  ",
                      rotation="360", rings="2", distance="200", base_scale="50")
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["name"] == "Мой контур"
    assert result["format"] == kind.lower()
    assert result["author_user_id"] == case.ids["author"]
    assert result["author_name"] == "author"
    assert result["can_delete"] and result["can_edit"]
    assert result["rotation"] == 360 and result["rings"] == 2
    assert result["distance"] == 200 and result["base_scale"] == 50
    assert "storage_key" not in result
    path, backup = stored_paths(case, result)
    assert path.parent == case.storage and not backup.exists()
    assert path.name == f"{UUID(result['id']).hex}.{kind.lower()}"
    image = case.client.get(result["image_url"])
    assert image.status_code == 200
    assert image.headers["content-type"] == f"image/{kind.lower()}"
    assert image.headers["x-content-type-options"] == "nosniff"
    assert image.headers["cache-control"] == "private, no-store"
    assert len(image.content) == result["file_size"] == path.stat().st_size
    assert b"private" not in image.content
    with Image.open(BytesIO(image.content)) as decoded:
        assert not decoded.info.get("exif") and not decoded.info.get("source-photo")
        assert decoded.convert("RGBA").getchannel("A").getextrema() == (0, 255)
    with case.factory() as db:
        db.get(User, case.ids["author"]).full_name = "Renamed author"
        assert db.scalar(select(SavedFile.id)) is None
        db.commit()
    loaded = case.client.get(URL).json()["items"]
    assert loaded == [result]


def test_list_is_global_newest_first_with_stable_id_tiebreak(case):
    first, second, third = saved(case), saved(case), saved(case)
    with case.factory() as db:
        now = datetime.now(timezone.utc)
        db.get(BackgroundContour, first["id"]).created_at = now - timedelta(days=1)
        for item in (second, third):
            db.get(BackgroundContour, item["id"]).created_at = now
        db.commit()
    case.login("other")  # A different company still sees the shared library.
    rows = case.client.get(URL).json()["items"]
    assert [row["id"] for row in rows] == [*sorted([second["id"], third["id"]], reverse=True), first["id"]]
    assert all(not row["can_delete"] and not row["can_edit"] for row in rows)
    assert case.client.get(first["image_url"]).status_code == 200


@pytest.mark.parametrize("role,allowed", [("author", True), ("admin", True), ("trainee", True), ("readonly", False)])
def test_creation_permissions(case, role, allowed):
    case.login(role)
    response = upload(case)
    assert response.status_code == (201 if allowed else 403)
    if allowed:
        assert response.json()["author_user_id"] == case.ids[role]
    else:
        assert not case.storage.exists()


def test_author_identity_and_permissions_use_real_user_and_ignore_supplied_metadata(case):
    response = upload(case, author_user_id=case.ids["admin"], author_name="Fake author", storage_key="../foreign.png")
    assert response.status_code == 201
    contour = response.json()
    assert contour["author_user_id"] == case.ids["author"] and contour["author_name"] == "author"
    # A future effective-user context cannot grant the real user admin rights.
    actor = CurrentUser(id=case.ids["admin"], role="admin", real_user_id=case.ids["other"], real_role="admin", is_real_admin=True)
    with case.factory() as db:
        with pytest.raises(contours.ContourError) as exc:
            contours.delete_contour(db, actor, contour["id"])
        assert exc.value.status_code == 403
        db.rollback()
        created = contours.create_contour(db, actor, "Real actor", BytesIO(image_bytes()), BackgroundContourComposition())
        assert created["author_user_id"] == case.ids["other"]


@pytest.mark.parametrize("role,status", [("author", 204), ("admin", 204), ("other", 403), ("trainee", 403), ("readonly", 403)])
def test_delete_permissions_and_repeated_delete(case, role, status):
    contour = saved(case)
    path, backup = stored_paths(case, contour)
    case.login(role)
    response = case.client.delete(f"{URL}/{contour['id']}")
    assert response.status_code == status
    if status == 204:
        assert not path.exists() and not backup.exists()
        assert case.client.delete(f"{URL}/{contour['id']}").status_code == 404
        assert case.client.get(contour["image_url"]).status_code == 404
        assert case.client.get(URL).json()["items"] == []
    else:
        assert path.exists() and case.client.get(contour["image_url"]).status_code == 200


@pytest.mark.parametrize("author_state", ["active", "inactive", "deleted"])
def test_future_edit_permissions_and_retained_author_after_deletion(case, author_state):
    contour = saved(case)
    with case.factory() as db:
        author = db.get(User, case.ids["author"])
        if author_state == "inactive":
            author.is_active = False
        elif author_state == "deleted":
            db.delete(author)
        db.commit()
    for viewer in ("admin", "other", "readonly"):
        case.login(viewer)
        row = case.client.get(URL).json()["items"][0]
        assert row["author_user_id"] == contour["author_user_id"] and row["author_name"] == "author"
        assert row["can_delete"] == (viewer == "admin")
        assert row["can_edit"] == (viewer == "admin" and author_state != "active")
        assert case.client.get(contour["image_url"]).status_code == 200
    case.login("admin")
    assert case.client.delete(f"{URL}/{contour['id']}").status_code == 204


@pytest.mark.parametrize("anonymous", [False, True])
def test_authentication_required_on_every_route(case, anonymous):
    contour = saved(case)
    if anonymous:
        case.client.cookies.clear()
    else:
        case.login("disabled")
    assert case.client.get(URL).status_code == 401
    assert upload(case).status_code == 401
    assert case.client.get(contour["image_url"]).status_code == 401
    assert case.client.delete(f"{URL}/{contour['id']}").status_code == 401


@pytest.mark.parametrize("fields", [
    {"name": " "}, {"name": "x" * 121}, {"name": "bad\x00name"}, {"name": "bad\nname"},
    {"rotation": "-1"}, {"rotation": "361"}, {"rotation": "nan"}, {"rotation": "inf"},
    {"rings": "-1"}, {"rings": "3"}, {"rings": "1.5"}, {"rings": "true"},
    {"distance": "-1"}, {"distance": "201"}, {"distance": "NaN"},
    {"base_scale": "49"}, {"base_scale": "201"}, {"base_scale": "Infinity"},
])
def test_invalid_name_and_composition_never_create_files_or_rows(case, fields):
    assert upload(case, **fields).status_code == 422
    assert case.client.get(URL).json()["items"] == []
    assert not case.storage.exists()


@pytest.mark.parametrize("content,status", [
    (b"", 400), (b"not an image", 400), (b"\x89PNG\r\n\x1a\ninvalid", 400),
    (b"<svg onload='alert(1)'/>", 400), (b"RIFF\x00\x00\x00\x00WEBPbroken", 400),
    (image_bytes()[:-20], 400), (image_bytes(kind="JPEG", mode="RGB", color="white"), 400),
    (image_bytes(mode="RGB", color="white"), 400), (image_bytes(color=(0, 0, 0, 255)), 400),
    (image_bytes(size=(2049, 2)), 400), (image_bytes(animated=True), 400),
    (image_bytes(kind="WEBP", animated=True), 400),
    (image_bytes() + b"x" * contours.MAX_BYTES, 413),
], ids=["empty", "text", "fake-png", "svg", "fake-webp", "truncated", "jpeg", "rgb-png",
        "opaque", "oversized-dimensions", "apng", "animated-webp", "oversized-bytes"])
def test_invalid_files_are_rejected_by_content(case, content, status):
    response = upload(case, content)
    assert response.status_code == status, response.text
    assert case.client.get(URL).json()["items"] == []
    assert not case.storage.exists()


def test_fully_transparent_file_is_rejected(case):
    output = BytesIO()
    Image.new("RGBA", (32, 32)).save(output, format="PNG")
    assert upload(case, output.getvalue()).status_code == 400


def test_decompression_bomb_header_is_rejected_without_allocation(case):
    header = b"IHDR" + struct.pack(">IIBBBBB", 100000, 100000, 8, 6, 0, 0, 0)
    content = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + header + struct.pack(">I", zlib.crc32(header))
    assert upload(case, content).status_code == 400
    assert not case.storage.exists()


def test_palette_transparency_is_supported_and_invisible_source_pixels_are_cleared(case):
    source = Image.new("P", (16, 16), 0)
    source.putpalette([255, 0, 0, 0, 0, 0] + [0] * 762)
    source.putpixel((4, 4), 1)
    output = BytesIO()
    source.save(output, format="PNG", transparency=0)
    response = upload(case, output.getvalue())
    assert response.status_code == 201
    with Image.open(BytesIO(case.client.get(response.json()["image_url"]).content)) as decoded:
        assert decoded.getpixel((0, 0)) == (0, 0, 0, 0)
        assert decoded.getpixel((4, 4))[3] == 255


def test_reencoded_file_must_also_fit_byte_limit(case):
    rgb = Image.frombytes("RGB", (1024, 1024), random.Random(17).randbytes(1024 * 1024 * 3)).convert("RGBA")
    rgb.putpixel((0, 0), (0, 0, 0, 0))
    source = BytesIO()
    rgb.save(source, format="WEBP", quality=30)
    assert len(source.getvalue()) < contours.MAX_BYTES
    response = upload(case, source.getvalue())
    assert response.status_code == 413, response.text
    assert not case.storage.exists()


def test_creation_fsync_failure_cleans_partial_file(case, monkeypatch):
    def fail(_):
        raise OSError("disk full")
    monkeypatch.setattr(contours.os, "fsync", fail)
    assert upload(case).status_code == 503
    assert list(case.storage.iterdir()) == []
    assert case.client.get(URL).json()["items"] == []


@pytest.mark.parametrize("after_commit", [False, True])
def test_creation_commit_failure_never_removes_a_committed_file(case, monkeypatch, after_commit):
    original = Session.commit
    def fail(db):
        if after_commit:
            original(db)
        else:
            db.flush()
        raise RuntimeError("commit acknowledgement failed")
    with monkeypatch.context() as patch:
        patch.setattr(Session, "commit", fail)
        assert upload(case).status_code == 503
    rows = case.client.get(URL).json()["items"]
    assert len(rows) == int(after_commit)
    assert len(list(case.storage.iterdir())) == int(after_commit)
    if after_commit:
        assert case.client.get(rows[0]["image_url"]).status_code == 200


def test_exclusive_creation_never_overwrites_an_existing_contour(case, monkeypatch):
    contour = saved(case)
    path, _ = stored_paths(case, contour)
    content = path.read_bytes()
    monkeypatch.setattr(contours, "uuid4", lambda: UUID(contour["id"]))
    assert upload(case).status_code == 503
    assert path.read_bytes() == content
    assert len(case.client.get(URL).json()["items"]) == 1


@pytest.mark.parametrize("failure", ["link", "unlink", "commit", "restore", "acknowledgement", "cleanup"])
def test_deletion_failures_keep_live_images_recoverable(case, monkeypatch, failure):
    contour = saved(case)
    other = saved(case)
    path, backup = stored_paths(case, contour)
    other_path, _ = stored_paths(case, other)
    other_content = other_path.read_bytes()
    original_commit, original_link, original_unlink = Session.commit, contours.os.link, Path.unlink
    def fail_commit(db):
        if failure == "acknowledgement":
            original_commit(db)
        else:
            db.flush()
        raise RuntimeError("database failed")
    def fail_link(source, destination, **kwargs):
        if failure == "link" or (failure == "restore" and Path(source) == backup):
            raise OSError("link failed")
        return original_link(source, destination, **kwargs)
    def fail_unlink(target, *args, **kwargs):
        if (failure == "unlink" and target == path) or (failure == "cleanup" and target == backup):
            raise PermissionError("unlink failed")
        return original_unlink(target, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(contours.os, "link", fail_link)
        patch.setattr(Path, "unlink", fail_unlink)
        if failure in {"commit", "restore", "acknowledgement"}:
            patch.setattr(Session, "commit", fail_commit)
        response = case.client.delete(f"{URL}/{contour['id']}")
    deleted = failure in {"cleanup", "acknowledgement"}
    assert response.status_code == (204 if failure == "cleanup" else 503)
    assert other_path.read_bytes() == other_content
    assert case.client.get(contour["image_url"]).status_code == (404 if deleted else 200)
    assert len(case.client.get(URL).json()["items"]) == (1 if deleted else 2)
    if failure in {"restore", "cleanup"}:
        assert backup.is_file() and not path.exists()
    if not deleted:
        assert case.client.delete(f"{URL}/{contour['id']}").status_code == 204
        assert not path.exists() and not backup.exists()


def test_missing_image_can_be_deleted_and_quarantine_collision_never_overwritten(case):
    contour = saved(case)
    path, backup = stored_paths(case, contour)
    backup.write_bytes(b"do not overwrite")
    assert case.client.delete(f"{URL}/{contour['id']}").status_code == 503
    assert backup.read_bytes() == b"do not overwrite" and path.exists()
    backup.unlink()
    path.unlink()
    assert case.client.get(contour["image_url"]).status_code == 404
    assert case.client.delete(f"{URL}/{contour['id']}").status_code == 204


def test_other_storage_domains_and_appearance_preferences_are_untouched(case):
    sentinels = []
    for name in ("saved_files", "avatars", "attachments", "telegram_client_avatars"):
        directory = case.storage.parent / name
        directory.mkdir(parents=True)
        path = directory / "foreign.png"
        path.write_bytes(b"foreign bytes")
        sentinels.append(path)
    patterns = {"light": {"selected": "dots", "patterns": {"dots": {"seed": 17}}}}
    with case.factory() as db:
        db.get(User, case.ids["author"]).background_patterns_json = patterns
        db.commit()
    contour = saved(case)
    assert case.client.delete(f"{URL}/{contour['id']}").status_code == 204
    assert all(path.read_bytes() == b"foreign bytes" for path in sentinels)
    with case.factory() as db:
        assert db.get(User, case.ids["author"]).background_patterns_json == patterns
        assert db.get(User, case.ids["author"]).appearance_write_state_json is None


@pytest.mark.parametrize("key", ["../foreign.png", "/etc/passwd", "C:\\foreign.png", "avatars/foreign.png", "f" * 32 + ".png"])
def test_corrupt_storage_keys_cannot_read_or_delete_other_files(case, key):
    contour = saved(case)
    sentinel = case.storage / ("f" * 32 + ".png")
    sentinel.write_bytes(b"foreign file")
    with case.factory() as db:
        db.get(BackgroundContour, contour["id"]).storage_key = key
        db.commit()
    assert case.client.get(contour["image_url"]).status_code == 503
    assert case.client.delete(f"{URL}/{contour['id']}").status_code == 503
    assert sentinel.read_bytes() == b"foreign file"


def test_symlinked_image_is_never_read_or_deleted(case, monkeypatch):
    contour = saved(case)
    path, _ = stored_paths(case, contour)
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda target: target == path or original(target))
    assert case.client.get(contour["image_url"]).status_code == 503
    assert case.client.delete(f"{URL}/{contour['id']}").status_code == 503
    assert path.exists()


def test_concurrent_deletes_are_serialized(case):
    contour = saved(case)
    actor = CurrentUser(id=case.ids["author"], role="agent", real_user_id=case.ids["author"], real_role="agent", is_real_admin=False)
    def delete():
        with case.factory() as db:
            try:
                contours.delete_contour(db, actor, contour["id"])
                return 204
            except contours.ContourError as exc:
                return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: delete(), range(2)))
    assert sorted(results) == [204, 404]
    assert list(case.storage.iterdir()) == []


def test_migration_is_idempotent_and_preserves_existing_data_and_authorship(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(schema_evolution, "engine", engine)
    with engine.begin() as db:
        db.execute(text("CREATE TABLE legacy_marker (value TEXT)"))
        db.execute(text("INSERT INTO legacy_marker VALUES ('keep')"))
    schema_evolution.ensure_background_contours_table()
    assert set(inspect(engine).get_table_names()) == {"legacy_marker", "background_contours"}
    assert {column["name"] for column in inspect(engine).get_columns("background_contours")} == {
        "id", "name", "author_user_id", "author_name", "created_at", "storage_key", "format", "file_size",
        "rotation", "rings", "distance", "base_scale",
    }
    assert not inspect(engine).get_foreign_keys("background_contours")
    assert inspect(engine).get_unique_constraints("background_contours")[0]["column_names"] == ["storage_key"]
    with Session(engine) as db:
        db.add(BackgroundContour(id=str(uuid4()), name="Saved", author_user_id="deleted-author", author_name="Author snapshot",
                                 created_at=datetime.now(timezone.utc), storage_key="a" * 32 + ".png", format="png", file_size=100,
                                 rotation=0, rings=0, distance=20, base_scale=100))
        db.commit()
    schema_evolution.ensure_background_contours_table()
    with Session(engine) as db:
        row = db.scalar(select(BackgroundContour))
        assert row.author_user_id == "deleted-author" and row.author_name == "Author snapshot"
        assert db.scalar(text("SELECT value FROM legacy_marker")) == "keep"
    assert {index["name"] for index in inspect(engine).get_indexes("background_contours")} == {"ix_background_contours_created"}
    engine.dispose()


def test_real_app_registers_only_requested_routes():
    from api.app.main import app
    routes = {(route.path, method) for route in app.routes for method in getattr(route, "methods", [])}
    assert {(URL, "GET"), (URL, "POST"), (URL + "/{contour_id}/image", "GET"), (URL + "/{contour_id}", "DELETE")} <= routes
    assert (URL + "/{contour_id}", "PATCH") not in routes
    assert app.openapi()["paths"][URL]["post"]["requestBody"]["content"].keys() == {"multipart/form-data"}
