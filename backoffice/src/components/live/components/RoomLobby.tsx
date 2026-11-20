import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { auth } from "../../../lib/firebase";
import type { ActiveRoomSummary, LobbyInfo } from "../types";

interface RoomLobbyProps {
  roomName: string;
  joining: boolean;
  onJoin: (roomName: string, useCamera: boolean) => void;
  onLobbyLoaded: (info: LobbyInfo | null) => void;
  onLobbyError: (message: string) => void;
}

export function RoomLobby({
  roomName,
  joining,
  onJoin,
  onLobbyLoaded,
  onLobbyError,
}: RoomLobbyProps) {
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [roomsList, setRoomsList] = useState<ActiveRoomSummary[]>([]);
  const [lobbyInfo, setLobbyInfo] = useState<LobbyInfo | null>(null);

  const onLobbyLoadedRef = useRef(onLobbyLoaded);
  onLobbyLoadedRef.current = onLobbyLoaded;
  const onLobbyErrorRef = useRef(onLobbyError);
  onLobbyErrorRef.current = onLobbyError;
  const [useCam, setUseCam] = useState(() => {
    try {
      return localStorage.getItem("useCam") === "1";
    } catch {
      return false;
    }
  });
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewActive, setPreviewActive] = useState(false);

  const previewVideoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const stopPreview = () => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (previewVideoRef.current) {
      previewVideoRef.current.srcObject = null;
    }
    setPreviewActive(false);
  };

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setLoadError(null);
      try {
        const user = auth.currentUser;
        if (!user) {
          throw new Error(
            "Non connecté. Connectez-vous d'abord sur le back-office (/login)."
          );
        }
        const idToken = await user.getIdToken();

        if (!roomName) {
          const res = await fetch("/api/mobile/rooms?limit=20", {
            headers: { Authorization: `Bearer ${idToken}` },
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const data = (await res.json()) as { rooms?: ActiveRoomSummary[] };
          if (!cancelled) {
            setLobbyInfo(null);
            setRoomsList(data.rooms || []);
            onLobbyLoadedRef.current(null);
          }
          return;
        }

        const res = await fetch(
          `/api/rooms/${encodeURIComponent(roomName)}/lobby`,
          { headers: { Authorization: `Bearer ${idToken}` } }
        );
        if (!res.ok) {
          const errData = (await res.json().catch(() => ({}))) as {
            detail?: string;
          };
          throw new Error(
            errData.detail || `Session inaccessible (HTTP ${res.status})`
          );
        }
        const info = (await res.json()) as LobbyInfo;
        if (!cancelled) {
          setLobbyInfo(info);
          onLobbyLoadedRef.current(info);
          if (!info.accessible) {
            onLobbyErrorRef.current(
              info.reason || "Cette session n'est plus accessible."
            );
          }
        }
      } catch (e) {
        if (!cancelled) {
          const msg =
            e instanceof Error ? e.message : "Impossible de charger la séance.";
          setLoadError(msg);
          onLobbyErrorRef.current(msg);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [roomName]);

  useEffect(() => {
    if (!roomName || !lobbyInfo?.accessible || !useCam) {
      stopPreview();
      return;
    }
    let cancelled = false;
    setPreviewError(null);

    navigator.mediaDevices
      .getUserMedia({ video: { width: { ideal: 640 }, height: { ideal: 480 } }, audio: false })
      .then((stream) => {
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = stream;
        if (previewVideoRef.current) {
          previewVideoRef.current.srcObject = stream;
        }
        setPreviewActive(true);
      })
      .catch((e) => {
        if (!cancelled) {
          stopPreview();
          setPreviewError(
            "Caméra indisponible : " + (e instanceof Error ? e.message : String(e))
          );
        }
      });

    return () => {
      cancelled = true;
      stopPreview();
    };
  }, [lobbyInfo?.accessible, roomName, useCam]);

  const handleToggleCam = (checked: boolean) => {
    setUseCam(checked);
    try {
      localStorage.setItem("useCam", checked ? "1" : "0");
    } catch {
      // ignore
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!roomName || !lobbyInfo?.accessible || joining) return;
    stopPreview();
    onJoin(roomName, useCam);
  };

  const durMin = Math.round((lobbyInfo?.duration_s || 3600) / 60);
  let scheduleText = `Durée prévue : ${durMin} min`;
  if (lobbyInfo?.scheduled_at) {
    const dt = new Date(lobbyInfo.scheduled_at);
    const timeStr = dt.toLocaleTimeString("fr-FR", {
      hour: "2-digit",
      minute: "2-digit",
    });
    const dateStr = dt.toLocaleDateString("fr-FR", {
      day: "numeric",
      month: "short",
    });
    scheduleText = `Prévue le ${dateStr} à ${timeStr} · Durée : ${durMin} min`;
  }

  const statusLabel = !lobbyInfo
    ? "En attente"
    : lobbyInfo.finished
      ? "Terminée"
      : !lobbyInfo.accessible
        ? "Expirée"
        : (lobbyInfo.active_count || 0) > 0
          ? `En cours (${lobbyInfo.active_count} connecté${(lobbyInfo.active_count || 0) > 1 ? "s" : ""})`
          : "En attente";

  return (
    <form id="login" onSubmit={handleSubmit}>
      {loading && <div className="lobby-meta">Chargement de la séance…</div>}

      {!loading && loadError && (
        <div>
          <p className="err">{loadError}</p>
          <Link className="lobby-back" to="/app/sessions">
            Retour aux sessions
          </Link>
        </div>
      )}

      {!loading && roomName && lobbyInfo && (
        <div id="lobbySession">
          <div className="lobby-header">
            <span className="lobby-badge">{lobbyInfo.org_name || "Videolog"}</span>
            <span className="lobby-badge lobby-badge-status">{statusLabel}</span>
          </div>
          <h1>{lobbyInfo.title || roomName}</h1>
          <p className="lobby-meta">{scheduleText}</p>

          <div className="lobby-section">
            <div className="lobby-label">Participants invités</div>
            <ul className="lobby-participants">
              {!lobbyInfo.invited_participants?.length ? (
                <li>Aucun participant listé</li>
              ) : (
                lobbyInfo.invited_participants.map((p, idx) => (
                  <li key={`${p.name}-${idx}`} className={p.connected ? "online" : undefined}>
                    <span className="p-dot" />
                    <span>
                      {p.name}
                      {p.is_owner ? " (Coach)" : ""}
                    </span>
                  </li>
                ))
              )}
            </ul>
          </div>

          <div className="lobby-preview-wrap">
            <video
              ref={previewVideoRef}
              id="lobbyPreview"
              autoPlay
              muted
              playsInline
              hidden={!previewActive}
            />
            {!previewActive && (
              <div className="lobby-preview-off">
                {previewError || "Caméra désactivée (mode spectateur)"}
              </div>
            )}
          </div>

          <label className="check">
            <input
              type="checkbox"
              id="useCam"
              checked={useCam}
              onChange={(e) => handleToggleCam(e.target.checked)}
            />{" "}
            Activer ma caméra à l&apos;entrée
          </label>
          <p className="hint">
            Sans caméra, vous entrez en spectateur : vous voyez et entendez les
            participants.
          </p>

          {lobbyInfo.accessible ? (
            <button id="join" type="submit" disabled={joining}>
              {joining ? "Connexion…" : "Rejoindre la séance"}
            </button>
          ) : (
            <Link
              className="lobby-back"
              to={`/app/sessions/${encodeURIComponent(roomName)}`}
            >
              Consulter le résumé / replay de la séance
            </Link>
          )}
        </div>
      )}

      {!loading && !roomName && (
        <div id="lobbyPicker">
          <h1>Choisir une séance</h1>
          <p className="lobby-meta">
            Sélectionnez une séance en cours ou planifiée pour la rejoindre.
          </p>
          <div className="lobby-room-list">
            {!roomsList.length ? (
              <p className="lobby-meta">Aucune séance active pour le moment.</p>
            ) : (
              roomsList.map((r) => (
                <Link
                  key={r.room_name}
                  className="lobby-room-card"
                  to={`/app/live/${encodeURIComponent(r.room_name)}`}
                >
                  <span>
                    <strong>{r.title || r.room_name}</strong>
                  </span>
                  <span className="lobby-meta">{r.count || 0} connecté(s)</span>
                </Link>
              ))
            )}
          </div>
          <Link className="lobby-back" to="/app/sessions">
            Retour aux sessions
          </Link>
        </div>
      )}
    </form>
  );
}
