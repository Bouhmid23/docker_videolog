import { useEffect, useRef, useState } from "react";
import {
  COACH_IA_ERRORS,
  COACH_IA_PHASE_LABELS,
  COACH_IA_VIEWS,
  degLabel,
} from "../codecs/coachIaParser";
import type { CoachIaMessage } from "../types";

const ALERT_MS = 3000;
const SILENT_MS = 3000;
const MAX_REPS = 6;
const SPEAK_GAP_MS = 2000;
const SPEAK_KEY = "coachIaSpeak";

function browserSpeak(phrase: string) {
  try {
    const synth = window.speechSynthesis;
    if (!synth) return;
    synth.cancel();
    const u = new SpeechSynthesisUtterance(phrase);
    u.lang = "fr-FR";
    synth.speak(u);
  } catch {
    // synthèse vocale indisponible
  }
}

interface RepItem {
  key: string;
  cls: "good" | "bad" | "part";
  text: string;
}

interface CoachIaPanelProps {
  identity: string;
  iaListenersRef: React.MutableRefObject<
    Map<string, Set<(m: CoachIaMessage) => void>>
  >;
}

export function CoachIaPanel({ identity, iaListenersRef }: CoachIaPanelProps) {
  const [live, setLive] = useState(false);
  const [isAlert, setIsAlert] = useState(false);
  const [statusText, setStatusText] = useState("En attente de la Jetson…");
  const [repsCount, setRepsCount] = useState(0);
  const [metricsText, setMetricsText] = useState("Genou — · Hanche — · Buste —");
  const [viewText, setViewText] = useState("");
  const [repsList, setRepsList] = useState<RepItem[]>([]);
  const [speak, setSpeak] = useState(() => {
    try {
      return localStorage.getItem(SPEAK_KEY) === "1";
    } catch {
      return false;
    }
  });

  const speakRef = useRef(speak);
  speakRef.current = speak;
  const lastStateAtRef = useRef(0);
  const phaseRef = useRef<string | null>(null);
  const alertTimerRef = useRef(0);
  const isAlertRef = useRef(false);
  const lastSpokenRef = useRef<{ text: string; at: number }>({ text: "", at: 0 });
  const repSeqRef = useRef(0);

  useEffect(() => {
    const updateIdleStatus = (isLive: boolean) => {
      if (isAlertRef.current) return;
      setStatusText(
        isLive
          ? (phaseRef.current && COACH_IA_PHASE_LABELS[phaseRef.current]) ||
              "Analyse en cours"
          : "En attente de la Jetson…"
      );
    };

    const onMessage = (m: CoachIaMessage) => {
      if (m.trainee !== identity) return;

      if (m.kind === "state") {
        lastStateAtRef.current = performance.now();
        setLive(true);
        if (m.reps !== null) setRepsCount(m.reps);
        phaseRef.current = m.phase;
        setMetricsText(
          `Genou ${degLabel(m.knee)} · Hanche ${degLabel(m.hip)} · Buste ${degLabel(m.lean)}`
        );
        setViewText(m.view ? `Vue ${COACH_IA_VIEWS[m.view]}` : "");
        updateIdleStatus(true);
      } else if (m.kind === "alert") {
        isAlertRef.current = true;
        setIsAlert(true);
        setStatusText(m.text);
        window.clearTimeout(alertTimerRef.current);
        alertTimerRef.current = window.setTimeout(() => {
          isAlertRef.current = false;
          setIsAlert(false);
          const curLive =
            Boolean(lastStateAtRef.current) &&
            performance.now() - lastStateAtRef.current < SILENT_MS;
          updateIdleStatus(curLive);
        }, ALERT_MS);

        const now = performance.now();
        if (
          speakRef.current &&
          !(
            m.text === lastSpokenRef.current.text &&
            now - lastSpokenRef.current.at < SPEAK_GAP_MS
          )
        ) {
          lastSpokenRef.current = { text: m.text, at: now };
          browserSpeak(m.text);
        }
      } else if (m.kind === "phase") {
        phaseRef.current = m.phase;
        if (m.phase === "exercise") {
          setRepsCount(0);
          setRepsList([]);
        }
        const curLive =
          Boolean(lastStateAtRef.current) &&
          performance.now() - lastStateAtRef.current < SILENT_MS;
        updateIdleStatus(curLive);
      } else if (m.kind === "rep") {
        if (m.n !== null) setRepsCount(m.n);
        const cls: RepItem["cls"] = m.errors.length
          ? "bad"
          : m.complete
            ? "good"
            : "part";
        const problems = m.errors.map((e) => COACH_IA_ERRORS[e] || e).join(", ");
        const text =
          (m.n !== null ? `${m.n}. ` : "") +
          `genou ${degLabel(m.kneeMin)} · buste max ${degLabel(m.leanMax)}` +
          (m.complete ? "" : " · partielle (non comptée)") +
          (problems ? ` · ⚠ ${problems}` : m.complete ? " · ✓" : "");
        repSeqRef.current += 1;
        setRepsList((prev) => [
          { key: `${repSeqRef.current}`, cls, text },
          ...prev.slice(0, MAX_REPS - 1),
        ]);
      }
    };

    let set = iaListenersRef.current.get(identity);
    if (!set) {
      set = new Set();
      iaListenersRef.current.set(identity, set);
    }
    set.add(onMessage);

    const silenceTimer = window.setInterval(() => {
      const isLive =
        Boolean(lastStateAtRef.current) &&
        performance.now() - lastStateAtRef.current < SILENT_MS;
      setLive((prev) => {
        if (prev !== isLive) updateIdleStatus(isLive);
        return isLive;
      });
    }, 1000);

    return () => {
      set?.delete(onMessage);
      window.clearInterval(silenceTimer);
      window.clearTimeout(alertTimerRef.current);
    };
  }, [iaListenersRef, identity]);

  return (
    <div
      className={`coach-ia${isAlert ? " is-alert" : ""}`}
      data-live={live ? "1" : "0"}
    >
      <span className="panel-title">Coach IA · squat</span>
      <div className="ia-status">{statusText}</div>
      <div className="ia-count">
        <strong>{repsCount}</strong>
        <small>répétitions</small>
      </div>
      <div className="ia-metrics">{metricsText}</div>
      <div className="ia-view">{viewText}</div>
      <ol className="ia-reps">
        {repsList.map((r) => (
          <li key={r.key} className={r.cls}>
            {r.text}
          </li>
        ))}
      </ol>
      <label className="ia-speak">
        <input
          type="checkbox"
          checked={speak}
          onChange={(e) => {
            const next = e.target.checked;
            setSpeak(next);
            try {
              localStorage.setItem(SPEAK_KEY, next ? "1" : "0");
            } catch {
              // ignore
            }
          }}
        />{" "}
        Parler
      </label>
    </div>
  );
}
