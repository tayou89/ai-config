import { expect, mock, test } from 'claude-code/testing'
import type { On, SessionMeasureInput, TurnCompleteInput } from 'claude-code'
import type { Engine, MockClock } from 'claude-code/testing'
import type {
  CacheOutput,
  CodexLocalOutput,
  CodexRecord,
  ListedSession,
  SessionState,
  SessionStates,
  Work,
} from '../types'

type SessionRow = ListedSession & { isArchived: boolean }
// The band timers a test counts, each by a call only its tick makes.
type TimerKind = 'helpers' | 'places' | 'codex' | 'claude' | 'sessions'
// A drawn surface, as far as finding a Text by its text goes.
type TextFinder = { find: (query: { type: 'Text'; text: RegExp }) => Promise<{ text?: string } | undefined> }
interface CountedCall {
  kind: TimerKind
  at: number
}

// The mod's own tool, as its schema declares it: the session that laid the
// types had not registered it yet.
declare module 'claude-code' {
  interface McpToolInputs {
    'mcp__usage-panel__work_estimate': {
      maker: 'claude' | 'codex'
      minutes: number
      steps?: number
      reservation?: string
    }
  }
}

const PANE = {
  plugin: 'usage-panel',
  component: 'Pane',
  requestId: 'usage',
  props: {
    title: '사용량',
    isFocused: false,
    bodyColumns: 44,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 60 },
    view: {},
  },
} as const

const ABOVE = {
  plugin: 'usage-panel',
  component: 'AbovePrompt',
  requestId: 'above',
  props: {
    hasSurvey: false,
    isWorking: false,
    maxRows: 12,
    bodyColumns: 80,
    scroll: { offset: 0, bodyRows: 12 },
    view: {},
  },
} as const

const HISTORY = {
  generatedAt: '2026-11-04T16:14:03+09:00',
  total: 1000,
  days: [
    { date: '2026-11-04', read: 370_038_442, write: 1_396_148, weight: 700, replies: 1038, sessions: 5 },
    { date: '2026-11-03', read: 172_239_059, write: 627_987, weight: 300, replies: 612, sessions: 5 },
    { date: '2026-11-02', read: 0, write: 0, weight: 0, replies: 0, sessions: 0 },
  ],
  projects: [{ name: '또갈래', read: 1, write: 1, weight: 600 }],
  models: [],
  sessions: [{ id: 'abc', title: '디자인', project: '또갈래', read: 1, write: 1, weight: 400, last: '' }],
}

const codexReads: string[] = []
// When a Codex record was last written and the config saved; 0 is none there.
const codexDisk = { recordAt: 0, configAt: 0 }
const CODEX_HOME = '/Users/t/.codex/'
const CODEX_CONFIG = `${CODEX_HOME}config.toml`
// The Claude reading the sessions share, as its file holds it: none is no file.
const claudeShared: { text?: string } = {}
const CLAUDE_SHARED = '/Users/t/Library/Caches/usage-panel/claude-limits.json'
// The readings the sessions share, by file: what each file holds as text, and
// how the reader behaves this time: busy (another session reads) or held until
// released (a read still going).
const SHARED = '/Users/t/Library/Caches/usage-panel/'
const SESSION_STATES_FILE = '/session-states.json'
const sharedFiles: Record<string, string> = {}
const sharedReader = { isBusy: false, hold: undefined as Promise<void> | undefined, calls: 0 }
const LOCAL_KEYS = ['snapshot', 'last', 'working', 'finished', 'running', 'records', 'isProcessReadFailed']
const SERVER_KEYS = ['limits', 'configured', 'error']
// What the mod did at start: the 7-day counts it ran and the panes it closed.
const started = { recounts: 0, closed: [] as string[] }
// The app list and the shared reader's completed decisions are independent
// fixtures: Python tests cover how the decisions are made.
const app = {
  listed: [] as SessionRow[],
  output: { version: 1, at: 0, unread: [], sessions: {}, unreadable: [] } as SessionStates,
  requests: [] as ListedSession[][],
  transcript: {} as CacheOutput,
  hostId: undefined as string | undefined,
  own: {
    sessionId: 'local_self',
    title: 'Own session',
    cwd: '/fixture',
    isRunning: false,
    lastActivityAt: '2026-11-04T06:59:00Z',
    parentSessionId: 'local_parent',
  },
  selfCalls: 0,
  listCalls: [] as Record<string, unknown>[],
  sessionExit: 0,
  agentLists: 0,
  agentsHold: undefined as Promise<void> | undefined,
  isAgentListFailing: false,
  tools: [] as string[],
  workFiles: {} as Record<string, string>,
  currentWorks: [] as Work[],
  countedCalls: [] as CountedCall[],
  workReads: [] as number[],
  sessionIds: [] as number[],
}
const WORK_FOLDER = '/Users/t/Library/Application Support/usage-panel/work'

function codexRecord(name: string, startedAt: number, fields: Partial<CodexRecord> = {}): CodexRecord {
  return {
    recordId: `rollout-${name}.jsonl`,
    reservation: `reservation-${name}`,
    ownerSessionId: 'cli-1',
    state: 'running',
    startedAt,
    steps: 3,
    read: 1000,
    cached: 0,
    write: 0,
    model: `model-${name}`,
    effort: 'high',
    folder: '/',
    ...fields,
  }
}

function ownCodexWorks(): Work[] {
  return app.currentWorks.filter(w => w.maker === 'codex')
}

async function publishCodex(output: CodexLocalOutput, records: CodexRecord[], clock: MockClock): Promise<void> {
  output.records = records
  output.working = records.filter(r => r.state === 'running')
  output.finished = records.flatMap(r => r.state === 'done' && r.finishedAt !== undefined
    ? [{ ...r, finishedAt: r.finishedAt }]
    : [])
  output.running = output.working.length
  codexDisk.recordAt = clock.now()
  await clock.advance(10_001)
}

const claudeReading = (fiveHour: number): SessionMeasureInput => ({
  context: { tokens: 1, window: 1_000_000, percent: 15 },
  rateLimits: [
    { kind: 'five_hour', percentUsed: fiveHour, resetsAt: '2026-11-04T11:50:00Z' },
    { kind: 'seven_day', percentUsed: 17, resetsAt: '2026-11-08T20:00:00Z' },
  ],
  changed: ['rateLimits'],
})

const sharedReading = (at: string, fiveHour: number) =>
  JSON.stringify({ at: Date.parse(at), limits: claudeReading(fiveHour).rateLimits })

const pick = (output: object, keys: readonly string[]) =>
  Object.fromEntries(Object.entries(output).filter(([name]) => keys.includes(name)))

// What the shared reader does: under its lock, the reading kept for the key
// when made since `since`, else a new one written to the file.
const readShared = async (
  path: string,
  key: string,
  since: number,
  mode: string | undefined,
  codexOutput: object,
  now: number,
) => {
  const file = JSON.parse(sharedFiles[path] ?? '{}') as { entries?: Record<string, { at: number }> }
  const kept = file.entries?.[key]

  if (mode === 'local' || mode === 'server') {
    sharedReader.calls += 1
  }
  await sharedReader.hold
  if (!Number.isInteger(since)) {
    return { exitCode: 1, stdout: '', stderr: 'ValueError: since is not whole milliseconds' }
  } else if (path.endsWith(SESSION_STATES_FILE) && app.sessionExit !== 0) {
    return { exitCode: app.sessionExit, stdout: '', stderr: 'fixture reader failure' }
  } else if (sharedReader.isBusy) {
    return { exitCode: 75, stdout: '', stderr: '' }
  } else if (kept && kept.at > since) {
    return { exitCode: 0, stdout: JSON.stringify(kept), stderr: '' }
  } else {
    const output = path.endsWith(SESSION_STATES_FILE) ? app.output : sharedOutput(mode, codexOutput, now)
    const shared = { at: Math.floor(now), output }

    sharedFiles[path] = JSON.stringify({ entries: { ...file.entries, [key]: shared } })
    return { exitCode: 0, stdout: JSON.stringify(shared), stderr: '' }
  }
}

// What the reader writes for each mode, counting the reads it makes.
const sharedOutput = (mode: string | undefined, codexOutput: object, now: number) => {
  if (mode === 'local') {
    codexReads.push(mode)
    return { working: [], records: [], running: 0, ...pick(codexOutput, LOCAL_KEYS) }
  } else if (mode === 'server') {
    codexReads.push(mode)
    return pick(codexOutput, SERVER_KEYS)
  } else {
    started.recounts += 1
    return { ...HISTORY, generatedAt: new Date(now).toISOString() }
  }
}

const engine = (
  on: Parameters<Parameters<typeof test>[1]>[1],
  opened: string[] = [],
  cwd = '/Users/t/Desktop/project/또갈래',
  dirs: string[] = [],
  hasGit = true,
  agents: unknown[] = [],
  codexOutput: object = { running: 0 },
) => {
  // The engine's clock reads fractions of a millisecond.
  const clock = mock.clock(on, { now: Date.parse('2026-11-04T07:00:00Z') + 0.25 })
  mock.store(on)
  codexDisk.recordAt = 0
  codexDisk.configAt = 0
  claudeShared.text = undefined
  for (const path of Object.keys(sharedFiles)) {
    delete sharedFiles[path]
  }
  sharedReader.isBusy = false
  sharedReader.hold = undefined
  sharedReader.calls = 0
  started.recounts = 0
  started.closed = []
  app.listed = []
  app.output = { version: 1, at: 0, unread: [], sessions: {}, unreadable: [] }
  app.selfCalls = 0
  app.sessionExit = 0
  app.requests = []
  app.transcript = {}
  app.hostId = undefined
  app.listCalls = []
  app.agentLists = 0
  app.agentsHold = undefined
  app.isAgentListFailing = false
  app.tools = []
  app.workFiles = {}
  app.currentWorks = []
  app.countedCalls = []
  app.workReads = []
  app.sessionIds = []
  on('state.get', { plugin: 'usage-panel', key: 'works' }, (_, e, next) => {
    app.workReads.push(clock.now())
    return next(e)
  })
  on('state.set', async (_, e, next) => {
    const result = await next(e)

    if (e.plugin === 'usage-panel' && e.key === 'works' && result.value?.isSet) {
      app.currentWorks = e.value.value.map(work => ({ ...work }))
    }
    return result
  })
  on('command.register', () => ({ value: { command: 'usage-panel' } }))
  on('tool.register', (_, e) => (app.tools.push(e.name), { value: { tool: `mcp__usage-panel__${e.name}` } }))
  on('mcp.call', (_, e) => {
    if (e.tool === 'get_session') {
      app.selfCalls += 1
      const content = [{ type: 'text' as const, text: JSON.stringify(app.own) }]

      return { value: { content, isError: false } }
    } else {
      app.listCalls.push(e.args)
      const content = [{ type: 'text' as const, text: JSON.stringify(app.listed) }]

      return { value: { content, isError: e.tool !== 'list_sessions' } }
    }
  })
  on('session.id', () => {
    app.sessionIds.push(clock.now())
    return { value: 'cli-1' }
  })
  on('ui.open', (_, e) => (opened.push(e.id), { value: { isPlaced: true } }))
  on('ui.close', (_, e) => (started.closed.push(e.id), { value: undefined }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('process.run', async (_, e) => {
    const [path = '', key = '', since = '0', , mode] = e.argv.slice(4)
    if (path.startsWith(SHARED)) {
      if (path.endsWith(SESSION_STATES_FILE)) {
        app.requests.push(JSON.parse(mode ?? '') as ListedSession[])
      }
      return { value: await readShared(path, key, Number(since), mode, codexOutput, clock.now()) }
    }
    if (e.argv[3]?.includes('def cache(')) return { value: { exitCode: 0, stdout: JSON.stringify(app.transcript), stderr: '' } }
    if (e.argv[0] === 'date') return { value: { exitCode: 0, stdout: '+0900\n', stderr: '' } }
    if (e.argv[0] === 'git' && !hasGit) return { value: { exitCode: 128, stdout: '', stderr: 'not a git repository' } }
    if (e.argv[0] === 'git' && e.argv.includes('status')) {
      app.countedCalls.push({ kind: 'places', at: clock.now() })
      return {
        value: {
          exitCode: 0,
          stdout: '# branch.oid 0123456789\n# branch.head main\n# branch.upstream origin/main\n# branch.ab +1 -0\n1 .M N... 100644 100644 100644 a b x.md\n1 .M N... 100644 100644 100644 a b y.md\n? new.txt\n',
          stderr: '',
        },
      }
    }
    return { value: { exitCode: 128, stdout: '', stderr: 'no stash' } }
  })
  // The session count asks for the app's id of this session once per tick.
  on('env.get', (_, e) => {
    const isHostId = e.name === 'CLAUDE_CODE_HOST_SESSION_ID'

    if (isHostId) {
      app.countedCalls.push({ kind: 'sessions', at: clock.now() })
    }
    return { value: isHostId ? app.hostId : '/Users/t' }
  })
  on('session.cwd', () => ({ value: cwd }))
  // The Codex tick looks for the config once, the Claude share for its file once.
  on('fs.exists', (_, e) => {
    if (e.path.startsWith(`${CODEX_HOME}sessions/`)) {
      return { value: codexDisk.recordAt > 0 }
    } else if (e.path === CODEX_CONFIG) {
      app.countedCalls.push({ kind: 'codex', at: clock.now() })
      return { value: codexDisk.configAt > 0 || e.path in sharedFiles }
    } else if (e.path === CLAUDE_SHARED) {
      app.countedCalls.push({ kind: 'claude', at: clock.now() })
      return { value: claudeShared.text !== undefined }
    } else {
      return { value: e.path in sharedFiles }
    }
  })
  on('fs.read', (_, e) => ({
    value:
      e.path === CLAUDE_SHARED
        ? (claudeShared.text ?? '')
        : (sharedFiles[e.path] ?? app.workFiles[e.path] ?? ''),
  }))
  on('fs.write', (_, e) => {
    if (e.path === CLAUDE_SHARED) {
      claudeShared.text = e.text
    } else if (e.path.startsWith(WORK_FOLDER)) {
      app.workFiles[e.path] = e.text
    } else if (e.path.startsWith(SHARED)) {
      sharedFiles[e.path] = e.text
    }
    return { value: undefined }
  })
  on('fs.stat', () => ({ value: { kind: 'file', size: 1, mtimeMs: codexDisk.configAt, isLink: false } }))
  on('agent.list', async () => {
    app.agentLists += 1
    app.countedCalls.push({ kind: 'helpers', at: clock.now() })
    await app.agentsHold
    if (app.isAgentListFailing) {
      throw new Error('fixture agent list failure')
    }
    return { value: agents }
  })
  on('fs.list', (_, e) => {
    if (e.path === WORK_FOLDER) {
      const mtimeMs = clock.now()

      return {
        value: Object.keys(app.workFiles).map(path => ({
          name: path.slice(WORK_FOLDER.length + 1),
          kind: 'file',
          size: 1,
          mtimeMs,
          isLink: false,
        })),
      }
    }
    return {
      value: e.path.startsWith(CODEX_HOME)
        ? [{ name: 'rollout-a.jsonl', kind: 'file', size: 1, mtimeMs: codexDisk.recordAt, isLink: false }]
        : dirs.map(name => ({ name, kind: 'dir', size: 0, mtimeMs: 0, isLink: false })),
    }
  })
  on('session.usage', () => ({
    value: {
      startedAt: 0,
      context: { tokens: 151_366, window: 1_000_000, percent: 15 },
      rateLimits: [
        { kind: 'five_hour', percentUsed: 12, resetsAt: '2026-11-04T11:50:00Z' },
        { kind: 'seven_day', percentUsed: 17, resetsAt: '2026-11-08T20:00:00Z' },
      ],
      cost: { usd: 3.21 },
    },
  }))
  on('turn.start', (_, e) => ({ turnId: e.turnId }))
  on('session.measure', (_, e) => ({ changed: e.changed }))
  on('turn.complete', (_, e) => ({ text: e.answer }))

  return clock
}

test('the pane shows limits, the turn and the past week, on every surface', async ($, on) => {
  engine(on)

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.turn.start({ text: '패널 만들어줘', turnId: 't1' })
  await $.turn.complete({
    turnId: 't1',
    answer: '',
    durationMs: 32_000,
    isAborted: false,
    reason: 'answer',
    usage: {
      model: 'claude-opus-5-5',
      input_tokens: 2,
      cache_creation_input_tokens: 2_000,
      cache_read_input_tokens: 150_000,
      output_tokens: 1_200,
    },
  })

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })

    expect((await ui.find({ type: 'Text', text: /^12%$/ }))?.text).toBe('12%')
    expect((await ui.find({ type: 'Text', text: /^resets 20:50/ }))?.text).toBe('resets 20:50 · 4h 50m left')
    expect((await ui.find({ type: 'Text', text: /^resets Mon 05:00/ }))?.text).toContain('resets Mon 05:00')
    expect((await ui.find({ type: 'Text', text: /패널 만들어줘/ }))?.text).toContain('패널 만들어줘')
    expect((await ui.find({ type: 'Text', text: /^In 152K/ }))?.text).toBe('In 152K·Out 1.2K · 0 steps · 32s')
    expect(await ui.find({ type: 'Text', text: /^Wed 11\/4$/ })).toBeUndefined()

    await ui.press({ key: 'tab-week' })
    expect((await ui.find({ type: 'Text', text: /^디자인$/ }))?.text).toBe('디자인')
    expect((await ui.find({ type: 'Text', text: /^Wed 11\/4$/ }))?.text).toBe('Wed 11/4')
    await ui.press({ key: 'tab-now' })
    await ui.unmount()
  }
})

test('the line above the prompt sums the limits and opens the details', async ($, on) => {
  const opened: string[] = []
  engine(on, opened)

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  expect(opened).toEqual([])

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...ABOVE, surface })

    expect((await ui.find({ type: 'Text', text: /^~\/Desktop/ }))?.text).toBe('~/Desktop/project/또갈래')
    expect((await ui.find({ type: 'Text', text: /^main/ }))?.text).toBe('main⇡1!2?1')
    expect(await ui.find({ type: 'Text', text: /^Opus/ })).toBeUndefined()
    expect((await ui.find({ type: 'Text', text: /^5h / }))?.text).toBe('5h 12%')
    expect((await ui.find({ type: 'Text', text: /^↻20:50$/ }))?.text).toBe('↻20:50')
    expect((await ui.find({ type: 'Text', text: /^Week / }))?.text).toBe('Week 17%')
    expect(await ui.find({ type: 'Text', text: /^↻Mon/ })).toBeUndefined()
    expect((await ui.find({ type: 'Text', text: /^Context / }))?.text).toBe('Context 15%')
    expect(await ui.find({ type: 'Text', text: /151K\/1M/ })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: /^Last/ })).toBeUndefined()
    expect((await ui.find({ key: 'details' }))?.text).toBe('›')
    await ui.press({ key: 'details' })
    await ui.unmount()
  }
  expect(opened).toEqual(['usage', 'usage'])
})

test('crossing 80% raises one toast per window and threshold', async ($, on) => {
  engine(on)
  const toasts: string[] = []
  on('ui.toast', (_, e) => (toasts.push(String(e.text ?? e)), { value: undefined }))

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.session.measure(claudeReading(70))
  await $.session.measure(claudeReading(85))
  await $.session.measure(claudeReading(88))
  await $.session.measure(claudeReading(96))

  expect(toasts).toEqual(['5h limit 85% · resets 20:50', '5h limit 96% · resets 20:50'])
})

test('the Claude line shows the last reply of this session once one finished', async ($, on) => {
  engine(on)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.turn.start({ text: '추천으로 해줘', turnId: 't2' })
  await $.turn.complete({
    turnId: 't2', answer: '', durationMs: 5_000, isAborted: false, reason: 'answer',
    usage: { model: 'claude-opus-5-5', input_tokens: 2, cache_creation_input_tokens: 2_000, cache_read_input_tokens: 150_000, output_tokens: 1_200 },
  })

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...ABOVE, surface })
    expect((await ui.find({ type: 'Text', text: /끝/ }))?.text).toBe('16:00 끝 1분 미만 걸림')
    expect((await ui.find({ type: 'Text', text: /^153K$/ }))?.text).toBe('153K')
    expect(await ui.find({ type: 'Text', text: /^Last|^In / })).toBeUndefined()
    await ui.unmount()
  }
})

test('a long path keeps its ends and shortens the middle to unique prefixes', async ($, on) => {
  const deep = '/Users/t/Library/Application Support/Claude/scratch-workspaces/0a8db381/scratch-2026-11-03-0449e6'
  const dirs = ['Library', 'Lighthouse', 'Application Support', 'Apps', 'Claude', 'scratch-workspaces', 'scripts', '0a8db381', '0b']
  engine(on, [], deep, dirs)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })

  const ui = await $.ui.mount({ ...ABOVE, surface: 'desktop' })
  expect((await ui.find({ type: 'Text', text: /^~\// }))?.text).toBe('~/Lib/Appl/C/scra/0a/scratch-2026-11-03-0449e6')
  await ui.unmount()
})

test('a folder without git shows its path alone', async ($, on) => {
  engine(on, [], '/Users/t/Desktop/창업 아이디어', [], false)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })

  const ui = await $.ui.mount({ ...ABOVE, surface: 'desktop' })
  expect((await ui.find({ type: 'Text', text: /^~\// }))?.text).toBe('~/Desktop/창업 아이디어')
  expect(await ui.find({ type: 'Text', text: /^main|no git/ })).toBeUndefined()
  await ui.unmount()
})

test('busy subagents show on the place line and in the Now tab', async ($, on) => {
  const agents = [
    { id: 'a1', description: 'Find usage of limits API', type: 'Explore', status: 'running' },
    { id: 'a2', description: 'Review board copy', type: 'general-purpose', status: 'running' },
    { id: 'a3', description: 'Done already', type: 'Plan', status: 'completed' },
  ]
  engine(on, [], undefined, [], true, agents)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })

  const band = await $.ui.mount({ ...ABOVE, surface: 'desktop' })
  expect((await band.find({ type: 'Text', text: /agents$/ }))?.text).toBe('2 agents')
  await band.unmount()

  const pane = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect((await pane.find({ type: 'Text', text: /RUNNING/ }))?.text).toBe('AGENTS · 2 RUNNING')
  expect((await pane.find({ type: 'Text', text: /^Explore/ }))?.text).toContain('Explore · running')
  expect(await pane.find({ type: 'Text', text: /^Plan/ })).toBeUndefined()
  await pane.unmount()
})

test('no busy subagent leaves the place line as it was', async ($, on) => {
  engine(on)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const band = await $.ui.mount({ ...ABOVE, surface: 'desktop' })
  expect(await band.find({ type: 'Text', text: /agents?$/ })).toBeUndefined()
  await band.unmount()
})

test('a fast 5h window turns red and says when it fills', async ($, on) => {
  engine(on)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  // Two hours into the window (40%) with 70% used: 175% by the reset.
  await $.session.measure({
    context: { tokens: 1, window: 1_000_000, percent: 15 },
    rateLimits: [
      { kind: 'five_hour', percentUsed: 70, resetsAt: '2026-11-04T10:00:00Z' },
      { kind: 'seven_day', percentUsed: 21, resetsAt: '2026-11-08T20:00:00Z' },
    ],
    changed: ['rateLimits'],
  })
  const ui = await $.ui.mount({ ...ABOVE, surface: 'desktop' })
  // 30% left at 35%/h from 16:00 local: full at about 16:51.
  expect((await ui.find({ type: 'Text', text: /^5h / }))?.text).toBe('5h 70%')
  expect((await ui.find({ type: 'Text', text: /^16:51$/ }))?.text).toBe('16:51')
  await ui.unmount()
})

test('a heavy reply leaves a note beside the branch and an insight, until the next prompt', async ($, on) => {
  engine(on)
  on('tool.call', () => ({ result: {}, text: 'x'.repeat(210_000) }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.turn.start({ text: '전체 파일 읽어줘', turnId: 'h1' })
  await $.tool.call({ tool: 'Bash', command: 'cat big.log' })
  await $.session.measure({
    context: { tokens: 151_366, window: 1_000_000, percent: 15 },
    rateLimits: [
      { kind: 'five_hour', percentUsed: 15, resetsAt: '2026-11-04T11:50:00Z' },
      { kind: 'seven_day', percentUsed: 17, resetsAt: '2026-11-08T20:00:00Z' },
    ],
    changed: ['rateLimits'],
  })
  await $.turn.complete({
    turnId: 'h1', answer: '', durationMs: 40_000, isAborted: false, reason: 'answer',
    usage: { model: 'claude-opus-5-5', input_tokens: 2, cache_creation_input_tokens: 2_000, cache_read_input_tokens: 150_000, output_tokens: 900 },
  })

  const band = await $.ui.mount({ ...ABOVE, surface: 'desktop' })
  expect((await band.find({ type: 'Text', text: /^▲/ }))?.text).toBe('▲5h+3% 큰 파일 / 일부만 읽게 요청')
  await band.unmount()

  const pane = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect((await pane.find({ type: 'Text', text: /^도구가 돌려준 결과/ }))?.text).toContain('약 70K 토큰')
  await pane.unmount()

  await $.turn.start({ text: '다음', turnId: 'h2' })
  const after = await $.ui.mount({ ...ABOVE, surface: 'desktop' })
  expect(await after.find({ type: 'Text', text: /^▲/ })).toBeUndefined()
  await after.unmount()
})

test('a running reply shows how long it ran and its tokens without a Working label', async ($, on) => {
  engine(on)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.turn.start({ text: '진행 중', turnId: 'w1' })
  const band = await $.ui.mount({ ...ABOVE, surface: 'desktop' })
  const spinners = (await band.findAll({ type: 'Svg' })).filter(svg => svg.props.alt === 'working')

  expect((await band.find({ type: 'Text', text: /^진행/ }))?.text).toBe('진행 1분 미만')
  expect((await band.find({ type: 'Text', text: /^0 ×0$/ }))?.text).toBe('0 ×0')
  expect(spinners).toHaveLength(1)
  expect(await band.find({ type: 'Text', text: /Working/ })).toBeUndefined()
  await band.unmount()
})

test('three simple replies on Opus no longer suggest another model', async ($, on) => {
  engine(on)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  for (const id of ['s1', 's2', 's3']) {
    await $.turn.start({ text: '짧은 질문', turnId: id })
    await $.turn.complete({
      turnId: id, answer: '네.', durationMs: 3_000, isAborted: false, reason: 'answer',
      usage: { model: 'claude-opus-5-5', input_tokens: 2, cache_creation_input_tokens: 100, cache_read_input_tokens: 50_000, output_tokens: 300 },
    })
  }
  const band = await $.ui.mount({ ...ABOVE, surface: 'desktop' })
  expect(await band.find({ type: 'Text', text: /^▲/ })).toBeUndefined()
  await band.unmount()
})

// Three simple requests that each spent most of what they wrote thinking, at
// the given effort, and the habit line the band shows after them.
const overthink = async ($: Engine, on: On, effort: 'high' | 'xhigh') => {
  const usage = {
    model: 'claude-opus-5-5',
    input_tokens: 2,
    cache_creation_input_tokens: 100,
    cache_read_input_tokens: 50_000,
    output_tokens: 5_000,
  }

  on('turn.step', async function* (_, e) {
    return { ...step(e.turnId), usage }
  })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  for (const id of ['o1', 'o2', 'o3']) {
    await $.turn.start({ text: '짧은 질문', turnId: id })
    for await (const _ of $.turn.step({ turnId: id, index: 0, model: 'claude-opus-5-5', effort, messageCount: 1 })) {
      // The step's chunks are not looked at.
    }
    await $.turn.complete({ turnId: id, answer: '네.', durationMs: 3_000, isAborted: false, reason: 'answer', usage })
  }
  const band = await $.ui.mount({ ...ABOVE, surface: 'desktop' })
  const habit = await band.find({ type: 'Text', text: /^▲/ })

  await band.unmount()
  return habit?.text
}

test('long thinking on simple requests suggests one step down, but not below high', async ($, on) => {
  engine(on)
  app.hostId = 'local_this'
  expect(await overthink($, on, 'xhigh')).toBe('▲긴 생각 / 사고 수준 high로')
  expect(app.listCalls).toContainEqual({ limit: 500, linked: true, include_archived: true })
})

test('at high, long thinking on simple requests suggests nothing', async ($, on) => {
  engine(on)
  expect(await overthink($, on, 'high')).toBeUndefined()
})

test('a session that started another one is never told to lower its effort', async ($, on) => {
  engine(on)
  app.hostId = 'local_main'
  app.listed = [{ ...row('local_child', false), startedBy: 'local_main' }]
  expect(await overthink($, on, 'xhigh')).toBeUndefined()
})

const CODEX = {
  limits: {
    at: Date.parse('2026-11-04T06:58:00Z'),
    plan: 'plus',
    limits: [
      { kind: 'five_hour', percentUsed: 52, resetsAt: '2026-11-04T09:10:00Z' },
      { kind: 'seven_day', percentUsed: 18, resetsAt: '2026-11-10T16:24:00Z' },
    ],
  },
  last: {
    finishedAt: Date.parse('2026-11-04T06:50:00Z'),
    read: 620_000,
    cached: 540_000,
    write: 9_400,
    model: 'gpt-6-astra',
    effort: 'xhigh',
    folder: '/tmp/review',
    rise: 3,
  },
  configured: { model: 'gpt-6-astra', effort: 'xhigh' },
  running: 0,
}

test('a Codex line follows the Claude line with its limits; its last run waits in the details', async ($, on) => {
  const opened: string[] = []
  const clock = engine(on, opened, undefined, [], true, [], CODEX)

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...ABOVE, surface })

    expect((await ui.find({ type: 'Text', text: /^5h / }))?.text).toBe('5h 12%')
    expect((await ui.find({ type: 'Text', text: /^5h 52%$/ }))?.text).toBe('5h 52%')
    expect((await ui.find({ type: 'Text', text: /^↻18:10$/ }))?.text).toBe('↻18:10')
    expect((await ui.find({ type: 'Text', text: /^Week 18%$/ }))?.text).toBe('Week 18%')
    expect(await ui.find({ type: 'Text', text: /^Last|^In / })).toBeUndefined()
    expect((await ui.find({ key: 'codex-details' }))?.text).toBe('›')
    await ui.press({ key: 'codex-details' })
    await ui.unmount()
  }
  expect(opened).toEqual(['usage', 'usage'])

  const pane = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect((await pane.find({ type: 'Text', text: /^CODEX$/ }))?.text).toBe('CODEX')
  const run = await pane.find({ type: 'Text', text: /^In 620K/ })

  expect(run?.text).toBe('In 620K (캐시 540K)·Out 9.4K · 한도 +3%p')
  await pane.unmount()
})

test('an old Codex reading turns grey and says when it was read', async ($, on) => {
  const old = {
    snapshot: { at: Date.parse('2026-11-04T06:30:00Z'), limits: [{ kind: 'seven_day', percentUsed: 6 }] },
    running: 0,
  }
  const clock = engine(on, [], undefined, [], true, [], old)

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  const ui = await $.ui.mount({ ...ABOVE, surface: 'desktop' })

  expect((await ui.find({ type: 'Text', text: /^Week 6%$/ }))?.text).toBe('Week 6%')
  expect((await ui.find({ type: 'Text', text: /기준$/ }))?.text).toBe('·15:30 기준')
  expect(await ui.find({ type: 'Text', text: /^Last/ })).toBeUndefined()
  await ui.unmount()
})

test('a Codex window crossing a threshold raises its own toast', async ($, on) => {
  const fiveHour = { kind: 'five_hour', percentUsed: 96, resetsAt: '2026-11-04T09:10:00Z' }
  const full = { limits: { at: Date.parse('2026-11-04T06:59:00Z'), limits: [fiveHour] }, running: 0 }
  const clock = engine(on, [], undefined, [], true, [], full)
  const toasts: string[] = []
  on('ui.toast', (_, e) => (toasts.push(String(e.text ?? e)), { value: undefined }))

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)

  expect(toasts).toEqual(['Codex 5h limit 96% · resets 18:10'])
})

test('a Codex run started here is read at once, and every ten seconds while it runs', async ($, on) => {
  codexReads.length = 0
  const clock = engine(on, [], undefined, [], true, [], { running: 1 })
  on('tool.call', () => ({ result: { text: 'OK', isError: false } }))

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  await $.tool.call({ tool: 'Bash', command: 'codex exec -s read-only "review"', run_in_background: true })
  await $.tool.call({ tool: 'Bash', command: 'ls' })
  await clock.advance(10_000)
  await clock.advance(10_000)

  expect(codexReads).toEqual(['local', 'server', 'local', 'local'])
})

test('the Codex line names the model and effort the next run takes, just before its details', async ($, on) => {
  const clock = engine(on, [], undefined, [], true, [], CODEX)

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...ABOVE, surface })
    const keysIn = async (key: string): Promise<(string | undefined)[]> =>
      ((await ui.find({ key }))?.children ?? []).map(child => (child as { props?: { key?: string } }).props?.key)

    expect((await ui.find({ key: 'codex-setting' }))?.text).toBe('gpt-6-astra xhigh')
    expect((await keysIn('codex')).slice(-1)).toEqual(['codex-end'])
    expect(await keysIn('codex-end')).toEqual(['codex-setting', 'codex-details'])
    await ui.unmount()
  }

  const pane = await $.ui.mount({ ...PANE, surface: 'desktop' })
  const configured = await pane.find({ type: 'Text', text: /^기본값/ })

  expect(configured?.text).toBe('기본값 gpt-6-astra xhigh · 16:00에 읽음')
  await pane.unmount()
})

test("a Codex run another session started shows in the details, not on this session's line", async ($, on) => {
  const work = {
    startedAt: Date.parse('2026-11-04T06:40:00Z'),
    steps: 37,
    read: 5_916_858,
    cached: 5_600_000,
    write: 21_653,
    model: 'gpt-6-sol',
    effort: 'high',
    folder: '/tmp/review',
    rise: 3,
  }
  const output: Record<string, unknown> = { ...CODEX, working: [work], running: 2 }
  const clock = engine(on, [], undefined, [], true, [], output)

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  const ui = await $.ui.mount({ ...ABOVE, surface: 'desktop' })
  const spinners = (await ui.findAll({ type: 'Svg' })).filter(svg => svg.props.alt === 'working')

  expect(await ui.find({ key: 'work-codex' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /^In 5.9M/ })).toBeUndefined()
  expect(spinners).toHaveLength(0)
  expect((await ui.find({ key: 'codex-setting' }))?.text).toBe('gpt-6-astra xhigh')
  await ui.unmount()

  const pane = await $.ui.mount({ ...PANE, surface: 'desktop' })
  const configured = await pane.find({ type: 'Text', text: /^기본값/ })

  expect((await pane.find({ type: 'Text', text: /^실행 중/ }))?.text).toBe('실행 중 15:40부터 · gpt-6-sol high')
  expect((await pane.find({ type: 'Text', text: /×37/ }))?.text).toBe('In 5.9M (캐시 5.6M)·Out 21.7K ×37 · 한도 +3%p')
  expect((await pane.find({ type: 'Text', text: /^기록을/ }))?.text).toBe('기록을 남기지 않는 실행 1개')
  expect(configured?.text).toBe('기본값 gpt-6-astra xhigh · 16:00에 읽음')
  await pane.unmount()

  delete output.working
  output.running = 0
  await clock.advance(10_000)
  const after = await $.ui.mount({ ...ABOVE, surface: 'desktop' })
  const left = (await after.findAll({ type: 'Svg' })).filter(svg => svg.props.alt === 'working')

  expect((await after.find({ key: 'codex-setting' }))?.text).toBe('gpt-6-astra xhigh')
  expect(left).toHaveLength(0)
  await after.unmount()
})

test('an old Codex setting turns grey and says when it was read', async ($, on) => {
  const output: Record<string, unknown> = { configured: { model: 'gpt-6-astra', effort: 'xhigh' }, running: 0 }
  const clock = engine(on, [], undefined, [], true, [], output)

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  delete output.configured
  await clock.advance(16 * 60_000)
  const ui = await $.ui.mount({ ...ABOVE, surface: 'desktop' })
  const name = await ui.find({ type: 'Text', text: /^gpt-6-astra/ })

  expect((await ui.find({ key: 'codex-setting' }))?.text).toBe('gpt-6-astra xhigh·16:00 기준')
  expect(name?.props.dimColor).toBe(true)
  await ui.unmount()

  const pane = await $.ui.mount({ ...PANE, surface: 'desktop' })
  const configured = await pane.find({ type: 'Text', text: /^기본값/ })

  expect(configured?.text).toBe('기본값 gpt-6-astra xhigh · 16:00에 읽은 값이에요')
  await pane.unmount()
})

test('a record written by any run, or a saved config, is read within ten seconds', async ($, on) => {
  codexReads.length = 0
  codexDisk.recordAt = 0
  const clock = engine(on, [], undefined, [], true, [], { running: 0 })
  codexDisk.configAt = clock.now() - 60_000

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(10_000)
  codexDisk.recordAt = clock.now()
  await clock.advance(10_000)
  codexDisk.configAt = clock.now()
  await clock.advance(10_000)
  codexDisk.recordAt = 0
  codexDisk.configAt = 0

  expect(codexReads).toEqual(['local', 'server', 'local', 'server'])
})

test('an idle session takes the newer Claude reading another session shared, within 15 seconds', async ($, on) => {
  const clock = engine(on)

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  claudeShared.text = sharedReading('2026-11-04T07:00:05Z', 40)
  await clock.advance(15_000)
  const ui = await $.ui.mount({ ...ABOVE, surface: 'desktop' })

  expect((await ui.find({ type: 'Text', text: /^5h / }))?.text).toBe('5h 40%')
  expect((await ui.find({ type: 'Text', text: /^↻20:50$/ }))?.text).toBe('↻20:50')
  await ui.unmount()
})

test('a reply here shares its Claude reading, and an older shared one leaves it as it is', async ($, on) => {
  const clock = engine(on)

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.session.measure(claudeReading(30))
  expect(claudeShared.text).toBe(JSON.stringify({ at: clock.now(), limits: claudeReading(30).rateLimits }))
  claudeShared.text = sharedReading('2026-11-04T06:59:00Z', 99)
  await clock.advance(15_000)
  const ui = await $.ui.mount({ ...ABOVE, surface: 'desktop' })

  expect((await ui.find({ type: 'Text', text: /^5h / }))?.text).toBe('5h 30%')
  await ui.unmount()
})

test('an old Claude reading turns grey and says when it was read', async ($, on) => {
  const clock = engine(on)

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.session.measure(claudeReading(30))
  await clock.advance(15 * 60_000)

  for (const surface of ['terminal', 'desktop'] as const) {
    const above = await $.ui.mount({ ...ABOVE, surface })

    expect((await above.find({ type: 'Text', text: /^5h / }))?.text).toBe('5h 30%')
    expect((await above.find({ type: 'Text', text: /기준$/ }))?.text).toBe('·16:00 기준')
    await above.unmount()
    const pane = await $.ui.mount({ ...PANE, surface })

    const asOf = await pane.find({ type: 'Text', text: /에 읽은 값이에요$/ })

    expect(asOf?.text).toBe('16:00에 읽은 값이에요')
    await pane.unmount()
  }
})

test('a reload neither recounts nor asks the Codex server again, and keeps the details open', async ($, on) => {
  codexReads.length = 0
  engine(on)

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })

  expect(started.recounts).toBe(1)
  expect(codexReads).toEqual(['local', 'server'])
  expect(started.closed).toEqual(['usage'])
})

const FOLDER = '/Users/t/Desktop/project/또갈래'
const seedShared = (name: string, key: string, at: number, output: object) => {
  sharedFiles[`${SHARED}${name}.json`] = JSON.stringify({ entries: { [key]: { at, output } } })
}

test('a Codex reading another session made since is taken without reading again', async ($, on) => {
  codexReads.length = 0
  const clock = engine(on)
  seedShared('codex-local', '', clock.now() - 1_000, { ...pick(CODEX, LOCAL_KEYS), working: [] })
  seedShared('codex-server', FOLDER, clock.now() - 60_000, pick(CODEX, SERVER_KEYS))

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  const ui = await $.ui.mount({ ...ABOVE, surface: 'desktop' })

  expect(codexReads).toEqual([])
  expect((await ui.find({ type: 'Text', text: /^5h 52%$/ }))?.text).toBe('5h 52%')
  expect((await ui.find({ type: 'Text', text: /^Week 18%$/ }))?.text).toBe('Week 18%')
  expect((await ui.find({ key: 'codex-setting' }))?.text).toBe('gpt-6-astra xhigh')
  await ui.unmount()
})

test('a server reading made for another folder is not taken', async ($, on) => {
  codexReads.length = 0
  const clock = engine(on, [], undefined, [], true, [], CODEX)
  seedShared('codex-server', '/Users/t/other', clock.now() - 1_000, { configured: { model: 'other-model' } })

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  const ui = await $.ui.mount({ ...ABOVE, surface: 'desktop' })

  expect(codexReads).toEqual(['local', 'server'])
  expect((await ui.find({ key: 'codex-setting' }))?.text).toBe('gpt-6-astra xhigh')
  await ui.unmount()
})

test('while another session reads Codex, this one keeps its line and takes that reading later', async ($, on) => {
  codexReads.length = 0
  const clock = engine(on, [], undefined, [], true, [], { running: 1 })
  sharedReader.isBusy = true

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  seedShared('codex-local', '', clock.now(), { ...pick(CODEX, LOCAL_KEYS), working: [], running: 1 })
  seedShared('codex-server', FOLDER, clock.now(), pick(CODEX, SERVER_KEYS))
  sharedReader.isBusy = false
  await clock.advance(10_000)
  const ui = await $.ui.mount({ ...ABOVE, surface: 'desktop' })

  expect(codexReads).toEqual([])
  expect(await ui.find({ type: 'Text', text: /읽지 못했어요/ })).toBeUndefined()
  expect((await ui.find({ type: 'Text', text: /^5h 52%$/ }))?.text).toBe('5h 52%')
  expect((await ui.find({ type: 'Text', text: /^Week 18%$/ }))?.text).toBe('Week 18%')
  await ui.unmount()
})

test('a tick that comes while the last Codex read still goes starts no second read', async ($, on) => {
  const clock = engine(on, [], undefined, [], true, [], { running: 1 })
  let release = () => {}

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  expect(sharedReader.calls).toBe(2)
  sharedReader.hold = new Promise<void>(resolve => {
    release = resolve
  })
  await clock.advance(10_000)
  await clock.advance(10_000)
  await clock.advance(10_000)
  expect(sharedReader.calls).toBe(3)
  release()
  sharedReader.hold = undefined
  await clock.advance(10_000)

  expect(sharedReader.calls).toBe(4)
})

test('a refresh an event asks for while the same list still runs lists once more after it', async ($, on) => {
  const clock = engine(on)
  const usage = { model: 'claude-sonnet-5-5', input_tokens: 1, output_tokens: 1 }
  let release = () => {}

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  app.agentsHold = new Promise<void>(resolve => {
    release = resolve
  })
  await clock.advance(3_000)
  const listed = app.agentLists
  const done = { agentId: 'helper', answer: '', durationMs: 1, isAborted: false, reason: 'answer', usage } as const

  await $.turn.complete({ ...done, turnId: 'a1' })
  await $.turn.complete({ ...done, turnId: 'a2' })
  app.agentsHold = undefined
  release()
  await clock.advance(1)

  expect(app.agentLists).toBe(listed + 1)
})

test('a 7-day count another session made within ten minutes is taken, and Recount counts again', async ($, on) => {
  const clock = engine(on)
  seedShared('history', '', clock.now() - 5 * 60_000, { ...HISTORY, total: 2000 })

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  expect(started.recounts).toBe(0)

  const pane = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await pane.press({ key: 'tab-week' })
  await pane.press({ key: 'recount' })
  await clock.advance(1)
  await pane.unmount()

  expect(started.recounts).toBe(1)
  await clock.advance(11 * 60_000)
  expect(started.recounts).toBe(2)
})

const row = (sessionId: string, isRunning: boolean, lastActivityAt = '2026-11-04T06:59:00Z'): SessionRow => ({
  sessionId,
  title: `Title ${sessionId}`,
  cwd: '/fixture/project',
  isRunning,
  lastActivityAt,
  isArchived: false,
})

function sessionOutput(states: Record<string, SessionState['state']>): SessionStates {
  const sessions = Object.fromEntries(Object.entries(states).map(([id, state]) => [id, {
    state,
    waitingFor: state === 'waiting' ? 'dialog' : null,
    isAnswerPending: false,
    reason: null,
    since: 0,
    row: { ...row(id, state === 'working'), link: null, startedBy: null },
  }]))

  return { version: 1, at: 0, unread: [], sessions, unreadable: [] }
}

const sessionLine = async (surface: 'terminal' | 'desktop', mount: (s: 'terminal' | 'desktop') => Promise<{ find: (q: { key: string }) => Promise<{ text?: string } | undefined>; unmount: () => Promise<void> }>) => {
  const ui = await mount(surface)
  const found = await ui.find({ key: 'sessions' })

  await ui.unmount()
  return found?.text
}

test('the place line counts the shared states and leaves failed, unknown and idle out', async ($, on) => {
  const clock = engine(on)

  const states = { a: 'working', b: 'working', c: 'waiting', d: 'done', e: 'idle', f: 'failed', g: 'unknown' } as const

  app.output = sessionOutput(states)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...ABOVE, surface })

    expect((await ui.find({ key: 'sessions' }))?.text).toBe('2 working · 1 waiting · 1 done')
    expect((await ui.find({ type: 'Text', text: /^1 waiting$/ }))?.props.color).toBe('warning')
    expect((await ui.find({ type: 'Text', text: /^1 done$/ }))?.props.color).toBe('suggestion')
    await ui.unmount()
  }
})

test('a count of none is left out, and no other session leaves the place line as it was', async ($, on) => {
  const clock = engine(on)

  app.output = sessionOutput({ a: 'working' })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  expect(await sessionLine('desktop', surface => $.ui.mount({ ...ABOVE, surface }))).toBe('1 working')
  app.output = sessionOutput({})
  await clock.advance(5_000)
  expect(await sessionLine('desktop', surface => $.ui.mount({ ...ABOVE, surface }))).toBeUndefined()
})

test('a store it cannot read still shows working and waiting decisions from the reader', async ($, on) => {
  const clock = engine(on)

  app.output = { ...sessionOutput({ a: 'working', b: 'waiting', c: 'idle' }), unread: null }
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)

  expect(await sessionLine('desktop', surface => $.ui.mount({ ...ABOVE, surface }))).toBe('1 working · 1 waiting')
})

test('26 a fresh shared file supplies counts without listing sessions that tick', async ($, on) => {
  const clock = engine(on)

  seedShared('session-states', '', clock.now(), sessionOutput({ a: 'working', b: 'done' }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  expect(app.listCalls).toHaveLength(0)
  expect(app.requests).toHaveLength(0)
  expect(app.selfCalls).toBe(0)
  expect(await sessionLine('desktop', surface => $.ui.mount({ ...ABOVE, surface }))).toBe('1 working · 1 done')
  seedShared('session-states', '', clock.now() + 4_000, sessionOutput({ a: 'waiting' }))
  await clock.advance(5_000)

  expect(app.listCalls).toHaveLength(0)
  expect(app.requests).toHaveLength(0)
  expect(app.selfCalls).toBe(0)
  expect(await sessionLine('desktop', surface => $.ui.mount({ ...ABOVE, surface }))).toBe('1 waiting')
})

test('27 a stale shared file and busy reader keep the displayed counts', async ($, on) => {
  const clock = engine(on)

  app.output = sessionOutput({ a: 'working' })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  app.output = sessionOutput({ a: 'done' })
  app.sessionExit = 75
  await clock.advance(5_000)

  expect(app.listCalls).toHaveLength(2)
  expect(app.requests).toHaveLength(2)
  expect(await sessionLine('desktop', surface => $.ui.mount({ ...ABOVE, surface }))).toBe('1 working')
  app.sessionExit = 0
  await clock.advance(5_000)
  expect(await sessionLine('desktop', surface => $.ui.mount({ ...ABOVE, surface }))).toBe('1 done')
})

test('29 the own app session is excluded from this band counts', async ($, on) => {
  const clock = engine(on)

  app.hostId = 'local_self'
  seedShared('session-states', '', clock.now(), sessionOutput({ local_self: 'working', other: 'done' }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)

  expect(await sessionLine('desktop', surface => $.ui.mount({ ...ABOVE, surface }))).toBe('1 done')
})

test('a band that finds another band reading the stale states leaves the app list alone', async ($, on) => {
  const clock = engine(on)

  const otherClaimAt = clock.now() - 1_000

  seedShared('session-states', '', clock.now() - 10_000, sessionOutput({ a: 'working' }))
  sharedFiles[`${SHARED}session-states.claim.json`] = JSON.stringify({ at: otherClaimAt })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)

  expect(app.listCalls).toHaveLength(0)
  expect(app.requests).toHaveLength(0)
  // Past one tick the other band's mark has lapsed, and this band reads.
  await clock.advance(5_000)
  expect(app.listCalls).toHaveLength(1)
  expect((JSON.parse(sharedFiles[`${SHARED}session-states.claim.json`] ?? '{}') as { at: number }).at).toBeGreaterThan(
    otherClaimAt + 5_000,
  )
})

test('a stale reading supplies only listed fields and the own row with its parent', async ($, on) => {
  const clock = engine(on)

  app.listed = [row('other', true)]
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)

  expect(app.requests[0]).toEqual([
    { sessionId: 'other', title: 'Title other', cwd: '/fixture/project', isRunning: true, lastActivityAt: '2026-11-04T06:59:00Z' },
    { sessionId: 'local_self', title: 'Own session', cwd: '/fixture', isRunning: false, lastActivityAt: '2026-11-04T06:59:00Z', startedBy: 'local_parent' },
  ])
})

test('a failed shared reader keeps the last counts', async ($, on) => {
  const clock = engine(on)

  app.output = sessionOutput({ a: 'waiting' })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  app.output = sessionOutput({})
  app.sessionExit = 1
  await clock.advance(5_000)

  expect(await sessionLine('desktop', surface => $.ui.mount({ ...ABOVE, surface }))).toBe('1 waiting')
})

const step = (turnId: string) => ({
  turnId,
  index: 0,
  answer: '',
  toolUses: [],
  stopReason: 'end_turn' as const,
  usage: { model: 'claude-opus-5-5', input_tokens: 2, cache_creation_input_tokens: 100, cache_read_input_tokens: 150_000, output_tokens: 50 },
})

const cacheText = async (mount: () => Promise<{ find: (q: { key: string }) => Promise<{ text?: string } | undefined>; unmount: () => Promise<void> }>) => {
  const ui = await mount()
  const found = await ui.find({ key: 'cache' })

  await ui.unmount()
  return found?.text
}

test('the cache counts an hour down from the last request, then says what the next one writes again', async ($, on) => {
  const clock = engine(on)
  const band = () => $.ui.mount({ ...ABOVE, surface: 'desktop' })

  // The engine's step: one request, no chunks.
  on('turn.step', async function* () {
    return step('c1')
  })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  expect(await cacheText(band)).toBeUndefined()

  await $.turn.start({ text: '캐시', turnId: 'c1' })
  for await (const _ of $.turn.step({ turnId: 'c1', index: 0, model: 'claude-opus-5-5', messageCount: 1 })) {
    // The step's chunks are not looked at.
  }
  for (const surface of ['terminal', 'desktop'] as const) {
    expect(await cacheText(() => $.ui.mount({ ...ABOVE, surface }))).toBe('Cache ·17:00까지')
  }
  await clock.advance(51 * 60_000)
  expect(await cacheText(band)).toBe('Cache 9분 남음')
  await clock.advance(9 * 60_000)
  expect(await cacheText(band)).toBe('Cache 만료 ·151K 다시 씀')
})

test('a session that starts again takes its last reply and its cache life from the transcript', async ($, on) => {
  const clock = engine(on)

  app.transcript = { at: Date.parse('2026-11-04T06:58:00Z'), ttl: '5m' }
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)

  expect(await cacheText(() => $.ui.mount({ ...ABOVE, surface: 'desktop' }))).toBe('Cache 3분 남음')
})

const summary = [{ role: 'user' as const, text: '요약', toolUses: [] }]

test('past its end the cache goes on from a summary made while idle, and any other compaction waits for a request', async ($, on) => {
  const clock = engine(on)
  const band = () => $.ui.mount({ ...ABOVE, surface: 'desktop' })

  app.transcript = { at: Date.parse('2026-11-04T06:58:00Z'), ttl: '1h' }
  on('session.compact', () => ({ messages: summary }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  expect(await cacheText(band)).toBe('Cache ·16:58까지')

  await clock.advance(52 * 60_000)
  await $.session.compact({ trigger: 'precompute', messages: summary })
  await clock.advance(10 * 60_000)
  expect(await cacheText(band)).toBe('Cache 만료 ·요약으로 이어감')

  await $.session.compact({ trigger: 'manual', messages: summary })
  expect(await cacheText(band)).toBeUndefined()
})

const START = Date.parse('2026-11-04T07:00:00Z')
const HOUR = 60 * 60_000
const ESTIMATE = 'mcp__usage-panel__work_estimate'
const COMBINED = { options: { codexLine: 'combined' } }
const REPLY_USAGE = {
  model: 'claude-opus-5-5',
  input_tokens: 2,
  cache_creation_input_tokens: 2_000,
  cache_read_input_tokens: 150_000,
  output_tokens: 1_200,
}

// Finished work another session wrote, from which the pace is read: each
// Claude reply 10 steps at a conversation of 100K, each Codex run ten minutes.
const pastWork = (maker: 'claude' | 'codex', index: number, finishedAt = START - HOUR) => ({
  maker,
  id: `${maker}-${index}`,
  sessionId: 'other',
  startedAt: finishedAt - 10 * 60_000,
  finishedAt,
  startContext: 100_000,
  steps: 10,
  tokens: maker === 'claude' ? 10_000_000 : 1_000_000,
  weight: maker === 'claude' ? 100_000 : 200_000,
  rise: 2,
})

// The weekly limits as that session saw them rise around the work: Claude's 4
// points over 300K of weight, Codex's 3 over 600K.
const CLAUDE_WEEK = '2026-11-08T20:00:00Z'
const CODEX_WEEK = '2026-11-08T21:00:00Z'
const PAST_STEPS = [
  { at: START - 2 * HOUR, maker: 'claude', percentUsed: 13, resetsAt: CLAUDE_WEEK },
  { at: START - HOUR / 2, maker: 'claude', percentUsed: 17, resetsAt: CLAUDE_WEEK },
  { at: START - 2 * HOUR, maker: 'codex', percentUsed: 20, resetsAt: CODEX_WEEK },
  { at: START - HOUR / 2, maker: 'codex', percentUsed: 23, resetsAt: CODEX_WEEK },
]

const seedPastWork = (extra: object[] = [], limitSteps: object[] = PAST_STEPS) => {
  const works = [0, 1, 2].flatMap(i => [pastWork('claude', i), pastWork('codex', i)])

  app.workFiles[`${WORK_FOLDER}/other.json`] = JSON.stringify({ works: [...works, ...extra], limitSteps })
}

// The band draws each piece of a work as one Text, so a piece is found by its
// whole text.
const textOf = async (ui: TextFinder, text: RegExp) => (await ui.find({ type: 'Text', text }))?.text

test('a reply with an estimate shows when it is due, then how late it ended and what it cost', async ($, on) => {
  const clock = engine(on)

  seedPastWork()
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  await $.turn.start({ text: '작업 시각 만들어줘', turnId: 'w1' })
  const recorded = await $.tool.call({ tool: ESTIMATE, maker: 'claude', minutes: 20, steps: 10 })

  expect(recorded.result).toBe('Claude 작업을 16:20 끝 예정으로 기록했어요.')
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...ABOVE, surface })

    expect(await textOf(ui, /까지/)).toBe('16:20까지 20분')
    expect(await textOf(ui, /^0\/예상/)).toBe('0/예상 15.1M ×0')
    expect(await textOf(ui, /^0\/2%$/)).toBe('0/2%')
    await ui.unmount()
  }

  await clock.advance(26 * 60_000)
  await $.turn.complete({ turnId: 'w1', answer: '', durationMs: 1, isAborted: false, reason: 'answer', usage: REPLY_USAGE })
  const ui = await $.ui.mount({ ...ABOVE, surface: 'desktop' })

  expect(await textOf(ui, /^16:26 끝/)).toBe('16:26 끝 6분 늦음')
  expect((await ui.find({ type: 'Text', text: /^ 6분 늦음$/ }))?.props.color).toBe('warning')
  expect(await textOf(ui, /^153K/)).toBe('153K/예상 15.1M -99%')
  expect(await textOf(ui, /^0\.3\//)).toBe('0.3/2%')
  expect(await ui.find({ key: 'work-codex' })).toBeUndefined()
  await ui.unmount()
})

// A reply that estimates 10 steps at this session's conversation, drawn on the
// band: its share of the week as the rate the steps and the work give.
const estimatedShare = async ($: Engine, clock: ReturnType<typeof engine>, turnId: string) => {
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  await $.turn.start({ text: '해줘', turnId })
  await $.tool.call({ tool: ESTIMATE, maker: 'claude', minutes: 20, steps: 10 })
  const band = await $.ui.mount({ ...ABOVE, surface: 'desktop' })
  const share = await textOf(band, /^0(\/[\d.]+)?%$/)

  await band.unmount()
  return share
}

test('the share of the week waits for three points of rise', async ($, on) => {
  const clock = engine(on)
  const twoPoints = PAST_STEPS.map(s => (s.maker === 'claude' && s.percentUsed === 13 ? { ...s, percentUsed: 15 } : s))

  seedPastWork([], twoPoints)
  expect(await estimatedShare($, clock, 'p1')).toBeUndefined()
})

test('the share of the week starts over where the percent falls, and passes a malformed step', async ($, on) => {
  const clock = engine(on)
  const lastWeek = [
    { at: START - 3 * HOUR, maker: 'claude', percentUsed: 40, resetsAt: CLAUDE_WEEK },
    { at: START - 2.5 * HOUR, maker: 'claude', percentUsed: 44, resetsAt: CLAUDE_WEEK },
    { at: 'soon', maker: 'claude', percentUsed: 99 },
  ]

  seedPastWork([], [...lastWeek, ...PAST_STEPS])
  expect(await estimatedShare($, clock, 'p2')).toBe('0/2%')
})

test('the share of the week starts over with a new week even when the percent did not fall', async ($, on) => {
  const clock = engine(on)
  const lastWeek = [{ at: START - 3 * HOUR, maker: 'claude', percentUsed: 10, resetsAt: '2026-11-01T20:00:00Z' }]

  seedPastWork([], [...lastWeek, ...PAST_STEPS])
  expect(await estimatedShare($, clock, 'p3')).toBe('0/2%')
})

test('the share of the week sums the work of every session, and each session saves the steps it saw', async ($, on) => {
  const clock = engine(on)
  const works = [0, 1, 2].map(i => ({ ...pastWork('claude', i), sessionId: `other-${i}` }))

  for (const [i, work] of works.entries()) {
    const limitSteps = i === 0 ? PAST_STEPS : []

    app.workFiles[`${WORK_FOLDER}/other-${i}.json`] = JSON.stringify({ works: [work], limitSteps })
  }
  expect(await estimatedShare($, clock, 'p4')).toBe('0/2%')
  await clock.advance(60_000)

  const own = JSON.parse(app.workFiles[`${WORK_FOLDER}/cli-1.json`] ?? '{}') as {
    limitSteps?: { maker: string; percentUsed: number; resetsAt?: string }[]
  }
  const seen = (own.limitSteps ?? []).map(step => [step.maker, step.percentUsed, step.resetsAt])

  expect(seen).toContainEqual(['claude', 17, CLAUDE_WEEK])
})

test('the estimate tool refuses an input it cannot record', async ($, on) => {
  engine(on)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })

  const idle = await $.tool.call({ tool: ESTIMATE, maker: 'claude', minutes: 5, steps: 3 })

  await $.turn.start({ text: '해줘', turnId: 'w2' })
  const noSteps = await $.tool.call({ tool: ESTIMATE, maker: 'claude', minutes: 5 })
  const noMinutes = await $.tool.call({ tool: ESTIMATE, maker: 'codex', minutes: 0 })

  expect(idle.deny).toContain('돌고 있는 답이 없어')
  expect(noSteps.deny).toContain('단계 수(steps')
  expect(noMinutes.deny).toContain('minutes')
})

test('work without an estimate shows how long it ran, and how long it took once it ended', async ($, on) => {
  const clock = engine(on)

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  await $.turn.start({ text: '고쳐줘', turnId: 'w3' })
  await clock.advance(3 * 60_000)
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...ABOVE, surface })

    expect(await textOf(ui, /^진행/)).toBe('진행 3분')
    expect(await textOf(ui, /^0 ×0$/)).toBe('0 ×0')
    await ui.unmount()
  }

  await $.turn.complete({ turnId: 'w3', answer: '', durationMs: 1, isAborted: false, reason: 'answer', usage: REPLY_USAGE })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const after = await $.ui.mount({ ...ABOVE, surface: 'desktop' })

  expect(await textOf(after, /끝/)).toBe('16:03 끝 3분 걸림')
  expect(await textOf(after, /^153K$/)).toBe('153K')
  await after.unmount()
})

test('a Codex run in the background takes its estimate and ends with its record', async ($, on) => {
  const output: Record<string, unknown> = { running: 0 }
  const clock = engine(on, [], undefined, [], true, [], output)
  on('tool.call', () => ({ result: { text: 'OK', isError: false } }))

  seedPastWork()
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  const startedAt = clock.now()

  await $.tool.call({ tool: ESTIMATE, maker: 'codex', minutes: 15 })
  const ran = await $.tool.call({ tool: 'Bash', command: 'codex exec "review"', run_in_background: true })
  const record: CodexRecord = {
    recordId: 'rollout-background.jsonl', ownerSessionId: 'cli-1', state: 'running',
    startedAt: startedAt + 2_000, steps: 30, read: 1_000_000, cached: 800_000, write: 20_000, folder: '/',
  }

  expect(ran.context ?? []).not.toContain('No Codex estimate was recorded for this run.')
  output.running = 1
  output.working = [record]
  output.records = [record]
  await clock.advance(10_000)
  const ui = await $.ui.mount({ ...ABOVE, surface: 'desktop' })

  expect(await textOf(ui, /까지/)).toBe('16:15까지 15분')
  expect(await textOf(ui, /^1M\/예상/)).toBe('1M/예상 1.5M ×30')
  expect(await textOf(ui, /^1\.9\//)).toBe('1.9/1.5%')
  await ui.unmount()

  await clock.advance(12 * 60_000 - 10_000)
  output.running = 0
  output.working = []
  output.finished = [{ ...record, finishedAt: startedAt + 12 * 60_000, rise: 1 }]
  output.records = [{ ...record, state: 'done', finishedAt: startedAt + 12 * 60_000, rise: 1 }]
  codexDisk.recordAt = clock.now()
  await clock.advance(10_000)
  await clock.advance(10_000)
  expect(ownCodexWorks()[0]?.state).toBe('done')
  const done = await $.ui.mount({ ...ABOVE, surface: 'desktop' })

  expect(await textOf(done, /^16:12 끝/)).toBe('16:12 끝 3분 빠름')
  expect(await textOf(done, /^1M\/예상/)).toBe('1M/예상 1.5M -32%')
  expect(await textOf(done, /^1\.9\//)).toBe('1.9/1.5%')
  await done.unmount()
  codexDisk.recordAt = 0
})

test('a run or a plan without an estimate tells the session once', async ($, on) => {
  engine(on)
  on('tool.call', () => ({ result: { text: 'OK', isError: false } }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.turn.start({ text: '해줘', turnId: 'w4' })

  const items = [{ content: '고치기', status: 'in_progress' }]
  const first = await $.tool.call({ tool: 'mcp__ccd_turn__update_plan', items })
  const second = await $.tool.call({ tool: 'mcp__ccd_turn__update_plan', items })
  const run = await $.tool.call({ tool: 'Bash', command: 'codex exec "fix"' })

  expect(first.context).toContain('No work estimate is recorded for this turn yet.')
  expect(second.context ?? []).not.toContain('No work estimate is recorded for this turn yet.')
  expect(run.context).toContain('No Codex estimate was recorded for this run.')
  expect(app.tools).toEqual(['work_estimate'])
})

test('the details list the week of work with its estimates and the median miss', async ($, on) => {
  const clock = engine(on)
  const old = { ...pastWork('claude', 9, START - 8 * 24 * HOUR), id: 'too-old' }

  seedPastWork([old])
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  await $.turn.start({ text: '해줘', turnId: 'w5' })
  await $.tool.call({ tool: ESTIMATE, maker: 'claude', minutes: 20, steps: 10 })
  await clock.advance(26 * 60_000)
  await $.turn.complete({ turnId: 'w5', answer: '', durationMs: 1, isAborted: false, reason: 'answer', usage: REPLY_USAGE })

  const pane = await $.ui.mount({ ...PANE, surface: 'desktop' })
  const rows = await pane.findAll({ type: 'Text', text: /^시간 / })

  expect(rows).toHaveLength(7)
  expect(rows[0]?.text).toBe('시간 26분/20분 · 단계 0/10 · 토큰 153K/15.1M · 주간 0.3%/2%')
  expect(rows[1]?.text).toBe('시간 10분 · 단계 10 · 토큰 1M · 주간 1%')
  expect((await pane.find({ type: 'Text', text: /^최근 7일/ }))?.text).toBe(
    '최근 7일, 예정보다 보통 시간 +30% · 단계 -100% · 토큰 -99%',
  )
  expect(await pane.find({ type: 'Text', text: /^%는 그 작업이/ })).toBeDefined()
  await pane.unmount()
  expect(Object.keys(app.workFiles)).toContain(`${WORK_FOLDER}/cli-1.json`)
})

test('quoted and search commands create no placeholder, and a denied invocation restores its estimate', async ($, on) => {
  const clock = engine(on)
  const answered = { result: { text: 'OK', isError: false } }
  on('tool.call', (_, e) => (e.tool === 'Bash' && e.command.includes('refused') ? { deny: '거절됨' } : answered))

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  const commands = [
    'git commit -m "codex exec 결과 반영"',
    'grep -n "codex exec" notes.md',
    'grep -n "exec" codex-implementation.md',
    'ps | grep -E "x|codex exec"',
    'echo "codex exec"',
    'codex exec --help',
  ]

  for (const command of commands) {
    const result = await $.tool.call({ tool: 'Bash', command })

    expect(result.context ?? []).not.toContain('No Codex estimate was recorded for this run.')
    expect(ownCodexWorks()).toHaveLength(0)
  }
  const none = await $.ui.mount({ ...ABOVE, surface: 'desktop' })

  expect(await none.find({ key: 'work-codex' })).toBeUndefined()
  await none.unmount()

  await $.tool.call({ tool: ESTIMATE, maker: 'codex', minutes: 10 })
  await $.tool.call({ tool: 'Bash', command: 'codex exec "refused"', run_in_background: true })
  await $.tool.call({ tool: 'Bash', command: 'cd /tmp && codex exec "review"', run_in_background: true })
  const ui = await $.ui.mount({ ...ABOVE, surface: 'desktop' })

  expect((ownCodexWorks()).filter(w => w.estimate?.minutes === 10)).toHaveLength(1)
  expect(await textOf(ui, /^0 ×0$/)).toBe('0 ×0')
  await ui.unmount()
})

test('a broken work file leaves the other files, and a stopped reply stays out of the median miss', async ($, on) => {
  const clock = engine(on)

  seedPastWork()
  app.workFiles[`${WORK_FOLDER}/broken.json`] = '{"works": [{"maker": "claude"'
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  await $.turn.start({ text: '해줘', turnId: 'w6' })
  await $.tool.call({ tool: ESTIMATE, maker: 'claude', minutes: 20, steps: 10 })
  await clock.advance(5 * 60_000)
  const stopped = {
    turnId: 'w6',
    answer: '',
    durationMs: 1,
    isAborted: true,
    reason: 'answer',
    usage: REPLY_USAGE,
  } as const

  await $.turn.complete(stopped)

  const pane = await $.ui.mount({ ...PANE, surface: 'desktop' })

  expect(await pane.findAll({ type: 'Text', text: /^시간 / })).toHaveLength(7)
  expect(await pane.find({ type: 'Text', text: /^최근 7일/ })).toBeUndefined()
  await pane.unmount()
})

test('two runs started close together each take their own record', async ($, on) => {
  const output: Record<string, unknown> = { running: 0 }
  const clock = engine(on, [], undefined, [], true, [], output)
  on('tool.call', () => ({ result: { text: 'OK', isError: false } }))

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  const first = clock.now()

  await $.tool.call({ tool: 'Bash', command: 'codex exec "one"', run_in_background: true })
  await clock.advance(5_000)
  await $.tool.call({ tool: 'Bash', command: 'codex exec "two"', run_in_background: true })
  const one = codexRecord('one', first + 2_000, { steps: 3, read: 1_000_000 })
  const two = codexRecord('two', first + 7_000, { steps: 4, read: 2_000_000 })

  output.running = 2
  output.working = [one]
  output.records = [one]
  await clock.advance(10_000)
  output.working = [one, two]
  output.records = [one, two]
  await clock.advance(10_000)
  output.running = 0
  output.working = []
  output.finished = [
    { ...two, finishedAt: first + 3 * 60_000 },
    { ...one, finishedAt: first + 2 * 60_000 },
  ]
  output.records = [
    { ...two, state: 'done', finishedAt: first + 3 * 60_000 },
    { ...one, state: 'done', finishedAt: first + 2 * 60_000 },
  ]
  codexDisk.recordAt = clock.now()
  await clock.advance(10_000)
  await clock.advance(10_000)
  const pane = await $.ui.mount({ ...PANE, surface: 'desktop' })
  const rows = (await pane.findAll({ type: 'Text', text: /^시간 / })).map(row => row.text)

  expect(rows).toEqual(['시간 3분 · 단계 4 · 토큰 2M', '시간 2분 · 단계 3 · 토큰 1M'])
  await pane.unmount()
  codexDisk.recordAt = 0
})

test('a foreground nohup command owns two equal-start runs until both records finish', async ($, on) => {
  const output: CodexLocalOutput = { working: [], running: 0 }
  const clock = engine(on, [], undefined, [], true, [], output)

  on('tool.call', () => ({ result: { text: 'returned immediately', isError: false } }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  await $.tool.call({ tool: 'Bash', command: 'nohup codex --search exec "one" & nohup codex exec "two" &' })
  const first = codexRecord('first', clock.now())
  const second = codexRecord('second', clock.now())

  await publishCodex(output, [first, second], clock)
  expect((ownCodexWorks()).map(w => w.state)).toEqual(['running', 'running'])
  for (const surface of ['desktop', 'terminal'] as const) {
    const ui = await $.ui.mount({ ...ABOVE, surface })

    expect((await ui.find({ key: 'codex' }))?.text).not.toContain('✓')
    expect((await ui.find({ key: 'codex' }))?.text).toContain('외 1개')
    await ui.unmount()
  }
  await publishCodex(output, [{ ...first, state: 'done', finishedAt: START + 60_000 }, second], clock)
  const partial = await $.ui.mount({ ...ABOVE, surface: 'desktop' })

  expect((await partial.find({ key: 'codex' }))?.text).not.toContain('✓')
  await partial.unmount()
  const bothDone: CodexRecord[] = [
    { ...first, state: 'done', finishedAt: START + 60_000 },
    { ...second, state: 'done', finishedAt: START + 120_000 },
  ]

  await publishCodex(output, bothDone, clock)
  const done = await $.ui.mount({ ...ABOVE, surface: 'desktop' })

  expect((await done.find({ key: 'codex' }))?.text).toContain('✓')
  expect((await done.find({ key: 'codex' }))?.text).toContain('16:02 끝')
  expect(ownCodexWorks()).toHaveLength(2)
  await done.unmount()
})

test('neither foreground nor background Bash returns end a Codex run', async ($, on) => {
  const output: CodexLocalOutput = { working: [], running: 0 }
  const clock = engine(on, [], undefined, [], true, [], output)

  on('tool.call', () => ({ result: { text: 'OK', isError: false } }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  for (const runInBackground of [false, true]) {
    await $.tool.call({ tool: 'Bash', command: 'codex -c k=v exec "work"', run_in_background: runInBackground })
  }
  expect((ownCodexWorks()).map(w => w.state)).toEqual(['starting', 'starting'])
  expect((ownCodexWorks()).every(w => w.finishedAt === undefined)).toBe(true)
  const first = codexRecord('live', clock.now())
  const second = codexRecord('live-too', clock.now())

  await publishCodex(output, [first], clock)
  await clock.advance(20_000)
  expect((ownCodexWorks()).find(w => w.recordId)?.state).toBe('running')
  await publishCodex(output, [first, second], clock)
  expect(ownCodexWorks().map(w => w.state)).toEqual(['running', 'running'])
})

test('another session reservation cannot be claimed even in the same second', async ($, on) => {
  const output: CodexLocalOutput = { working: [], running: 0 }
  const clock = engine(on, [], undefined, [], true, [], output)

  on('tool.call', () => ({ result: { text: 'OK', isError: false } }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  await $.tool.call({ tool: 'Bash', command: '/path/codex e "work"' })
  const sameSecond = [
    codexRecord('other', clock.now(), { ownerSessionId: 'other-session', read: 9_000_000 }),
    codexRecord('mine', clock.now()),
  ]

  await publishCodex(output, sameSecond, clock)
  const own = ownCodexWorks()
  const ui = await $.ui.mount({ ...ABOVE, surface: 'desktop' })

  expect(own.map(w => w.recordId)).toEqual(['rollout-mine.jsonl'])
  expect((await ui.find({ key: 'codex' }))?.text).not.toContain('model-other')
  expect((await ui.find({ key: 'codex' }))?.text).not.toContain('9M')
  await ui.unmount()
})

test('an owned run started inside run-review appears without Codex in the Bash text', async ($, on) => {
  const output: CodexLocalOutput = { working: [], running: 0 }
  const clock = engine(on, [], undefined, [], true, [], output)

  on('tool.call', () => ({ result: { text: 'OK', isError: false } }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(61_000)
  await $.tool.call({ tool: 'Bash', command: 'python3 skills/outside-review/run-review.py' })
  output.records = [codexRecord('script', clock.now())]
  output.working = output.records
  output.running = 1
  codexDisk.recordAt = clock.now()
  await clock.advance(60_000)
  expect((ownCodexWorks()).map(w => w.recordId)).toEqual(['rollout-script.jsonl'])
})

test('a real invocation with no record loses its placeholder at the estimate deadline', async ($, on) => {
  const output: CodexLocalOutput = { working: [], running: 0 }
  const clock = engine(on, [], undefined, [], true, [], output)

  on('tool.call', () => ({ result: { text: 'OK', isError: false } }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  await $.tool.call({ tool: 'Bash', command: 'codex exec "delayed"' })
  await clock.advance(10 * 60_000 - 1)
  expect(ownCodexWorks().map(w => w.state)).toEqual(['starting'])
  await clock.advance(1)
  expect(ownCodexWorks()).toHaveLength(0)
  const ui = await $.ui.mount({ ...ABOVE, surface: 'desktop' })

  expect(await ui.find({ key: 'work-codex' })).toBeUndefined()
  await ui.unmount()
  await publishCodex(output, [codexRecord('late-owned', clock.now())], clock)
  expect(ownCodexWorks().filter(w => w.state === 'unknown')).toHaveLength(0)
  expect(ownCodexWorks().find(w => w.state === 'running')?.recordId).toBe('rollout-late-owned.jsonl')
})

test('late estimates stay on the running run and reservation and command estimates reach their runs', async ($, on) => {
  const output: CodexLocalOutput = { working: [], running: 0 }
  const clock = engine(on, [], undefined, [], true, [], output)

  on('tool.call', () => ({ result: { text: 'OK', isError: false } }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  const first = codexRecord('late', clock.now())

  await publishCodex(output, [first], clock)
  expect(ownCodexWorks().map(w => w.state)).toEqual(['running'])
  const late = await $.tool.call({ tool: ESTIMATE, maker: 'codex', minutes: 25 })

  expect(late.deny).toBeUndefined()
  expect((ownCodexWorks())[0]?.estimate?.minutes).toBe(25)
  await $.tool.call({ tool: ESTIMATE, maker: 'codex', minutes: 7, reservation: first.reservation })
  expect((ownCodexWorks())[0]?.estimate?.minutes).toBe(7)
  await $.tool.call({ tool: ESTIMATE, maker: 'codex', minutes: 11, reservation: 'reservation-reserved' })
  await $.tool.call({ tool: ESTIMATE, maker: 'codex', minutes: 13, reservation: 'reservation-reserved-too' })
  const reserved = [first, codexRecord('reserved', clock.now()), codexRecord('reserved-too', clock.now())]

  await publishCodex(output, reserved, clock)
  expect((ownCodexWorks()).map(w => w.estimate?.minutes)).toEqual([7, 11, 13])
  await $.tool.call({ tool: ESTIMATE, maker: 'codex', minutes: 20 })
  await $.tool.call({ tool: 'Bash', command: 'nohup codex exec "one" & nohup codex exec "two" &' })
  const one = codexRecord('group-one', clock.now())
  const two = codexRecord('group-two', clock.now())
  const old = output.records ?? []

  await publishCodex(output, [...old, one], clock)
  await $.tool.call({ tool: ESTIMATE, maker: 'codex', minutes: 30, reservation: one.reservation })
  await publishCodex(output, [...old, one, two], clock)
  const group = (ownCodexWorks()).filter(w => w.commandId)

  expect(group.map(w => w.estimate?.minutes)).toEqual([30, 20])
  expect(group[0]?.commandId).toBe(group[1]?.commandId)
})

test('stopped and unknown runs have no check or expected cost and do not train estimates', async ($, on) => {
  const output: CodexLocalOutput = { working: [], running: 0 }
  const clock = engine(on, [], undefined, [], true, [], output)
  const heavyWork = (state: string, index: number) => ({
    ...pastWork('codex', index),
    id: `${state}-${index}`,
    state,
    tokens: 100_000_000,
    weight: 100_000_000,
  })
  const excluded = ['stopped', 'unknown'].flatMap(state => [0, 1, 2].map(index => heavyWork(state, index)))

  seedPastWork(excluded)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  const record = codexRecord('state', clock.now())

  await publishCodex(output, [record], clock)
  await $.tool.call({ tool: ESTIMATE, maker: 'codex', minutes: 10 })
  const running = await $.ui.mount({ ...ABOVE, surface: 'desktop' })

  expect((await running.find({ key: 'codex' }))?.text).toContain('/예상 1M')
  expect((await running.find({ key: 'codex' }))?.text).toContain('1%')
  await running.unmount()
  for (const state of ['stopped', 'unknown'] as const) {
    await publishCodex(output, [{ ...record, state }], clock)
    const ui = await $.ui.mount({ ...ABOVE, surface: 'desktop' })
    const text = (await ui.find({ key: 'codex' }))?.text

    expect(text).toContain(state === 'stopped' ? '멈춤' : '확인 불가')
    expect(text).not.toContain('✓')
    expect(text).not.toContain('/예상')
    expect((ownCodexWorks())[0]?.expected).toBeUndefined()
    await ui.unmount()
  }
})

test('the shown run supplies both its model and effort regardless of the account first run', async ($, on) => {
  const mine = codexRecord('mine', START, { model: 'gpt-6.1-sol', effort: 'medium' })
  const other = codexRecord('other', START, { ownerSessionId: 'another', model: 'gpt-6-astra', effort: 'xhigh' })
  const output: CodexLocalOutput = { working: [other, mine], records: [other, mine], running: 2 }
  const clock = engine(on, [], undefined, [], true, [], output)

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  const ui = await $.ui.mount({ ...ABOVE, surface: 'desktop' })

  expect((await ui.find({ key: 'codex-setting' }))?.text).toBe('gpt-6.1-sol medium')
  await ui.unmount()
})

test('a failed open-file reading keeps the prior state as unknown and recovers without a false end', async ($, on) => {
  const output: CodexLocalOutput = { working: [], running: 0 }
  const clock = engine(on, [], undefined, [], true, [], output)

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  const record = codexRecord('uncertain', clock.now() - 20 * 60_000)

  await publishCodex(output, [record], clock)
  output.isProcessReadFailed = true
  await publishCodex(output, [{ ...record, state: 'unknown' }], clock)
  expect(ownCodexWorks()[0]?.state).toBe('unknown')
  expect(ownCodexWorks()[0]?.previousState).toBe('running')
  expect(ownCodexWorks()[0]?.finishedAt).toBeUndefined()
  const ui = await $.ui.mount({ ...ABOVE, surface: 'desktop' })

  expect((await ui.find({ key: 'codex' }))?.text).toContain('확인 불가')
  expect((await ui.find({ key: 'codex' }))?.text).not.toContain('✓')
  await ui.unmount()
  output.isProcessReadFailed = false
  await publishCodex(output, [record], clock)
  expect(ownCodexWorks()[0]?.state).toBe('running')
  expect(ownCodexWorks()[0]?.previousState).toBeUndefined()
})

test('an unreserved record can be guessed once and is removed if the ledger assigns another owner', async ($, on) => {
  const output: CodexLocalOutput = { working: [], running: 0 }
  const clock = engine(on, [], undefined, [], true, [], output)

  on('tool.call', () => ({ result: { text: 'OK', isError: false } }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  await $.tool.call({ tool: 'Bash', command: 'codex e "work"' })
  const record = codexRecord('guessed', clock.now(), { ownerSessionId: 'unknown', reservation: undefined })

  await publishCodex(output, [record], clock)
  expect(ownCodexWorks()).toHaveLength(1)
  expect(ownCodexWorks()[0]?.isGuessed).toBe(true)
  const ui = await $.ui.mount({ ...ABOVE, surface: 'desktop' })

  expect((await ui.find({ key: 'codex' }))?.text).toContain('추정')
  await ui.unmount()
  await publishCodex(output, [{ ...record, ownerSessionId: 'another-session' }], clock)
  expect(ownCodexWorks()).toEqual([])
})

test('a resumed record replaces its prior completion without adding another run', async ($, on) => {
  const output: CodexLocalOutput = { working: [], running: 0 }
  const clock = engine(on, [], undefined, [], true, [], output)

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  const record = codexRecord('resumed', clock.now(), { turnId: 'one' })

  await publishCodex(output, [{ ...record, state: 'done', finishedAt: START + 1_000 }], clock)
  const done = await $.ui.mount({ ...ABOVE, surface: 'desktop' })

  expect((await done.find({ key: 'codex' }))?.text).toContain('✓')
  await done.unmount()
  await publishCodex(output, [{ ...record, state: 'running', turnId: 'two' }], clock)
  const running = await $.ui.mount({ ...ABOVE, surface: 'desktop' })

  expect(ownCodexWorks()).toHaveLength(1)
  expect(ownCodexWorks()[0]?.finishedAt).toBeUndefined()
  expect(ownCodexWorks()[0]?.turnId).toBe('two')
  expect((await running.find({ key: 'codex' }))?.text).not.toContain('✓')
  await running.unmount()
})

test('a failed reading never reopens a run that already ended', async ($, on) => {
  const output: CodexLocalOutput = { working: [], running: 0 }
  const clock = engine(on, [], undefined, [], true, [], output)

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  const ended = codexRecord('ended', clock.now(), { state: 'done', finishedAt: clock.now() + 1_000 })
  const recent = codexRecord('recent', clock.now() + 2_000)

  await publishCodex(output, [ended, recent], clock)
  output.isProcessReadFailed = true
  await publishCodex(output, [{ ...recent, state: 'unknown' }], clock)
  expect(ownCodexWorks().find(w => w.recordId === ended.recordId)?.state).toBe('done')
  output.isProcessReadFailed = false
  await publishCodex(output, [{ ...recent, state: 'done', finishedAt: clock.now() }], clock)
  const ui = await $.ui.mount({ ...ABOVE, surface: 'desktop' })

  expect(ownCodexWorks().map(w => w.state)).toEqual(['done', 'done'])
  expect((await ui.find({ key: 'codex' }))?.text).toContain('✓')
  await ui.unmount()
})

test('a resume command joins the record it resumes and gives it the new estimate', async ($, on) => {
  const output: CodexLocalOutput = { working: [], running: 0 }
  const clock = engine(on, [], undefined, [], true, [], output)

  on('tool.call', () => ({ result: { text: 'OK', isError: false } }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  const record = codexRecord('again', clock.now(), { turnId: 'one', state: 'done', finishedAt: clock.now() + 1_000 })

  await publishCodex(output, [record], clock)
  await $.tool.call({ tool: ESTIMATE, maker: 'codex', minutes: 7 })
  await $.tool.call({ tool: 'Bash', command: 'codex exec resume --last "more"' })
  const resumed: CodexRecord = { ...record, state: 'running', turnId: 'two', turnStartedAt: clock.now() + 1_000 }

  delete resumed.finishedAt
  await publishCodex(output, [resumed], clock)
  expect(ownCodexWorks().map(w => w.state)).toEqual(['running'])
  expect(ownCodexWorks()[0]?.estimate?.minutes).toBe(7)
})

test('a run a script starts later takes the estimate given for it, not the last command one', async ($, on) => {
  const output: CodexLocalOutput = { working: [], running: 0 }
  const clock = engine(on, [], undefined, [], true, [], output)

  on('tool.call', () => ({ result: { text: 'OK', isError: false } }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  await $.tool.call({ tool: ESTIMATE, maker: 'codex', minutes: 20 })
  await $.tool.call({ tool: 'Bash', command: 'codex exec "first"' })
  const first = codexRecord('first', clock.now())

  await publishCodex(output, [first], clock)
  await clock.advance(3 * 60_000)
  await $.tool.call({ tool: ESTIMATE, maker: 'codex', minutes: 7 })
  await $.tool.call({ tool: 'Bash', command: 'python3 skills/outside-review/run-review.py' })
  await publishCodex(output, [first, codexRecord('second', clock.now())], clock)
  const minutes = Object.fromEntries(ownCodexWorks().map(w => [w.recordId, w.estimate?.minutes]))

  expect(minutes).toEqual({ 'rollout-first.jsonl': 20, 'rollout-second.jsonl': 7 })
})

test('the combined line still shows a run at work beside an unknown one', COMBINED, async ($, on) => {
  const output: CodexLocalOutput = { working: [], running: 0 }
  const clock = engine(on, [], undefined, [], true, [], output)

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  const busy = codexRecord('busy', clock.now())
  const unsure = codexRecord('unsure', clock.now(), { state: 'unknown' })

  await publishCodex(output, [busy, unsure], clock)
  const ui = await $.ui.mount({ ...ABOVE, surface: 'desktop' })
  const text = (await ui.find({ key: 'codex' }))?.text ?? ''

  expect(text).toContain('실행 2개')
  expect(text).toContain('진행')
  expect(text).toContain('확인 불가 포함')
  expect(text).not.toContain('✓')
  await ui.unmount()
})

for (const mode of ['latest-due', 'combined'] as const) {
  const options = { options: { codexLine: mode } }

  test(`the ${mode} line renders every run state on desktop and terminal`, options, async ($, on) => {
    const output: CodexLocalOutput = { working: [], running: 0 }
    const clock = engine(on, [], undefined, [], true, [], output)

    await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
    await clock.advance(1)
    const first = codexRecord('a', clock.now(), { read: 1000 })
    const second = codexRecord('b', clock.now(), { read: 2000 })

    await publishCodex(output, [first, second], clock)
    await $.tool.call({ tool: ESTIMATE, maker: 'codex', minutes: 10, reservation: first.reservation })
    await $.tool.call({ tool: ESTIMATE, maker: 'codex', minutes: 20, reservation: second.reservation })
    const scenarios: { name: string; records: CodexRecord[]; expected: string; isDone: boolean }[] = [
      {
        name: 'running',
        records: [first, second],
        expected: mode === 'combined' ? '실행 2개' : '외 1개',
        isDone: false,
      },
      {
        name: 'partly done',
        records: [{ ...first, state: 'done', finishedAt: START + 60_000 }, second],
        expected: mode === 'combined' ? '실행 1개' : 'model-b',
        isDone: false,
      },
      {
        name: 'all done',
        records: [
          { ...first, state: 'done', finishedAt: START + 60_000 },
          { ...second, state: 'done', finishedAt: START + 120_000 },
        ],
        expected: '16:02 끝',
        isDone: true,
      },
      {
        name: 'no estimate',
        records: [first, second, codexRecord('unestimated', clock.now())],
        expected: '예정 모름 포함',
        isDone: false,
      },
      {
        name: 'stopped',
        records: [
          { ...first, state: 'stopped' },
          { ...second, state: 'stopped' },
          codexRecord('unestimated', clock.now(), { state: 'stopped' }),
        ],
        expected: '멈춤',
        isDone: false,
      },
      {
        name: 'unknown',
        records: [
          { ...first, state: 'unknown' },
          { ...second, state: 'unknown' },
          codexRecord('unestimated', clock.now(), { state: 'unknown' }),
        ],
        expected: '확인 불가',
        isDone: false,
      },
    ]

    for (const scenario of scenarios) {
      await publishCodex(output, scenario.records, clock)
      for (const surface of ['desktop', 'terminal'] as const) {
        const props = { ...ABOVE.props, bodyColumns: 44 }
        const ui = await $.ui.mount({ ...ABOVE, props, surface })
        const line = (await ui.find({ key: 'codex' }))?.text ?? ''

        expect(line).toContain(scenario.expected)
        expect(line.includes('✓')).toBe(scenario.isDone)
        if (mode === 'combined') {
          expect(await ui.find({ key: 'codex-setting' })).toBeUndefined()
          if (scenario.name === 'running') {
            expect(line).toContain('3K')
            expect(line).toContain('16:20')
          }
        } else if (scenario.name === 'running') {
          expect(line).toContain('model-b high')
          expect(line).toContain('2K')
        }
        await ui.unmount()
      }
    }
  })
}

test('recent old stored runs relink by unique owned start or become unknown while old history stays', async ($, on) => {
  const output: CodexLocalOutput = { working: [], running: 0 }
  const clock = engine(on, [], undefined, [], true, [], output)
  const legacy: Work = {
    maker: 'codex',
    id: 'old',
    sessionId: 'cli-1',
    startedAt: START - 60_000,
    recordStartedAt: START - 60_000,
    finishedAt: START - 59_000,
    steps: 4,
    tokens: 500,
    weight: 500,
  }

  on('state.get', { plugin: 'usage-panel', key: 'works' }, async (_, e, next) => {
    const current = await next(e)
    const value = { shape: 'works-1', value: [
      legacy,
      { ...legacy, id: 'missing', recordStartedAt: START - 120_000 },
      { ...legacy, id: 'ambiguous', recordStartedAt: START - 180_000 },
      { ...legacy, id: 'open', finishedAt: undefined, recordStartedAt: START - 240_000 },
      { ...legacy, id: 'history', finishedAt: START - HOUR, recordStartedAt: START - 2 * HOUR },
    ] }

    return current.value?.value === undefined ? { value: { value, version: 0 } } : current
  })
  output.records = [
    codexRecord('relinked', START - 60_000),
    codexRecord('same-one', START - 180_000),
    codexRecord('same-two', START - 180_000),
    codexRecord('foreign', START - 120_000, { ownerSessionId: 'another' }),
  ]
  output.working = output.records
  output.running = 4
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  const own = ownCodexWorks()

  expect(own.find(w => w.recordId === 'rollout-relinked.jsonl')?.state).toBe('running')
  expect(own.find(w => w.recordId === 'rollout-relinked.jsonl')?.finishedAt).toBeUndefined()
  expect(own.find(w => w.id === 'missing')?.state).toBe('unknown')
  expect(own.find(w => w.id === 'ambiguous')?.state).toBe('unknown')
  expect(own.find(w => w.id === 'open')?.state).toBe('unknown')
  expect(own.find(w => w.id === 'history')?.finishedAt).toBe(START - HOUR)
  expect(own.find(w => w.id === 'history')?.state).toBeUndefined()
  expect(own.filter(w => w.recordId === 'rollout-relinked.jsonl')).toHaveLength(1)
})

test('unlinkable legacy and recordless unknown work expire after their last change and allow a check', async ($, on) => {
  const output: CodexLocalOutput = { working: [], running: 0 }
  const clock = engine(on, [], undefined, [], true, [], output)
  const legacy: Work = {
    maker: 'codex',
    id: 'unlinked',
    sessionId: 'cli-1',
    startedAt: clock.now() - 20 * 60_000,
    finishedAt: clock.now() - (10 * 60_000 - 20_000),
    steps: 1,
    tokens: 100,
    weight: 100,
  }

  on('state.get', { plugin: 'usage-panel', key: 'works' }, async (_, e, next) => {
    const current = await next(e)
    const value = { shape: 'works-1', value: [legacy, { ...legacy, id: 'unknown', state: 'unknown' as const }] }

    return current.value?.value === undefined ? { value: { value, version: 0 } } : current
  })
  output.records = [codexRecord('done', clock.now() - 60_000, { state: 'done', finishedAt: clock.now() - 1_000 })]
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  const before = await $.ui.mount({ ...ABOVE, surface: 'desktop' })

  expect((await before.find({ key: 'codex' }))?.text).toContain('확인 불가')
  expect((await before.find({ key: 'codex' }))?.text).not.toContain('✓')
  await before.unmount()
  await clock.advance(20_000)
  expect(ownCodexWorks().find(w => w.id === 'unlinked')?.state).toBe('unknown')
  expect(ownCodexWorks().find(w => w.id === 'unknown')?.state).toBe('unknown')
  const after = await $.ui.mount({ ...ABOVE, surface: 'desktop' })

  expect((await after.find({ key: 'codex' }))?.text).toContain('✓')
  await after.unmount()
  await clock.advance(2 * 60_000)
  const idleAt = clock.now()

  await clock.advance(60_000)
  expect(ticksBetween(idleAt, clock.now())).toEqual(IDLE_TICKS)
})

// The band goes idle a minute after the last activity; a test looks a second
// before that minute ends or a second after it.
const IDLE_MS = 60_000
// A gap between replies, shorter than the idle minute.
const GAP_MS = 31_000
const BEFORE_IDLE_MS = IDLE_MS - 1_000
const PAST_IDLE_MS = IDLE_MS + 1_000
const NO_TICKS = { helpers: 0, places: 0, codex: 0, claude: 0, sessions: 0 }
// Waking reads again what the idle pace paused or slowed; the sessions keep
// their pace and are not read again.
const WAKE_TICKS = { helpers: 1, places: 1, codex: 1, claude: 1, sessions: 0 }
// A minute idle: no helpers or places, Codex and Claude once, the sessions
// every 5 seconds as while at work.
const IDLE_TICKS = { helpers: 0, places: 0, codex: 1, claude: 1, sessions: 12 }
// A minute at work: helpers every 3 seconds, the folder every 15; a second
// short of the idle minute, one helper tick fewer.
const HELPER_TICKS_A_MINUTE = 20
const HELPER_TICKS_BEFORE_IDLE = 19
const PLACE_TICKS_A_MINUTE = 4
const RUNNING_HELPER = { id: 'a1', description: 'Find usage', type: 'Explore', status: 'running' }

// How many ticks of each counted timer ran in (from, to]. Each counted call is
// made once per tick, so a timer started twice counts twice.
function ticksBetween(from: number, to: number): Record<TimerKind, number> {
  const ticks: Record<TimerKind, number> = { ...NO_TICKS }

  for (const call of app.countedCalls) {
    if (call.at > from && call.at <= to) {
      ticks[call.kind] += 1
    }
  }
  return ticks
}

function finishTurn(turnId: string): TurnCompleteInput {
  return {
    turnId,
    answer: '',
    durationMs: 1_000,
    isAborted: false,
    reason: 'answer',
    usage: {
      model: 'claude-opus-5-5',
      input_tokens: 1,
      cache_creation_input_tokens: 0,
      cache_read_input_tokens: 1,
      output_tokens: 1,
    },
  }
}

test('an idle session pauses its fast timers after a minute and slows the rest', async ($, on) => {
  const clock = engine(on)

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const startedAt = clock.now()

  await clock.advance(BEFORE_IDLE_MS)
  expect(ticksBetween(startedAt, clock.now())).toEqual({ helpers: 19, places: 3, codex: 5, claude: 3, sessions: 11 })
  await clock.advance(1_000)
  const idleAt = clock.now()

  await clock.advance(IDLE_MS)
  expect(ticksBetween(idleAt, clock.now())).toEqual(IDLE_TICKS)
})

test('unchanged Codex ticks read neither the session id nor the work list on an idle band', async ($, on) => {
  const record = codexRecord('foreign', START, { ownerSessionId: 'another-session' })
  const output: CodexLocalOutput = { records: [record], working: [record], running: 1 }
  const clock = engine(on, [], undefined, [], true, [], output)

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  const startedAt = clock.now()

  await clock.advance(BEFORE_IDLE_MS - 1)
  expect({
    workReads: app.workReads.filter(at => at > startedAt).map(at => at - startedAt),
    sessionIds: app.sessionIds.filter(at => at > startedAt).map(at => at - startedAt),
  }).toEqual({ workReads: [], sessionIds: [] })
  await clock.advance(2_000)
  const idleAt = clock.now()

  await clock.advance(IDLE_MS)
  // The existing history reader reads works and the id once per minute; Codex adds none.
  expect(app.workReads.filter(at => at > idleAt)).toHaveLength(1)
  expect(app.sessionIds.filter(at => at > idleAt)).toHaveLength(1)
  expect(ticksBetween(idleAt, clock.now())).toEqual(IDLE_TICKS)
})

test('a reply on an idle band reads each paused timer once and goes back to the working pace', async ($, on) => {
  const clock = engine(on)

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(PAST_IDLE_MS)
  const wokeAt = clock.now()

  await $.turn.start({ text: '다음', turnId: 'i1' })
  await clock.advance(1)
  expect(ticksBetween(wokeAt - 1, clock.now())).toEqual(WAKE_TICKS)
  await clock.advance(15_000 - 1)
  expect(ticksBetween(wokeAt + 1, clock.now())).toEqual({ helpers: 5, places: 1, codex: 1, claude: 1, sessions: 3 })
})

test('opening the details wakes an idle band too', async ($, on) => {
  const clock = engine(on)

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(PAST_IDLE_MS)
  const wokeAt = clock.now()
  const ui = await $.ui.mount({ ...ABOVE, surface: 'desktop' })

  await ui.press({ key: 'details' })
  await ui.unmount()
  await clock.advance(1)
  expect(ticksBetween(wokeAt - 1, clock.now())).toEqual(WAKE_TICKS)
})

test('a Codex run this session starts wakes an idle band and keeps it at work', async ($, on) => {
  const clock = engine(on)
  on('tool.call', () => ({ result: { text: 'OK', isError: false } }))

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(PAST_IDLE_MS)
  const wokeAt = clock.now()

  await $.tool.call({ tool: 'Bash', command: 'codex exec "check"', run_in_background: true })
  await clock.advance(1)
  expect(ticksBetween(wokeAt - 1, clock.now())).toEqual(WAKE_TICKS)
  await clock.advance(PAST_IDLE_MS)
  const workingAt = clock.now()

  await clock.advance(IDLE_MS)
  expect(ticksBetween(workingAt, clock.now()).helpers).toBe(HELPER_TICKS_A_MINUTE)
})

test('a wake while a slow read still goes reads once more after it', async ($, on) => {
  const clock = engine(on, [], undefined, [], true, [], { running: 1 })
  let release = () => {}

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(PAST_IDLE_MS)
  sharedReader.hold = new Promise<void>(resolve => {
    release = resolve
  })
  await clock.advance(IDLE_MS)
  const wokeAt = clock.now()

  await $.turn.start({ text: '다음', turnId: 'w1' })
  expect(ticksBetween(wokeAt - 1, clock.now()).codex).toBe(0)
  sharedReader.hold = undefined
  release()
  await clock.advance(1)
  expect(ticksBetween(wokeAt - 1, clock.now()).codex).toBe(1)
})

test('a busy helper keeps the band at work past a minute', async ($, on) => {
  const agents = [RUNNING_HELPER]
  const clock = engine(on, [], undefined, [], true, agents)

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(PAST_IDLE_MS)
  const helperAt = clock.now()

  await clock.advance(IDLE_MS)
  expect(ticksBetween(helperAt, clock.now()).helpers).toBe(HELPER_TICKS_A_MINUTE)
})

test('a helper list that fails keeps the helpers last seen, so the band stays at work', async ($, on) => {
  const agents = [RUNNING_HELPER]
  const clock = engine(on, [], undefined, [], true, agents)

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(GAP_MS)
  app.isAgentListFailing = true
  await clock.advance(PAST_IDLE_MS)
  const failingAt = clock.now()

  await clock.advance(IDLE_MS)
  expect(ticksBetween(failingAt, clock.now()).places).toBe(PLACE_TICKS_A_MINUTE)
})

test('the idle minute starts when a helper is first seen done, not at the last busy look', async ($, on) => {
  const helper = { ...RUNNING_HELPER }
  const clock = engine(on, [], undefined, [], true, [helper])

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(IDLE_MS + 40_000)
  helper.status = 'completed'
  await clock.advance(20_000)
  const quietAt = clock.now()

  await clock.advance(BEFORE_IDLE_MS)
  expect(ticksBetween(quietAt, clock.now()).helpers).toBe(HELPER_TICKS_BEFORE_IDLE)
  await clock.advance(PAST_IDLE_MS - BEFORE_IDLE_MS)
  const idleAt = clock.now()

  await clock.advance(IDLE_MS)
  expect(ticksBetween(idleAt, clock.now()).helpers).toBe(0)
})

test('a running reply keeps the band at work past a minute', async ($, on) => {
  const clock = engine(on)

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.turn.start({ text: '긴 답', turnId: 'r1' })
  await clock.advance(PAST_IDLE_MS)
  const replyAt = clock.now()

  await clock.advance(IDLE_MS)
  expect(ticksBetween(replyAt, clock.now()).places).toBe(PLACE_TICKS_A_MINUTE)
})

test('the gaps between replies leave the timers as they are', async ($, on) => {
  const clock = engine(on)

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.turn.start({ text: '첫 답', turnId: 'g1' })
  await clock.advance(GAP_MS)
  await $.turn.complete(finishTurn('g1'))
  await clock.advance(GAP_MS)
  const gapAt = clock.now()

  await $.turn.start({ text: '둘째 답', turnId: 'g2' })
  expect(ticksBetween(gapAt - 1, gapAt)).toEqual(NO_TICKS)
  await $.turn.complete(finishTurn('g2'))
  await clock.advance(BEFORE_IDLE_MS)
  expect(ticksBetween(gapAt, clock.now()).helpers).toBe(HELPER_TICKS_A_MINUTE)
  await clock.advance(PAST_IDLE_MS - BEFORE_IDLE_MS)
  const idleAt = clock.now()

  await clock.advance(IDLE_MS)
  expect(ticksBetween(idleAt, clock.now()).helpers).toBe(0)
})

// The test engine keeps the timers an earlier start made, as a real reload
// would not: an idle band before the reload adds none of the counted fast ones.
test('a reload starts the band at work and finds the idle minute again', async ($, on) => {
  const clock = engine(on)

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(PAST_IDLE_MS)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const reloadAt = clock.now()

  await clock.advance(BEFORE_IDLE_MS)
  expect(ticksBetween(reloadAt, clock.now()).helpers).toBe(HELPER_TICKS_BEFORE_IDLE)
  await clock.advance(PAST_IDLE_MS - BEFORE_IDLE_MS)
  const idleAt = clock.now()

  await clock.advance(IDLE_MS)
  expect(ticksBetween(idleAt, clock.now()).helpers).toBe(0)
})
