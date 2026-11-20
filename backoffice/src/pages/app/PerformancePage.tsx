import { useState } from "react";
import {
  Accordion,
  Alert,
  Badge,
  Button,
  Card,
  Divider,
  Group,
  Modal,
  MultiSelect,
  Paper,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { useQuery } from "@tanstack/react-query";
import { Download, FileText, HeartPulse, HelpCircle, Printer, TrendingUp, Users } from "lucide-react";
import { useAuth } from "../../auth/AuthProvider";
import { PmcChart } from "../../components/analysis/PmcChart";
import { ApiError, apiDownloadBlob, apiFetch } from "../../lib/api";
import type {
  AthleteReport,
  AthletesKpisResponse,
  DailyMetricPoint,
  MeanMaxAggregate,
  OrgMember,
  WellnessEntry,
} from "../../types";

export function PerformancePage() {
  const { me, activeMembership } = useAuth();
  const orgId = activeMembership?.org_id ?? "";
  const role = activeMembership?.role ?? "athlete";
  const isStaff = role === "owner" || role === "admin" || role === "coach";

  const [selectedUid, setSelectedUid] = useState<string>("");
  const [compareUids, setCompareUids] = useState<string[]>([]);
  const [daysRange, setDaysRange] = useState("90");
  const [reportOpened, { open: openReport, close: closeReport }] = useDisclosure(false);
  const [pdfLoading, setPdfLoading] = useState(false);

  const handleDownloadPdf = async () => {
    if (!orgId || !effectiveUid) return;
    setPdfLoading(true);
    try {
      const q = new URLSearchParams({ days: daysRange, uid: effectiveUid });
      await apiDownloadBlob(
        `/v1/orgs/${orgId}/analytics/report/pdf?${q.toString()}`,
        `bilan_${effectiveUid}_${daysRange}j.pdf`
      );
    } catch (err) {
      notifications.show({
        color: "red",
        message: err instanceof ApiError ? err.detail : "Impossible de générer le PDF.",
      });
    } finally {
      setPdfLoading(false);
    }
  };

  const fromIso = new Date(Date.now() - Number(daysRange) * 86400 * 1000)
    .toISOString()
    .slice(0, 10);

  const { data: membersData } = useQuery<{ members: OrgMember[] }>({
    queryKey: ["members", orgId, "athlete"],
    queryFn: () =>
      apiFetch<{ members: OrgMember[] }>(`/v1/orgs/${orgId}/members?role=athlete`),
    enabled: Boolean(orgId && isStaff),
  });
  const athletes = membersData?.members ?? [];
  const effectiveUid = isStaff
    ? athletes.some((a) => a.uid === selectedUid)
      ? selectedUid
      : athletes[0]?.uid ?? ""
    : me?.uid ?? "";

  const { data: pmcData } = useQuery<{ items: DailyMetricPoint[] }>({
    queryKey: ["pmc", orgId, effectiveUid, fromIso],
    queryFn: () => {
      const q = new URLSearchParams({ from: fromIso, include_projected: "true" });
      if (effectiveUid) q.set("uid", effectiveUid);
      return apiFetch<{ items: DailyMetricPoint[] }>(
        `/v1/orgs/${orgId}/analytics/pmc?${q.toString()}`
      );
    },
    enabled: Boolean(orgId && effectiveUid),
  });

  const { data: mmData } = useQuery<MeanMaxAggregate>({
    queryKey: ["mean-max", orgId, effectiveUid, fromIso],
    queryFn: () => {
      const q = new URLSearchParams({ from: fromIso });
      if (effectiveUid) q.set("uid", effectiveUid);
      return apiFetch<MeanMaxAggregate>(
        `/v1/orgs/${orgId}/analytics/mean-max?${q.toString()}`
      );
    },
    enabled: Boolean(orgId && effectiveUid),
  });

  const { data: wellData } = useQuery<{ items: WellnessEntry[] }>({
    queryKey: ["wellness", orgId, effectiveUid],
    queryFn: () => {
      const q = effectiveUid ? `?uid=${encodeURIComponent(effectiveUid)}` : "";
      return apiFetch<{ items: WellnessEntry[] }>(`/v1/orgs/${orgId}/wellness${q}`);
    },
    enabled: Boolean(orgId && effectiveUid),
  });

  const { data: teamKpis } = useQuery<AthletesKpisResponse>({
    queryKey: ["athletes-kpis", orgId, compareUids.join(",")],
    queryFn: () => {
      const q = compareUids.length > 0 ? `?uids=${encodeURIComponent(compareUids.join(","))}` : "";
      return apiFetch<AthletesKpisResponse>(`/v1/orgs/${orgId}/analytics/athletes-kpis${q}`);
    },
    enabled: Boolean(orgId && isStaff && athletes.length > 0),
  });

  const { data: reportData } = useQuery<AthleteReport>({
    queryKey: ["athlete-report", orgId, effectiveUid, daysRange],
    queryFn: () => {
      const q = new URLSearchParams({ days: daysRange });
      if (effectiveUid) q.set("uid", effectiveUid);
      return apiFetch<AthleteReport>(`/v1/orgs/${orgId}/analytics/report?${q.toString()}`);
    },
    enabled: Boolean(orgId && effectiveUid && reportOpened),
  });

  const pmcItems = pmcData?.items ?? [];
  const todayIso = new Date().toISOString().slice(0, 10);
  const currentMetric =
    pmcItems.filter((i) => !i.is_projected && i.date <= todayIso).slice(-1)[0] ??
    pmcItems[pmcItems.length - 1];

  const wellItems = wellData?.items ?? [];
  const powerRecords = Object.entries(mmData?.power ?? {});

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="center" wrap="wrap">
        <Group gap="xs">
          <TrendingUp size={24} color="#18abf8" />
          <Title order={2}>Forme &amp; Charge d&apos;entraînement</Title>
        </Group>

        <Group gap="xs">
          {isStaff && (membersData?.members ?? []).length > 0 && (
            <Select
              placeholder="Athlète"
              value={effectiveUid}
              onChange={(v) => v && setSelectedUid(v)}
              data={(membersData?.members ?? []).map((m) => ({
                value: m.uid,
                label:
                  `${m.first_name ?? ""} ${m.last_name ?? ""}`.trim() ||
                  m.email ||
                  m.uid,
              }))}
              style={{ width: 220 }}
            />
          )}
          <SegmentedControl
            size="xs"
            value={daysRange}
            onChange={setDaysRange}
            data={[
              { value: "30", label: "30j" },
              { value: "90", label: "90j" },
              { value: "180", label: "6m" },
              { value: "365", label: "1an" },
            ]}
          />
          <Button
            size="xs"
            variant="light"
            leftSection={<FileText size={14} />}
            onClick={openReport}
            disabled={!effectiveUid}
          >
            Bilan / Export
          </Button>
        </Group>
      </Group>

      {isStaff && membersData && athletes.length === 0 && (
        <Alert color="blue" variant="light">
          Aucun athlète n&apos;est encore rattaché à cette organisation. Invitez un athlète depuis la page Membres pour suivre ses KPIs et courbes de performance.
        </Alert>
      )}

      <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="sm">
        <Paper withBorder p="md" radius="md">
          <Text size="xs" c="dimmed">FORME DE FOND (42j)</Text>
          <Text size="xl" fw={700} c="#18abf8">
            {currentMetric ? currentMetric.ctl.toFixed(1) : "0.0"}
          </Text>
          <Text size="xs" c="dimmed">Condition physique</Text>
        </Paper>
        <Paper withBorder p="md" radius="md">
          <Text size="xs" c="dimmed">FATIGUE RÉCENTE (7j)</Text>
          <Text size="xl" fw={700} c="#ff922b">
            {currentMetric ? currentMetric.atl.toFixed(1) : "0.0"}
          </Text>
          <Text size="xs" c="dimmed">Charge des derniers jours</Text>
        </Paper>
        <Paper withBorder p="md" radius="md">
          <Text size="xs" c="dimmed">FRAÎCHEUR</Text>
          <Text
            size="xl"
            fw={700}
            c={(currentMetric?.tsb ?? 0) >= 0 ? "#3ddc97" : "#fa5252"}
          >
            {currentMetric ? currentMetric.tsb.toFixed(1) : "0.0"}
          </Text>
          <Text size="xs" c="dimmed">Équilibre forme / fatigue</Text>
        </Paper>
        <Paper withBorder p="md" radius="md">
          <Text size="xs" c="dimmed">PROGRESSION HEBDO</Text>
          <Text size="xl" fw={700}>
            {currentMetric ? `${currentMetric.ramp_rate > 0 ? "+" : ""}${currentMetric.ramp_rate.toFixed(1)}` : "0.0"}
          </Text>
        </Paper>
      </SimpleGrid>

      <Card withBorder radius="md" p="md">
        <Title order={4} mb="sm">Courbe Forme / Fatigue / Fraîcheur (avec projection 28j)</Title>
        {pmcItems.length > 0 ? (
          <PmcChart items={pmcItems} />
        ) : (
          <Text size="sm" c="dimmed">Aucune donnée de charge disponible.</Text>
        )}

        <Accordion variant="separated" mt="md">
          <Accordion.Item value="how-it-works">
            <Accordion.Control icon={<HelpCircle size={16} color="#18abf8" />}>
              <Text size="sm" fw={600}>
                Comment sont calculées ces valeurs et comment tester leur évolution ?
              </Text>
            </Accordion.Control>
            <Accordion.Panel>
              <Stack gap="xs">
                <Text size="xs" c="dimmed">
                  Chaque activité enregistrée (ou séance planifiée pour la projection à 28 jours) génère un{" "}
                  <strong>score d&apos;effort quotidien</strong> calculé à partir de sa durée et de son intensité
                  (ex. 1 h à intensité seuil ≈ 100 pts d&apos;effort).
                </Text>
                <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="xs">
                  <Paper withBorder p="xs" radius="sm">
                    <Text size="xs" fw={700} c="#18abf8">Forme de fond (42 jours)</Text>
                    <Text size="xs" c="dimmed">
                      Moyenne exponentielle sur 42 jours :{" "}
                      <code>Forme(j) = Forme(j-1) + (Effort(j) - Forme(j-1)) / 42</code>.
                      Elle augmente lentement avec la régularité et baisse doucement au repos.
                    </Text>
                  </Paper>
                  <Paper withBorder p="xs" radius="sm">
                    <Text size="xs" fw={700} c="#ff922b">Fatigue récente (7 jours)</Text>
                    <Text size="xs" c="dimmed">
                      Moyenne exponentielle sur 7 jours :{" "}
                      <code>Fatigue(j) = Fatigue(j-1) + (Effort(j) - Fatigue(j-1)) / 7</code>.
                      Elle réagit vite après une grosse séance ou un bloc d&apos;entraînement.
                    </Text>
                  </Paper>
                  <Paper withBorder p="xs" radius="sm">
                    <Text size="xs" fw={700} c="#3ddc97">Fraîcheur (équilibre)</Text>
                    <Text size="xs" c="dimmed">
                      Écart avant la séance du jour : <code>Fraîcheur(j) = Forme(j-1) - Fatigue(j-1)</code>.
                      Positive = athlète reposé / prêt pour un objectif ; négative = en phase de charge.
                    </Text>
                  </Paper>
                  <Paper withBorder p="xs" radius="sm">
                    <Text size="xs" fw={700}>Progression hebdo &amp; test</Text>
                    <Text size="xs" c="dimmed">
                      Variation de la Forme sur 7 jours (<code>Forme(J) - Forme(J-7)</code>). Pour tester
                      l&apos;évolution : importez un fichier <code>.fit</code> dans <strong>Activités</strong> ou
                      planifiez des séances futures dans <strong>Calendrier</strong> (projection en pointillés).
                    </Text>
                  </Paper>
                </SimpleGrid>
              </Stack>
            </Accordion.Panel>
          </Accordion.Item>
        </Accordion>
      </Card>

      {powerRecords.length > 0 && (
        <Card withBorder radius="md" p="md">
          <Title order={4} mb="sm">Records de puissance sur la période (Mean-Max)</Title>
          <SimpleGrid cols={{ base: 3, sm: 6, md: 11 }} spacing="xs">
            {powerRecords.map(([dur, val]) => (
              <Paper key={dur} withBorder p="xs" radius="sm" style={{ textAlign: "center" }}>
                <Text size="xs" c="dimmed">
                  {Number(dur) >= 60 ? `${Number(dur) / 60}m` : `${dur}s`}
                </Text>
                <Text fw={700} size="sm">{Math.round(val)} W</Text>
              </Paper>
            ))}
          </SimpleGrid>
        </Card>
      )}

      <Card withBorder radius="md" p="md">
        <Group gap="xs" mb="sm">
          <HeartPulse size={18} color="#ff6b6b" />
          <Title order={4}>Journal de forme & récupération</Title>
        </Group>
        {wellItems.length === 0 ? (
          <Text size="sm" c="dimmed">Aucune saisie de forme sur les 30 derniers jours.</Text>
        ) : (
          <Table striped>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Date</Table.Th>
                <Table.Th>Sommeil</Table.Th>
                <Table.Th>Qualité</Table.Th>
                <Table.Th>Fatigue</Table.Th>
                <Table.Th>Courbatures</Table.Th>
                <Table.Th>Stress</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {wellItems.map((w) => (
                <Table.Tr key={w.date}>
                  <Table.Td>{w.date}</Table.Td>
                  <Table.Td>{w.sleep_h != null ? `${w.sleep_h} h` : "—"}</Table.Td>
                  <Table.Td>{w.sleep_quality ?? "—"}/5</Table.Td>
                  <Table.Td>{w.fatigue ?? "—"}/5</Table.Td>
                  <Table.Td>{w.soreness ?? "—"}/5</Table.Td>
                  <Table.Td>{w.stress ?? "—"}/5</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Card>

      {isStaff && athletes.length > 0 && teamKpis && (
        <Card withBorder p="md" radius="md">
          <Stack gap="md">
            <Group justify="space-between" align="center" wrap="wrap">
              <Group gap="xs">
                <Users size={18} color="#18abf8" />
                <Title order={4}>Comparateur &amp; KPIs d&apos;équipe</Title>
              </Group>
              <MultiSelect
                placeholder="Filtrer les athlètes à comparer"
                size="xs"
                clearable
                value={compareUids}
                onChange={setCompareUids}
                data={athletes.map((a) => ({
                  value: a.uid,
                  label: `${a.first_name ?? ""} ${a.last_name ?? ""}`.trim() || a.email || a.uid,
                }))}
                style={{ minWidth: 260 }}
              />
            </Group>

            <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="sm">
              <Paper withBorder p="sm" radius="md">
                <Text size="xs" c="dimmed">ATHLÈTES SUIVIS</Text>
                <Text size="lg" fw={700}>{teamKpis.team.athletes_count}</Text>
              </Paper>
              <Paper withBorder p="sm" radius="md">
                <Text size="xs" c="dimmed">FORME MOYENNE</Text>
                <Text size="lg" fw={700} c="#18abf8">{teamKpis.team.avg_ctl.toFixed(1)}</Text>
              </Paper>
              <Paper withBorder p="sm" radius="md">
                <Text size="xs" c="dimmed">EFFORT 7J ÉQUIPE</Text>
                <Text size="lg" fw={700} c="#ff922b">{teamKpis.team.total_tss_7d.toFixed(0)} pts</Text>
              </Paper>
              <Paper withBorder p="sm" radius="md">
                <Text size="xs" c="dimmed">ASSIDUITÉ MOYENNE</Text>
                <Text size="lg" fw={700} c="teal">{teamKpis.team.avg_compliance_pct.toFixed(1)}%</Text>
              </Paper>
            </SimpleGrid>

            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Athlète</Table.Th>
                  <Table.Th>Forme</Table.Th>
                  <Table.Th>Fatigue</Table.Th>
                  <Table.Th>Fraîcheur</Table.Th>
                  <Table.Th>Effort 7j</Table.Th>
                  <Table.Th>Effort 28j</Table.Th>
                  <Table.Th>Assiduité 28j</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {teamKpis.athletes.map((row) => (
                  <Table.Tr
                    key={row.uid}
                    style={{ cursor: "pointer" }}
                    onClick={() => setSelectedUid(row.uid)}
                  >
                    <Table.Td fw={600}>{row.name}</Table.Td>
                    <Table.Td>{row.ctl.toFixed(1)}</Table.Td>
                    <Table.Td>{row.atl.toFixed(1)}</Table.Td>
                    <Table.Td>
                      <Badge color={row.tsb < -25 ? "red" : row.tsb > 5 ? "teal" : "gray"} variant="light">
                        {row.tsb > 0 ? `+${row.tsb.toFixed(1)}` : row.tsb.toFixed(1)}
                      </Badge>
                    </Table.Td>
                    <Table.Td>{row.tss_7d.toFixed(0)}</Table.Td>
                    <Table.Td>{row.tss_28d.toFixed(0)}</Table.Td>
                    <Table.Td>{row.compliance_pct_28d.toFixed(1)}%</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Stack>
        </Card>
      )}

      <Modal opened={reportOpened} onClose={closeReport} title="Bilan athlète exportable" size="lg">
        {!reportData ? (
          <Text size="sm" c="dimmed">Chargement du bilan...</Text>
        ) : (
          <Stack gap="md">
            <Group justify="space-between">
              <div>
                <Title order={4}>{reportData.athlete_name}</Title>
                <Text size="xs" c="dimmed">
                  Période : {reportData.period.from} → {reportData.period.to} ({reportData.period.days} jours)
                </Text>
              </div>
              <Group gap="xs">
                <Button
                  size="xs"
                  color="cyan"
                  leftSection={<Download size={14} />}
                  loading={pdfLoading}
                  onClick={handleDownloadPdf}
                >
                  Télécharger PDF
                </Button>
                <Button
                  size="xs"
                  variant="light"
                  leftSection={<Printer size={14} />}
                  onClick={() => window.print()}
                >
                  Imprimer
                </Button>
              </Group>
            </Group>
            <Divider />
            <SimpleGrid cols={2} spacing="sm">
              <Paper withBorder p="sm" radius="md">
                <Text size="xs" c="dimmed">ACTIVITÉS</Text>
                <Text fw={700}>{reportData.activities.count} séance(s)</Text>
                <Text size="xs" c="dimmed">
                  {(reportData.activities.total_duration_s / 3600).toFixed(1)} h · {reportData.activities.total_distance_km} km · {reportData.activities.total_tss} TSS
                </Text>
              </Paper>
              <Paper withBorder p="sm" radius="md">
                <Text size="xs" c="dimmed">CONFORMITÉ PLAN</Text>
                <Text fw={700} c="teal">{reportData.compliance.pct}%</Text>
                <Text size="xs" c="dimmed">
                  {reportData.compliance.planned_done} / {reportData.compliance.planned_total} séances réalisées
                </Text>
              </Paper>
              <Paper withBorder p="sm" radius="md">
                <Text size="xs" c="dimmed">CHARGE PMC (FIN DE PÉRIODE)</Text>
                <Text fw={700}>
                  CTL {reportData.pmc.end ? reportData.pmc.end.ctl.toFixed(1) : "—"} · ATL {reportData.pmc.end ? reportData.pmc.end.atl.toFixed(1) : "—"} · TSB {reportData.pmc.end ? reportData.pmc.end.tsb.toFixed(1) : "—"}
                </Text>
              </Paper>
              <Paper withBorder p="sm" radius="md">
                <Text size="xs" c="dimmed">FORME MOYENNE</Text>
                <Text fw={700}>
                  Sommeil : {reportData.wellness.avg_sleep_h != null ? `${reportData.wellness.avg_sleep_h} h` : "—"} · Fatigue : {reportData.wellness.avg_fatigue != null ? `${reportData.wellness.avg_fatigue}/5` : "—"}
                </Text>
              </Paper>
            </SimpleGrid>
            <Paper withBorder p="sm" radius="md">
              <Text size="xs" c="dimmed" mb={4}>RECORDS DE PUISSANCE SUR LA PÉRIODE</Text>
              <Group gap="lg">
                <Text size="sm">5s : <strong>{reportData.records_power_w["5s"] ?? "—"} W</strong></Text>
                <Text size="sm">1m : <strong>{reportData.records_power_w["1m"] ?? "—"} W</strong></Text>
                <Text size="sm">5m : <strong>{reportData.records_power_w["5m"] ?? "—"} W</strong></Text>
                <Text size="sm">20m : <strong>{reportData.records_power_w["20m"] ?? "—"} W</strong></Text>
              </Group>
            </Paper>
          </Stack>
        )}
      </Modal>
    </Stack>
  );
}
