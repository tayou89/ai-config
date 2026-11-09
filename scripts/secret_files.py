"""Decide whether a file holds credentials that must never be committed.

Shared by the shell guard hook (staging) and claude-commit.py (the
final staged set). Names that always hold secrets are matched by name;
names that only sometimes do (.pem, .key, .json, registry configs) are
matched only when their content carries a private key or a token.
Example and template copies (.env.example, secrets.sample.json) pass.
"""

from __future__ import annotations

import posixpath
import re
from typing import Callable, Optional


ContentReader = Callable[[], Optional[bytes]]

MAX_CONTENT_BYTES = 64 * 1024
ENV_FILE_NAME = ".env"
ENV_FILE_PREFIX = ".env."
ENV_FILE_SUFFIX = ".env"
DIRENV_FILE_NAME = ".envrc"
EXEMPT_NAME_PARTS = frozenset({
    "example",
    "examples",
    "sample",
    "samples",
    "template",
    "templates",
    "tmpl",
    "tpl",
    "dist",
    "default",
    "defaults",
    "schema",
})
PRIVATE_KEY_NAMES = frozenset({
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_ecdsa_sk",
    "id_ed25519_sk",
})
KEY_STORE_SUFFIXES = (".p12", ".pfx", ".jks", ".keystore", ".ppk", ".p8", ".kdbx")
CREDENTIAL_NAMES = frozenset({
    ".git-credentials",
    ".netrc",
    "_netrc",
    ".pgpass",
    ".htpasswd",
    "credentials.json",
})
CREDENTIAL_NAME_PATTERNS = (
    re.compile(r"^client_secret.*\.json$"),
    re.compile(r"^service[-_]account.*\.json$"),
    re.compile(r"\.tfstate(?:\.backup)?$"),
)
CREDENTIAL_PATH_SUFFIXES = ("/.aws/credentials", "/.docker/config.json", "/.kube/config")
PRIVATE_KEY_CONTENT_SUFFIXES = (".pem", ".key", ".json")
PRIVATE_KEY_PATTERN = re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
TOKEN_CONFIG_NAMES = frozenset({".npmrc", ".yarnrc.yml", ".pypirc"})
TOKEN_CONTENT_PATTERN = re.compile(
    rb"(?im)^[ \t]*[^#;\n]*(?:_authtoken|_auth|_password|npmauthtoken|password)[ \t]*[=:]"
)
REASON_ENV_FILE = "environment file"
REASON_PRIVATE_KEY = "private key"
REASON_KEY_STORE = "key store"
REASON_CREDENTIAL_FILE = "credential file"
REASON_PRIVATE_KEY_CONTENT = "contains a private key"
REASON_TOKEN_CONTENT = "contains a registry token or password"
SECRET_TOKEN_PATTERNS = (
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("API secret key", re.compile(r"\bsk-(?:ant-|proj-)?(?=[A-Za-z0-9_-]*[0-9])(?=[A-Za-z0-9_-]*[A-Z])[A-Za-z0-9_-]{32,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),
    ("Stripe live key", re.compile(r"\b[rs]k_live_[0-9A-Za-z]{24,}")),
    ("npm token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
    ("Figma token", re.compile(r"\bfigd_[A-Za-z0-9_-]{40,}")),
)
SHOWN_TOKEN_PREFIX_LENGTH = 6
STAGED_DIFF_ARGUMENTS = (
    "-c",
    "core.quotepath=false",
    "diff",
    "--cached",
    "--no-color",
    "--no-ext-diff",
    "--unified=0",
    "--diff-filter=d",
)
DIFF_FILE_PREFIX = "diff --git "
DIFF_NEW_PATH_PREFIX = "+++ "
DIFF_HUNK_PREFIX = "@@"
DIFF_ADDED_PREFIX = "+"
DIFF_NEW_SIDE_PREFIX = "b/"
DIFF_PATH_QUOTE = '"'
DIFF_PATH_END = "\t"


def get_credential_reason(path: str, read_content: ContentReader) -> str | None:
    normalized = "/" + path.replace("\\", "/").lstrip("/")
    name = posixpath.basename(normalized).lower()
    name_parts = frozenset(name.split("."))

    if name_parts & EXEMPT_NAME_PARTS:
        return None
    elif _is_env_file(name):
        return REASON_ENV_FILE
    elif name in PRIVATE_KEY_NAMES:
        return REASON_PRIVATE_KEY
    elif name.endswith(KEY_STORE_SUFFIXES):
        return REASON_KEY_STORE
    elif _is_credential_name(name) or normalized.lower().endswith(CREDENTIAL_PATH_SUFFIXES):
        return REASON_CREDENTIAL_FILE
    elif name.endswith(PRIVATE_KEY_CONTENT_SUFFIXES):
        return _find_content_reason(read_content, PRIVATE_KEY_PATTERN, REASON_PRIVATE_KEY_CONTENT)
    elif name in TOKEN_CONFIG_NAMES:
        return _find_content_reason(read_content, TOKEN_CONTENT_PATTERN, REASON_TOKEN_CONTENT)
    else:
        return None


def find_added_secret_tokens(diff_text: str) -> list[str]:
    """Describe each live-looking token on an added line of a unified diff, showing only its prefix.

    Header lines come only before a file's first hunk, so an added line that
    itself starts with "++ " is still read as content.
    """
    findings: list[str] = []
    path = ""
    in_hunk = False

    for line in diff_text.splitlines():
        if line.startswith(DIFF_FILE_PREFIX):
            in_hunk = False
        elif not in_hunk and line.startswith(DIFF_NEW_PATH_PREFIX):
            path = read_diff_path(line[len(DIFF_NEW_PATH_PREFIX) :])
        elif line.startswith(DIFF_HUNK_PREFIX):
            in_hunk = True
        elif in_hunk and line.startswith(DIFF_ADDED_PREFIX):
            findings.extend(describe_token(path, kind, token) for kind, token in find_secret_tokens(line))
    return findings


def read_diff_path(text: str) -> str:
    unquoted = text.strip(DIFF_PATH_QUOTE) if text.startswith(DIFF_PATH_QUOTE) else text.removesuffix(DIFF_PATH_END)

    return unquoted.removeprefix(DIFF_NEW_SIDE_PREFIX)


def describe_token(path: str, kind: str, token: str) -> str:
    return f"{path}: {kind} ({token[:SHOWN_TOKEN_PREFIX_LENGTH]}…)"


def find_secret_tokens(text: str) -> list[tuple[str, str]]:
    return [(kind, match.group(0)) for kind, pattern in SECRET_TOKEN_PATTERNS for match in pattern.finditer(text)]


def read_file_head(path: str) -> bytes | None:
    try:
        with open(path, "rb") as handle:
            return handle.read(MAX_CONTENT_BYTES)
    except OSError:
        return None


def _find_content_reason(read_content: ContentReader, pattern: re.Pattern[bytes], reason: str) -> str | None:
    content = read_content()

    if content and pattern.search(content[:MAX_CONTENT_BYTES]):
        return reason
    else:
        return None


def _is_env_file(name: str) -> bool:
    is_dotenv = name == ENV_FILE_NAME or name.startswith(ENV_FILE_PREFIX)

    return is_dotenv or name.endswith(ENV_FILE_SUFFIX) or name == DIRENV_FILE_NAME


def _is_credential_name(name: str) -> bool:
    return name in CREDENTIAL_NAMES or any(pattern.search(name) for pattern in CREDENTIAL_NAME_PATTERNS)
