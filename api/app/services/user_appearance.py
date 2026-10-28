import hashlib
import json
from copy import deepcopy
from datetime import datetime
from secrets import randbelow
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..models import User


MAX_APPEARANCE_SESSIONS = 32
LEGACY_ACCENT_HUES = {"purple": 259, "blue": 215, "red": 350, "green": 153, "orange": 29, "teal": 184, "mono": 0}
COLOR_FIELDS = tuple(f"{role}_{theme}" for theme in ("light", "dark")
                     for role in ("interface_hue", "interface_intensity", "accent_hue", "accent_strength"))
APPEARANCE_FIELDS = ("theme_preference", "accent_preference", "accent_intensity_light", "accent_intensity_dark", *COLOR_FIELDS,
                     "background_patterns")


def merge_background_patterns(user: User, patch: dict) -> None:
    # Merge under the appearance row lock; never replace another theme/pattern.
    stored = deepcopy(user.background_patterns_json or {})
    for theme, changes in patch.items():
        if not changes:
            continue
        target = stored.setdefault(theme, {"selected": "grid", "patterns": {}})
        if "selected" in changes:
            target["selected"] = changes["selected"]
        parameters = changes.get("patterns", {})
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
    state = json.dumps(user.appearance_write_state_json or {}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(state.encode()).hexdigest()


def register_appearance_session(
    db: Session, user: User, initial_preferences: dict | None = None, sequence: int = 0,
    expected_revision: str | None = None,
) -> str:
    _lock_appearance(db, user)
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
            merge_background_patterns(user, value)
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
