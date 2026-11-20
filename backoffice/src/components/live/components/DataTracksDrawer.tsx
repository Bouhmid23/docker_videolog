import { useEffect, useMemo, useRef, useState } from "react";
import type { DataLogState } from "../hooks/useLiveKitRoom";
import type { RoomParticipantSummary } from "../types";

interface DataTracksDrawerProps {
  open: boolean;
  onToggleOpen: () => void;
  roomName: string;
  participants: RoomParticipantSummary[];
  dataLog: DataLogState;
  onClearLog: () => void;
}

const getStoredBool = (k: string, def: boolean) => {
  try {
    const v = localStorage.getItem(k);
    return v == null ? def : v === "1";
  } catch {
    return def;
  }
};

export function DataTracksDrawer({
  open,
  onToggleOpen,
  roomName,
  participants,
  dataLog,
  onClearLog,
}: DataTracksDrawerProps) {
  const [salonOpen, setSalonOpen] = useState(() =>
    getStoredBool("sd-data-salon", true)
  );
  const [tracksOpen, setTracksOpen] = useState(() =>
    getStoredBool("sd-data-datatracks", true)
  );
  const [filter, setFilter] = useState("");
  const [atBottom, setAtBottom] = useState(true);
  const logRef = useRef<HTMLPreElement | null>(null);

  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return q
      ? dataLog.lines.filter((l) => l.toLowerCase().includes(q))
      : dataLog.lines;
  }, [dataLog.lines, filter]);

  useEffect(() => {
    const el = logRef.current;
    if (!el || !atBottom) return;
    el.scrollTop = el.scrollHeight;
  }, [atBottom, shown]);

  const checkAtBottom = () => {
    const el = logRef.current;
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };

  const toggleSalon = () => {
    const next = !salonOpen;
    setSalonOpen(next);
    try {
      localStorage.setItem("sd-data-salon", next ? "1" : "0");
    } catch {
      // ignore
    }
  };

  const toggleTracks = () => {
    const next = !tracksOpen;
    setTracksOpen(next);
    try {
      localStorage.setItem("sd-data-datatracks", next ? "1" : "0");
    } catch {
      // ignore
    }
  };

  return (
    <aside
      id="sd-data"
      className={`sd-drawer sd-left${open ? " open" : ""}`}
      aria-label="Données"
    >
      <button
        className="sd-toggle"
        type="button"
        aria-expanded={open}
        aria-controls="sd-data-blocks"
        title="Afficher / masquer : Données"
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
          <path d="M9 5l7 7-7 7" />
        </svg>
        <span>DataTracks</span>
      </button>

      <div className="sd-head">Données</div>

      <div className="sd-blocks" id="sd-data-blocks">
        <section className={`sd-block${salonOpen ? "" : " collapsed"}`}>
          <button
            className="sd-block-head"
            type="button"
            aria-expanded={salonOpen}
            onClick={toggleSalon}
          >
            <span className="sd-block-title">Salon et participants</span>
            <span className="sd-badge">{participants.length || ""}</span>
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.4"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M6 9l6 6 6-6" />
            </svg>
          </button>
          <div className="sd-block-body">
            <p id="roomTitle">{roomName ? `Salon : ${roomName}` : ""}</p>
            <ul id="participants">
              {participants.map((p) => (
                <li key={p.identity}>
                  <span className={`dot${p.isSpeaking ? " speaking" : ""}`} />
                  <span>{p.name}</span>
                  {!p.hasVideoCard && <span className="you">{p.micLabel}</span>}
                  {p.isLocal && <span className="you">(vous)</span>}
                </li>
              ))}
            </ul>
          </div>
        </section>

        <section className={`sd-block${tracksOpen ? "" : " collapsed"}`}>
          <button
            className="sd-block-head"
            type="button"
            aria-expanded={tracksOpen}
            onClick={toggleTracks}
          >
            <span className="sd-block-title">Flux DataTracks</span>
            <span className="sd-badge">{dataLog.totalCount}</span>
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.4"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M6 9l6 6 6-6" />
            </svg>
          </button>
          <div className="sd-block-body">
            <div className="data-bar">
              <input
                type="search"
                id="dataFilter"
                placeholder="Rechercher (ex. marker, engo_message, posture, telemetry)…"
                autoComplete="off"
                spellCheck={false}
                value={filter}
                onChange={(e) => {
                  setFilter(e.target.value);
                  setAtBottom(true);
                }}
              />
              <button
                type="button"
                id="dataClear"
                className="secondary"
                title="Efface l'historique du journal"
                onClick={onClearLog}
              >
                Effacer
              </button>
            </div>
            <div id="dataFilterInfo">
              {filter.trim()
                ? `${shown.length} / ${dataLog.lines.length} message(s)`
                : ""}
            </div>
            <div className="data-wrap">
              <pre
                ref={logRef}
                id="dataLog"
                onScroll={() => setAtBottom(checkAtBottom())}
              >
                {shown.join("\n\n")}
              </pre>
              <button
                type="button"
                id="dataJump"
                className="secondary"
                hidden={atBottom}
                title="Revenir au dernier message"
                onClick={() => {
                  if (logRef.current) {
                    logRef.current.scrollTop = logRef.current.scrollHeight;
                  }
                  setAtBottom(true);
                }}
              >
                ↓ Dernier
              </button>
            </div>
          </div>
        </section>
      </div>
    </aside>
  );
}
