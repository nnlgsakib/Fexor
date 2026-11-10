export type Role = "owner" | "manager" | "viewer";
export type Mode = "single" | "in_out";
export type DayStatus = "present" | "late" | "half_day" | "absent" | "holiday" | "leave" | "off";

export interface Me {
  id: string;
  email: string;
  name: string;
  is_superadmin: boolean;
  totp_enabled: boolean;
  csrf: string;
  spaces: { id: string; name: string; mode: Mode; role: Role }[];
  public_url: string | null;
  http_mode: boolean;
}

export interface FieldDef {
  key: string;
  label: string;
  type: "text" | "number" | "date" | "select";
  required: boolean;
  options: string[];
}

export interface Space {
  id: string;
  name: string;
  description: string;
  timezone: string;
  mode: Mode;
  country: string | null;
  member_label: string;
  field_schema: FieldDef[];
  default_shift_id: string | null;
  min_rescan_minutes: number;
  match_threshold: number;
  liveness_threshold: number;
  challenge_actions: number;
  store_failure_snapshots: boolean;
  keep_enrollment_photos: boolean;
  time_format: "24h" | "12h";
  role: Role;
}

export interface Member {
  id: string;
  person_id: string;
  full_name: string;
  email: string | null;
  phone: string | null;
  external_id: string;
  title: string;
  fields: Record<string, string | number>;
  active: boolean;
  start_date: string | null;
  end_date: string | null;
  consent_at: string | null;
  face_count: number;
  has_qr: boolean;
}

export interface Shift {
  id: string;
  name: string;
  start_time: string;
  end_time: string;
  weekdays: number[];
  grace_minutes: number;
  early_window_minutes: number;
  late_window_minutes: number;
  half_day_minutes: number | null;
  checkin_close_minutes: number | null;  // null: check in any time
  checkout_open_minutes: number | null;  // null: check out any time
  active: boolean;
  overnight: boolean;
}

export interface AttendanceEvent {
  id: string;
  member_id: string | null;
  member_name: string | null;
  external_id: string | null;
  device: string | null;
  occurred_at: string;
  received_at: string;
  status: "accepted" | "rejected" | "pending";
  source: "kiosk" | "admin";
  reason: string;
  scores: Record<string, number | string | boolean | null>;
  note: string;
  has_snapshot: boolean;
  reviewed_by: string | null;
  reviewed_at?: string | null;
}

export interface DayRow {
  member_id: string;
  member_name: string;
  external_id: string;
  title: string;
  day: string;
  status: DayStatus;
  first_in: string | null;
  last_out: string | null;
  worked_minutes: number;
  late_minutes: number;
  early_leave_minutes: number;
  overtime_minutes: number;
  flags: string[];
}

export interface Device {
  id: string;
  name: string;
  enrolled_at: string | null;
  last_seen_at: string | null;
  last_ip: string | null;
  info: Record<string, string | number | boolean>;
  revoked_at: string | null;
  online: boolean;
  enroll_pending: boolean;
  has_pin: boolean;
}
