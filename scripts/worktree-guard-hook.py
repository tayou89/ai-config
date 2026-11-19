#!/usr/bin/env python3
"""Hook that keeps each folder of a worktree repository to its one writer.

A repository whose .agent-workflow.json names a worktree mode gives every
writing task its own worktree and branch, because sessions that wrote in
one shared folder mixed each other's changes (user decision, 2026-11-13).
Rules alone left sessions writing in the original folder, so before a tool
call (PreToolUse) this hook refuses there:

  - Edit, Write, and NotebookEdit of a file Git does not ignore
  - shell redirections, `tee`, in-place `sed` or `perl`, and file commands
    (`rm`, `mv`, `touch`, `mkdir`, the destination of `cp` or `ln`) on such a file
  - Git commands that change the working tree, the index, or HEAD there,
    and the commit wrapper run there

Reads, `git fetch`, and `git worktree add` pass, so resident sessions can
read there and start worktrees from it. Files Git ignores, such as
claude-plans/, stay writable, since plans must outlive a task's worktree.
In any folder of such a repository it also refuses `git worktree remove`
and `git worktree move`, which skip the preservation checks of
worktree-cleanup.py.

Each linked worktree, in any repository, has one owner session
(folder_owner.py), so the commit wrapper never has to guess whose changes
are whose there (user decision, 2026-11-18). This hook records the owner
after `git worktree add` or EnterWorktree (PostToolUse) and when a session
starts in a worktree the desktop app made for it (SessionStart). Before a
call, a session that does not own a worktree may only read there: a call
that writes there, or whose program is not a known reader and runs there or
names a path there, is refused while the owner is open or its state is
unknown, and so is a command this hook cannot follow that names such a
folder. Codex counts as a reader only with a read-only sandbox. A worktree
with no owner, or whose owner the app archived, goes to the next session
that writes there; the user is asked first when it holds uncommitted
changes, and the claim is recorded once the call ran. Owner records are
written only by folder_owner.py, never by a tool call.

What a script file or a program does inside itself is not seen, as in the
skill gate. Any failure lets the call through, since a wrong refusal stops
every session's work; a failure in a task worktree is recorded there as a
gap, and the commit wrapper asks the user once before committing over it.
"""

from __future__ import annotations

import functools
import glob
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from commit_arguments import CommitArguments, CommitArgumentsError, parse_commit_arguments
from folder_owner import (
    CLAIM_COMMAND,
    COMMONDIR_FILE,
    FROM_OPTION,
    HAND_OVER_COMMAND,
    SHOW_COMMAND,
    TAKE_COMMAND,
    UNKNOWN_NAME,
    AccessKind,
    ClaimMethod,
    FolderAccess,
    GuardGap,
    LinkedWorktree,
    OwnerState,
    SessionIdentity,
    add_gap,
    claim_folder,
    find_linked_worktree,
    is_app_worktree,
    is_protected_file,
    judge_access,
    offer_gaps,
    parse_owner_command,
    read_current_identity,
    read_gaps,
    read_identity_title,
    read_owner,
    read_owner_state,
    refresh_owner_process,
    take_folder,
)
from session_lineage import CLI_SESSION_VARIABLE, HOST_SESSION_VARIABLE
from shell_command import (
    IN_PLACE_EDITORS,
    TEE_PROGRAM,
    UNKNOWN_MARK,
    VARIABLE_MARK,
    CommandCall,
    ExpansionLimitError,
    expand_shell_command,
    find_script_index,
    join_directory,
    parse_git_command,
)
from workflow_policy import (
    GIT_DIRECTORY_NAME,
    find_repository_root,
    is_original_checkout,
    read_checkout_policy,
    read_original_checkout,
)

_CONFIG_DIRECTORY_VARIABLE = "CLAUDE_CONFIG_DIR"
_DEFAULT_CONFIG_DIRECTORY = "~/.claude"
_FILE_PATH_KEYS = {"Edit": "file_path", "Write": "file_path", "NotebookEdit": "notebook_path"}
_SHELL_TOOLS = frozenset({"Bash", "Monitor", "mcp__terminal__run_in_terminal"})
_TERMINAL_TOOL = "mcp__terminal__run_in_terminal"
_ENTER_WORKTREE_TOOL = "EnterWorktree"
_COMMAND_KEY = "command"
_EVENT_KEY = "hook_event_name"
_SESSION_KEY = "session_id"
_TOOL_NAME_KEY = "tool_name"
_TOOL_INPUT_KEY = "tool_input"
_TOOL_RESPONSE_KEY = "tool_response"
_CWD_KEY = "cwd"
_TAB_KEY = "tab_id"
_TOOL_USE_KEY = "tool_use_id"
_HOOK_OUTPUT_KEY = "hookSpecificOutput"
_DECISION_KEY = "permissionDecision"
_SESSION_ARGUMENT = "session"
_SCRIPTS_DIRECTORY = Path(__file__).resolve().parent
_ENCODING = "utf-8"
_PENDING_ADDS_DIRECTORY = Path("folder-owner") / "pending-adds"
_PENDING_PATTERN = "*.json"
_PENDING_LIFETIME_SECONDS = 24 * 60 * 60
_PRE_TOOL_USE = "PreToolUse"
_POST_TOOL_USE = "PostToolUse"
_SESSION_START = "SessionStart"
_WRITING_GIT_SUBCOMMANDS = frozenset(
    {
        "add",
        "am",
        "apply",
        "checkout",
        "checkout-index",
        "cherry-pick",
        "clean",
        "commit",
        "merge",
        "mv",
        "pull",
        "read-tree",
        "rebase",
        "reset",
        "restore",
        "revert",
        "rm",
        "stage",
        "stash",
        "switch",
        "update-index",
    },
)
_READING_GIT_SUBCOMMANDS = frozenset(
    {
        "blame",
        "cat-file",
        "describe",
        "diff",
        "fetch",
        "grep",
        "log",
        "ls-files",
        "ls-tree",
        "merge-base",
        "rev-list",
        "rev-parse",
        "shortlog",
        "show",
        "status",
    },
)
_PROGRAM_RUNNING_GIT_OPTIONS = ("--output", "--ext-diff", "--textconv", "--open-files-in-pager", "-O")
_SAFE_GIT_CONFIG_PREFIXES = ("core.quotepath", "color.")
_GIT_CONFIG_OPTION = "-c"
_GIT_LOCATION_VARIABLES = frozenset({"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"})
_GIT_CONFIG_VARIABLE_PREFIX = "GIT_CONFIG"
_SESSION_VARIABLES = frozenset({HOST_SESSION_VARIABLE, CLI_SESSION_VARIABLE})
_PROGRAM_RUNNING_VARIABLES = frozenset(
    {
        "BROWSER",
        "EDITOR",
        "GH_BROWSER",
        "GH_PAGER",
        "GIT_ASKPASS",
        "GIT_EDITOR",
        "GIT_EXTERNAL_DIFF",
        "GIT_PAGER",
        "GIT_SSH",
        "GIT_SSH_COMMAND",
        "LESSCLOSE",
        "LESSOPEN",
        "PAGER",
        "SSH_ASKPASS",
        "VISUAL",
    },
)
_SYSTEM_PROGRAM_FOLDERS = frozenset({"/bin", "/sbin", "/usr/bin", "/usr/sbin", "/usr/local/bin", "/opt/homebrew/bin"})
_WORKTREE_LIST_ACTION = "list"
_BRANCH_SUBCOMMAND = "branch"
_LISTING_BRANCH_OPTIONS = frozenset({"--show-current", "--list", "-l", "-a", "--all", "-r", "--remotes", "-v", "-vv"})
_WORKTREE_RECORD_PATTERN = "worktrees/*/gitdir"
_OWNER_RECORD_PATTERNS = ("claude-owner*", "*/claude-owner*", "*/*/claude-owner*")
_WILDCARD_CHARACTERS = "*?["
_SHELL_EXPANSION_START = "$"
_REMOVING_PROGRAMS = frozenset({"rm", "rmdir", "unlink", "mv"})
_RSYNC_PROGRAM = "rsync"
_RSYNC_REMOVE_OPTION = "--remove-source-files"
_RG_PROGRAM = "rg"
_RG_PROGRAM_OPTIONS = ("--pre",)
_WORKTREE_SUBCOMMAND = "worktree"
_WORKTREE_ADD_ACTION = "add"
_WORKTREE_ADD_OPTIONS_WITH_VALUE = frozenset({"-b", "-B", "--reason"})
_UNGUARDED_WORKTREE_ACTIONS = frozenset({"remove", "move"})
_COMMIT_WRAPPER_NAME = "claude-commit.py"
_FOLDER_OWNER_NAME = "folder_owner.py"
_SESSION_WRITES_NAME = "session_writes.py"
_ADOPT_COMMAND = "adopt"
_READING_OWNER_COMMANDS = frozenset({SHOW_COMMAND, HAND_OVER_COMMAND})
_REPORT_COMMAND = "report"
_EVERY_OPERAND_WRITERS = frozenset({"rm", "rmdir", "unlink", "mv", "touch", "mkdir", "truncate"})
_DESTINATION_WRITERS = frozenset({"cp", "ln", "install", "rsync"})
_READING_PROGRAMS = frozenset(
    {
        "[",
        "basename",
        "cat",
        "cd",
        "cmp",
        "column",
        "comm",
        "cut",
        "date",
        "df",
        "diff",
        "dirname",
        "du",
        "echo",
        "egrep",
        "exit",
        "export",
        "false",
        "fgrep",
        "file",
        "grep",
        "head",
        "jq",
        "ls",
        "md5",
        "nl",
        "od",
        "popd",
        "printf",
        "pushd",
        "pwd",
        "readlink",
        "realpath",
        "rg",
        "shasum",
        "sleep",
        "stat",
        "tail",
        "test",
        "tr",
        "true",
        "uname",
        "unset",
        "wc",
        "which",
    },
)
_FIND_PROGRAM = "find"
_FIND_WRITING_ACTIONS = frozenset({"-delete", "-fprint", "-fprint0", "-fprintf", "-fls"})
_SORT_PROGRAM = "sort"
_SORT_OUTPUT_OPTIONS = ("-o", "--output")
_SED_PROGRAMS = frozenset({"sed", "gsed"})
_SED_READING_OPTIONS = frozenset({"-n", "-E", "-r", "--quiet", "--silent"})
_SED_PRINT_SCRIPT = re.compile(r"(?:\d+|\$)(?:,(?:\d+|\$))?p")
_GH_PROGRAM = "gh"
_READING_GH_COMMANDS = {
    "pr": frozenset({"view", "list", "checks", "status", "diff"}),
    "run": frozenset({"view", "list", "watch"}),
    "repo": frozenset({"view"}),
}
_CODEX_PROGRAM = "codex"
_CODEX_EXEC_COMMANDS = frozenset({"exec", "e"})
_CODEX_SANDBOX_OPTIONS = frozenset({"-s", "--sandbox"})
_CODEX_READ_ONLY = "read-only"
_CODEX_WRITING_OPTIONS = frozenset(
    {
        "--full-auto",
        "--dangerously-bypass-approvals-and-sandbox",
        "--yolo",
        "-o",
        "--output-last-message",
    },
)
_CODEX_CONFIG_OPTIONS = frozenset({"-c", "--config"})
_CODEX_SANDBOX_CONFIG_KEYS = ("sandbox", "approval")
_ABSOLUTE_PATH_PATTERN = re.compile(r"""(?:^|(?<=[\s'"=(:]))((?:/|~/)[^\s'"<>|;&()`$]+)""")
_HOME_PREFIX = "~"
_OPTION_PREFIX = "-"
_VALUE_SEPARATOR = "="
_GIT_TIMEOUT_SECONDS = 5
_MAX_SHOWN_GAPS = 5
_DENY = "deny"
_ASK = "ask"
_READ_ONLY_REASON = (
    "{root} is the original checkout of a repository whose .agent-workflow.json gives every writing task "
    "its own worktree, so it stays read-only ({what}). Enter a worktree first (EnterWorktree, or a session "
    "started with use_worktree) and write there; files Git ignores, such as claude-plans/, stay writable here."
)
_WORKTREE_REMOVAL_REASON = (
    "Remove a task's worktree with ~/.claude/scripts/worktree-cleanup.py, which first checks that its work "
    "is committed, integrated, and not in use ({what})."
)
_OWNER_FILE_REASON = (
    "{path} records which session owns a task worktree, so only ~/.claude/scripts/folder_owner.py changes it ({what})."
)
_OTHERS_FOLDER_REASON = (
    "{root} is the task worktree of another session, {owner}, which {state}, so this session may only read there "
    "({what}). Write in a worktree of your own. If that session handed this task to you, ask it to run "
    "`python3 ~/.claude/scripts/folder_owner.py hand-over --to {me}`; if it has ended or the user wants you to take "
    "over, run `python3 ~/.claude/scripts/folder_owner.py take --from {owner_id} {root}`, which asks the user first."
)
_OWNER_OPEN_STATE = "is still open"
_OWNER_UNKNOWN_STATE = "may still be open (its state is unknown)"
_SESSION_IDENTITY_REASON = (
    "This call sets the session id the owner record and the commit wrapper read ({what}), so it could act as "
    "another session; run it without setting CLAUDE_CODE_HOST_SESSION_ID or CLAUDE_CODE_SESSION_ID."
)
_LOST_CLAIM_REASON = "{root} was just claimed by {owner}, so this session may only read there ({what})."
_LOST_AFTER_CALL_GAP = "{what} ran after the user approved taking the folder, but {owner} took it first"
_UNREADABLE_COMMAND_WHAT = "a command this hook cannot follow"
_CLAIM_QUESTION = (
    "이 작업 폴더에는 주인 세션 기록이 없고, 아직 커밋하지 않은 바뀜이 있습니다: {root}\n"
    "이 세션({me})이 이 폴더를 맡아 써도 되면 승인해 주세요. 승인하시면 이 세션이 주인이 되어 그 바뀜도 함께 커밋할 수 있고, "
    "거절하시면 이 세션은 이 폴더에 쓰지 않습니다.\n하려던 일: {what}"
)
_TAKE_LEFT_QUESTION = (
    "이 작업 폴더의 주인 세션({owner})은 닫혔지만, 커밋하지 않은 바뀜이 남아 있습니다: {root}\n"
    "이 세션({me})이 넘겨받아 이어 써도 되면 승인해 주세요. 거절하시면 이 세션은 이 폴더에 쓰지 않습니다.\n하려던 일: {what}"
)
_TAKE_OPEN_QUESTION = (
    "이 명령은 {state} 세션({owner})의 작업 폴더를 이 세션({me})으로 넘겨받습니다: {root}\n"
    "그 세션이 넘긴 것이 맞을 때만 승인해 주세요. 넘겨받으면 그 세션은 이 폴더에 더는 쓰지 못합니다.\n명령: {what}"
)
_OPEN_STATE_WORDS = "아직 닫히지 않은"
_UNKNOWN_STATE_WORDS = "닫혔는지 알 수 없는"
_GAPS_QUESTION = (
    "폴더 보호 장치가 이 작업 폴더에서 {count}번 판정하지 못하고 호출을 그대로 통과시켰습니다. "
    "그때 다른 세션이 이 폴더에 썼을 수도 있습니다: {root}\n{gaps}\n"
    "이 커밋에 그때의 바뀜이 섞여도 되면 승인해 주세요. 거절하시면 커밋하지 않습니다."
)
_GAP_LINE = "  - {session}: {what}"
_MORE_GAPS_LINE = "  … 그 밖에 {count}건"


_IdentityReader = Callable[[], "SessionIdentity | None"]


@dataclass(frozen=True)
class _FolderUse:
    """A task worktree a call writes, and what the call does there, for messages."""

    worktree: LinkedWorktree
    what: str


@dataclass(frozen=True)
class _OwnerCommand:
    """A folder_owner.py command as that script reads it; `command` is None for one it would refuse."""

    command: str | None
    sender: str | None
    folder: Path | None


@dataclass(frozen=True)
class _Verdict:
    deny: list[str]
    ask: list[str]
    claims: list[_FolderUse]


_NO_VERDICT = _Verdict([], [], [])


def main() -> None:
    payload: dict[str, Any] = {}

    try:
        payload = json.load(sys.stdin)
        decision = decide(payload)

        if decision:
            print(json.dumps(decision))
    except Exception as error:
        print(f"worktree-guard-hook: {type(error).__name__}: {error}", file=sys.stderr)
        _record_gap(payload, error)


def decide(payload: dict[str, Any]) -> dict[str, Any] | None:
    event = payload.get(_EVENT_KEY) or _PRE_TOOL_USE

    if event == _POST_TOOL_USE:
        _record_owners_after_call(payload)
        return None
    elif event == _SESSION_START:
        _claim_app_worktree(payload)
        return None
    else:
        return _decide_before_call(payload)


def _decide_before_call(payload: dict[str, Any]) -> dict[str, Any] | None:
    tool_name = _read_tool_name(payload)
    tool_input = _read_tool_input(payload)
    cwd = _read_call_folder(payload)
    file_path = tool_input.get(_FILE_PATH_KEYS.get(tool_name, ""))
    identity = _make_identity_reader(payload)

    if isinstance(file_path, str) and file_path:
        verdict = _judge_file_tool(_join(cwd, file_path), f"{tool_name} {os.path.basename(file_path)}", identity)
    elif tool_name in _SHELL_TOOLS:
        verdict = _judge_command(str(tool_input.get(_COMMAND_KEY) or ""), cwd, identity)
    else:
        verdict = _NO_VERDICT
    decision = _settle_verdict(verdict, identity)

    if tool_name in _SHELL_TOOLS and (decision is None or decision[_HOOK_OUTPUT_KEY][_DECISION_KEY] != _DENY):
        _note_folders_to_add(payload)
    return decision


def _settle_verdict(verdict: _Verdict, identity: _IdentityReader) -> dict[str, Any] | None:
    """Refuse, ask, or claim the folders the call takes and let it through. A folder may change between the judgment
    and the claim: another session's claim refuses, and new uncommitted changes ask first."""
    current = identity() if verdict.claims and not verdict.deny and not verdict.ask else None
    claims = [_claim_before_call(use, current) for use in verdict.claims] if current is not None else []
    claimed = _merge_verdicts(claims)

    if verdict.deny or claimed.deny:
        return _make_decision(_DENY, [*verdict.deny, *claimed.deny])
    elif verdict.ask or claimed.ask:
        return _make_decision(_ASK, [*verdict.ask, *claimed.ask])
    else:
        return None


def _judge_file_tool(path: Path, what: str, identity: _IdentityReader) -> _Verdict:
    original = _Verdict(_list_file_reasons(path, what), [], [])

    if is_protected_file(path):
        return _merge_verdicts([original, _Verdict([_OWNER_FILE_REASON.format(path=path, what=what)], [], [])])
    else:
        return _merge_verdicts([original, _judge_uses(_list_folder_uses([path], what), identity)])


def _judge_command(command: str, cwd: str | None, identity: _IdentityReader) -> _Verdict:
    """Judge each call; when a call that may write hides where it writes (a variable, an unknown folder), also refuse
    the whole command if it names a folder another open session owns, since not knowing must not read as safe."""
    try:
        calls = expand_shell_command(command, cwd, _get_config_directory())
    except ExpansionLimitError:
        return _judge_unreadable_command(command, cwd, identity)
    else:
        is_uncertain = any(_is_uncertain_call(call) for call in calls)
        fallback = _judge_unreadable_command(command, cwd, identity) if is_uncertain else _NO_VERDICT

        return _merge_verdicts([*(_judge_call(call, identity) for call in calls), fallback])


def _judge_unreadable_command(command: str, cwd: str | None, identity: _IdentityReader) -> _Verdict:
    """A command too deep to follow is refused only where it names a folder another open session owns."""
    named = [_join(cwd, path) for path in _ABSOLUTE_PATH_PATTERN.findall(command)]
    verdict = _judge_uses(_list_folder_uses([*_optional_path(cwd), *named], _UNREADABLE_COMMAND_WHAT), identity)

    return _Verdict(verdict.deny, [], [])


def _judge_call(call: CommandCall, identity: _IdentityReader) -> _Verdict:
    written_paths = [*_list_written_absolute_paths(call), *_list_removed_worktrees(call)]
    protected = [path for path in [*written_paths, *_list_removed_owner_records(call)] if is_protected_file(path)]
    owner_command = _read_owner_command(call)
    is_owner_command = owner_command is not None and owner_command.command is not None
    what = _describe_call(call)
    uses = _list_folder_uses(written_paths, what) if is_owner_command else _list_call_uses(call, written_paths)
    owner_verdict = _judge_owner_command(call, owner_command, identity) if owner_command is not None else _NO_VERDICT

    if protected:
        reasons = [_OWNER_FILE_REASON.format(path=path, what=_describe_call(call)) for path in protected]

        return _Verdict(reasons, [], [])
    elif _sets_session_identity(call):
        return _Verdict([_SESSION_IDENTITY_REASON.format(what=_describe_call(call))], [], [])
    else:
        return _merge_verdicts(
            [
                _Verdict(_list_original_reasons(call), [], []),
                _judge_uses(uses, identity),
                owner_verdict,
                _judge_gap_acceptance(call),
            ],
        )


def _list_call_uses(call: CommandCall, written_paths: list[Path]) -> list[_FolderUse]:
    """The task worktrees a call writes: the files it writes, and for a program not known to only read, its
    folder and every path it names."""
    named = [] if _is_reading_call(call) or _is_file_command(call) else _list_named_paths(call)

    return _list_folder_uses([*written_paths, *named], _describe_call(call))


def _list_named_paths(call: CommandCall) -> list[Path]:
    """The call's folder and every path its words or variables name, such as Git's GIT_WORK_TREE."""
    git_command = parse_git_command(call.argv, call.cwd)
    work_dir = git_command.work_dir if git_command else call.cwd
    words = [*call.argv[1:], *(value for _, value in call.assignments)]
    candidates = [candidate for word in words for candidate in _list_path_candidates(word)]
    named = [_join(call.cwd, candidate) for candidate in candidates if call.cwd or os.path.isabs(candidate)]

    return [*_optional_path(work_dir), *named]


def _list_path_candidates(word: str) -> list[str]:
    """The paths a word may name: itself or an option's value when it looks like a path, and absolute paths in it."""
    is_option_value = word.startswith(_OPTION_PREFIX) and _VALUE_SEPARATOR in word
    value = word.split(_VALUE_SEPARATOR, 1)[1] if is_option_value else word
    direct = [value] if os.sep in value or value.startswith(_HOME_PREFIX) else []

    return [*direct, *_ABSOLUTE_PATH_PATTERN.findall(word)]


def _list_folder_uses(paths: Iterable[Path], what: str) -> list[_FolderUse]:
    return [_FolderUse(worktree, what) for worktree in _find_worktrees(paths)]


def _judge_uses(uses: list[_FolderUse], identity: _IdentityReader) -> _Verdict:
    """Refuse writes to another open session's folder, ask before taking one with changes, and claim the rest."""
    current = identity() if uses else None

    if current is not None:
        accesses = [(use, judge_access(use.worktree, current)) for use in uses]

        for use, access in accesses:
            if access.kind == AccessKind.OWN and access.owner is not None:
                refresh_owner_process(use.worktree, access.owner, current)
        return _Verdict(
            [_make_others_reason(use, access, current) for use, access in accesses if access.kind == AccessKind.OTHERS],
            [_make_take_question(use, access, current) for use, access in accesses if access.kind.is_asking],
            [use for use, access in accesses if access.kind in (AccessKind.CLAIM, AccessKind.TAKE)],
        )
    else:
        return _NO_VERDICT


def _judge_owner_command(call: CommandCall, owner_command: _OwnerCommand, identity: _IdentityReader) -> _Verdict:
    """Ask before taking an open session's folder, or one left with changes; the command itself checks the rest."""
    worktree = find_linked_worktree(owner_command.folder) if owner_command.folder is not None else None
    command = owner_command.command
    is_taking = command is not None and command not in _READING_OWNER_COMMANDS
    current = identity() if worktree is not None and is_taking else None

    if worktree is not None and current is not None and command is not None:
        return _ask_before_taking(_FolderUse(worktree, _describe_call(call)), command, owner_command.sender, current)
    else:
        return _NO_VERDICT


def _ask_before_taking(use: _FolderUse, command: str, sender: str | None, identity: SessionIdentity) -> _Verdict:
    access = judge_access(use.worktree, identity)
    is_taking_from_open = command != CLAIM_COMMAND and access.kind == AccessKind.OTHERS and sender is not None

    if access.kind.is_asking or is_taking_from_open:
        return _Verdict([], [_make_take_question(use, access, identity)], [])
    else:
        return _NO_VERDICT


def _judge_gap_acceptance(call: CommandCall) -> _Verdict:
    """The commit wrapper's --accept-gaps commits over calls the guard let through unjudged, so the user decides, and
    the gaps the dialog shows are kept, so the wrapper takes in only those."""
    arguments = _read_commit_arguments(call)
    accepts = arguments is not None and arguments.accept_gaps
    worktree = find_linked_worktree(Path(call.cwd).resolve()) if accepts and call.cwd else None
    gaps = read_gaps(worktree) if worktree is not None else []

    if worktree is not None and gaps:
        offer_gaps(worktree, gaps)
        return _Verdict([], [_make_gaps_question(worktree, gaps)], [])
    else:
        return _NO_VERDICT


def _claim_before_call(use: _FolderUse, identity: SessionIdentity) -> _Verdict:
    access = judge_access(use.worktree, identity)
    owner = take_folder(access, identity) if access.kind in (AccessKind.CLAIM, AccessKind.TAKE) else access.owner

    if access.kind.is_asking:
        return _Verdict([], [_make_take_question(use, access, identity)], [])
    elif owner is not None and not owner.owner.is_same(identity):
        reason = _LOST_CLAIM_REASON.format(root=use.worktree.root, owner=owner.describe(), what=use.what)

        return _Verdict([reason], [], [])
    else:
        return _NO_VERDICT


def _record_owners_after_call(payload: dict[str, Any]) -> None:
    """After a call ran: own the worktrees it added or entered, and those it took after the user approved."""
    identity = read_current_identity(payload.get(_SESSION_KEY) or None)

    if identity is not None:
        _record_call_owners(payload, identity)


def _record_call_owners(payload: dict[str, Any], identity: SessionIdentity) -> None:
    tool_name = _read_tool_name(payload)
    tool_input = _read_tool_input(payload)
    cwd = _read_call_folder(payload)
    file_path = tool_input.get(_FILE_PATH_KEYS.get(tool_name, ""))

    if tool_name == _ENTER_WORKTREE_TOOL:
        _claim_created([*_list_response_paths(payload.get(_TOOL_RESPONSE_KEY)), *_optional_path(cwd)], identity)
    elif isinstance(file_path, str) and file_path:
        _take_after_call(_list_folder_uses([_join(cwd, file_path)], tool_name), identity)
    elif tool_name in _SHELL_TOOLS:
        _record_command_owners(str(tool_input.get(_COMMAND_KEY) or ""), cwd, identity, _read_added_folders(payload))


def _record_command_owners(command: str, cwd: str | None, identity: SessionIdentity, added: list[Path]) -> None:
    """Own the worktrees this call made, which the step before it found missing, and take those it wrote after the
    user approved."""
    calls = _try_expand(command, cwd)
    writing = [call for call in calls if _read_owner_command(call) is None]
    uses = [use for call in writing for use in _list_call_uses(call, _list_written_absolute_paths(call))]

    _claim_created(added, identity)
    _take_after_call(uses, identity)


def _claim_created(paths: Iterable[Path], identity: SessionIdentity) -> None:
    for worktree in _find_worktrees(paths):
        if read_owner(worktree) is None:
            claim_folder(worktree, identity, read_identity_title(identity), ClaimMethod.CREATED)


def _take_after_call(uses: list[_FolderUse], identity: SessionIdentity) -> None:
    """Take the folders a call wrote after the user approved taking them. When another session took one first, the
    call wrote in that session's folder, so the folder records a gap, which its owner's commit asks about."""
    for use in uses:
        access = judge_access(use.worktree, identity)
        owner = take_folder(access, identity) if access.kind.is_taking else access.owner

        if owner is not None and not owner.owner.is_same(identity):
            add_gap(use.worktree, identity, _LOST_AFTER_CALL_GAP.format(owner=owner.describe(), what=use.what))


def _claim_app_worktree(payload: dict[str, Any]) -> None:
    cwd = payload.get(_CWD_KEY)
    worktree = find_linked_worktree(Path(cwd).resolve()) if isinstance(cwd, str) and cwd else None
    identity = read_current_identity(payload.get(_SESSION_KEY) or None) if worktree is not None else None
    is_unclaimed_app_worktree = (
        worktree is not None
        and identity is not None
        and read_owner(worktree) is None
        and is_app_worktree(worktree, identity)
    )

    if is_unclaimed_app_worktree and worktree is not None and identity is not None:
        claim_folder(worktree, identity, read_identity_title(identity), ClaimMethod.APP_WORKTREE)


def _record_gap(payload: dict[str, Any], error: Exception) -> None:
    """Note a failure in every task worktree the call names, so the commit there asks once; this never raises."""
    try:
        tool_name = _read_tool_name(payload)
        tool_input = _read_tool_input(payload)
        cwd = _read_call_folder(payload)
        texts = [str(value) for value in tool_input.values() if isinstance(value, str)]
        paths = [_join(cwd, path) for text in texts for path in _ABSOLUTE_PATH_PATTERN.findall(text)]
        file_path = tool_input.get(_FILE_PATH_KEYS.get(tool_name, ""))
        files = [_join(cwd, file_path)] if isinstance(file_path, str) and file_path else []
        identity = read_current_identity(payload.get(_SESSION_KEY) or None)

        for worktree in _find_worktrees([*_optional_path(cwd), *paths, *files]):
            add_gap(worktree, identity, f"{tool_name}: {type(error).__name__}: {error}")
    except Exception as gap_error:
        print(f"worktree-guard-hook: the gap could not be recorded: {gap_error}", file=sys.stderr)


def _list_original_reasons(call: CommandCall) -> list[str]:
    """Why a call may not run in the original checkout of a worktree repository, which stays read-only."""
    written = [path for path in _list_written_paths(call) if call.cwd]
    file_reasons = [
        reason for path in written for reason in _list_file_reasons(_join(call.cwd, path), f"writes {path}")
    ]
    git_command = parse_git_command(call.argv, call.cwd)
    work_dir = git_command.work_dir if git_command else call.cwd

    if git_command and work_dir and git_command.subcommand in _WRITING_GIT_SUBCOMMANDS:
        return [*file_reasons, *_list_folder_reasons(work_dir, f"git {git_command.subcommand}")]
    elif git_command and work_dir and _is_unguarded_worktree_change(git_command.subcommand, git_command.arguments):
        return [*file_reasons, *_list_worktree_removal_reasons(work_dir, f"git worktree {git_command.arguments[0]}")]
    elif call.cwd and find_script_index(call.argv, _COMMIT_WRAPPER_NAME) >= 0:
        return [*file_reasons, *_list_folder_reasons(call.cwd, "the commit wrapper")]
    else:
        return file_reasons


def _list_file_reasons(path: Path, what: str) -> list[str]:
    root = _find_guarded_original(path)

    if root and not _is_inside(path, root / GIT_DIRECTORY_NAME) and not _is_ignored(root, path):
        return [_READ_ONLY_REASON.format(root=root, what=what)]
    else:
        return []


def _list_folder_reasons(folder: str, what: str) -> list[str]:
    root = _find_guarded_original(Path(folder))

    if root:
        return [_READ_ONLY_REASON.format(root=root, what=what)]
    else:
        return []


def _list_written_paths(call: CommandCall) -> list[str]:
    return [path for path in [*call.list_written_files(), *_list_file_command_targets(call)] if path]


def _list_file_command_targets(call: CommandCall) -> list[str]:
    """What file commands change: every operand of `rm` or `touch`, the destination of `cp` or `ln`, and every
    operand of an rsync that removes its sources."""
    program = _read_call_program(call)
    operands = [argument for argument in call.argv[1:] if not argument.startswith(_OPTION_PREFIX)]

    if program in _EVERY_OPERAND_WRITERS or _is_removing_rsync(call):
        return operands
    elif program in _DESTINATION_WRITERS and operands:
        return [operands[-1]]
    else:
        return []


def _list_worktree_removal_reasons(folder: str, what: str) -> list[str]:
    root = find_repository_root(Path(folder))
    original = read_original_checkout(root) if root else None

    if original and _is_worktree_workflow(original):
        return [_WORKTREE_REMOVAL_REASON.format(what=what)]
    else:
        return []


def _read_owner_command(call: CommandCall) -> _OwnerCommand | None:
    """A call of the installed folder_owner.py, read by its own parser with the folder it acts on, or of
    `session_writes.py adopt`, which takes the folder it runs in; None for any other call."""
    owner_index = _find_installed_script(call, _FOLDER_OWNER_NAME)
    writes_index = _find_installed_script(call, _SESSION_WRITES_NAME)
    parsed = parse_owner_command(call.argv[owner_index + 1 :]) if owner_index >= 0 else None
    adopt = call.argv[writes_index + 1 : writes_index + 4] if writes_index >= 0 else []
    folder = Path(call.cwd).resolve() if call.cwd else None

    if parsed is not None:
        named = _join(call.cwd, parsed.folder) if parsed.folder else folder

        return _OwnerCommand(parsed.command, getattr(parsed, _SESSION_ARGUMENT, None), named)
    elif owner_index >= 0:
        return _OwnerCommand(None, None, None)
    elif adopt[:2] == [_ADOPT_COMMAND, FROM_OPTION] and len(adopt) == 3:
        return _OwnerCommand(TAKE_COMMAND, adopt[2], folder)
    else:
        return None


def _read_added_worktree(call: CommandCall) -> Path | None:
    """The folder a `git worktree add` call makes: its first operand after the options."""
    git_command = parse_git_command(call.argv, call.cwd)
    arguments = git_command.arguments if git_command and git_command.subcommand == _WORKTREE_SUBCOMMAND else []
    positionals = _list_worktree_add_operands(arguments[1:]) if arguments[:1] == [_WORKTREE_ADD_ACTION] else []

    if git_command is not None and git_command.work_dir and positionals:
        return _join(git_command.work_dir, positionals[0])
    else:
        return None


def _note_folders_to_add(payload: dict[str, Any]) -> None:
    """Before a call runs, keep the folders its `git worktree add` calls name that do not exist yet, so the step
    after it owns only a worktree this call made, never one a skipped command named. Recording only helps, so a
    failure here leaves the new worktree to its first write."""
    try:
        tool_use_id = payload.get(_TOOL_USE_KEY)
        command = str(_read_tool_input(payload).get(_COMMAND_KEY) or "")
        calls = _try_expand(command, _read_call_folder(payload)) if isinstance(tool_use_id, str) and tool_use_id else []
        missing = [path for call in calls if (path := _read_added_worktree(call)) is not None and not path.exists()]

        if missing and isinstance(tool_use_id, str):
            _write_pending_adds(tool_use_id, missing)
    except Exception as error:
        print(f"worktree-guard-hook: the new worktree's owner will be its first writer ({error})", file=sys.stderr)


def _read_added_folders(payload: dict[str, Any]) -> list[Path]:
    """The folders noted before this call that exist now; the note is removed either way."""
    tool_use_id = payload.get(_TOOL_USE_KEY)
    pending = _get_pending_adds_directory() / f"{tool_use_id}.json" if isinstance(tool_use_id, str) else None

    try:
        noted = json.loads(pending.read_text(encoding=_ENCODING)) if pending is not None and pending.exists() else []
    except (OSError, ValueError):
        noted = []
    if pending is not None:
        pending.unlink(missing_ok=True)
    return [Path(path) for path in noted if isinstance(path, str) and Path(path).exists()]


def _list_worktree_add_operands(arguments: list[str]) -> list[str]:
    operands: list[str] = []
    skip_next = False

    for argument in arguments:
        if skip_next:
            skip_next = False
        elif argument in _WORKTREE_ADD_OPTIONS_WITH_VALUE:
            skip_next = True
        elif not argument.startswith(_OPTION_PREFIX):
            operands.append(argument)
    return operands


def _list_response_paths(response: Any) -> list[Path]:
    """Every absolute path a tool's response names, wherever in it, since EnterWorktree's shape is not documented."""
    if isinstance(response, str):
        return [Path(path) for path in _ABSOLUTE_PATH_PATTERN.findall(response)]
    elif isinstance(response, dict):
        return [path for value in response.values() for path in _list_response_paths(value)]
    elif isinstance(response, list):
        return [path for value in response for path in _list_response_paths(value)]
    else:
        return []


def _make_others_reason(use: _FolderUse, access: FolderAccess, identity: SessionIdentity) -> str:
    owner = access.owner
    is_open = owner is not None and read_owner_state(owner) == OwnerState.ACTIVE

    return _OTHERS_FOLDER_REASON.format(
        root=use.worktree.root,
        owner=owner.describe() if owner is not None else UNKNOWN_NAME,
        state=_OWNER_OPEN_STATE if is_open else _OWNER_UNKNOWN_STATE,
        what=use.what,
        me=identity.name,
        owner_id=owner.owner.name if owner is not None else UNKNOWN_NAME,
    )


def _make_take_question(use: _FolderUse, access: FolderAccess, identity: SessionIdentity) -> str:
    owner = access.owner

    if owner is None:
        return _CLAIM_QUESTION.format(root=use.worktree.root, me=identity.name, what=use.what)
    elif access.kind == AccessKind.TAKE_ASKING:
        return _TAKE_LEFT_QUESTION.format(
            owner=owner.describe(),
            root=use.worktree.root,
            me=identity.name,
            what=use.what,
        )
    else:
        state = _OPEN_STATE_WORDS if read_owner_state(owner) == OwnerState.ACTIVE else _UNKNOWN_STATE_WORDS

        return _TAKE_OPEN_QUESTION.format(
            state=state, owner=owner.describe(), me=identity.name, root=use.worktree.root, what=use.what
        )


def _make_gaps_question(worktree: LinkedWorktree, gaps: list[GuardGap]) -> str:
    shown = [_GAP_LINE.format(session=gap.session or UNKNOWN_NAME, what=gap.what) for gap in gaps[:_MAX_SHOWN_GAPS]]
    hidden = len(gaps) - _MAX_SHOWN_GAPS
    more = [_MORE_GAPS_LINE.format(count=hidden)] if hidden > 0 else []

    return _GAPS_QUESTION.format(count=len(gaps), root=worktree.root, gaps="\n".join([*shown, *more]))


def _merge_verdicts(verdicts: list[_Verdict]) -> _Verdict:
    return _Verdict(
        [reason for verdict in verdicts for reason in verdict.deny],
        [question for verdict in verdicts for question in verdict.ask],
        [use for verdict in verdicts for use in verdict.claims],
    )


def _find_guarded_original(path: Path) -> Path | None:
    """The repository root when the path is in an original checkout whose workflow uses worktrees, else None."""
    root = find_repository_root(path)

    if root and is_original_checkout(root) and _is_worktree_workflow(root):
        return root
    else:
        return None


def _is_worktree_workflow(root: Path) -> bool:
    """Direct and team repositories record a workflow without task worktrees, so their original checkout stays writable."""
    policy = read_checkout_policy(root)

    return policy is not None and policy.mode.is_worktree_mode


def _make_decision(decision: str, reasons: list[str]) -> dict[str, Any]:
    return {
        _HOOK_OUTPUT_KEY: {
            "hookEventName": _PRE_TOOL_USE,
            _DECISION_KEY: decision,
            "permissionDecisionReason": "\n".join(dict.fromkeys(reasons)),
        },
    }


def _read_call_folder(payload: dict[str, Any]) -> str | None:
    """The call's folder: a new terminal tab's `cwd` from the session's folder; an existing tab's is unknown, since
    the tab may have moved."""
    tool_input = _read_tool_input(payload)
    cwd = payload.get(_CWD_KEY)
    is_terminal = _read_tool_name(payload) == _TERMINAL_TOOL
    tab_folder = tool_input.get(_CWD_KEY)

    if not isinstance(cwd, str) or (is_terminal and tool_input.get(_TAB_KEY)):
        return None
    elif is_terminal and isinstance(tab_folder, str) and tab_folder:
        return join_directory(cwd, tab_folder)
    else:
        return cwd


def _read_tool_name(payload: dict[str, Any]) -> str:
    return str(payload.get(_TOOL_NAME_KEY) or "")


def _read_tool_input(payload: dict[str, Any]) -> dict[str, Any]:
    tool_input = payload.get(_TOOL_INPUT_KEY)

    return tool_input if isinstance(tool_input, dict) else {}


def _read_commit_arguments(call: CommandCall) -> CommitArguments | None:
    """The commit wrapper's arguments in a call, read by its own parser, or None for any other call."""
    script_index = find_script_index(call.argv, _COMMIT_WRAPPER_NAME)

    try:
        return parse_commit_arguments(call.argv[script_index + 1 :]) if script_index >= 0 else None
    except CommitArgumentsError:
        return None


def _list_written_absolute_paths(call: CommandCall) -> list[Path]:
    """The files a call writes, resolved, with wildcards expanded as the shell would; a relative one in a terminal
    tab, whose folder is unknown, is left out, and the command then counts as uncertain."""
    joined = [_join(call.cwd, path) for path in _list_written_paths(call) if call.cwd or os.path.isabs(path)]

    return [match for path in joined for match in _expand_wildcards(path)]


def _expand_wildcards(path: Path) -> list[Path]:
    if any(character in str(path) for character in _WILDCARD_CHARACTERS):
        return [Path(match).resolve() for match in glob.glob(str(path))] or [path]
    else:
        return [path]


def _list_removed_worktrees(call: CommandCall) -> list[Path]:
    """The task worktrees inside the folders a removing command deletes or moves, which their own paths do not name."""
    removed = _list_removed_folders(call)

    return [root for folder in removed for root in _list_contained_worktree_roots(folder)]


def _list_removed_owner_records(call: CommandCall) -> list[Path]:
    """Owner records inside a Git folder a removing command deletes or moves."""
    removed = [folder for folder in _list_removed_folders(call) if GIT_DIRECTORY_NAME in folder.parts]

    return [record for folder in removed for pattern in _OWNER_RECORD_PATTERNS for record in folder.glob(pattern)]


def _list_removed_folders(call: CommandCall) -> list[Path]:
    program = _read_call_program(call)
    removes = program in _REMOVING_PROGRAMS or _is_removing_rsync(call)
    folders = _list_written_absolute_paths(call) if removes else []

    return [folder for folder in folders if folder.is_dir()]


def _list_contained_worktree_roots(folder: Path) -> list[Path]:
    """Linked worktrees of the repository holding a folder that lie inside it, read from the repository's own list."""
    root = find_repository_root(folder)
    common = _read_common_directory(root) if root is not None else None
    records = sorted(common.glob(_WORKTREE_RECORD_PATTERN)) if common is not None else []
    roots = [_read_worktree_root(record) for record in records]

    return [path for path in roots if path is not None and (path == folder or folder in path.parents)]


def _read_common_directory(root: Path) -> Path | None:
    git_path = root / GIT_DIRECTORY_NAME
    worktree = find_linked_worktree(root) if git_path.is_file() else None

    if git_path.is_dir():
        return git_path.resolve()
    elif worktree is not None:
        return _read_text_path(worktree.git_directory / COMMONDIR_FILE, worktree.git_directory)
    else:
        return None


def _read_worktree_root(record: Path) -> Path | None:
    """A worktree's folder from its `gitdir` record, which names the worktree's `.git` file."""
    git_file = _read_text_path(record, record.parent)

    return git_file.parent if git_file is not None else None


def _read_text_path(path: Path, base: Path) -> Path | None:
    try:
        text = path.read_text(encoding=_ENCODING).strip()
    except (OSError, UnicodeDecodeError):
        return None
    else:
        return (base / text).resolve() if text else None


def _find_worktrees(paths: Iterable[Path]) -> list[LinkedWorktree]:
    """The task worktrees holding these paths, each once."""
    worktrees = {worktree.root: worktree for path in paths if (worktree := find_linked_worktree(path)) is not None}

    return list(worktrees.values())


def _read_call_program(call: CommandCall) -> str:
    return os.path.basename(call.argv[0]) if call.argv else ""


def _describe_call(call: CommandCall) -> str:
    return " ".join(call.argv[:4]) or "a command"


def _join(cwd: str | None, path: str) -> Path:
    """The absolute path with symbolic links resolved, so a linked folder cannot hide the original checkout."""
    expanded = os.path.expanduser(path)

    return Path(os.path.join(cwd or os.getcwd(), expanded)).resolve()


def _optional_path(folder: str | None) -> list[Path]:
    return [Path(folder).resolve()] if folder else []


def _run_git(root: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
        check=False,
    )


def _try_expand(command: str, cwd: str | None) -> list[CommandCall]:
    try:
        return expand_shell_command(command, cwd, _get_config_directory())
    except ExpansionLimitError:
        return []


def _write_pending_adds(tool_use_id: str, paths: list[Path]) -> None:
    directory = _get_pending_adds_directory()

    directory.mkdir(parents=True, exist_ok=True)
    for stale in directory.glob(_PENDING_PATTERN):
        if _read_modified_time(stale) < time.time() - _PENDING_LIFETIME_SECONDS:
            stale.unlink(missing_ok=True)
    (directory / f"{tool_use_id}.json").write_text(json.dumps([str(path) for path in paths]), encoding=_ENCODING)


def _read_modified_time(path: Path) -> float:
    """Another hook run may remove the file meanwhile; a missing file reads as fresh, so it is left alone."""
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return time.time()


def _get_pending_adds_directory() -> Path:
    return _get_config_directory() / _PENDING_ADDS_DIRECTORY


def _find_installed_script(call: CommandCall, name: str) -> int:
    """Where a call runs this hook's sibling script, or -1; a script elsewhere with the same name is any program."""
    index = find_script_index(call.argv, name)
    path = _join(call.cwd, call.argv[index]) if index >= 0 and (call.cwd or os.path.isabs(call.argv[index])) else None

    return index if path is not None and path == _SCRIPTS_DIRECTORY / name else -1


def _get_config_directory() -> Path:
    return Path(os.path.expanduser(os.environ.get(_CONFIG_DIRECTORY_VARIABLE) or _DEFAULT_CONFIG_DIRECTORY))


def _make_identity_reader(payload: dict[str, Any]) -> _IdentityReader:
    """This session's identity, read only when a call touches a task worktree, since finding a terminal session's
    process takes several `ps` runs."""
    session_id = payload.get(_SESSION_KEY) or None

    @functools.cache
    def read_identity() -> SessionIdentity | None:
        return read_current_identity(session_id)

    return read_identity


def _is_reading_call(call: CommandCall) -> bool:
    """Whether a call's program only reads: known readers, read-only Git, gh, and Codex, and print-only sed, with no
    option or variable that makes it run another program."""
    program = _read_program_name(call.argv[0]) if call.argv else ""
    git_command = parse_git_command(call.argv, call.cwd)
    writes_index = find_script_index(call.argv, _SESSION_WRITES_NAME)
    arguments = call.argv[1:]
    runs_program = any(_is_program_running_variable(name) for name, _ in call.assignments)

    if not call.argv:
        return True
    elif runs_program or call.dynamic_name:
        return False
    elif writes_index >= 0:
        return call.argv[writes_index + 1 : writes_index + 2] == [_REPORT_COMMAND]
    elif git_command is not None:
        return _is_reading_git(git_command.subcommand, git_command.arguments, git_command.global_options)
    elif program == _FIND_PROGRAM:
        return not any(argument in _FIND_WRITING_ACTIONS for argument in arguments)
    elif program == _SORT_PROGRAM:
        return not any(argument.startswith(_SORT_OUTPUT_OPTIONS) for argument in arguments)
    elif program == _RG_PROGRAM:
        return not any(argument.startswith(_RG_PROGRAM_OPTIONS) for argument in arguments)
    elif program in _SED_PROGRAMS:
        return _is_print_only_sed(arguments)
    elif program == _GH_PROGRAM:
        return arguments[1:2] != [] and arguments[1] in _READING_GH_COMMANDS.get(arguments[0], frozenset())
    elif program == _CODEX_PROGRAM:
        return _is_read_only_codex(arguments)
    else:
        return program in _READING_PROGRAMS


def _read_program_name(word: str) -> str:
    """A program's bare name; a path counts only in the system's own program folders, since a local script can carry
    a reading program's name."""
    folder, name = os.path.split(word)

    return name if not folder or folder in _SYSTEM_PROGRAM_FOLDERS else ""


def _is_reading_git(subcommand: str, arguments: list[str], global_options: list[str]) -> bool:
    """Read-only subcommands without options that run a program, and Git options without configuration, which may
    name a program (a pager, a diff driver); a configuration Git reads from the environment makes the call writing."""
    keys = [_read_git_config_key(global_options, index) for index in range(len(global_options))]
    has_configuration = any(key is not None and not key.startswith(_SAFE_GIT_CONFIG_PREFIXES) for key in keys)
    runs_program = any(argument.startswith(_PROGRAM_RUNNING_GIT_OPTIONS) for argument in arguments)

    if has_configuration or runs_program:
        return False
    elif subcommand == _WORKTREE_SUBCOMMAND:
        return arguments[:1] == [_WORKTREE_LIST_ACTION]
    elif subcommand == _BRANCH_SUBCOMMAND:
        return all(argument in _LISTING_BRANCH_OPTIONS for argument in arguments)
    else:
        return subcommand in _READING_GIT_SUBCOMMANDS


def _read_git_config_key(global_options: list[str], index: int) -> str | None:
    """The key a `-c key=value` or `-ckey=value` global option sets at this position, or None for another option."""
    option = global_options[index]

    if option == _GIT_CONFIG_OPTION and index + 1 < len(global_options):
        return global_options[index + 1].split(_VALUE_SEPARATOR, 1)[0].lower()
    elif option.startswith(_GIT_CONFIG_OPTION) and len(option) > len(_GIT_CONFIG_OPTION):
        return option[len(_GIT_CONFIG_OPTION) :].split(_VALUE_SEPARATOR, 1)[0].lower()
    else:
        return None


def _is_print_only_sed(arguments: list[str]) -> bool:
    """`sed -n 1,20p <file>` and the like; any other script may write a file with its `w` command."""
    options = [argument for argument in arguments if argument.startswith(_OPTION_PREFIX)]
    operands = [argument for argument in arguments if not argument.startswith(_OPTION_PREFIX)]

    return (
        all(option in _SED_READING_OPTIONS for option in options)
        and bool(operands)
        and _SED_PRINT_SCRIPT.fullmatch(operands[0]) is not None
    )


def _is_read_only_codex(arguments: list[str]) -> bool:
    """`codex exec` whose sandbox is named read-only on the command line, with no option that widens it or writes a
    file of its own (`-o` saves the last message)."""
    named_read_only = any(
        (argument in _CODEX_SANDBOX_OPTIONS and arguments[index + 1 : index + 2] == [_CODEX_READ_ONLY])
        or argument == f"--sandbox={_CODEX_READ_ONLY}"
        for index, argument in enumerate(arguments)
    )
    widened = any(argument.split(_VALUE_SEPARATOR, 1)[0] in _CODEX_WRITING_OPTIONS for argument in arguments)
    configured = [
        arguments[index + 1] if index + 1 < len(arguments) else ""
        for index, argument in enumerate(arguments)
        if argument in _CODEX_CONFIG_OPTIONS
    ]
    widens_configuration = any(value.startswith(_CODEX_SANDBOX_CONFIG_KEYS) for value in configured)

    return (
        arguments[:1] != []
        and arguments[0] in _CODEX_EXEC_COMMANDS
        and named_read_only
        and not widened
        and not widens_configuration
    )


def _is_file_command(call: CommandCall) -> bool:
    """File commands and in-place editors change only the files they name, which the written paths already hold."""
    program = _read_call_program(call)
    is_in_place = program in IN_PLACE_EDITORS and bool(call.list_written_files())

    return program in _EVERY_OPERAND_WRITERS or program in _DESTINATION_WRITERS or program == TEE_PROGRAM or is_in_place


def _is_removing_rsync(call: CommandCall) -> bool:
    program = _read_call_program(call)

    return program == _RSYNC_PROGRAM and _RSYNC_REMOVE_OPTION in call.argv[1:]


def _is_uncertain_call(call: CommandCall) -> bool:
    """Whether a call that may write hides where: a word only known when it runs, or a folder the hook cannot see."""
    may_write = bool(call.write_targets) or not _is_reading_call(call)
    hides_target = any(_has_marks(target) or _SHELL_EXPANSION_START in target for target in call.write_targets)
    hides_word = call.dynamic_name or call.unknown_arguments or hides_target or any(map(_has_marks, call.argv))
    moves_git = any(name in _GIT_LOCATION_VARIABLES for name, _ in call.assignments)

    return may_write and (hides_word or call.cwd is None or moves_git)


def _sets_session_identity(call: CommandCall) -> bool:
    """Whether a call names a session for the owner record or the commit wrapper, which read it from the variables
    Claude Code sets, so a call could otherwise speak as another session."""
    names = {name for name, _ in call.assignments}
    is_owner_tool = _read_owner_command(call) is not None or find_script_index(call.argv, _COMMIT_WRAPPER_NAME) >= 0

    return is_owner_tool and bool(names & _SESSION_VARIABLES)


def _has_marks(text: str) -> bool:
    return VARIABLE_MARK in text or UNKNOWN_MARK in text


def _is_program_running_variable(name: str) -> bool:
    return name in _PROGRAM_RUNNING_VARIABLES or name.startswith(_GIT_CONFIG_VARIABLE_PREFIX)


def _is_ignored(root: Path, path: Path) -> bool:
    return _run_git(root, ["check-ignore", "--quiet", str(path)]).returncode == 0


def _is_inside(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


def _is_unguarded_worktree_change(subcommand: str, arguments: list[str]) -> bool:
    return subcommand == _WORKTREE_SUBCOMMAND and bool(arguments) and arguments[0] in _UNGUARDED_WORKTREE_ACTIONS


if __name__ == "__main__":
    main()
