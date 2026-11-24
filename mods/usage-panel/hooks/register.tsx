import { atom, read, update } from 'claude-code'
import {
  CODEX_WORK_STATES,
  MINUTE_MS,
  WORK_KEEP_MS,
  actualLoad,
  averageOver,
  codexTokens,
  codexWeight,
  expectedLoad,
  isCodexWorkActive,
  isCodexWorkUncertain,
  isNewWeek,
  keptSteps,
  keptWorks,
  overLevel,
  workPace,
} from './work'
import type { EngineInterface, FsEntry, Register, RenderChildren, Timer, ToolCallResult } from 'claude-code'
import type {
  Cache,
  CacheOutput,
  CacheTtl,
  Codex,
  CodexLocalOutput,
  CodexRecord,
  CodexReading,
  CodexReservations,
  CodexServerOutput,
  CodexSetting,
  CodexTotals,
  Helper,
  History,
  Insight,
  Level,
  Limit,
  LimitStep,
  ListedSession,
  Live,
  Reading,
  Segment,
  SessionCounts,
  SessionListFilter,
  SessionStates,
  SessionStatesClaim,
  Shared,
  SharedFile,
  Tab,
  Turn,
  Where,
  Work,
  WorkEstimate,
  WorkFile,
  WorkLoad,
  WorkMaker,
  WorkPace,
} from '../types'

// What the estimate tool was asked to record, once its input checked out.
type EstimateInput = { maker: WorkMaker; minutes: number; steps?: number; reservation?: string }
type EstimateCheck = { input: EstimateInput; error?: undefined } | { input?: undefined; error: string }
// A model call's usage, as a step or a turn reports it.
type Usage = {
  input_tokens: number
  cache_creation_input_tokens?: number
  cache_read_input_tokens?: number
  output_tokens: number
}
// One piece of a work row as the band draws it: its text, and the color or
// weight that carries how it compares with the estimate.
type WorkPart = { key: string; text: string; color?: string; isBold?: boolean; isDim?: boolean }
// A work's pieces in its usage line: when it ends, its tokens, its share of the week.
type WorkPieces = { time: WorkPart[]; tokens: WorkPart[]; percent: WorkPart[] }
type OwnSession = Omit<ListedSession, 'startedBy'> & { parentSessionId?: string | null }
type CacheView = { kind: 'warm'; until: string } | { kind: 'soon'; minutes: number } | { kind: 'cold'; after: string }
// What each shared reading holds, by its file's name.
type SharedOutputs = {
  [SHARED_NAME.codexLocal]: CodexLocalOutput
  [SHARED_NAME.codexServer]: CodexServerOutput
  [SHARED_NAME.history]: History
  [SHARED_NAME.sessionStates]: SessionStates
}
type SharedName = keyof SharedOutputs
// One shared reading asked for: one made since `since` for the key's folder.
type SharedRequest<N extends SharedName> = { name: N; key: string; since: number; arguments: readonly string[] }
// One piece of work a session repeats: a timer's tick, or a refresh an event asks for.
type OneAtATime = { tick: () => Promise<void>; refresh: () => Promise<void> }
type WorkObserver = (list: readonly Work[]) => void
// A timer of the band: `busyMs` is its pace while the session works, `idleMs`
// its pace while idle; left out, the timer waits while the session is idle.
// `tick` runs at its pace and skips while its last run still goes; `refresh`,
// asked on waking, runs once more after it.
interface BandTimer {
  busyMs: number
  idleMs?: number
  tick: () => void
  refresh: () => void
}
interface TimerOwner {
  markActive: () => Promise<void>
}
interface SettledCodexWorks {
  works: Work[]
  usedPending: boolean
  usedReservations: string[]
}
interface SeenCodexRecords {
  records: readonly CodexRecord[]
  foundIds: ReadonlySet<string | undefined>
  usedCommands: ReadonlySet<string>
  sessionId: string
  isReadFailed: boolean
}

const PANE = 'usage'
const TITLE = '사용량'
const KEEP_TURNS = 30
const RECOUNT_EVERY_MS = 10 * 60 * 1000
// The running reply's time redraws each second.
const TURN_TICK_MS = 1000
// A session is idle once no reply, busy helper or open work was seen for this
// long, so the short gaps between replies leave the timers as they are.
const IDLE_AFTER_MS = 60 * 1000

// Keep tags for compatible additions: another tag discards stored work.
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
// An idle session looks at the Codex records each minute instead.
const CODEX_IDLE_TICK_MS = 60 * 1000
const CODEX_SERVER_EVERY_MS = 5 * 60 * 1000
// A reading older than this is drawn grey with the time it was read.
const STALE_MS = 15 * 60 * 1000
const CODEX_READ_MS = 40 * 1000
const CODEX_RUN = /\bcodex\b[^\n;&]*\s(?:exec|e)\b/
// A command that runs codex exec itself, not one that only names it (a
// commit message, a search): at the start of the line or after a separator,
// past variable settings and a few wrappers, and not asking for its help.
const CODEX_INVOCATION =
  /(?:^|[;&|\n])\s*(?:\w+=\S*\s+)*(?:(?:env|nohup|caffeinate(?:\s+-\w+)*|timeout\s+\S+)\s+)*(?:[^\s'";|&]*\/)?codex\s+(?:(?:--[\w-]+(?:=[^\s'";|&]+)?|-\w)(?:\s+[^\s'";|&]+=[^\s'";|&]+)?\s+)*(?:exec|e)\b(?!\s+(?:--help|-h)\b)/
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
// An idle session takes another session's Claude reading each minute instead.
const CLAUDE_SHARE_IDLE_EVERY_MS = 60 * 1000
// The Codex readings and the 7-day count pass through the same folder: one
// session reads, under a lock on the file, and the others take its reading.
const SHARED_NAME = {
  codexLocal: 'codex-local',
  codexServer: 'codex-server',
  history: 'history',
  sessionStates: 'session-states',
} as const
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
// An idle session counts at the same pace: the person reads the waiting count
// on an idle band, the dashboard calls the shared file stale past 15 seconds,
// and a reading taken from it may already be 5 seconds old.
const SESSIONS_EVERY_MS = 5 * 1000
const SESSIONS_READ_MS = 20 * 1000
// The one switch for the yellow count, which stands in for a dot the app keeps
// to itself: off, a session waiting for the person is counted in none.
const IS_WAITING_COUNTED = true
const SESSION_LIST = { server: 'ccd_session_mgmt', tool: 'list_sessions', limit: 500 } as const
const SESSION_SELF = { tool: 'get_session', arguments: { session_id: 'self' } } as const
// The first band to find the shared reading stale marks that it reads, so the
// others skip the app's list until the new reading is in; a mark left by a
// band that stopped mid-read lapses after one tick.
const SESSION_STATES_CLAIM = `${SHARED_FOLDER}/session-states.claim.json`
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
// This session's work, a reply or a codex exec run it started, each with the
// estimate the session gave for it; the finished work of every session here
// from the files they keep; and the weekly limits' steps this session saw and
// every session saw, which the percent of a work is figured from.
const works = atom({ plugin: 'usage-panel', key: 'works' } as const, [] as Work[], { shape: 'works-1' })
const workHistory = atom({ plugin: 'usage-panel', key: 'workHistory' } as const, [] as Work[], {
  shape: 'work-history-1',
})
const limitSteps = atom({ plugin: 'usage-panel', key: 'limitSteps' } as const, [] as LimitStep[], {
  shape: 'limit-steps-1',
})
const limitHistory = atom({ plugin: 'usage-panel', key: 'limitHistory' } as const, [] as LimitStep[], {
  shape: 'limit-history-1',
})
// A Codex estimate waits here for the codex exec run the session starts next.
const pendingCodexEstimate = atom(
  { plugin: 'usage-panel', key: 'pendingCodexEstimate' } as const,
  null as WorkEstimate | null,
  { shape: 'pending-codex-1' },
)
const reservedCodexEstimates = atom(
  { plugin: 'usage-panel', key: 'reservedCodexEstimates' } as const,
  {} as CodexReservations,
  { shape: 'codex-reservations-1' },
)
// Bumped twice a minute while work runs, so its minutes left redraw.
const workTick = atom({ plugin: 'usage-panel', key: 'workTick' } as const, 0, { shape: 'work-tick-1' })
const WORK_TICK_MS = 30 * 1000
const WEEK_LIMIT = 'seven_day'
const PERCENT_SHOWN_MIN = 0.1
const WORK_FOLDER = 'Library/Application Support/usage-panel/work'
const WORK_EVERY_MS = 60 * 1000
const KEEP_OWN_WORKS = 50
const SHOWN_HISTORY = 10
// A Codex estimate is for the run started within this long after it.
const CODEX_ESTIMATE_MS = 10 * 60 * 1000
// A run's record begins up to this long before its command was seen to start,
// and up to the approval wait after it, since the command waits for the
// person's approval before it runs.
const CODEX_MATCH_MS = 15 * 1000
// The owner the reader names for a run whose reservation it could not trace.
const UNKNOWN_OWNER = 'unknown'
const CODEX_APPROVAL_MS = 5 * 60 * 1000
// Recent legacy completions need proof before they can keep their check.
const CODEX_SETTLE_MS = 10 * 60 * 1000
// No estimate runs past a day: a longer one is a slip.
const ESTIMATE_MAX_MINUTES = 24 * 60
const ESTIMATE_TOOL = 'work_estimate'
// Matched by pattern: the tool is this mod's own, and the engine lists it
// among the known tools only once the mod has registered it.
const ESTIMATE_TOOL_NAME = new RegExp(`^mcp__usage-panel__${ESTIMATE_TOOL}$`)
const PLAN_TOOL_NAME = 'mcp__ccd_turn__update_plan'
const ESTIMATE_SPEC = {
  name: ESTIMATE_TOOL,
  description:
    'Record a time estimate so the usage band shows it next to the actual result. Call it whenever you tell the user how long work will take: maker "claude" for the rest of this turn, with steps as the number of tool calls you expect. For maker "codex", omit steps and give reservation to attach or update the estimate for that reservation, now or when its record appears. Without reservation, attach to the only running or starting run in this session that has no estimate; otherwise keep it for the next run appearing within ten minutes. Every run from that same command receives it. Claude estimates are unchanged.',
  inputSchema: {
    type: 'object',
    properties: {
      maker: { enum: ['claude', 'codex'] },
      minutes: { type: 'number', minimum: 1, maximum: ESTIMATE_MAX_MINUTES },
      steps: { type: 'integer', minimum: 1 },
      reservation: { type: 'string', minLength: 1 },
    },
    required: ['maker', 'minutes'],
  },
  isDeferred: false,
}
const NO_CLAUDE_ESTIMATE = 'No work estimate is recorded for this turn yet.'
const NO_CODEX_ESTIMATE = 'No Codex estimate was recorded for this run.'
const WORK_NAME: Record<WorkMaker, string> = { claude: 'Claude', codex: 'Codex' }
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
// their last step, the last finished `codex exec` run, the runs finished within
// two hours with when each began, and how many runs work now (a
// CodexLocalOutput). Big records are read from their end for the limits; a
// run's record is read whole once and then on from where the last read
// stopped, and only for the runs those need. Keys without a value are left out.
const CODEX_READER = String.raw`
import glob, subprocess, select, shutil, datetime, functools
import re
HOME = os.path.expanduser('~')
ROOT = os.path.join(HOME, '.codex', 'sessions')
KIND = {300: 'five_hour', 10080: 'seven_day'}
TAIL_BYTES = 512 * 1024
FINISHED_KEEP_S = 2 * 60 * 60
LEDGER = os.path.join(HOME, '.claude', 'codex-budget', 'ledger.jsonl')
LEDGER_KEY = '__ledger__'
RESERVATION_MARK = b'Budget reservation: '
RESERVATION_LINE = re.compile('^' + re.escape(RESERVATION_MARK.decode()) + r'([^\s]+)(?:\r?\n|$)')
RUNNING = 'running'
DONE = 'done'
STOPPED = 'stopped'
UNKNOWN = 'unknown'

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
    'reservation': None,
    'has_turn': False,
    'turn_id': None,
    'turn_started': None,
    'aborted': False,
}


# What a run's record says so far, read on from where the last read stopped:
# the setting its last turn started with, its totals, the limits at its first
# and newest step and how many steps reported them, how many steps reported
# their totals, when it finished.
# Codex puts its own instructions in user messages before the brief, so the
# reservation is looked for in each user message until one names it.
def scan_record(path, kept):
    if kept is not None and 'reservation' not in kept:
        kept = None
    record = start_place(path, kept, EMPTY_RECORD)

    for raw in new_lines(path, record):
        is_user = record['reservation'] is None and RESERVATION_MARK in raw and b'"user"' in raw
        is_event = b'"event_msg"' in raw and any(marker in raw for marker in (
            b'"token_count"', b'"task_started"', b'"task_complete"', b'"turn_aborted"'))

        if not (is_user or is_event or b'"turn_context"' in raw):
            continue
        entry = json.loads(raw)
        payload = entry.get('payload') or {}
        kind = entry.get('type')

        if (is_user and kind == 'response_item'
                and payload.get('type') == 'message' and payload.get('role') == 'user'):
            text = next((part.get('text', '') for part in payload.get('content', [])
                         if part.get('type') == 'input_text'), '')
            match = RESERVATION_LINE.match(text)

            record['reservation'] = match.group(1) if match else None
        elif kind == 'turn_context':
            record['model'] = payload.get('model') or record['model']
            record['effort'] = payload.get('effort')
        elif kind == 'event_msg' and payload.get('type') == 'token_count':
            if payload.get('info'):
                record['total'] = payload['info'].get('total_token_usage') or record['total']
                record['steps'] += 1
            if payload.get('rate_limits'):
                found = windows(payload['rate_limits'])

                record['first_rate'] = found if record['rate_count'] == 0 else record['first_rate']
                record['last_rate'] = found
                record['rate_count'] += 1
        elif kind == 'event_msg':
            turn_id = ((payload.get('turn_attribution') or {}).get('turn_id')
                       or payload.get('turn_id') or entry.get('turn_id'))

            if payload.get('type') == 'task_started':
                record['has_turn'] = True
                record['turn_id'] = turn_id
                record['turn_started'] = ms(entry['timestamp'])
                record['finished'] = None
                record['aborted'] = False
            elif record['has_turn'] and turn_id == record['turn_id']:
                if payload.get('type') == 'turn_aborted':
                    record['aborted'] = True
                    record['finished'] = None
                elif payload.get('type') == 'task_complete' and not record['aborted']:
                    record['finished'] = ms(entry['timestamp'])
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


# A ledger line's reservation and the session that made it, or None.
def ledger_owner(raw):
    try:
        entry = json.loads(raw)
    except ValueError:
        return None
    if (entry.get('event') == 'reserve' and isinstance(entry.get('identifier'), str)
            and isinstance(entry.get('session'), str)):
        return entry['identifier'], entry['session']
    else:
        return None


def reservation_owners(kept):
    try:
        stat = os.stat(LEDGER)
        signature = [stat.st_size, stat.st_mtime_ns]

        if kept and kept.get('signature') == signature:
            return kept
        else:
            with open(LEDGER, encoding='utf-8') as file:
                owners = dict(owner for owner in map(ledger_owner, file) if owner)
            return {'signature': signature, 'owners': owners}
    except OSError:
        return {'owners': {}}


def open_records(command):
    try:
        ran = subprocess.run(command, capture_output=True, text=True, timeout=10)

        if ran.returncode == 1 and not ran.stdout.strip() and not ran.stderr.strip():
            return {}, False
        elif ran.returncode != 0:
            return {}, True
        else:
            found = {}
            process = None

            for line in ran.stdout.splitlines():
                if line.startswith('p'):
                    process = line[1:]
                elif line.startswith('n' + ROOT + os.sep) and process:
                    path = line[1:]

                    if os.path.basename(path).startswith('rollout-') and path.endswith('.jsonl'):
                        found.setdefault(path, set()).add(process)
            return found, False
    except (OSError, subprocess.SubprocessError):
        return {}, True


def run_record(path, record, owners, opened, failed):
    if failed:
        state = UNKNOWN
    elif path in opened:
        state = RUNNING
    elif record['finished'] is not None and not record['aborted']:
        state = DONE
    else:
        state = STOPPED
    run = {
        **totals(path, record),
        'recordId': os.path.basename(path),
        'startedAt': ms(first(path)['timestamp']),
        'steps': record['steps'],
        'reservation': record['reservation'],
        'ownerSessionId': owners.get(record['reservation'], UNKNOWN),
        'state': state,
        'turnId': record['turn_id'],
        'turnStartedAt': record.get('turn_started'),
        'finishedAt': record['finished'] if state == DONE else None,
    }

    return present(run)


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
        command = arguments[1:] or ['/usr/sbin/lsof', '-nP', '-w', '-Fpn', '-c', 'codex']
        opened, failed = open_records(command)
        ledger = reservation_owners(kept.get(LEDGER_KEY))
        now = time.time() * 1000
        since = now - FINISHED_KEEP_S * 1000
        retained = {path for path, scan in kept.items()
                    if path != LEDGER_KEY and scan.get('state') in (RUNNING, UNKNOWN) and os.path.isfile(path)}
        closed = {path for path, scan in kept.items()
                  if path != LEDGER_KEY and scan.get('closed_at', 0) >= since and os.path.isfile(path)}
        files = sorted(set(recent()) | set(opened) | retained | closed, key=os.path.getmtime, reverse=True)
        runs = []
        processes = set()

        for path in files:
            if not is_exec(path):
                continue
            # A closed record that grew since its scan was resumed: read it again.
            is_grown = path in kept and os.path.getsize(path) != kept[path].get('offset')
            is_recent = ms(first(path)['timestamp']) >= since or os.path.getmtime(path) * 1000 >= since
            needs_scan = path in opened or path in retained or is_grown or is_recent

            if needs_scan or path in closed:
                record = scan_record(path, kept.get(path)) if needs_scan else dict(kept[path])
                run = run_record(path, record, ledger['owners'], opened, failed)

                if run['state'] in (DONE, STOPPED):
                    record['closed_at'] = record.get('closed_at', now)
                else:
                    record.pop('closed_at', None)
                record['state'] = run['state']
                records[path] = record
                runs.append(run)
                processes.update(opened.get(path, set()))
        done = sorted((run for run in runs if run['state'] == DONE), key=lambda run: run['finishedAt'], reverse=True)
        found = {
            'snapshot': snapshot(files),
            'last': done[0] if done else None,
            'working': [run for run in runs if run['state'] == RUNNING],
            'finished': [run for run in done if run['finishedAt'] >= (time.time() - FINISHED_KEEP_S) * 1000],
            'records': runs,
            'running': len(processes),
            'isProcessReadFailed': failed,
        }

        records[LEDGER_KEY] = ledger
        return present(found), records

run_shared(read_codex)
`

const SESSION_STATES_JOIN = String.raw`
sys.path.insert(0, os.path.expanduser('~/.claude/scripts'))
from session_states import read_shared_states

run_shared(read_shared_states)
`

// Each shared reading by its file's name: the script that makes it and how
// long it may take.
const SHARED_READERS = {
  [SHARED_NAME.codexLocal]: { script: CODEX_READER, timeoutMs: CODEX_READ_MS },
  [SHARED_NAME.codexServer]: { script: CODEX_READER, timeoutMs: CODEX_READ_MS },
  [SHARED_NAME.history]: { script: HISTORY_READER, timeoutMs: RECOUNT_READ_MS },
  [SHARED_NAME.sessionStates]: { script: SESSION_STATES_JOIN, timeoutMs: SESSIONS_READ_MS },
} as const

// This session's newest reply and the life of the cache it wrote.
const CACHE_READER = String.raw`
import json, os, glob, sys, datetime
PROJECTS = os.path.expanduser('~/.claude/projects')
TAIL_BYTES = 512 * 1024

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

print(json.dumps(cache(sys.argv[1])))
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

const LIMIT_NAME: Record<string, string> = { five_hour: '5h', [WEEK_LIMIT]: 'Week' }

// Fixed hues read on light and dark alike; text keeps the theme's own colors.
const ORANGE = '#D97757'
const AMBER = '#D99A00'
const RED = '#E5484D'
const TRACK = 'rgba(128,128,128,0.28)'
// A reading too old to trust is drawn grey, its time said beside it.
const DIM_HEX = '#9C9A92'

const clamp = (p: number) => Math.max(0, Math.min(100, p))

const WINDOW_MS: Record<string, number> = { five_hour: 5 * 3600_000, [WEEK_LIMIT]: 7 * 86_400_000 }

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
const weightOf = (u: Usage) =>
  u.input_tokens + (u.cache_creation_input_tokens ?? 0) * 1.25 + (u.cache_read_input_tokens ?? 0) * 0.1 + u.output_tokens * 5

// What a call read: its new input, the cache it wrote and the cache it read.
const readTokens = (u: Usage): number =>
  u.input_tokens + (u.cache_creation_input_tokens ?? 0) + (u.cache_read_input_tokens ?? 0)

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
    finished: output.finished ?? before.finished,
    running: output.isProcessReadFailed ? before.running : output.running,
    records: output.records,
    isProcessReadFailed: output.isProcessReadFailed,
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
function runSetting(run: Pick<CodexTotals, 'model' | 'effort'>): CodexSetting | undefined {
  if (run.model) {
    return { model: run.model, effort: run.effort }
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

// A repeated piece of work as a band timer runs it, its promise left to the
// work's own error handling.
function pacedRuns(work: OneAtATime): Pick<BandTimer, 'tick' | 'refresh'> {
  return {
    tick: () => void work.tick(),
    refresh: () => void work.refresh(),
  }
}

// A piece of work that never overlaps itself ticks and refreshes alike.
function plainRuns(run: () => void): Pick<BandTimer, 'tick' | 'refresh'> {
  return { tick: run, refresh: run }
}

// Keeps every band timer at the pace the session asks for: the working pace
// from the start, the idle pace once the session stayed quiet for
// IDLE_AFTER_MS. Quiet starts at the last activity, or at the first look that
// found nothing busy, never at a look that found it busy. Activity while idle
// asks each paused or slowed timer for a refresh, which also runs once more
// after a read still going, so what the idle pace left behind is read again; a
// timer whose pace stays keeps its beat. An activity count, raised before any
// wait, tells the idle entry that activity came while it read the state.
async function startPacedTimers(
  $: EngineInterface,
  timers: readonly BandTimer[],
  isBusy: () => Promise<boolean>,
): Promise<TimerOwner> {
  const running = new Map<BandTimer, Timer>()
  const paceChanging = timers.filter(timer => timer.idleMs !== timer.busyMs)
  let isIdle = false
  let activity = 0
  // None while the last look found the session busy.
  let quietSince: number | undefined = await $.clock.now()

  const startTimer = (timer: BandTimer): void => {
    const ms = isIdle ? timer.idleMs : timer.busyMs

    running.get(timer)?.cancel()
    running.delete(timer)
    if (ms !== undefined) {
      running.set(timer, $.clock.every(ms, timer.tick))
    }
  }
  const wakeTimer = (timer: BandTimer): void => {
    timer.refresh()
    startTimer(timer)
  }
  const scheduleIdleEntry = (ms: number): void => {
    $.clock.after(ms, () => void enterIdleWhenQuiet())
  }
  const enterIdleWhenQuiet = async (): Promise<void> => {
    try {
      const seenActivity = activity
      const isWorking = await isBusy()
      const now = await $.clock.now()
      const quietMs = activity === seenActivity && quietSince !== undefined ? now - quietSince : 0

      if (isWorking) {
        quietSince = undefined
        scheduleIdleEntry(IDLE_AFTER_MS)
      } else if (quietMs >= IDLE_AFTER_MS) {
        isIdle = true
        paceChanging.forEach(startTimer)
      } else {
        quietSince = quietSince ?? now
        scheduleIdleEntry(IDLE_AFTER_MS - quietMs)
      }
    } catch {
      scheduleIdleEntry(IDLE_AFTER_MS)
    }
  }
  const markActive = async (): Promise<void> => {
    try {
      activity += 1
      quietSince = await $.clock.now()
      if (isIdle) {
        isIdle = false
        paceChanging.forEach(wakeTimer)
        scheduleIdleEntry(IDLE_AFTER_MS)
      }
    } catch {
      // The next activity wakes the timers.
    }
  }

  timers.forEach(startTimer)
  scheduleIdleEntry(IDLE_AFTER_MS)
  return { markActive }
}

// A failed read of the run records shows on the Codex line until a read
// succeeds. Noting it is a help: one that fails leaves the line as it was.
async function recordCodexReadFailure($: EngineInterface, rememberWorks: WorkObserver): Promise<Codex | undefined> {
  try {
    const now = await $.clock.now()
    // A run seen running may have ended since; one seen ended stays ended.
    const markRecordUnknown = (record: CodexRecord): CodexRecord =>
      record.state === 'running' ? { ...record, state: 'unknown', finishedAt: undefined } : record
    const markFailed = (before: Codex): Codex => ({
      ...before,
      error: CODEX_READ_FAILED,
      isProcessReadFailed: true,
      records: before.records?.map(markRecordUnknown),
    })
    const state = await update($, codex, markFailed)
    const markUnknown = (work: Work): Work => isCodexWorkOpen(work, now) ? unknownCodexWork(work) : work

    await writeWorks($, rememberWorks, list => list.map(markUnknown))
    return state
  } catch {
    // The next read notes it again.
  }
}

// A failed server read shows as one that failed, and the server is asked
// again once its next reading is due.
async function recordCodexServerFailure($: EngineInterface): Promise<Codex | undefined> {
  try {
    const now = await $.clock.now()

    return await update($, codex, before => ({ ...before, error: CODEX_READ_FAILED, serverAt: now }))
  } catch {
    // The next read notes it again.
  }
}

// The sessions the app lists, less this one: by default the sidebar's own
// list, which leaves archived ones out.
async function listSessions($: EngineInterface, filter: SessionListFilter = {}): Promise<ListedSession[]> {
  const listed = await $.mcp.call(SESSION_LIST.server, SESSION_LIST.tool, { limit: SESSION_LIST.limit, ...filter })
  const text = listed.content.map(block => block.text ?? '').join('')
  const rows = listed.isError ? undefined : (JSON.parse(text) as ListedSession[])

  if (Array.isArray(rows)) {
    return rows.filter(row => typeof row.sessionId === 'string').map(row => ({
      sessionId: row.sessionId,
      title: row.title,
      cwd: row.cwd,
      isRunning: row.isRunning,
      lastActivityAt: row.lastActivityAt,
      link: row.link,
      startedBy: row.startedBy,
    }))
  } else {
    throw new Error('세션 목록을 읽지 못했어요')
  }
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

// The app's own id differs from the engine's transcript id. Include this row
// in the shared reading, so every band can leave out only itself.
async function readOwnSession($: EngineInterface): Promise<ListedSession> {
  const result = await $.mcp.call(SESSION_LIST.server, SESSION_SELF.tool, SESSION_SELF.arguments)
  const text = result.content.map(block => block.text ?? '').join('')
  const own = result.isError ? undefined : (JSON.parse(text) as OwnSession)

  if (own && typeof own.sessionId === 'string') {
    return {
      sessionId: own.sessionId,
      title: own.title,
      cwd: own.cwd,
      isRunning: own.isRunning,
      lastActivityAt: own.lastActivityAt,
      link: own.link,
      startedBy: own.parentSessionId,
    }
  } else {
    throw new Error('이 세션의 정보를 읽지 못했어요')
  }
}

// The time another band marked that it reads the session states; 0 when no
// mark can be read.
async function readClaimAt($: EngineInterface, path: string): Promise<number> {
  try {
    const text = (await $.fs.exists(path)) ? await $.fs.read(path) : ''
    const claim = text ? (JSON.parse(text) as SessionStatesClaim) : undefined

    return typeof claim?.at === 'number' ? claim.at : 0
  } catch {
    return 0
  }
}

// A new reading for every band, made under a mark so the bands that find the
// same stale reading meanwhile leave the app's list alone; none while another
// band reads.
async function readNewSessionStates(
  $: EngineInterface,
  claimPath: string,
  now: number,
  since: number,
): Promise<Shared<SessionStates> | undefined> {
  if ((await readClaimAt($, claimPath)) > since) {
    return undefined
  } else {
    const claim: SessionStatesClaim = { at: now }

    await $.fs.write(claimPath, JSON.stringify(claim))
    const listed = await listSessions($)
    const own = await readOwnSession($)
    const rows = [...listed.filter(row => row.sessionId !== own.sessionId), own]
    const request = {
      name: SHARED_NAME.sessionStates,
      key: SHARED_ANY_FOLDER,
      since,
      arguments: [JSON.stringify(rows)],
    } as const

    return readShared($, request)
  }
}

// Only the display counts here; the shared reader owns decisions and memory.
function sharedSessionCounter($: EngineInterface, home: string): () => Promise<void> {
  const path = `${home}/${SHARED_FOLDER}/${SHARED_NAME.sessionStates}.json`
  const claimPath = `${home}/${SESSION_STATES_CLAIM}`

  return async (): Promise<void> => {
    try {
      const now = await $.clock.now()
      const since = now - SESSIONS_EVERY_MS
      // The engine lists the variables a module reads only from a literal name.
      const ownId = await $.env.get('CLAUDE_CODE_HOST_SESSION_ID')
      const kept = await readSharedEntry<SessionStates>($, path, SHARED_ANY_FOLDER)
      const shared = kept && kept.at > since ? kept : await readNewSessionStates($, claimPath, now, since)

      if (shared) {
        const counts: SessionCounts = { working: 0, waiting: 0, done: 0 }

        for (const [id, session] of Object.entries(shared.output.sessions)) {
          if (id !== ownId) {
            if (session.state === 'working' || session.state === 'done') {
              counts[session.state] += 1
            } else if (session.state === 'waiting' && IS_WAITING_COUNTED) {
              counts.waiting += 1
            }
          }
        }
        const before = await read($, sessions)

        if (
          before?.working !== counts.working ||
          before.waiting !== counts.waiting ||
          before.done !== counts.done
        ) {
          await update($, sessions, () => counts)
        }
      }
    } catch {
      // A busy or failed reading keeps the previous counts until a later tick.
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
  const argv = [PYTHON, '-I', '-c', CACHE_READER, await $.session.id()]
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

// A work's pieces in a usage line, each after its own mark: a stopwatch for
// when it ends (the clock is the cache's), a coin stack for its tokens, a dial
// for its share of the week. A chevron opens the details.
const DETAILS_GLYPH = '›'
const STOPWATCH_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 16 16"><g fill="none" stroke="#8A877F" stroke-width="1.4" stroke-linecap="round"><circle cx="8" cy="9.3" r="5.2"/><path d="M6.4 1.8h3.2M8 9.3V6.4"/></g></svg>'
const TOKENS_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 16 16"><g fill="none" stroke="#8A877F" stroke-width="1.3"><ellipse cx="8" cy="4.3" rx="5" ry="2"/><path d="M3 4.3v3.6c0 1.1 2.2 2 5 2s5-.9 5-2V4.3M3 7.9v3.6c0 1.1 2.2 2 5 2s5-.9 5-2V7.9"/></g></svg>'
const DIAL_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 16 16"><g fill="none" stroke="#8A877F" stroke-width="1.4" stroke-linecap="round"><path d="M2.3 11.5a5.7 5.7 0 0 1 11.4 0"/><path d="M8 11.5l2.6-3.6"/></g></svg>'

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

// Opening the details is activity: what an idle band left behind is read again.
async function openDetails($: EngineInterface, timerOwner: TimerOwner | undefined): Promise<void> {
  await $.ui.open({ id: PANE, title: TITLE, closeOnEscape: true })
  await timerOwner?.markActive()
}

function isWorkMaker(value: unknown): value is WorkMaker {
  return value === 'claude' || value === 'codex'
}

function isWholeNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 1
}

// The estimate tool's input comes from the model, so each field is checked.
function checkEstimateInput(e: Record<string, unknown>): EstimateCheck {
  const { maker, minutes, steps, reservation } = e

  if (!isWorkMaker(maker)) {
    return { error: 'maker는 "claude"나 "codex"여야 해요.' }
  } else if (typeof minutes !== 'number' || !(minutes >= 1 && minutes <= ESTIMATE_MAX_MINUTES)) {
    return { error: `minutes는 1에서 ${ESTIMATE_MAX_MINUTES} 사이의 숫자여야 해요.` }
  } else if (reservation !== undefined && (typeof reservation !== 'string' || reservation.trim().length === 0)) {
    return { error: '예약 번호(reservation)는 비어 있지 않은 문자열이어야 해요.' }
  } else if (maker === 'codex') {
    return { input: { maker, minutes, reservation: typeof reservation === 'string' ? reservation : undefined } }
  } else if (isWholeNumber(steps)) {
    return { input: { maker, minutes, steps } }
  } else {
    return { error: 'Claude 작업에는 예상 단계 수(steps, 1 이상의 정수)도 넣어 주세요.' }
  }
}

function isOptionalNumber(value: unknown): boolean {
  return value === undefined || (typeof value === 'number' && Number.isFinite(value))
}

function isEstimate(value: unknown): boolean {
  const estimate = value as Partial<WorkEstimate> | null

  return (
    typeof estimate === 'object' &&
    estimate !== null &&
    typeof estimate.minutes === 'number' &&
    typeof estimate.dueAt === 'number' &&
    isOptionalNumber(estimate.steps)
  )
}

function isLoad(value: unknown): boolean {
  const load = value as Partial<WorkLoad> | null

  return typeof load === 'object' && load !== null && typeof load.tokens === 'number' && isOptionalNumber(load.percent)
}

// Every field the band and the details read is checked, so a file another
// version of the mod wrote cannot break the drawing.
function isWork(value: unknown): value is Work {
  const work = value as Partial<Work> | null
  const isShaped = typeof work === 'object' && work !== null

  return (
    isShaped &&
    isWorkMaker(work.maker) &&
    typeof work.id === 'string' &&
    typeof work.sessionId === 'string' &&
    typeof work.startedAt === 'number' &&
    typeof work.steps === 'number' &&
    typeof work.weight === 'number' &&
    typeof work.tokens === 'number' &&
    isOptionalNumber(work.finishedAt) &&
    isOptionalNumber(work.startContext) &&
    isOptionalNumber(work.rise) &&
    isOptionalNumber(work.recordStartedAt) &&
    isOptionalNumber(work.commandStartedAt) &&
    [work.recordId, work.reservation, work.turnId, work.model, work.effort, work.commandId]
      .every(field => field === undefined || typeof field === 'string') &&
    [work.state, work.previousState]
      .every(state => state === undefined || CODEX_WORK_STATES.some(known => known === state)) &&
    [work.isGuessed, work.canMatchRecord].every(field => field === undefined || typeof field === 'boolean') &&
    (work.estimate === undefined || isEstimate(work.estimate)) &&
    (work.firstEstimate === undefined || isEstimate(work.firstEstimate)) &&
    (work.commandEstimate === undefined || isEstimate(work.commandEstimate)) &&
    (work.commandFirstEstimate === undefined || isEstimate(work.commandFirstEstimate)) &&
    (work.expected === undefined || isLoad(work.expected))
  )
}

function isLimitStep(value: unknown): value is LimitStep {
  const step = value as Partial<LimitStep> | null
  const isShaped = typeof step === 'object' && step !== null

  return (
    isShaped &&
    isWorkMaker(step.maker) &&
    Number.isFinite(step.at) &&
    Number.isFinite(step.percentUsed) &&
    (step.resetsAt === undefined || typeof step.resetsAt === 'string')
  )
}

// What a session's work file holds, each entry checked: the file is written by
// any session, and a version of the mod may have changed its shape. A file
// caught half-written is empty until its next read.
function parseWorkFile(text: string): Required<WorkFile> {
  try {
    const parsed = JSON.parse(text) as Partial<WorkFile> | null

    return {
      works: Array.isArray(parsed?.works) ? parsed.works.filter(isWork) : [],
      limitSteps: Array.isArray(parsed?.limitSteps) ? parsed.limitSteps.filter(isLimitStep) : [],
    }
  } catch {
    return { works: [], limitSteps: [] }
  }
}

// Another session's work file written within the week.
function isRecentOtherFile(entry: FsEntry, sessionId: string, now: number): boolean {
  const isWorkFile = entry.kind === 'file' && entry.name.endsWith('.json')

  return isWorkFile && entry.name !== `${sessionId}.json` && now - entry.mtimeMs < WORK_KEEP_MS
}

async function workFolder($: EngineInterface): Promise<string> {
  return `${(await $.env.get('HOME')) ?? ''}/${WORK_FOLDER}`
}

// The finished work and the limit steps of the other sessions here within the
// week, from their files, and this session's from its own memory, the newest
// last. A file not written within the week is passed by its listing, and this
// session's own file is left out: its memory holds the same, and newer.
async function readWorkHistory($: EngineInterface): Promise<Required<WorkFile>> {
  const folder = await workFolder($)
  const now = await $.clock.now()
  const sessionId = await $.session.id()
  const entries = (await $.fs.list(folder)).filter(entry => isRecentOtherFile(entry, sessionId, now))
  const files = await Promise.allSettled(entries.map(entry => $.fs.read(`${folder}/${entry.name}`)))
  const others = files.map(file => parseWorkFile(file.status === 'fulfilled' ? String(file.value) : ''))
  const otherWorks = others.flatMap(file => file.works).filter(w => w.sessionId !== sessionId)

  return {
    works: keptWorks([...otherWorks, ...(await read($, works))], now),
    limitSteps: keptSteps([...others.flatMap(file => file.limitSteps), ...(await read($, limitSteps))], now),
  }
}

// This session's finished work and the limit steps it saw go to its own file,
// which no other session writes, read from memory at the write so the newest
// save holds everything; the week's work beside it takes this session's at
// once. A failed write leaves the band as it is, and the next one writes all.
async function saveOwnWorks($: EngineInterface): Promise<void> {
  try {
    const now = await $.clock.now()
    const sessionId = await $.session.id()
    const own = keptWorks(await read($, works), now)
    const file: WorkFile = { works: own, limitSteps: keptSteps(await read($, limitSteps), now) }

    await update($, workHistory, past => keptWorks([...past.filter(w => w.sessionId !== sessionId), ...own], now))
    await $.fs.write(`${await workFolder($)}/${sessionId}.json`, JSON.stringify(file))
  } catch {
    // The next finished work writes the file again.
  }
}

// A tool's weekly limit as read now, as a step when it moved from the last
// step this session saw of that tool: a point up, or a new week. It carries
// the time the reading was made, so a reading another session shared keeps
// its own time.
function movedLimitSteps(
  seen: readonly LimitStep[],
  maker: WorkMaker,
  limits: readonly Limit[],
  at: number,
): LimitStep[] {
  const limit = limits.find(l => l.kind === WEEK_LIMIT)
  const last = [...seen].reverse().find(s => s.maker === maker)

  if (limit && (last === undefined || last.percentUsed !== limit.percentUsed || isNewWeek(last, limit))) {
    return [{ at, maker, percentUsed: limit.percentUsed, resetsAt: limit.resetsAt }]
  } else {
    return []
  }
}

// Adds the steps the weekly limits took since this session last looked; says
// whether there were any, so the caller saves them. A failure records none,
// and the history is read as it is.
async function recordLimitSteps($: EngineInterface): Promise<boolean> {
  try {
    const now = await $.clock.now()
    const seen = await read($, limitSteps)
    const claudeState = await read($, live)
    const codexState = await read($, codex)
    const found = [
      ...movedLimitSteps(seen, 'claude', claudeState.limits, claudeState.limitsAt ?? now),
      ...movedLimitSteps(seen, 'codex', codexState.limits, codexState.limitsAt ?? now),
    ]
    const isMoved = found.length > 0

    if (isMoved) {
      await update($, limitSteps, list => keptSteps([...list, ...found], now))
    }
    return isMoved
  } catch {
    return false
  }
}

function latestWork(list: readonly Work[], maker: WorkMaker): Work | undefined {
  return [...list].reverse().find(w => w.maker === maker)
}

function shownCodexWork(list: readonly Work[], now: number): Work | undefined {
  const codexWorks = list.filter(w => w.maker === 'codex')
  const open = codexWorks.filter(w => isCodexWorkOpen(w, now))
  const active = open.filter(isCodexWorkActive)
  const closed = codexWorks.filter(w => w.state !== 'unknown')
  const byDue = (a: Work, b: Work): number => {
    const aDue = a.estimate?.dueAt ?? Infinity
    const bDue = b.estimate?.dueAt ?? Infinity

    return aDue === bDue ? b.startedAt - a.startedAt : aDue > bDue ? -1 : 1
  }

  return active.sort(byDue)[0] ?? open.find(w => w.state === 'unknown') ??
    codexWorks.find(w => w.state === 'stopped') ??
    closed.sort((a, b) => (b.finishedAt ?? b.startedAt) - (a.finishedAt ?? a.startedAt))[0] ?? codexWorks.at(-1)
}

function runningClaudeWork(list: readonly Work[]): Work | undefined {
  return [...list].reverse().find(w => w.maker === 'claude' && w.finishedAt === undefined)
}

function codexWorkExpiresAt(work: Work): number {
  const changedAt = Math.max(work.startedAt, work.finishedAt ?? 0, work.estimate?.setAt ?? 0)

  return (work.state === 'starting' ? (work.commandStartedAt ?? work.startedAt) : changedAt) + CODEX_ESTIMATE_MS
}

function isCodexWorkOpen(work: Work, now: number): boolean {
  if (!work.recordId && now >= codexWorkExpiresAt(work)) {
    return false
  } else if (work.state) {
    return work.maker === 'codex' && (isCodexWorkActive(work) || work.state === 'unknown')
  } else {
    return work.maker === 'codex' && (work.finishedAt === undefined || now - work.finishedAt < CODEX_SETTLE_MS)
  }
}

function unknownCodexWork(work: Work): Work {
  return {
    ...work,
    state: 'unknown',
    previousState: work.state === 'unknown' ? work.previousState : work.state,
    expected: undefined,
  }
}

function recordWork(record: CodexRecord, work: Work, pace: WorkPace): Work {
  const state = record.state ?? 'unknown'
  const settled: Work = {
    ...work,
    id: record.recordId ?? work.id,
    recordId: record.recordId,
    recordStartedAt: record.startedAt,
    reservation: record.reservation,
    startedAt: record.startedAt,
    finishedAt: state === 'done' ? record.finishedAt : state === 'unknown' ? work.finishedAt : undefined,
    state,
    previousState: state === 'unknown' ? (work.state === 'unknown' ? work.previousState : work.state) : undefined,
    turnId: record.turnId,
    model: record.model,
    effort: record.effort,
    steps: record.steps,
    tokens: codexTokens(record),
    weight: codexWeight(record),
    rise: state === 'done' ? record.rise : undefined,
    expected: undefined,
    isGuessed: record.ownerSessionId === UNKNOWN_OWNER,
  }

  return { ...settled, expected: state === 'done' ? expectedLoad(settled, pace) : undefined }
}

// The command a record's run came from: a placeholder whose command came just
// before the run started (its first turn, or the turn a resume began), or a
// command already resolved whose first run started together with this one.
function matchingCommand(
  record: CodexRecord,
  list: readonly Work[],
  usedCommands: ReadonlySet<string>,
  runStartedAt: number,
): Work | undefined {
  const isEligible = (work: Work): boolean => {
    const startedAt = work.commandStartedAt
    // An owned record can resolve an expired placeholder; an unowned one only
    // within the approval wait.
    const latest = work.recordId
      ? (work.recordStartedAt ?? work.startedAt) + CODEX_MATCH_MS
      : (startedAt ?? 0) + (record.ownerSessionId === UNKNOWN_OWNER ? CODEX_APPROVAL_MS : Infinity)

    return work.maker === 'codex' && work.canMatchRecord === true && startedAt !== undefined &&
      runStartedAt >= startedAt - CODEX_MATCH_MS && runStartedAt <= latest
  }
  const distance = (work: Work): number => Math.abs(runStartedAt - (work.commandStartedAt ?? work.startedAt))
  const used = (work: Work): number => Number(usedCommands.has(work.commandId ?? ''))

  return list.filter(isEligible).sort((a, b) => distance(a) - distance(b) || used(a) - used(b))[0]
}

// Old timestamps are only migration evidence, never an identity for new work.
function relinkCodexWork(work: Work, records: readonly CodexRecord[], now: number): Work {
  const matches = records.filter(r => r.ownerSessionId === work.sessionId && r.startedAt === work.recordStartedAt)

  if (work.maker !== 'codex' || work.recordId || work.commandId) {
    return work
  } else if (matches.length === 1 && matches[0]?.recordId) {
    return { ...work, recordId: matches[0].recordId }
  } else if (work.finishedAt === undefined || now - work.finishedAt < CODEX_SETTLE_MS || work.state === 'unknown') {
    return unknownCodexWork(work)
  } else {
    return work
  }
}

function settleCodexWorks(
  list: readonly Work[],
  state: Codex,
  pace: WorkPace,
  now: number,
  sessionId: string,
  pending: WorkEstimate | null,
  reservations: Readonly<CodexReservations>,
): SettledCodexWorks {
  const records = (state.records ?? []).filter(r => r.recordId !== undefined)
  const migrated = list.map(w => relinkCodexWork(w, records, now))
  const found: Work[] = []
  const usedCommands = new Set<string>()
  const usedReservations: string[] = []
  let usedPending = false

  for (const record of records) {
    const existing = migrated.find(w => w.maker === 'codex' && w.recordId === record.recordId)
    const isResumed = existing !== undefined && existing.turnId !== undefined && existing.turnId !== record.turnId
    const resumedCommand = isResumed
      ? matchingCommand(record, migrated, usedCommands, record.turnStartedAt ?? record.startedAt)
      : undefined
    const command = existing
      ? (resumedCommand ?? (existing.commandId ? existing : undefined))
      : matchingCommand(record, migrated, usedCommands, record.startedAt)
    const isOwned = record.ownerSessionId === sessionId
    const isGuess = record.ownerSessionId === UNKNOWN_OWNER && (existing?.isGuessed === true || command !== undefined)

    if (isOwned || isGuess) {
      const pendingEstimate = !usedPending && pending && now - pending.setAt <= CODEX_ESTIMATE_MS
        ? pending
        : undefined
      const reserved = record.reservation ? reservations[record.reservation] : undefined
      const group = command?.commandId ? found.find(w => w.commandId === command.commandId) : undefined
      // A run takes its command's estimate, or a sibling's from that command.
      const inherited = command?.commandEstimate ?? group?.commandEstimate
      const inheritedFirst = command?.commandFirstEstimate ?? group?.commandFirstEstimate
      const commandPending = command ? pendingEstimate : undefined
      const resumedEstimate = resumedCommand ? (inherited ?? pendingEstimate) : undefined
      const resumed: Work | undefined = existing && resumedCommand
        ? {
          ...existing,
          commandId: resumedCommand.commandId,
          commandStartedAt: resumedCommand.commandStartedAt,
          commandEstimate: resumedEstimate,
          commandFirstEstimate: resumedEstimate,
          estimate: resumedEstimate,
          firstEstimate: resumedEstimate,
        }
        : undefined
      const base: Work = resumed ?? existing ?? {
        maker: 'codex',
        id: record.recordId ?? '',
        sessionId,
        startedAt: record.startedAt,
        steps: 0,
        tokens: 0,
        weight: 0,
        commandId: command?.commandId,
        commandStartedAt: command?.commandStartedAt,
        canMatchRecord: command?.canMatchRecord,
        commandEstimate: inherited ?? commandPending,
        commandFirstEstimate: inheritedFirst ?? commandPending,
        estimate: inherited ?? pendingEstimate,
        firstEstimate: inheritedFirst ?? pendingEstimate,
      }
      const estimated = reserved ? addEstimate(base, reserved) : base

      found.push(recordWork(record, estimated, pace))
      if (reserved && record.reservation) {
        usedReservations.push(record.reservation)
      }
      if ((!existing || resumedCommand) && !inherited && pendingEstimate) {
        usedPending = true
      }
      if (command?.commandId) {
        usedCommands.add(command.commandId)
      }
    }
  }
  const seen: SeenCodexRecords = {
    records,
    foundIds: new Set(found.map(w => w.recordId)),
    usedCommands,
    sessionId,
    isReadFailed: state.isProcessReadFailed === true,
  }
  const kept = migrated.flatMap(w => keptCodexWork(w, seen, now))

  return { works: [...kept, ...found], usedPending, usedReservations }
}

// What becomes of a work the records did not settle this time. One a record
// now stands for, or another session owns, goes. A recordless one goes, or
// closes, once its wait ends. An open run whose record the reader no longer
// lists is unknown while reads fail and stopped once a read succeeds; a run
// already closed stays as it was, whatever the read.
function keptCodexWork(work: Work, seen: SeenCodexRecords, now: number): Work[] {
  const hasRecord = work.recordId !== undefined
  const record = hasRecord ? seen.records.find(r => r.recordId === work.recordId) : undefined
  const isOtherOwner = record?.ownerSessionId !== undefined && record.ownerSessionId !== seen.sessionId &&
    record.ownerSessionId !== UNKNOWN_OWNER
  const isResolved = !hasRecord && work.commandId !== undefined && seen.usedCommands.has(work.commandId)
  const isReplaced = hasRecord && seen.foundIds.has(work.recordId)
  const isRecordlessDue = !hasRecord && (work.state !== undefined || work.finishedAt === undefined) &&
    now >= codexWorkExpiresAt(work)

  if (work.maker !== 'codex') {
    return [work]
  } else if (isReplaced || isOtherOwner || isResolved) {
    return []
  } else if (isRecordlessDue) {
    return work.state === 'starting' || work.commandId !== undefined ? [] : [unknownCodexWork(work)]
  } else if (hasRecord && isCodexWorkOpen(work, now)) {
    return seen.isReadFailed
      ? [unknownCodexWork(work)]
      : [{ ...work, state: 'stopped', previousState: work.state, finishedAt: undefined, expected: undefined }]
  } else {
    return [work]
  }
}

function hasOpenWork(list: readonly Work[], now: number): boolean {
  return list.some(w => w.maker === 'codex' ? isCodexWorkOpen(w, now) : w.finishedAt === undefined)
}

function keepOwnWorks(list: readonly Work[], now: number): Work[] {
  const closed = list.filter(w => !hasOpenWork([w], now)).slice(-KEEP_OWN_WORKS)
  const kept = new Set(closed)

  return list.filter(w => kept.has(w) || hasOpenWork([w], now))
}

// Remember only the committed value, so a failed write cannot change which
// timers remain awake. The host still owns the value across reloads.
async function writeWorks(
  $: EngineInterface,
  rememberWorks: WorkObserver,
  change: (list: Work[]) => Work[],
): Promise<void> {
  const saved = await update($, works, change)

  rememberWorks(saved)
}

async function startClaudeWork(
  $: EngineInterface,
  turnId: string,
  now: number,
  rememberWorks: WorkObserver,
): Promise<void> {
  try {
    const work: Work = {
      maker: 'claude',
      id: turnId,
      sessionId: await $.session.id(),
      startedAt: now,
      startContext: (await read($, live)).contextTokens,
      steps: 0,
      tokens: 0,
      weight: 0,
    }

    await writeWorks($, rememberWorks, list => keepOwnWorks([...list, work], now))
  } catch {
    // This turn goes unrecorded.
  }
}

function addStep(work: Work, usage: Usage): Work {
  return {
    ...work,
    steps: work.steps + 1,
    tokens: work.tokens + readTokens(usage) + usage.output_tokens,
    weight: work.weight + weightOf(usage),
  }
}

// Each step of the turn adds to its work: one more step, its tokens and weight.
async function addClaudeStep(
  $: EngineInterface,
  turnId: string,
  usage: Usage,
  rememberWorks: WorkObserver,
): Promise<void> {
  try {
    const add = (work: Work): Work => work.maker === 'claude' && work.id === turnId ? addStep(work, usage) : work

    await writeWorks($, rememberWorks, list => list.map(add))
  } catch {
    // The turn's end reports its totals.
  }
}

// The turn's work ends with the turn, keeping what its estimate came to then;
// its rise is the 5-hour window's, other sessions' use included.
async function finishClaudeWork(
  $: EngineInterface,
  turnId: string,
  now: number,
  finished: Turn | undefined,
  isAborted: boolean,
  rememberWorks: WorkObserver,
): Promise<void> {
  try {
    const pace = workPace(await read($, workHistory), await read($, limitHistory), 'claude')
    const startPct = finished?.startPct
    const endPct = finished?.endPct
    const rise = startPct !== undefined && endPct !== undefined ? endPct - startPct : undefined
    const end = (w: Work): Work => {
      const ended = { ...withTurnTotals(w, finished), finishedAt: now, rise, isAborted }

      return { ...ended, expected: expectedLoad(ended, pace) }
    }

    await writeWorks($, rememberWorks, list =>
      list.map(w => (w.maker === 'claude' && w.id === turnId && w.finishedAt === undefined ? end(w) : w)),
    )
  } catch {
    // The work stays open until the session starts again.
  }
}

// A turn whose steps reported no usage takes the totals its end reported.
function withTurnTotals(work: Work, finished: Turn | undefined): Work {
  if (work.steps === 0 && finished) {
    return {
      ...work,
      steps: finished.steps ?? 0,
      tokens: (finished.read ?? 0) + (finished.write ?? 0),
      weight: finished.weight ?? 0,
    }
  } else {
    return work
  }
}

function estimateCodexWork(list: readonly Work[], reservation: string | undefined): Work | undefined {
  const candidates = list.filter(w => w.maker === 'codex' && (reservation
    ? w.reservation === reservation
    : isCodexWorkActive(w) && !w.estimate && w.canMatchRecord !== false))

  return candidates.length === 1 ? candidates[0] : undefined
}

function isWorkOf(work: Work, maker: WorkMaker, id: string): boolean {
  return work.maker === maker && work.id === id
}

// Records the estimate the tool was given: Claude's on the turn at work, with
// the conversation's length now; Codex's on the run at work, or else until
// the next codex exec starts.
async function recordEstimate(
  $: EngineInterface,
  input: EstimateInput,
  rememberWorks: WorkObserver,
): Promise<string | undefined> {
  const now = await $.clock.now()
  const estimate: WorkEstimate = {
    minutes: input.minutes,
    steps: input.steps,
    setAt: now,
    dueAt: now + input.minutes * MINUTE_MS,
    contextTokens: input.maker === 'claude' ? (await read($, live)).contextTokens : undefined,
  }
  const list = await read($, works)
  const target = input.maker === 'claude' ? runningClaudeWork(list) : estimateCodexWork(list, input.reservation)
  const due = local(estimate.dueAt, await read($, tzOffset)).hm

  if (target) {
    const attach = (work: Work): Work => isWorkOf(work, target.maker, target.id) ? addEstimate(work, estimate) : work

    await writeWorks($, rememberWorks, all => all.map(attach))
    return `${WORK_NAME[target.maker]} 작업을 ${due} 끝 예정으로 기록했어요.`
  } else if (input.maker === 'codex' && input.reservation) {
    const reservation = input.reservation

    await update($, reservedCodexEstimates, all => ({ ...all, [reservation]: estimate }))
    return `예약 ${reservation}의 Codex 작업을 ${due} 끝 예정으로 기록했어요.`
  } else if (input.maker === 'codex') {
    await update($, pendingCodexEstimate, () => estimate)
    return `다음 Codex 작업을 ${due} 끝 예정으로 기록했어요.`
  } else {
    return undefined
  }
}

function addEstimate(work: Work, estimate: WorkEstimate): Work {
  return {
    ...work,
    estimate,
    firstEstimate: work.firstEstimate ?? estimate,
    commandEstimate: work.state === 'starting' ? estimate : work.commandEstimate,
    commandFirstEstimate: work.state === 'starting'
      ? (work.commandFirstEstimate ?? estimate)
      : work.commandFirstEstimate,
  }
}

// A codex exec run starts with the Codex estimate given within the last ten
// minutes, which it uses up. Says whether it had one; a failure counts as one,
// so the session is not told of an estimate the mod lost.
async function startCodexWork(
  $: EngineInterface,
  toolUseId: string,
  now: number,
  rememberWorks: WorkObserver,
): Promise<boolean> {
  try {
    const pending = await read($, pendingCodexEstimate)
    const estimate = pending && now - pending.setAt <= CODEX_ESTIMATE_MS ? pending : undefined
    const commandEstimate = estimate
      ? { ...estimate, setAt: now, dueAt: now + estimate.minutes * MINUTE_MS }
      : undefined
    const work: Work = {
      maker: 'codex',
      id: toolUseId,
      sessionId: await $.session.id(),
      startedAt: now,
      state: 'starting',
      commandId: toolUseId,
      commandStartedAt: now,
      canMatchRecord: true,
      estimate: commandEstimate,
      firstEstimate: estimate,
      commandEstimate,
      commandFirstEstimate: estimate,
      steps: 0,
      tokens: 0,
      weight: 0,
    }

    if (estimate) {
      await update($, pendingCodexEstimate, current => current?.setAt === estimate.setAt ? null : current)
    }
    await writeWorks($, rememberWorks, list => keepOwnWorks([...list, work], now))
    return estimate !== undefined
  } catch {
    return true
  }
}

// Whether the reply at work has no estimate and was not told so yet; marks it
// told, so a reload keeps it told. A failure tells nothing.
async function markEstimateTold($: EngineInterface, rememberWorks: WorkObserver): Promise<boolean> {
  try {
    const running = runningClaudeWork(await read($, works))
    const id = running?.id ?? ''
    const isDue = running !== undefined && running.estimate === undefined && running.isTold !== true

    if (isDue) {
      const markTold = (work: Work): Work => isWorkOf(work, 'claude', id) ? { ...work, isTold: true } : work

      await writeWorks($, rememberWorks, list => list.map(markTold))
    }
    return isDue
  } catch {
    return false
  }
}

// A command that was refused never ran: its work goes, and the estimate it
// took waits for the next run again.
async function dropCodexWork($: EngineInterface, toolUseId: string, rememberWorks: WorkObserver): Promise<void> {
  try {
    const dropped = (await read($, works)).find(w => isWorkOf(w, 'codex', toolUseId))

    await writeWorks($, rememberWorks, list => list.filter(w => !isWorkOf(w, 'codex', toolUseId)))
    if (dropped?.firstEstimate) {
      await update($, pendingCodexEstimate, () => dropped.firstEstimate ?? null)
    }
  } catch {
    // An unresolved placeholder is removed when its waiting period ends.
  }
}

// The result with one line after it that the model reads and the person does
// not; a refused or failed call stays as it was.
function withContext<R extends ToolCallResult>(ran: R, line: string): R {
  if (ran.deny === undefined && ran.isError !== true) {
    return { ...ran, context: [...(ran.context ?? []), line] }
  } else {
    return ran
  }
}

function minutesText(ms: number): string {
  const minutes = Math.round(ms / MINUTE_MS)

  return minutes < 1 ? '1분 미만' : `${minutes}분`
}

// A work's share of the week is often under a point: one decimal, and a share
// too small for it reads as under 0.1.
function percentText(percent: number): string {
  if (percent > 0 && percent < PERCENT_SHOWN_MIN) {
    return `<${PERCENT_SHOWN_MIN}`
  } else if (percent < 10) {
    return percent.toFixed(1).replace(/\.0$/, '')
  } else {
    return String(Math.round(percent))
  }
}

function signedShareText(actual: number, expected: number): string {
  const share = Math.round((actual / expected - 1) * 100)

  return share >= 0 ? `+${share}%` : `${share}%`
}

// When the work runs to or ended at, against when it was due: "14:25까지 8분"
// or past due while it runs, "14:22 끝" and how far off once it ended.
function timeParts(work: Work, now: number, offset: number): WorkPart[] {
  const estimate = work.estimate
  const end = work.finishedAt

  if (isCodexWorkUncertain(work)) {
    return [{ key: 'state', text: work.state === 'stopped' ? '멈춤' : '확인 불가' }]
  } else if (work.state === 'starting' && !estimate) {
    return [{ key: 'state', text: '시작 중' }]
  } else if (end !== undefined) {
    return [{ key: 'end', text: `${local(end, offset).hm} 끝`, isBold: true }, ...endGapPart(work, end)]
  } else if (estimate && estimate.dueAt >= now) {
    return [{ key: 'due', text: `${local(estimate.dueAt, offset).hm}까지 ${minutesText(estimate.dueAt - now)}` }]
  } else if (estimate) {
    return [
      { key: 'due', text: `예정 ${local(estimate.dueAt, offset).hm}`, isDim: true },
      { key: 'gap', text: ` ${minutesText(now - estimate.dueAt)} 지남`, color: LEVEL_KEY.warn },
    ]
  } else {
    return [{ key: 'took', text: `진행 ${minutesText(now - work.startedAt)}`, isDim: true }]
  }
}

// How far a finished work ended from its due time, colored by the estimate's
// own length; without an estimate, how long it took. Each part carries the
// space before it, as the line draws the parts side by side.
function endGapPart(work: Work, end: number): WorkPart[] {
  const estimate = work.estimate
  const late = estimate ? end - estimate.dueAt : 0
  const level = estimate ? overLevel(end - work.startedAt, estimate.dueAt - work.startedAt) : 'ok'

  if (estimate && late >= MINUTE_MS) {
    return [{ key: 'gap', text: ` ${minutesText(late)} 늦음`, color: LEVEL_KEY[level] }]
  } else if (estimate && late <= -MINUTE_MS) {
    return [{ key: 'gap', text: ` ${minutesText(-late)} 빠름`, color: LEVEL_KEY.ok }]
  } else if (estimate) {
    return [{ key: 'gap', text: ' 제때', color: LEVEL_KEY.ok }]
  } else {
    return [{ key: 'took', text: ` ${minutesText(end - work.startedAt)} 걸림`, isDim: true }]
  }
}

// The tokens so far against the estimate's, the steps while it runs, and how
// far past its tokens a finished work went.
function tokenParts(work: Work, actual: WorkLoad, expected: WorkLoad | undefined): WorkPart[] {
  const isDone = work.maker === 'codex' ? work.state === 'done' : work.finishedAt !== undefined
  const total: WorkPart = { key: 'tokens', text: tokens(actual.tokens) }
  const estimate: WorkPart[] = expected
    ? [{ key: 'expected', text: `/예상 ${tokens(expected.tokens)}`, isDim: true }]
    : []
  const steps: WorkPart[] = isDone ? [] : [{ key: 'steps', text: ` ×${work.steps}`, isDim: true }]
  const gap: WorkPart[] =
    isDone && expected
      ? [
          {
            key: 'token-gap',
            text: ` ${signedShareText(actual.tokens, expected.tokens)}`,
            color: LEVEL_KEY[overLevel(actual.tokens, expected.tokens)],
          },
        ]
      : []

  return [total, ...estimate, ...steps, ...gap]
}

// The work's share of the week so far, against the estimate's; none until the
// rate is known.
function percentParts(actual: WorkLoad, expected: WorkLoad | undefined): WorkPart[] {
  if (actual.percent !== undefined && expected?.percent !== undefined) {
    return [{ key: 'percent', text: `${percentText(actual.percent)}/${percentText(expected.percent)}%` }]
  } else if (actual.percent !== undefined) {
    return [{ key: 'percent', text: `${percentText(actual.percent)}%` }]
  } else {
    return []
  }
}

// A work's pieces as its usage line draws them, each after its own mark.
function workPieces(work: Work, pace: WorkPace, now: number, offset: number): WorkPieces {
  const actual = actualLoad(work, pace)
  const expected = isCodexWorkUncertain(work) ? undefined : work.expected ?? expectedLoad(work, pace)

  return {
    time: timeParts(work, now, offset),
    tokens: tokenParts(work, actual, expected),
    percent: percentParts(actual, expected),
  }
}

// One finished work in the details: each measure as actual/estimated, or the
// actual alone without an estimate.
function workHistoryText(work: Work, pace: WorkPace): string {
  const took = (work.finishedAt ?? work.startedAt) - work.startedAt
  const estimate = work.firstEstimate
  const actual = actualLoad(work, pace)
  const expected = work.expected
  const time = estimate ? `시간 ${minutesText(took)}/${minutesText(estimate.minutes * MINUTE_MS)}` : `시간 ${minutesText(took)}`
  const steps = estimate?.steps ? `단계 ${work.steps}/${estimate.steps}` : `단계 ${work.steps}`
  const tokenText = expected ? `토큰 ${tokens(actual.tokens)}/${tokens(expected.tokens)}` : `토큰 ${tokens(actual.tokens)}`
  const percent =
    actual.percent === undefined
      ? []
      : [
          expected?.percent === undefined
            ? `주간 ${percentText(actual.percent)}%`
            : `주간 ${percentText(actual.percent)}%/${percentText(expected.percent)}%`,
        ]

  return [time, steps, tokenText, ...percent].join(' · ')
}

// How far the finished work of the week went past its estimates, by the
// median of each measure: time, steps and tokens.
function workAverageText(list: readonly Work[]): string | undefined {
  const time = averageOver(list, w =>
    w.firstEstimate && w.finishedAt !== undefined ? [w.finishedAt - w.startedAt, w.firstEstimate.minutes * MINUTE_MS] : undefined,
  )
  const steps = averageOver(list, w => (w.firstEstimate?.steps ? [w.steps, w.firstEstimate.steps] : undefined))
  const tokenShare = averageOver(list, w => (w.expected ? [w.tokens, w.expected.tokens] : undefined))
  const signed = (share: number): string => `${share >= 0 ? '+' : ''}${Math.round(share * 100)}%`
  const parts = [
    time === undefined ? '' : `시간 ${signed(time)}`,
    steps === undefined ? '' : `단계 ${signed(steps)}`,
    tokenShare === undefined ? '' : `토큰 ${signed(tokenShare)}`,
  ].filter(Boolean)

  return parts.length > 0 ? `최근 7일, 예정보다 보통 ${parts.join(' · ')}` : undefined
}

// Without the tool the band still shows each work's start and end, so a
// failed registration leaves the rest of the start as it is.
async function registerEstimateTool($: EngineInterface): Promise<void> {
  try {
    await $.tool.register(ESTIMATE_SPEC)
  } catch {
    // The next start registers it again.
  }
}

export const register: Register = on => {
  // Set by session.start, which runs again on every reload.
  let recounter: (() => Promise<void>) | undefined
  let relocater: (() => Promise<void>) | undefined
  let rehelp: (() => Promise<void>) | undefined
  let codexReader: ((isServerRead: boolean) => Promise<void>) | undefined
  // Writes this session's work file, one write at a time, the last with all.
  let ownWorksSaver: (() => Promise<void>) | undefined
  let cacheReader: (() => Promise<void>) | undefined
  let timerOwner: TimerOwner | undefined
  let settleCodex: (() => Promise<void>) | undefined
  let ownWorks: readonly Work[] = []
  let worksVersion = 0
  const rememberWorks: WorkObserver = (list): void => {
    ownWorks = list
    worksVersion += 1
  }

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

    const relocation = runOneAtATime(async (): Promise<void> => {
      const found = await locate($).catch(() => null)

      await update($, where, () => found)
    })
    relocater = relocation.refresh
    await relocation.tick()

    // Each agent keeps the time it was first seen at work, for its age. A list
    // that fails keeps the agents last seen, so a session whose helpers still
    // work is never taken for idle.
    const listHelpers = async (): Promise<void> => {
      try {
        const all = await $.agent.list()
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

    // Every session takes the newest shared reading of the run records, and
    // the newest server reading made for its own folder. Each returns when the
    // reading it took was made, none when it took none. Until this copy of the
    // mod took a reading of the records, every tick asks for one.
    let hasLocalReading = false
    let localReadingAt: number | undefined
    let readingVersion = 0
    let localRecords: readonly CodexRecord[] = []
    let codexState: Codex | undefined
    let localRecordKey = '[]'
    let codexSessionId: string | undefined
    const readCodexLocal = async (since: number): Promise<number | undefined> => {
      try {
        const request = { name: SHARED_NAME.codexLocal, key: SHARED_ANY_FOLDER, since, arguments: [CODEX_MODE.local] }
        const shared = await readShared($, request)

        if (shared) {
          const before = codexState ?? await read($, codex)
          const merged = mergeCodexLocal(before, shared.output)

          hasLocalReading = true
          codexState = await update($, codex, () => merged)
          if (shared.at !== localReadingAt || before.isProcessReadFailed !== merged.isProcessReadFailed) {
            localReadingAt = shared.at
            localRecords = merged.records ?? []
            if (localRecords.length > 0) {
              codexSessionId = codexSessionId ?? await $.session.id()
            }
            const isGuessable = (record: CodexRecord): boolean => record.ownerSessionId === UNKNOWN_OWNER &&
              matchingCommand(record, ownWorks, new Set(), record.turnStartedAt ?? record.startedAt) !== undefined
            const relevant = localRecords.filter(record => record.ownerSessionId === codexSessionId ||
              ownWorks.some(work => work.recordId === record.recordId) || isGuessable(record))
            const key = JSON.stringify(relevant)

            const isFailureChanged = relevant.length > 0 && before.isProcessReadFailed !== merged.isProcessReadFailed

            if (key !== localRecordKey || isFailureChanged) {
              localRecordKey = key
              readingVersion += 1
              await settleCodex?.()
            }
          }
          await alertLimits($, merged.limits, CODEX_NAME)
        }
        return shared?.at
      } catch {
        codexState = await recordCodexReadFailure($, rememberWorks)
      }
    }
    // The folder is read at each call: a session's folder can change.
    const readCodexServer = async (since: number): Promise<void> => {
      try {
        const cwd = await $.session.cwd()
        const request = { name: SHARED_NAME.codexServer, key: cwd, since, arguments: [CODEX_MODE.server, cwd] }
        const shared = await readShared($, request)

        if (shared) {
          const merged = mergeCodexServer(codexState ?? await read($, codex), shared)

          codexState = await update($, codex, () => merged)
          await alertLimits($, merged.limits, CODEX_NAME)
        }
      } catch {
        codexState = await recordCodexServerFailure($)
      }
    }
    // A Codex run started here: read since now, and ask the server too once
    // a foreground command returns; that return says nothing about its runs.
    const readCodex = async (isServerRead: boolean): Promise<void> => {
      try {
        const now = await $.clock.now()

        await readCodexLocal(now)
        if (isServerRead) {
          await readCodexServer(now)
        }
      } catch {
        codexState = await recordCodexReadFailure($, rememberWorks)
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
        const isLocalDue = before.running > 0 || before.isProcessReadFailed === true || isRecordWritten ||
          !hasLocalReading || ownWorks.some(w => isCodexWorkOpen(w, now))

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
        codexState = await recordCodexReadFailure($, rememberWorks)
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
        codexState = await recordCodexReadFailure($, rememberWorks)
      }
    }
    codexReader = readCodex
    if (isCodexServerDue(await read($, codex), await $.clock.now())) {
      void readCodexOnStart()
    }

    // The estimate tool, and the work every session here finished with the
    // weekly limits' steps, read again each minute after this session adds
    // the steps it saw. A session that starts anew drops a reply its last run
    // left open: that turn never ended.
    await registerEstimateTool($)
    if (!isReload) {
      await writeWorks($, rememberWorks, list => list.filter(w => w.maker !== 'claude' || w.finishedAt !== undefined))
      await update($, turns, list => list.map(t => (t.isRunning ? { ...t, isRunning: false } : t)))
    }
    ownWorks = await read($, works)
    worksVersion += 1
    const ownWorksSave = runOneAtATime(() => saveOwnWorks($))
    ownWorksSaver = ownWorksSave.refresh
    const refreshWorkHistory = async (): Promise<void> => {
      try {
        if (await recordLimitSteps($)) {
          await ownWorksSave.refresh()
        }
        const found = await readWorkHistory($)

        await update($, workHistory, () => found.works)
        await update($, limitHistory, () => found.limitSteps)
      } catch {
        // The next minute reads them again.
      }
    }
    const workHistoryRead = runOneAtATime(refreshWorkHistory)
    await workHistoryRead.tick()

    // State writes and new readings make settlement due. An unchanged timer
    // does no host calls; recordless work gets one deadline of its own.
    let settledWorksVersion = -1
    let settledReadingVersion = -1
    let expiryTimer: Timer | undefined
    let isExpiryDue = false
    const settleOpenCodexWorks = async (): Promise<void> => {
      try {
        const isChanged = worksVersion !== settledWorksVersion ||
          readingVersion !== settledReadingVersion || isExpiryDue

        if (isChanged) {
          settledWorksVersion = worksVersion
          settledReadingVersion = readingVersion
          isExpiryDue = false
          expiryTimer?.cancel()
          if (localRecords.length > 0 || ownWorks.some(w => w.maker === 'codex')) {
            const state = codexState ?? await read($, codex)
            const now = await $.clock.now()
            const pace = workPace(await read($, workHistory), await read($, limitHistory), 'codex')
            const owner = codexSessionId ?? await $.session.id()
            const pending = await read($, pendingCodexEstimate)
            const reservations = await read($, reservedCodexEstimates)
            let settled = settleCodexWorks(ownWorks, state, pace, now, owner, pending, reservations)
            const kept = keepOwnWorks(settled.works, now)
            const isWorkChanged = JSON.stringify(ownWorks) !== JSON.stringify(kept)

            const settleList = (list: Work[]): Work[] => {
              settled = settleCodexWorks(list, state, pace, now, owner, pending, reservations)
              return keepOwnWorks(settled.works, now)
            }

            codexSessionId = owner
            if (isWorkChanged) {
              await writeWorks($, rememberWorks, settleList)
              await ownWorksSave.refresh()
            }
            if (settled.usedPending) {
              await update($, pendingCodexEstimate, current => current?.setAt === pending?.setAt ? null : current)
            }
            if (settled.usedReservations.length > 0) {
              const used = settled.usedReservations
              const isUnused = ([id, estimate]: [string, WorkEstimate]): boolean =>
                !used.includes(id) || estimate.setAt !== reservations[id]?.setAt
              const keepUnused = (current: CodexReservations): CodexReservations =>
                Object.fromEntries(Object.entries(current).filter(isUnused))

              await update($, reservedCodexEstimates, keepUnused)
            }
            const expiresAt = Math.min(...ownWorks.filter(w => w.maker === 'codex' && !w.recordId)
              .map(codexWorkExpiresAt).filter(at => at > now))

            if (Number.isFinite(expiresAt)) {
              expiryTimer = $.clock.after(expiresAt - now, () => void expireCodexWork())
            }
          }
        }
      } catch {
        settledWorksVersion = -1
        // The next reading settles them.
      }
    }
    const codexWorkSettle = runOneAtATime(settleOpenCodexWorks)
    const expireCodexWork = async (): Promise<void> => {
      try {
        isExpiryDue = true
        await codexWorkSettle.refresh()
        await update($, workTick, n => n + 1)
      } catch {
        // A later work change or reading retries settlement.
      }
    }

    settleCodex = codexWorkSettle.refresh
    await settleCodex()
    const tickWork = async (): Promise<void> => {
      try {
        if (hasOpenWork(ownWorks, await $.clock.now())) {
          await update($, workTick, n => n + 1)
        }
      } catch {
        // The next tick redraws.
      }
    }

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

    const sessionCount = runOneAtATime(sharedSessionCounter($, home))
    void sessionCount.tick()

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

    const tickTurn = async (): Promise<void> => {
      try {
        if ((await read($, turns)).some(t => t.isRunning)) {
          await update($, tick, n => n + 1)
        }
      } catch {
        // The next second redraws.
      }
    }
    // A reply, a busy helper or open work keeps the session at work.
    const isSessionBusy = async (): Promise<boolean> => {
      const now = await $.clock.now()
      const isTurnRunning = (await read($, turns)).some(t => t.isRunning)
      const isHelperBusy = (await read($, helpers)).length > 0

      return isTurnRunning || isHelperBusy || hasOpenWork(ownWorks, now)
    }
    // The timers left out while idle redraw or read what only changes while
    // the session works: its reply, helpers, folder and own runs.
    const bandTimers: BandTimer[] = [
      { busyMs: RECOUNT_CHECK_MS, idleMs: RECOUNT_CHECK_MS, ...plainRuns(() => void recountIfDue()) },
      { busyMs: WHERE_EVERY_MS, ...pacedRuns(relocation) },
      { busyMs: HELPERS_EVERY_MS, ...pacedRuns(helperList) },
      { busyMs: CODEX_TICK_MS, idleMs: CODEX_IDLE_TICK_MS, ...pacedRuns(codexTicks) },
      // The details may stay open on an idle session and show other sessions' work.
      { busyMs: WORK_EVERY_MS, idleMs: WORK_EVERY_MS, ...pacedRuns(workHistoryRead) },
      { busyMs: CODEX_TICK_MS, ...pacedRuns(codexWorkSettle) },
      { busyMs: WORK_TICK_MS, ...plainRuns(() => void tickWork()) },
      { busyMs: CLAUDE_SHARE_EVERY_MS, idleMs: CLAUDE_SHARE_IDLE_EVERY_MS, ...pacedRuns(claudeShare) },
      { busyMs: SESSIONS_EVERY_MS, idleMs: SESSIONS_EVERY_MS, ...pacedRuns(sessionCount) },
      { busyMs: CACHE_TICK_MS, idleMs: CACHE_TICK_MS, ...plainRuns(() => void tickCache()) },
      { busyMs: TURN_TICK_MS, ...plainRuns(() => void tickTurn()) },
    ]
    timerOwner = await startPacedTimers($, bandTimers, isSessionBusy)

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
    await openDetails($, timerOwner)
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
    const place = await read($, where)
    const busy = await read($, helpers)
    const codexState = await read($, codex)
    const heavy = await read($, note)
    const ownWorks = await read($, works)
    const pastWorks = await read($, workHistory)
    const pastSteps = await read($, limitHistory)
    await read($, workTick)
    const counts = await read($, sessions)
    const sessionParts = SESSION_PARTS.map(part => ({ ...part, count: counts?.[part.state] ?? 0 })).filter(
      part => part.count > 0,
    )
    const cached = cacheView(await read($, cache), state.contextTokens, now, offset)

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
    const slot = (key: string, child: RenderChildren) => (
      <Box key={key} flexDirection="row" alignItems="center" alignSelf="center" gap="xs">
        {child}
      </Box>
    )

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

    // A piece's mark; the terminal draws no Svg, so a dot stands in for it.
    const pieceMark = (source: string, name: string) =>
      Svg ? (
        <Svg source={source} alt={name} width={11} height={11} />
      ) : (
        <Text size="caption" dimColor>
          ·
        </Text>
      )
    const pieceText = (parts: readonly WorkPart[]) => (
      <Text size="caption">
        {parts.map(part => (
          <Text key={part.key} color={part.color} bold={part.isBold} dimColor={part.isDim}>
            {part.text}
          </Text>
        ))}
      </Text>
    )

    // The newest work this session did with the line's tool takes the place
    // the reply's tokens had: a spinner while it runs or a check once done,
    // then when it ends, its tokens and its share of the week, each after its
    // mark. The tokens in and out stay in the details.
    const paces = {
      claude: workPace(pastWorks, pastSteps, 'claude'),
      codex: workPace(pastWorks, pastSteps, 'codex'),
    }
    const workBox = (maker: WorkMaker, work: Work, isAllDone?: boolean) => {
      const pieces = workPieces(work, paces[maker], now, offset)
      const hasShare = pieces.percent.length > 0
      const isDone = isAllDone ?? work.finishedAt !== undefined
      const isWorking = isCodexWorkActive(work) || (maker === 'claude' && work.finishedAt === undefined)

      return (
        <Box key={`work-${maker}`} flexDirection="row" alignItems="center" alignSelf="center" gap="xs">
          {isWorking ? (
            spinner()
          ) : isDone ? (
            <Text size="caption" color={LEVEL_KEY.ok}>
              ✓
            </Text>
          ) : null}
          {pieceMark(STOPWATCH_SVG, 'ends')}
          {pieceText(pieces.time)}
          {pieceMark(TOKENS_SVG, 'tokens')}
          {pieceText(pieces.tokens)}
          {hasShare ? pieceMark(DIAL_SVG, 'share of the week') : null}
          {hasShare ? pieceText(pieces.percent) : null}
        </Box>
      )
    }
    const workItem = (maker: WorkMaker) => {
      const work = latestWork(ownWorks, maker)

      return work ? workBox(maker, work) : null
    }

    // The details open from a chevron at the end of each usage line. The
    // mark is the label itself: a Button holds no image, and the desktop app
    // draws a Button's label rather than its children.
    const pressDetails = (): void => void openDetails($, timerOwner)
    const detailsButton = (key: string) =>
      slot(key, <Button key={key} label={DETAILS_GLYPH} plain dimColor onPress={pressDetails} />)

    // The chevron keeps close to the piece before it: the line's gap between
    // pieces, on top of the desktop button's own padding, set it far apart.
    const lineEnd = (key: string, last: RenderChildren, detailsKey: string) => (
      <Box key={key} flexDirection="row" alignItems="center" alignSelf="center" gap="xs">
        {last}
        {detailsButton(detailsKey)}
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

    // The line keeps what is worth a glance: the limits, the context and this
    // session's latest work. The cost, the week's reset and the context's size
    // wait in the details. A reading older than the stale mark is grey and
    // says when it was read, in place of its reset and its pace.
    const limitItems = (prefix: string, limits: readonly Limit[], at: number | undefined) => {
      const isOld = isReadingOld(at, now)
      const asOf = at === undefined ? '' : local(at, offset).hm
      const afterText = (l: Limit): string => (l.kind === WEEK_LIMIT ? '' : `↻${resetAt(l.resetsAt, now, offset)}`)

      return limits.map(l => {
        const p = isOld ? undefined : pace(l, now)
        const full = p?.fullAt !== undefined ? local(p.fullAt, offset).hm : ''
        const after = isOld ? `·${asOf} 기준` : afterText(l)

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

    // The run due last stands for the others, which the line only counts.
    const shown = shownCodexWork(ownWorks, now)
    const openCodexWorks = ownWorks.filter(w => isCodexWorkOpen(w, now))
    const activeCodexWorks = openCodexWorks.filter(isCodexWorkActive)
    const isAllCodexDone = openCodexWorks.length === 0 && shown?.state !== 'stopped' && shown?.state !== 'unknown'
    // At the end of the line, as the app puts the model under the prompt: the
    // setting of the run at work, or with none at work, the setting the next
    // run takes, greyed with its time once old.
    const workSetting = shown ? runSetting(shown) : undefined
    const codexSetting = shown ? workSetting : codexState.configured
    const isCodexSettingOld = workSetting === undefined && isReadingOld(codexState.configuredAt, now)
    const codexSettingAsOf = codexState.configuredAt === undefined ? '' : local(codexState.configuredAt, offset).hm
    const codexWorkItem = shown ? (
      <Box key="codex-summary" flexDirection="row" flexWrap="wrap" alignItems="center" gap="xs">
        {workBox('codex', shown, isAllCodexDone)}
        {activeCodexWorks.length > 1 ? (
          <Text size="caption">{` 외 ${activeCodexWorks.length - 1}개`}</Text>
        ) : null}
        {shown.state !== 'unknown' && openCodexWorks.some(w => w.state === 'unknown') ? (
          <Text size="caption"> · 확인 불가 포함</Text>
        ) : null}
        {ownWorks.some(w => w.maker === 'codex' && w.state === 'stopped') && shown.state !== 'stopped' ? (
          <Text size="caption"> · 멈춤 포함</Text>
        ) : null}
        {openCodexWorks.some(w => !w.estimate) ? <Text size="caption"> · 예정 모름 포함</Text> : null}
        {shown.isGuessed ? <Text size="caption" dimColor> · 추정</Text> : null}
      </Box>
    ) : null
    const codexSettingItem = codexSetting ? (
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
    ) : null
    // A run this session started shows even before any Codex reading came.
    const codexLine = hasCodex(codexState) || latestWork(ownWorks, 'codex') ? (
      <Box key="codex" flexDirection="row" flexWrap="wrap" alignItems="center" columnGap="md">
        {mark('codex-mark', CODEX_SVG, CODEX_NAME)}
        {limitItems('codex-', codexState.limits, codexState.limitsAt)}
        {codexSettingItem ? codexWorkItem : null}
        {lineEnd('codex-end', codexSettingItem ?? codexWorkItem, 'codex-details')}
      </Box>
    ) : null
    const claudeWorkItem = workItem('claude')
    const contextItem = item('context', state.contextPercent ?? 0, 'Context', '', contextLevel(state.contextPercent ?? 0))
    const usageLine = (
      <Box key="usage" flexDirection="row" flexWrap="wrap" alignItems="center" columnGap="md">
        {mark('claude-mark', CLAUDE_SVG, CLAUDE_NAME)}
        {limitItems('', state.limits, state.limitsAt)}
        {claudeWorkItem ? contextItem : null}
        {lineEnd('usage-end', claudeWorkItem ?? contextItem, 'details')}
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
    await timerOwner?.markActive()
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
      await startClaudeWork($, e.turnId, now, rememberWorks)
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
      await addClaudeStep($, e.turnId, u, rememberWorks)
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
                liveRead: (t.liveRead ?? 0) + readTokens(u),
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

  // Only unquoted invocations leave a placeholder. Other mentions just wake
  // the reader and cannot borrow records or consume an estimate.
  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    const isCodexRun = e.tool === 'Bash' && CODEX_RUN.test(e.command)
    const unquoted = e.tool === 'Bash' ? e.command.replace(/'[^']*'|"(?:\\.|[^"\\])*"/g, '""') : ''
    const isCodexWork = isCodexRun && CODEX_INVOCATION.test(unquoted)
    const isForeground = e.tool === 'Bash' && e.run_in_background !== true

    // A run this session starts is work: the band wakes for it.
    if (isCodexWork) {
      await timerOwner?.markActive()
    }
    const hasEstimate = isCodexWork
      ? await startCodexWork($, e.tool_use_id, await $.clock.now(), rememberWorks)
      : true
    const ran = await next(e)

    if (isCodexWork && ran.deny !== undefined) {
      await dropCodexWork($, e.tool_use_id, rememberWorks)
    }
    if (isCodexRun) {
      void codexReader?.(isForeground)
    }
    return hasEstimate || !isCodexWork ? ran : withContext(ran, NO_CODEX_ESTIMATE)
  })

  on('tool.call', { tool: ESTIMATE_TOOL_NAME }, async ($, e) => {
    const checked = checkEstimateInput(e)
    const recorded = checked.input ? await recordEstimate($, checked.input, rememberWorks) : undefined

    if (checked.error !== undefined) {
      return { deny: checked.error }
    } else if (recorded === undefined) {
      return { deny: '지금 이 세션에서 돌고 있는 답이 없어 Claude 추정을 붙일 곳이 없어요.' }
    } else {
      return { result: recorded }
    }
  })

  // A multi-step task starts with its plan: without an estimate yet, the
  // session hears so once for the turn.
  on('tool.call', { tool: PLAN_TOOL_NAME }, async ($, e, next) => {
    const ran = await next(e)
    const isTelling = Array.isArray(e.items) && (await markEstimateTold($, rememberWorks))

    return isTelling ? withContext(ran, NO_CLAUDE_ESTIMATE) : ran
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
    await timerOwner?.markActive()
    const spawned = await next(e)
    await update($, turns, list => list.map(t => (t.isRunning ? { ...t, helpersSpawned: (t.helpersSpawned ?? 0) + 1 } : t)))
    void rehelp?.()

    return spawned
  })

  // A reply's end starts the idle minute over.
  on('turn.complete', async ($, e, next) => {
    await timerOwner?.markActive()
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
            read: u ? readTokens(u) : t.liveRead,
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
      await finishClaudeWork($, e.turnId, now, finished, e.isAborted, rememberWorks)
      await ownWorksSaver?.()
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
    const pastWorks = await read($, workHistory)
    const pastSteps = await read($, limitHistory)
    const recentWorks = [...pastWorks].reverse().slice(0, SHOWN_HISTORY)
    const averageText = workAverageText(pastWorks.filter(w => w.firstEstimate !== undefined))

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
            <Box key={`codex-work-${work.recordId ?? work.startedAt}`} flexDirection="column" marginTop={1}>
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

        {heading('WORK TIMES')}
        <Text size="caption" dimColor>
          %는 그 작업이 주간 사용량을 얼마나 썼는지예요. 다른 기기에서 쓴 양이 섞이면 조금 높게 잡혀요.
        </Text>
        {recentWorks.length === 0 ? (
          <Text size="footnote" dimColor>
            작업이 끝나면 예정과 실제를 여기에 쌓아요.
          </Text>
        ) : (
          recentWorks.map(w => (
            <Box key={`${w.sessionId}-${w.id}`} flexDirection="column" marginTop={1}>
              <Text size="footnote">
                <Text dimColor tabular>
                  {local(w.startedAt, offset).hm}
                </Text>{' '}
                <Text bold>{WORK_NAME[w.maker]}</Text>
              </Text>
              <Text size="caption" dimColor>
                {workHistoryText(w, workPace(pastWorks, pastSteps, w.maker))}
              </Text>
            </Box>
          ))
        )}
        {averageText ? (
          <Box marginTop={1}>
            <Text size="caption">{averageText}</Text>
          </Box>
        ) : null}

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
