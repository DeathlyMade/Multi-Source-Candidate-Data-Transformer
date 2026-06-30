"""GitHub adapter -- UNSTRUCTURED source (design doc section 3).

A public REST/GraphQL API is available, but to keep runs deterministic, offline,
and ToS-safe (the doc uses the same "export fixture" stance for LinkedIn) this
adapter consumes a SAVED API-response export: a JSON file containing the user
object and their repos. Skills are INFERRED from repo languages (lower trust
than declared skills -- the reliability matrix encodes that).

Accepted shapes:
  {"user": {...}, "repos": [ {"language": "...", "languages": {...}}, ... ] }
  or the raw /users/{login} object alone, or a list of repos alone.
"""
from __future__ import annotations

import json
from typing import Iterable

from ..model import Claim, Source
from ..normalize.text import decode_bytes
from .base import Adapter, make_claim


class GithubApiAdapter(Adapter):
    source_type = "github"

    def parse(self, data: bytes, source: Source) -> Iterable[Claim]:
        obj = json.loads(decode_bytes(data))
        user = {}
        repos = []
        if isinstance(obj, dict) and ("user" in obj or "repos" in obj):
            user = obj.get("user") or {}
            repos = obj.get("repos") or []
        elif isinstance(obj, dict):
            user = obj  # raw user object
        elif isinstance(obj, list):
            repos = obj
        rid = f"{source.source_id}#gh=0"
        out: list[Claim] = []

        def add(path, raw, sub, loc):
            out.append(make_claim(source, rid, path, raw, f"github.{sub}", loc))

        if user.get("name"):
            add("full_name", user["name"], "name", "user.name")
        if user.get("bio"):
            add("headline", user["bio"], "bio", "user.bio")
        if user.get("email"):
            add("emails", user["email"], "email", "user.email")
        if user.get("html_url"):
            add("links", user["html_url"], "html_url", "user.html_url")
        elif user.get("login"):
            add("links", f"https://github.com/{user['login']}", "login", "user.login")
        if user.get("blog"):
            add("links", user["blog"], "blog", "user.blog")
        if user.get("location"):
            add("location.city", user["location"], "location", "user.location")

        # Aggregate languages across repos -> inferred skills (deterministic order).
        lang_bytes: dict[str, int] = {}
        for j, repo in enumerate(repos if isinstance(repos, list) else []):
            if not isinstance(repo, dict):
                continue
            if isinstance(repo.get("languages"), dict):
                for lang, n in repo["languages"].items():
                    try:
                        lang_bytes[lang] = lang_bytes.get(lang, 0) + int(n)
                    except (TypeError, ValueError):
                        lang_bytes[lang] = lang_bytes.get(lang, 0) + 1
            elif repo.get("language"):
                lang_bytes[repo["language"]] = lang_bytes.get(repo["language"], 0) + 1
        # Stable, deterministic ordering: by bytes desc then name asc.
        for lang, _n in sorted(lang_bytes.items(), key=lambda kv: (-kv[1], kv[0])):
            add("skills", lang, "language", f"languages:{lang}")
        return out
