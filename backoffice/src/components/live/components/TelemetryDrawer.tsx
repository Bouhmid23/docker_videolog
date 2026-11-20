import { useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import {
  buildLiveHrZones,
  dirName,
  fmtCoord,
  formatHrZoneSource,
  hrScaleMax,
  toFiniteNum,
  zoneOf,
  type HrZoneDef,
} from "../codecs/telemetryCodec";
import type { TelemetryData, WorkoutThresholds } from "../types";

const HR_MIN = 50;
const HR_WINDOW_MS = 90000;
const GAUGE_LEN = Math.PI * 80;
const RING_LEN = 2 * Math.PI * 26;
const FOLD_KEY = "teleFolded";

function getFoldedSet(): Set<string> {
  try {
    const raw = JSON.parse(localStorage.getItem(FOLD_KEY) || "[]");
    return new Set(Array.isArray(raw) ? raw : []);
  } catch {
    return new Set();
  }
}

interface AthleteCardProps {
  identity: string;
  displayName?: string;
  thresholds?: WorkoutThresholds | null;
  drawerOpen: boolean;
  latestRef: React.MutableRefObject<Map<string, TelemetryData>>;
  telemetryListenersRef: React.MutableRefObject<
    Set<(id: string, data: TelemetryData | null) => void>
  >;
}

function drawHrCanvas(
  cv: HTMLCanvasElement | null,
  ptsAll: Array<{ t: number; v: number }>,
  hrZones: readonly HrZoneDef[],
  hrMaxScale: number
) {
  if (!cv) return;
  const ctx = cv.getContext("2d");
  if (!ctx) return;
  const W = cv.width;
  const H = cv.height;
  const now = Date.now();
  ctx.clearRect(0, 0, W, H);

  const y = (v: number) =>
    H -
    ((Math.min(Math.max(v, HR_MIN), hrMaxScale) - HR_MIN) /
      (hrMaxScale - HR_MIN)) *
      H;

  let top = hrMaxScale;
  ctx.font = "600 18px Montserrat, system-ui, sans-serif";
  ctx.textAlign = "right";
  for (let i = hrZones.length - 1; i >= 0; i--) {
    const lo = i === 0 ? HR_MIN : hrZones[i - 1].max;
    ctx.fillStyle = hrZones[i].color + "14";
    ctx.fillRect(0, y(top), W, y(lo) - y(top));
    ctx.fillStyle = hrZones[i].color + "99";
    ctx.fillText(hrZones[i].name, W - 6, y(top) + 20);
    top = lo;
  }

  const pts = ptsAll.filter((p) => now - p.t <= HR_WINDOW_MS);
  if (pts.length < 2) {
    ctx.fillStyle = "#98a3b0";
    ctx.textAlign = "center";
    ctx.font = "500 20px Montserrat, system-ui, sans-serif";
    ctx.fillText(
      pts.length ? "Mesure en cours…" : "En attente du capteur",
      W / 2,
      H / 2 + 6
    );
    return;
  }

  const x = (t: number) => W * (1 - (now - t) / HR_WINDOW_MS);
  ctx.lineWidth = 4;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  for (let i = 1; i < pts.length; i++) {
    ctx.strokeStyle = zoneOf(pts[i].v, hrZones).color;
    ctx.beginPath();
    ctx.moveTo(x(pts[i - 1].t), y(pts[i - 1].v));
    ctx.lineTo(x(pts[i].t), y(pts[i].v));
    ctx.stroke();
  }
  const last = pts[pts.length - 1];
  ctx.fillStyle = zoneOf(last.v, hrZones).color;
  ctx.beginPath();
  ctx.arc(x(last.t), y(last.v), 7, 0, Math.PI * 2);
  ctx.fill();
}

function drawAltCanvas(cv: HTMLCanvasElement | null, vals: number[]) {
  if (!cv) return;
  const ctx = cv.getContext("2d");
  if (!ctx) return;
  const W = cv.width;
  const H = cv.height;
  ctx.clearRect(0, 0, W, H);
  if (vals.length < 2) return;

  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const span = Math.max(hi - lo, 4);
  const pt = (i: number): [number, number] => [
    (i / (vals.length - 1)) * W,
    H - 4 - ((vals[i] - lo) / span) * (H - 8),
  ];

  ctx.beginPath();
  ctx.moveTo(0, H);
  vals.forEach((_, i) => ctx.lineTo(...pt(i)));
  ctx.lineTo(W, H);
  ctx.closePath();
  ctx.fillStyle = "rgba(24,171,248,.18)";
  ctx.fill();

  ctx.beginPath();
  vals.forEach((_, i) => (i ? ctx.lineTo(...pt(i)) : ctx.moveTo(...pt(i))));
  ctx.strokeStyle = "#18abf8";
  ctx.lineWidth = 2.5;
  ctx.stroke();
}

function TelemetryAthleteCard({
  identity,
  displayName,
  thresholds,
  drawerOpen,
  latestRef,
  telemetryListenersRef,
}: AthleteCardProps) {
  const hrZones = useMemo(() => buildLiveHrZones(thresholds), [thresholds]);
  const hrMaxScale = useMemo(() => hrScaleMax(thresholds), [thresholds]);
  const hrZoneSource = useMemo(() => formatHrZoneSource(thresholds), [thresholds]);
  const hrZonesRef = useRef(hrZones);
  const hrMaxScaleRef = useRef(hrMaxScale);
  hrZonesRef.current = hrZones;
  hrMaxScaleRef.current = hrMaxScale;
  const [folded, setFolded] = useState(() => getFoldedSet().has(identity));
  const [live, setLive] = useState(false);
  const [hasGps, setHasGps] = useState(false);

  const mapBoxRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const lineRef = useRef<L.Polyline | null>(null);
  const pinRef = useRef<L.Marker | null>(null);
  const pathRef = useRef<Array<[number, number]>>([]);
  const userMovedRef = useRef(0);

  const hrCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const altCanvasRef = useRef<HTMLCanvasElement | null>(null);

  const statsRef = useRef({
    hr: [] as Array<{ t: number; v: number }>,
    alt: [] as number[],
    maxSpeed: 0,
    sumSpeed: 0,
    nSpeed: 0,
    gaugeMax: 30,
    maxPower: 0,
    sumPower: 0,
    nPower: 0,
  });

  const liveTimerRef = useRef(0);

  const [viewState, setViewState] = useState<{
    role: string;
    coords: string;
    bpm: string;
    zoneName: string;
    zoneColor: string;
    heartBeatSec: number | null;
    speed: string;
    speedMax: string;
    speedAvg: string;
    gaugeMax: number;
    speedFrac: number;
    power: string;
    powerMax: string;
    powerAvg: string;
    hasPower: boolean;
    cadence: string;
    cadUnit: string;
    cadFrac: number;
    altitude: string;
    bearing: number | null;
    dirText: string;
  }>({
    role: "",
    coords: "–",
    bpm: "–",
    zoneName: "Pas de capteur",
    zoneColor: "",
    heartBeatSec: null,
    speed: "–",
    speedMax: "–",
    speedAvg: "–",
    gaugeMax: 30,
    speedFrac: 0,
    power: "–",
    powerMax: "–",
    powerAvg: "–",
    hasPower: false,
    cadence: "–",
    cadUnit: "rpm",
    cadFrac: 0,
    altitude: "–",
    bearing: null,
    dirText: "–",
  });

  useEffect(() => {
    const applyTelemetry = (t: TelemetryData) => {
      const now = Date.now();
      const gps = t.gps || {};
      const hr = toFiniteNum(t.heart_rate ?? t.heartRate);
      const speed = toFiniteNum(t.speed);
      const cad = toFiniteNum(t.cadence);
      const powerW = toFiniteNum(t.power_w);
      const alt = toFiniteNum(t.altitude ?? gps.altitude);
      const brg = toFiniteNum(t.bearing ?? gps.bearing);
      const lat = toFiniteNum(gps.lat);
      const lng = toFiniteNum(gps.lng);

      setLive(true);
      window.clearTimeout(liveTimerRef.current);
      liveTimerRef.current = window.setTimeout(() => setLive(false), 3000);

      // Carte Leaflet
      let nextCoords = "–";
      if (lat != null && lng != null && !(lat === 0 && lng === 0)) {
        nextCoords = `${fmtCoord(lat, "N", "S")} · ${fmtCoord(lng, "E", "O")}`;
        setHasGps(true);
        const box = mapBoxRef.current;
        if (box) {
          if (!mapRef.current) {
            const m = L.map(box, {
              zoomControl: false,
              attributionControl: true,
              scrollWheelZoom: false,
            }).setView([lat, lng], 16);
            L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
              maxZoom: 19,
              attribution: "© OpenStreetMap",
            }).addTo(m);
            lineRef.current = L.polyline([], {
              color: "#18abf8",
              weight: 4,
              opacity: 0.9,
            }).addTo(m);
            pinRef.current = L.marker([lat, lng], {
              icon: L.divIcon({
                className: "tc-pin",
                html: "<span class='pin-pulse'></span><i class='pin-arrow'></i>",
                iconSize: [26, 26],
                iconAnchor: [13, 13],
              }),
              interactive: false,
            }).addTo(m);
            m.on("dragstart zoomstart", () => {
              userMovedRef.current = Date.now();
            });
            mapRef.current = m;
            [60, 450, 1200].forEach((ms) =>
              window.setTimeout(() => m.invalidateSize({ pan: false }), ms)
            );
          }

          const path = pathRef.current;
          const last = path[path.length - 1];
          if (!last || last[0] !== lat || last[1] !== lng) {
            path.push([lat, lng]);
            if (path.length > 400) path.shift();
            lineRef.current?.setLatLngs(path);
          }
          pinRef.current?.setLatLng([lat, lng]);
          const arrow = pinRef.current
            ?.getElement()
            ?.querySelector(".pin-arrow") as HTMLElement | null;
          if (arrow) arrow.style.transform = `rotate(${brg ?? 0}deg)`;
          if (Date.now() - userMovedRef.current > 15000) {
            mapRef.current.panTo([lat, lng], { animate: true, duration: 0.6 });
          }
        }
      }

      const st = statsRef.current;

      // Cardio
      let bpmStr = "–";
      let zoneName = "Pas de capteur";
      let zoneColor = "";
      let heartBeatSec: number | null = null;
      if (hr != null && hr > 0) {
        st.hr.push({ t: now, v: hr });
        if (st.hr.length > 600) st.hr.shift();
        const z = zoneOf(hr, hrZonesRef.current);
        bpmStr = String(Math.round(hr));
        zoneName = z.name;
        zoneColor = z.color;
        heartBeatSec = Math.max(0.3, 60 / hr);
      }
      drawHrCanvas(
        hrCanvasRef.current,
        st.hr,
        hrZonesRef.current,
        hrMaxScaleRef.current
      );

      // Vitesse
      let speedStr = "–";
      let speedMaxStr = st.nSpeed ? st.maxSpeed.toFixed(1) : "–";
      let speedAvgStr = st.nSpeed ? (st.sumSpeed / st.nSpeed).toFixed(1) : "–";
      let speedFrac = 0;
      if (speed != null) {
        st.maxSpeed = Math.max(st.maxSpeed, speed);
        st.sumSpeed += speed;
        st.nSpeed += 1;
        while (speed > st.gaugeMax * 0.9) {
          st.gaugeMax += st.gaugeMax < 60 ? 10 : 20;
        }
        speedFrac = Math.min(speed / st.gaugeMax, 1);
        speedStr = speed.toFixed(1);
        speedMaxStr = st.maxSpeed.toFixed(1);
        speedAvgStr = (st.sumSpeed / st.nSpeed).toFixed(1);
      }

      // Puissance (Home-trainer / Rameur / Capteur de puissance)
      let powerStr = "–";
      let powerMaxStr = st.nPower ? String(Math.round(st.maxPower)) : "–";
      let powerAvgStr = st.nPower ? String(Math.round(st.sumPower / st.nPower)) : "–";
      if (powerW != null && powerW >= 0) {
        st.maxPower = Math.max(st.maxPower, powerW);
        st.sumPower += powerW;
        st.nPower += 1;
        powerStr = String(Math.round(powerW));
        powerMaxStr = String(Math.round(st.maxPower));
        powerAvgStr = String(Math.round(st.sumPower / st.nPower));
      }

      // Cadence adaptative (Rameur ≤ 50 spm, Cyclisme ≤ 120 rpm, Course/Tapis > 135 spm)
      const cadMax = cad != null && cad > 135 ? 200 : cad != null && cad > 0 && cad <= 45 && speed != null && speed < 25 ? 45 : 120;
      const cadUnit = cad != null && cad > 135 ? "spm" : "rpm";

      // Altitude
      let altStr = "–";
      if (alt != null) {
        altStr = alt.toFixed(0);
        st.alt.push(alt);
        if (st.alt.length > 90) st.alt.shift();
        drawAltCanvas(altCanvasRef.current, st.alt);
      }

      setViewState((prev) => ({
        ...prev,
        role: t.role || "",
        coords: nextCoords === "–" ? prev.coords : nextCoords,
        bpm: bpmStr,
        zoneName,
        zoneColor,
        heartBeatSec,
        speed: speed != null ? speedStr : prev.speed,
        speedMax: speedMaxStr,
        speedAvg: speedAvgStr,
        gaugeMax: st.gaugeMax,
        speedFrac: speed != null ? speedFrac : prev.speedFrac,
        power: powerW != null ? powerStr : prev.power,
        powerMax: powerMaxStr,
        powerAvg: powerAvgStr,
        hasPower: prev.hasPower || powerW != null,
        cadence: cad != null ? String(Math.round(cad)) : "–",
        cadUnit: cad != null ? cadUnit : prev.cadUnit,
        cadFrac: cad != null ? Math.min(cad / cadMax, 1) : 0,
        altitude: alt != null ? altStr : prev.altitude,
        bearing: brg != null ? brg : prev.bearing,
        dirText: brg != null ? `${Math.round(brg)}° ${dirName(brg)}` : prev.dirText,
      }));
    };

    const listeners = telemetryListenersRef.current;
    const initial = latestRef.current.get(identity);
    if (initial) applyTelemetry(initial);

    const listener = (id: string, data: TelemetryData | null) => {
      if (id === identity && data) applyTelemetry(data);
    };
    listeners.add(listener);

    const hrInterval = window.setInterval(() => {
      drawHrCanvas(
        hrCanvasRef.current,
        statsRef.current.hr,
        hrZonesRef.current,
        hrMaxScaleRef.current
      );
    }, 1000);

    return () => {
      listeners.delete(listener);
      window.clearInterval(hrInterval);
      window.clearTimeout(liveTimerRef.current);
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
      }
    };
  }, [identity, latestRef, telemetryListenersRef]);

  useEffect(() => {
    if (!drawerOpen || folded || !mapRef.current) return;
    const timers = [50, 200, 450, 800].map((ms) =>
      window.setTimeout(() => mapRef.current?.invalidateSize({ pan: false }), ms)
    );
    return () => timers.forEach((t) => window.clearTimeout(t));
  }, [drawerOpen, folded]);

  useEffect(() => {
    const box = mapBoxRef.current;
    if (!box || !window.ResizeObserver) return;
    const ro = new ResizeObserver(() => mapRef.current?.invalidateSize({ pan: false }));
    ro.observe(box);
    return () => ro.disconnect();
  }, []);

  const toggleFold = () => {
    const next = !folded;
    setFolded(next);
    const set = getFoldedSet();
    if (next) set.add(identity);
    else set.delete(identity);
    try {
      localStorage.setItem(FOLD_KEY, JSON.stringify([...set]));
    } catch {
      // ignore
    }
  };

  return (
    <section className={`tcard${folded ? " folded" : ""}`}>
      <header className="tc-head" onClick={toggleFold}>
        <button
          className="tc-fold"
          type="button"
          aria-expanded={!folded}
          title={folded ? "Déplier" : "Replier"}
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M6 9l6 6 6-6" />
          </svg>
        </button>
        <b>{displayName || identity.split(":")[0]}</b>
        <span className="tc-role">{viewState.role}</span>
        <span className={`tc-live${live ? " on" : ""}`} title="Mesure reçue" />
      </header>

      <div className="tc-body">
        <div className="tc-block" hidden={!hasGps}>
          <div className="tc-label">Position GPS</div>
          <div ref={mapBoxRef} className="tc-map" />
          <div className="tc-coords">{viewState.coords}</div>
        </div>

        {viewState.hasPower && (
          <div className="tc-block">
            <div className="tc-label">Puissance</div>
            <div className="tc-hr">
              <div className="tc-bpm">
                <strong style={{ color: "#a855f7" }}>{viewState.power}</strong>
                <small>W</small>
              </div>
              <span
                className="tc-zone"
                style={{
                  color: "#a855f7",
                  borderColor: "rgba(168,85,247,0.4)",
                  marginLeft: "auto",
                }}
              >
                Capteur W
              </span>
            </div>
            <div className="tc-stats" style={{ marginTop: 6 }}>
              <span>
                Max <b className="s-max">{viewState.powerMax} W</b>
              </span>
              <span>
                Moy. <b className="s-avg">{viewState.powerAvg} W</b>
              </span>
            </div>
          </div>
        )}

        <div className="tc-block">
          <div className="tc-label">
            Fréquence cardiaque
            {hrZoneSource && (
              <span style={{ marginLeft: 6, opacity: 0.7, fontWeight: 500 }}>
                · {hrZoneSource}
              </span>
            )}
          </div>
          <div className="tc-hr">
            <svg
              className={`tc-heart${viewState.heartBeatSec ? " beat" : ""}`}
              viewBox="0 0 24 24"
              style={{
                fill: viewState.zoneColor || undefined,
                animationDuration: viewState.heartBeatSec
                  ? `${viewState.heartBeatSec}s`
                  : undefined,
              }}
            >
              <path d="M12 21s-7.5-4.6-9.6-9.2C.9 8.4 2.8 4.5 6.5 4.5c2 0 3.6 1 4.5 2.6.9-1.6 2.5-2.6 4.5-2.6 3.7 0 5.6 3.9 4.1 7.3C19.5 16.4 12 21 12 21z" />
            </svg>
            <div className="tc-bpm">
              <strong>{viewState.bpm}</strong>
              <small>bpm</small>
            </div>
            <span
              className="tc-zone"
              style={
                viewState.zoneColor
                  ? {
                      color: viewState.zoneColor,
                      borderColor: `${viewState.zoneColor}66`,
                    }
                  : undefined
              }
            >
              {viewState.zoneName}
            </span>
          </div>
          <canvas
            ref={hrCanvasRef}
            className="tc-hr-chart"
            width={680}
            height={170}
          />
        </div>

        <div className="tc-block">
          <div className="tc-label">Vitesse</div>
          <div className="tc-gauge">
            <svg viewBox="0 0 200 122">
              <path className="g-track" d="M20 100 A80 80 0 0 1 180 100" />
              <path
                className="g-val"
                d="M20 100 A80 80 0 0 1 180 100"
                stroke="url(#tcGrad)"
                style={{
                  strokeDasharray: `${GAUGE_LEN * viewState.speedFrac} ${GAUGE_LEN}`,
                }}
              />
              <g
                className="g-needle"
                style={{
                  transform: `rotate(${-90 + viewState.speedFrac * 180}deg)`,
                }}
              >
                <circle cx="100" cy="20" r="8" />
              </g>
              <text x="20" y="120" className="g-t">
                0
              </text>
              <text x="180" y="120" className="g-t g-max" textAnchor="end">
                {viewState.gaugeMax}
              </text>
            </svg>
            <div className="tc-speed">
              <strong>{viewState.speed}</strong>
              <small>km/h</small>
            </div>
          </div>
          <div className="tc-stats">
            <span>
              Max <b className="s-max">{viewState.speedMax}</b>
            </span>
            <span>
              Moy. <b className="s-avg">{viewState.speedAvg}</b>
            </span>
          </div>
        </div>

        <div className="tc-row">
          <div className="tc-mini">
            <div className="tc-label">Cadence</div>
            <div className="tc-ringwrap">
              <svg viewBox="0 0 64 64" className="tc-ring">
                <circle className="r-track" cx="32" cy="32" r="26" />
                <circle
                  className="r-val"
                  cx="32"
                  cy="32"
                  r="26"
                  transform="rotate(-90 32 32)"
                  style={{
                    strokeDasharray: `${RING_LEN * viewState.cadFrac} ${RING_LEN}`,
                  }}
                />
              </svg>
              <div className="tc-ringtxt">
                <strong>{viewState.cadence}</strong>
                <small>{viewState.cadUnit}</small>
              </div>
            </div>
          </div>

          <div className="tc-mini">
            <div className="tc-label">Altitude</div>
            <div className="tc-alt">
              <strong>{viewState.altitude}</strong>
              <small>m</small>
            </div>
            <canvas
              ref={altCanvasRef}
              className="tc-alt-chart"
              width={180}
              height={48}
            />
          </div>

          <div className="tc-mini">
            <div className="tc-label">Cap</div>
            <svg className="tc-compass" viewBox="0 0 64 64">
              <circle cx="32" cy="32" r="28" />
              <text x="32" y="13" textAnchor="middle">
                N
              </text>
              <g
                className="c-needle"
                style={
                  viewState.bearing != null
                    ? { transform: `rotate(${viewState.bearing}deg)` }
                    : undefined
                }
              >
                <polygon points="32,15 37,34 32,31 27,34" />
              </g>
            </svg>
            <small className="tc-dir">{viewState.dirText}</small>
          </div>
        </div>
      </div>
    </section>
  );
}

interface TelemetryDrawerProps {
  open: boolean;
  onToggleOpen: () => void;
  nameMap?: Record<string, string>;
  thresholdsMap?: Record<string, WorkoutThresholds>;
  latestRef: React.MutableRefObject<Map<string, TelemetryData>>;
  telemetryListenersRef: React.MutableRefObject<
    Set<(id: string, data: TelemetryData | null) => void>
  >;
}

export function TelemetryDrawer({
  open,
  onToggleOpen,
  nameMap,
  thresholdsMap,
  latestRef,
  telemetryListenersRef,
}: TelemetryDrawerProps) {
  const [identities, setIdentities] = useState<string[]>([]);

  useEffect(() => {
    const listeners = telemetryListenersRef.current;
    setIdentities([...latestRef.current.keys()]);
    const onTelemetry = (id: string, data: TelemetryData | null) => {
      if (id === "*" && data === null) {
        setIdentities([]);
        return;
      }
      if (data === null) {
        setIdentities((prev) => prev.filter((x) => x !== id));
        return;
      }
      setIdentities((prev) => (prev.includes(id) ? prev : [...prev, id]));
    };
    listeners.add(onTelemetry);
    return () => {
      listeners.delete(onTelemetry);
    };
  }, [latestRef, telemetryListenersRef]);

  return (
    <>
      <svg
        width="0"
        height="0"
        style={{ position: "absolute" }}
        aria-hidden="true"
      >
        <defs>
          <linearGradient id="tcGrad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stopColor="#3a8fd6" />
            <stop offset="1" stopColor="#18abf8" />
          </linearGradient>
        </defs>
      </svg>

      <aside id="teleDrawer" className="tele-drawer" aria-label="Télémétrie">
        <button
          id="teleToggle"
          className="tele-toggle"
          type="button"
          aria-expanded={open}
          aria-controls="teleCards"
          title="Afficher / masquer la télémétrie"
          onClick={onToggleOpen}
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.4"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M15 5l-7 7 7 7" />
          </svg>
          <span>Télémétrie</span>
        </button>

        <div className="tele-head">Télémétrie en direct</div>

        <div id="teleCards" className="tele-cards">
          {!identities.length ? (
            <p className="tele-empty">En attente des données du sportif…</p>
          ) : (
            identities.map((id) => (
              <TelemetryAthleteCard
                key={id}
                identity={id}
                displayName={
                  nameMap?.[id] || nameMap?.[id.split(":")[0]] || id.split(":")[0]
                }
                thresholds={
                  thresholdsMap?.[id] || thresholdsMap?.[id.split(":")[0]] || null
                }
                drawerOpen={open}
                latestRef={latestRef}
                telemetryListenersRef={telemetryListenersRef}
              />
            ))
          )}
        </div>
      </aside>
    </>
  );
}
