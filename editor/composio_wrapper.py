from __future__ import annotations

import os

from composio import Composio

_client: Composio | None = None

DEFAULT_USER_ID = "rosettawang"

# Toolkit versions must be PINNED. Composio refuses manual tool execution without an
# explicit version ("latest" is rejected with ToolVersionRequiredError), so an unpinned
# client fails at publish time before it ever reaches the platform. Format is the bare
# datestamp -- NO "v" prefix (a "v..." string resolves to a nonexistent version and every
# slug comes back "Tool not found").
#
# Re-pin when Composio ships a newer toolkit and the slugs in social/*.py are re-verified;
# override per-toolkit without a code change via COMPOSIO_TOOLKIT_VERSION_<TOOLKIT>.
TOOLKIT_VERSIONS = {
    "instagram": "20260721_00",
}


def _toolkit_versions() -> dict[str, str]:
    """Pinned versions, with per-toolkit env overrides
    (e.g. COMPOSIO_TOOLKIT_VERSION_INSTAGRAM=20260801_00)."""
    versions = dict(TOOLKIT_VERSIONS)
    for toolkit in list(versions):
        override = os.environ.get(f"COMPOSIO_TOOLKIT_VERSION_{toolkit.upper()}", "").strip()
        if override:
            versions[toolkit] = override
    return versions


def get_client() -> Composio:
    global _client
    if _client is None:
        api_key = os.environ.get("COMPOSIO_API_KEY")
        if not api_key:
            raise RuntimeError(
                "COMPOSIO_API_KEY is not set -- get one at https://app.composio.dev "
                "and export it before launching the app."
            )
        _client = Composio(api_key=api_key, toolkit_versions=_toolkit_versions())
    return _client


def list_toolkit_actions(toolkit: str, user_id: str = DEFAULT_USER_ID) -> list[dict]:
    """Look up the real action slugs/descriptions for a toolkit (e.g. 'instagram')
    from your actual Composio account, rather than guessing them."""
    tools = get_client().tools.get(user_id=user_id, toolkits=[toolkit])
    return [
        {
            "slug": t["function"]["name"],
            "description": t["function"].get("description", ""),
        }
        for t in tools
    ]


def initiate_connection(toolkit: str, auth_config_id: str, user_id: str = DEFAULT_USER_ID, callback_url: str | None = None):
    """Start an OAuth connection for a toolkit (e.g. Instagram). Returns an object with
    .redirect_url (send the user there to approve) and .wait_for_connection()."""
    kwargs = {
        "user_id": user_id,
        "auth_config_id": auth_config_id,
        "config": {"auth_scheme": "OAUTH2"},
    }
    if callback_url:
        kwargs["callback_url"] = callback_url
    return get_client().connected_accounts.initiate(**kwargs)


def list_connected_accounts(toolkit: str | None = None, user_id: str = DEFAULT_USER_ID) -> list[dict]:
    """Actual OAuth-connected accounts for a user, optionally filtered to one toolkit.

    Distinct from list_toolkit_actions, which lists a toolkit's *actions* whether or not
    any account is connected — so it's the wrong thing to gate real posting on. This
    returns real connections. NOTE: the SDK's response shape varies by composio version;
    this normalizes defensively — re-check the field names on first live use."""
    accounts = get_client().connected_accounts.list(user_ids=[user_id])
    items = getattr(accounts, "items", None) or accounts or []
    out = []
    for a in items:
        d = a if isinstance(a, dict) else getattr(a, "__dict__", {}) or {}
        tk = d.get("toolkit") or d.get("app_name") or d.get("appName") or ""
        if isinstance(tk, dict):
            tk = tk.get("slug") or tk.get("name") or ""
        if toolkit and str(tk).lower() != toolkit.lower():
            continue
        out.append({
            "id": d.get("id") or d.get("nano_id") or d.get("connectedAccountId"),
            "toolkit": str(tk),
            "status": d.get("status") or d.get("connectionStatus") or "",
        })
    return out


def execute_action(action_slug: str, arguments: dict, user_id: str = DEFAULT_USER_ID):
    return get_client().tools.execute(action_slug, user_id=user_id, arguments=arguments)
