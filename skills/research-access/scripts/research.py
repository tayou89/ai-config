#!/usr/bin/env python3
"""research.py — read a result list or one source in a single browser round trip.

Usage:
    research.py google <query> [--limit N]
    research.py youtube <query> [--limit N]
    research.py transcript <video-url> [--max-chars N]
    research.py naver <query> [--limit N]
    research.py reddit <thread-url> [--limit N] [--max-chars N]
    research.py play <package-name> [--limit N]
    research.py appstore <app-id> [--limit N]
    research.py page <url> [--max-chars N]

Each command runs one script in its own ego-browser task space and prints
compact JSON. Lists carry only what is needed to pick sources (title, source,
popularity, date, link); the other commands return the text of one source,
cut at --max-chars.
The review and comment commands leave out reviewer and commenter names.

Exit codes:
    0  printed the result
    1  ego-browser failed or returned no result
    2  the site asks for a human check; stop and do not solve it
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path


EXTRACTOR_DIR = Path(__file__).resolve().parent / "extractors"
TASK_SPACE_PREFIX = "research-access"
SESSION_ID_VARIABLE = "CLAUDE_CODE_SESSION_ID"
SHELL_SESSION_TAG = "shell"
SESSION_TAG_LENGTH = 8
CALL_TAG_BYTES = 3
NAVIGATION_TIMEOUT_MS = 30000
NOTICE_PREFIX = "[ego-browser:"
DEFAULT_LIMIT = 5
DEFAULT_MAX_CHARS = 6000
EXIT_TOOL_FAILURE = 1
EXIT_HUMAN_CHECK = 2

# finish() runs even on failure: no later call resumes a per-call space, so leaving it open only holds a page.
SCRIPT_TEMPLATE = """const task = await taskSpace({space});
try {{
  const page = task.page("p1");
  const extract = {extractor};
  await page.goto({url}, {{ timeout: {timeout} }});
  await page.waitForTimeout({wait});
  const result = await page.evaluate(extract, {options});
  console.log(JSON.stringify(result));
}} finally {{
  await task.finish({{ keep: [] }});
}}
"""


@dataclass(frozen=True)
class Command:
    url_template: str
    extractor: str
    wait_ms: int
    is_search: bool


COMMANDS = {
    "google": Command("https://www.google.com/search?hl=ko&q={}", "google-results.js", 1500, True),
    "youtube": Command("https://www.youtube.com/results?search_query={}", "youtube-search.js", 2500, True),
    "transcript": Command("{}", "youtube-transcript.js", 3000, False),
    "naver": Command("https://m.search.naver.com/search.naver?where=m_blog&query={}", "naver-results.js", 1500, True),
    "reddit": Command("{}", "reddit-thread.js", 1500, False),
    "play": Command("https://play.google.com/store/apps/details?hl=ko&id={}", "play-reviews.js", 2000, True),
    "appstore": Command("https://apps.apple.com/kr/app/id{}?see-all=reviews", "appstore-reviews.js", 2500, True),
    "page": Command("{}", "page-text.js", 1500, False),
}


def make_url(command_name: str, target: str) -> str:
    command = COMMANDS[command_name]
    if command.is_search:
        return command.url_template.format(urllib.parse.quote(target))
    elif command_name == "reddit":
        return target.replace("://www.reddit.com/", "://old.reddit.com/")
    else:
        return command.url_template.format(target)


def make_space_name() -> str:
    # One space per call: calls sharing a name read each other's pages,
    # and one call's finish() closes the space under another.
    session_tag = os.environ.get(SESSION_ID_VARIABLE, "")[:SESSION_TAG_LENGTH] or SHELL_SESSION_TAG

    return f"{TASK_SPACE_PREFIX}-{session_tag}-{secrets.token_hex(CALL_TAG_BYTES)}"


def make_script(command_name: str, target: str, options: dict[str, int]) -> str:
    command = COMMANDS[command_name]
    extractor = (EXTRACTOR_DIR / command.extractor).read_text(encoding="utf-8").strip()

    return SCRIPT_TEMPLATE.format(
        space=json.dumps(make_space_name()),
        extractor=extractor,
        url=json.dumps(make_url(command_name, target)),
        timeout=NAVIGATION_TIMEOUT_MS,
        wait=command.wait_ms,
        options=json.dumps(options),
    )


def parse_result(lines: list[str]) -> object | None:
    content = [line for line in lines if line.strip() and not line.startswith(NOTICE_PREFIX)]

    if content:
        return json.loads(content[-1])
    else:
        return None


def run(command_name: str, target: str, options: dict[str, int]) -> int:
    completed = subprocess.run(
        ["ego-browser", "nodejs"],
        input=make_script(command_name, target, options),
        capture_output=True,
        text=True,
    )
    lines = (completed.stdout + "\n" + completed.stderr).splitlines()
    notices = [line for line in lines if line.startswith(NOTICE_PREFIX)]

    for notice in notices:
        print(notice, file=sys.stderr)
    try:
        result = parse_result(lines)
    except json.JSONDecodeError:
        result = None
    if completed.returncode != 0 or result is None:
        print("\n".join(lines).strip()[-800:], file=sys.stderr)
        return EXIT_TOOL_FAILURE
    elif isinstance(result, dict) and result.get("humanCheck"):
        print("The site asks for a human check. Stop here and do not solve it.", file=sys.stderr)
        return EXIT_HUMAN_CHECK
    else:
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Read a result list or one source through ego-browser.")
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("target", help="search words, a URL, or an app id")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    arguments = parser.parse_args()
    options = {"limit": arguments.limit, "maxChars": arguments.max_chars}

    return run(arguments.command, arguments.target, options)


if __name__ == "__main__":
    sys.exit(main())
