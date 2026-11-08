"""Antigravity read-side helpers (metadata, workspace, project resolution)."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from urllib.parse import unquote, urlparse

from .helpers_workspace import usable_project_source_path, workspace_uri_to_path

ANTIGRAVITY_HOME_ENV = "ANTIGRAVITY_HOME"
ANTIGRAVITY_DEFAULT_HOME = ".gemini/antigravity-cli"
ANTIGRAVITY_DEFAULT_CONFIG_HOME = ".gemini/config"
ANTIGRAVITY_PROJECTS_DIR = "projects"
ANTIGRAVITY_CONVERSATIONS_DIR = "conversations"
ANTIGRAVITY_SUMMARY_STORE_NAME = "conversation_summaries.db"
ANTIGRAVITY_SUMMARY_TABLE = "conversation_summaries"


def resolve_antigravity_project(conversation_id: str) -> str:
    for project_id, workspace_uris in iter_antigravity_summary_rows(conversation_id):
        project = resolve_antigravity_project_definition(project_id)
        if project:
            return project
        workspace = resolve_antigravity_workspace_values(workspace_uris)
        if workspace:
            return workspace
    return ""


def resolve_antigravity_workspace(conversation_id: str) -> str:
    if not conversation_id:
        return ""
    for _pid, workspace_uris in iter_antigravity_summary_rows(conversation_id):
        workspace = resolve_antigravity_workspace_values(workspace_uris)
        if workspace:
            return workspace
    return ""


def iter_antigravity_summary_rows(conversation_id: str):
    if not conversation_id:
        return
    for store_path in antigravity_summary_stores():
        try:
            connection = sqlite3.connect(f"file:{store_path}?mode=ro&immutable=1", uri=True)
        except sqlite3.Error:
            continue
        try:
            try:
                row = connection.execute(
                    f"SELECT project_id, workspace_uris FROM {ANTIGRAVITY_SUMMARY_TABLE} "
                    "WHERE conversation_id = ? LIMIT 1", (conversation_id,),
                ).fetchone()
            except sqlite3.Error:
                try:
                    legacy_row = connection.execute(
                        f"SELECT workspace_uris FROM {ANTIGRAVITY_SUMMARY_TABLE} "
                        "WHERE conversation_id = ? LIMIT 1", (conversation_id,),
                    ).fetchone()
                except sqlite3.Error:
                    legacy_row = None
                row = ("", legacy_row[0]) if legacy_row else None
        finally:
            connection.close()
        if not row:
            continue
        yield str(row[0] or ""), row[1]


def resolve_antigravity_workspace_values(value) -> str:
    for candidate in iter_antigravity_workspace_values(value):
        workspace = workspace_uri_to_path(candidate)
        usable = usable_project_source_path(workspace)
        if usable:
            return usable
    return ""


def resolve_antigravity_project_definition(project_id: str) -> str:
    project_id = str(project_id or "").strip()
    if not project_id or any(sep in project_id for sep in ("/", "\\", "\x00")):
        return ""
    projects = Path.home() / ANTIGRAVITY_DEFAULT_CONFIG_HOME / ANTIGRAVITY_PROJECTS_DIR
    if not projects.is_dir() or projects.is_symlink():
        return ""
    definition_path = projects / f"{project_id}.json"
    if not definition_path.is_file() or definition_path.is_symlink():
        return ""
    try:
        with definition_path.open("r", encoding="utf-8") as stream:
            definition = json.load(stream)
    except (OSError, TypeError, ValueError):
        return ""
    for candidate in iter_antigravity_project_definition_values(definition):
        workspace = workspace_uri_to_path(candidate)
        usable = usable_project_source_path(workspace)
        if usable:
            return usable
    return ""


def iter_antigravity_project_definition_values(definition):
    if not isinstance(definition, dict):
        return
    project_resources = definition.get("projectResources")
    resources = project_resources.get("resources") if isinstance(project_resources, dict) else None
    if isinstance(resources, list) and resources:
        resource = resources[0]
        if isinstance(resource, dict):
            git_folder = resource.get("gitFolder")
            if isinstance(git_folder, dict):
                yield git_folder.get("folderUri")
            yield resource.get("folderUri")
            yield resource.get("name")
    yield definition.get("name")


def antigravity_summary_stores():
    home = Path(os.environ.get(ANTIGRAVITY_HOME_ENV) or (Path.home() / ANTIGRAVITY_DEFAULT_HOME)).expanduser()
    try:
        root_stores = tuple(p for p in home.glob("*.db") if p.is_file() and not p.is_symlink())
        canonical = tuple(p for p in root_stores if p.name == ANTIGRAVITY_SUMMARY_STORE_NAME)
        if canonical:
            return canonical
        conversations = home / ANTIGRAVITY_CONVERSATIONS_DIR
        nested = ()
        if conversations.is_dir():
            nested = tuple(p for p in conversations.rglob("*.db") if p.is_file() and not p.is_symlink())
        return tuple(sorted(set(root_stores + nested)))
    except OSError:
        return ()


def iter_antigravity_workspace_values(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return
        try:
            value = json.loads(text)
        except (TypeError, ValueError):
            yield text
            return
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from iter_antigravity_workspace_values(item)
    elif isinstance(value, dict):
        for key in ("workspace_uris", "workspaceUris", "uris", "paths", "path"):
            if key in value:
                yield from iter_antigravity_workspace_values(value[key])


def session_project_fallback(conversation_id: str) -> str:
    digest = hashlib.sha256(f"antigravity:{conversation_id}".encode("utf-8")).hexdigest()[:8]
    return f"unknown-{digest}(session)"
