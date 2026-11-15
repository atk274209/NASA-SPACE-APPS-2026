import { request } from './client'

/**
 * done: the routine was recorded that day.
 * missed: AMC holds other records for that day, but not this one.
 * no_data: AMC holds no records at all for that day, so it cannot tell.
 * pending: today, still open.
 */
export type StreakDayState = 'done' | 'missed' | 'no_data' | 'pending'

export interface StreakDay {
  mission_day: number
  day_date: string
  state: StreakDayState
  /** The day counted only because of records from the AMC simulator. */
  simulated: boolean
}

export interface StreakMilestone {
  days: number
  label: string
  reached: boolean
  reached_day: number | null
}

export interface Streak {
  code: string
  title: string
  rule: string
  current: number
  at_risk: boolean
  today_done: boolean
  longest: number
  /** [first, last] mission day of the longest run, or [] when there is none. */
  longest_days: number[]
  total_done: number
  next_milestone: number | null
  milestones: StreakMilestone[]
  calendar: StreakDay[]
}

export interface StreakReport {
  astronaut_id: number
  mission_id: number
  mission_start: string
  mission_day: number
  today: string
  simulated: boolean
  window_days: number
  rules: string
  milestone_note: string
  streaks: Streak[]
}

export const streaksApi = {
  get: (astronaut_id: number) => request<StreakReport>('GET', '/streaks', undefined, { astronaut_id }),
}
