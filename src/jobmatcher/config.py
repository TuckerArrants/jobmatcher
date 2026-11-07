"""Where things live, and layered configuration (package defaults, then user overrides)."""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Paths:
    user: Path

    @classmethod
    def from_env(cls, user_dir: str | os.PathLike | None = None) -> "Paths":
        return cls(Path(user_dir or os.environ.get("JOBMATCH_USER_DIR") or "user").resolve())

    @property
    def profile(self) -> Path:
        return self.user / "profile.md"

    @property
    def companies(self) -> Path:
        return self.user / "companies.yaml"

    @property
    def filters(self) -> Path:
        return self.user / "filters.yaml"

    @property
    def decision(self) -> Path:
        return self.user / "decision.yaml"

    @property
    def resumes(self) -> Path:
        return self.user / "resumes.yaml"

    @property
    def labels(self) -> Path:
        return self.user / "labels.csv"

    @property
    def db(self) -> Path:
        return self.user / "data" / "jobs.sqlite"

    @property
    def cache(self) -> Path:
        return self.user / "cache"

    @property
    def reports(self) -> Path:
        return self.user / "reports"


def load_yaml(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name}: invalid YAML ({exc.__class__.__name__})") from None


def package_yaml(name: str) -> Any:
    text = resources.files("jobmatcher").joinpath("config", name).read_text(encoding="utf-8")
    return yaml.safe_load(text)


def deep_merge(base: dict, override: dict | None) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_layered(default_name: str, user_path: Path) -> dict:
    user = load_yaml(user_path)
    if user is not None and not isinstance(user, dict):
        raise ConfigError(f"{user_path.name}: expected a mapping at the top level")
    base = package_yaml(default_name)
    unknown = set(user or {}) - set(base)
    if unknown:
        raise ConfigError(f"{user_path.name}: unknown keys {sorted(unknown)}")
    return deep_merge(base, user)


def load_filters(paths: Paths) -> dict:
    return load_layered("filters.default.yaml", paths.filters)


@dataclass(frozen=True)
class Company:
    name: str
    ats: str
    token: str


def load_companies(paths: Paths) -> list[Company]:
    from jobmatcher.sources import ATS

    data = load_yaml(paths.companies) or {}
    items = data.get("companies", []) if isinstance(data, dict) else data
    out = []
    for i, item in enumerate(items or []):
        try:
            company = Company(str(item["name"]), str(item["ats"]).lower(), str(item["token"]))
        except (KeyError, TypeError):
            raise ConfigError(f"companies.yaml entry {i + 1}: needs name, ats and token") from None
        if company.ats not in ATS:
            raise ConfigError(
                f"companies.yaml entry {i + 1}: ats must be one of {sorted(ATS)}, not {company.ats!r}"
            )
        out.append(company)
    return out


def load_profile(paths: Paths) -> str:
    if not paths.profile.exists():
        raise ConfigError("user/profile.md not found; run `jobmatch init` or copy user.example/")
    return paths.profile.read_text(encoding="utf-8").strip()
