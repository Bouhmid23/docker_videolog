import { LogLevel, Room, RoomEvent, setLogExtension, setLogLevel } from "livekit-client";

const CONN_KEY = "connLog";
const CONN_SDK_NOISE = /^room event (dataReceived|activeSpeakersChanged)$/;

const redactConn = (s: string) =>
  String(s)
    .replace(/(access_token|token)=[^&\s'"]+/gi, "$1=***")
    .replace(/eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]+/g, "***");

interface ConnEventEntry {
  t: string;
  name: string;
  [key: string]: unknown;
}

const getMaxEvents = () => {
  const lk = new URLSearchParams(window.location.search).get("lk");
  return lk ? 2000 : 300;
};

let connEvents: ConnEventEntry[] = (() => {
  try {
    const raw = JSON.parse(localStorage.getItem(CONN_KEY) || "[]");
    return Array.isArray(raw) ? raw.slice(-getMaxEvents()) : [];
  } catch {
    return [];
  }
})();

let saveTimer = 0;

function saveConnLog() {
  window.clearTimeout(saveTimer);
  saveTimer = 0;
  try {
    localStorage.setItem(CONN_KEY, JSON.stringify(connEvents));
  } catch {
    // localStorage indisponible
  }
}

export function connPush(
  name: string,
  detail: Record<string, unknown> = {}
): ConnEventEntry {
  const clean: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(detail)) {
    clean[k] = typeof v === "string" ? redactConn(v).slice(0, 160) : v;
  }
  const entry: ConnEventEntry = {
    t: new Date().toISOString(),
    name: redactConn(name).slice(0, 200),
    ...clean,
  };
  connEvents.push(entry);
  const max = getMaxEvents();
  if (connEvents.length > max) connEvents = connEvents.slice(-max);
  if (!saveTimer) saveTimer = window.setTimeout(saveConnLog, 500);
  return entry;
}

export function connLog(name: string, detail: Record<string, unknown> = {}): void {
  const e = connPush(name, detail);
  console.info("[salon-conn]", e.t, e.name, detail);
}

export function connSnap(r: Room | null, videoCount: number | null = null) {
  return {
    etat: r?.state,
    sid: r?.localParticipant?.sid,
    enLigne: navigator.onLine,
    onglet: document.visibilityState,
    cartes: videoCount,
  };
}

export function attachConnLog(r: Room, getVideoCount: () => number): void {
  const on = <A extends unknown[]>(
    ev: RoomEvent,
    name: string,
    pick?: (...args: A) => Record<string, unknown> | null
  ) => {
    r.on(ev as never, ((...args: A) => {
      const extra = pick ? pick(...args) : {};
      if (extra) connLog(name, { ...connSnap(r, getVideoCount()), ...extra });
    }) as never);
  };

  on(RoomEvent.ConnectionStateChanged, "état de connexion", (s) => ({ nouveau: s }));
  on(RoomEvent.SignalReconnecting, "signal : reconnexion");
  on(RoomEvent.Reconnecting, "reconnexion en cours");
  on(RoomEvent.Reconnected, "reconnecté");
  on(RoomEvent.Disconnected, "déconnecté", (reason) => ({ raison: reason }));
  on(RoomEvent.ConnectionQualityChanged, "qualité de ma connexion", (q, p) =>
    p === r.localParticipant ? { qualite: q } : null
  );
  on(RoomEvent.ParticipantConnected, "participant arrivé", (p: { identity?: string }) => ({
    id: p.identity,
  }));
  on(RoomEvent.ParticipantDisconnected, "participant parti", (p: { identity?: string }) => ({
    id: p.identity,
  }));
  on(
    RoomEvent.TrackSubscribed,
    "flux reçu",
    (track: { kind?: string }, _pub: unknown, p: { identity?: string }) => ({
      id: p.identity,
      type: track.kind,
    })
  );
  on(
    RoomEvent.TrackUnsubscribed,
    "flux perdu",
    (track: { kind?: string }, _pub: unknown, p?: { identity?: string }) => ({
      id: p?.identity,
      type: track.kind,
    })
  );
}

let sdkHooked = false;
export function initConnLogHooks(): () => void {
  if (!sdkHooked) {
    sdkHooked = true;
    const levelParam = new URLSearchParams(window.location.search).get("lk");
    if (levelParam) {
      const lvlMap: Record<string, LogLevel> = {
        trace: LogLevel.trace,
        debug: LogLevel.debug,
        info: LogLevel.info,
        warn: LogLevel.warn,
        error: LogLevel.error,
        silent: LogLevel.silent,
      };
      if (lvlMap[levelParam] != null) {
        try {
          setLogLevel(lvlMap[levelParam]);
        } catch {
          // ignore
        }
      }
    }
    setLogExtension((lvl, msg, ctx) => {
      if (!((lvl >= LogLevel.info || !!levelParam) && !CONN_SDK_NOISE.test(String(msg)))) {
        return;
      }
      const d: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(ctx || {})) {
        if (["string", "number", "boolean"].includes(typeof v)) d[k] = v;
      }
      connPush("sdk : " + msg, d);
    });
    (window as unknown as { salonConnDump?: () => string }).salonConnDump = () =>
      connEvents
        .map(
          ({ t, name, ...rest }) =>
            `${t} ${name}${Object.keys(rest).length ? " " + JSON.stringify(rest) : ""}`
        )
        .join("\n");
  }

  const onOnline = () => connLog("navigateur : réseau revenu");
  const onOffline = () => connLog("navigateur : réseau perdu");
  const onVis = () => connLog("onglet " + (document.hidden ? "masqué" : "visible"));
  const onPageHide = () => saveConnLog();

  window.addEventListener("online", onOnline);
  window.addEventListener("offline", onOffline);
  document.addEventListener("visibilitychange", onVis);
  window.addEventListener("pagehide", onPageHide);

  return () => {
    window.removeEventListener("online", onOnline);
    window.removeEventListener("offline", onOffline);
    document.removeEventListener("visibilitychange", onVis);
    window.removeEventListener("pagehide", onPageHide);
    saveConnLog();
  };
}
