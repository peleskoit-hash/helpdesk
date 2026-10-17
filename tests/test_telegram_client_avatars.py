from io import BytesIO
from pathlib import Path

from PIL import Image
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from api.app.db import Base
from api.app.models import Client, Company, TelegramContact
from api.app.services import telegram_client_avatars as avatars


def image_bytes(size=(640, 320), color="red", kind="PNG", **kwargs):
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format=kind, **kwargs)
    return output.getvalue()


@pytest.fixture
def storage(monkeypatch, tmp_path):
    directory = tmp_path / "storage" / "telegram_client_avatars"
    monkeypatch.setattr(avatars.settings, "telegram_client_avatar_storage_dir", str(directory))
    return directory


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Company(id="company", name="Test"))
        session.commit()
        session.add(Client(id="client", company_id="company", display_name="Client"))
        session.commit()
        session.add(TelegramContact(id="contact", company_id="company", client_id="client",
                                    external_user_id="123", external_chat_id="456"))
        session.commit()
        yield session
    engine.dispose()


def snapshot(db):
    contact = db.get(TelegramContact, "contact")
    return (contact.avatar_thumbnail, contact.avatar_file_unique_id,
            contact.avatar_path, contact.avatar_updated_at)


@pytest.mark.parametrize("size,box", [((640, 320), (0, 64, 256, 192)),
                                      ((320, 640), (64, 0, 192, 256)),
                                      ((30, 30), (0, 0, 256, 256))])
def test_thumbnail_keeps_entire_image_and_aspect_ratio(size, box):
    thumbnail, extension = avatars.prepare_avatar(image_bytes(size))
    with Image.open(BytesIO(thumbnail)) as image:
        assert image.format == "WEBP"
        assert image.size == (256, 256)
        assert image.convert("RGBA").getchannel("A").getbbox() == box
    assert extension == "png"


@pytest.mark.parametrize("kind,extension", [("JPEG", "jpg"), ("PNG", "png"), ("WEBP", "webp")])
def test_save_and_repeat_is_noop(db, storage, kind, extension):
    content = image_bytes(kind=kind)
    assert avatars.save_avatar(db, "contact", "photo_1-unique", content)
    first = snapshot(db)
    assert first[0] and first[1] == "photo_1-unique" and first[3] is not None
    assert first[2].startswith("telegram_client_avatars/")
    assert first[2].endswith("." + extension)
    assert (storage / Path(first[2]).name).read_bytes() == content
    assert not avatars.save_avatar(db, "contact", "photo_1-unique")
    assert snapshot(db) == first
    assert len(list(storage.iterdir())) == 1


def test_replace_removes_old_full_file(db, storage):
    avatars.save_avatar(db, "contact", "first", image_bytes())
    first = snapshot(db)
    replacement = image_bytes(color="blue", kind="JPEG")
    assert avatars.save_avatar(db, "contact", "second", replacement)
    second = snapshot(db)
    assert second[0] != first[0]
    assert second[1] == "second"
    assert second[2] != first[2]
    assert second[3] >= first[3]
    assert not (storage / Path(first[2]).name).exists()
    assert (storage / Path(second[2]).name).read_bytes() == replacement
    assert len(list(storage.iterdir())) == 1


@pytest.mark.parametrize("failure", ["invalid", "oversized", "pixels", "truncated", "commit", "write"])
def test_failure_preserves_previous_database_and_file(db, storage, monkeypatch, failure):
    original = image_bytes()
    avatars.save_avatar(db, "contact", "first", original)
    before = snapshot(db)
    content = image_bytes(color="blue")
    if failure == "invalid":
        content = b"not a picture"
    elif failure == "oversized":
        monkeypatch.setattr(avatars, "MAX_IMAGE_BYTES", len(content) - 1)
    elif failure == "pixels":
        monkeypatch.setattr(avatars, "MAX_IMAGE_PIXELS", 10)
    elif failure == "truncated":
        content = content[:80]
    elif failure == "commit":
        def fail_commit(session):
            raise RuntimeError("Simulated database failure")
        event.listen(db, "before_commit", fail_commit, once=True)
    elif failure == "write":
        original_open = Path.open
        class BrokenWriter:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def write(self, value):
                raise OSError("Simulated disk full")
        def fail_write(path, mode="r", *args, **kwargs):
            if mode == "xb":
                with original_open(path, mode) as target:
                    target.write(b"partial")
                return BrokenWriter()
            return original_open(path, mode, *args, **kwargs)
        monkeypatch.setattr(Path, "open", fail_write)
    with pytest.raises((ValueError, RuntimeError, OSError)):
        avatars.save_avatar(db, "contact", "second", content)
    assert snapshot(db) == before
    assert (storage / Path(before[2]).name).read_bytes() == original
    assert len(list(storage.iterdir())) == 1


def test_no_telegram_photo_keeps_all_saved_data(db, storage):
    assert not avatars.save_avatar(db, "contact", None)
    assert snapshot(db) == (None, None, None, None)
    avatars.save_avatar(db, "contact", "first", image_bytes())
    before = snapshot(db)
    assert not avatars.save_avatar(db, "contact", None)
    assert snapshot(db) == before
    assert len(list(storage.iterdir())) == 1


def test_delete_full_retains_thumbnail_and_identity(db, storage):
    avatars.save_avatar(db, "contact", "first", image_bytes())
    before = snapshot(db)
    assert avatars.delete_full_avatar(db, "contact")
    after = snapshot(db)
    assert after == (before[0], before[1], None, before[3])
    assert not list(storage.iterdir())
    assert not avatars.delete_full_avatar(db, "contact")
    assert not avatars.save_avatar(db, "contact", "first")


@pytest.mark.parametrize("missing_file", ["deleted_after_close", "missing_on_disk"])
def test_same_avatar_restored_on_next_client_request(db, storage, missing_file):
    content = image_bytes()
    avatars.save_avatar(db, "contact", "first", content)
    # Retain even an existing thumbnail encoded with different WebP settings.
    contact = db.get(TelegramContact, "contact")
    with Image.open(BytesIO(contact.avatar_thumbnail)) as image:
        output = BytesIO()
        image.save(output, format="WEBP", lossless=True)
    contact.avatar_thumbnail = output.getvalue()
    db.commit()
    original = snapshot(db)
    if missing_file == "deleted_after_close":
        avatars.delete_full_avatar(db, "contact")
    else:
        (storage / Path(original[2]).name).unlink()
    before_restore = snapshot(db)

    # The next request can ask for a download before image bytes are available.
    assert avatars.needs_avatar_download(db.get(TelegramContact, "contact"), "first")
    assert not avatars.save_avatar(db, "contact", "first")
    assert snapshot(db) == before_restore
    assert not list(storage.iterdir())

    assert avatars.save_avatar(db, "contact", "first", content)
    restored = snapshot(db)
    assert (restored[0], restored[1], restored[3]) == (original[0], original[1], original[3])
    assert (storage / Path(restored[2]).name).read_bytes() == content
    assert not avatars.needs_avatar_download(db.get(TelegramContact, "contact"), "first")
    assert not avatars.save_avatar(db, "contact", "first", content)
    assert snapshot(db) == restored
    assert len(list(storage.iterdir())) == 1


def test_download_check_and_missing_bytes_for_new_photo(db, storage):
    contact = db.get(TelegramContact, "contact")
    assert not avatars.needs_avatar_download(contact, None)
    assert avatars.needs_avatar_download(contact, "first")
    assert not avatars.save_avatar(db, "contact", "first")
    assert snapshot(db) == (None, None, None, None)
    assert not storage.exists()
    avatars.save_avatar(db, "contact", "first", image_bytes())
    before = snapshot(db)
    assert avatars.needs_avatar_download(db.get(TelegramContact, "contact"), "second")
    assert not avatars.save_avatar(db, "contact", "second")
    assert snapshot(db) == before
    assert len(list(storage.iterdir())) == 1


@pytest.mark.parametrize("failure", ["invalid", "commit"])
def test_failed_restore_preserves_thumbnail_and_cleans_new_file(db, storage, failure):
    content = image_bytes()
    avatars.save_avatar(db, "contact", "first", content)
    avatars.delete_full_avatar(db, "contact")
    before = snapshot(db)
    if failure == "invalid":
        content = b"invalid image"
    else:
        def fail_commit(session):
            raise RuntimeError("Simulated database failure")
        event.listen(db, "before_commit", fail_commit, once=True)
    with pytest.raises((ValueError, RuntimeError)):
        avatars.save_avatar(db, "contact", "first", content)
    assert snapshot(db) == before
    assert not list(storage.iterdir())


def test_delete_commit_failure_preserves_file(db, storage):
    avatars.save_avatar(db, "contact", "first", image_bytes())
    before = snapshot(db)
    def fail_commit(session):
        raise RuntimeError("Simulated database failure")
    event.listen(db, "before_commit", fail_commit, once=True)
    with pytest.raises(RuntimeError):
        avatars.delete_full_avatar(db, "contact")
    assert snapshot(db) == before
    assert (storage / Path(before[2]).name).exists()


@pytest.mark.parametrize("unsafe", ["avatars/employee.jpg", "../avatars/employee.jpg",
                                    "telegram_client_avatars/../avatars/employee.jpg",
                                    "telegram_client_avatars/C:\\employee.jpg",
                                    "/app/storage/avatars/employee.jpg"])
def test_unsafe_paths_never_delete_employee_files(db, storage, unsafe):
    employee = storage.parent / "avatars" / "employee.jpg"
    employee.parent.mkdir(parents=True)
    employee.write_bytes(b"employee")
    contact = db.get(TelegramContact, "contact")
    contact.avatar_path = unsafe
    db.commit()
    avatars.delete_full_avatar(db, "contact")
    avatars.save_avatar(db, "contact", "first", image_bytes())
    avatars.save_avatar(db, "contact", "second", image_bytes(color="blue"))
    assert employee.read_bytes() == b"employee"


@pytest.mark.parametrize("invalid_id", ["https://api.telegram.org/file/botSECRET/photo", "123:secret", "../photo", "", "x" * 256])
def test_reject_urls_tokens_and_unsafe_ids(db, storage, invalid_id):
    with pytest.raises(ValueError):
        avatars.save_avatar(db, "contact", invalid_id, image_bytes())
    assert snapshot(db) == (None, None, None, None)
    assert not storage.exists()


def test_exif_orientation_is_applied():
    exif = Image.Exif()
    exif[274] = 6
    thumbnail, _ = avatars.prepare_avatar(image_bytes(kind="JPEG", exif=exif))
    with Image.open(BytesIO(thumbnail)) as image:
        assert image.getchannel("A").getbbox() == (64, 0, 192, 256)


def test_reject_animated_images():
    output = BytesIO()
    Image.new("RGB", (10, 10), "red").save(
        output, format="PNG", save_all=True, append_images=[Image.new("RGB", (10, 10), "blue")])
    with pytest.raises(ValueError, match="Animated"):
        avatars.prepare_avatar(output.getvalue())


def test_filename_collision_does_not_remove_previous_file(db, storage, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(avatars.uuid, "uuid4", lambda: SimpleNamespace(hex="a" * 32))
    content = image_bytes()
    avatars.save_avatar(db, "contact", "first", content)
    before = snapshot(db)
    with pytest.raises(FileExistsError):
        avatars.save_avatar(db, "contact", "second", image_bytes(color="blue"))
    assert snapshot(db) == before
    assert (storage / Path(before[2]).name).read_bytes() == content


def test_contacts_have_independent_full_files(db, storage):
    db.add(TelegramContact(id="other", company_id="company", client_id="client",
                           external_user_id="789", external_chat_id="999"))
    db.commit()
    avatars.save_avatar(db, "contact", "same-telegram-photo", image_bytes())
    first = snapshot(db)
    avatars.save_avatar(db, "other", "same-telegram-photo", image_bytes())
    other_path = db.get(TelegramContact, "other").avatar_path
    avatars.delete_full_avatar(db, "contact")
    assert other_path != first[2]
    assert (storage / Path(other_path).name).exists()
    assert len(list(storage.iterdir())) == 1


def test_missing_contact_does_not_write_files(db, storage):
    with pytest.raises(ValueError, match="does not exist"):
        avatars.save_avatar(db, "missing", "first", image_bytes())
    assert not storage.exists()
