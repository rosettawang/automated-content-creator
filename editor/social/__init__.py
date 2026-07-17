"""Social publishing — the distribution domain (spec: specs/social-core.md).

Kept as its own package so publishing logic never leaks into core.py/campaigns.py.
`base` holds the adapter Protocol + registry + the DryRunAdapter; `scheduler` holds
the DB-driven claim/publish loop. Real platform adapters (instagram, tiktok, …)
register themselves here in a separate spec (social-adapters).
"""
