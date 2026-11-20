import "leaflet/dist/leaflet.css";
import "./SyncedGpsMap.css";
import L from "leaflet";
import { useEffect, useMemo, useRef, useState } from "react";
import { ActionIcon, Badge, Card, Group, Text, Tooltip } from "@mantine/core";
import { Crosshair, MapPin, Navigation } from "lucide-react";
import {
  downsampleGpsPinpoints,
  findClosestGpsPoint,
  type GpsTrackPoint,
} from "./gpsUtils";
import { formatPtsWithTenths } from "./timeUtils";

interface SyncedGpsMapProps {
  track: GpsTrackPoint[];
  currentTimeMs: number;
  onSeek?: (ptsMs: number) => void;
}

interface PinpointEntry {
  pt: GpsTrackPoint;
  marker: L.CircleMarker;
}

const ROOM_FOLLOW_PAUSE_MS = 15_000;
const ROOM_DEFAULT_ZOOM = 16;

export function SyncedGpsMap({
  track,
  currentTimeMs,
  onSeek,
}: SyncedGpsMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const pinRef = useRef<L.Marker | null>(null);
  const pinpointEntriesRef = useRef<PinpointEntry[]>([]);
  const userMovedRef = useRef<number>(0);
  const onSeekRef = useRef(onSeek);
  onSeekRef.current = onSeek;

  const [followMarker, setFollowMarker] = useState(true);
  const followRef = useRef(followMarker);
  followRef.current = followMarker;

  const pinpoints = useMemo(
    () => downsampleGpsPinpoints(track, 100),
    [track]
  );

  const activePoint = useMemo(
    () => findClosestGpsPoint(track, currentTimeMs),
    [track, currentTimeMs]
  );
  const activePointRef = useRef(activePoint);
  activePointRef.current = activePoint;

  // Initialiser la carte Leaflet avec le même comportement que TelemetryDrawer (Live Room)
  useEffect(() => {
    const el = containerRef.current;
    if (!el || track.length === 0) return;

    const first = track[0];
    const map = L.map(el, {
      zoomControl: true,
      attributionControl: true,
      scrollWheelZoom: false,
    }).setView([first.lat, first.lng], ROOM_DEFAULT_ZOOM);
    mapRef.current = map;
    map.attributionControl.setPrefix(false);

    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap",
    }).addTo(map);

    // Pinpoints discrets synchronisés (sans polyline tracée sur la carte)
    const entries: PinpointEntry[] = [];
    for (const pt of pinpoints) {
      const tooltipParts = [formatPtsWithTenths(pt.pts_ms)];
      if (pt.speed !== null) tooltipParts.push(`${pt.speed.toFixed(1)} km/h`);

      const cm = L.circleMarker([pt.lat, pt.lng], {
        radius: 3.5,
        color: "#0f172a",
        fillColor: "#94a3b8",
        fillOpacity: 0.45,
        weight: 1.5,
      })
        .bindTooltip(tooltipParts.join(" · "), { direction: "top" })
        .addTo(map);

      cm.on("click", () => {
        onSeekRef.current?.(pt.pts_ms);
      });
      entries.push({ pt, marker: cm });
    }
    pinpointEntriesRef.current = entries;

    // Flèche directionnelle identique à la Live Room (.tc-pin / .pin-pulse / .pin-arrow)
    const arrowMarker = L.marker([first.lat, first.lng], {
      icon: L.divIcon({
        className: "tc-pin",
        html: "<span class='pin-pulse'></span><i class='pin-arrow'></i>",
        iconSize: [26, 26],
        iconAnchor: [13, 13],
      }),
      interactive: false,
      zIndexOffset: 1000,
    }).addTo(map);
    pinRef.current = arrowMarker;

    map.on("dragstart zoomstart", () => {
      userMovedRef.current = Date.now();
    });

    const resizeTimers = [60, 450, 1200].map((ms) =>
      window.setTimeout(() => map.invalidateSize({ pan: false }), ms)
    );

    return () => {
      resizeTimers.forEach((id) => window.clearTimeout(id));
      pinpointEntriesRef.current = [];
      pinRef.current = null;
      mapRef.current = null;
      map.remove();
    };
  }, [track, pinpoints]);

  // Synchroniser la flèche, sa rotation (bearing), les pinpoints et le panTo sur le timecode
  useEffect(() => {
    const map = mapRef.current;
    const pin = pinRef.current;
    if (!map || !pin || !activePoint) return;

    const pos: L.LatLngTuple = [activePoint.lat, activePoint.lng];
    pin.setLatLng(pos);

    const arrow = pin
      .getElement()
      ?.querySelector(".pin-arrow") as HTMLElement | null;
    if (arrow) {
      arrow.style.transform = `rotate(${activePoint.bearing ?? 0}deg)`;
    }

    // Mettre à jour l'état parcouru / à venir des pinpoints
    for (const entry of pinpointEntriesRef.current) {
      const passed = entry.pt.pts_ms <= currentTimeMs;
      entry.marker.setStyle({
        radius: passed ? 4 : 3.5,
        fillColor: passed ? "#18abf8" : "#94a3b8",
        fillOpacity: passed ? 0.9 : 0.45,
      });
    }

    if (
      followRef.current &&
      Date.now() - userMovedRef.current > ROOM_FOLLOW_PAUSE_MS
    ) {
      map.panTo(pos, { animate: true, duration: 0.6 });
    }
  }, [activePoint, currentTimeMs]);

  if (track.length === 0) return null;

  return (
    <Card withBorder radius="md" p="md">
      <Group justify="space-between" mb="xs">
        <Group gap="xs">
          <MapPin size={18} color="#22d3ee" />
          <Text fw={600} size="sm">
            Carte GPS synchronisée ({pinpoints.length} pinpoints)
          </Text>
        </Group>

        <Group gap={6}>
          <Tooltip
            label={
              followMarker
                ? "Auto-suivi actif (cliquer pour figer)"
                : "Activer l'auto-suivi du marqueur"
            }
          >
            <ActionIcon
              size="sm"
              variant={followMarker ? "filled" : "light"}
              color="cyan"
              onClick={() => {
                setFollowMarker((prev) => {
                  const next = !prev;
                  if (next) userMovedRef.current = 0;
                  return next;
                });
              }}
              aria-label="Basculer l'auto-suivi GPS"
            >
              <Navigation size={14} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label="Recentrer sur la position actuelle">
            <ActionIcon
              size="sm"
              variant="light"
              color="gray"
              onClick={() => {
                const map = mapRef.current;
                const pt = activePointRef.current ?? track[0];
                if (map && pt) {
                  userMovedRef.current = 0;
                  map.setView([pt.lat, pt.lng], ROOM_DEFAULT_ZOOM, {
                    animate: true,
                  });
                }
              }}
              aria-label="Recentrer sur la position actuelle"
            >
              <Crosshair size={14} />
            </ActionIcon>
          </Tooltip>
        </Group>
      </Group>

      <div
        ref={containerRef}
        className="vl-replay-map"
        style={{
          width: "100%",
          height: 250,
          borderRadius: 8,
          overflow: "hidden",
          backgroundColor: "#0f172a",
        }}
      />

      {activePoint && (
        <Group justify="space-between" mt="xs" gap="xs">
          <Group gap={6}>
            <Badge size="xs" variant="light" color="cyan">
              {formatPtsWithTenths(activePoint.pts_ms)}
            </Badge>
            {activePoint.speed !== null && (
              <Badge size="xs" variant="outline" color="teal">
                {activePoint.speed.toFixed(1)} km/h
              </Badge>
            )}
            {activePoint.altitude !== null && (
              <Badge size="xs" variant="outline" color="gray">
                {Math.round(activePoint.altitude)} m
              </Badge>
            )}
          </Group>
          <Text size="xs" c="dimmed">
            Clic sur un pinpoint pour naviguer
          </Text>
        </Group>
      )}
    </Card>
  );
}
