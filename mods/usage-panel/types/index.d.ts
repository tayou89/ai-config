export type Limit = { kind: string; percentUsed: number; resetsAt?: string }

export type Live = {
  limits: Limit[]
  // When a reply last reported the limits, here or in another session; none
  // for the figures the engine had at start.
  limitsAt?: number
  contextTokens?: number
  contextWindow: number
  contextPercent?: number
  costUsd?: number
}

export type Turn = {
  id: string
  prompt: string
  startedAt: number
  startPct?: number
  isRunning: boolean
  durationMs?: number
  read?: number
  write?: number
  // Filled while the turn runs, from each step's usage.
  liveRead?: number
  liveWrite?: number
  liveWeight?: number
  // The model and effort the turn's first step ran with.
  model?: string
  effort?: string
  answerChars?: number
  toolInputChars?: number
  steps?: number
  // Filled when the turn ends.
  endPct?: number
  weight?: number
  cacheWrite?: number
  biggestResult?: number
  helpersSpawned?: number
  level?: Level
}

export type Level = 'ok' | 'warn' | 'high'

export type InsightKind = 'heavy' | 'effort'

export type Insight = {
  turnId: string
  at: number
  level: Level
  kind?: InsightKind
  short: string
  detail: string
  metrics: string
}

export type Tally = { read: number; write: number; weight: number }
export type Day = Tally & { date: string; replies: number; sessions: number }
export type Named = Tally & { name: string }
export type Conversation = Tally & { id: string; title: string; project: string; last: string }

export type History = {
  generatedAt: string
  total: number
  days: Day[]
  projects: Named[]
  models: Named[]
  sessions: Conversation[]
}

export type Recount = { isRunning: boolean; error?: string }

export type Tab = 'now' | 'week'

export type Segment = { text: string; isAnchor: boolean }

export type Helper = { id: string; type: string; description: string; status: string; seenAt: number }

export type Where = {
  segments: Segment[]
  isGit: boolean
  branch?: string
  ahead: number
  behind: number
  staged: number
  unstaged: number
  untracked: number
  conflicted: number
  stashes: number
}

// One reading of a maker's limits and when it was made.
export type Reading = { at: number; limits: Limit[] }

// One reading of Codex's limits: from its server, or from the newest record a Codex run wrote.
export type CodexReading = Reading & { plan?: string }

// What a `codex exec` run's record says of it so far: its totals, the setting
// its last turn started with, and how far its tighter window rose.
export type CodexTotals = {
  read: number
  cached: number
  write: number
  model?: string
  effort?: string
  folder: string
  rise?: number
}

// A finished `codex exec` run, totals for the whole run.
export type CodexRun = CodexTotals & { finishedAt: number }

// A `codex exec` run still at work, its totals up to its newest step.
export type CodexWork = CodexTotals & { startedAt: number; steps: number }

// The model and reasoning effort a Codex run works with, as Codex spells them.
// No `effort` when Codex leaves it to the model's own default.
export type CodexSetting = { model: string; effort?: string }

// What the reader script reads from the run records alone.
export type CodexLocalOutput = {
  snapshot?: CodexReading
  last?: CodexRun
  // The runs at work that keep a record, the most recently written first.
  working: CodexWork[]
  // Every `codex exec` at work, with or without a record.
  running: number
}

// What the reader script asks Codex's server, for one folder.
export type CodexServerOutput = {
  limits?: CodexReading
  // The setting a new `codex exec` in this folder starts with.
  configured?: CodexSetting
  error?: string
}

// One reading the sessions share, when it was made and what it said.
export type Shared<T> = { at: number; output: T }

// A file of shared readings, one per key: the folder a server reading was made
// for, or none (the empty key) for a reading that is the same in every folder.
export type SharedFile<T> = { entries?: Record<string, Shared<T>> }

export type Codex = {
  limits: Limit[]
  limitsAt?: number
  plan?: string
  serverAt?: number
  last?: CodexRun
  working: CodexWork[]
  configured?: CodexSetting
  configuredAt?: number
  running: number
  error?: string
}

// One row of the app's own session list (`list_sessions`), the fields counted.
export type ListedSession = { sessionId: string; isRunning: boolean; lastActivityAt?: string; startedBy?: string }
// Which sessions the app's list returns: this session's family only, and archived ones too.
export type SessionListFilter = { linked?: boolean; include_archived?: boolean }

// How the sidebar reads a session's last turn: the app's summary of it, which
// the turn's own wrap-up overrides.
export type Verdict = 'needs-input' | 'failed' | 'other'

// What the sessions reader is asked: the store of unread sessions when its
// files changed, each session whose state file changed, and whether to look in
// its transcript for a question still open. Each carries the file's save time
// and the activity stamp it is asked at, kept with what is read.
export type SessionRequest = {
  id: string
  file: string
  savedAt: number
  activityAt?: string
  readsQuestion: boolean
}
export type SessionsRequest = { store?: string; sessions: SessionRequest[] }

export type SessionRead = { verdict: Verdict; isAsking: boolean }

// `unread` only when the store was asked for, null when it could not be read.
export type SessionsOutput = { unread?: string[] | null; sessions: Record<string, SessionRead> }

// The other sessions in each of the sidebar's states: at work, waiting for the
// person (its yellow dot), finished and unread (its blue dot).
export type SessionCounts = { working: number; waiting: number; done: number }

export type CacheTtl = '1h' | '5m'

// What this session's transcript says of its last reply: when it came and how
// long the cache it wrote lives.
export type CacheOutput = { at?: number; ttl?: CacheTtl }

// The main conversation's prompt cache: renewed by each request, and replaced
// by a summary the engine made while the session sat idle.
export type Cache = { requestAt?: number; ttlMs: number; summaryAt?: number }

declare module 'claude-code' {
  interface PluginState {
    'usage-panel': {
      live: Shaped<Live>
      turns: Shaped<Turn[]>
      history: Shaped<History | null>
      recount: Shaped<Recount>
      tzOffset: Shaped<number>
      tab: Shaped<Tab>
      alerted: Shaped<string[]>
      where: Shaped<Where | null>
      helpers: Shaped<Helper[]>
      codex: Shaped<Codex>
      insights: Shaped<Insight[]>
      note: Shaped<Insight | null>
      tick: Shaped<number>
      loaded: Shaped<boolean>
      sessions: Shaped<SessionCounts | null>
      cache: Shaped<Cache>
      cacheTick: Shaped<number>
    }
  }
}
