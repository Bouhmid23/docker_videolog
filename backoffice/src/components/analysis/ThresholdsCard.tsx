import { useState } from "react";
import {
  Badge,
  Button,
  Card,
  Group,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiFetch } from "../../lib/api";
import { SPORTS, getSportMeta } from "../../lib/constants";
import type { ThresholdProfile } from "../../types";

export interface ThresholdsCardProps {
  orgId: string;
  uid: string;
  canEdit?: boolean;
}

export function ThresholdsCard({ orgId, uid, canEdit = true }: ThresholdsCardProps) {
  const queryClient = useQueryClient();
  const [sport, setSport] = useState("cyclisme");
  const [effectiveFrom, setEffectiveFrom] = useState(() =>
    new Date().toISOString().slice(0, 10)
  );
  const [ftpW, setFtpW] = useState<number | string>("");
  const [lthrBpm, setLthrBpm] = useState<number | string>("");
  const [paceSec, setPaceSec] = useState<number | string>("");
  const [pace500mSec, setPace500mSec] = useState<number | string>("");
  const [cssSec, setCssSec] = useState<number | string>("");
  const [hrMax, setHrMax] = useState<number | string>("");
  const [hrRest, setHrRest] = useState<number | string>("");

  const { data } = useQuery<{ items: ThresholdProfile[] }>({
    queryKey: ["thresholds", orgId, uid],
    queryFn: () =>
      apiFetch<{ items: ThresholdProfile[] }>(
        `/v1/orgs/${orgId}/athletes/${uid}/thresholds`
      ),
    enabled: Boolean(orgId && uid),
  });

  const items = data?.items ?? [];
  const activeForSport = items.find((item) => item.sport === sport) ?? items[0];
  const sportMeta = getSportMeta(sport);

  const saveMutation = useMutation({
    mutationFn: () =>
      apiFetch(`/v1/orgs/${orgId}/athletes/${uid}/thresholds`, {
        method: "PUT",
        body: JSON.stringify({
          sport,
          effective_from: effectiveFrom,
          ftp_w: ftpW === "" ? null : Number(ftpW),
          lthr_bpm: lthrBpm === "" ? null : Number(lthrBpm),
          threshold_pace_s_per_km: paceSec === "" ? null : Number(paceSec),
          pace_500m_s: pace500mSec === "" ? null : Number(pace500mSec),
          css_s_per_100m: cssSec === "" ? null : Number(cssSec),
          hr_max: hrMax === "" ? null : Number(hrMax),
          hr_rest: hrRest === "" ? null : Number(hrRest),
          zones_model: "coggan_7",
          recalculate_activities: true,
        }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["thresholds", orgId, uid] });
      notifications.show({
        color: "teal",
        message: "Seuils enregistrés et zones recalculées.",
      });
    },
    onError: (err) => {
      notifications.show({
        color: "red",
        message:
          err instanceof ApiError
            ? err.detail
            : "Erreur lors de l'enregistrement des seuils.",
      });
    },
  });

  return (
    <Card withBorder radius="md" p="md">
      <Stack gap="md">
        <Group justify="space-between" align="center">
          <Title order={4}>Seuils physiologiques & zones d&apos;entraînement</Title>
          {activeForSport && (
            <Badge variant="light" color="cyan">
              Actif depuis {activeForSport.effective_from}
            </Badge>
          )}
        </Group>

        {canEdit && (
          <Stack gap="sm">
            <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="sm">
              <Select
                label="Sport"
                searchable
                value={sport}
                onChange={(v) => v && setSport(v)}
                data={SPORTS}
              />
              <TextInput
                label="Date d'effet"
                type="date"
                value={effectiveFrom}
                onChange={(e) => setEffectiveFrom(e.currentTarget.value)}
              />
              {sportMeta.supportsPower && (
                <NumberInput
                  label="FTP (W)"
                  placeholder={activeForSport?.ftp_w ? String(activeForSport.ftp_w) : "ex: 260"}
                  min={50}
                  max={700}
                  value={ftpW}
                  onChange={setFtpW}
                />
              )}
              <NumberInput
                label="FC seuil / LTHR (bpm)"
                placeholder={activeForSport?.lthr_bpm ? String(activeForSport.lthr_bpm) : "ex: 170"}
                min={80}
                max={230}
                value={lthrBpm}
                onChange={setLthrBpm}
              />
              {sportMeta.supportsPaceKm && (
                <NumberInput
                  label="Allure seuil (s/km)"
                  placeholder={
                    activeForSport?.threshold_pace_s_per_km
                      ? String(activeForSport.threshold_pace_s_per_km)
                      : "ex: 240 (4:00/km)"
                  }
                  min={120}
                  max={900}
                  value={paceSec}
                  onChange={setPaceSec}
                />
              )}
              {sportMeta.supportsPace500m && (
                <NumberInput
                  label="Allure seuil /500m (s)"
                  placeholder={
                    activeForSport?.pace_500m_s
                      ? String(activeForSport.pace_500m_s)
                      : "ex: 110 (1:50/500m)"
                  }
                  min={60}
                  max={300}
                  value={pace500mSec}
                  onChange={setPace500mSec}
                />
              )}
              {sportMeta.supportsCss && (
                <NumberInput
                  label="CSS natation (s/100m)"
                  placeholder={
                    activeForSport?.css_s_per_100m
                      ? String(activeForSport.css_s_per_100m)
                      : "ex: 95"
                  }
                  min={40}
                  max={300}
                  value={cssSec}
                  onChange={setCssSec}
                />
              )}
              <NumberInput
                label="FC max (bpm)"
                min={100}
                max={250}
                value={hrMax}
                onChange={setHrMax}
              />
              <NumberInput
                label="FC repos (bpm)"
                min={25}
                max={120}
                value={hrRest}
                onChange={setHrRest}
              />
            </SimpleGrid>

            <Group justify="flex-end">
              <Button
                onClick={() => saveMutation.mutate()}
                loading={saveMutation.isPending}
              >
                Enregistrer ce palier
              </Button>
            </Group>
          </Stack>
        )}

        {activeForSport && activeForSport.power_zones.length > 0 && (
          <div>
            <Text size="xs" fw={600} c="dimmed" mb={6}>
              ZONES DE PUISSANCE (COGGAN — FTP {activeForSport.ftp_w} W)
            </Text>
            <Table withTableBorder striped>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Zone</Table.Th>
                  <Table.Th>Intitulé</Table.Th>
                  <Table.Th>Plage (W)</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {activeForSport.power_zones.map((z) => (
                  <Table.Tr key={z.zone}>
                    <Table.Td fw={600}>{z.zone}</Table.Td>
                    <Table.Td>{z.name}</Table.Td>
                    <Table.Td>
                      {z.max_w != null ? `${z.min_w} – ${z.max_w} W` : `≥ ${z.min_w} W`}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </div>
        )}
      </Stack>
    </Card>
  );
}
