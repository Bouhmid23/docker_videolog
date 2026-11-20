export interface SportMeta {
  value: string;
  label: string;
  isIndoor: boolean;
  category: "ergo" | "strength" | "skill" | "outdoor";
  cadenceUnit: "rpm" | "spm" | "reps/min";
  cadenceMax: number;
  supportsPower: boolean;
  supportsGps: boolean;
  supportsReps: boolean;
  supportsPaceKm: boolean;
  supportsPace500m: boolean;
  supportsCss: boolean;
}

export const SPORT_CATALOG: readonly SportMeta[] = [
  {
    value: "cyclisme",
    label: "Cyclisme (route / VTT)",
    isIndoor: false,
    category: "outdoor",
    cadenceUnit: "rpm",
    cadenceMax: 120,
    supportsPower: true,
    supportsGps: true,
    supportsReps: false,
    supportsPaceKm: false,
    supportsPace500m: false,
    supportsCss: false,
  },
  {
    value: "home_trainer",
    label: "Home-trainer / Cyclisme indoor",
    isIndoor: true,
    category: "ergo",
    cadenceUnit: "rpm",
    cadenceMax: 120,
    supportsPower: true,
    supportsGps: false,
    supportsReps: false,
    supportsPaceKm: false,
    supportsPace500m: false,
    supportsCss: false,
  },
  {
    value: "course à pied",
    label: "Course à pied",
    isIndoor: false,
    category: "outdoor",
    cadenceUnit: "spm",
    cadenceMax: 200,
    supportsPower: true,
    supportsGps: true,
    supportsReps: false,
    supportsPaceKm: true,
    supportsPace500m: false,
    supportsCss: false,
  },
  {
    value: "tapis_course",
    label: "Tapis de course (indoor)",
    isIndoor: true,
    category: "ergo",
    cadenceUnit: "spm",
    cadenceMax: 200,
    supportsPower: true,
    supportsGps: false,
    supportsReps: false,
    supportsPaceKm: true,
    supportsPace500m: false,
    supportsCss: false,
  },
  {
    value: "aviron_indoor",
    label: "Rameur / Aviron indoor",
    isIndoor: true,
    category: "ergo",
    cadenceUnit: "spm",
    cadenceMax: 45,
    supportsPower: true,
    supportsGps: false,
    supportsReps: false,
    supportsPaceKm: false,
    supportsPace500m: true,
    supportsCss: false,
  },
  {
    value: "skierg",
    label: "SkiErg (indoor)",
    isIndoor: true,
    category: "ergo",
    cadenceUnit: "spm",
    cadenceMax: 60,
    supportsPower: true,
    supportsGps: false,
    supportsReps: false,
    supportsPaceKm: false,
    supportsPace500m: true,
    supportsCss: false,
  },
  {
    value: "musculation",
    label: "Musculation & Force (indoor)",
    isIndoor: true,
    category: "strength",
    cadenceUnit: "reps/min",
    cadenceMax: 60,
    supportsPower: false,
    supportsGps: false,
    supportsReps: true,
    supportsPaceKm: false,
    supportsPace500m: false,
    supportsCss: false,
  },
  {
    value: "cross_training",
    label: "Cross-Training / HIIT (indoor)",
    isIndoor: true,
    category: "strength",
    cadenceUnit: "rpm",
    cadenceMax: 120,
    supportsPower: true,
    supportsGps: false,
    supportsReps: true,
    supportsPaceKm: true,
    supportsPace500m: true,
    supportsCss: false,
  },
  {
    value: "yoga_pilates",
    label: "Yoga / Pilates / Mobilité (indoor)",
    isIndoor: true,
    category: "skill",
    cadenceUnit: "reps/min",
    cadenceMax: 30,
    supportsPower: false,
    supportsGps: false,
    supportsReps: true,
    supportsPaceKm: false,
    supportsPace500m: false,
    supportsCss: false,
  },
  {
    value: "boxe_combat",
    label: "Boxe & Arts martiaux (indoor)",
    isIndoor: true,
    category: "skill",
    cadenceUnit: "reps/min",
    cadenceMax: 120,
    supportsPower: false,
    supportsGps: false,
    supportsReps: true,
    supportsPaceKm: false,
    supportsPace500m: false,
    supportsCss: false,
  },
  {
    value: "escalade",
    label: "Escalade indoor / Bloc",
    isIndoor: true,
    category: "skill",
    cadenceUnit: "reps/min",
    cadenceMax: 30,
    supportsPower: false,
    supportsGps: false,
    supportsReps: true,
    supportsPaceKm: false,
    supportsPace500m: false,
    supportsCss: false,
  },
  {
    value: "natation",
    label: "Natation (bassin / eau libre)",
    isIndoor: true,
    category: "outdoor",
    cadenceUnit: "spm",
    cadenceMax: 90,
    supportsPower: false,
    supportsGps: true,
    supportsReps: false,
    supportsPaceKm: false,
    supportsPace500m: false,
    supportsCss: true,
  },
  {
    value: "triathlon",
    label: "Triathlon",
    isIndoor: false,
    category: "outdoor",
    cadenceUnit: "rpm",
    cadenceMax: 120,
    supportsPower: true,
    supportsGps: true,
    supportsReps: false,
    supportsPaceKm: true,
    supportsPace500m: false,
    supportsCss: true,
  },
  {
    value: "aviron",
    label: "Aviron (bateau)",
    isIndoor: false,
    category: "outdoor",
    cadenceUnit: "spm",
    cadenceMax: 45,
    supportsPower: true,
    supportsGps: true,
    supportsReps: false,
    supportsPaceKm: false,
    supportsPace500m: true,
    supportsCss: false,
  },
  {
    value: "football",
    label: "Football",
    isIndoor: false,
    category: "outdoor",
    cadenceUnit: "spm",
    cadenceMax: 180,
    supportsPower: false,
    supportsGps: true,
    supportsReps: false,
    supportsPaceKm: true,
    supportsPace500m: false,
    supportsCss: false,
  },
  {
    value: "rugby",
    label: "Rugby",
    isIndoor: false,
    category: "outdoor",
    cadenceUnit: "spm",
    cadenceMax: 180,
    supportsPower: false,
    supportsGps: true,
    supportsReps: false,
    supportsPaceKm: true,
    supportsPace500m: false,
    supportsCss: false,
  },
  {
    value: "autre",
    label: "Autre",
    isIndoor: false,
    category: "outdoor",
    cadenceUnit: "rpm",
    cadenceMax: 120,
    supportsPower: true,
    supportsGps: true,
    supportsReps: true,
    supportsPaceKm: true,
    supportsPace500m: false,
    supportsCss: false,
  },
] as const;

export const SPORTS = SPORT_CATALOG.map(({ value, label }) => ({
  value,
  label,
}));

const SPORT_META_MAP: Record<string, SportMeta> = Object.fromEntries(
  SPORT_CATALOG.map((m) => [m.value, m])
);

export function getSportMeta(sport: string | null | undefined): SportMeta {
  if (!sport) return SPORT_CATALOG[0];
  return SPORT_META_MAP[sport] ?? SPORT_CATALOG[SPORT_CATALOG.length - 1];
}

export function formatSportLabel(sport: string | null | undefined): string {
  if (!sport) return "—";
  return SPORT_META_MAP[sport]?.label ?? sport;
}

export const LEVELS = [
  { value: "loisir", label: "Loisir" },
  { value: "compétition régionale", label: "Compétition régionale" },
  { value: "nationale", label: "Nationale" },
  { value: "élite", label: "Élite" },
] as const;

export const ROLE_LABELS: Record<string, string> = {
  owner: "Propriétaire",
  admin: "Administrateur",
  coach: "Coach",
  athlete: "Athlète",
};

export function computeAge(birthDateIso: string): number | null {
  if (!birthDateIso) return null;
  const parts = birthDateIso.split("-").map(Number);
  if (parts.length !== 3 || parts.some(Number.isNaN)) return null;
  const [year, month, day] = parts;
  const today = new Date();
  let age = today.getFullYear() - year;
  const m = today.getMonth() + 1;
  if (m < month || (m === month && today.getDate() < day)) {
    age -= 1;
  }
  return age;
}
