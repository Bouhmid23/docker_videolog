"""Schémas Pydantic v2 et règles de validation métier (dont interdiction < 15 ans)."""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

SPORTS = (
    "cyclisme",
    "home_trainer",
    "course à pied",
    "tapis_course",
    "triathlon",
    "natation",
    "aviron",
    "aviron_indoor",
    "skierg",
    "musculation",
    "cross_training",
    "yoga_pilates",
    "boxe_combat",
    "escalade",
    "football",
    "rugby",
    "autre",
)

SportLiteral = Literal[
    "cyclisme",
    "home_trainer",
    "course à pied",
    "tapis_course",
    "triathlon",
    "natation",
    "aviron",
    "aviron_indoor",
    "skierg",
    "musculation",
    "cross_training",
    "yoga_pilates",
    "boxe_combat",
    "escalade",
    "football",
    "rugby",
    "autre",
]

LEVELS = (
    "loisir",
    "compétition régionale",
    "nationale",
    "élite",
)

HEALTH_CONSENT_VERSION = "2026-11-v1"
MIN_AGE_YEARS = 15


def compute_age(birth_date: date, today: date | None = None) -> int:
    ref = today or date.today()
    years = ref.year - birth_date.year
    if (ref.month, ref.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years


class OnboardingIn(BaseModel):
    account_type: Literal["club", "solo_coach"]
    organization_name: str = Field(min_length=2, max_length=120)
    sports: list[str] = Field(default_factory=list, max_length=10)
    city: str | None = Field(default=None, max_length=100)
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    phone: str | None = Field(default=None, max_length=32)
    age_confirmed: bool = True

    @field_validator("organization_name", "first_name", "last_name")
    @classmethod
    def strip_required(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Ce champ ne peut pas être vide.")
        return s

    @field_validator("sports")
    @classmethod
    def validate_sports(cls, v: list[str]) -> list[str]:
        cleaned = [s.strip().lower() for s in v if s.strip()]
        invalid = [s for s in cleaned if s not in SPORTS]
        if invalid:
            raise ValueError(f"Sport(s) non reconnu(s) : {', '.join(invalid)}")
        return list(dict.fromkeys(cleaned))

    @model_validator(mode="after")
    def check_age_confirmed(self):
        if not self.age_confirmed:
            raise ValueError("Vous devez confirmer avoir au moins 15 ans.")
        return self


class MeUpdateIn(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    last_name: str | None = Field(default=None, min_length=1, max_length=80)
    phone: str | None = Field(default=None, max_length=32)
    locale: Literal["fr", "en"] | None = None

    @field_validator("first_name", "last_name")
    @classmethod
    def strip_opt(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = v.strip()
        if not s:
            raise ValueError("Ce champ ne peut pas être vide.")
        return s


class AthleteProfileIn(BaseModel):
    sport: SportLiteral
    level: Literal["loisir", "compétition régionale", "nationale", "élite"]
    birth_date: date
    height_cm: int | None = Field(default=None, ge=100, le=230)
    weight_kg: float | None = Field(default=None, ge=25, le=250)
    hr_max: int | None = Field(default=None, ge=40, le=250)
    hr_rest: int | None = Field(default=None, ge=25, le=120)
    health_consent: bool = False

    @model_validator(mode="after")
    def validate_age_and_consent(self):
        age = compute_age(self.birth_date)
        if age < MIN_AGE_YEARS:
            raise ValueError(
                "L'accès à la plateforme est interdit aux personnes de moins de 15 ans."
            )
        if age > 110:
            raise ValueError("Date de naissance invalide.")

        has_health_fields = any(
            v is not None for v in (self.weight_kg, self.hr_max, self.hr_rest)
        )
        if has_health_fields and not self.health_consent:
            raise ValueError(
                "Le consentement explicite est requis pour enregistrer le poids ou la fréquence cardiaque."
            )
        if (
            self.hr_max is not None
            and self.hr_rest is not None
            and self.hr_rest >= self.hr_max
        ):
            raise ValueError(
                "La fréquence cardiaque de repos doit être inférieure à la FC maximale."
            )
        return self


class CoachSportEntry(BaseModel):
    sport: SportLiteral
    level: Literal[
        "loisir",
        "compétition régionale",
        "nationale",
        "élite",
    ] = "loisir"
    years_experience: int | None = Field(default=None, ge=0, le=70)


class CoachProfileIn(BaseModel):
    sports: list[CoachSportEntry] = Field(default_factory=list, max_length=10)
    certifications: str | None = Field(default=None, max_length=300)
    bio: str | None = Field(default=None, max_length=1000)

    @field_validator("certifications", "bio")
    @classmethod
    def strip_optional_text(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = v.strip()
        return s or None

    @model_validator(mode="after")
    def deduplicate_sports(self):
        seen: set[str] = set()
        unique: list[CoachSportEntry] = []
        for entry in self.sports:
            if entry.sport not in seen:
                seen.add(entry.sport)
                unique.append(entry)
        self.sports = unique
        return self


class OrgSettingsIn(BaseModel):
    max_recording_s: int | None = Field(default=None, ge=60, le=28800)


class OrgUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    sports: list[str] | None = Field(default=None, max_length=10)
    city: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    is_public: bool | None = None
    accepting_join_requests: bool | None = None
    settings: OrgSettingsIn | None = None

    @field_validator("sports")
    @classmethod
    def validate_sports(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        cleaned = [s.strip().lower() for s in v if s.strip()]
        invalid = [s for s in cleaned if s not in SPORTS]
        if invalid:
            raise ValueError(f"Sport(s) non reconnu(s) : {', '.join(invalid)}")
        return list(dict.fromkeys(cleaned))

    @field_validator("description")
    @classmethod
    def strip_description(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = v.strip()
        return s or None


class JoinRequestCreateIn(BaseModel):
    requested_sports: list[SportLiteral] = Field(default_factory=list, max_length=10)
    requested_group_id: str | None = Field(default=None, max_length=128)
    target_coach_uid: str | None = Field(default=None, max_length=128)
    message: str | None = Field(default=None, max_length=1000)
    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    last_name: str | None = Field(default=None, min_length=1, max_length=80)
    age_confirmed: bool = True

    @field_validator("message", "first_name", "last_name", "requested_group_id", "target_coach_uid")
    @classmethod
    def strip_optional(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = v.strip()
        return s or None

    @model_validator(mode="after")
    def validate_request(self):
        if not self.age_confirmed:
            raise ValueError("Vous devez confirmer avoir au moins 15 ans.")
        self.requested_sports = list(dict.fromkeys(self.requested_sports))
        return self


class JoinRequestApproveIn(BaseModel):
    role: Literal["athlete", "coach"] = "athlete"
    group_ids: list[str] | None = Field(default=None, max_length=20)
    coach_uids: list[str] | None = Field(default=None, max_length=20)
    decision_note: str | None = Field(default=None, max_length=500)

    @field_validator("decision_note")
    @classmethod
    def strip_note(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = v.strip()
        return s or None


class JoinRequestRejectIn(BaseModel):
    decision_note: str | None = Field(default=None, max_length=500)

    @field_validator("decision_note")
    @classmethod
    def strip_note(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = v.strip()
        return s or None


class InvitationIn(BaseModel):
    email: EmailStr
    role: Literal["coach", "athlete"] = "athlete"
    group_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: EmailStr) -> str:
        return str(v).strip().lower()


class AcceptInvitationIn(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    last_name: str | None = Field(default=None, min_length=1, max_length=80)
    age_confirmed: bool = True

    @model_validator(mode="after")
    def check_age(self):
        if not self.age_confirmed:
            raise ValueError("Vous devez confirmer avoir au moins 15 ans.")
        return self


class MemberUpdateIn(BaseModel):
    role: Literal["admin", "coach", "athlete"] | None = None
    group_ids: list[str] | None = Field(default=None, max_length=50)


class GroupIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    sport: SportLiteral = "autre"
    coach_uids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Le nom du groupe est requis.")
        return s


class SessionIn(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    group_id: str | None = None
    participant_uids: list[str] = Field(default_factory=list, max_length=50)
    scheduled_at: datetime | None = None
    duration_s: int | None = Field(default=None, ge=300, le=28800)
    planned_workout_id: str | None = None

    @field_validator("title")
    @classmethod
    def strip_title(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Le titre de la session est requis.")
        return s


class MarkerIn(BaseModel):
    pts_ms: float = Field(ge=0, description="Timestamp de synchronisation video en millisecondes")
    label: str = Field(min_length=1, max_length=120, description="Libelle du marqueur")
    note: str | None = Field(default=None, max_length=1000)
    category: str = Field(default="general", max_length=50)

    @field_validator("label")
    @classmethod
    def strip_label(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Le libellé du marqueur ne peut pas être vide.")
        return s


ZONES_MODELS = ("coggan_7", "friel_5", "custom")


class ThresholdProfileIn(BaseModel):
    sport: SportLiteral = "cyclisme"
    effective_from: date
    ftp_w: int | None = Field(default=None, ge=50, le=700)
    lthr_bpm: int | None = Field(default=None, ge=80, le=230)
    threshold_pace_s_per_km: int | None = Field(default=None, ge=120, le=900)
    pace_500m_s: int | None = Field(default=None, ge=60, le=300)
    css_s_per_100m: int | None = Field(default=None, ge=40, le=300)
    hr_max: int | None = Field(default=None, ge=100, le=250)
    hr_rest: int | None = Field(default=None, ge=25, le=120)
    zones_model: Literal["coggan_7", "friel_5", "custom"] = "coggan_7"
    recalculate_activities: bool = False

    @model_validator(mode="after")
    def validate_hr_consistency(self):
        if (
            self.hr_max is not None
            and self.hr_rest is not None
            and self.hr_rest >= self.hr_max
        ):
            raise ValueError(
                "La fréquence cardiaque de repos doit être inférieure à la FC maximale."
            )
        if (
            self.lthr_bpm is not None
            and self.hr_max is not None
            and self.lthr_bpm > self.hr_max
        ):
            raise ValueError(
                "La FC seuil (LTHR) ne peut pas dépasser la FC maximale."
            )
        return self


class ActivityPatchIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    sport: SportLiteral | None = None
    rpe: int | None = Field(default=None, ge=1, le=10)
    feeling: int | None = Field(default=None, ge=1, le=5)
    notes: str | None = Field(default=None, max_length=4000)

    @field_validator("title")
    @classmethod
    def strip_title(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = v.strip()
        if not s:
            raise ValueError("Le titre ne peut pas être vide.")
        return s


class ActivityCommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=2000)

    @field_validator("body")
    @classmethod
    def strip_body(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Le commentaire ne peut pas être vide.")
        return s


class ActivityShareIn(BaseModel):
    shared_org_ids: list[str] = Field(default_factory=list, max_length=20)


class WorkoutStep(BaseModel):
    kind: Literal["warmup", "active", "recovery", "cooldown", "rest", "repeat"] = "active"
    duration_s: int | None = Field(default=None, ge=1, le=43200)
    distance_m: int | None = Field(default=None, ge=1, le=300000)
    reps: int | None = Field(default=None, ge=1, le=1000)
    target_type: Literal["ftp_pct", "lthr_pct", "pace_pct", "rpe", "open"] = "open"
    target_min: float | None = Field(default=None, ge=0, le=500)
    target_max: float | None = Field(default=None, ge=0, le=500)
    repeat_count: int = Field(default=1, ge=1, le=100)
    steps: list["WorkoutStep"] = Field(default_factory=list, max_length=50)
    note: str | None = Field(default=None, max_length=300)


WorkoutStep.model_rebuild()


class WorkoutTemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    sport: SportLiteral = "cyclisme"
    description: str | None = Field(default=None, max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=15)
    steps: list[WorkoutStep] = Field(default_factory=list, max_length=100)

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Le nom du modèle est requis.")
        return s


class PlannedWorkoutIn(BaseModel):
    uid: str = Field(min_length=1, max_length=128)
    workout_date: date = Field(alias="date")
    start_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    template_id: str | None = None
    title: str | None = Field(default=None, min_length=1, max_length=160)
    sport: SportLiteral | None = None
    description: str | None = Field(default=None, max_length=2000)
    steps: list[WorkoutStep] | None = Field(default=None, max_length=100)
    coach_note: str | None = Field(default=None, max_length=2000)

    model_config = {"populate_by_name": True}


class PlannedWorkoutPatchIn(BaseModel):
    workout_date: date | None = Field(default=None, alias="date")
    start_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    title: str | None = Field(default=None, min_length=1, max_length=160)
    sport: SportLiteral | None = None
    description: str | None = Field(default=None, max_length=2000)
    steps: list[WorkoutStep] | None = Field(default=None, max_length=100)
    coach_note: str | None = Field(default=None, max_length=2000)
    status: Literal["planned", "done", "partial", "missed"] | None = None

    model_config = {"populate_by_name": True}


class PlanItemIn(BaseModel):
    day_offset: int = Field(ge=0, le=365)
    template_id: str | None = None
    title: str = Field(min_length=1, max_length=160)
    sport: SportLiteral = "cyclisme"
    description: str | None = Field(default=None, max_length=2000)
    steps: list[WorkoutStep] = Field(default_factory=list, max_length=100)


class TrainingPlanIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    sport: SportLiteral = "cyclisme"
    weeks: int = Field(ge=1, le=52)
    description: str | None = Field(default=None, max_length=2000)
    items: list[PlanItemIn] = Field(default_factory=list, max_length=400)


class ApplyPlanIn(BaseModel):
    uid: str | None = None
    group_id: str | None = None
    start_date: date | None = None
    target_date: date | None = None

    @model_validator(mode="after")
    def validate_target(self):
        if not self.uid and not self.group_id:
            raise ValueError("Spécifiez un athlète (uid) ou un groupe (group_id).")
        if not self.start_date and not self.target_date:
            raise ValueError("Spécifiez une date de début (start_date) ou d'objectif (target_date).")
        return self


class GoalIn(BaseModel):
    uid: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=160)
    goal_date: date = Field(alias="date")
    sport: SportLiteral = "cyclisme"
    priority: Literal["A", "B", "C"] = "A"
    distance_m: int | None = Field(default=None, ge=100, le=500000)
    notes: str | None = Field(default=None, max_length=2000)

    model_config = {"populate_by_name": True}


class WellnessEntryIn(BaseModel):
    entry_date: date = Field(alias="date")
    sleep_h: float | None = Field(default=None, ge=0, le=24)
    sleep_quality: int | None = Field(default=None, ge=1, le=5)
    fatigue: int | None = Field(default=None, ge=1, le=5)
    soreness: int | None = Field(default=None, ge=1, le=5)
    stress: int | None = Field(default=None, ge=1, le=5)
    mood: int | None = Field(default=None, ge=1, le=5)
    hrv_ms: float | None = Field(default=None, ge=5, le=300)
    resting_hr: int | None = Field(default=None, ge=25, le=120)
    weight_kg: float | None = Field(default=None, ge=25, le=250)
    notes: str | None = Field(default=None, max_length=1000)

    model_config = {"populate_by_name": True}




