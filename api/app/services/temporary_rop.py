from __future__ import annotations

from ..models import ClientProfile


TEMPORARY_ROP_FIELD = "custom_vremennyy_rop"
TEMPORARY_ROP_DESTINATION = "Руководство"
TEMPORARY_ROP_SUFFIX = " ВРиО РОПа"


def is_temporary_rop(profile: ClientProfile | None) -> bool:
    return profile is not None and dict(profile.custom_fields or {}).get(TEMPORARY_ROP_FIELD) is True


def temporary_rop_destination(profile: ClientProfile) -> str | None:
    return TEMPORARY_ROP_DESTINATION if is_temporary_rop(profile) else profile.destination


def with_temporary_rop_suffix(name: str, profile: ClientProfile | None) -> str:
    if not name or not is_temporary_rop(profile) or name.endswith(TEMPORARY_ROP_SUFFIX):
        return name
    return f"{name}{TEMPORARY_ROP_SUFFIX}"
