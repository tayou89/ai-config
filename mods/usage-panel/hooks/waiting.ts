import type { OwnSession, OwnSidebarPlace, WaitingInput, WaitingRecord, WaitingStep } from '../types'

type MovedRecord = Extract<WaitingRecord, { kind: 'moved' }>

export const UNGROUPED_SECTION = 'sessions'
export const WAITING_SECTION_NAME = '답 기다림'
const STAY: WaitingStep = { kind: 'stay' }
const TO_UNGROUPED: WaitingStep = { kind: 'leave', toSectionId: UNGROUPED_SECTION }

export function sidebarPlace(own: OwnSession): OwnSidebarPlace {
  return { groupId: own.group?.id, isPinned: own.pinned === true, activityAt: own.lastActivityAt }
}

function isWaiting(input: WaitingInput): boolean {
  return input.isOn && input.state === 'waiting'
}

// A change the app refused or could not make is not asked again until the
// session's activity moves on, so a refusing app is not called every tick.
function isRefusedNow(input: WaitingInput): boolean {
  return input.refused !== undefined && input.refused.activityAt === input.own.activityAt
}

// A session the band moved leaves once its activity moved on, which a reply
// does and a mere look does not; one the person moved out is let go.
function movedStep(input: WaitingInput, record: MovedRecord): WaitingStep {
  const isDue = !input.isOn || input.own.activityAt !== record.activityAt

  if (input.own.groupId !== record.sectionId) {
    return { kind: 'release', activityAt: record.activityAt }
  } else if (isDue && !isRefusedNow(input)) {
    return { kind: 'leave', toSectionId: record.fromGroupId ?? UNGROUPED_SECTION }
  } else {
    return STAY
  }
}

// A pinned session already sits above every section, and moving it would
// unpin it; a wait the person took it out of stays out.
function isEntering(input: WaitingInput): boolean {
  const { own, record } = input
  const isReleased = record?.kind === 'released' && record.activityAt === own.activityAt

  return isWaiting(input) && !own.isPinned && !isReleased && !isRefusedNow(input)
}

// What this session does about its own place in the sidebar on one tick. A
// session in the section with no record of the band's (one whose record was
// lost) goes to the ungrouped list once it no longer waits.
export function decideWaitingStep(input: WaitingInput): WaitingStep {
  const { own, record, sectionId } = input
  const isInSection = sectionId !== undefined && own.groupId === sectionId

  if (record?.kind === 'moved') {
    return movedStep(input, record)
  } else if (isInSection) {
    return isWaiting(input) || isRefusedNow(input) ? STAY : TO_UNGROUPED
  } else if (isEntering(input)) {
    return { kind: 'enter', fromGroupId: own.groupId, activityAt: own.activityAt }
  } else if (record?.kind === 'released' && record.activityAt !== own.activityAt) {
    return { kind: 'forget' }
  } else {
    return STAY
  }
}
