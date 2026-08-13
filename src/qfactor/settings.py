from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def find_project_root(start: Path | None = None) -> Path:
    here = (start or Path.cwd()).resolve()
    for p in [here, *here.parents]:
        if (p / "configs" / "project.yaml").exists():
            return p
    return here


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    tushare_token: str = ""
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    qfactor_root: str = ""

    @property
    def root(self) -> Path:
        if self.qfactor_root:
            return Path(self.qfactor_root).resolve()
        return find_project_root()


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be mapping: {path}")
    return data


class ProjectConfig:
    def __init__(self, root: Path | None = None):
        self.root = root or get_settings().root
        self.project = load_yaml(self.root / "configs" / "project.yaml")
        self.eval = load_yaml(self.root / "configs" / "eval_thresholds.yaml")
        self.data_sources = load_yaml(self.root / "configs" / "data_sources.yaml")
        self.fields = load_yaml(self.root / "data_catalog" / "fields.yaml")
        self.universes = load_yaml(self.root / "data_catalog" / "universes.yaml")

    @property
    def universe(self) -> str:
        return str(self.project.get("universe", "csi100"))

    @property
    def frequency(self) -> str:
        return str(self.project.get("frequency", "daily"))

    def path(self, key: str) -> Path:
        rel = self.project.get("paths", {}).get(key)
        if not rel:
            raise KeyError(f"Unknown path key: {key}")
        return (self.root / rel).resolve()

    def ensure_dirs(self) -> None:
        for key in ("data_raw", "data_processed", "factor_lib", "runs"):
            self.path(key).mkdir(parents=True, exist_ok=True)
        (self.path("data_processed") / "universe" / self.universe).mkdir(
            parents=True, exist_ok=True
        )
        (self.path("data_processed") / "bars" / "daily").mkdir(parents=True, exist_ok=True)
        (self.path("data_processed") / "calendar").mkdir(parents=True, exist_ok=True)


@lru_cache
def get_project_config() -> ProjectConfig:
    return ProjectConfig()