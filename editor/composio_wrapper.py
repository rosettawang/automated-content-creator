from __future__ import annotations

import os

from composio import Composio

_client: Composio | None = None

DEFAULT_USER_ID = "rosettawang"


def get_client() -> Composio:
    global _client
    if _client is None:
        api_key = os.environ.get("COMPOSIO_API_KEY")
        if not api_key:
            raise RuntimeError(
                "COMPOSIO_API_KEY is not set -- get one at https://app.composio.dev "
                "and export it before launching the app."
            )
        _client = Composio(api_key=api_key)
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


def execute_action(action_slug: str, arguments: dict, user_id: str = DEFAULT_USER_ID):
    return get_client().tools.execute(action_slug, user_id=user_id, arguments=arguments)
