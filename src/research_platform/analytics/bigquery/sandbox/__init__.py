"""Offline tooling for the #103 GCP_SANDBOX deploy-and-measure cycle.

Nothing in this package creates, deletes or queries GCP resources by itself.
``synthetic`` builds a deterministic synthetic OpenAlex cycle (raw assets,
batch-1 canonical Parquet, a composed batch-2 delta and its expected
publication decisions) entirely on the local machine.
"""
