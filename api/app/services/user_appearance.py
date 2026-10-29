import hashlib
import json
from copy import deepcopy
from datetime import datetime
from secrets import randbelow
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models import BackgroundContour, User


MAX_APPEARANCE_SESSIONS = 32
LEGACY_ACCENT_HUES = {"purple": 259, "blue": 215, "red": 350, "green": 153, "orange": 29, "teal": 184, "mono": 0}
COLOR_FIELDS = tuple(f"{role}_{theme}" for theme in ("light", "dark")
                     for role in ("interface_hue", "interface_intensity", "accent_hue", "accent_strength"))
APPEARANCE_FIELDS = ("theme_preference", "accent_preference", "accent_intensity_light", "accent_intensity_dark", *COLOR_FIELDS,
                     "background_patterns")


def is_contour(value) -> bool:
    try:
        UUID(str(value))
        return True
    except (ValueError, TypeError):
        return False


def repair_background_contours(db: Session, user: User, *, locked=False) -> bool:
    if not any(is_contour(theme.get("selected")) for theme in (user.background_patterns_json or {}).values()):
        return False
    if not locked:
        _lock_appearance(db, user)
    stored = deepcopy(user.background_patterns_json or {})
    ids = {theme["selected"] for theme in stored.values() if is_contour(theme.get("selected"))}
    existing = set(db.scalars(select(BackgroundContour.id).where(BackgroundContour.id.in_(ids))))
    changed = False
    for theme in stored.values():
        if is_contour(theme.get("selected")) and theme["selected"] not in existing:
            theme.update(selected="grid", fallback_grid=True)
            changed = True
    if changed:
        user.background_patterns_json = stored
    return changed


def merge_background_patterns(db: Session, user: User, patch: dict) -> None:
    # Merge under the appearance row lock; never replace another theme/pattern.
    ids = {key for changes in patch.values() for key in [changes.get("selected"), *changes.get("patterns", {})]
           if is_contour(key)}
    # Hold a shared contour lock until the preference commit; deletion cannot pass validation in between.
    existing = set(db.scalars(select(BackgroundContour.id).where(BackgroundContour.id.in_(sorted(ids))).with_for_update(read=True))) if ids else set()
    if ids - existing:
        db.rollback()
        raise HTTPException(status_code=422, detail={"error": {"code": "background_contour_not_found",
                                                             "message": "Выбранный контур больше не существует"}})
    stored = deepcopy(user.background_patterns_json or {})
    for theme, changes in patch.items():
        if not changes:
            continue
        target = stored.setdefault(theme, {"selected": "grid", "patterns": {}})
        if "selected" in changes:
            target["selected"] = changes["selected"]
            target.pop("fallback_grid", None)
        parameters = changes.get("patterns", {})
        if target.get("fallback_grid") and "grid" in parameters:
            target.pop("fallback_grid", None)
        kinds = set(parameters)
        if target["selected"] != "none":
            kinds.add(target["selected"])
        for kind in sorted(kinds):
            if kind not in target["patterns"]:
                target["patterns"][kind] = {
                    "scale": 100, "intensity": 50, "variable": False,
                    "seed": parameters.get(kind, {}).get("seed", randbelow(65535) + 1),
                }
            target["patterns"][kind].update(parameters.get(kind, {}))
    if stored:
        user.background_patterns_json = stored


def appearance_colors(user: User) -> dict[str, int]:
    colors = {}
    for theme in ("light", "dark"):
        defaults = {
            f"interface_hue_{theme}": 225 if theme == "dark" else 259, f"interface_intensity_{theme}": 50,
            f"accent_hue_{theme}": LEGACY_ACCENT_HUES.get(user.accent_preference, 259),
            f"accent_strength_{theme}": 0 if user.accent_preference == "mono" else getattr(user, f"accent_intensity_{theme}"),
        }
        colors.update({field: getattr(user, field) if getattr(user, field) is not None else value
                       for field, value in defaults.items()})
    return colors


def convert_legacy_appearance(user: User, updates: dict) -> None:
    # Cached clients may still send the former fields; convert them under the same row lock.
    for theme in ("light", "dark"):
        if "accent_preference" in updates:
            updates.setdefault(f"accent_hue_{theme}", LEGACY_ACCENT_HUES[user.accent_preference])
        if "accent_preference" in updates or f"accent_intensity_{theme}" in updates:
            updates.setdefault(f"accent_strength_{theme}",
                               0 if user.accent_preference == "mono" else getattr(user, f"accent_intensity_{theme}"))


class AppearanceSessionExpired(ValueError):
    pass


def _lock_appearance(db: Session, user: User) -> None:
    # An UPDATE locks the row until commit/rollback on PostgreSQL and also
    # serializes writers on SQLite, where SELECT FOR UPDATE has no effect.
    db.execute(
        update(User).where(User.id == user.id)
        .values(appearance_write_state_json=User.appearance_write_state_json)
        .execution_options(synchronize_session=False)
    )
    db.refresh(user, attribute_names=["appearance_write_state_json", "background_patterns_json",
                                     *(field for field in APPEARANCE_FIELDS if field != "background_patterns")])


def appearance_revision(user: User) -> str:
    state = json.dumps([user.appearance_write_state_json or {}, user.background_patterns_json], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(state.encode()).hexdigest()


def register_appearance_session(
    db: Session, user: User, initial_preferences: dict | None = None, sequence: int = 0,
    expected_revision: str | None = None,
) -> str:
    _lock_appearance(db, user)
    repair_background_contours(db, user, locked=True)
    # A delayed/retried registration cannot overwrite a write accepted since its GET.
    accept_initial = expected_revision == appearance_revision(user)
    state = dict(user.appearance_write_state_json or {})
    # Keep registration order explicitly: JSONB does not preserve object key order.
    while len(state) >= MAX_APPEARANCE_SESSIONS:
        oldest = min(state, key=lambda key: state[key]["order"])
        del state[oldest]
    order = max((entry["order"] for entry in state.values()), default=0) + 1
    writer_id = str(uuid4())
    state[writer_id] = {"order": order, "sequence": sequence if accept_initial else 0}
    user.appearance_write_state_json = state
    # The first keepalive POST saves its snapshot without waiting for unloaded JS.
    updates = dict(initial_preferences or {}) if accept_initial else {}
    for field, value in updates.items():
        if field == "background_patterns":
            merge_background_patterns(db, user, value)
        else:
            setattr(user, field, value)
    convert_legacy_appearance(user, updates)
    for field in COLOR_FIELDS:
        if field in updates:
            setattr(user, field, updates[field])
    if updates:
        user.updated_at = datetime.utcnow()
    db.commit()
    return writer_id


def accept_appearance_write(db: Session, user: User, writer_id: str, sequence: int) -> bool:
    _lock_appearance(db, user)
    repair_background_contours(db, user, locked=True)
    state = dict(user.appearance_write_state_json or {})
    entry = state.get(writer_id)
    if entry is None:
        raise AppearanceSessionExpired()
    if sequence <= entry["sequence"]:
        return False
    state[writer_id] = {**entry, "sequence": sequence}
    user.appearance_write_state_json = state
    # The caller writes preferences and commits in this same transaction.
    return True
