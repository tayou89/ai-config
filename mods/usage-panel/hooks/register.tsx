import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type {
  Cache,
  CacheOutput,
  CacheTtl,
  Codex,
  CodexLocalOutput,
  CodexReading,
  CodexServerOutput,
  CodexSetting,
  CodexTotals,
  Helper,
  History,
  Insight,
  Level,
  Limit,
  ListedSession,
  Live,
  Reading,
  Segment,
  SessionCounts,
  SessionListFilter,
  SessionRead,
  SessionsOutput,
  SessionsRequest,
  Shared,
  SharedFile,
  Tab,
  Turn,
  Where,
} from '../types'

// A session's state file as the app last saved it.
type SavedFile = { file: string; savedAt: number }
// What was last read of a session: its file's save time, and the activity
// stamp its transcript was last looked at for a question.
type SessionSeen = SessionRead & { savedAt: number; askedAt?: string }
type SessionState = keyof SessionCounts | 'idle'
type CacheView = { kind: 'warm'; until: string } | { kind: 'soon'; minutes: number } | { kind: 'cold'; after: string }
// What each shared reading holds, by its file's name.
type SharedOutputs = {
  [SHARED_NAME.codexLocal]: CodexLocalOutput
  [SHARED_NAME.codexServer]: CodexServerOutput
  [SHARED_NAME.history]: History
}
type SharedName = keyof SharedOutputs
// One shared reading asked for: one made since `since` for the key's folder.
type SharedRequest<N extends SharedName> = { name: N; key: string; since: number; arguments: readonly string[] }
// One piece of work a session repeats: a timer's tick, or a refresh an event asks for.
type OneAtATime = { tick: () => Promise<void>; refresh: () => Promise<void> }

const PANE = 'usage'
const TITLE = '사용량'
const KEEP_TURNS = 30
const RECOUNT_EVERY_MS = 10 * 60 * 1000

// Change a tag when that value's fields change: it declines what an earlier
// version stored, which outlives a reload.
const live = atom({ plugin: 'usage-panel', key: 'live' } as const, { limits: [], contextWindow: 0 } as Live, {
  shape: 'live-1',
})
const turns = atom({ plugin: 'usage-panel', key: 'turns' } as const, [] as Turn[], { shape: 'turns-1' })
const history = atom({ plugin: 'usage-panel', key: 'history' } as const, null as History | null, {
  shape: 'history-1',
})
const recount = atom({ plugin: 'usage-panel', key: 'recount' } as const, { isRunning: false }, {
  shape: 'recount-1',
})
const tzOffset = atom({ plugin: 'usage-panel', key: 'tzOffset' } as const, 0, { shape: 'tz-1' })
const tab = atom({ plugin: 'usage-panel', key: 'tab' } as const, 'now' as Tab, { shape: 'tab-1' })
// Which thresholds of which window already raised a toast, as
// `kind|resetsAt|threshold`, so each crossing alerts once.
const alerted = atom({ plugin: 'usage-panel', key: 'alerted' } as const, [] as string[], { shape: 'alerted-1' })

// The same thresholds the Claude Usage menu bar app alerts at.
const ALERT_AT = [75, 90, 95]
const insights = atom({ plugin: 'usage-panel', key: 'insights' } as const, [] as Insight[], { shape: 'insights-2' })
// The heavy reply's one-line note beside the branch, cleared by the next prompt.
const note = atom({ plugin: 'usage-panel', key: 'note' } as const, null as Insight | null, { shape: 'note-1' })
// Bumped every second while a turn runs, so its live counts and clock redraw.
const tick = atom({ plugin: 'usage-panel', key: 'tick' } as const, 0, { shape: 'tick-1' })
const KEEP_INSIGHTS = 20
const where = atom({ plugin: 'usage-panel', key: 'where' } as const, null as Where | null, { shape: 'where-1' })

const WHERE_EVERY_MS = 15 * 1000
// Subagents that are at work: shown while any is, gone when none is.
const helpers = atom({ plugin: 'usage-panel', key: 'helpers' } as const, [] as Helper[], { shape: 'helpers-1' })
const HELPERS_EVERY_MS = 3 * 1000
const BUSY = ['pending', 'running', 'waiting']
// Codex is read from its own server every few minutes, and from the records its
// runs write every ten seconds while one runs or a record was written: a run
// writes its totals every few tens of seconds, and the band follows them.
const INITIAL_CODEX: Codex = { limits: [], working: [], running: 0 }
const codex = atom({ plugin: 'usage-panel', key: 'codex' } as const, { ...INITIAL_CODEX }, { shape: 'codex-3' })
const CODEX_SESSIONS = '.codex/sessions'
const CODEX_CONFIG = '.codex/config.toml'
const DAY_MS = 24 * 60 * 60 * 1000
const CODEX_TICK_MS = 10 * 1000
const CODEX_SERVER_EVERY_MS = 5 * 60 * 1000
// A reading older than this is drawn grey with the time it was read.
const STALE_MS = 15 * 60 * 1000
const CODEX_READ_MS = 40 * 1000
const CODEX_RUN = /\bcodex\s+(?:exec|e)\b/
const CODEX_MODE = { local: 'local', server: 'server' } as const
const CODEX_READ_FAILED = 'Codex 사용량을 읽지 못했어요'
const CLAUDE_NAME = 'Claude'
const CODEX_NAME = 'Codex'
// Every session here reads the one account's Claude limits, but only from its
// own replies, so an idle one would keep the reading of its last reply. The
// sessions pass the newest reading through one file outside the mod's folder,
// where a write never reloads the mod.
const SHARED_FOLDER = 'Library/Caches/usage-panel'
const CLAUDE_SHARED = `${SHARED_FOLDER}/claude-limits.json`
const CLAUDE_SHARE_EVERY_MS = 15 * 1000
// The Codex readings and the 7-day count pass through the same folder: one
// session reads, under a lock on the file, and the others take its reading.
const SHARED_NAME = { codexLocal: 'codex-local', codexServer: 'codex-server', history: 'history' } as const
const SHARED_BUSY = 75
// A shared reader stops itself this long before its call times out.
const SHARED_MARGIN_MS = 5 * 1000
const SHARED_ANY_FOLDER = ''
// How often a session looks whether the 7-day count is due; a count less than
// RECOUNT_EVERY_MS old, made here or in another session, stands.
const RECOUNT_CHECK_MS = 60 * 1000
const RECOUNT_READ_MS = 120 * 1000
// Set once the session has started, so a reload of the mod finds it set.
const loaded = atom({ plugin: 'usage-panel', key: 'loaded' } as const, false, { shape: 'loaded-1' })
const PYTHON = '/usr/bin/python3'
// The other sessions, counted as the app's sidebar marks them: at work by the
// app's own session list, the dots by the store of unread sessions and the
// verdict the app saves for each turn. The list is asked every few seconds;
// a file is read again only once the app saved it.
const sessions = atom({ plugin: 'usage-panel', key: 'sessions' } as const, null as SessionCounts | null, {
  shape: 'sessions-1',
})
const SESSIONS_EVERY_MS = 5 * 1000
const SESSIONS_READ_MS = 20 * 1000
// The one switch for the yellow count, which stands in for a dot the app keeps
// to itself: off, a session waiting for the person is counted in none.
const IS_WAITING_COUNTED = true
const SESSION_LIST = { server: 'ccd_session_mgmt', tool: 'list_sessions', limit: 500 } as const
const APP_SESSIONS = 'Library/Application Support/Claude/claude-code-sessions'
const APP_STORE = 'Library/Application Support/Claude/Local Storage/leveldb'
const STORE_FILE = /\.(log|ldb)$/
const READER_MODE = { sessions: 'sessions', cache: 'cache' } as const
// In the sidebar's colors: yellow waits for the person, blue is finished and
// unread; work stays grey.
const SESSION_PARTS = [
  { state: 'working', color: undefined, isBold: false },
  { state: 'waiting', color: 'warning', isBold: true },
  { state: 'done', color: 'suggestion', isBold: true },
] as const
// The main conversation's prompt cache lives an hour on a subscription and
// five minutes past its limits, from the last request that read or wrote it.
const CACHE_TTL_MS: Record<CacheTtl, number> = { '1h': 60 * 60 * 1000, '5m': 5 * 60 * 1000 }
const cache = atom({ plugin: 'usage-panel', key: 'cache' } as const, { ttlMs: CACHE_TTL_MS['1h'] } as Cache, {
  shape: 'cache-1',
})
// Bumped each minute while the cache nears its end, so its minutes redraw.
const cacheTick = atom({ plugin: 'usage-panel', key: 'cacheTick' } as const, 0, { shape: 'cache-tick-1' })
const CACHE_SOON_MS = 10 * 60 * 1000
const CACHE_TICK_MS = 60 * 1000
// A rise of the limit during one reply or run: 2 points is a lot, 4 is too much.
const RISE_WARN = 2
const RISE_HIGH = 4
// The path is shortened past this many characters, as Powerlevel10k's
// truncate_to_unique does: middle folders become their shortest prefix no
// sibling shares, while the first, the last and anchor folders stay whole.
const MAX_PATH = 40
const ANCHORS = ['.git', '.hg', '.svn', '.tool-versions', '.node-version', '.python-version', '.mise.toml']

// The runner every shared reader starts with: it prints a reading another
// session made since the time asked for, or makes one under a lock on the
// file, or exits with SHARED_BUSY while another session holds that lock. Its
// alarm stops a read that outlasts its limit, which lets the lock go. A
// reader joined to it imports what it needs after it, as one `python -c` text.
const SHARED_READER = String.raw`
from __future__ import annotations

import fcntl
import json
import os
import signal
import sys
import time
from collections.abc import Callable, Iterator
from types import FrameType
from typing import Any, TextIO

Json = dict[str, Any]
Read = Callable[[list[str], Json], tuple[Json, Json]]

BUSY = 75
DAY_MS = 24 * 60 * 60 * 1000
HEAD_BYTES = 256


def _stop(signal_number: int, frame: FrameType | None) -> None:
    raise SystemExit("the read took longer than its limit")


def _load(path: str) -> Json:
    try:
        with open(path, encoding="utf-8") as file:
            loaded = json.load(file)
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path: str, value: Json) -> None:
    temporary = f"{path}.{os.getpid()}.tmp"

    try:
        with open(temporary, "w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def _try_lock(lock: TextIO) -> bool:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _scan_path(path: str) -> str:
    return f"{os.path.splitext(path)[0]}.scan.json"


def _read_shared(path: str, key: str, since: int, arguments: list[str], read: Read) -> Json:
    entries: Json = _load(path).get("entries") or {}
    kept = entries.get(key)

    if kept and kept["at"] > since:
        return dict(kept)
    else:
        at = int(time.time() * 1000)
        output, scans = read(arguments, _load(_scan_path(path)))
        shared = {"at": at, "output": output}
        recent = {name: entry for name, entry in entries.items() if at - entry["at"] < DAY_MS}

        _save(_scan_path(path), scans)
        _save(path, {"entries": {**recent, key: shared}})
        return shared


def run_shared(read: Read) -> None:
    """Prints the reading under the key: one another session made since the given time, or a new one.

    Exits with BUSY, reading nothing, while another session holds the lock on the same file.
    """
    path, key, since, limit, *arguments = sys.argv[1:]

    signal.signal(signal.SIGALRM, _stop)
    signal.alarm(int(limit))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(f"{path}.lock", "w") as lock:
        if _try_lock(lock):
            print(json.dumps(_read_shared(path, key, int(since), arguments, read), ensure_ascii=False))
        else:
            sys.exit(BUSY)


def _head(path: str) -> str:
    with open(path, "rb") as file:
        return file.read(HEAD_BYTES).hex()


def _is_same_start(path: str, head: str, kept: Json) -> bool:
    common = min(len(head), len(kept["head"]))

    return bool(kept["offset"] <= os.path.getsize(path) and head[:common] == kept["head"][:common])


def start_place(path: str, kept: Json | None, empty: Json) -> Json:
    """What a file's reading kept, to go on past it; the empty reading when the file was replaced or cut."""
    head = _head(path)

    if kept is not None and _is_same_start(path, head, kept):
        return {**kept, "head": head}
    else:
        return {**empty, "offset": 0, "head": head}


def new_lines(path: str, place: Json) -> Iterator[bytes]:
    """Each whole line written past the place, moving the place past it; a line still being written waits."""
    with open(path, "rb") as file:
        file.seek(place["offset"])
        for raw in file:
            if raw.endswith(b"\n"):
                place["offset"] += len(raw)
                yield raw
            else:
                break
`

// Sums every Claude Code reply logged on this computer over the last 7 days.
// Lines of one reply repeat its usage, so each (message id, request id) counts
// once. `weight` estimates how hard a reply leans on the plan from the API's
// price ratios: cache reads are cheap, output is dear. Each transcript is read
// on from where the last count stopped.
const HISTORY_READER = String.raw`
import collections
import datetime
import glob

ROOT = os.path.expanduser("~/.claude/projects")
DAYS = 7
OTHER_PROJECT = "기타"
EMPTY_FILE: Json = {"entries": [], "titles": {}}


def _blank() -> Json:
    return {"read": 0, "write": 0, "weight": 0}


def _add(total: Json, read: int, write: int, weight: float) -> None:
    total["read"] += read
    total["write"] += write
    total["weight"] += weight


def _entry(line: str) -> list[Any] | None:
    """A reply's key and what it counts, as the transcript line has them; none for a line that is no reply."""
    try:
        record = json.loads(line)
    except ValueError:
        return None
    message = record.get("message") or {}
    usage = message.get("usage") or {}

    if record.get("type") != "assistant" or not usage:
        return None
    else:
        fresh = usage.get("input_tokens") or 0
        written = usage.get("cache_creation_input_tokens") or 0
        cached = usage.get("cache_read_input_tokens") or 0
        output = usage.get("output_tokens") or 0
        weight = fresh + written * 1.25 + cached * 0.1 + output * 5

        return [
            message.get("id"),
            record.get("requestId"),
            record.get("timestamp"),
            fresh + written + cached,
            output,
            weight,
            record.get("sessionId"),
            os.path.basename(record.get("cwd") or "") or OTHER_PROJECT,
            message.get("model") or "?",
        ]


def _title(line: str, titles: Json) -> None:
    try:
        record = json.loads(line)

        if record.get("type") == "custom-title" and record.get("customTitle"):
            titles[record.get("sessionId")] = record["customTitle"]
    except ValueError:
        pass


def _is_recent(entry: list[Any], start: datetime.date) -> bool:
    at = _local_time(entry[2])

    return at is not None and at.date() >= start


def _scan_file(path: str, kept: Json | None, start: datetime.date) -> Json:
    """The replies since the start day and the titles a transcript holds, read on from where the last read stopped.

    A reply older than the start day is not kept: the count leaves it out, and the start day only moves on.
    """
    place = start_place(path, kept, EMPTY_FILE)
    entries = [entry for entry in place["entries"] if _is_recent(entry, start)]
    titles = dict(place["titles"])

    for raw in new_lines(path, place):
        line = raw.decode("utf-8", "replace")

        if '"custom-title"' in line:
            _title(line, titles)
        elif '"usage"' in line and '"assistant"' in line:
            entry = _entry(line)

            if entry is not None and _is_recent(entry, start):
                entries.append(entry)
    return {**place, "entries": entries, "titles": titles}


def _local_time(stamp: str | None) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromisoformat((stamp or "").replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def _rank(totals: Json) -> list[Json]:
    rows = ({"name": name, **total, "weight": round(total["weight"])} for name, total in totals.items())

    return sorted(rows, key=lambda row: -row["weight"])


def _count(scans: Json, now: datetime.datetime) -> Json:
    today = now.date()
    start = today - datetime.timedelta(days=DAYS - 1)
    seen: set[tuple[Any, Any]] = set()
    days: Json = collections.defaultdict(lambda: {**_blank(), "replies": 0, "sessions": set()})
    projects: Json = collections.defaultdict(_blank)
    models: Json = collections.defaultdict(_blank)
    sessions: Json = {}
    titles: Json = {}

    for path, scan in scans.items():
        titles.update(scan["titles"])
        for message_id, request_id, stamp, read, write, weight, session_id, project, model in scan["entries"]:
            key = (message_id, request_id)

            if key not in seen:
                seen.add(key)
                at = _local_time(stamp)

                if at is not None and at.date() >= start:
                    session = session_id or path
                    day = days[at.date().isoformat()]
                    row = sessions.setdefault(session, {"id": session, "project": project, **_blank(), "last": ""})

                    _add(day, read, write, weight)
                    day["replies"] += 1
                    day["sessions"].add(session)
                    _add(projects[project], read, write, weight)
                    if not model.startswith("<"):
                        _add(models[model], read, write, weight)
                    _add(row, read, write, weight)
                    row["last"] = max(row["last"], at.isoformat())
    counted_days = []

    for back in range(DAYS):
        date = (today - datetime.timedelta(days=back)).isoformat()
        day = days.get(date) or {**_blank(), "replies": 0, "sessions": set()}
        counted = {
            "date": date,
            "read": day["read"],
            "write": day["write"],
            "weight": round(day["weight"]),
            "replies": day["replies"],
            "sessions": len(day["sessions"]),
        }

        counted_days.append(counted)
    top = sorted(sessions.values(), key=lambda row: -row["weight"])[:5]

    for row in top:
        row["title"] = titles.get(row["id"]) or ""
        row["weight"] = round(row["weight"])
    return {
        "generatedAt": now.isoformat(),
        "total": round(sum(day["weight"] for day in days.values())),
        "days": counted_days,
        "projects": _rank(projects)[:5],
        "models": _rank(models)[:4],
        "sessions": top,
    }


def read_history(arguments: list[str], kept: Json) -> tuple[Json, Json]:
    now = datetime.datetime.now().astimezone()
    start = now.date() - datetime.timedelta(days=DAYS - 1)
    scans: Json = {}

    for path in glob.glob(os.path.join(ROOT, "**", "*.jsonl"), recursive=True):
        try:
            if datetime.date.fromtimestamp(os.path.getmtime(path)) >= start:
                scans[path] = _scan_file(path, kept.get(path), start)
        except OSError:
            pass
    return _count(scans, now), scans


run_shared(read_history)
`

// Reads Codex without its credentials, joined to SHARED_READER. In `server`
// mode `codex app-server` asks Codex's own server for the limits and the
// setting a run in the given folder takes (a CodexServerOutput). In `local`
// mode the newest run records under ~/.codex/sessions give the limits as of
// their last step, the last finished `codex exec` run and how many runs work
// now (a CodexLocalOutput). Big records are read from their end for the limits;
// a run's record is read whole once and then on from where the last read
// stopped, and only for the last finished run and the runs at work. Keys
// without a value are left out.
const CODEX_READER = String.raw`
import glob, subprocess, select, shutil, datetime, functools
HOME = os.path.expanduser('~')
ROOT = os.path.join(HOME, '.codex', 'sessions')
KIND = {300: 'five_hour', 10080: 'seven_day'}
TAIL_BYTES = 512 * 1024
START_SLACK_MS = 5000

def iso(seconds):
    return datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc).isoformat()

def ms(stamp):
    return int(datetime.datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp() * 1000)

def windows(rate):
    found = []
    for key in ('primary', 'secondary'):
        w = rate.get(key) or {}
        minutes = w.get('windowDurationMins', w.get('window_minutes'))
        used = w.get('usedPercent', w.get('used_percent'))
        resets = w.get('resetsAt', w.get('resets_at'))
        if minutes in KIND and used is not None:
            found.append({'kind': KIND[minutes], 'percentUsed': used, **({'resetsAt': iso(resets)} if resets else {})})
    return found

def reading(at, rate):
    return {'at': at, 'limits': windows(rate), **({'plan': rate.get('planType') or rate.get('plan_type')} if (rate.get('planType') or rate.get('plan_type')) else {})}

def codex_path():
    for p in (os.path.join(HOME, '.local/bin/codex'), '/opt/homebrew/bin/codex', '/usr/local/bin/codex'):
        if os.access(p, os.X_OK):
            return p
    return shutil.which('codex')

def setting(model, effort):
    return {'model': model, **({'effort': effort} if effort else {})} if model else None

# The setting a new run in this folder takes: its config, or with no model
# there, the model Codex marks as its default. None when the config was not read.
def configured_setting(answer, listed):
    if not answer or 'error' in answer:
        return None
    config = (answer.get('result') or {}).get('config') or {}
    models = ((listed or {}).get('result') or {}).get('data') or []
    model = config.get('model') or next((m.get('model') for m in models if m.get('isDefault')), None)
    return setting(model, config.get('model_reasoning_effort'))

def ask_server(cwd):
    path = codex_path()
    if not path:
        return None, None, 'Codex를 찾지 못했어요'
    asks = {
        2: ('account/rateLimits/read', {'excludeResetCreditDetails': True}),
        3: ('config/read', {'cwd': cwd}),
        4: ('model/list', {}),
    }
    answers = {}
    p = subprocess.Popen([path, 'app-server'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
    try:
        p.stdin.write(json.dumps({'id': 1, 'method': 'initialize', 'params': {'clientInfo': {'name': 'usage-panel', 'version': '0.1.0'}}}) + '\n')
        p.stdin.flush()
        end = time.time() + 20
        while time.time() < end and len(answers) < len(asks):
            if not select.select([p.stdout], [], [], 0.5)[0]:
                continue
            line = p.stdout.readline()
            if not line:
                break
            m = json.loads(line)
            if m.get('id') == 1:
                p.stdin.write(json.dumps({'method': 'initialized'}) + '\n')
                for i, (method, params) in asks.items():
                    p.stdin.write(json.dumps({'id': i, 'method': method, 'params': params}) + '\n')
                p.stdin.flush()
            elif m.get('id') in asks:
                answers[m['id']] = m
    finally:
        p.terminate()
    configured = configured_setting(answers.get(3), answers.get(4))
    if 2 not in answers:
        return None, configured, 'Codex가 제때 답하지 않았어요'
    if 'error' in answers[2]:
        return None, configured, 'Codex 사용량을 읽지 못했어요'
    return reading(int(time.time() * 1000), (answers[2].get('result') or {}).get('rateLimits') or {}), configured, None

@functools.lru_cache(maxsize=None)
def first(path):
    with open(path, 'rb') as f:
        return json.loads(f.readline()).get('payload') or {}

def tail(path):
    with open(path, 'rb') as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - TAIL_BYTES))
        lines = f.read().split(b'\n')
    return lines[1:] if size > TAIL_BYTES else lines

def recent():
    today = datetime.date.today()
    files = []
    for d in range(3):
        files += glob.glob(os.path.join(ROOT, (today - datetime.timedelta(days=d)).strftime('%Y/%m/%d'), 'rollout-*.jsonl'))
    return sorted(files, key=os.path.getmtime, reverse=True)

def snapshot(files):
    for path in files[:6]:
        for raw in reversed(tail(path)):
            if b'"token_count"' in raw and b'"rate_limits"' in raw:
                o = json.loads(raw)
                rate = (o.get('payload') or {}).get('rate_limits') or {}
                if rate.get('limit_id') in (None, 'codex') and windows(rate):
                    return reading(ms(o['timestamp']), rate)
    return None

# A run started by codex exec itself: not one Codex spawned under it, such as
# the reviewer that approves its commands, whose record also says codex_exec.
def is_exec(path):
    head = first(path)
    return head.get('originator') == 'codex_exec' and head.get('source', 'exec') == 'exec'

EMPTY_RECORD = {
    'model': None,
    'effort': None,
    'finished': None,
    'total': None,
    'first_rate': None,
    'last_rate': None,
    'rate_count': 0,
    'steps': 0,
}


# What a run's record says so far, read on from where the last read stopped:
# the setting its last turn started with, its totals, the limits at its first
# and newest step and how many steps reported them, how many steps reported
# their totals, when it finished.
def scan_record(path, kept):
    record = start_place(path, kept, EMPTY_RECORD)

    for raw in new_lines(path, record):
        if b'"turn_context"' in raw[:120]:
            payload = json.loads(raw)['payload']

            record['model'] = payload.get('model') or record['model']
            record['effort'] = payload.get('effort') or record['effort']
        elif b'"token_count"' in raw:
            payload = json.loads(raw)['payload']

            if payload.get('info'):
                record['total'] = payload['info'].get('total_token_usage') or record['total']
                record['steps'] += 1
            if payload.get('rate_limits'):
                found = windows(payload['rate_limits'])

                record['first_rate'] = found if record['rate_count'] == 0 else record['first_rate']
                record['last_rate'] = found
                record['rate_count'] += 1
        elif b'"task_complete"' in raw:
            record['finished'] = ms(json.loads(raw)['timestamp'])
    return record


# A run's totals so far; all zero before its first step reports them.
def totals(path, record):
    total = record['total'] or {}
    found = {
        'read': total.get('input_tokens', 0),
        'cached': total.get('cached_input_tokens', 0),
        'write': total.get('output_tokens', 0),
        'folder': first(path).get('cwd', ''),
    }

    if record['model']:
        found['model'] = record['model']
    if record['effort']:
        found['effort'] = record['effort']
    if record['rate_count'] >= 2 and record['last_rate']:
        kind = 'five_hour' if any(w['kind'] == 'five_hour' for w in record['last_rate']) else 'seven_day'
        start = next((w['percentUsed'] for w in record['first_rate'] if w['kind'] == kind), None)
        end = next((w['percentUsed'] for w in record['last_rate'] if w['kind'] == kind), None)

        if start is not None and end is not None:
            found['rise'] = end - start
    return found


def last_run(files, read_record):
    for path in files:
        if is_exec(path):
            record = read_record(path)

            if record['finished'] and record['total']:
                return {'finishedAt': record['finished'], **totals(path, record)}
    return None

# When each codex exec at work started, from the process list.
def exec_starts():
    lines = subprocess.run(['/bin/ps', '-axo', 'lstart=,command='], capture_output=True, text=True, env={'LC_ALL': 'C'}).stdout.splitlines()
    starts = []
    for words in (l.split() for l in lines):
        if len(words) > 6 and words[5].endswith('codex') and words[6] == 'exec':
            starts.append(int(time.mktime(time.strptime(' '.join(words[:5]), '%a %b %d %H:%M:%S %Y')) * 1000))
    return starts

# A run at work is the one whose record began when a codex exec process
# started; a record left unfinished by a run that was stopped has no process.
# Only such a record is read, the others are passed by their first line.
def working(files, starts, read_record):
    found = []

    for path in files:
        if is_exec(path):
            began = ms(first(path)['timestamp'])

            if any(abs(began - s) <= START_SLACK_MS for s in starts):
                record = read_record(path)

                if not record['finished']:
                    found.append({'startedAt': began, 'steps': record['steps'], **totals(path, record)})
    return found


def present(values):
    return {key: value for key, value in values.items() if value is not None}


# Arguments: 'local', or 'server' and the folder the setting is read for.
def read_codex(arguments, kept):
    mode = arguments[0]

    if mode == 'server':
        limits, configured, error = ask_server(arguments[1])

        return present({'limits': limits, 'configured': configured, 'error': error}), {}
    else:
        records = {}

        def read_record(path):
            if path not in records:
                records[path] = scan_record(path, kept.get(path))
            return records[path]

        files = recent()
        starts = exec_starts()
        found = {
            'snapshot': snapshot(files),
            'last': last_run(files, read_record),
            'working': working(files, starts, read_record),
            'running': len(starts),
        }

        return present(found), records

run_shared(read_codex)
`

// Each shared reading by its file's name: the script that makes it and how
// long it may take.
const SHARED_READERS = {
  [SHARED_NAME.codexLocal]: { script: CODEX_READER, timeoutMs: CODEX_READ_MS },
  [SHARED_NAME.codexServer]: { script: CODEX_READER, timeoutMs: CODEX_READ_MS },
  [SHARED_NAME.history]: { script: HISTORY_READER, timeoutMs: RECOUNT_READ_MS },
} as const

// Reads what the app keeps of its sessions, without changing any of it, in
// one of two modes. `sessions` (argument: a SessionsRequest) prints a
// SessionsOutput: the newest write of the sidebar's unread list from the app's
// Local Storage (LevelDB, its snappy blocks and write-ahead log decoded here),
// each session's verdict from its state file, and, where asked, whether its
// transcript ends on a question still open. `cache` (argument: this session's
// id) prints a CacheOutput from the end of this session's transcript.
const SESSIONS_READER = String.raw`
import json, os, glob, struct, sys, datetime
PROJECTS = os.path.expanduser('~/.claude/projects')
UNREAD_KEY = b'epitaxy-unread-v1'
TAIL_BYTES = 512 * 1024
NEEDS_INPUT = ('blocked', 'need_input')
LOCAL_COMMANDS = ('<command-name>', '<local-command-', '<bash-input>', '<bash-stdout>', '<bash-stderr>')

def varint(b, i):
    shift = n = 0
    while True:
        c = b[i]
        i += 1
        n |= (c & 0x7F) << shift
        if c < 0x80:
            return n, i
        shift += 7

def snappy(b):
    _, i = varint(b, 0)
    out = bytearray()
    while i < len(b):
        tag = b[i]
        i += 1
        kind = tag & 3
        if kind == 0:
            ln = tag >> 2
            if ln >= 60:
                extra = ln - 59
                ln = int.from_bytes(b[i:i + extra], 'little')
                i += extra
            ln += 1
            out += b[i:i + ln]
            i += ln
            continue
        if kind == 1:
            ln = ((tag >> 2) & 7) + 4
            off = ((tag >> 5) << 8) | b[i]
            i += 1
        elif kind == 2:
            ln = (tag >> 2) + 1
            off = int.from_bytes(b[i:i + 2], 'little')
            i += 2
        else:
            ln = (tag >> 2) + 1
            off = int.from_bytes(b[i:i + 4], 'little')
            i += 4
        for _ in range(ln):
            out.append(out[-off])
    return bytes(out)

def block(data, off, size):
    raw = data[off:off + size]
    return snappy(raw) if data[off + size] == 1 else raw

def entries(blk):
    restarts = struct.unpack('<I', blk[-4:])[0]
    end = len(blk) - 4 - 4 * restarts
    i, key = 0, b''
    while i < end:
        shared, i = varint(blk, i)
        non, i = varint(blk, i)
        vlen, i = varint(blk, i)
        key = key[:shared] + blk[i:i + non]
        i += non
        yield key, blk[i:i + vlen]
        i += vlen

# Each write of the key in a table, as (sequence number, value).
def table(path):
    data = open(path, 'rb').read()
    _, j = varint(data[-48:], 0)
    _, j = varint(data[-48:], j)
    index, j = varint(data[-48:], j)
    size, j = varint(data[-48:], j)
    for _, handle in entries(block(data, index, size)):
        offset, k = varint(handle, 0)
        length, _ = varint(handle, k)
        for key, value in entries(block(data, offset, length)):
            if UNREAD_KEY in key:
                yield int.from_bytes(key[-8:], 'little') >> 8, value

# Each write of the key in the write-ahead log, which holds the newest ones.
def log(path):
    data = open(path, 'rb').read()
    record, i = bytearray(), 0
    while i + 7 <= len(data):
        length, kind = struct.unpack('<H', data[i + 4:i + 6])[0], data[i + 6]
        if length == 0 and kind == 0:
            i = (i // 32768 + 1) * 32768
            continue
        record += data[i + 7:i + 7 + length]
        i += 7 + length
        if kind in (1, 4):
            batch, record = bytes(record), bytearray()
            seq, count, k = int.from_bytes(batch[:8], 'little'), struct.unpack('<I', batch[8:12])[0], 12
            for n in range(count):
                is_put = batch[k] == 1
                size, k = varint(batch, k + 1)
                key, k = batch[k:k + size], k + size
                value = b''
                if is_put:
                    size, k = varint(batch, k)
                    value, k = batch[k:k + size], k + size
                if UNREAD_KEY in key:
                    yield seq + n, value
        if 32768 - (i % 32768) < 7:
            i = (i // 32768 + 1) * 32768

# The sessions the sidebar marks unread, and those marked unread by hand. None
# when the store cannot be read, as after the app changed how it keeps it.
def unread(store):
    try:
        found = [w for p in glob.glob(os.path.join(store, '*.log')) for w in log(p)]
        found = found or [w for p in glob.glob(os.path.join(store, '*.ldb')) for w in table(p)]
        value = max(found)[1] if found else b''
        if not value:
            return []
        text = value[1:].decode('utf-16-le') if value[:1] == b'\x00' else value[1:].decode('latin-1')
        state = json.loads(text)['state']
        return [i for i in state.get('unreadIds', []) + state.get('explicitUnreadIds', []) if isinstance(i, str)]
    except Exception:
        return None

# As the app reads a finished turn for its sidebar: the turn's own wrap-up,
# when it made one, says whether the person is needed; else the app's summary.
def verdict(state):
    summary = state.get('postTurnSummary') or {}
    category = summary.get('status_category')
    wrap_up = state.get('turnWrapUp')
    if category == 'failed':
        return 'failed'
    if wrap_up is not None:
        return 'needs-input' if wrap_up.get('needsUser') is not None else 'other'
    return 'needs-input' if category in NEEDS_INPUT else 'other'

def transcript(session_id):
    found = glob.glob(os.path.join(PROJECTS, '*', session_id + '.jsonl'))
    return found[0] if found else None

def tail(path):
    with open(path, 'rb') as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - TAIL_BYTES))
        lines = f.read().split(b'\n')
    return lines[1:] if size > TAIL_BYTES else lines

def entry(raw):
    try:
        found = json.loads(raw)
    except ValueError:
        return None
    return None if found.get('isSidechain') else found

def blocks(found):
    content = (found.get('message') or {}).get('content')
    return [c for c in content if isinstance(c, dict)] if isinstance(content, list) else []

# A user entry that started a turn: not the engine's own note, not tool
# results alone, not a command run in the app.
def is_prompt(found):
    content = (found.get('message') or {}).get('content')
    others = [c.get('text', '') for c in blocks(found) if c.get('type') != 'tool_result']
    texts = [content] if isinstance(content, str) else others
    return not found.get('isMeta') and any(not t.lstrip().startswith(LOCAL_COMMANDS) for t in texts)

def is_question(c):
    return c.get('type') == 'tool_use' and c.get('name') == 'AskUserQuestion'

# An AskUserQuestion call with no result yet, before any prompt since.
def is_asking(path):
    answered = set()
    for raw in reversed(tail(path)):
        found = entry(raw) if b'"type":"user"' in raw or b'"type":"assistant"' in raw else None
        if found is None:
            continue
        if found.get('type') == 'user':
            if is_prompt(found):
                return False
            answered.update(c.get('tool_use_id') for c in blocks(found) if c.get('type') == 'tool_result')
        elif any(is_question(c) and c.get('id') not in answered for c in blocks(found)):
            return True
    return False

def session(asked):
    state = json.load(open(asked['file'], encoding='utf-8'))
    path = transcript(state.get('cliSessionId') or '') if asked['readsQuestion'] else None
    return {'verdict': verdict(state), 'isAsking': path is not None and is_asking(path)}

def sessions(request):
    out = {'sessions': {}}
    if 'store' in request:
        out['unread'] = unread(request['store'])
    for asked in request['sessions']:
        try:
            out['sessions'][asked['id']] = session(asked)
        except (OSError, ValueError):
            continue
    return out

def ms(stamp):
    return int(datetime.datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp() * 1000)

# This session's newest reply, and how long the newest cache it wrote lives: a
# reply served from the cache alone wrote none.
def cache(session_id):
    path = transcript(session_id)
    out = {}
    for raw in reversed(tail(path) if path else []):
        found = entry(raw) if b'"type":"assistant"' in raw and b'"usage"' in raw else None
        message = (found or {}).get('message') or {}
        if not message.get('usage') or message.get('model') == '<synthetic>':
            continue
        out.setdefault('at', ms(found['timestamp']))
        written = message['usage'].get('cache_creation') or {}
        if written.get('ephemeral_1h_input_tokens') or written.get('ephemeral_5m_input_tokens'):
            out['ttl'] = '1h' if written.get('ephemeral_1h_input_tokens') else '5m'
            break
    return out

if sys.argv[1] == 'cache':
    print(json.dumps(cache(sys.argv[2])))
else:
    print(json.dumps(sessions(json.loads(sys.argv[2]))))
`

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

// The sandbox's own clock may run in UTC, so local time is shifted by the
// offset `date +%z` reported, then read with the UTC getters.
const local = (ms: number, offset: number) => {
  const d = new Date(ms + offset * 60_000)

  return {
    month: d.getUTCMonth() + 1,
    day: d.getUTCDate(),
    weekday: WEEKDAYS[d.getUTCDay()],
    hm: `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`,
    dayKey: Math.floor((ms + offset * 60_000) / 86_400_000),
  }
}

const resetAt = (iso: string | undefined, now: number, offset: number) => {
  const at = iso ? Date.parse(iso) : NaN
  if (Number.isNaN(at)) return ''
  const when = local(at, offset)

  return when.dayKey === local(now, offset).dayKey ? when.hm : `${when.weekday} ${when.hm}`
}

const timeLeft = (iso: string | undefined, now: number) => {
  const at = iso ? Date.parse(iso) : NaN
  if (Number.isNaN(at) || at <= now) return ''
  const minutes = Math.round((at - now) / 60_000)
  const days = Math.floor(minutes / 1440)
  const hours = Math.floor((minutes % 1440) / 60)

  return days > 0 ? `${days}d ${hours}h left` : `${hours}h ${minutes % 60}m left`
}

const tokens = (n: number | undefined) => {
  if (n === undefined) return '–'
  if (n < 1_000) return String(n)
  if (n < 100_000) return `${(n / 1_000).toFixed(1).replace(/\.0$/, '')}K`
  if (n < 1_000_000) return `${Math.round(n / 1_000)}K`
  if (n < 100_000_000) return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`

  return `${Math.round(n / 1_000_000)}M`
}

const duration = (ms: number | undefined) => {
  if (ms === undefined) return ''
  const s = Math.round(ms / 1000)

  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`
}

const LIMIT_NAME: Record<string, string> = { five_hour: '5h', seven_day: 'Week' }

// Fixed hues read on light and dark alike; text keeps the theme's own colors.
const ORANGE = '#D97757'
const AMBER = '#D99A00'
const RED = '#E5484D'
const TRACK = 'rgba(128,128,128,0.28)'
// A reading too old to trust is drawn grey, its time said beside it.
const DIM_HEX = '#9C9A92'

const clamp = (p: number) => Math.max(0, Math.min(100, p))

const WINDOW_MS: Record<string, number> = { five_hour: 5 * 3600_000, seven_day: 7 * 86_400_000 }

// Colors by pace, as the Claude Usage app does: what share of the window the
// current rate would use by the reset. Under 85% is fine, 85-100% is close, and
// past 100% the window fills before it resets, at `fullAt`.
const pace = (l: { kind: string; percentUsed: number; resetsAt?: string }, now: number) => {
  const span = WINDOW_MS[l.kind]
  const end = l.resetsAt ? Date.parse(l.resetsAt) : NaN
  if (!span || Number.isNaN(end)) return undefined
  const elapsed = Math.min(1, Math.max(0, 1 - (end - now) / span))
  // Too early in the window a pace is mostly noise: go by the amount alone.
  if (elapsed < 0.15) {
    const level: Level = l.percentUsed >= 90 ? 'high' : l.percentUsed >= 75 ? 'warn' : 'ok'
    return { elapsed, projected: l.percentUsed, level, fullAt: undefined }
  }
  const projected = l.percentUsed / elapsed
  const perMs = l.percentUsed / Math.max(elapsed * span, 1)
  const fullAt = projected > 100 && perMs > 0 ? now + (100 - l.percentUsed) / perMs : undefined
  const level: Level = l.percentUsed >= 100 || projected > 100 ? 'high' : projected >= 85 ? 'warn' : 'ok'

  return { elapsed, projected, level, fullAt: fullAt !== undefined && fullAt < end ? fullAt : undefined }
}

// The context has no reset to pace against: its size is what every step of a
// reply reads again, so it is colored by how full it is.
const contextLevel = (p: number): Level => (p >= 80 ? 'high' : p >= 50 ? 'warn' : 'ok')

const LEVEL_KEY: Record<Level, string> = { ok: 'success', warn: 'warning', high: 'error' }
const LEVEL_HEX: Record<Level, string> = { ok: '#5FAF5F', warn: AMBER, high: RED }

// A reply's weight in API price ratios: uncached input 1, cache writes 1.25,
// cache reads 0.1, output 5. The same scale the 7-day history ranks by.
const weightOf = (u: { input_tokens: number; cache_creation_input_tokens?: number; cache_read_input_tokens?: number; output_tokens: number }) =>
  u.input_tokens + (u.cache_creation_input_tokens ?? 0) * 1.25 + (u.cache_read_input_tokens ?? 0) * 0.1 + u.output_tokens * 5

// Heavy when the 5h window rose 2 points (warn) or 4 (high) in one reply, or
// the reply weighed 3x (warn) or 6x (high) the average of the ten before it.
// The 5h rise also counts other sessions running at the same time.
const turnLevel = (t: Turn, before: Turn[]): { level: Level; delta: number; ratio: number } => {
  const delta = t.endPct !== undefined && t.startPct !== undefined ? t.endPct - t.startPct : 0
  const base = before.filter(b => (b.weight ?? 0) > 0).slice(-10)
  const avg = base.length >= 3 ? base.reduce((sum, b) => sum + (b.weight ?? 0), 0) / base.length : 0
  const ratio = avg > 0 ? (t.weight ?? 0) / avg : 0
  const level: Level = delta >= RISE_HIGH || ratio >= 6 ? 'high' : delta >= RISE_WARN || ratio >= 3 ? 'warn' : 'ok'

  return { level, delta, ratio }
}

// A Codex run is heavy by the same measure as a reply: how far its tighter
// window rose while it ran, or has risen so far.
function codexRunLevel(run: CodexTotals): Level {
  const rise = run.rise ?? 0

  if (rise >= RISE_HIGH) {
    return 'high'
  } else if (rise >= RISE_WARN) {
    return 'warn'
  } else {
    return 'ok'
  }
}

// The newest reading wins: while a run works, its record is newer than the
// last server read.
function newestCodexLimits(before: Codex, reading: CodexReading | undefined): Codex {
  const kept: CodexReading | undefined =
    before.limitsAt === undefined ? undefined : { at: before.limitsAt, plan: before.plan, limits: before.limits }
  const readings = [reading, kept].filter((r): r is CodexReading => r !== undefined)
  const newest = readings.sort((a, b) => b.at - a.at)[0]

  return { ...before, limits: newest?.limits ?? [], limitsAt: newest?.at, plan: newest?.plan }
}

// A read of the run records says which runs work now.
function mergeCodexLocal(before: Codex, output: CodexLocalOutput): Codex {
  return {
    ...newestCodexLimits(before, output.snapshot),
    last: output.last ?? before.last,
    working: output.working,
    running: output.running,
  }
}

// A failed server read keeps the error until the next one. The configured
// setting comes with a server read only, and keeps the time it was read.
function mergeCodexServer(before: Codex, shared: Shared<CodexServerOutput>): Codex {
  return {
    ...newestCodexLimits(before, shared.output.limits),
    serverAt: shared.at,
    configured: shared.output.configured ?? before.configured,
    configuredAt: shared.output.configured ? shared.at : before.configuredAt,
    error: shared.output.error,
  }
}

// A value read longer ago than the stale mark is drawn grey. It is judged as
// it is drawn, so failed reads that leave the value as it was grey it too.
function isReadingOld(at: number | undefined, now: number): boolean {
  return at !== undefined && now - at >= STALE_MS
}

// A reload of the mod starts every open session again at once: a count or a
// server read made a moment ago is not made again, and waits for its timer.
function isCountDue(counted: History | null, now: number): boolean {
  const countedAt = counted ? Date.parse(counted.generatedAt) : NaN

  return Number.isNaN(countedAt) || now - countedAt >= RECOUNT_EVERY_MS
}

function isCodexServerDue(state: Codex, now: number): boolean {
  return now - (state.serverAt ?? 0) >= CODEX_SERVER_EVERY_MS
}

function hasCodex(state: Codex): boolean {
  return (
    state.limits.length > 0 ||
    state.last !== undefined ||
    state.configured !== undefined ||
    state.working.length > 0
  )
}

function settingText(setting: CodexSetting): string {
  return setting.effort ? `${setting.model} ${setting.effort}` : setting.model
}

// The setting a run's record names; none before its first turn starts.
function runSetting(run: CodexTotals): CodexSetting | undefined {
  if (run.model) {
    return { model: run.model, effort: run.effort }
  }
}

// The runs at work beyond the one the band shows, with or without a record.
function otherRunsText(state: Codex): string {
  const others = state.running - 1

  if (others > 0) {
    return ` +${others} ${others === 1 ? 'run' : 'runs'}`
  } else {
    return ''
  }
}

// Run records sit in a folder per local day, and a run started before
// midnight goes on writing in yesterday's.
function codexDayFolder(ms: number, offset: number): string {
  return new Date(ms + offset * 60_000).toISOString().slice(0, 10).split('-').join('/')
}

// When a Codex run record was last written, from file times alone, so it can
// be looked at every tick without a process. 0 when there is none.
async function codexRecordAt($: EngineInterface, home: string, now: number, offset: number): Promise<number> {
  try {
    let newest = 0

    for (const ms of [now, now - DAY_MS]) {
      const folder = `${home}/${CODEX_SESSIONS}/${codexDayFolder(ms, offset)}`

      if (await $.fs.exists(folder)) {
        const entries = await $.fs.list(folder)

        newest = Math.max(newest, ...entries.map(entry => entry.mtimeMs))
      }
    }
    return newest
  } catch {
    return 0
  }
}

// When Codex's config was last saved, 0 when there is none.
async function codexConfigAt($: EngineInterface, home: string): Promise<number> {
  try {
    const path = `${home}/${CODEX_CONFIG}`

    if (await $.fs.exists(path)) {
      return (await $.fs.stat(path)).mtimeMs
    } else {
      return 0
    }
  } catch {
    return 0
  }
}

async function sharedClaudePath($: EngineInterface): Promise<string> {
  return `${(await $.env.get('HOME')) ?? ''}/${CLAUDE_SHARED}`
}

// The newest Claude reading any session here shared, none while no session has
// shared one yet.
async function readSharedClaude($: EngineInterface): Promise<Reading | undefined> {
  try {
    const path = await sharedClaudePath($)

    if (await $.fs.exists(path)) {
      const shared = JSON.parse(await $.fs.read(path)) as Reading

      if (typeof shared.at === 'number' && Array.isArray(shared.limits)) {
        return shared
      }
    }
  } catch {
    // A file read while another session writes it is none until the next tick.
  }
}

// Sharing is a help to the other sessions: one that fails keeps this
// session's own reading as it is.
async function writeSharedClaude($: EngineInterface, reading: Reading): Promise<void> {
  try {
    await $.fs.write(await sharedClaudePath($), JSON.stringify(reading))
  } catch {
    // The next reply here writes it again.
  }
}

// The reading a session shared for the key, none while there is none yet.
async function readSharedEntry<T>($: EngineInterface, path: string, key: string): Promise<Shared<T> | undefined> {
  try {
    if (await $.fs.exists(path)) {
      const file = JSON.parse(await $.fs.read(path)) as SharedFile<T>

      return file.entries?.[key]
    }
  } catch {
    // A file that cannot be read now is read again on a later tick.
  }
}

// The reading a session made since `request.since`, from the shared file; else
// a new one this session makes. None while another session makes it: a later
// tick takes that one from the file.
async function readShared<N extends SharedName>(
  $: EngineInterface,
  request: SharedRequest<N>,
): Promise<Shared<SharedOutputs[N]> | undefined> {
  const reader = SHARED_READERS[request.name]
  const path = `${(await $.env.get('HOME')) ?? ''}/${SHARED_FOLDER}/${request.name}.json`
  const kept = await readSharedEntry<SharedOutputs[N]>($, path, request.key)

  if (kept && kept.at > request.since) {
    return kept
  } else {
    // The clock reads fractions of a millisecond; the reader takes whole ones.
    const since = String(Math.floor(request.since))
    const limitSeconds = String(Math.floor((reader.timeoutMs - SHARED_MARGIN_MS) / 1000))
    const script = `${SHARED_READER}\n\n${reader.script}`
    const argv = [PYTHON, '-I', '-c', script, path, request.key, since, limitSeconds, ...request.arguments]
    const ran = await $.process.run(argv, { timeoutMs: reader.timeoutMs })

    if (ran.exitCode === 0) {
      return JSON.parse(ran.stdout) as Shared<SharedOutputs[N]>
    } else if (ran.exitCode !== SHARED_BUSY) {
      throw new Error(ran.stderr.trim().split('\n').pop() || `exit ${ran.exitCode}`)
    }
  }
}

// The work never runs twice at once. A timer's tick that comes while it runs
// is let pass, so a slow read never piles up behind its own timer. A refresh
// an event asks for meanwhile runs it once more after, so it is not lost.
function runOneAtATime(run: () => Promise<void>): OneAtATime {
  let isRunning = false
  let isRefreshAsked = false

  const runNow = async (): Promise<void> => {
    isRunning = true
    try {
      do {
        isRefreshAsked = false
        await run()
      } while (isRefreshAsked)
    } finally {
      isRunning = false
    }
  }

  return {
    tick: async (): Promise<void> => {
      if (!isRunning) {
        await runNow()
      }
    },
    refresh: async (): Promise<void> => {
      if (isRunning) {
        isRefreshAsked = true
      } else {
        await runNow()
      }
    },
  }
}

// A failed read of the run records shows on the Codex line until a read
// succeeds. Noting it is a help: one that fails leaves the line as it was.
async function recordCodexReadFailure($: EngineInterface): Promise<void> {
  try {
    await update($, codex, before => ({ ...before, error: CODEX_READ_FAILED }))
  } catch {
    // The next read notes it again.
  }
}

// A failed server read shows as one that failed, and the server is asked
// again once its next reading is due.
async function recordCodexServerFailure($: EngineInterface): Promise<void> {
  try {
    const now = await $.clock.now()

    await update($, codex, before => ({ ...before, error: CODEX_READ_FAILED, serverAt: now }))
  } catch {
    // The next read notes it again.
  }
}

// The sessions the app lists, less this one: by default the sidebar's own
// list, which leaves archived ones out.
async function listSessions($: EngineInterface, filter: SessionListFilter = {}): Promise<ListedSession[]> {
  const listed = await $.mcp.call(SESSION_LIST.server, SESSION_LIST.tool, { limit: SESSION_LIST.limit, ...filter })
  const text = listed.content.map(block => block.text ?? '').join('')
  const rows = listed.isError ? [] : (JSON.parse(text) as ListedSession[])

  return Array.isArray(rows) ? rows.filter(row => typeof row.sessionId === 'string') : []
}

// A session that started another one is a main session: its model and effort
// cap every session it starts, so it is never told to go lower. Archived ones
// count, since starting one is what made it main. Unknown is not main. The
// app's own id for this session is what the rows it started name as `startedBy`.
async function isMainSession($: EngineInterface): Promise<boolean> {
  try {
    const own = await $.env.get('CLAUDE_CODE_HOST_SESSION_ID')
    const family = own ? await listSessions($, { linked: true, include_archived: true }) : []

    return family.some(row => row.startedBy === own)
  } catch {
    return false
  }
}

// Each session's state file by session id: the app keeps one per session, in
// a folder per account and organization.
async function sessionFiles($: EngineInterface, home: string): Promise<Map<string, SavedFile>> {
  const root = `${home}/${APP_SESSIONS}`
  const found = new Map<string, SavedFile>()

  for (const account of await $.fs.list(root)) {
    for (const organization of await $.fs.list(`${root}/${account.name}`)) {
      const folder = `${root}/${account.name}/${organization.name}`
      const files = organization.kind === 'dir' ? await $.fs.list(folder) : []

      for (const file of files.filter(entry => entry.name.endsWith('.json'))) {
        found.set(file.name.slice(0, -'.json'.length), { file: `${folder}/${file.name}`, savedAt: file.mtimeMs })
      }
    }
  }
  return found
}

// The store's files by name, size and time: any write the app makes changes it.
async function storeStamp($: EngineInterface, home: string): Promise<string> {
  const files = await $.fs.list(`${home}/${APP_STORE}`)

  return files
    .filter(file => STORE_FILE.test(file.name))
    .map(file => `${file.name}:${file.size}:${file.mtimeMs}`)
    .sort()
    .join('|')
}

// A finished session's state file is read again once the app saved it: one at
// work saves it all the time, and its verdict counts only once it stops. A
// session at work is looked at for an open question once it stood still for a
// tick, as it does while the question waits, and once for each such stretch.
function sessionsRequest(
  listed: readonly ListedSession[],
  files: ReadonlyMap<string, SavedFile>,
  before: ReadonlyMap<string, ListedSession>,
  seen: ReadonlyMap<string, SessionSeen>,
  store: string | undefined,
): SessionsRequest {
  const asked = listed.flatMap(session => {
    const saved = files.get(session.sessionId)
    const last = seen.get(session.sessionId)
    const isStill = session.isRunning && before.get(session.sessionId)?.lastActivityAt === session.lastActivityAt
    const readsQuestion = IS_WAITING_COUNTED && isStill && last?.askedAt !== session.lastActivityAt
    const isSaved = !session.isRunning && saved !== undefined && saved.savedAt !== last?.savedAt

    return saved && (isSaved || readsQuestion)
      ? [{ id: session.sessionId, ...saved, activityAt: session.lastActivityAt, readsQuestion }]
      : []
  })

  return store === undefined ? { sessions: asked } : { store, sessions: asked }
}

async function readSessions($: EngineInterface, request: SessionsRequest): Promise<SessionsOutput | undefined> {
  const argv = [PYTHON, '-I', '-c', SESSIONS_READER, READER_MODE.sessions, JSON.stringify(request)]
  const ran = await $.process.run(argv, { timeoutMs: SESSIONS_READ_MS })

  return ran.exitCode === 0 ? (JSON.parse(ran.stdout) as SessionsOutput) : undefined
}

// The sidebar's order: a question or a finished turn that needs the person,
// then a turn at work, then a finished turn not looked at yet. A failed turn
// is the sidebar's error mark, none of these.
function sessionState(session: ListedSession, seen: SessionSeen | undefined, isUnread: boolean): SessionState {
  const isAsking = session.isRunning && seen?.isAsking === true
  const needsInput = !session.isRunning && isUnread && seen?.verdict === 'needs-input'

  if (isAsking || needsInput) {
    return IS_WAITING_COUNTED ? 'waiting' : 'idle'
  } else if (session.isRunning) {
    return 'working'
  } else if (isUnread && seen?.verdict !== 'failed') {
    return 'done'
  } else {
    return 'idle'
  }
}

// Without the store no session counts as unread: nothing is done, and a turn
// that needs the person waits only while its question is open.
function countSessions(
  listed: readonly ListedSession[],
  seen: ReadonlyMap<string, SessionSeen>,
  unread: readonly string[] | null,
): SessionCounts {
  const unreadIds = new Set(unread ?? [])
  const states = listed.map(session =>
    sessionState(session, seen.get(session.sessionId), unreadIds.has(session.sessionId)),
  )
  const total = (state: SessionState) => states.filter(s => s === state).length

  return { working: total('working'), waiting: total('waiting'), done: total('done') }
}

// Keeps what was read of each session asked about; one not asked about its
// question keeps the answer it had.
function recordSeen(seen: Map<string, SessionSeen>, request: SessionsRequest, output: SessionsOutput): void {
  for (const asked of request.sessions) {
    const read = output.sessions[asked.id]
    const last = seen.get(asked.id)

    if (read) {
      seen.set(asked.id, {
        verdict: read.verdict,
        isAsking: asked.readsQuestion ? read.isAsking : (last?.isAsking ?? false),
        savedAt: asked.savedAt,
        askedAt: asked.readsQuestion ? asked.activityAt : last?.askedAt,
      })
    }
  }
}

function isSameCounts(before: SessionCounts | null, after: SessionCounts): boolean {
  return (
    before !== null &&
    before.working === after.working &&
    before.waiting === after.waiting &&
    before.done === after.done
  )
}

// Counts the other sessions on each tick. What it read last stays with it: a
// file is read again, and the store's unread list too, only once it changed.
function sessionCounter($: EngineInterface, home: string): () => Promise<void> {
  const seen = new Map<string, SessionSeen>()
  let before = new Map<string, ListedSession>()
  let storeAt = ''
  let unread: string[] | null = null

  return async () => {
    try {
      const listed = await listSessions($)
      const stamp = await storeStamp($, home)
      const store = stamp === storeAt ? undefined : `${home}/${APP_STORE}`
      const request = sessionsRequest(listed, await sessionFiles($, home), before, seen, store)
      const isAsked = request.store !== undefined || request.sessions.length > 0
      const output = isAsked ? await readSessions($, request) : undefined

      if (output) {
        storeAt = request.store === undefined ? storeAt : stamp
        unread = output.unread === undefined ? unread : output.unread
        recordSeen(seen, request, output)
      }
      before = new Map(listed.map(session => [session.sessionId, session]))
      const counts = countSessions(listed, seen, unread)

      if (!isSameCounts(await read($, sessions), counts)) {
        await update($, sessions, () => counts)
      }
    } catch {
      // The next tick counts again.
    }
  }
}

// Warm until the cache's life past the last request; in its last ten minutes
// the minutes left; once cold, what the next request writes again, or that it
// goes on from the summary the engine made while the session sat idle.
function cacheView(
  state: Cache,
  contextTokens: number | undefined,
  now: number,
  offset: number,
): CacheView | undefined {
  if (state.requestAt !== undefined) {
    const expiresAt = state.requestAt + state.ttlMs
    const left = expiresAt - now
    const isSummarized = (state.summaryAt ?? 0) > state.requestAt
    const rewritten = `${tokens(contextTokens ?? 0)} 다시 씀`

    if (left > CACHE_SOON_MS) {
      return { kind: 'warm', until: local(expiresAt, offset).hm }
    } else if (left > 0) {
      return { kind: 'soon', minutes: Math.ceil(left / 60_000) }
    } else {
      return { kind: 'cold', after: isSummarized ? '요약으로 이어감' : rewritten }
    }
  }
}

// From a tick before the last ten minutes until a tick past the end.
function isCacheEnding(state: Cache, now: number): boolean {
  const expiresAt = state.requestAt === undefined ? undefined : state.requestAt + state.ttlMs

  return expiresAt !== undefined && now >= expiresAt - CACHE_SOON_MS - CACHE_TICK_MS && now <= expiresAt + CACHE_TICK_MS
}

// The cache line is a help: a compaction stands whether or not it is noted.
async function recordCompaction($: EngineInterface, isIdleSummary: boolean): Promise<void> {
  try {
    const now = await $.clock.now()

    await update($, cache, state => (isIdleSummary ? { ...state, summaryAt: now } : { ttlMs: state.ttlMs }))
  } catch {
    // The next request sets the cache line again.
  }
}

async function readCache($: EngineInterface): Promise<CacheOutput | undefined> {
  const argv = [PYTHON, '-I', '-c', SESSIONS_READER, READER_MODE.cache, await $.session.id()]
  const ran = await $.process.run(argv, { timeoutMs: SESSIONS_READ_MS })

  return ran.exitCode === 0 ? (JSON.parse(ran.stdout) as CacheOutput) : undefined
}

// What to do about a heavy reply, first match wins: `cause / action` sits
// beside the branch, in words the app itself uses; `detail`, in the Details
// pane, says why and how.
const explain = (t: Turn, contextTokens: number, contextPercent: number) => {
  const steps = t.steps ?? 0
  if ((t.biggestResult ?? 0) >= 50_000)
    return {
      cause: '큰 파일',
      action: '일부만 읽게 요청',
      detail: `도구가 돌려준 결과 하나가 약 ${tokens(t.biggestResult)} 토큰이었어요. 파일 전체 대신 “○○ 부분만 읽어줘”처럼 범위를 말하면 줄어요.`,
    }
  if (contextPercent >= 40 && steps >= 3)
    return {
      cause: '긴 대화',
      action: '새 세션 추천',
      detail: `대화가 ${tokens(contextTokens)} 토큰까지 길어졌어요. Claude는 한 요청 안에서 생각하고 도구를 쓸 때마다 대화 전체를 다시 읽는데, 이번엔 ${steps}번 읽어서 합계 ${tokens(t.read)}가 됐어요. 왼쪽 목록에서 새 세션을 열어 이어가면 줄어요. 같은 작업을 이어야 하면 입력창에 /compact를 입력하세요(지금까지 대화를 요약해 길이를 줄여요).`,
    }
  if (steps >= 10)
    return {
      cause: `${steps}단계`,
      action: '요청 나누기',
      detail: `한 요청 안에서 생각하고 도구를 쓰는 과정을 ${steps}번 반복했어요. 단계마다 대화를 다시 읽으니, 큰 요청을 두세 번으로 나눠 보내면 줄어요.`,
    }
  if ((t.cacheWrite ?? 0) >= 200_000)
    return {
      cause: '쉬고 다시 읽음',
      action: '정상이에요',
      detail: `저장해 둔 대화(캐시)는 1시간 동안만 유지돼요. 그 뒤에 이어서 물으면 대화를 처음부터 다시 읽어 평소보다 많이 써요(이번에 ${tokens(t.cacheWrite)}). 따로 할 일은 없어요.`,
    }
  if ((t.helpersSpawned ?? 0) > 0)
    return {
      cause: `보조 작업 ${t.helpersSpawned}개`,
      action: '직접 처리 요청',
      detail: `이 요청에서 서브 에이전트가 ${t.helpersSpawned}개 떠서 따로 사용량을 썼어요. 간단한 일이면 “에이전트 없이 해줘”라고 하면 줄어요.`,
    }
  if ((t.write ?? 0) >= 20_000)
    return {
      cause: '긴 답변',
      action: '짧게 요청',
      detail: `답변이 약 ${tokens(t.write)} 토큰이었어요. “요약해서”, “바뀐 부분만”처럼 요청하면 줄어요.`,
    }

  return { cause: '평소보다 많이 씀', action: 'Details 확인', detail: '뚜렷한 원인 하나를 찾지 못했어요. 아래 수치를 참고하세요.' }
}

// Waste that is not one heavy reply but a habit: thinking long on simple
// requests three replies in a row, which names the one step down to take. It
// stops at high, the floor the work-type defaults keep for judgment work,
// since a mod cannot tell judgment work from mechanical work; mechanical work
// already starts on the smaller model.
const EFFORT_DOWN: Record<string, string> = { max: 'xhigh', xhigh: 'high' }

// Output tokens a reply spent thinking: all it wrote, less its visible answer
// and its tool inputs, at roughly 3 characters a token.
const thinkingOf = (t: Turn) => Math.max(0, (t.write ?? 0) - ((t.answerChars ?? 0) + (t.toolInputChars ?? 0)) / 3)

const isOverthought = (t: Turn) =>
  t.effort !== undefined &&
  EFFORT_DOWN[t.effort] !== undefined &&
  (t.steps ?? 0) <= 3 &&
  thinkingOf(t) >= 3_000 &&
  thinkingOf(t) / Math.max(t.write ?? 1, 1) >= 0.7

const ringSvg = (percent: number, size: number, color: string, elapsed?: number) => {
  const stroke = Math.max(2, Math.round(size / 7))
  const mid = size / 2
  const r = (size - stroke) / 2
  const around = 2 * Math.PI * r
  const lit = (clamp(percent) / 100) * around
  const arc =
    lit < 0.5
      ? ''
      : `<circle cx="${mid}" cy="${mid}" r="${r}" fill="none" stroke="${color}" stroke-width="${stroke}" stroke-linecap="round" stroke-dasharray="${lit.toFixed(2)} ${around.toFixed(2)}" transform="rotate(-90 ${mid} ${mid})"/>`

  // A dot on the ring where the window's time now stands.
  const angle = elapsed === undefined ? 0 : elapsed * 2 * Math.PI - Math.PI / 2
  const dot =
    elapsed === undefined
      ? ''
      : `<circle cx="${(mid + r * Math.cos(angle)).toFixed(2)}" cy="${(mid + r * Math.sin(angle)).toFixed(2)}" r="${Math.max(1.4, stroke * 0.8)}" fill="#8A877F"/>`

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}"><circle cx="${mid}" cy="${mid}" r="${r}" fill="none" stroke="${TRACK}" stroke-width="${stroke}"/>${arc}${dot}</svg>`
}

const barSvg = (percent: number, width: number, height: number, color: string) => {
  const lit = (clamp(percent) / 100) * width
  const fill = lit < 0.5 ? '' : `<rect width="${Math.max(height, lit).toFixed(1)}" height="${height}" rx="${height / 2}" fill="${color}"/>`

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><rect width="${width}" height="${height}" rx="${height / 2}" fill="${TRACK}"/>${fill}</svg>`
}

// The terminal draws no Svg: a row of cells stands in for a ring or a bar.
const cells = (percent: number, width: number) => {
  const lit = Math.round((clamp(percent) / 100) * width)

  return '▰'.repeat(lit) + '▱'.repeat(width - lit)
}

const toLive = (u: {
  rateLimits: readonly { kind: string; percentUsed: number; resetsAt?: string }[]
  context: { tokens?: number; window: number; percent?: number }
  cost?: { usd: number }
}): Live => ({
  limits: u.rateLimits.map(r => ({ kind: r.kind, percentUsed: r.percentUsed, resetsAt: r.resetsAt })),
  contextTokens: u.context.tokens,
  contextWindow: u.context.window,
  contextPercent: u.context.percent,
  costUsd: u.cost?.usd,
})

async function locate($: EngineInterface): Promise<Where> {
  const cwd = await $.session.cwd()
  const home = (await $.env.get('HOME')) ?? ''
  const inHome = home !== '' && (cwd === home || cwd.startsWith(`${home}/`))
  const root = inHome ? home : ''
  const names = (inHome ? cwd.slice(home.length) : cwd).split('/').filter(Boolean)
  const isAnchor = async (dir: string) => {
    for (const name of ANCHORS) if (await $.fs.exists(`${dir}/${name}`).catch(() => false)) return true
    return false
  }

  const head: Segment = { text: inHome ? '~' : '', isAnchor: true }
  let segments: Segment[] = [head, ...names.map((text, i) => ({ text, isAnchor: i === names.length - 1 }))]
  const length = segments.map(s => s.text).join('/').length
  if (length > MAX_PATH) {
    segments = [head]
    let dir = root
    for (const [i, text] of names.entries()) {
      const parent = dir
      dir = `${dir}/${text}`
      if (i === names.length - 1 || (await isAnchor(dir))) {
        segments.push({ text, isAnchor: true })
        continue
      }
      const siblings = (await $.fs.list(parent || '/').catch(() => []))
        .filter(entry => entry.kind === 'dir' && entry.name !== text)
        .map(entry => entry.name)
      let size = 1
      while (size < text.length && siblings.some(name => name.startsWith(text.slice(0, size)))) size += 1
      segments.push({ text: text.slice(0, size), isAnchor: false })
    }
  }

  const blank = { ahead: 0, behind: 0, staged: 0, unstaged: 0, untracked: 0, conflicted: 0, stashes: 0 }
  const status = await $.process
    .run(['git', '-C', cwd, 'status', '--porcelain=v2', '--branch'], { timeoutMs: 5_000 })
    .catch(() => undefined)
  if (!status || status.exitCode !== 0) return { segments, isGit: false, ...blank }

  const found = { ...blank }
  let branch = ''
  let oid = ''
  for (const line of status.stdout.split('\n')) {
    if (line.startsWith('# branch.head ')) branch = line.slice(14)
    else if (line.startsWith('# branch.oid ')) oid = line.slice(13)
    else if (line.startsWith('# branch.ab ')) {
      const ab = /\+(\d+) -(\d+)/.exec(line)
      if (ab) {
        found.ahead = Number(ab[1])
        found.behind = Number(ab[2])
      }
    } else if (line.startsWith('1 ') || line.startsWith('2 ')) {
      if (line[2] !== '.') found.staged += 1
      if (line[3] !== '.') found.unstaged += 1
    } else if (line.startsWith('u ')) found.conflicted += 1
    else if (line.startsWith('? ')) found.untracked += 1
  }
  const stash = await $.process
    .run(['git', '-C', cwd, 'rev-list', '--walk-reflogs', '--count', 'refs/stash'], { timeoutMs: 5_000 })
    .catch(() => undefined)
  found.stashes = stash?.exitCode === 0 ? Number(stash.stdout.trim()) || 0 : 0

  return { segments, isGit: true, branch: branch === '(detached)' ? `@${oid.slice(0, 8)}` : branch, ...found }
}

// The time a window runs out at this pace: an hourglass, never an emoji, so
// every device draws the same mark.
const HOURGLASS_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 16 16"><path d="M4 2h8M4 14h8M5 2c0 3.5 6 4.5 6 6s-6 2.5-6 6M11 2c0 3.5-6 4.5-6 6s6 2.5 6 6" fill="none" stroke="#E5484D" stroke-width="1.5" stroke-linecap="round"/><path d="M6.5 12.6h3L8 10.4z" fill="#E5484D"/></svg>'

// Turns while a reply is being written, in place of a "Working" label.
const SPINNER_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 16 16"><circle cx="8" cy="8" r="6" fill="none" stroke="rgba(128,128,128,0.35)" stroke-width="2"/><path d="M8 2a6 6 0 0 1 6 6" fill="none" stroke="#D97757" stroke-width="2" stroke-linecap="round"><animateTransform attributeName="transform" type="rotate" from="0 8 8" to="360 8 8" dur="0.9s" repeatCount="indefinite"/></path></svg>'

const AGENT_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 16 16"><circle cx="8" cy="8" r="6" fill="none" stroke="rgba(128,128,128,0.35)" stroke-width="2"/><path d="M8 2a6 6 0 0 1 6 6" fill="none" stroke="#D97757" stroke-width="2" stroke-linecap="round"/></svg>'

const FOLDER_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 16 16"><path d="M1.5 3.5h4.2l1.5 1.6h7.3v7.9h-13z" fill="none" stroke="#8A877F" stroke-width="1.4" stroke-linejoin="round"/></svg>'
const BRANCH_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 16 16"><g fill="none" stroke="#5FAF5F" stroke-width="1.4"><circle cx="4.5" cy="3.5" r="1.7"/><circle cx="4.5" cy="12.5" r="1.7"/><circle cx="11.5" cy="5.5" r="1.7"/><path d="M4.5 5.2v5.6M11.5 7.2c0 2.6-7 1.6-7 3.6"/></g></svg>'
// Two stacked windows for the other sessions, a clock for the cache.
const SESSIONS_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 16 16"><g fill="none" stroke="#8A877F" stroke-width="1.4" stroke-linejoin="round"><rect x="5.5" y="2" width="8.5" height="8" rx="1.3"/><path d="M2.5 5.5v7a1.3 1.3 0 0 0 1.3 1.3h7"/></g></svg>'
const CACHE_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 16 16"><g fill="none" stroke="#8A877F" stroke-width="1.4" stroke-linecap="round"><circle cx="8" cy="8" r="6"/><path d="M8 4.8V8l2.2 1.4"/></g></svg>'

// Each usage line starts with its maker's mark, as the place line starts with
// a folder: a four-point spark for Claude, a prompt for Codex. Neither is a logo.
const CLAUDE_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 16 16"><path d="M8 1.5v13M1.5 8h13M3.4 3.4l9.2 9.2M12.6 3.4l-9.2 9.2" fill="none" stroke="#8A877F" stroke-width="1.4" stroke-linecap="round"/></svg>'
const CODEX_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 16 16"><g fill="none" stroke="#8A877F" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><rect x="1.5" y="2.5" width="13" height="11" rx="1.5"/><path d="M4.5 6l2 2-2 2M8.5 10.5h3"/></g></svg>'

// One toast per window and threshold, the first time a reading crosses it.
async function alertLimits($: EngineInterface, limits: readonly Limit[], maker?: string): Promise<void> {
  const offset = await read($, tzOffset)
  const now = await $.clock.now()
  const done = await read($, alerted)
  const prefix = maker ? `${maker}|` : ''
  const due = limits.flatMap(l =>
    ALERT_AT.filter(at => l.percentUsed >= at)
      .slice(-1)
      .map(at => ({ l, key: `${prefix}${l.kind}|${l.resetsAt ?? ''}|${at}` }))
      .filter(a => !done.includes(a.key)),
  )

  if (due.length > 0) {
    await update($, alerted, list => [...list, ...due.map(a => a.key)].slice(-40))
    for (const { l } of due) {
      const name = maker ? `${maker} ${LIMIT_NAME[l.kind] ?? l.kind}` : (LIMIT_NAME[l.kind] ?? l.kind)

      $.ui.toast(`${name} limit ${Math.round(l.percentUsed)}% · resets ${resetAt(l.resetsAt, now, offset)}`)
    }
  }
}

async function openDetails($: EngineInterface) {
  await $.ui.open({ id: PANE, title: TITLE, closeOnEscape: true })
}

export const register: Register = on => {
  // Set by session.start, which runs again on every reload.
  let recounter: (() => Promise<void>) | undefined
  let relocater: (() => Promise<void>) | undefined
  let rehelp: (() => Promise<void>) | undefined
  let codexReader: ((isServerRead: boolean) => Promise<void>) | undefined
  let cacheReader: (() => Promise<void>) | undefined

  on('session.start', async ($, e, next) => {
    const isReload = await read($, loaded)

    await $.command.register({ name: 'usage-panel', description: '사용량 패널을 엽니다' })

    const zone = await $.process.run(['date', '+%z']).catch(() => undefined)
    const m = /^([+-])(\d{2})(\d{2})/.exec(zone?.stdout.trim() ?? '')
    if (m) await update($, tzOffset, () => (m[1] === '-' ? -1 : 1) * (Number(m[2]) * 60 + Number(m[3])))

    await update($, live, () => toLive({ rateLimits: [], context: { window: 0 } }))
    const usage = await $.session.usage().catch(() => undefined)
    if (usage) await update($, live, () => toLive(usage))

    // Each session kept its own count in the store before the sessions shared
    // one; the shared file holds it now.
    await $.store.delete('history')

    // A count made since `since`, by any session, stands.
    const takeSharedCount = async (since: number): Promise<void> => {
      const request = { name: SHARED_NAME.history, key: SHARED_ANY_FOLDER, since, arguments: [] } as const
      const shared = await readShared($, request)

      if (shared) {
        await update($, history, () => shared.output)
      }
    }
    const recountSince = async (since: number): Promise<void> => {
      try {
        const status = await read($, recount)

        if (!status.isRunning) {
          await update($, recount, () => ({ isRunning: true }))
          await takeSharedCount(since)
          await update($, recount, () => ({ isRunning: false }))
        }
      } catch (err) {
        const error = String((err as Error)?.message ?? err).slice(0, 200)

        await update($, recount, () => ({ isRunning: false, error }))
      }
    }
    const recountIfDue = async (): Promise<void> => {
      try {
        const now = await $.clock.now()

        if (isCountDue(await read($, history), now)) {
          await recountSince(now - RECOUNT_EVERY_MS)
        }
      } catch {
        // The next minute looks again.
      }
    }
    const recountNow = async (): Promise<void> => {
      try {
        await recountSince(await $.clock.now())
      } catch {
        // The button counts again.
      }
    }
    recounter = recountNow
    void recountIfDue()
    $.clock.every(RECOUNT_CHECK_MS, () => void recountIfDue())

    const relocation = runOneAtATime(async (): Promise<void> => {
      const found = await locate($).catch(() => null)

      await update($, where, () => found)
    })
    relocater = relocation.refresh
    await relocation.tick()
    $.clock.every(WHERE_EVERY_MS, () => void relocation.tick())

    // Each agent keeps the time it was first seen at work, for its age.
    const listHelpers = async (): Promise<void> => {
      try {
        const all = await $.agent.list().catch(() => [])
        const now = await $.clock.now()

        await update($, helpers, known =>
          all
            .filter(a => BUSY.includes(a.status))
            .map(a => ({
              id: a.id,
              type: a.type,
              description: a.description,
              status: a.status,
              seenAt: known.find(k => k.id === a.id)?.seenAt ?? now,
            })),
        )
      } catch {
        // The next tick lists them again.
      }
    }
    const helperList = runOneAtATime(listHelpers)
    rehelp = helperList.refresh
    await helperList.tick()
    $.clock.every(HELPERS_EVERY_MS, () => void helperList.tick())

    // Every session takes the newest shared reading of the run records, and
    // the newest server reading made for its own folder. Each returns when the
    // reading it took was made, none when it took none. Until this copy of the
    // mod took a reading of the records, every tick asks for one.
    let hasLocalReading = false
    const readCodexLocal = async (since: number): Promise<number | undefined> => {
      try {
        const request = { name: SHARED_NAME.codexLocal, key: SHARED_ANY_FOLDER, since, arguments: [CODEX_MODE.local] }
        const shared = await readShared($, request)

        if (shared) {
          const merged = mergeCodexLocal(await read($, codex), shared.output)

          hasLocalReading = true
          await update($, codex, () => merged)
          await alertLimits($, merged.limits, CODEX_NAME)
        }
        return shared?.at
      } catch {
        await recordCodexReadFailure($)
      }
    }
    // The folder is read at each call: a session's folder can change.
    const readCodexServer = async (since: number): Promise<void> => {
      try {
        const cwd = await $.session.cwd()
        const request = { name: SHARED_NAME.codexServer, key: cwd, since, arguments: [CODEX_MODE.server, cwd] }
        const shared = await readShared($, request)

        if (shared) {
          const merged = mergeCodexServer(await read($, codex), shared)

          await update($, codex, () => merged)
          await alertLimits($, merged.limits, CODEX_NAME)
        }
      } catch {
        await recordCodexServerFailure($)
      }
    }
    // A Codex run started here: read since now, and ask the server too once
    // a run in the foreground has ended.
    const readCodex = async (isServerRead: boolean): Promise<void> => {
      try {
        const now = await $.clock.now()

        await readCodexLocal(now)
        if (isServerRead) {
          await readCodexServer(now)
        }
      } catch {
        await recordCodexReadFailure($)
      }
    }
    // The records are read every tick while a run works, and when one was
    // written since the last tick, by a run started anywhere. The server is
    // asked when a run ends, when the config is saved, or every few minutes.
    // Each reading is asked for since the moment every session sees alike (the
    // record's write, the reading that showed the end, the config's save), so
    // one session reads and the others take its reading.
    const home = (await $.env.get('HOME')) ?? ''
    let seenRecordAt = await codexRecordAt($, home, await $.clock.now(), await read($, tzOffset))
    let seenConfigAt = await codexConfigAt($, home)
    const tickCodex = async (): Promise<void> => {
      try {
        const before = await read($, codex)
        const now = await $.clock.now()
        const recordAt = await codexRecordAt($, home, now, await read($, tzOffset))
        const configAt = await codexConfigAt($, home)
        const isRecordWritten = recordAt > seenRecordAt
        const isConfigSaved = configAt !== seenConfigAt
        const isLocalDue = before.running > 0 || isRecordWritten || !hasLocalReading

        seenRecordAt = recordAt
        seenConfigAt = configAt
        const localAt = isLocalDue ? await readCodexLocal(Math.max(now - CODEX_TICK_MS, recordAt)) : undefined
        const after = await read($, codex)
        const endedAt = after.running < before.running ? localAt : undefined
        const savedAt = isConfigSaved ? configAt : undefined

        if (endedAt !== undefined || savedAt !== undefined || isCodexServerDue(after, now)) {
          await readCodexServer(Math.max(now - CODEX_SERVER_EVERY_MS, endedAt ?? 0, savedAt ?? 0))
        }
      } catch {
        await recordCodexReadFailure($)
      }
    }
    const codexTicks = runOneAtATime(tickCodex)
    // A session that starts takes any reading made within the timers' own
    // spans, as a tick would.
    const readCodexOnStart = async (): Promise<void> => {
      try {
        const now = await $.clock.now()

        await readCodexLocal(now - CODEX_TICK_MS)
        await readCodexServer(now - CODEX_SERVER_EVERY_MS)
      } catch {
        await recordCodexReadFailure($)
      }
    }
    codexReader = readCodex
    if (isCodexServerDue(await read($, codex), await $.clock.now())) {
      void readCodexOnStart()
    }
    $.clock.every(CODEX_TICK_MS, () => void codexTicks.tick())

    // Another session's reading wins once it heard a reply since this one last
    // did. It is weighed inside the write, so a reply here meanwhile stands.
    const shareClaude = async (): Promise<void> => {
      try {
        const shared = await readSharedClaude($)

        if (shared) {
          await update($, live, state =>
            shared.at > (state.limitsAt ?? 0) ? { ...state, limits: shared.limits, limitsAt: shared.at } : state,
          )
        }
      } catch {
        // The next tick reads it again.
      }
    }
    const claudeShare = runOneAtATime(shareClaude)
    await claudeShare.tick()
    $.clock.every(CLAUDE_SHARE_EVERY_MS, () => void claudeShare.tick())

    const sessionCount = runOneAtATime(sessionCounter($, home))
    void sessionCount.tick()
    $.clock.every(SESSIONS_EVERY_MS, () => void sessionCount.tick())

    // The transcript says how long the cache lives, and for a session that
    // starts again, when its last request was: what it held is gone.
    const readCacheNow = async (): Promise<void> => {
      try {
        const found = await readCache($)

        if (found) {
          await update($, cache, state => ({
            ...state,
            ttlMs: found.ttl ? CACHE_TTL_MS[found.ttl] : state.ttlMs,
            requestAt: state.requestAt ?? found.at,
          }))
        }
      } catch {
        // The next reply reads it again.
      }
    }
    const tickCache = async (): Promise<void> => {
      try {
        if (isCacheEnding(await read($, cache), await $.clock.now())) {
          await update($, cacheTick, n => n + 1)
        }
      } catch {
        // The next minute draws it.
      }
    }
    const cacheRead = runOneAtATime(readCacheNow)
    cacheReader = cacheRead.refresh
    void cacheRead.refresh()
    $.clock.every(CACHE_TICK_MS, () => void tickCache())

    $.clock.every(1_000, async () => {
      if ((await read($, turns)).some(t => t.isRunning)) await update($, tick, n => n + 1)
    })

    // The details stay closed until asked for: the line above the prompt
    // carries the summary, so the pane takes no room by default. A reload
    // leaves them as the person left them.
    if (!isReload) {
      await $.ui.close({ id: PANE }).catch(() => undefined)
    }
    await update($, loaded, () => true)

    return next(e)
  })

  on('command.run', { command: 'usage-panel' }, async $ => {
    await openDetails($)

    return { text: '사용량 패널을 열었어요.' }
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey) return next(e)
    // Read so the cache's minutes redraw while it nears its end.
    await read($, cacheTick)

    const table = $.ui.resolve(e)
    const { Box, Text, Button } = table
    const Svg = 'Svg' in table ? table.Svg : undefined
    const now = await $.clock.now()
    const offset = await read($, tzOffset)
    const state = await read($, live)
    const list = await read($, turns)
    const last = [...list].reverse().find(t => !t.isRunning)
    const isWorking = list.some(t => t.isRunning)
    const place = await read($, where)
    const busy = await read($, helpers)
    const codexState = await read($, codex)
    const heavy = await read($, note)
    const counts = await read($, sessions)
    const sessionParts = SESSION_PARTS.map(part => ({ ...part, count: counts?.[part.state] ?? 0 })).filter(
      part => part.count > 0,
    )
    const cached = cacheView(await read($, cache), state.contextTokens, now, offset)
    const running = list.find(t => t.isRunning)
    // While a reply runs, its numbers take the color it would get if it
    // ended now: the 5h rise so far and its weight so far.
    const liveLevel: Level = running
      ? turnLevel(
          { ...running, endPct: state.limits.find(l => l.kind === 'five_hour')?.percentUsed, weight: running.liveWeight },
          list.slice(0, list.indexOf(running)),
        ).level
      : 'ok'

    const gauge = (percent: number, alt: string, level: Level, elapsed?: number, isDim = false) =>
      Svg ? (
        <Svg
          source={ringSvg(percent, 14, isDim ? DIM_HEX : LEVEL_HEX[level], elapsed)}
          alt={alt}
          width={14}
          height={14}
        />
      ) : (
        <Text mono color={isDim ? undefined : LEVEL_KEY[level]} dimColor={isDim}>
          {cells(percent, 4)}
        </Text>
      )

    // Every piece of the line is a Box row of its own, so the desktop centers
    // them alike: a bare Text or Button beside these Boxes sat lower. The
    // children are written out in each Box, never passed through a fragment:
    // the desktop drew a fragment as a block and stacked the ring above its text.
    const slot = (key: string, child: unknown) => (
      <Box key={key} flexDirection="row" alignItems="center" alignSelf="center" gap="xs">
        {child}
      </Box>
    )

    const item = (
      key: string,
      percent: number,
      name: string,
      after: string,
      level: Level,
      elapsed?: number,
      warning = '',
      isDim = false,
    ) => (
      <Box key={key} flexDirection="row" alignItems="center" alignSelf="center" gap="xs">
        {gauge(percent, `${name} ${Math.round(percent)}%`, level, elapsed, isDim)}
        {/* No gap inside the reading: the symbols (⧗, ↻, ·) are its separators. */}
        <Box flexDirection="row" alignItems="center">
          <Text size="caption">
            <Text bold>{name}</Text>{' '}
            <Text color={isDim ? undefined : LEVEL_KEY[level]} dimColor={isDim}>
              {Math.round(percent)}%
            </Text>
          </Text>
          {warning && Svg ? <Svg source={HOURGLASS_SVG} alt="runs out at" width={11} height={11} /> : null}
          {warning ? (
            <Text size="caption" color={LEVEL_KEY.high}>
              {Svg ? warning : `⧗${warning}`}
            </Text>
          ) : null}
          {after ? (
            <Text size="caption" dimColor>
              {after}
            </Text>
          ) : null}
        </Box>
      </Box>
    )

    // Git marks as Powerlevel10k draws them, in its order and colors: behind,
    // ahead and stashes green, conflicts red, staged and unstaged yellow,
    // untracked blue. A clean tree shows the branch alone.
    const marks = (w: Where) =>
      [
        { show: w.behind, text: `⇣${w.behind}`, color: 'success' },
        { show: w.ahead, text: `⇡${w.ahead}`, color: 'success' },
        { show: w.stashes, text: `*${w.stashes}`, color: 'success' },
        { show: w.conflicted, text: `~${w.conflicted}`, color: 'error' },
        { show: w.staged, text: `+${w.staged}`, color: 'warning' },
        { show: w.unstaged, text: `!${w.unstaged}`, color: 'warning' },
        { show: w.untracked, text: `?${w.untracked}`, color: 'suggestion' },
      ].filter(m => m.show > 0)

    // The first line says where the session works; the second, closest to the
    // prompt, what it costs, since that one moves with every reply.
    const placeLine = place && (
      <Box key="where" flexDirection="row" flexWrap="wrap" alignItems="center" columnGap="md">
        <Box flexDirection="row" alignItems="center" alignSelf="center" gap="xs">
          {Svg ? <Svg source={FOLDER_SVG} alt="folder" width={12} height={12} /> : null}
          <Text size="caption" dimColor wrap="truncate-middle">
            {place.segments.map((s, i) => (
              <Text bold={s.isAnchor}>
                {i > 0 ? '/' : ''}
                {s.text}
              </Text>
            ))}
          </Text>
        </Box>
        {place.isGit && (
          <Box flexDirection="row" alignItems="center" alignSelf="center" gap="xs">
            {Svg ? <Svg source={BRANCH_SVG} alt="branch" width={12} height={12} /> : null}
            <Text size="caption">
              <Text color="success" bold>
                {place.branch}
              </Text>
              {marks(place).map(m => (
                <Text color={m.color}>{m.text}</Text>
              ))}
            </Text>
          </Box>
        )}
        {busy.length > 0 && (
          <Box flexDirection="row" alignItems="center" alignSelf="center" gap="xs">
            {Svg ? <Svg source={AGENT_SVG} alt="agents" width={12} height={12} /> : null}
            <Text size="caption">
              <Text bold>{busy.length}</Text>
              <Text dimColor> {busy.length === 1 ? 'agent' : 'agents'}</Text>
            </Text>
          </Box>
        )}
        {sessionParts.length > 0 && (
          <Box key="sessions" flexDirection="row" alignItems="center" alignSelf="center" gap="xs">
            {Svg ? <Svg source={SESSIONS_SVG} alt="sessions" width={12} height={12} /> : null}
            <Text size="caption">
              {sessionParts.flatMap((part, i) => [
                i > 0 ? <Text dimColor> · </Text> : null,
                <Text color={part.color} bold={part.isBold} dimColor={part.color === undefined}>
                  {`${part.count} ${part.state}`}
                </Text>,
              ])}
            </Text>
          </Box>
        )}
        {cached && (
          <Box key="cache" flexDirection="row" alignItems="center" alignSelf="center" gap="xs">
            {Svg ? <Svg source={CACHE_SVG} alt="cache" width={12} height={12} /> : null}
            <Text size="caption">
              <Text bold>Cache</Text>
              {cached.kind === 'warm' ? <Text dimColor>{` ·${cached.until}까지`}</Text> : null}
              {cached.kind === 'soon' ? (
                <Text color={LEVEL_KEY.warn} bold>
                  {` ${cached.minutes}분 남음`}
                </Text>
              ) : null}
              {cached.kind === 'cold' ? (
                <Text color={LEVEL_KEY.high} bold>
                  {' 만료'}
                </Text>
              ) : null}
              {cached.kind === 'cold' ? <Text dimColor>{` ·${cached.after}`}</Text> : null}
            </Text>
          </Box>
        )}
        {heavy && (
          <Box flexDirection="row" alignItems="center" alignSelf="center" flexGrow={1} justifyContent="flex-end">
            <Text size="caption" wrap="truncate-end">
              <Text color={LEVEL_KEY[heavy.level]}>▲{heavy.short}</Text>
            </Text>
          </Box>
        )}
      </Box>
    )

    // The line keeps what is worth a glance: the limits, the context and how
    // heavy the last reply was. The cost waits in the details.
    // The terminal draws no Svg: the maker's name stands in for its mark.
    const mark = (key: string, source: string, name: string) =>
      slot(
        key,
        Svg ? (
          <Svg source={source} alt={name} width={12} height={12} />
        ) : (
          <Text size="caption" bold dimColor>
            {name}
          </Text>
        ),
      )

    // Turns beside whatever works now: the reply being written, the Codex run.
    const spinner = () =>
      Svg ? (
        <Svg source={SPINNER_SVG} alt="working" width={11} height={11} isInteractive />
      ) : (
        <Text size="caption" dimColor>
          ◌
        </Text>
      )

    // A reading older than the stale mark is grey and says when it was read, in
    // place of its reset and its pace.
    const limitItems = (prefix: string, limits: readonly Limit[], at: number | undefined) => {
      const isOld = isReadingOld(at, now)
      const asOf = at === undefined ? '' : local(at, offset).hm

      return limits.map(l => {
        const p = isOld ? undefined : pace(l, now)
        const full = p?.fullAt !== undefined ? local(p.fullAt, offset).hm : ''
        const after = isOld ? `·${asOf} 기준` : `↻${resetAt(l.resetsAt, now, offset)}`

        return item(
          `${prefix}${l.kind}`,
          l.percentUsed,
          LIMIT_NAME[l.kind] ?? l.kind,
          after,
          p?.level ?? 'ok',
          p?.elapsed,
          full,
          isOld,
        )
      })
    }

    const codexLast = codexState.last
    const codexLastLevel = codexLast ? codexRunLevel(codexLast) : 'ok'
    // As on the Claude line, a run at work takes the place of the last one: the
    // spinner and its totals so far, then how many other runs work beside it.
    const codexWork = codexState.working[0]
    const codexWorkLevel = codexWork ? codexRunLevel(codexWork) : 'ok'
    // At the end of the line, as the app puts the model under the prompt: the
    // setting of the run at work, or with none at work, the setting the next
    // run takes, greyed with its time once old.
    const workSetting = codexWork ? runSetting(codexWork) : undefined
    const codexSetting = workSetting ?? codexState.configured
    const isCodexSettingOld = workSetting === undefined && isReadingOld(codexState.configuredAt, now)
    const codexSettingAsOf = codexState.configuredAt === undefined ? '' : local(codexState.configuredAt, offset).hm
    const codexLine = hasCodex(codexState) ? (
      <Box key="codex" flexDirection="row" flexWrap="wrap" alignItems="center" columnGap="md">
        {mark('codex-mark', CODEX_SVG, CODEX_NAME)}
        {limitItems('codex-', codexState.limits, codexState.limitsAt)}
        {codexWork
          ? (
            <Box key="codex-last" flexDirection="row" alignItems="center" alignSelf="center" gap="xs">
              {spinner()}
              <Text size="caption">
                <Text dimColor>In </Text>
                <Text color={LEVEL_KEY[codexWorkLevel]}>{tokens(codexWork.read)}</Text>
                <Text dimColor>·Out </Text>
                <Text color={LEVEL_KEY[codexWorkLevel]}>{tokens(codexWork.write)}</Text>
                <Text dimColor>
                  {` ×${codexWork.steps}`}
                  {otherRunsText(codexState)}
                </Text>
              </Text>
            </Box>
          )
          : codexLast
            ? slot(
                'codex-last',
                <Text size="caption">
                  <Text bold>Last</Text>
                  <Text dimColor> In </Text>
                  <Text color={LEVEL_KEY[codexLastLevel]}>{tokens(codexLast.read)}</Text>
                  <Text dimColor>·Out </Text>
                  <Text color={LEVEL_KEY[codexLastLevel]}>{tokens(codexLast.write)}</Text>
                </Text>,
              )
            : null}
        {codexSetting ? (
          <Box key="codex-setting" flexDirection="row" alignItems="center" alignSelf="center" gap="xs">
            <Text size="caption" dimColor={isCodexSettingOld}>
              <Text bold>{codexSetting.model}</Text>
              {codexSetting.effort ? ` ${codexSetting.effort}` : ''}
            </Text>
            {isCodexSettingOld ? (
              <Text size="caption" dimColor>
                {`·${codexSettingAsOf} 기준`}
              </Text>
            ) : null}
          </Box>
        ) : null}
        {slot(
          'codex-details',
          <Button key="codex-details" plain onPress={() => void openDetails($)}>
            <Text size="caption">Details ›</Text>
          </Button>,
        )}
      </Box>
    ) : null

    const usageLine = (
      <Box key="usage" flexDirection="row" flexWrap="wrap" alignItems="center" columnGap="md">
        {mark('claude-mark', CLAUDE_SVG, CLAUDE_NAME)}
        {limitItems('', state.limits, state.limitsAt)}
        {item(
          'context',
          state.contextPercent ?? 0,
          'Context',
          `·${tokens(state.contextTokens ?? 0)}/${tokens(state.contextWindow)}`,
          contextLevel(state.contextPercent ?? 0),
        )}
        {isWorking && running
          ? (
            <Box key="last" flexDirection="row" alignItems="center" alignSelf="center" gap="xs">
              {spinner()}
              <Text size="caption">
                <Text dimColor>In </Text>
                <Text color={LEVEL_KEY[liveLevel]}>{tokens(running.liveRead ?? 0)}</Text>
                <Text dimColor>·Out </Text>
                <Text color={LEVEL_KEY[liveLevel]}>{tokens(running.liveWrite ?? 0)}</Text>
                <Text dimColor> ×{running.steps ?? 0}</Text>
              </Text>
            </Box>
          )
          : last
            ? slot(
                'last',
                <Text size="caption">
                  <Text bold>Last</Text>
                  {/* Only the numbers take the reply's color, as the % of a gauge does. */}
                  <Text dimColor> In </Text>
                  <Text color={LEVEL_KEY[last.level ?? 'ok']}>{tokens(last.read)}</Text>
                  <Text dimColor>·Out </Text>
                  <Text color={LEVEL_KEY[last.level ?? 'ok']}>{tokens(last.write)}</Text>
                </Text>,
              )
            : null}
        {slot(
          'details',
          <Button key="details" plain onPress={() => void openDetails($)}>
            <Text size="caption">Details ›</Text>
          </Button>,
        )}
      </Box>
    )

    return (
      <Box flexDirection="column" rowGap="xs">
        {placeLine}
        {usageLine}
        {codexLine}
      </Box>
    )
  })

  // A measure carries the limits only once a reply here reported them: until
  // then the shared reading stays.
  on('session.measure', async ($, e, next) => {
    const now = await $.clock.now()
    const measured = toLive(e)
    const hasLimits = measured.limits.length > 0

    await update($, live, state =>
      hasLimits ? { ...measured, limitsAt: now } : { ...measured, limits: state.limits, limitsAt: state.limitsAt },
    )
    if (hasLimits) {
      await writeSharedClaude($, { at: now, limits: measured.limits })
    }
    await alertLimits($, e.rateLimits)

    return next(e)
  })

  on('turn.start', async ($, e, next) => {
    if (e.agentId !== undefined) void rehelp?.()
    if (e.agentId === undefined) {
      const now = await $.clock.now()
      const fiveHour = (await read($, live)).limits.find(l => l.kind === 'five_hour')
      const turn: Turn = {
        id: e.turnId,
        prompt: e.text.replace(/\s+/g, ' ').trim().slice(0, 40),
        startedAt: now,
        startPct: fiveHour?.percentUsed,
        isRunning: true,
      }
      await update($, turns, list => [...list, turn].slice(-KEEP_TURNS))
      await update($, note, () => null)
    }

    return next(e)
  })

  // Each step of a main-loop turn adds its usage to the running counts, and
  // its request renews the main conversation's cache from when it was made.
  on('turn.step', async function* ($, e, next) {
    const requestAt = await $.clock.now()
    const step = yield* next(e)
    const u = step?.usage
    if (e.agentId === undefined && u) {
      await update($, cache, state => ({ ...state, requestAt }))
      await update($, turns, list =>
        list.map(t =>
          t.id !== e.turnId
            ? t
            : {
                ...t,
                steps: (t.steps ?? 0) + 1,
                model: t.model ?? e.model,
                effort: t.effort ?? (typeof e.effort === 'string' ? e.effort : undefined),
                toolInputChars:
                  (t.toolInputChars ?? 0) +
                  (step?.toolUses ?? []).reduce((sum, use) => sum + JSON.stringify(use.input ?? '').length, 0),
                liveRead: (t.liveRead ?? 0) + u.input_tokens + (u.cache_creation_input_tokens ?? 0) + (u.cache_read_input_tokens ?? 0),
                liveWrite: (t.liveWrite ?? 0) + u.output_tokens,
                liveWeight: (t.liveWeight ?? 0) + weightOf(u),
              },
        ),
      )
    }

    return step
  })

  // The biggest tool result of the running turn, in rough tokens (3 characters
  // each, between English and Korean).
  on('tool.call', async ($, e, next) => {
    const ran = await next(e)
    const size = Math.round((ran.text?.length ?? 0) / 3)
    if (e.agentId === undefined && size > 0) {
      await update($, turns, list =>
        list.map(t => (t.isRunning ? { ...t, biggestResult: Math.max(t.biggestResult ?? 0, size) } : t)),
      )
    }

    return ran
  })

  // A Codex run started here is read at once: a background one starts the
  // half-minute reads, a finished one asks the server for the new limits.
  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    const ran = await next(e)

    if (e.tool === 'Bash' && CODEX_RUN.test(e.command)) {
      void codexReader?.(e.run_in_background !== true)
    }
    return ran
  })

  // A summary the engine made while the session sat idle is what the next
  // prompt goes on from. Any other compaction replaces the conversation, and
  // the next request caches its new start.
  on('session.compact', async ($, e, next) => {
    const compacted = await next(e)

    if (e.agentId === undefined && compacted.messages !== undefined) {
      await recordCompaction($, e.trigger === 'precompute')
    }
    return compacted
  })

  on('agent.spawn', async ($, e, next) => {
    const spawned = await next(e)
    await update($, turns, list => list.map(t => (t.isRunning ? { ...t, helpersSpawned: (t.helpersSpawned ?? 0) + 1 } : t)))
    void rehelp?.()

    return spawned
  })

  on('turn.complete', async ($, e, next) => {
    if (e.agentId !== undefined) void rehelp?.()
    if (e.agentId === undefined) {
      void relocater?.()
      void cacheReader?.()
      const u = e.usage
      const state = await read($, live)
      const endPct = state.limits.find(l => l.kind === 'five_hour')?.percentUsed
      const now = await $.clock.now()
      let finished: Turn | undefined
      let before: Turn[] = []
      await update($, turns, list =>
        list.map((t, i) => {
          if (t.id !== e.turnId) return t
          finished = {
            ...t,
            isRunning: false,
            durationMs: e.durationMs,
            model: t.model ?? u?.model,
            read: u ? u.input_tokens + (u.cache_creation_input_tokens ?? 0) + (u.cache_read_input_tokens ?? 0) : t.liveRead,
            write: u?.output_tokens ?? t.liveWrite,
            cacheWrite: u?.cache_creation_input_tokens,
            weight: u ? weightOf(u) : undefined,
            answerChars: e.answer.length,
            endPct,
          }
          before = list.slice(0, i)
          finished.level = turnLevel(finished, before).level

          return finished
        }),
      )
      if (finished && finished.level !== 'ok') {
        const { delta, ratio } = turnLevel(finished, before)
        const why = explain(finished, state.contextTokens ?? 0, state.contextPercent ?? 0)
        const head = delta >= 1 ? `5h+${Math.round(delta)}%` : `평소×${Math.round(ratio)}`
        const fiveHour = state.limits.find(l => l.kind === 'five_hour')
        const p = fiveHour ? pace(fiveHour, now) : undefined
        const offset = await read($, tzOffset)
        const insight: Insight = {
          turnId: finished.id,
          at: now,
          level: finished.level ?? 'warn',
          kind: 'heavy',
          short: `${head} ${why.cause} / ${why.action}`,
          detail:
            why.detail +
            (p?.fullAt !== undefined ? ` 이 속도면 ${local(p.fullAt, offset).hm}에 5시간 한도를 다 써요.` : ''),
          metrics: `In ${tokens(finished.read)} · Out ${tokens(finished.write)} · ${finished.steps ?? 0} steps · ${duration(finished.durationMs)}`,
        }
        await update($, note, () => insight)
        await update($, insights, list => [...list, insight].slice(-KEEP_INSIGHTS))
      }
      if (finished && finished.level === 'ok') {
        const recent = [...before, finished].filter(t => !t.isRunning).slice(-3)
        const seen = (await read($, insights)).filter(s => recent.some(t => t.id === s.turnId) || s.at > now - 30 * 60_000)
        const isHabit = recent.length === 3 && recent.every(isOverthought)

        // Once said, a habit waits half an hour before it is said again. The
        // app is asked about the main session only once one is due.
        if (isHabit && !seen.some(s => s.kind === 'effort') && !(await isMainSession($))) {
          const effort = finished.effort ?? ''
          const share = Math.round((recent.reduce((sum, t) => sum + thinkingOf(t), 0) / Math.max(1, recent.reduce((sum, t) => sum + (t.write ?? 0), 0))) * 100)
          const insight: Insight = {
            turnId: finished.id,
            at: now,
            level: 'warn',
            kind: 'effort',
            short: `긴 생각 / 사고 수준 ${EFFORT_DOWN[effort]}로`,
            detail: `최근 간단한 요청 3번에서 쓴 양의 약 ${share}%를 생각하는 데 썼어요. 지금 사고 수준은 ${effort}예요. 입력창 오른쪽 아래에서 사고 수준을 ${EFFORT_DOWN[effort]}로 낮추면 덜 써요. 어려운 작업을 시작할 때 다시 올리면 돼요.`,
            metrics: `Out ${tokens(recent.reduce((sum, t) => sum + (t.write ?? 0), 0))} (3번 합계) · 사고 수준 ${effort}`,
          }
          await update($, note, () => insight)
          await update($, insights, list => [...list, insight].slice(-KEEP_INSIGHTS))
        }
      }
    }

    return next(e)
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const table = $.ui.resolve(e)
    const { Box, Text, Button } = table
    const Svg = 'Svg' in table ? table.Svg : undefined
    const now = await $.clock.now()
    const offset = await read($, tzOffset)
    const state = await read($, live)
    const list = await read($, turns)
    const past = await read($, history)
    const status = await read($, recount)
    const codexState = await read($, codex)
    const isClaudeLimitsOld = isReadingOld(state.limitsAt, now)
    const claudeAsOf = state.limitsAt === undefined ? '' : local(state.limitsAt, offset).hm
    const isCodexLimitsOld = isReadingOld(codexState.limitsAt, now)
    const isCodexConfiguredOld = isReadingOld(codexState.configuredAt, now)
    const codexConfiguredAsOf = codexState.configuredAt === undefined ? '' : local(codexState.configuredAt, offset).hm
    const columns = e.props.bodyColumns ?? 40
    const barWidth = Math.max(60, Math.min(140, columns * 3))

    const fiveHour = state.limits.find(l => l.kind === 'five_hour')
    const shown = [...list].reverse().slice(0, 6)
    const share = (w: number) => (past && past.total > 0 ? (w / past.total) * 100 : 0)

    const heading = (text: string) => (
      <Box marginTop={2}>
        <Text size="caption" bold dimColor>
          {text}
        </Text>
      </Box>
    )

    const bar = (percent: number, alt: string, color = ORANGE) =>
      Svg ? (
        <Svg source={barSvg(percent, barWidth, 6, color)} alt={alt} width={barWidth} height={6} />
      ) : (
        <Text mono dimColor>
          {cells(percent, 10)}
        </Text>
      )

    const meter = (
      key: string,
      percent: number,
      title: string,
      line1: string,
      line2: string,
      level: Level = 'ok',
      elapsed?: number,
    ) => (
      <Box key={key} flexDirection="row" alignItems="center" gap={1}>
        {Svg ? (
          <Svg
            source={ringSvg(percent, 34, LEVEL_HEX[level], elapsed)}
            alt={`${title} ${Math.round(percent)}%`}
            width={34}
            height={34}
          />
        ) : (
          <Text mono color={LEVEL_KEY[level]}>
            {cells(percent, 8)}
          </Text>
        )}
        <Box flexDirection="column">
          <Text size="footnote">
            <Text bold>{title}</Text> <Text color={LEVEL_KEY[level]}>{Math.round(percent)}%</Text>
          </Text>
          {line1 ? (
            <Text size="caption" dimColor>
              {line1}
            </Text>
          ) : null}
          {line2 ? (
            <Text size="caption" dimColor>
              {line2}
            </Text>
          ) : null}
        </Box>
      </Box>
    )

    const ranked = (key: string, title: string, sub: string, weight: number) => (
      <Box key={key} flexDirection="column" marginTop={1}>
        <Box flexDirection="row" justifyContent="space-between" gap={1}>
          <Text size="footnote" wrap="truncate-end">
            {title}
          </Text>
          <Text size="footnote" tabular>
            {Math.round(share(weight))}%
          </Text>
        </Box>
        {bar(share(weight), `${title} ${Math.round(share(weight))}%`)}
        {sub ? (
          <Text size="caption" dimColor wrap="truncate-end">
            {sub}
          </Text>
        ) : null}
      </Box>
    )

    const days = (past?.days ?? []).filter((d, i) => i === 0 || d.replies > 0)
    const counted = past ? local(Date.parse(past.generatedAt), offset).hm : undefined
    const shownTab = await read($, tab)
    const busyNow = await read($, helpers)
    const seen = [...(await read($, insights))].reverse().slice(0, 5)
    await read($, tick)

    const tabButton = (key: Tab, label: string) => (
      <Button
        key={`tab-${key}`}
        variant={shownTab === key ? 'primary' : 'secondary'}
        onPress={() => void update($, tab, () => key)}
      >
        <Text size="caption" bold={shownTab === key}>
          {label}
        </Text>
      </Button>
    )

    const tabs = (
      <Box flexDirection="row" gap={1} marginBottom={1}>
        {tabButton('now', 'Now')}
        {tabButton('week', '7 days')}
      </Box>
    )

    if (shownTab === 'week') {
      return (
        <Box flexDirection="column" paddingX={1}>
          {tabs}
          <Text size="caption" bold dimColor>
            LAST 7 DAYS · THIS MAC
          </Text>
          {past === null ? (
            <Text size="footnote" dimColor>
              {status.isRunning ? 'Counting…' : '아직 센 기록이 없어요.'}
            </Text>
          ) : (
            days.map(d => {
              const date = new Date(`${d.date}T00:00:00Z`)
              const label = `${WEEKDAYS[date.getUTCDay()]} ${date.getUTCMonth() + 1}/${date.getUTCDate()}`

              return ranked(
                d.date,
                label,
                `${d.sessions} sessions · ${d.replies.toLocaleString('en-US')} replies · In ${tokens(d.read)} · Out ${tokens(d.write)}`,
                d.weight,
              )
            })
          )}

          {past && past.sessions.length > 0 && heading('TOP SESSIONS')}
          {past?.sessions.map(s =>
            ranked(s.id, s.title || s.id.slice(0, 8), `${s.project} · In ${tokens(s.read)} · Out ${tokens(s.write)}`, s.weight),
          )}

          {past && past.projects.length > 0 && heading('PROJECTS')}
          {past?.projects.map(p => ranked(p.name, p.name, '', p.weight))}

          <Box marginTop={2}>
            <Text size="caption" dimColor>
              %는 API 요금 비율로 추정한 7일 사용 비중이에요. 한도 계산식은 공개되지 않아 실제와 다를 수 있어요.
            </Text>
          </Box>

          <Box marginTop={1} flexDirection="row" gap={1} alignItems="center">
            <Button key="recount" plain onPress={() => void recounter?.()}>
              <Text size="caption">{status.isRunning ? 'Counting…' : 'Recount ›'}</Text>
            </Button>
            {counted && (
              <Text size="caption" dimColor>
                as of {counted}
              </Text>
            )}
          </Box>
          {status.error && (
            <Text size="caption" color="error">
              Recount failed: {status.error}
            </Text>
          )}
        </Box>
      )
    }

    return (
      <Box flexDirection="column" paddingX={1}>
        {tabs}
        {busyNow.length > 0 && (
          <Box flexDirection="column" marginBottom={2}>
            <Text size="caption" bold dimColor>
              AGENTS · {busyNow.length} RUNNING
            </Text>
            {busyNow.map(a => (
              <Box key={a.id} flexDirection="column" marginTop={1}>
                <Text size="footnote">
                  <Text bold>{a.type}</Text>
                  <Text dimColor>
                    {' '}
                    · {a.status} · {duration(now - a.seenAt)}
                  </Text>
                </Text>
                <Text size="caption" dimColor wrap="truncate-end">
                  {a.description}
                </Text>
              </Box>
            ))}
          </Box>
        )}
        <Text size="caption" bold dimColor>
          LIMITS
        </Text>
        {state.limits.length === 0 ? (
          <Text size="footnote" dimColor>
            첫 답변이 끝나면 5시간·1주일 한도가 보여요.
          </Text>
        ) : (
          <Box flexDirection="row" flexWrap="wrap" columnGap={3} rowGap={1} marginTop={1}>
            {state.limits.map(l => {
              const p = isClaudeLimitsOld ? undefined : pace(l, now)
              const outlook = isClaudeLimitsOld
                ? `${claudeAsOf}에 읽은 값이에요`
                : p === undefined
                  ? timeLeft(l.resetsAt, now)
                  : p.fullAt !== undefined
                    ? `이 속도면 ${local(p.fullAt, offset).hm}에 다 써요`
                    : `이 속도면 초기화 때 ${Math.round(Math.min(p.projected, 999))}%`

              return meter(
                l.kind,
                l.percentUsed,
                LIMIT_NAME[l.kind] ?? l.kind,
                `resets ${resetAt(l.resetsAt, now, offset)} · ${timeLeft(l.resetsAt, now)}`,
                outlook,
                p?.level ?? 'ok',
                p?.elapsed,
              )
            })}
          </Box>
        )}

        {hasCodex(codexState) ? heading('CODEX') : null}
        {codexState.limits.length > 0 ? (
          <Box flexDirection="row" flexWrap="wrap" columnGap={3} rowGap={1} marginTop={1}>
            {codexState.limits.map(l => {
              const p = isCodexLimitsOld ? undefined : pace(l, now)
              const asOf = codexState.limitsAt === undefined ? '' : local(codexState.limitsAt, offset).hm
              const outlook = isCodexLimitsOld
                ? `${asOf}에 읽은 값이에요`
                : p?.fullAt !== undefined
                  ? `이 속도면 ${local(p.fullAt, offset).hm}에 다 써요`
                  : `${asOf}에 읽음`

              return meter(
                `codex-${l.kind}`,
                l.percentUsed,
                LIMIT_NAME[l.kind] ?? l.kind,
                `resets ${resetAt(l.resetsAt, now, offset)} · ${timeLeft(l.resetsAt, now)}`,
                outlook,
                p?.level ?? 'ok',
                p?.elapsed,
              )
            })}
          </Box>
        ) : null}
        {codexState.working.map(work => {
          const setting = runSetting(work)
          const since = `${local(work.startedAt, offset).hm}부터`

          return (
            <Box key={`codex-work-${work.startedAt}`} flexDirection="column" marginTop={1}>
              <Text size="footnote">
                <Text bold>실행 중</Text>{' '}
                <Text dimColor>{setting ? `${since} · ${settingText(setting)}` : since}</Text>
              </Text>
              <Text size="caption" dimColor>
                {`In ${tokens(work.read)} (캐시 ${tokens(work.cached)})·Out ${tokens(work.write)} ×${work.steps}`}
                {work.rise === undefined ? '' : ` · 한도 +${work.rise}%p`}
              </Text>
              <Text size="caption" dimColor wrap="truncate-middle">
                {work.folder}
              </Text>
            </Box>
          )
        })}
        {codexState.running > codexState.working.length ? (
          <Text size="caption" dimColor>
            {`기록을 남기지 않는 실행 ${codexState.running - codexState.working.length}개`}
          </Text>
        ) : null}
        {codexState.configured ? (
          <Box flexDirection="column" marginTop={1}>
            <Text size="footnote">
              <Text bold>기본값</Text>{' '}
              <Text dimColor>
                {`${settingText(codexState.configured)} · ${codexConfiguredAsOf}`}
                {isCodexConfiguredOld ? '에 읽은 값이에요' : '에 읽음'}
              </Text>
            </Text>
          </Box>
        ) : null}
        {codexState.last ? (
          <Box flexDirection="column" marginTop={1}>
            <Text size="footnote">
              <Text bold>마지막 실행</Text>{' '}
              <Text dimColor>
                {local(codexState.last.finishedAt, offset).hm}
                {codexState.last.model ? ` · ${codexState.last.model}` : ''}
                {codexState.last.effort ? ` ${codexState.last.effort}` : ''}
              </Text>
            </Text>
            <Text size="caption" dimColor>
              {`In ${tokens(codexState.last.read)} (캐시 ${tokens(codexState.last.cached)})·Out ${tokens(codexState.last.write)}`}
              {codexState.last.rise === undefined ? '' : ` · 한도 +${codexState.last.rise}%p`}
            </Text>
            <Text size="caption" dimColor wrap="truncate-middle">
              {codexState.last.folder}
            </Text>
          </Box>
        ) : null}
        {codexState.error ? (
          <Text size="caption" color={LEVEL_KEY.warn}>
            {codexState.error}
          </Text>
        ) : null}

        {heading('THIS SESSION')}
        <Box flexDirection="row" flexWrap="wrap" columnGap={3} rowGap={1} marginTop={1}>
          {meter(
            'context',
            state.contextPercent ?? 0,
            'Context',
            `${tokens(state.contextTokens ?? 0)} / ${tokens(state.contextWindow)}`,
            '',
            contextLevel(state.contextPercent ?? 0),
          )}
          {state.costUsd !== undefined && (
            <Box flexDirection="column">
              <Text size="footnote" bold>
                ≈${state.costUsd.toFixed(2)}
              </Text>
              <Text size="caption" dimColor>
                API 요금 환산
              </Text>
              <Text size="caption" dimColor>
                요금제엔 청구 안 됨
              </Text>
            </Box>
          )}
        </Box>

        {heading('INSIGHTS')}
        {seen.length === 0 ? (
          <Text size="footnote" dimColor>
            평소보다 많이 쓴 답변이 생기면 이유와 줄이는 방법을 여기에 남겨요.
          </Text>
        ) : (
          seen.map(s => (
            <Box key={s.turnId} flexDirection="column" marginTop={1}>
              <Text size="footnote">
                <Text dimColor tabular>
                  {local(s.at, offset).hm}
                </Text>{' '}
                <Text color={LEVEL_KEY[s.level]}>▲ {s.short}</Text>
              </Text>
              <Text size="caption" dimColor>
                {s.metrics}
              </Text>
              <Text size="caption">{s.detail}</Text>
            </Box>
          ))
        )}

        {heading('RECENT REPLIES')}
        {shown.length === 0 ? (
          <Text size="footnote" dimColor>
            패널을 켠 뒤의 답변부터 쌓여요.
          </Text>
        ) : (
          shown.map((t, i) => {
            const newerStart = t.endPct ?? (i === 0 ? fiveHour?.percentUsed : shown[i - 1].startPct)
            const delta =
              !t.isRunning && t.startPct !== undefined && newerStart !== undefined ? newerStart - t.startPct : undefined

            return (
              <Box key={t.id} flexDirection="column" marginTop={1}>
                <Text size="footnote" wrap="truncate-end">
                  <Text dimColor tabular>
                    {local(t.startedAt, offset).hm}
                  </Text>{' '}
                  {t.prompt || '(continued)'}
                </Text>
                {t.isRunning ? (
                  <Text size="caption" dimColor>
                    {`Working · In ${tokens(t.liveRead ?? 0)} · Out ${tokens(t.liveWrite ?? 0)} · ${t.steps ?? 0} steps · ${duration(now - t.startedAt)}`}
                  </Text>
                ) : (
                  <Text size="caption">
                    <Text dimColor>In </Text>
                    <Text color={LEVEL_KEY[t.level ?? 'ok']}>{tokens(t.read)}</Text>
                    <Text dimColor>·Out </Text>
                    <Text color={LEVEL_KEY[t.level ?? 'ok']}>{tokens(t.write)}</Text>
                    <Text dimColor>
                      {` · ${t.steps ?? 0} steps · ${duration(t.durationMs)}${
                        delta !== undefined && delta > 0 ? ` · 5h +${Math.round(delta)}%` : ''
                      }`}
                    </Text>
                  </Text>
                )}
              </Box>
            )
          })
        )}

        {shown.length > 0 && (
          <Box marginTop={2}>
            <Text size="caption" dimColor>
              5h +%에는 같은 시간에 돌린 다른 대화도 섞여요.
            </Text>
          </Box>
        )}

        {heading('SYMBOLS')}
        <Text size="caption" dimColor>
          ↻ 한도 초기화 시각 · ⧗ 지금 속도면 한도를 다 쓰는 시각 · 링 위의 점 = 지나간 시간
        </Text>
        <Text size="caption" dimColor>
          ⇡ 안 올린 커밋 · ⇣ 받아올 커밋 · ! 바뀐 파일 · ? 새 파일 · + 커밋 준비 · * 임시 보관 · ~ 충돌
        </Text>
      </Box>
    )
  })
}
