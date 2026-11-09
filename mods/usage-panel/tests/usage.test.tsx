import { expect, mock, test } from 'claude-code/testing'
import type { On, SessionMeasureInput } from 'claude-code'
import type { Engine } from 'claude-code/testing'
import type { CacheOutput, ListedSession, SessionsOutput, SessionsRequest } from '../types'

type SessionRow = ListedSession & { isArchived: boolean }

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
// The Claude reading the sessions share, as its file holds it: none is no file.
const claudeShared: { text?: string } = {}
const CLAUDE_SHARED = '/Users/t/Library/Caches/usage-panel/claude-limits.json'
// What the mod did at start: the 7-day counts it ran and the panes it closed.
const started = { recounts: 0, closed: [] as string[] }
// The other sessions as the app keeps them: its session list, its state files'
// save times, its store's time, what the sessions reader prints, and each
// request the reader was given; this session's transcript as read for the cache;
// the app's id for this session (none outside the app) and each list asked for.
const APP_SESSIONS = '/Users/t/Library/Application Support/Claude/claude-code-sessions'
const APP_STORE = '/Users/t/Library/Application Support/Claude/Local Storage/leveldb'
const app = {
  listed: [] as SessionRow[],
  savedAt: {} as Record<string, number>,
  storeAt: 1,
  output: { sessions: {} } as SessionsOutput,
  requests: [] as SessionsRequest[],
  transcript: {} as CacheOutput,
  hostId: undefined as string | undefined,
  listCalls: [] as Record<string, unknown>[],
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

const engine = (
  on: Parameters<Parameters<typeof test>[1]>[1],
  opened: string[] = [],
  cwd = '/Users/t/Desktop/project/또갈래',
  dirs: string[] = [],
  hasGit = true,
  agents: unknown[] = [],
  codexOutput: object = { running: 0 },
) => {
  const clock = mock.clock(on, { now: Date.parse('2026-11-04T07:00:00Z') })
  mock.store(on)
  claudeShared.text = undefined
  started.recounts = 0
  started.closed = []
  app.listed = []
  app.savedAt = {}
  app.storeAt = 1
  app.output = { sessions: {} }
  app.requests = []
  app.transcript = {}
  app.hostId = undefined
  app.listCalls = []
  on('command.register', () => ({ value: { command: 'usage-panel' } }))
  on('mcp.call', (_, e) => {
    app.listCalls.push(e.args)
    const content = [{ type: 'text' as const, text: JSON.stringify(app.listed) }]

    return { value: { content, isError: e.tool !== 'list_sessions' } }
  })
  on('session.id', () => ({ value: 'cli-1' }))
  on('ui.open', (_, e) => (opened.push(e.id), { value: { isPlaced: true } }))
  on('ui.close', (_, e) => (started.closed.push(e.id), { value: undefined }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('process.run', (_, e) => {
    if (e.argv[4] === 'server' || e.argv[4] === 'local') {
      codexReads.push(e.argv[4])
      return { value: { exitCode: 0, stdout: JSON.stringify(codexOutput), stderr: '' } }
    }
    if (e.argv[4] === 'sessions') {
      app.requests.push(JSON.parse(e.argv[5] ?? '') as SessionsRequest)
      return { value: { exitCode: 0, stdout: JSON.stringify(app.output), stderr: '' } }
    }
    if (e.argv[4] === 'cache') return { value: { exitCode: 0, stdout: JSON.stringify(app.transcript), stderr: '' } }
    if (e.argv[0] === 'date') return { value: { exitCode: 0, stdout: '+0900\n', stderr: '' } }
    if (e.argv[0] === 'git' && !hasGit) return { value: { exitCode: 128, stdout: '', stderr: 'not a git repository' } }
    if (e.argv[0] === 'git' && e.argv.includes('status'))
      return {
        value: {
          exitCode: 0,
          stdout: '# branch.oid 0123456789\n# branch.head main\n# branch.upstream origin/main\n# branch.ab +1 -0\n1 .M N... 100644 100644 100644 a b x.md\n1 .M N... 100644 100644 100644 a b y.md\n? new.txt\n',
          stderr: '',
        },
      }
    if (e.argv[0] === 'git') return { value: { exitCode: 128, stdout: '', stderr: 'no stash' } }
    started.recounts += 1
    return { value: { exitCode: 0, stdout: JSON.stringify(HISTORY), stderr: '' } }
  })
  on('env.get', (_, e) => ({ value: e.name === 'CLAUDE_CODE_HOST_SESSION_ID' ? app.hostId : '/Users/t' }))
  on('session.cwd', () => ({ value: cwd }))
  on('fs.exists', (_, e) => ({
    value: e.path.startsWith(`${CODEX_HOME}sessions/`)
      ? codexDisk.recordAt > 0
      : e.path === CLAUDE_SHARED
        ? claudeShared.text !== undefined
        : e.path === `${CODEX_HOME}config.toml` && codexDisk.configAt > 0,
  }))
  on('fs.read', () => ({ value: claudeShared.text ?? '' }))
  on('fs.write', (_, e) => {
    if (e.path === CLAUDE_SHARED) {
      claudeShared.text = e.text
    }
    return { value: undefined }
  })
  on('fs.stat', () => ({ value: { kind: 'file', size: 1, mtimeMs: codexDisk.configAt, isLink: false } }))
  on('agent.list', () => ({ value: agents }))
  on('fs.list', (_, e) => {
    if (e.path === APP_STORE) return { value: [{ name: '000003.log', kind: 'file', size: 1, mtimeMs: app.storeAt, isLink: false }] }
    if (e.path === `${APP_SESSIONS}/account/organization`)
      return {
        value: Object.entries(app.savedAt).map(([id, mtimeMs]) => ({ name: `${id}.json`, kind: 'file', size: 1, mtimeMs, isLink: false })),
      }
    if (e.path.startsWith(APP_SESSIONS)) {
      const name = e.path === APP_SESSIONS ? 'account' : 'organization'
      return { value: [{ name, kind: 'dir', size: 0, mtimeMs: 0, isLink: false }] }
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
    expect((await ui.find({ type: 'Text', text: /^↻Mon 05:00$/ }))?.text).toBe('↻Mon 05:00')
    expect((await ui.find({ type: 'Text', text: /^Context / }))?.text).toBe('Context 15%')
    expect((await ui.find({ type: 'Text', text: /^·151K\/1M$/ }))?.text).toBe('·151K/1M')
    expect(await ui.find({ type: 'Text', text: /^Last/ })).toBeUndefined()
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

test('the line shows the last reply once one finished', async ($, on) => {
  engine(on)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.turn.start({ text: '추천으로 해줘', turnId: 't2' })
  await $.turn.complete({
    turnId: 't2', answer: '', durationMs: 5_000, isAborted: false, reason: 'answer',
    usage: { model: 'claude-opus-5-5', input_tokens: 2, cache_creation_input_tokens: 2_000, cache_read_input_tokens: 150_000, output_tokens: 1_200 },
  })

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...ABOVE, surface })
    expect((await ui.find({ type: 'Text', text: /^Last/ }))?.text).toBe('Last In 152K·Out 1.2K')
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

test('a running reply shows its counts without a Working label', async ($, on) => {
  engine(on)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.turn.start({ text: '진행 중', turnId: 'w1' })
  const band = await $.ui.mount({ ...ABOVE, surface: 'desktop' })
  expect((await band.find({ type: 'Text', text: /^In / }))?.text).toBe('In 0·Out 0 ×0')
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

test('a Codex line follows the Claude line with its limits, last run and details', async ($, on) => {
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
    expect((await ui.find({ type: 'Text', text: /^Last/ }))?.text).toBe('Last In 620K·Out 9.4K')
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
  await $.tool.call({ tool: 'Bash', command: 'codex exec -s read-only "review"', run_in_background: true })
  await $.tool.call({ tool: 'Bash', command: 'ls' })
  await clock.advance(10_000)

  expect(codexReads).toEqual(['server', 'local', 'local'])
})

test('the Codex line names the model and effort the next run takes, just before its details', async ($, on) => {
  const clock = engine(on, [], undefined, [], true, [], CODEX)

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...ABOVE, surface })
    const line = await ui.find({ key: 'codex' })
    const keys = (line?.children ?? []).map(child => (child as { props?: { key?: string } }).props?.key)

    expect((await ui.find({ key: 'codex-setting' }))?.text).toBe('gpt-6-astra xhigh')
    expect(keys.slice(-3)).toEqual(['codex-last', 'codex-setting', 'codex-details'])
    await ui.unmount()
  }

  const pane = await $.ui.mount({ ...PANE, surface: 'desktop' })
  const configured = await pane.find({ type: 'Text', text: /^기본값/ })

  expect(configured?.text).toBe('기본값 gpt-6-astra xhigh · 16:00에 읽음')
  await pane.unmount()
})

test('a Codex run at work shows its totals so far beside a spinner, as the Claude line does', async ($, on) => {
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
  const live = await ui.find({ key: 'codex-last' })
  const spinners = (await ui.findAll({ type: 'Svg' })).filter(svg => svg.props.alt === 'working')
  const first = (live?.children ?? [])[0] as { props?: { alt?: string } } | undefined

  expect(live?.text).toBe('In 5.9M·Out 21.7K ×37 +1 run')
  expect(spinners).toHaveLength(1)
  expect(first?.props?.alt).toBe('working')
  expect((await ui.find({ key: 'codex-setting' }))?.text).toBe('gpt-6-sol high')
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

  expect((await after.find({ key: 'codex-last' }))?.text).toBe('Last In 620K·Out 9.4K')
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
  codexDisk.configAt = 100
  const clock = engine(on, [], undefined, [], true, [], { running: 0 })

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(10_000)
  codexDisk.recordAt = 5_000
  await clock.advance(10_000)
  codexDisk.configAt = 200
  await clock.advance(10_000)
  codexDisk.configAt = 0

  expect(codexReads).toEqual(['server', 'local', 'server'])
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
  expect(claudeShared.text).toBe(sharedReading('2026-11-04T07:00:00Z', 30))
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
  expect(codexReads).toEqual(['server'])
  expect(started.closed).toEqual(['usage'])
})

const row = (sessionId: string, isRunning: boolean, lastActivityAt = '2026-11-04T06:59:00Z'): SessionRow => ({
  sessionId,
  isRunning,
  lastActivityAt,
  isArchived: false,
})

const read = (verdict: 'needs-input' | 'failed' | 'other', isAsking = false) => ({ verdict, isAsking })

const sessionLine = async (surface: 'terminal' | 'desktop', mount: (s: 'terminal' | 'desktop') => Promise<{ find: (q: { key: string }) => Promise<{ text?: string } | undefined>; unmount: () => Promise<void> }>) => {
  const ui = await mount(surface)
  const found = await ui.find({ key: 'sessions' })

  await ui.unmount()
  return found?.text
}

test('the place line counts the other sessions as the sidebar marks them', async ($, on) => {
  const clock = engine(on)

  app.listed = [row('a', true), row('b', true), row('c', false), row('d', false), row('e', false), row('f', false)]
  app.savedAt = { a: 1, b: 1, c: 1, d: 1, e: 1, f: 1 }
  app.output = {
    unread: ['c', 'd', 'f', 'archived'],
    sessions: { a: read('other'), b: read('needs-input'), c: read('needs-input'), d: read('other'), e: read('needs-input'), f: read('failed') },
  }
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...ABOVE, surface })

    expect((await ui.find({ key: 'sessions' }))?.text).toBe('2 working · 1 waiting · 1 done')
    expect((await ui.find({ type: 'Text', text: /^1 waiting$/ }))?.props.color).toBe('warning')
    expect((await ui.find({ type: 'Text', text: /^1 done$/ }))?.props.color).toBe('suggestion')
    await ui.unmount()
  }
  expect(app.requests[0]?.store).toBe(APP_STORE)
  // The sessions at work keep saving their files: theirs are not read.
  expect(app.requests[0]?.sessions.map(asked => asked.id)).toEqual(['c', 'd', 'e', 'f'])
})

test('a count of none is left out, and no other session leaves the place line as it was', async ($, on) => {
  const clock = engine(on)

  app.listed = [row('a', true)]
  app.savedAt = { a: 1 }
  app.output = { unread: [], sessions: { a: read('other') } }
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  expect(await sessionLine('desktop', surface => $.ui.mount({ ...ABOVE, surface }))).toBe('1 working')

  app.listed = []
  await clock.advance(5_000)
  expect(await sessionLine('desktop', surface => $.ui.mount({ ...ABOVE, surface }))).toBeUndefined()
})

test('a store it cannot read leaves done and the finished turns waiting out, and the rest counted', async ($, on) => {
  const clock = engine(on)

  app.listed = [row('a', true), row('c', false), row('d', false)]
  app.savedAt = { a: 1, c: 1, d: 1 }
  app.output = { unread: null, sessions: { a: read('other'), c: read('needs-input'), d: read('other') } }
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)

  expect(await sessionLine('desktop', surface => $.ui.mount({ ...ABOVE, surface }))).toBe('1 working')
})

test('a file or the store is read again only once the app saved it', async ($, on) => {
  const clock = engine(on)

  app.listed = [row('a', false), row('b', false)]
  app.savedAt = { a: 1, b: 1 }
  app.output = { unread: ['a'], sessions: { a: read('other'), b: read('other') } }
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  await clock.advance(5_000)
  expect(app.requests).toHaveLength(1)

  app.savedAt = { a: 1, b: 2 }
  await clock.advance(5_000)
  app.storeAt = 2
  await clock.advance(5_000)

  expect(app.requests.slice(1).map(request => [request.store, request.sessions.map(asked => asked.id)])).toEqual([
    [undefined, ['b']],
    [APP_STORE, []],
  ])
})

test('a session at work that stands still is looked at once for a question still open', async ($, on) => {
  const clock = engine(on)

  app.listed = [row('a', true, '2026-11-04T06:59:00Z')]
  app.savedAt = { a: 1 }
  app.output = { unread: [], sessions: { a: read('other') } }
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1)
  app.output = { sessions: { a: read('other', true) } }
  await clock.advance(5_000)
  expect(await sessionLine('desktop', surface => $.ui.mount({ ...ABOVE, surface }))).toBe('1 waiting')
  await clock.advance(5_000)

  app.listed = [row('a', true, '2026-11-04T07:00:10Z')]
  await clock.advance(5_000)
  app.output = { sessions: { a: read('other', false) } }
  await clock.advance(5_000)

  // The first tick reads the store alone: the session at work keeps saving its file.
  expect(app.requests.map(request => request.sessions.map(asked => asked.readsQuestion))).toEqual([[], [true], [true]])
  expect(await sessionLine('desktop', surface => $.ui.mount({ ...ABOVE, surface }))).toBe('1 working')
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
