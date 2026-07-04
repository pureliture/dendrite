from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_HERMES_PROFILE = "default"
HERMES_DEFAULT_HOME = ".hermes"
HERMES_STATE_DB = "state.db"


@dataclass(frozen=True)
class HermesProfileStore:
    profile: str
    path: Path


def slug_hermes_profile(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return DEFAULT_HERMES_PROFILE
    slug = "".join(char.lower() if char.isalnum() else "-" for char in text)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or DEFAULT_HERMES_PROFILE


def hermes_agent_id(profile: object) -> str:
    return f"hermes-{slug_hermes_profile(profile)}-transcript-capture"


def hermes_session_hash_seed(session_id: str, profile: object) -> str:
    return f"hermes:{slug_hermes_profile(profile)}:{session_id}"


def hermes_profile_from_payload(payload: dict, *, locator: str = "") -> str:
    for key in ("hermes_profile", "profile", "profile_name", "HERMES_PROFILE"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return slug_hermes_profile(value)
    return hermes_profile_from_state_db_path(locator)


def hermes_profile_from_state_db_path(path_value: str | Path | None) -> str:
    if not path_value:
        return DEFAULT_HERMES_PROFILE
    parts = [part for part in Path(path_value).parts if part]
    lower_parts = [part.lower() for part in parts]
    if "profiles" in lower_parts:
        marker_index = lower_parts.index("profiles")
        if marker_index + 1 < len(parts):
            return slug_hermes_profile(parts[marker_index + 1])
    return DEFAULT_HERMES_PROFILE


def default_hermes_home() -> Path:
    return Path.home() / HERMES_DEFAULT_HOME


def state_db_for_profile(hermes_home: Path, profile: object) -> Path:
    profile_slug = slug_hermes_profile(profile)
    if profile_slug == DEFAULT_HERMES_PROFILE:
        return hermes_home / HERMES_STATE_DB
    return hermes_home / "profiles" / profile_slug / HERMES_STATE_DB


def discover_hermes_profile_stores(root: str | Path | None = None) -> list[HermesProfileStore]:
    """Discover default and named Hermes profile stores without opening them."""
    if root is None:
        root_path = default_hermes_home()
    else:
        root_path = Path(root).expanduser()
    if root_path.is_file() and not root_path.is_symlink():
        return [HermesProfileStore(hermes_profile_from_state_db_path(root_path), root_path)]
    if not root_path.is_dir() or root_path.is_symlink():
        return []

    stores: list[HermesProfileStore] = []
    default_store = root_path / HERMES_STATE_DB
    if default_store.is_file() and not default_store.is_symlink():
        stores.append(HermesProfileStore(hermes_profile_from_state_db_path(default_store), default_store))

    profiles_root = root_path / "profiles"
    if profiles_root.is_dir() and not profiles_root.is_symlink():
        for profile_dir in sorted(path for path in profiles_root.iterdir() if path.is_dir() and not path.is_symlink()):
            state_db = profile_dir / HERMES_STATE_DB
            if state_db.is_file() and not state_db.is_symlink():
                stores.append(HermesProfileStore(slug_hermes_profile(profile_dir.name), state_db))
    return stores


def build_hermes_profile_readiness(root: str | Path | None = None) -> dict[str, dict]:
    """Return public-safe profile readiness; never include raw local paths."""
    if root is None:
        hermes_home = default_hermes_home()
    else:
        hermes_home = Path(root).expanduser()
    stores = discover_hermes_profile_stores(hermes_home)
    profiles = {store.profile for store in stores}
    if not profiles and hermes_home.is_dir() and not hermes_home.is_symlink():
        profiles.add(DEFAULT_HERMES_PROFILE)

    readiness: dict[str, dict] = {}
    store_by_profile = {store.profile: store for store in stores}
    for profile in sorted(profiles, key=lambda value: (value != DEFAULT_HERMES_PROFILE, value)):
        store = store_by_profile.get(profile)
        readiness[profile] = {
            "profile": profile,
            "source_status": "source_present" if store else "source_missing",
            "hook_status": _hook_status(hermes_home, profile),
            "agent_id": hermes_agent_id(profile),
            "recommended_command_shape": [
                "dendrite",
                "transcript-capture",
                "--provider",
                "hermes",
                "--hermes-profile",
                profile,
                "--project",
                "<project>",
                "--spool",
                "<private-transcript-capture-spool>",
                "--stdin-json",
                "--non-fatal",
            ],
        }
    return readiness


def _hook_status(hermes_home: Path, profile: str) -> str:
    config = hermes_home / "config.yaml"
    if profile != DEFAULT_HERMES_PROFILE:
        config = hermes_home / "profiles" / profile / "config.yaml"
    if not config.is_file() or config.is_symlink():
        return "hook_missing"
    try:
        text = config.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "hook_unreadable"
    if "on_session_end" in text and "transcript-capture" in text and "--provider hermes" in text:
        return "hook_present"
    return "hook_missing"
