import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from generate_demo import generate

from esun_agent.analysis import Analysis
from esun_agent.common import load_json, resource, stamp
from esun_agent.evidence import EvidenceBundle, build_observations, verify_bundle
from esun_agent.models import DataPack, RunConfig
from esun_agent.pipeline import assemble


@pytest.fixture
def scenario(tmp_path):
    raw_dp, raw_bundle, raw_response = generate(tmp_path)
    dp = DataPack.model_validate(raw_dp)
    b = EvidenceBundle.model_validate(raw_bundle)
    config = RunConfig.model_validate(load_json(Path("config/google.json")))
    verified = verify_bundle(b, tmp_path, dp, config.strategy, "test")
    sources, observations, ids, features = build_observations(b, verified)
    a = Analysis.model_validate(raw_response)
    plan = assemble(
        a,
        dp,
        verified["prices"],
        sources,
        observations,
        "other",
        "TEST-only",
        "test:v1",
        stamp(),
        {},
    )
    return {
        "root": tmp_path,
        "dp": dp,
        "bundle": b,
        "config": config,
        "verified": verified,
        "response": raw_response,
        "analysis": a,
        "plan": plan,
        "ids": ids,
        "features": features,
        "raw_dp": raw_dp,
        "raw_bundle": raw_bundle,
    }


@pytest.fixture
def official_schema():
    return resource("dplan.schema.json")
