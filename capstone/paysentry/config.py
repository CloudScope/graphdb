"""Configuration loading: ``config.yaml`` for behaviour, ``.env`` for secrets.

The split is strict and worth stating once, because it is a security boundary
rather than a preference: **no credential ever appears in ``config.yaml``**, which
is committed, and no tunable ever hides in ``.env``, which is not. If a value
would be embarrassing in a git diff it belongs in the environment; if it would be
useful in a code review it belongs in the YAML.

Access is deliberately fail-loud. A typo'd config key raises immediately with the
path that was missing, rather than returning ``None`` and producing a detector
that silently never fires — a failure mode that would be invisible in an
evaluation report and would quietly invalidate it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from .timeutil import parse_duration, to_ms

# capstone/ — the directory holding config.yaml, .env, data/ and reports/.
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config.yaml"
PROFILES = ("small", "medium", "large")


class ConfigError(RuntimeError):
    """Raised for a missing key, an unknown profile, or absent credentials."""


# --------------------------------------------------------------------------
# Nested access
# --------------------------------------------------------------------------

class Section:
    """A read-only view over a nested config mapping.

    Supports ``cfg.detection.temporal.cycle_max_hops`` and
    ``cfg.get("detection.temporal.cycle_max_hops")`` equally; both report the
    full dotted path when something is missing.
    """

    __slots__ = ("_data", "_path")

    def __init__(self, data: dict[str, Any], path: str = "") -> None:
        self._data = data
        self._path = path

    def __getattr__(self, name: str) -> Any:
        if name not in self._data:
            raise ConfigError(f"missing config key: {self._qualify(name)}")
        return self._wrap(self._data[name], self._qualify(name))

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __repr__(self) -> str:
        return f"Section({self._path or '<root>'}: {sorted(self._data)})"

    def get(self, dotted: str, default: Any = ...) -> Any:
        """Fetch by dotted path. Without a ``default``, a missing key raises."""
        node: Any = self
        for part in dotted.split("."):
            if not isinstance(node, Section) or part not in node._data:
                if default is ...:
                    raise ConfigError(f"missing config key: {self._qualify(dotted)}")
                return default
            node = getattr(node, part)
        return node

    def as_dict(self) -> dict[str, Any]:
        """A deep copy, for callers that need a plain mutable mapping."""
        import copy

        return copy.deepcopy(self._data)

    def duration_ms(self, dotted: str) -> int:
        """Fetch a duration-shaped value (``"24h"``) and return milliseconds."""
        return parse_duration(self.get(dotted))

    def _qualify(self, name: str) -> str:
        return f"{self._path}.{name}" if self._path else name

    @staticmethod
    def _wrap(value: Any, path: str) -> Any:
        return Section(value, path) if isinstance(value, dict) else value


# --------------------------------------------------------------------------
# Typed pieces
# --------------------------------------------------------------------------

@dataclass(slots=True, frozen=True)
class Profile:
    """One row of ``generation.profiles`` (DESIGN.md §4.1)."""

    name: str
    customers: int
    accounts: int
    devices: int
    merchants: int
    transactions: int
    span_days: int

    def scaled(self, per_profile: dict[str, int]) -> int:
        """Pick this profile's value out of a ``{small:, medium:, large:}`` map.

        Typology ring counts are expressed that way in ``config.yaml`` so a
        profile switch scales the planted patterns along with the background
        traffic, instead of leaving three rings adrift in two million events.
        """
        if self.name not in per_profile:
            raise ConfigError(
                f"value not defined for profile {self.name!r}; have {sorted(per_profile)}"
            )
        return per_profile[self.name]


@dataclass(slots=True, frozen=True)
class SavannaCredentials:
    """TigerGraph Savanna connection details, read from the environment only."""

    host: str
    username: str
    password: str
    graph: str
    rest_port: str = "443"
    gsql_port: str = "443"
    secret: str | None = None

    @property
    def is_jwt(self) -> bool:
        """True when the credential is a JWT rather than a database secret.

        Savanna's console hands out several credential shapes and they are easy
        to confuse. A JWT is three dot-separated base64 segments beginning
        ``eyJ``; a database secret is a ~32-character alphanumeric string.
        pyTigerGraph takes them as *different* arguments — ``jwtToken`` versus
        ``gsqlSecret`` — so passing one as the other simply fails to authenticate.
        """
        value = (self.secret or "").strip()
        return value.startswith("eyJ") and value.count(".") == 2

    @property
    def auth_kind(self) -> str:
        if self.is_jwt:
            return "jwt"
        if self.secret:
            return "secret"
        if self.username and self.password:
            return "password"
        return "none"

    @property
    def is_configured(self) -> bool:
        return bool(self.host and self.graph
                    and (self.secret or (self.username and self.password)))

    @property
    def normalized_host(self) -> str:
        """Reduce whatever was pasted into TG_HOST to a scheme + hostname.

        The console shows this value in several shapes depending on where you
        copy it from — a bare domain, a GraphStudio URL with a path, sometimes
        with a port. pyTigerGraph wants the origin only, so anything after the
        host is dropped rather than being a footgun:

            abcd.i.tgcloud.io                  -> https://abcd.i.tgcloud.io
            https://abcd.i.tgcloud.io/studio/  -> https://abcd.i.tgcloud.io
            https://abcd.i.tgcloud.io:14240    -> https://abcd.i.tgcloud.io
        """
        from urllib.parse import urlparse

        raw = self.host.strip()
        if not raw:
            return raw
        if not raw.startswith(("http://", "https://")):
            raw = f"https://{raw}"
        parsed = urlparse(raw)
        return f"{parsed.scheme}://{parsed.hostname}" if parsed.hostname else ""

    def missing(self) -> list[str]:
        """Which variables still need setting, in the order to go and find them."""
        gaps = []
        if not self.host:
            gaps.append("TG_HOST")
        if not self.graph:
            gaps.append("TG_GRAPH")
        if not self.secret and not (self.username and self.password):
            gaps.append("TG_SECRET")
        return gaps

    def redacted(self) -> dict[str, str]:
        """Safe to print or log — no secret material (DESIGN.md §10.1)."""
        return {
            "host": self.normalized_host or "<unset>",
            "graph": self.graph or "<unset>",
            "credential": (f"<{self.auth_kind}, {len(self.secret)} chars>"
                           if self.secret else "<unset>"),
            "username": self.username or "<unset (not used by Savanna)>",
            "password": "<set>" if self.password else "<unset (not used by Savanna)>",
        }


@dataclass(slots=True, frozen=True)
class Paths:
    """Filesystem layout. Data is per-profile so profiles never overwrite each other."""

    root: Path
    data_dir: Path
    reports_dir: Path
    profile: str

    @property
    def profile_dir(self) -> Path:
        return self.data_dir / self.profile

    @property
    def events(self) -> Path:
        return self.profile_dir / "events.jsonl"

    @property
    def entities(self) -> Path:
        return self.profile_dir / "entities.json"

    @property
    def labels(self) -> Path:
        return self.profile_dir / "labels.jsonl"

    @property
    def decisions(self) -> Path:
        return self.profile_dir / "decisions.jsonl"

    def decisions_for(self, engine: str) -> Path:
        """Decisions from a named engine, kept alongside the default file.

        A replay overwrites ``decisions.jsonl``, so a run against one store would
        otherwise destroy the record of the other — and the whole point is to
        compare them.
        """
        return self.profile_dir / f"decisions.{engine}.jsonl"

    @property
    def signals(self) -> Path:
        return self.profile_dir / "signals.jsonl"

    @property
    def scores(self) -> Path:
        return self.profile_dir / "scores.jsonl"

    @property
    def local_db(self) -> Path:
        return self.profile_dir / "local_store.sqlite"

    def ensure(self) -> None:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# Top-level config
# --------------------------------------------------------------------------

@dataclass(slots=True)
class Config:
    raw: dict[str, Any]
    profile: Profile
    paths: Paths
    config_path: Path

    # -- sections --------------------------------------------------------
    @property
    def generation(self) -> Section:
        return Section(self.raw["generation"], "generation")

    @property
    def detection(self) -> Section:
        return Section(self.raw["detection"], "detection")

    @property
    def runtime(self) -> Section:
        return Section(self.raw["runtime"], "runtime")

    @property
    def end_time_ms(self) -> int:
        """The simulated clock's end instant; data is generated backwards from here."""
        return to_ms(self.generation.end_time)

    def get(self, dotted: str, default: Any = ...) -> Any:
        return Section(self.raw).get(dotted, default)

    # -- credentials -----------------------------------------------------
    @staticmethod
    def savanna_credentials(env_file: Path | None = None) -> SavannaCredentials:
        """Read Savanna credentials from ``.env`` and the process environment.

        Deliberately not called during ``load()``: everything except the
        TigerGraph-backed phases must work on a machine that has never seen a
        credential, so the read happens only when a Savanna store is constructed.
        """
        load_dotenv(env_file or ROOT / ".env", override=False)
        return SavannaCredentials(
            host=os.getenv("TG_HOST", "").strip(),
            username=os.getenv("TG_USER", "").strip(),
            password=os.getenv("TG_PASSWORD", "").strip(),
            graph=os.getenv("TG_GRAPH", "PaySentry").strip(),
            rest_port=os.getenv("TG_REST_PORT", "443").strip(),
            gsql_port=os.getenv("TG_GSQL_PORT", "443").strip(),
            secret=(os.getenv("TG_SECRET", "").strip() or None),
        )

    # -- construction ----------------------------------------------------
    @classmethod
    def load(cls, profile: str = "small", config_path: Path | str | None = None) -> Config:
        path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        if not path.exists():
            raise ConfigError(f"config file not found: {path}")
        raw = yaml.safe_load(path.read_text()) or {}

        for section in ("generation", "detection", "runtime"):
            if section not in raw:
                raise ConfigError(f"config is missing the '{section}' section: {path}")

        profiles = raw["generation"].get("profiles", {})
        if profile not in profiles:
            raise ConfigError(
                f"unknown profile {profile!r}; config defines {sorted(profiles)}"
            )
        resolved = Profile(name=profile, **profiles[profile])

        paths_cfg = raw["runtime"].get("paths", {})
        paths = Paths(
            root=ROOT,
            data_dir=ROOT / paths_cfg.get("data_dir", "data"),
            reports_dir=ROOT / paths_cfg.get("reports_dir", "reports"),
            profile=profile,
        )
        cfg = cls(raw=raw, profile=resolved, paths=paths, config_path=path)
        cfg.validate()
        return cfg

    def validate(self) -> None:
        """Catch the config errors that would otherwise surface as bad science.

        A payment mix that does not sum to 1, or 24 missing hourly weights, would
        skew the background traffic in ways that look like a detector working.
        """
        mix = self.generation.background.mix.as_dict()
        total = sum(mix.values())
        if abs(total - 1.0) > 1e-6:
            raise ConfigError(
                f"generation.background.mix must sum to 1.0, got {total:.6f} ({mix})"
            )

        weights = self.generation.background.hourly_weights
        if len(weights) != 24:
            raise ConfigError(
                f"generation.background.hourly_weights needs 24 entries, got {len(weights)}"
            )

        block_at = self.detection.decisions.block_at
        review_at = self.detection.decisions.review_at
        if not 0 < review_at < block_at:
            raise ConfigError(
                f"decision bands must satisfy 0 < review_at < block_at, "
                f"got review_at={review_at}, block_at={block_at}"
            )

        # Weights are summed into a score capped at max_score; if they cannot
        # reach the block threshold, nothing is ever blocked and the report is
        # a flat zero that looks like a detector bug.
        for path, weights in (("detection.scoring.weights",
                               self.detection.scoring.weights.as_dict()),
                              ("detection.hot_path.hot_weights",
                               self.detection.hot_path.hot_weights.as_dict())):
            total = sum(weights.values())
            if total < block_at:
                raise ConfigError(
                    f"{path} sums to {total} but decisions.block_at is {block_at} "
                    f"— nothing could ever be blocked"
                )

        ok = self.detection.hot_path.device_share_customers_ok
        alarm = self.detection.hot_path.device_share_customers_alarm
        if not ok < alarm:
            raise ConfigError(
                f"device_share_customers_ok ({ok}) must be below "
                f"device_share_customers_alarm ({alarm})"
            )
