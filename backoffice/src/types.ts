export type OrgType = "club" | "solo_coach";
export type MemberRole = "owner" | "admin" | "coach" | "athlete";

export interface AthleteProfile {
  sport: string;
  level: string;
  birth_date: string;
  height_cm: number | null;
  weight_kg: number | null;
  hr_max: number | null;
  hr_rest: number | null;
}

export interface CoachSportEntry {
  sport: string;
  level: string;
  years_experience: number | null;
}

export interface CoachProfile {
  sports: CoachSportEntry[];
  certifications: string | null;
  bio: string | null;
}

export interface HealthConsent {
  granted: boolean;
  version: string;
  granted_at: string;
}

export interface UserProfile {
  uid: string;
  email: string | null;
  email_verified: boolean;
  first_name: string | null;
  last_name: string | null;
  phone: string | null;
  locale: string;
  athlete_profile: AthleteProfile | null;
  coach_profile?: CoachProfile | null;
  health_consent: HealthConsent | null;
}

export interface Organization {
  id: string;
  type: OrgType;
  name: string;
  sports: string[];
  city: string | null;
  country: string;
  description?: string | null;
  is_public?: boolean;
  accepting_join_requests?: boolean;
  owner_uid: string;
  status: string;
  settings?: {
    max_recording_s?: number | null;
  };
}

export interface OrgCoachInfo {
  uid: string;
  name: string;
  role: MemberRole;
  coach_profile?: CoachProfile | null;
}

export interface Membership {
  org_id: string;
  role: MemberRole;
  group_ids: string[];
  organization: Organization | null;
  coaches?: OrgCoachInfo[];
}

export interface MeResponse {
  uid: string;
  email: string | null;
  email_verified: boolean;
  platform_admin: boolean;
  needs_onboarding: boolean;
  user: UserProfile | null;
  memberships: Membership[];
}

export interface OrgMember {
  uid: string;
  role: MemberRole;
  group_ids: string[];
  coach_uids?: string[];
  invited_by?: string | null;
  email: string | null;
  first_name: string | null;
  last_name: string | null;
  has_athlete_profile: boolean;
  sport: string | null;
  level: string | null;
  has_coach_profile?: boolean;
  coach_profile?: CoachProfile | null;
  created_at: string | null;
}

export interface GroupItem {
  id: string;
  org_id: string;
  name: string;
  sport: string;
  coach_uids: string[];
  created_at: string | null;
}

export interface InvitationItem {
  id: string;
  org_id: string;
  email: string;
  role: "coach" | "athlete";
  group_ids: string[];
  status: "pending" | "accepted" | "revoked" | "expired" | "declined";
  expires_at: string;
  created_at: string;
}

export interface PendingInvitation extends InvitationItem {
  org_name: string;
  org_type: OrgType;
  inviter_name: string | null;
}

export interface JoinRequestItem {
  id: string;
  org_id: string;
  uid: string;
  requested_sports: string[];
  requested_sports_details?: Array<{
    code: string;
    label: string;
    is_indoor: boolean;
    category: string;
  }>;
  requested_group_id?: string | null;
  requested_group_name?: string | null;
  target_coach_uid?: string | null;
  target_coach_name?: string | null;
  message?: string | null;
  status: "pending" | "approved" | "rejected" | "cancelled";
  decision_note?: string | null;
  decided_by?: string | null;
  decided_at?: string | null;
  assigned_group_ids?: string[];
  assigned_coach_uids?: string[];
  created_at: string;
  applicant: {
    uid: string;
    email?: string | null;
    first_name?: string | null;
    last_name?: string | null;
    photo_url?: string | null;
    athlete_profile?: {
      sport?: string | null;
      level?: string | null;
      birth_date?: string | null;
      height_cm?: number | null;
    } | null;
  };
}

export interface AthleteKpiRow {
  uid: string;
  name: string;
  ctl: number;
  atl: number;
  tsb: number;
  tss_7d: number;
  tss_28d: number;
  activities_count_7d: number;
  compliance_pct_28d: number;
  latest_wellness?: {
    date: string;
    sleep_h?: number | null;
    fatigue?: number | null;
  } | null;
}

export interface AthletesKpisResponse {
  athletes: AthleteKpiRow[];
  team: {
    athletes_count: number;
    avg_ctl: number;
    total_tss_7d: number;
    avg_compliance_pct: number;
  };
}

export interface NotificationItem {
  id: string;
  type: string;
  title: string;
  body: string;
  link?: string | null;
  org_id?: string | null;
  read: boolean;
  created_at: string;
}

export interface AthleteReport {
  uid: string;
  athlete_name: string;
  sport?: string | null;
  period: { from: string; to: string; days: number };
  activities: {
    count: number;
    total_duration_s: number;
    total_distance_km: number;
    total_tss: number;
  };
  pmc: {
    start: { date: string; ctl: number; atl: number; tsb: number } | null;
    end: { date: string; ctl: number; atl: number; tsb: number } | null;
  };
  compliance: {
    planned_total: number;
    planned_done: number;
    pct: number;
  };
  records_power_w: {
    "5s": number | null;
    "1m": number | null;
    "5m": number | null;
    "20m": number | null;
  };
  wellness: {
    entries_count: number;
    avg_sleep_h: number | null;
    avg_fatigue: number | null;
  };
}

export interface SessionItem {
  room_name: string;
  org_id: string;
  owner_uid: string;
  group_id: string | null;
  title: string | null;
  scheduled_at: string | null;
  participant_uids: string[];
  count: number;
  recording: boolean;
  finished: boolean;
  joinable?: boolean;
  joinable_reason?: string | null;
  created_at: string | null;
}

export interface SessionRecording {
  egress_id?: string;
  status?: string;
  mp4_url?: string;
  duration_s?: number;
  video_started_at?: string;
}

export interface SessionDetail {
  room_name: string;
  title?: string;
  org_id: string;
  created_at?: string;
  finished?: boolean;
  joinable?: boolean;
  joinable_reason?: string | null;
  participants?: Array<{
    uid?: string;
    identity?: string;
    display_name?: string;
    email?: string;
    role?: string;
  }>;
  recordings?: SessionRecording[];
}

export interface TelemetryPoint {
  pts_ms: number;
  server_ts?: number;
  participant?: string;
  speed?: number;
  heart_rate?: number;
  cadence?: number;
  power_w?: number | null;
  lat?: number | null;
  lng?: number | null;
  altitude?: number | null;
  bearing?: number | null;
}

export interface SessionAlert {
  id?: string;
  _id?: string;
  type: string;
  value?: number;
  threshold?: number;
  participant?: string;
  pts_ms?: number;
  message?: string;
  ts?: number;
}

export interface CoachMarker {
  id?: string;
  _id?: string;
  room_name: string;
  org_id: string;
  pts_ms: number;
  label: string;
  note?: string | null;
  category: string;
  created_by?: string;
  author_name?: string;
  created_at?: string;
}

export interface PowerZone {
  zone: string;
  name: string;
  min_w: number;
  max_w: number | null;
}

export interface HrZone {
  zone: string;
  name: string;
  min_bpm: number;
  max_bpm: number | null;
}

export interface PaceZone {
  zone: string;
  name: string;
  fast_s_per_km: number | null;
  slow_s_per_km: number | null;
}

export interface ThresholdProfile {
  id: string;
  org_id: string;
  uid: string;
  sport: string;
  effective_from: string;
  ftp_w: number | null;
  lthr_bpm: number | null;
  threshold_pace_s_per_km: number | null;
  pace_500m_s?: number | null;
  css_s_per_100m: number | null;
  hr_max: number | null;
  hr_rest: number | null;
  zones_model: "coggan_7" | "friel_5" | "custom";
  power_zones: PowerZone[];
  hr_zones: HrZone[];
  pace_zones: PaceZone[];
}

export interface ActivityMetrics {
  avg_power?: number | null;
  max_power?: number | null;
  normalized_power?: number | null;
  intensity_factor?: number | null;
  tss?: number | null;
  tss_method?: string | null;
  power_tss?: number | null;
  hr_tss?: number | null;
  rtss?: number | null;
  trimp?: number | null;
  avg_hr?: number | null;
  max_hr?: number | null;
  avg_speed_kmh?: number | null;
  max_speed_kmh?: number | null;
  avg_pace_s_per_km?: number | null;
  avg_cadence?: number | null;
  elevation_gain_m?: number | null;
  time_in_power_zones?: Record<string, number>;
  time_in_hr_zones?: Record<string, number>;
  mean_max_power?: Record<string, number>;
  mean_max_hr?: Record<string, number>;
}

export interface ActivityLap {
  index: number;
  duration_s: number;
  distance_m: number;
  avg_power?: number | null;
  avg_hr?: number | null;
  avg_cadence?: number | null;
}

export interface VideoAsset {
  id: string;
  kind?: string;
  mp4_url: string;
  duration_s?: number | null;
  offset_ms: number;
}

export interface ActivitySummary {
  id: string;
  org_id: string;
  uid: string;
  athlete_name?: string;
  sport: string;
  title: string;
  start_time: string;
  duration_s: number;
  moving_time_s: number;
  distance_m: number;
  elevation_gain_m?: number | null;
  source: string;
  room_name?: string | null;
  metrics: ActivityMetrics;
  rpe?: number | null;
  feeling?: number | null;
  notes?: string | null;
  shared_org_ids?: string[];
}

export interface PlannedWorkoutSummary {
  id: string;
  title: string;
  date?: string | null;
  estimated_duration_s: number;
  estimated_tss: number;
  status?: string;
  compliance?: {
    duration_pct: number;
    tss_pct?: number | null;
    score: number;
    color: "green" | "orange" | "red";
  } | null;
}

export interface ActivityDetail extends ActivitySummary {
  laps: ActivityLap[];
  zones?: {
    power_zones: PowerZone[];
    hr_zones: HrZone[];
  };
  video_assets?: VideoAsset[];
  planned_workout_id?: string | null;
  planned_workout_summary?: PlannedWorkoutSummary | null;
}

export interface ActivityStreams {
  activity_id: string;
  t: number[];
  hr: (number | null)[];
  power: (number | null)[];
  cadence: (number | null)[];
  speed: (number | null)[];
  lat: (number | null)[];
  lng: (number | null)[];
  alt: (number | null)[];
}

export interface ActivityComment {
  id: string;
  activity_id: string;
  author_uid: string;
  author_name?: string | null;
  author_role?: string | null;
  body: string;
  created_at: string;
}

export type WorkoutStepKind =
  | "warmup"
  | "active"
  | "recovery"
  | "cooldown"
  | "rest"
  | "repeat";

export type WorkoutTargetType =
  | "ftp_pct"
  | "lthr_pct"
  | "pace_pct"
  | "rpe"
  | "open";

export interface WorkoutStep {
  kind: WorkoutStepKind;
  duration_s?: number | null;
  distance_m?: number | null;
  reps?: number | null;
  target_type: WorkoutTargetType;
  target_min?: number | null;
  target_max?: number | null;
  repeat_count?: number;
  steps?: WorkoutStep[];
  note?: string | null;
}

export interface WorkoutBlockPreview {
  index: number;
  kind: string;
  duration_s: number;
  reps?: number | null;
  intensity_pct: number;
}

export interface WorkoutTemplate {
  id: string;
  org_id: string;
  owner_uid: string;
  name: string;
  sport: string;
  description?: string | null;
  tags: string[];
  steps: WorkoutStep[];
  estimated_duration_s: number;
  estimated_distance_m: number;
  estimated_tss: number;
  blocks: WorkoutBlockPreview[];
}

export interface WorkoutComment {
  id: string;
  planned_workout_id: string;
  author_uid: string;
  author_name: string;
  author_role: string;
  body: string;
  created_at: string;
}

export interface PlannedWorkout {
  id: string;
  org_id: string;
  uid: string;
  date: string;
  start_time?: string | null;
  template_id?: string | null;
  title: string;
  sport: string;
  description?: string | null;
  steps: WorkoutStep[];
  coach_note?: string | null;
  status: "planned" | "done" | "partial" | "missed";
  activity_id?: string | null;
  compliance?: {
    duration_pct: number;
    tss_pct?: number | null;
    score: number;
    color: "green" | "orange" | "red";
  } | null;
  estimated_duration_s: number;
  estimated_distance_m: number;
  estimated_tss: number;
  blocks: WorkoutBlockPreview[];
}

export interface GoalItem {
  id: string;
  uid: string;
  title: string;
  date: string;
  sport: string;
  priority: "A" | "B" | "C";
  distance_m?: number | null;
  notes?: string | null;
}

export interface CalendarResponse {
  planned_workouts: PlannedWorkout[];
  activities: Array<{
    id: string;
    uid: string;
    date: string;
    sport: string;
    title: string;
    duration_s: number;
    distance_m: number;
    tss?: number | null;
    planned_workout_id?: string | null;
  }>;
  goals: GoalItem[];
  weekly_totals: Array<{
    week_start: string;
    planned_duration_s: number;
    actual_duration_s: number;
    planned_tss: number;
    actual_tss: number;
  }>;
}

export interface TrainingPlanItem {
  day_offset: number;
  template_id?: string | null;
  title: string;
  sport: string;
  description?: string | null;
  steps: WorkoutStep[];
  estimated_duration_s?: number;
  estimated_tss?: number;
}

export interface TrainingPlan {
  id: string;
  org_id: string;
  name: string;
  sport: string;
  weeks: number;
  description?: string | null;
  items: TrainingPlanItem[];
}

export interface DailyMetricPoint {
  date: string;
  tss: number;
  ctl: number;
  atl: number;
  tsb: number;
  ramp_rate: number;
  is_projected?: boolean;
}

export interface WellnessEntry {
  date: string;
  sleep_h?: number | null;
  sleep_quality?: number | null;
  fatigue?: number | null;
  soreness?: number | null;
  stress?: number | null;
  mood?: number | null;
  hrv_ms?: number | null;
  resting_hr?: number | null;
  weight_kg?: number | null;
  notes?: string | null;
}

export interface CoachOverviewRow {
  uid: string;
  name: string;
  ctl: number;
  atl: number;
  tsb: number;
  ramp_rate: number;
  tss_7d: number;
  latest_wellness?: WellnessEntry | null;
  missed_workouts_7d: number;
  flags: string[];
}

export interface MeanMaxAggregate {
  uid: string;
  power: Record<string, number>;
  hr: Record<string, number>;
}
