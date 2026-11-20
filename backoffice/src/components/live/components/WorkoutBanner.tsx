import { useEffect, useMemo, useRef, useState } from "react";
import type { Room } from "livekit-client";
import type { WorkoutProgressSummary } from "../codecs/sessionSummary";
import {
  flattenWorkoutSteps,
  formatStepTarget,
  formatTimerMMSS,
  totalWorkoutDurationS,
} from "../codecs/workoutUtils";
import type { LobbyWorkout } from "../types";

export interface WorkoutSyncMsg {
  idx: number;
  remaining_s: number;
  paused: boolean;
  started?: boolean;
  completed?: boolean;
}

interface WorkoutBannerProps {
  connected: boolean;
  canControl?: boolean;
  workout: LobbyWorkout | null;
  roomRef: React.MutableRefObject<Room | null>;
  externalSyncRef: React.MutableRefObject<((msg: WorkoutSyncMsg) => void) | null>;
  onProgressChange?: (summary: WorkoutProgressSummary | null) => void;
}

export function WorkoutBanner({
  connected,
  canControl = true,
  workout,
  roomRef,
  externalSyncRef,
  onProgressChange,
}: WorkoutBannerProps) {
  const flatSteps = useMemo(
    () => (workout ? flattenWorkoutSteps(workout.steps) : []),
    [workout]
  );
  const totalDurationS = useMemo(
    () => totalWorkoutDurationS(flatSteps),
    [flatSteps]
  );

  const [started, setStarted] = useState(false);
  const [completed, setCompleted] = useState(false);
  const [stepIdx, setStepIdx] = useState(0);
  const [remainingS, setRemainingS] = useState(0);
  const [paused, setPaused] = useState(false);

  const stateRef = useRef({ started, completed, stepIdx, remainingS, paused });
  stateRef.current = { started, completed, stepIdx, remainingS, paused };

  // À la connexion, le programme reste prêt mais NE démarre PAS automatiquement
  useEffect(() => {
    if (!connected || !flatSteps.length) {
      setStarted(false);
      setCompleted(false);
      setStepIdx(0);
      setRemainingS(0);
      setPaused(false);
      return;
    }
    setStarted(false);
    setCompleted(false);
    setStepIdx(0);
    setRemainingS(flatSteps[0].duration_s);
    setPaused(false);
  }, [connected, flatSteps]);

  useEffect(() => {
    if (!workout || !flatSteps.length) {
      onProgressChange?.(null);
      return;
    }
    onProgressChange?.({
      title: workout.title || "Séance structurée",
      started,
      completed,
      currentStepIdx: stepIdx,
      totalSteps: flatSteps.length,
      totalDurationS,
    });
  }, [
    completed,
    flatSteps.length,
    onProgressChange,
    started,
    stepIdx,
    totalDurationS,
    workout,
  ]);

  useEffect(() => {
    externalSyncRef.current = (msg) => {
      if (
        typeof msg.idx === "number" &&
        msg.idx >= 0 &&
        msg.idx < flatSteps.length
      ) {
        setStarted(msg.started !== undefined ? Boolean(msg.started) : true);
        setCompleted(Boolean(msg.completed));
        setStepIdx(msg.idx);
        setRemainingS(
          typeof msg.remaining_s === "number"
            ? msg.remaining_s
            : flatSteps[msg.idx].duration_s
        );
        setPaused(Boolean(msg.paused));
      }
    };
    return () => {
      externalSyncRef.current = null;
    };
  }, [externalSyncRef, flatSteps]);

  useEffect(() => {
    if (!connected || !flatSteps.length || !started || paused || completed) {
      return;
    }
    const id = window.setInterval(() => {
      setRemainingS((prev) => {
        if (prev > 1) return prev - 1;
        setStepIdx((curIdx) => {
          if (curIdx < flatSteps.length - 1) {
            const nxt = curIdx + 1;
            setRemainingS(flatSteps[nxt].duration_s);
            return nxt;
          }
          setCompleted(true);
          return curIdx;
        });
        return 0;
      });
    }, 1000);
    return () => window.clearInterval(id);
  }, [completed, connected, flatSteps, paused, started]);

  const broadcast = (
    idx: number,
    rem: number,
    isPaused: boolean,
    isStarted = true,
    isCompleted = false
  ) => {
    const local = roomRef.current?.localParticipant;
    if (!local) return;
    try {
      const payload = new TextEncoder().encode(
        JSON.stringify({
          idx,
          remaining_s: rem,
          paused: isPaused,
          started: isStarted,
          completed: isCompleted,
        })
      );
      void local.publishData(payload, { reliable: true, topic: "workout_step" });
    } catch {
      // ignore
    }
  };

  // Battement de synchronisation périodique (toutes les 5s côté coach) pour les retardataires/reconnexions
  useEffect(() => {
    if (!connected || !canControl || !started || completed || !flatSteps.length) {
      return;
    }
    const syncId = window.setInterval(() => {
      const s = stateRef.current;
      if (!s.started || s.completed) return;
      const local = roomRef.current?.localParticipant;
      if (!local) return;
      try {
        const payload = new TextEncoder().encode(
          JSON.stringify({
            idx: s.stepIdx,
            remaining_s: s.remainingS,
            paused: s.paused,
            started: s.started,
            completed: s.completed,
          })
        );
        void local.publishData(payload, { reliable: true, topic: "workout_step" });
      } catch {
        // ignore
      }
    }, 5000);
    return () => window.clearInterval(syncId);
  }, [canControl, completed, connected, flatSteps.length, roomRef, started]);

  if (!connected || !workout || !flatSteps.length) return null;

  const cur = flatSteps[stepIdx] ?? flatSteps[0];
  const nxt = flatSteps[stepIdx + 1];

  const handleStartWorkout = () => {
    const firstDur = flatSteps[0].duration_s;
    setStarted(true);
    setCompleted(false);
    setStepIdx(0);
    setRemainingS(firstDur);
    setPaused(false);
    broadcast(0, firstDur, false, true, false);
  };

  return (
    <section id="workoutBanner" className="workout-banner">
      <div className="wb-main">
        <div className="wb-meta">
          <span className="wb-title">{workout.title || "Séance structurée"}</span>
          <span className="wb-step-count">
            {started
              ? completed
                ? `Terminé · ${flatSteps.length} / ${flatSteps.length} étapes`
                : `Étape ${stepIdx + 1} / ${flatSteps.length}`
              : `${flatSteps.length} étape${flatSteps.length > 1 ? "s" : ""} · Prêt`}
          </span>
        </div>

        <div className="wb-current">
          <strong>
            {completed ? "Programme terminé" : cur.name}
          </strong>
          {!completed && (
            <span className="wb-target">
              {formatStepTarget(cur, workout.thresholds)}
            </span>
          )}
        </div>

        <div className="wb-next">
          {!started
            ? `Durée totale : ${formatTimerMMSS(totalDurationS)} — En attente du démarrage`
            : completed
              ? "Toutes les étapes du programme ont été complétées."
              : nxt
                ? `Suivante : ${nxt.name} (${Math.round(nxt.duration_s / 60)} min)`
                : "Dernière étape"}
        </div>
      </div>

      <div className="wb-right">
        <div className="wb-timer">
          {formatTimerMMSS(started ? remainingS : totalDurationS)}
        </div>

        {!started ? (
          canControl ? (
            <button
              id="startWorkoutBtn"
              className="wb-start-btn"
              type="button"
              onClick={handleStartWorkout}
            >
              Commencer
            </button>
          ) : (
            <span className="wb-waiting-pill">En attente du coach</span>
          )
        ) : (
          canControl && (
            <div className="wb-controls">
              {completed ? (
                <button
                  className="secondary"
                  type="button"
                  onClick={handleStartWorkout}
                >
                  Recommencer
                </button>
              ) : (
                <>
                  <button
                    className="secondary"
                    type="button"
                    onClick={() => {
                      const nextIdx = Math.max(0, stepIdx - 1);
                      const nextRem = flatSteps[nextIdx].duration_s;
                      setStepIdx(nextIdx);
                      setRemainingS(nextRem);
                      broadcast(nextIdx, nextRem, paused, true, false);
                    }}
                  >
                    Préc.
                  </button>
                  <button
                    className="secondary"
                    type="button"
                    onClick={() => {
                      const nextPaused = !paused;
                      setPaused(nextPaused);
                      broadcast(stepIdx, remainingS, nextPaused, true, false);
                    }}
                  >
                    {paused ? "Reprendre" : "Pause"}
                  </button>
                  <button
                    className="secondary"
                    type="button"
                    onClick={() => {
                      const nextIdx = Math.min(flatSteps.length - 1, stepIdx + 1);
                      const nextRem = flatSteps[nextIdx].duration_s;
                      setStepIdx(nextIdx);
                      setRemainingS(nextRem);
                      broadcast(nextIdx, nextRem, paused, true, false);
                    }}
                  >
                    Suiv.
                  </button>
                </>
              )}
            </div>
          )
        )}
      </div>
    </section>
  );
}
