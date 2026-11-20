import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiFetch } from "../../../lib/api";
import type { SessionEndedSnapshot } from "../codecs/sessionSummary";
import { formatDurationHuman } from "../codecs/workoutUtils";

interface RoomEndedSummaryProps {
  summary: SessionEndedSnapshot;
  isCoach: boolean;
}

export function RoomEndedSummary({ summary, isCoach }: RoomEndedSummaryProps) {
  const navigate = useNavigate();
  const [finishing, setFinishing] = useState(false);
  const [finishedServer, setFinishedServer] = useState(false);
  const [finishError, setFinishError] = useState<string | null>(null);

  const workoutStatusText = summary.workout
    ? summary.workout.completed
      ? `Terminé (${summary.workout.totalSteps}/${summary.workout.totalSteps} étapes)`
      : summary.workout.started
        ? `Étape ${summary.workout.currentStepIdx + 1}/${summary.workout.totalSteps}`
        : "Non démarré"
    : "Session libre";

  const handleCloseServerSession = async () => {
    if (!summary.orgId || finishing || finishedServer) return;
    setFinishing(true);
    setFinishError(null);
    try {
      await apiFetch(
        `/v1/orgs/${encodeURIComponent(summary.orgId)}/sessions/${encodeURIComponent(
          summary.roomName
        )}/finish`,
        { method: "POST" }
      );
      setFinishedServer(true);
    } catch (err) {
      setFinishError(
        err instanceof Error ? err.message : "Impossible de clôturer la session."
      );
    } finally {
      setFinishing(false);
    }
  };

  return (
    <section id="roomEndedSummary" className="ended-summary-wrap">
      <div className="lobby-card ended-summary-card">
        <div className="ended-header">
          <span className="ended-badge">Séance terminée</span>
          <h2>{summary.title}</h2>
          <p className="lobby-meta">
            {[summary.orgName, summary.sport, summary.roomName]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>

        <div className="ended-kpis">
          <div className="ended-kpi">
            <span className="ended-kpi-label">Durée</span>
            <strong className="ended-kpi-val">
              {formatDurationHuman(summary.durationS)}
            </strong>
          </div>
          <div className="ended-kpi">
            <span className="ended-kpi-label">Participants</span>
            <strong className="ended-kpi-val">{summary.participants.length}</strong>
          </div>
          <div className="ended-kpi">
            <span className="ended-kpi-label">Programme</span>
            <strong className="ended-kpi-val ended-kpi-sm">
              {workoutStatusText}
            </strong>
          </div>
          <div className="ended-kpi">
            <span className="ended-kpi-label">Enregistrement &amp; Repères</span>
            <strong className="ended-kpi-val ended-kpi-sm">
              {summary.hadRecording ? "Vidéo capturée" : "Sans vidéo"}
              {summary.markersCount > 0 ? ` · ${summary.markersCount} repère(s)` : ""}
            </strong>
          </div>
        </div>

        {summary.participants.length > 0 && (
          <div className="lobby-section">
            <div className="lobby-label">Participants présents</div>
            <ul className="lobby-participants">
              {summary.participants.map((p) => (
                <li key={p.identity}>
                  <span>
                    {p.name}
                    <small className="ended-role-tag"> ({p.role || "participant"})</small>
                  </span>
                  <span className="p-state on">Présent</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {summary.telemetryRows.length > 0 && (
          <div className="lobby-section">
            <div className="lobby-label">Récapitulatif télémétrie</div>
            <div className="ended-table-wrap">
              <table className="ended-table">
                <thead>
                  <tr>
                    <th>Athlète</th>
                    <th>FC moy / max</th>
                    <th>Vitesse max</th>
                    <th>Puissance moy</th>
                    <th>Paquets</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.telemetryRows.map((row) => (
                    <tr key={row.identity}>
                      <td>{row.name}</td>
                      <td>
                        {row.hrAvg !== null ? `${row.hrAvg} / ${row.hrMax} bpm` : "—"}
                      </td>
                      <td>{row.speedMax !== null ? `${row.speedMax} km/h` : "—"}</td>
                      <td>{row.powerAvg !== null ? `${row.powerAvg} W` : "—"}</td>
                      <td>{row.samples}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {finishError && <div className="ended-error">{finishError}</div>}

        <div className="ended-actions">
          <button
            id="endedHomeBtn"
            className="wb-start-btn"
            type="button"
            onClick={() => navigate("/app")}
          >
            Retour à l'accueil
          </button>

          {summary.orgId && (
            <button
              className="secondary"
              type="button"
              onClick={() =>
                navigate(`/app/sessions/${encodeURIComponent(summary.roomName)}`)
              }
            >
              Voir la fiche séance
            </button>
          )}

          {isCoach && summary.orgId && (
            <button
              className="secondary"
              type="button"
              disabled={finishing || finishedServer}
              onClick={() => void handleCloseServerSession()}
            >
              {finishedServer
                ? "Séance clôturée"
                : finishing
                  ? "Clôture..."
                  : "Clôturer définitivement"}
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
