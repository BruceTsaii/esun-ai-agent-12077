from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")


class AgentError(ValueError):
    """Actionable input, provider or readiness failure (safe to display)."""


def now() -> datetime:
    return datetime.now(TAIPEI)


def stamp() -> str:
    return now().isoformat(timespec="seconds")


def decimal(value) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise AgentError("數值不可為 NaN/Infinity")
    return result


def load_json(path: str | Path):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise AgentError(f"JSON 重複鍵: {k}")
            result[k] = v
        return result

    def invalid(value):
        raise AgentError(f"不合法 JSON 數值: {value}")

    return json.loads(
        Path(path).read_text(encoding="utf-8"), object_pairs_hook=pairs, parse_constant=invalid
    )


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def resource(name: str):
    return json.loads(files("esun_agent.resources").joinpath(name).read_text())


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def code_snapshot(destination: Path, runtime_config: dict | None = None) -> str:
    """Include code, prompts, default strategy, universe, config and lock in each run."""
    import zipfile

    package = Path(__file__).resolve().parent
    project = package.parent.parent
    paths = [
        ("src/esun_agent/" + p.relative_to(package).as_posix(), p)
        for p in sorted(package.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    ]
    # Read package code even after wheel installation, where no src/ checkout exists.
    if (project / "pyproject.toml").is_file():
        paths += [
            (p.relative_to(project).as_posix(), p)
            for p in sorted((project / "config").glob("*"))
            if p.is_file()
        ]
        paths += [(n, project / n) for n in ["pyproject.toml", "uv.lock"] if (project / n).exists()]
    digest = hashlib.sha256()
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, p in paths:
            raw = p.read_bytes()
            digest.update(name.encode() + b"\0" + raw + b"\0")
            archive.writestr(name, raw)
        if runtime_config is not None:
            name = "runtime-config.json"
            raw = json.dumps(runtime_config, sort_keys=True, ensure_ascii=False).encode()
            digest.update(name.encode() + b"\0" + raw + b"\0")
            archive.writestr(name, raw)
    return "sha256:" + digest.hexdigest()[:48]
