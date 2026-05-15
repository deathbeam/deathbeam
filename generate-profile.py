#!/usr/bin/env python3
"""
Generate a fastfetch-themed GitHub profile README.

Uses the `gh` CLI for all API calls (must be authenticated).
Stats: total commits, lines added/deleted, stars, repos, languages.

Usage: python3 generate-profile.py
       python3 generate-profile.py --fast   # skip LOC sampling
"""

import datetime
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

USERNAME = "deathbeam"
BIRTHDAY = datetime.date(1996, 1, 1)

# ── helpers ──────────────────────────────────────────────────────────────


def gh(endpoint: str) -> dict | list:
    """Call ``gh api <endpoint>`` and return parsed JSON."""
    cmd = ["gh", "api", endpoint, "--jq", "."]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"gh api failed: {result.stderr.strip()}")
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


def gh_graphql(query: str, variables: dict | None = None) -> dict:
    """Run a GraphQL query via ``gh api graphql``."""
    args = ["gh", "api", "graphql", "-f", f"query={query}"]
    if variables:
        for k, v in variables.items():
            if v is not None:
                args += ["-f", f"{k}={v}"]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"gh graphql failed: {result.stderr.strip()}")
        data = json.loads(result.stdout)
        if "errors" in data:
            print(f"  GraphQL errors: {data['errors']}", file=sys.stderr)
        return data.get("data", {})
    except json.JSONDecodeError:
        return {}


def fmt(n: int | float) -> str:
    """Format a number with commas."""
    if isinstance(n, float):
        return f"{n:,.1f}"
    return f"{n:,}"


# ── ASCII art ───────────────────────────────────────────────────────────

# fastfetch-style logo (taken from fastfetch output)
ASCII_ART = [
    "                  -`",
    "                 .o+`",
    "                `ooo/",
    "               `+oooo:",
    "              `+oooooo:",
    "             -+oooooo+:",
    "           `/:-:++oooo+:",
    "          `/++++/+++++++:",
    "         `/++++++++++++++:",
    "        `/+++ooooooooooooo/`",
    "       ./ooosssso++osssssso+`",
    "      .oossssso-````/ossssss+`",
    "     -osssssso.      :ssssssso.",
    "    :osssssss/        osssso+++.",
    "   /ossssssss/        +ssssooo/-",
    " `/ossssso+/:-        -:/+osssso+-",
    "`+sso+:-`                 `.-/+oso:",
    "`++:.                           `-/+/",
    ".`                                 `/",
]

# ── data fetching ────────────────────────────────────────────────────────


def fetch_repos() -> list[dict]:
    """Fetch all owned non-fork repos (paginated)."""
    repos = []
    page = 1
    while True:
        ep = f"users/{USERNAME}/repos?per_page=100&page={page}&type=owner&sort=pushed"
        batch = gh(ep)
        if not batch:
            break
        repos.extend(r for r in batch if not r.get("fork"))
        if len(batch) < 100:
            break
        page += 1
    return repos


def fetch_total_commits() -> int:
    """Total commits by the user across all repos via search API."""
    data = gh("search/commits?q=author:deathbeam&per_page=1")
    return data.get("total_count", 0)


def fetch_commit_locs(repos: list[dict]) -> tuple[int, int, int]:
    """
    For each repo sample the first 100 commits' additions/deletions via
    GraphQL, then extrapolate to the repo's total commit count.

    Returns (total_commits_from_sample, total_additions, total_deletions).
    """
    SAMPLE_SIZE = 100
    total_commits_sampled = 0
    total_additions = 0
    total_deletions = 0

    for i, repo in enumerate(repos):
        name = repo["name"]
        print(f"  [{i + 1}/{len(repos)}] {name}...", end=" ", flush=True)

        if repo.get("size", 0) == 0:
            print("empty, skipped")
            continue

        # Skip the profile repo itself
        if name == USERNAME:
            print("profile repo, skipped")
            continue

        owner, rname = repo["full_name"].split("/")

        query = f"""
        query {{
          repository(name: "{rname}", owner: "{owner}") {{
            defaultBranchRef {{
              target {{
                ... on Commit {{
                  history(first: {SAMPLE_SIZE}) {{
                    totalCount
                    edges {{
                      node {{
                        additions
                        deletions
                      }}
                    }}
                  }}
                }}
              }}
            }}
          }}
        }}
        """

        data = gh_graphql(query)
        ref = (data.get("repository", {})
               .get("defaultBranchRef", {}))
        if not ref:
            print("no default branch, skipped")
            continue

        target = ref.get("target", {})
        history = target.get("history", {})
        total_count = history.get("totalCount", 0)
        if total_count == 0:
            print("no commits, skipped")
            continue

        edges = history.get("edges", [])
        sample_additions = sum(
            e["node"].get("additions", 0) or 0 for e in edges if e.get("node")
        )
        sample_deletions = sum(
            e["node"].get("deletions", 0) or 0 for e in edges if e.get("node")
        )
        sample_size = len(edges)

        if sample_size == 0:
            print("no sampled commits, skipped")
            continue

        # Extrapolate: avg per commit in sample * total commits
        avg_add = sample_additions / sample_size
        avg_del = sample_deletions / sample_size
        est_add = round(avg_add * total_count)
        est_del = round(avg_del * total_count)

        total_additions += est_add
        total_deletions += est_del
        total_commits_sampled += total_count

        print(
            f"~{fmt(est_add)}/+{fmt(est_del)} "
            f"(sampled {sample_size}/{total_count} commits)"
        )

    print(
        f"\n  Total: ~{fmt(total_additions)} added, ~{fmt(total_deletions)} deleted, "
        f"across {fmt(total_commits_sampled)} commits"
    )
    return total_commits_sampled, total_additions, total_deletions


def fetch_languages() -> list[tuple[str, float]]:
    """
    Fetch language stats across all repos via GraphQL.
    Returns list of (name, percentage) for top languages.
    """
    LANG_QUERY = """
    query($login: String!, $cursor: String) {
      user(login: $login) {
        repositories(first: 100, after: $cursor, ownerAffiliations: OWNER, isFork: false) {
          nodes {
            languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
              edges {
                size
                node { name }
              }
            }
          }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
    """
    lang_bytes = Counter()
    cursor = None
    pages = 0

    while pages < 10:
        vars_dict = {"login": USERNAME}
        if cursor:
            vars_dict["cursor"] = cursor
        data = gh_graphql(LANG_QUERY, vars_dict)
        user_data = data.get("user", {})
        repos_data = user_data.get("repositories", {})
        nodes = repos_data.get("nodes", [])
        for repo_node in nodes:
            langs = repo_node.get("languages", {})
            for edge in langs.get("edges", []):
                lang_bytes[edge["node"]["name"]] += edge["size"]

        page_info = repos_data.get("pageInfo", {})
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        pages += 1

    total = sum(lang_bytes.values())
    if total == 0:
        return []
    return [(lang, round(b / total * 100, 1))
            for lang, b in lang_bytes.most_common(6)]


def calc_uptime(birthday: datetime.date) -> str:
    """Human-readable age."""
    now = datetime.datetime.now()
    bday = datetime.datetime.combine(birthday, datetime.time.min)
    diff = now - bday
    days = diff.days
    hours, remainder = divmod(diff.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{days:,} days, {hours} hours, {minutes} mins"


# ── main ─────────────────────────────────────────────────────────────────


def main() -> None:
    start = time.perf_counter()
    fast_mode = "--fast" in sys.argv
    print("Fetching GitHub stats...\n")

    # basic info
    print(" User info...", end=" ", flush=True)
    user_data = gh(f"users/{USERNAME}")
    name = user_data.get("name") or USERNAME
    followers = user_data.get("followers", 0)
    print(f"{name}, {fmt(followers)} followers")

    # repos
    print(" Repos...", end=" ", flush=True)
    repos = fetch_repos()
    print(f"{len(repos)} owned repos")

    # total commits
    print(" Total commits...", end=" ", flush=True)
    total_commits = fetch_total_commits()
    print(fmt(total_commits))

    # LOC per repo (skip with --fast)
    if fast_mode:
        total_additions = 0
        total_deletions = 0
        changed = 0
        net_loc = 0
        print(" LOC (skipped with --fast)")
    else:
        print(" LOC (extrapolated from sample)...")
        _, total_additions, total_deletions = fetch_commit_locs(repos)
        net_loc = total_additions - total_deletions
        changed = total_additions + total_deletions

    # stars
    print(" Stars...", end=" ", flush=True)
    total_stars = sum(r.get("stargazers_count", 0) for r in repos)
    print(fmt(total_stars))

    # top repo
    top_repo = max(repos, key=lambda r: r.get("stargazers_count", 0))
    top_repo_name = top_repo["name"]
    top_repo_stars = top_repo.get("stargazers_count", 0)

    # languages
    print(" Languages...", end=" ", flush=True)
    languages = fetch_languages()
    if languages:
        lang_str = ", ".join(f"{l[0]} {l[1]}%" for l in languages)
    else:
        lang_str = "\u2014"
    print(lang_str)

    # uptime
    uptime = calc_uptime(BIRTHDAY)

    elapsed = time.perf_counter() - start
    print(f"\n Done in {elapsed:.1f}s")
    print(f" Fetched {len(repos)} repos, {fmt(total_commits)} commits")
    print()

    # fastfetch-themed output
    right_lines = [
        f"                    {USERNAME}@{name}",
        "                    -------------------",
        f"                    OS: {name}",
        f"                    Host: Slovakia",
        f"                    Kernel: Human v30.0",
        f"                    Uptime: {uptime}",
        f"                    Packages: {fmt(total_commits)}",
        f"                    Memory: {fmt(total_additions)} / {fmt(total_deletions)} ({fmt(net_loc)} net)",
        f"                    Disk: {fmt(total_stars)} (⭐)",
        f"                    Display: {lang_str}",
        f"                    WM: Neovim",
        f"                    Shell: zsh 5.9",
        f"                    Terminal: tmux 3.6a",
        f"                    CPU: {top_repo_name} ({fmt(top_repo_stars)} ⭐)",
        f"                    GPU: {fmt(changed)}",
        f"                    Local IP: slusnucky@gmail.com",
        f"                    Locale: sk_SK.UTF-8",
    ]

    # Align: each ascii row on the left, right-side info row on the right
    max_left = max(len(l) for l in ASCII_ART)
    lines = []
    for i, left in enumerate(ASCII_ART):
        if i < len(right_lines):
            padded = left.ljust(max_left)
            lines.append(f"{padded}{right_lines[i]}")
        else:
            lines.append(left)

    # Combine into block
    readme_lines = ["```"] + lines + ["", "```", ""]
    readme = "\n".join(readme_lines)

    # Build trophy line
    trophy = "https://github-profile-trophy.vercel.app/?username=deathbeam&theme=nord&no-frame=true&margin-w=20&margin-h=20"

    # Write to README.md
    output_path = (Path(__file__).resolve().parent / "README.md").resolve()
    with open(output_path, "w") as f:
        f.write(readme)
        f.write(f"\n\n![]({trophy})\n")

    print(f"README.md generated -> {output_path}")
    print()
    print(readme)


if __name__ == "__main__":
    main()
