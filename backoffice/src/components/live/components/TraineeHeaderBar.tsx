import { useEffect, useRef, useState } from "react";
import { ConnectionQuality } from "livekit-client";
import type { MicInfo, NetSample, PostureDisplay } from "../types";

const WINDOW_MS = 60000;
const KEEP_MS = 125000;
const GAP_MS = 3500;

interface NetTileDef {
  id: keyof Pick<
    NetSample,
    "kbps" | "fps" | "loss" | "jitter" | "rtt" | "poseHz" | "poseAge"
  >;
  label: string;
  fmt: (v: number) => string;
  min: number;
  minSpan: number;
  color?: string;
  warn?: (v: number) => boolean;
  warnText?: string;
}

const msFmt = (v: number) => `${Math.round(v)} ms`;

const NET_DEFS: NetTileDef[] = [
  {
    id: "kbps",
    label: "Débit vidéo",
    fmt: (v) =>
      v >= 1000 ? `${(v / 1000).toFixed(1)} Mb/s` : `${Math.round(v)} kb/s`,
    min: 0,
    minSpan: 200,
    color: "#ff7900",
  },
  {
    id: "fps",
    label: "Images / s",
    fmt: (v) => `${Math.round(v)} ips`,
    min: 0,
    minSpan: 30,
  },
  {
    id: "loss",
    label: "Perte vidéo",
    fmt: (v) => `${v.toFixed(1)} %`,
    min: 0,
    minSpan: 5,
    warn: (v) => v > 2,
    warnText: "Perte élevée",
  },
  {
    id: "jitter",
    label: "Gigue vidéo",
    fmt: msFmt,
    min: 0,
    minSpan: 30,
    warn: (v) => v > 60,
    warnText: "Gigue élevée",
  },
  {
    id: "rtt",
    label: "Latence (RTT)",
    fmt: msFmt,
    min: 0,
    minSpan: 100,
    warn: (v) => v > 300,
    warnText: "Latence élevée",
  },
  {
    id: "poseHz",
    label: "Posture reçue",
    fmt: (v) => `${v.toFixed(1)} /s`,
    min: 0,
    minSpan: 20,
  },
  {
    id: "poseAge",
    label: "Âge de la pose",
    fmt: msFmt,
    min: 0,
    minSpan: 200,
    warn: (v) => v > 500,
    warnText: "Pose en retard",
  },
];

const MIC_LABELS = {
  live: "Micro actif",
  muted: "Micro coupé",
  none: "Aucun micro publié",
  pending: "Son non reçu",
} as const;

function qualityInfo(q: ConnectionQuality | undefined): {
  key: string;
  label: string;
  bars: number;
} {
  switch (q) {
    case ConnectionQuality.Excellent:
      return { key: "excellent", label: "Excellente", bars: 3 };
    case ConnectionQuality.Good:
      return { key: "good", label: "Bonne", bars: 2 };
    case ConnectionQuality.Poor:
      return { key: "poor", label: "Faible", bars: 1 };
    case ConnectionQuality.Lost:
      return { key: "lost", label: "Perdue", bars: 0 };
    default:
      return { key: "unknown", label: "Inconnue", bars: 0 };
  }
}

const niceCeil = (x: number) => {
  const p = Math.pow(10, Math.floor(Math.log10(x)));
  const n = x / p;
  return (
    ([1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((s) => n <= s + 1e-9) ||
      10) * p
  );
};

interface SparkTileState {
  data: Array<{ t: number; v: number }>;
  hover: number | null;
}

function drawSpark(
  cv: HTMLCanvasElement | null,
  def: NetTileDef,
  st: SparkTileState,
  tipEl: HTMLDivElement | null,
  tileEl: HTMLDivElement | null
) {
  if (!cv) return;
  const r = cv.getBoundingClientRect();
  if (!r.width) return;
  const dpr = window.devicePixelRatio || 1;
  const W = Math.round(r.width * dpr);
  const H = Math.round(r.height * dpr);
  if (cv.width !== W || cv.height !== H) {
    cv.width = W;
    cv.height = H;
  }
  const ctx = cv.getContext("2d");
  if (!ctx) return;

  const lineCol = "#232b35";
  const mutedCol = "#98a3b0";
  const cyanCol = "#18abf8";
  const surfaceCol = "#0f1318";
  const now = Date.now();

  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const w = r.width;
  const h = r.height;
  const padT = 4;
  const padB = 3;
  ctx.clearRect(0, 0, w, h);

  const pts = st.data.filter((p) => now - p.t <= WINDOW_MS);
  ctx.font = "500 9px Montserrat, system-ui, sans-serif";
  if (pts.length < 2) {
    ctx.fillStyle = mutedCol;
    ctx.textAlign = "center";
    ctx.fillText(
      pts.length ? "Mesure en cours…" : "En attente des données",
      w / 2,
      h / 2 + 3
    );
    return;
  }

  let lo = Math.min(...pts.map((p) => p.v));
  let hi = Math.max(...pts.map((p) => p.v));
  if (def.min === 0) {
    lo = 0;
    hi = niceCeil(Math.max(hi * 1.15, def.minSpan));
  } else {
    const span = Math.max(hi - lo, def.minSpan);
    const mid = (hi + lo) / 2;
    lo = Math.floor((mid - span * 0.65) / 10) * 10;
    hi = Math.ceil((mid + span * 0.65) / 10) * 10;
  }

  const X = (tt: number) => w * (1 - (now - tt) / WINDOW_MS);
  const Y = (v: number) => padT + (1 - (v - lo) / (hi - lo)) * (h - padT - padB);

  ctx.strokeStyle = lineCol;
  ctx.lineWidth = 1;
  for (const f of [0, 0.5, 1]) {
    const y = Math.round(padT + f * (h - padT - padB)) + 0.5;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(w, y);
    ctx.stroke();
  }
  ctx.fillStyle = mutedCol;
  ctx.textAlign = "left";
  ctx.fillText(String(Math.round(hi * 10) / 10), 2, padT + 9);
  ctx.fillText(String(Math.round(lo * 10) / 10), 2, h - padB - 2);

  const segs: Array<Array<{ t: number; v: number }>> = [];
  let cur: Array<{ t: number; v: number }> = [];
  for (const p of pts) {
    if (cur.length && p.t - cur[cur.length - 1].t > GAP_MS) {
      segs.push(cur);
      cur = [];
    }
    cur.push(p);
  }
  segs.push(cur);

  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  const col = def.color || cyanCol;
  for (const s of segs) {
    if (s.length < 2) continue;
    ctx.beginPath();
    ctx.moveTo(X(s[0].t), h);
    s.forEach((p) => ctx.lineTo(X(p.t), Y(p.v)));
    ctx.lineTo(X(s[s.length - 1].t), h);
    ctx.closePath();
    ctx.fillStyle = col + "1a";
    ctx.fill();

    ctx.beginPath();
    s.forEach((p, i) => (i ? ctx.lineTo(X(p.t), Y(p.v)) : ctx.moveTo(X(p.t), Y(p.v))));
    ctx.strokeStyle = col;
    ctx.lineWidth = 2;
    ctx.stroke();
  }

  const dot = (p: { t: number; v: number }) => {
    ctx.beginPath();
    ctx.arc(X(p.t), Y(p.v), 5, 0, Math.PI * 2);
    ctx.fillStyle = surfaceCol;
    ctx.fill();
    ctx.beginPath();
    ctx.arc(X(p.t), Y(p.v), 4, 0, Math.PI * 2);
    ctx.fillStyle = col;
    ctx.fill();
  };

  const last = pts[pts.length - 1];
  if (now - last.t <= GAP_MS) dot(last);

  if (st.hover != null && tipEl && tileEl) {
    const tt = now - (1 - st.hover / w) * WINDOW_MS;
    const p = pts.reduce((a, b) =>
      Math.abs(b.t - tt) < Math.abs(a.t - tt) ? b : a
    );
    ctx.strokeStyle = mutedCol;
    ctx.beginPath();
    ctx.moveTo(Math.round(X(p.t)) + 0.5, 0);
    ctx.lineTo(Math.round(X(p.t)) + 0.5, h);
    ctx.stroke();
    dot(p);
    const ago = Math.round((now - p.t) / 1000);
    tipEl.textContent = `${def.fmt(p.v)} · ${ago <= 1 ? "maintenant" : `il y a ${ago} s`}`;
    tipEl.hidden = false;
    const left = Math.min(
      Math.max(X(p.t) + 10 - tipEl.offsetWidth / 2, 0),
      tileEl.clientWidth - tipEl.offsetWidth
    );
    tipEl.style.left = `${left}px`;
  }
}

interface TraineeHeaderBarProps {
  identity: string;
  displayName?: string;
  role?: string;
  mic?: MicInfo;
  quality?: ConnectionQuality;
  posture?: PostureDisplay;
  defaultSyncText: string;
  showAvatarButton?: boolean;
  avatarOpen?: boolean;
  onToggleAvatar?: () => void;
  netListenersRef: React.MutableRefObject<
    Map<string, Set<(s: NetSample) => void>>
  >;
  statusListenersRef?: React.MutableRefObject<
    Map<string, Set<(txt: string) => void>>
  >;
}

function formatRoleBadge(role?: string): string {
  if (!role) return "";
  const r = role.trim().toLowerCase();
  if (["coach", "org_owner", "org_admin", "platform_admin", "owner", "admin"].includes(r)) {
    return "coach";
  }
  if (["athlete", "trainee"].includes(r)) {
    return "athlète";
  }
  return role;
}

export function TraineeHeaderBar({
  identity,
  displayName,
  role,
  mic,
  quality,
  posture,
  defaultSyncText,
  showAvatarButton,
  avatarOpen,
  onToggleAvatar,
  netListenersRef,
  statusListenersRef,
}: TraineeHeaderBarProps) {
  const [netOpen, setNetOpen] = useState(() => {
    try {
      return localStorage.getItem("trNet") !== "0";
    } catch {
      return true;
    }
  });
  const [syncText, setSyncText] = useState(defaultSyncText);
  const [vals, setVals] = useState<
    Record<string, { text: string; sub: string; bad: boolean }>
  >({});

  const sparkStateRef = useRef<Record<string, SparkTileState>>(
    Object.fromEntries(NET_DEFS.map((d) => [d.id, { data: [], hover: null }]))
  );
  const canvasRefs = useRef<Record<string, HTMLCanvasElement | null>>({});
  const tipRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const tileRefs = useRef<Record<string, HTMLDivElement | null>>({});

  useEffect(() => {
    const onNet = (sample: NetSample) => {
      const now = Date.now();
      const nextVals: Record<string, { text: string; sub: string; bad: boolean }> =
        {};
      let changed = false;

      for (const def of NET_DEFS) {
        const v = sample[def.id];
        const st = sparkStateRef.current[def.id];
        const sub =
          def.id === "poseHz"
            ? sample.poseSub || ""
            : def.id === "poseAge"
              ? sample.poseAgeSub || ""
              : "";

        if (v != null && Number.isFinite(v)) {
          st.data.push({ t: now, v });
          while (st.data.length && now - st.data[0].t > KEEP_MS) st.data.shift();
          nextVals[def.id] = {
            text: def.fmt(v),
            sub,
            bad: Boolean(def.warn && def.warn(v)),
          };
          changed = true;
        }
        drawSpark(
          canvasRefs.current[def.id],
          def,
          st,
          tipRefs.current[def.id],
          tileRefs.current[def.id]
        );
      }

      if (changed) {
        setVals((prev) => ({ ...prev, ...nextVals }));
      }
    };

    let set = netListenersRef.current.get(identity);
    if (!set) {
      set = new Set();
      netListenersRef.current.set(identity, set);
    }
    set.add(onNet);

    const timer = window.setInterval(() => {
      if (!netOpen) return;
      for (const def of NET_DEFS) {
        drawSpark(
          canvasRefs.current[def.id],
          def,
          sparkStateRef.current[def.id],
          tipRefs.current[def.id],
          tileRefs.current[def.id]
        );
      }
    }, 250);

    return () => {
      set?.delete(onNet);
      window.clearInterval(timer);
    };
  }, [identity, netListenersRef, netOpen]);

  useEffect(() => {
    if (!statusListenersRef) return;
    let set = statusListenersRef.current.get(identity);
    if (!set) {
      set = new Set();
      statusListenersRef.current.set(identity, set);
    }
    const cb = (txt: string) => setSyncText(txt);
    set.add(cb);
    return () => {
      set?.delete(cb);
    };
  }, [identity, statusListenersRef]);

  const micState = mic?.state || "none";
  const meterPct = Math.round(
    Math.min(1, Math.sqrt(Math.max(0, mic?.level || 0))) * 100
  );
  const qInfo = qualityInfo(quality);

  return (
    <section className="tr">
      <div className="tr-head">
        <b className="tr-name">{displayName || identity.split(":")[0]}</b>
        <span className="tr-role">
          {role ? `· ${formatRoleBadge(role)}` : ""}
        </span>

        <span
          className={`tr-chip tr-mic${mic?.speaking && micState === "live" ? " speaking" : ""}`}
          data-state={micState}
          title="État du micro"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <rect x="9" y="3" width="6" height="11" rx="3" />
            <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
            {micState !== "live" && <path d="M4 4l16 16" />}
          </svg>
          <span className="tr-mic-label">{MIC_LABELS[micState]}</span>
          <span className="tr-meter">
            <i style={{ width: `${meterPct}%` }} />
          </span>
        </span>

        <span
          className="tr-chip tr-q"
          data-q={qInfo.key}
          title="Qualité de connexion LiveKit de ce participant"
        >
          <svg viewBox="0 0 24 24" fill="currentColor">
            <rect
              x="3"
              y="14"
              width="4"
              height="7"
              rx="1"
              opacity={qInfo.bars >= 1 ? 1 : 0.28}
            />
            <rect
              x="10"
              y="9"
              width="4"
              height="12"
              rx="1"
              opacity={qInfo.bars >= 2 ? 1 : 0.28}
            />
            <rect
              x="17"
              y="3"
              width="4"
              height="18"
              rx="1"
              opacity={qInfo.bars >= 3 ? 1 : 0.28}
            />
          </svg>
          <span>Réseau : {qInfo.label}</span>
        </span>

        <span
          className="tr-chip tr-kpi tr-post"
          title={
            posture
              ? `Posture détectée par le mobile · ${posture.count} points`
              : "Posture détectée par le mobile"
          }
        >
          <small>Posture</small>
          <b>{posture ? posture.label || "inconnue" : "—"}</b>
        </span>

        <span
          className="tr-chip tr-kpi tr-conf"
          title="Confiance de la détection de posture (envoyée par le mobile)"
        >
          <small>Confiance</small>
          <b>
            {posture
              ? posture.confidence == null
                ? "N/A"
                : `${Math.round(posture.confidence * 100)} %`
              : "—"}
          </b>
        </span>

        <span className="tr-spacer" />

        <button
          className="secondary tr-toggle"
          type="button"
          aria-expanded={netOpen}
          onClick={() => {
            const next = !netOpen;
            setNetOpen(next);
            try {
              localStorage.setItem("trNet", next ? "1" : "0");
            } catch {
              // ignore
            }
          }}
        >
          {netOpen ? "Réseau ▾" : "Réseau ▸"}
        </button>

        {showAvatarButton && onToggleAvatar && (
          <button
            className="secondary tr-toggle"
            type="button"
            aria-expanded={Boolean(avatarOpen)}
            onClick={onToggleAvatar}
          >
            {avatarOpen ? "Avatar ▾" : "Avatar ▸"}
          </button>
        )}
      </div>

      <div className="tr-netbox" hidden={!netOpen}>
        <p className="tr-sect">
          Réseau{" "}
          <small>60 dernières secondes · survole une courbe pour la valeur</small>
        </p>
        <div className="tr-grid tr-netgrid">
          {NET_DEFS.map((def) => {
            const v = vals[def.id];
            return (
              <div
                key={def.id}
                ref={(el) => {
                  tileRefs.current[def.id] = el;
                }}
                className="tr-tile"
                role="group"
              >
                <div className="t-label">
                  <span>{def.label}</span>
                  <span
                    className="t-warn"
                    hidden={!v?.bad}
                    title={v?.bad ? def.warnText : ""}
                  >
                    ⚠
                  </span>
                </div>
                <div className="t-val">{v?.text || "–"}</div>
                <div className="t-sub">{v?.sub || ""}</div>
                <canvas
                  ref={(el) => {
                    canvasRefs.current[def.id] = el;
                  }}
                  role="img"
                  aria-label={`${def.label} : courbe des 60 dernières secondes`}
                  onPointerMove={(e) => {
                    const cv = canvasRefs.current[def.id];
                    if (!cv) return;
                    sparkStateRef.current[def.id].hover =
                      e.clientX - cv.getBoundingClientRect().left;
                    drawSpark(
                      cv,
                      def,
                      sparkStateRef.current[def.id],
                      tipRefs.current[def.id],
                      tileRefs.current[def.id]
                    );
                  }}
                  onPointerLeave={() => {
                    sparkStateRef.current[def.id].hover = null;
                    const tip = tipRefs.current[def.id];
                    if (tip) tip.hidden = true;
                    drawSpark(
                      canvasRefs.current[def.id],
                      def,
                      sparkStateRef.current[def.id],
                      tip,
                      tileRefs.current[def.id]
                    );
                  }}
                />
                <div
                  ref={(el) => {
                    tipRefs.current[def.id] = el;
                  }}
                  className="tr-tip"
                  hidden
                />
              </div>
            );
          })}
        </div>
      </div>

      <details className="tr-details">
        <summary>Détails de synchronisation</summary>
        <p className="video-posture">{syncText}</p>
      </details>
    </section>
  );
}
