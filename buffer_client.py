#!/usr/bin/env python3
"""Minimal Buffer GraphQL client: find the Instagram channel, queue a Story.

API notes (from developers.buffer.com)
- Endpoint https://api.buffer.com, header "Authorization: Bearer <key>".
- There is no upload endpoint: images are passed as public HTTPS URLs.
- createPost(input: CreatePostInput!) with schedulingType "automatic" publishes
  without a reminder; mode "customScheduled" + dueAt (UTC ISO) sets the time.
- Instagram settings live under metadata.instagram; type "story" makes it a Story.
"""
import json
import urllib.error
import urllib.request

API_URL = "https://api.buffer.com"


class BufferError(RuntimeError):
    pass


def _post(payload, key):
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": "story-promoter"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        raise BufferError(f"HTTP {e.code} from Buffer: {body}") from None


def gql(query, key, variables=None):
    data = _post({"query": query, "variables": variables or {}}, key)
    if data.get("errors"):
        raise BufferError("; ".join(e.get("message", "?") for e in data["errors"]))
    return data["data"]


def instagram_channel(key, name=None):
    """Return the id of the Instagram channel (optionally matched by name)."""
    orgs = gql("query { account { organizations { id name } } }", key)["account"]["organizations"]
    found = []
    for org in orgs:
        q = "query { channels(input: { organizationId: %s }) { id name service } }" % json.dumps(org["id"])
        found += [c for c in gql(q, key)["channels"] if c.get("service") == "instagram"]
    if name:
        found = [c for c in found if (c.get("name") or "").lower() == name.lower()]
    if not found:
        raise BufferError("No Instagram channel found in Buffer"
                          + (f" named {name!r}" if name else ""))
    if len(found) > 1:
        names = ", ".join(f"{c['name']} ({c['id']})" for c in found)
        raise BufferError(f"Several Instagram channels found ({names}); set "
                          "buffer.instagram_channel_name in config.json")
    return found[0]["id"]


CREATE_POST = """
mutation CreateStory($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess { post { id dueAt } }
    ... on MutationError { message }
  }
}
"""


def create_story(key, channel_id, image_url, due_at_utc):
    """Queue an image Story. due_at_utc looks like 2026-10-08T12:00:00.000Z."""
    story_input = {
        "channelId": channel_id,
        "schedulingType": "automatic",
        "mode": "customScheduled",
        "dueAt": due_at_utc,
        "assets": [{"image": {"url": image_url}}],
        "metadata": {"instagram": {"type": "story", "shouldShareToFeed": False}},
    }
    result = gql(CREATE_POST, key, {"input": story_input})["createPost"]
    if result.get("post"):
        return result["post"]
    raise BufferError(result.get("message") or f"unexpected response: {result}")
