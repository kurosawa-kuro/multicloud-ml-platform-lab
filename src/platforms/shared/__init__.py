"""Everything under `platforms/` that is not a platform.

`src/platforms/` answers exactly one question at its top level: **is this a
platform?** The five comparison subjects (`vertex` / `sagemaker` / `azureml` /
`databricks` / `snowflake`) and `neon` (the measurement destination, not a
subject) each get a package. Everything cross-cutting lives here, so a new file
never has to be judged against a vague "does this feel shared?".

    contracts/          the shape all five adapters must have (Protocol + the
                        one shared implementation: 1 operation = 1 ml_runs row)
    config.py           resolve adapter settings (defaults < config.yaml <
                        terraform outputs < environment)
    terraform_vars.py   resolve Terraform *inputs* from the same two sources
    factory.py          build adapters, and hold the deploy-argument差 in one place
    artifacts.py        ArtifactStore implementations (GCS / S3 / Blob)
    packaging_names.py  derive distribution names from pyproject

**This is not a dumping ground.** The rule from `contracts/ports.py` still
applies: only put something here when five platforms genuinely share it and the
caller must not see the difference. A helper used by one adapter belongs in that
adapter's package — the differences between platforms are the deliverable, and
hiding them under a shared name destroys what the lab measures.
"""
