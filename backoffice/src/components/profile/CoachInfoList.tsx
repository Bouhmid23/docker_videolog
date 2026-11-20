import { Badge, Group, Paper, Stack, Text } from "@mantine/core";
import { Award } from "lucide-react";
import { LEVELS, SPORTS } from "../../lib/constants";
import type { OrgCoachInfo } from "../../types";

function sportLabel(value: string): string {
  return SPORTS.find((s) => s.value === value)?.label ?? value;
}

function levelLabel(value: string): string {
  return LEVELS.find((l) => l.value === value)?.label ?? value;
}

export function CoachInfoList({ coaches }: { coaches: OrgCoachInfo[] }) {
  if (!coaches || coaches.length === 0) return null;

  return (
    <Stack gap="xs">
      {coaches.map((c) => {
        const cp = c.coach_profile;
        return (
          <Paper
            key={c.uid}
            withBorder
            p="xs"
            radius="sm"
            style={{ backgroundColor: "#141a22" }}
          >
            <Stack gap={4}>
              <Group justify="space-between" wrap="wrap" gap="xs">
                <Group gap="xs">
                  <Award size={14} color="#18abf8" />
                  <Text size="sm" fw={600}>
                    {c.name}
                  </Text>
                </Group>
                {cp?.certifications && (
                  <Badge size="xs" color="teal" variant="light">
                    {cp.certifications}
                  </Badge>
                )}
              </Group>

              {cp?.sports && cp.sports.length > 0 && (
                <Group gap={6} wrap="wrap">
                  {cp.sports.map((s) => (
                    <Badge key={s.sport} size="xs" variant="outline" color="cyan">
                      {sportLabel(s.sport)} · {levelLabel(s.level)}
                      {s.years_experience != null
                        ? ` (${s.years_experience} an${s.years_experience > 1 ? "s" : ""})`
                        : ""}
                    </Badge>
                  ))}
                </Group>
              )}

              {cp?.bio && (
                <Text size="xs" c="dimmed">
                  {cp.bio}
                </Text>
              )}

              {!cp && (
                <Text size="xs" c="dimmed">
                  Profil entraîneur non encore complété.
                </Text>
              )}
            </Stack>
          </Paper>
        );
      })}
    </Stack>
  );
}
