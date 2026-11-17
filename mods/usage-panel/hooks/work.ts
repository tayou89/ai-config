import type { CodexTotals, Level, Work, WorkLoad, WorkMaker, WorkPace } from '../types'

export const MINUTE_MS = 60 * 1000
const DAY_MS = 24 * 60 * MINUTE_MS
export const WORK_KEEP_MS = 7 * DAY_MS
// Work shorter than a minute says little about a pace.
const PACE_MIN_MS = MINUTE_MS
const PACE_KEEP = 30
const PACE_MIN_COUNT = 3
// A rise under one point is the window's rounding, not the work.
const RISE_MIN = 1
const PERCENT_KEEP = 10
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

// Points of the 5-hour window per unit of weight, from work that ran alone
// and rose the window by a point or more.
function percentPerWeight(works: readonly Work[], maker: WorkMaker): number | undefined {
  const rates = works
    .filter(w => w.maker === maker && w.isAlone && w.isAborted !== true && (w.rise ?? 0) >= RISE_MIN && w.weight > 0)
    .sort((a, b) => (a.finishedAt ?? 0) - (b.finishedAt ?? 0))
    .slice(-PERCENT_KEEP)
    .map(w => (w.rise ?? 0) / w.weight)

  return settledMedian(rates)
}

// Claude's pace is per step and token of the conversation, since each step
// reads the whole conversation again; Codex's is per minute.
export function workPace(works: readonly Work[], maker: WorkMaker): WorkPace {
  const samples = paceSamples(works, maker)
  const percent = percentPerWeight(works, maker)

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
