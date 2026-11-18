import type { CodexTotals, Level, LimitStep, Work, WorkLoad, WorkMaker, WorkPace } from '../types'

export const MINUTE_MS = 60 * 1000
const DAY_MS = 24 * 60 * MINUTE_MS
export const WORK_KEEP_MS = 7 * DAY_MS
// Work shorter than a minute says little about a pace.
const PACE_MIN_MS = MINUTE_MS
const PACE_KEEP = 30
const PACE_MIN_COUNT = 3
// The weekly window rises in whole points: below three, one point of rounding
// is a third of the rate or more.
const WEEK_RISE_MIN = 3
// A week that ends this much later than the one before is a new week.
const RESET_SHIFT_MS = DAY_MS
// How far over its estimate a piece of work went before it turns yellow, then red.
const OVER_WARN = 1.2
const OVER_HIGH = 1.5
// A Codex run's weight on the scale a Claude reply's is weighed on: uncached
// input 1, cached input 0.1, output 5.
const CACHED_WEIGHT = 0.1
const OUTPUT_WEIGHT = 5

function median(values: readonly number[]): number | undefined {
  const sorted = [...values].sort((a, b) => a - b)
  const middle = Math.floor(sorted.length / 2)

  if (sorted.length === 0) {
    return undefined
  } else if (sorted.length % 2 === 1) {
    return sorted[middle]
  } else {
    return ((sorted[middle - 1] ?? 0) + (sorted[middle] ?? 0)) / 2
  }
}

// The median once enough work stands behind it, none before.
function settledMedian(values: readonly number[]): number | undefined {
  return values.length >= PACE_MIN_COUNT ? median(values) : undefined
}

function lastingMs(work: Work): number {
  return (work.finishedAt ?? work.startedAt) - work.startedAt
}

// A stopped reply says nothing of what its work would have cost.
function isPaceSample(work: Work, maker: WorkMaker): boolean {
  const isFinished = work.finishedAt !== undefined && work.isAborted !== true

  return work.maker === maker && isFinished && lastingMs(work) >= PACE_MIN_MS && work.weight > 0
}

// The newest finished work of one maker, oldest first, as the pace reads it.
function paceSamples(works: readonly Work[], maker: WorkMaker): Work[] {
  return works
    .filter(w => isPaceSample(w, maker))
    .sort((a, b) => (a.finishedAt ?? 0) - (b.finishedAt ?? 0))
    .slice(-PACE_KEEP)
}

export function isNewWeek(before: Pick<LimitStep, 'resetsAt'>, after: Pick<LimitStep, 'resetsAt'>): boolean {
  const beforeEnd = Date.parse(before.resetsAt ?? '')
  const afterEnd = Date.parse(after.resetsAt ?? '')

  return afterEnd - beforeEnd > RESET_SHIFT_MS
}

// The steps since the window last started over: after the last one whose
// percent fell or whose week ends a day or more later than the one before.
function sinceReset(sorted: readonly LimitStep[]): LimitStep[] {
  const isReset = (step: LimitStep, i: number): boolean => {
    const before = sorted[i - 1]

    return before !== undefined && (step.percentUsed < before.percentUsed || isNewWeek(before, step))
  }
  const start = sorted.reduce((found, step, i) => (isReset(step, i) ? i : found), 0)

  return sorted.slice(start)
}

// Points of the weekly window per unit of weight: how far the window rose
// since it last started over, over the weight of every session's work of the
// tool that finished meanwhile. The window is the account's, so all of it
// counts; none until it rose three points.
function percentPerWeight(works: readonly Work[], steps: readonly LimitStep[], maker: WorkMaker): number | undefined {
  const week = sinceReset(steps.filter(s => s.maker === maker).sort((a, b) => a.at - b.at))
  const first = week[0]
  const last = week[week.length - 1]

  if (first && last && last.percentUsed - first.percentUsed >= WEEK_RISE_MIN) {
    const isWithin = (w: Work): boolean =>
      w.finishedAt !== undefined && w.finishedAt > first.at && w.finishedAt <= last.at
    const weight = works.filter(w => w.maker === maker && isWithin(w)).reduce((sum, w) => sum + w.weight, 0)

    return weight > 0 ? (last.percentUsed - first.percentUsed) / weight : undefined
  } else {
    return undefined
  }
}

// Claude's pace is per step and token of the conversation, since each step
// reads the whole conversation again; Codex's is per minute.
export function workPace(works: readonly Work[], steps: readonly LimitStep[], maker: WorkMaker): WorkPace {
  const samples = paceSamples(works, maker)
  const percent = percentPerWeight(works, steps, maker)

  if (maker === 'claude') {
    const reads = samples.filter(w => (w.startContext ?? 0) > 0 && w.steps > 0)
    const size = (w: Work): number => w.steps * (w.startContext ?? 1)

    return {
      weightPerStepContext: settledMedian(reads.map(w => w.weight / size(w))),
      tokensPerStepContext: settledMedian(reads.map(w => w.tokens / size(w))),
      percentPerWeight: percent,
    }
  } else {
    const minutes = (w: Work): number => lastingMs(w) / MINUTE_MS

    return {
      weightPerMinute: settledMedian(samples.map(w => w.weight / minutes(w))),
      tokensPerMinute: settledMedian(samples.map(w => w.tokens / minutes(w))),
      percentPerWeight: percent,
    }
  }
}

function withPercent(tokens: number, weight: number, pace: WorkPace): WorkLoad {
  return {
    tokens,
    weight,
    percent: pace.percentPerWeight === undefined ? undefined : weight * pace.percentPerWeight,
  }
}

// What the estimate says the work will cost; none until the pace is known or
// without an estimate (or, for Claude, without its steps).
export function expectedLoad(work: Work, pace: WorkPace): WorkLoad | undefined {
  const estimate = work.estimate
  const context = estimate?.contextTokens ?? work.startContext ?? 0
  const steps = estimate?.steps ?? 0

  if (estimate && work.maker === 'claude' && pace.weightPerStepContext && pace.tokensPerStepContext) {
    return steps > 0 && context > 0
      ? withPercent(steps * context * pace.tokensPerStepContext, steps * context * pace.weightPerStepContext, pace)
      : undefined
  } else if (estimate && work.maker === 'codex' && pace.weightPerMinute && pace.tokensPerMinute) {
    return withPercent(estimate.minutes * pace.tokensPerMinute, estimate.minutes * pace.weightPerMinute, pace)
  } else {
    return undefined
  }
}

// What the work cost so far, its points by the same rate as its estimate.
export function actualLoad(work: Work, pace: WorkPace): WorkLoad {
  return withPercent(work.tokens, work.weight, pace)
}

// Yellow past a fifth over the estimate, red past a half; within it, green.
export function overLevel(actual: number, expected: number): Level {
  const ratio = expected > 0 ? actual / expected : 1

  if (ratio > OVER_HIGH) {
    return 'high'
  } else if (ratio > OVER_WARN) {
    return 'warn'
  } else {
    return 'ok'
  }
}

export function codexWeight(totals: Pick<CodexTotals, 'read' | 'cached' | 'write'>): number {
  return totals.read - totals.cached + totals.cached * CACHED_WEIGHT + totals.write * OUTPUT_WEIGHT
}

export function codexTokens(totals: Pick<CodexTotals, 'read' | 'write'>): number {
  return totals.read + totals.write
}

// Finished work younger than the week, the newest last.
export function keptWorks(works: readonly Work[], now: number): Work[] {
  return works
    .filter(w => w.finishedAt !== undefined && now - w.finishedAt < WORK_KEEP_MS)
    .sort((a, b) => (a.finishedAt ?? 0) - (b.finishedAt ?? 0))
}

// Limit steps younger than the week, the oldest first.
export function keptSteps(steps: readonly LimitStep[], now: number): LimitStep[] {
  return steps.filter(s => now - s.at < WORK_KEEP_MS).sort((a, b) => a.at - b.at)
}

// How much longer or shorter than estimated, as a share of the estimate.
function overShare(actual: number, expected: number): number {
  return expected > 0 ? actual / expected - 1 : 0
}

// An actual measure and its estimate, or none when the work has no estimate of it.
export type MeasurePair = readonly [actual: number, expected: number]

// The median of how far finished work went past its estimate, per measure;
// a stopped reply is left out.
export function averageOver(
  works: readonly Work[],
  measure: (work: Work) => MeasurePair | undefined,
): number | undefined {
  const shares = works.flatMap(w => {
    const pair = w.isAborted === true ? undefined : measure(w)

    return pair ? [overShare(pair[0], pair[1])] : []
  })

  return median(shares)
}
