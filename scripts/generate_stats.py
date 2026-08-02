#!/usr/bin/env python3
"""Render GitHub stats cards as SVGs for the profile README.

Queries the GitHub GraphQL API for public repository data and writes four files
into assets/: a summary card and a top-languages card, each in a light and a
dark variant so the README can serve them through <picture>.

No third-party service and no personal access token - the built-in GITHUB_TOKEN
that Actions provides is enough to read public data, which is all these cards
show.

Usage:
    GITHUB_TOKEN=... GITHUB_LOGIN=bhargava-sarma python3 scripts/generate_stats.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

API = "https://api.github.com/graphql"
ASSETS = Path(__file__).resolve().parent.parent / "assets"

# GitHub's own interface colours, so the cards sit naturally on either theme.
THEMES = {
    "light": {
        "text": "#1f2328",
        "muted": "#59636e",
        "border": "#d1d9e0",
        "track": "#eaeef2",
    },
    "dark": {
        "text": "#f0f6fc",
        "muted": "#9198a1",
        "border": "#3d444d",
        "track": "#21262d",
    },
}

FONT = (
    "ui-sans-serif,-apple-system,BlinkMacSystemFont,'Segoe UI',"
    "Helvetica,Arial,sans-serif"
)

WIDTH = 440
HEIGHT = 170
TOP_LANGUAGES = 6

QUERY = """
query($login: String!, $after: String) {
  user(login: $login) {
    followers { totalCount }
    contributionsCollection {
      totalCommitContributions
      totalPullRequestContributions
    }
    repositories(
      first: 100
      after: $after
      ownerAffiliations: OWNER
      isFork: false
      privacy: PUBLIC
    ) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        stargazerCount
        languages(first: 12, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
      }
    }
  }
}
"""


def graphql(token: str, login: str, after: str | None) -> dict:
    payload = json.dumps(
        {"query": QUERY, "variables": {"login": login, "after": after}}
    ).encode()
    request = urllib.request.Request(
        API,
        data=payload,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": f"{login}-profile-stats",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.load(response)
    if "errors" in body:
        raise SystemExit(f"GraphQL error: {body['errors']}")
    return body["data"]["user"]


def collect(token: str, login: str) -> tuple[dict, list[tuple[str, str, int]]]:
    """Return headline totals and language sizes, following repo pagination."""
    stars = 0
    repos = 0
    sizes: dict[str, int] = {}
    colors: dict[str, str] = {}
    after = None
    summary: dict = {}

    while True:
        user = graphql(token, login, after)
        if not summary:
            contributions = user["contributionsCollection"]
            summary = {
                "followers": user["followers"]["totalCount"],
                "commits": contributions["totalCommitContributions"],
                "pull_requests": contributions["totalPullRequestContributions"],
                "repos": user["repositories"]["totalCount"],
            }

        page = user["repositories"]
        for node in page["nodes"]:
            repos += 1
            stars += node["stargazerCount"]
            for edge in node["languages"]["edges"]:
                name = edge["node"]["name"]
                sizes[name] = sizes.get(name, 0) + edge["size"]
                colors.setdefault(name, edge["node"]["color"] or "#8b949e")

        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]

    summary["stars"] = stars
    summary["repos"] = repos

    ranked = sorted(sizes.items(), key=lambda item: item[1], reverse=True)
    languages = [(name, colors[name], size) for name, size in ranked]
    return summary, languages


def compact(value: int) -> str:
    if value >= 1000:
        return f"{value / 1000:.1f}k".replace(".0k", "k")
    return str(value)


def frame(theme: dict) -> str:
    return (
        f'<rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{HEIGHT - 1}" '
        f'rx="10" fill="none" stroke="{theme["border"]}"/>'
    )


def title(text: str, theme: dict) -> str:
    return (
        f'<text x="24" y="34" font-family="{FONT}" font-size="15" '
        f'font-weight="600" fill="{theme["text"]}">{escape(text)}</text>'
    )


def open_svg(label: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" '
        f'height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" '
        f'aria-label="{escape(label)}">'
    )


def stats_card(summary: dict, theme: dict) -> str:
    tiles = [
        (compact(summary["stars"]), "Total stars"),
        (compact(summary["repos"]), "Public repos"),
        (compact(summary["commits"]), "Commits (past year)"),
        (compact(summary["followers"]), "Followers"),
    ]

    parts = [open_svg("GitHub statistics"), frame(theme), title("GitHub", theme)]
    for index, (value, label) in enumerate(tiles):
        x = 24 + (index % 2) * 208
        y = 88 + (index // 2) * 52
        parts.append(
            f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="23" '
            f'font-weight="700" fill="{theme["text"]}">{escape(value)}</text>'
        )
        parts.append(
            f'<text x="{x}" y="{y + 18}" font-family="{FONT}" font-size="11" '
            f'fill="{theme["muted"]}">{escape(label)}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def languages_card(languages: list[tuple[str, str, int]], theme: dict) -> str:
    total = sum(size for _, _, size in languages)
    parts = [open_svg("Top languages"), frame(theme), title("Top languages", theme)]

    if not total:
        parts.append(
            f'<text x="24" y="90" font-family="{FONT}" font-size="12" '
            f'fill="{theme["muted"]}">No language data available</text></svg>'
        )
        return "".join(parts)

    shown = languages[:TOP_LANGUAGES]
    remainder = sum(size for _, _, size in languages[TOP_LANGUAGES:])
    entries = [(name, color, size / total * 100) for name, color, size in shown]
    if remainder:
        entries.append(("Other", theme["muted"], remainder / total * 100))

    bar_x, bar_y, bar_w, bar_h = 24, 52, WIDTH - 48, 10
    parts.append(
        f'<clipPath id="bar"><rect x="{bar_x}" y="{bar_y}" width="{bar_w}" '
        f'height="{bar_h}" rx="{bar_h / 2}"/></clipPath>'
    )
    parts.append(
        f'<rect x="{bar_x}" y="{bar_y}" width="{bar_w}" height="{bar_h}" '
        f'rx="{bar_h / 2}" fill="{theme["track"]}"/>'
    )
    parts.append('<g clip-path="url(#bar)">')
    offset = float(bar_x)
    for _, color, percent in entries:
        segment = bar_w * percent / 100
        parts.append(
            f'<rect x="{offset:.2f}" y="{bar_y}" width="{segment:.2f}" '
            f'height="{bar_h}" fill="{color}"/>'
        )
        offset += segment
    parts.append("</g>")

    for index, (name, color, percent) in enumerate(entries):
        x = 24 + (index % 2) * 208
        y = 92 + (index // 2) * 24
        parts.append(
            f'<circle cx="{x + 5}" cy="{y - 4}" r="5" fill="{color}"/>'
        )
        parts.append(
            f'<text x="{x + 18}" y="{y}" font-family="{FONT}" font-size="12" '
            f'fill="{theme["text"]}">{escape(name)}</text>'
        )
        parts.append(
            f'<text x="{x + 176}" y="{y}" font-family="{FONT}" font-size="12" '
            f'text-anchor="end" fill="{theme["muted"]}">{percent:.1f}%</text>'
        )

    parts.append("</svg>")
    return "".join(parts)


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN")
    login = os.environ.get("GITHUB_LOGIN")
    if not token or not login:
        print("GITHUB_TOKEN and GITHUB_LOGIN must be set", file=sys.stderr)
        return 1

    try:
        summary, languages = collect(token, login)
    except urllib.error.URLError as error:
        print(f"GitHub API request failed: {error}", file=sys.stderr)
        return 1

    ASSETS.mkdir(exist_ok=True)
    for name, theme in THEMES.items():
        (ASSETS / f"stats-{name}.svg").write_text(stats_card(summary, theme))
        (ASSETS / f"languages-{name}.svg").write_text(
            languages_card(languages, theme)
        )

    print(
        f"Wrote 4 cards to {ASSETS.name}/ - {summary['repos']} repos, "
        f"{summary['stars']} stars, {len(languages)} languages"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
