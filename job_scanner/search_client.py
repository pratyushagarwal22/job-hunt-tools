"""Serper (serper.dev) Google search API client. Single-page call only."""

import os

import requests

_ENDPOINT = "https://google.serper.dev/search"


def search(
    query: str, tbs: str, gl: str, num: int, page: int, timeout: float = 15
) -> list[dict]:
    api_key = os.environ.get("SERPER_API_KEY")
    if not api_key:
        raise RuntimeError("SERPER_API_KEY is not set")

    response = requests.post(
        _ENDPOINT,
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        json={"q": query, "tbs": tbs, "gl": gl, "num": num, "page": page},
        timeout=timeout,
    )
    if not response.ok:
        raise RuntimeError(f"Serper search failed: HTTP {response.status_code}")

    return [
        {
            "title": item.get("title"),
            "link": item.get("link"),
            "snippet": item.get("snippet"),
            "date": item.get("date"),
            "position": item.get("position"),
        }
        for item in response.json().get("organic", [])
    ]
