"""Regenerate the documented input/analysis contracts from the actual models."""

from pathlib import Path

from esun_agent.analysis import Analysis
from esun_agent.common import write_json
from esun_agent.evidence import EvidenceBundle
from esun_agent.extraction import ExtractedEvidence
from esun_agent.models import DataPack, RunConfig

for name, model in [
    ("evidence", EvidenceBundle),
    ("analysis", Analysis),
    ("datapack", DataPack),
    ("config", RunConfig),
    ("extraction", ExtractedEvidence),
]:
    write_json(Path("docs/schemas") / f"{name}.schema.json", model.model_json_schema())
