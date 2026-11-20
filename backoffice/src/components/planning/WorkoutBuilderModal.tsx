import { useEffect, useMemo, useState } from "react";
import {
  ActionIcon,
  Badge,
  Button,
  Group,
  Modal,
  NumberInput,
  Paper,
  Select,
  Stack,
  Text,
  TextInput,
  Textarea,
} from "@mantine/core";
import { Plus, Trash2 } from "lucide-react";
import { SPORTS, getSportMeta } from "../../lib/constants";
import type {
  WorkoutBlockPreview,
  WorkoutStep,
  WorkoutStepKind,
} from "../../types";
import { WorkoutIntensityBar } from "./WorkoutIntensityBar";

const INTENSITY_PRESETS = [
  { value: "facile", label: "Facile", min: 55, max: 65 },
  { value: "moderee", label: "Modérée", min: 70, max: 80 },
  { value: "soutenue", label: "Soutenue", min: 85, max: 95 },
  { value: "intense", label: "Intense", min: 98, max: 110 },
] as const;

function intensityValueFromStep(step: WorkoutStep): string {
  const avg = ((step.target_min ?? 70) + (step.target_max ?? step.target_min ?? 70)) / 2;
  if (avg < 68) return "facile";
  if (avg < 83) return "moderee";
  if (avg < 97) return "soutenue";
  return "intense";
}

export interface WorkoutBuilderModalProps {
  opened: boolean;
  onClose: () => void;
  titleLabel: string;
  initialName?: string;
  initialSport?: string;
  initialDescription?: string;
  initialSteps?: WorkoutStep[];
  saving?: boolean;
  onSave: (payload: {
    name: string;
    sport: string;
    description: string;
    steps: WorkoutStep[];
  }) => void;
}

function estimateLocal(steps: WorkoutStep[]): {
  durS: number;
  tss: number;
  blocks: WorkoutBlockPreview[];
} {
  let durS = 0;
  let tss = 0;
  const blocks: WorkoutBlockPreview[] = [];

  const pushFlat = (list: WorkoutStep[]) => {
    for (const s of list) {
      if (s.kind === "repeat") {
        const count = Math.max(1, s.repeat_count || 1);
        for (let i = 0; i < count; i++) pushFlat(s.steps || []);
        continue;
      }
      const d = Math.max(
        5,
        Number(s.duration_s || (s.reps && s.reps > 0 ? s.reps * 4 : 300))
      );
      const lo = s.target_min ?? 70;
      const hi = s.target_max ?? lo;
      const ifVal = (lo + hi) / 2 / 100;
      durS += d;
      tss += (d / 3600) * (ifVal * ifVal) * 100;
      blocks.push({
        index: blocks.length,
        kind: s.kind,
        duration_s: d,
        reps: s.reps ?? null,
        intensity_pct: Math.round(ifVal * 100),
      });
    }
  };
  pushFlat(steps);
  return { durS, tss: Math.round(tss * 10) / 10, blocks };
}

export function WorkoutBuilderModal({
  opened,
  onClose,
  titleLabel,
  initialName = "",
  initialSport = "cyclisme",
  initialDescription = "",
  initialSteps,
  saving = false,
  onSave,
}: WorkoutBuilderModalProps) {
  const [name, setName] = useState(initialName);
  const [sport, setSport] = useState(initialSport);
  const [description, setDescription] = useState(initialDescription);
  const [steps, setSteps] = useState<WorkoutStep[]>(
    initialSteps && initialSteps.length > 0
      ? initialSteps
      : [
          { kind: "warmup", duration_s: 600, target_type: "ftp_pct", target_min: 55, target_max: 65 },
          { kind: "active", duration_s: 1200, target_type: "ftp_pct", target_min: 95, target_max: 100 },
          { kind: "cooldown", duration_s: 300, target_type: "ftp_pct", target_min: 50, target_max: 55 },
        ]
  );

  useEffect(() => {
    if (!opened) return;
    setName(initialName);
    setSport(initialSport);
    setDescription(initialDescription);
    setSteps(
      initialSteps && initialSteps.length > 0
        ? initialSteps
        : [
            { kind: "warmup", duration_s: 600, target_type: "ftp_pct", target_min: 55, target_max: 65 },
            { kind: "active", duration_s: 1200, target_type: "ftp_pct", target_min: 95, target_max: 100 },
            { kind: "cooldown", duration_s: 300, target_type: "ftp_pct", target_min: 50, target_max: 55 },
          ]
    );
  }, [opened, initialName, initialSport, initialDescription, initialSteps]);

  const preview = useMemo(() => estimateLocal(steps), [steps]);
  const sportMeta = getSportMeta(sport);
  const useSecUnit =
    sportMeta.category === "strength" ||
    sport === "boxe_combat" ||
    sport === "escalade";

  const updateStep = (idx: number, patch: Partial<WorkoutStep>) => {
    setSteps((prev) => prev.map((s, i) => (i === idx ? { ...s, ...patch } : s)));
  };

  const addStep = () => {
    setSteps((prev) => [
      ...prev,
      {
        kind: "active",
        duration_s: useSecUnit ? 45 : 300,
        reps: sportMeta.supportsReps ? 12 : null,
        target_type: sportMeta.supportsPower ? "ftp_pct" : "open",
        target_min: 85,
        target_max: 95,
      },
    ]);
  };

  const removeStep = (idx: number) => {
    setSteps((prev) => prev.filter((_, i) => i !== idx));
  };

  return (
    <Modal opened={opened} onClose={onClose} title={titleLabel} size="lg">
      <Stack gap="sm">
        <Group grow>
          <TextInput
            label="Nom de la séance"
            placeholder="Ex : Seuil 3x10 min / Circuit HIIT"
            value={name}
            onChange={(e) => setName(e.currentTarget.value)}
            required
          />
          <Select
            label="Sport"
            searchable
            value={sport}
            onChange={(v) => v && setSport(v)}
            data={SPORTS}
          />
        </Group>

        <Textarea
          label="Consignes"
          value={description}
          onChange={(e) => setDescription(e.currentTarget.value)}
          rows={2}
        />

        <Paper withBorder p="xs" radius="md">
          <Group justify="space-between" mb={6}>
            <Text size="xs" fw={600}>
              Aperçu d&apos;intensité
            </Text>
            <Group gap="xs">
              <Badge size="sm" color="cyan">
                {Math.round(preview.durS / 60)} min
              </Badge>
              <Badge size="sm" color="grape">
                Charge estimée : ~{preview.tss} pts
              </Badge>
            </Group>
          </Group>
          <WorkoutIntensityBar blocks={preview.blocks} height={44} />
        </Paper>

        <Stack gap="xs">
          <Group justify="space-between">
            <Text size="sm" fw={600}>
              Étapes ({steps.length})
            </Text>
            <Button size="xs" variant="light" leftSection={<Plus size={14} />} onClick={addStep}>
              Ajouter une étape
            </Button>
          </Group>

          {steps.map((s, idx) => (
            <Paper key={idx} withBorder p="xs" radius="sm">
              <Group gap="xs" align="flex-end" wrap="wrap">
                <Select
                  label="Type"
                  size="xs"
                  style={{ width: 120 }}
                  value={s.kind}
                  onChange={(v) => v && updateStep(idx, { kind: v as WorkoutStepKind })}
                  data={[
                    { value: "warmup", label: "Échauffement" },
                    { value: "active", label: "Effort" },
                    { value: "recovery", label: "Récupération" },
                    { value: "cooldown", label: "Retour calme" },
                    { value: "rest", label: "Repos" },
                  ]}
                />
                {useSecUnit ? (
                  <NumberInput
                    label="Durée (sec)"
                    size="xs"
                    style={{ width: 92 }}
                    min={5}
                    max={7200}
                    step={5}
                    value={Math.max(5, Math.round(s.duration_s || 45))}
                    onChange={(v) =>
                      updateStep(idx, { duration_s: Math.max(5, Number(v || 45)) })
                    }
                  />
                ) : (
                  <NumberInput
                    label="Durée (min)"
                    size="xs"
                    style={{ width: 90 }}
                    min={1}
                    max={600}
                    value={Math.max(1, Math.round((s.duration_s || 300) / 60))}
                    onChange={(v) =>
                      updateStep(idx, { duration_s: Number(v || 1) * 60 })
                    }
                  />
                )}
                {sportMeta.supportsReps && (
                  <NumberInput
                    label="Répétitions"
                    size="xs"
                    style={{ width: 90 }}
                    min={1}
                    max={500}
                    placeholder="—"
                    value={s.reps ?? ""}
                    onChange={(v) =>
                      updateStep(idx, {
                        reps: v === "" || v == null ? null : Math.max(1, Number(v)),
                      })
                    }
                  />
                )}
                <Select
                  label="Intensité"
                  size="xs"
                  style={{ width: 120 }}
                  value={intensityValueFromStep(s)}
                  onChange={(v) => {
                    const preset =
                      INTENSITY_PRESETS.find((p) => p.value === v) ??
                      INTENSITY_PRESETS[1];
                    updateStep(idx, {
                      target_type: "open",
                      target_min: preset.min,
                      target_max: preset.max,
                    });
                  }}
                  data={INTENSITY_PRESETS.map((p) => ({
                    value: p.value,
                    label: p.label,
                  }))}
                />
                <TextInput
                  label="Consigne (optionnelle)"
                  size="xs"
                  style={{ flex: 1, minWidth: 140 }}
                  placeholder="Ex : cadence souple"
                  value={s.note ?? ""}
                  onChange={(e) =>
                    updateStep(idx, { note: e.currentTarget.value || null })
                  }
                />
                <ActionIcon color="red" variant="subtle" onClick={() => removeStep(idx)}>
                  <Trash2 size={14} />
                </ActionIcon>
              </Group>
            </Paper>
          ))}
        </Stack>

        <Group justify="flex-end" mt="md">
          <Button variant="default" onClick={onClose}>
            Annuler
          </Button>
          <Button
            loading={saving}
            disabled={!name.trim() || steps.length === 0}
            onClick={() => onSave({ name: name.trim(), sport, description, steps })}
          >
            Enregistrer
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
