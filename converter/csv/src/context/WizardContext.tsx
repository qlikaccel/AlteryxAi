// // src/context/WizardContext.tsx
// import { createContext, useContext, useRef, useState, useCallback } from "react";

// type WizardContextType = {
//   step: number;
//   setStep: (n: number) => void;
//   selectedApp: any;
//   setSelectedApp: (app: any) => void;

//   // Navigation timing
//   startTimer: (target?: string) => void;
//   stopTimer: (target?: string) => string;
//   // Returns formatted elapsed time for a given target pathname (e.g., '/apps')
//   getLastElapsed: (target?: string) => string | null;
//   lastElapsed: string | null;
// };

// const WizardContext = createContext<WizardContextType>(null!);

// export const WizardProvider = ({ children }: { children: React.ReactNode }) => {
//   const [step, setStep] = useState(1);
//   const [selectedApp, setSelectedApp] = useState<any>(null);

//   // Timer state using ref + stable callbacks to avoid identity changes
//   const timerStartRef = useRef<number | null>(null);
//   const [lastElapsed, setLastElapsed] = useState<string | null>(null);

//   const formatElapsed = (msTotal: number) => {
//     const minutes = Math.floor(msTotal / 60000);
//     const seconds = Math.floor((msTotal % 60000) / 1000);
//     // Show centiseconds as two digits to match `00m:00s:00ms`
//     const centis = Math.floor((msTotal % 1000) / 10);

//     const pad = (n: number, width = 2) => String(n).padStart(width, "0");

//     return `${pad(minutes)}m:${pad(seconds)}s:${pad(centis)}ms`;
//   };

//   const startTimer = useCallback((target?: string) => {
//     timerStartRef.current = Date.now();
//     setLastElapsed(null);
//     if (target) {
//       sessionStorage.setItem("lastTimerTarget", target);
//     }
//   }, []);

//   const stopTimer = useCallback((target?: string) => {
//     const ts = timerStartRef.current;
//     if (!ts) return "00m:00s:00ms";
//     const elapsed = Date.now() - ts;
//     const formatted = formatElapsed(elapsed);
//     setLastElapsed(formatted);
//     timerStartRef.current = null;

//     // Persist per-target value and keep generic key for compatibility
//     if (target) {
//       sessionStorage.setItem("lastTimerTarget", target);
//       sessionStorage.setItem(`lastElapsedMs_${target}`, String(elapsed));
//     }
//     sessionStorage.setItem("lastElapsedMs", String(elapsed));

//     return formatted;
//   }, []);

//   const getLastElapsed = useCallback((target?: string) => {
//     const key = target ? `lastElapsedMs_${target}` : "lastElapsedMs";
//     const raw = sessionStorage.getItem(key);
//     if (!raw) return null;
//     const ms = parseInt(raw, 10);
//     if (isNaN(ms)) return null;
//     return formatElapsed(ms);
//   }, []);


//   return (
//     <WizardContext.Provider
//       value={{
//         step,
//         setStep,
//         selectedApp,
//         setSelectedApp,
//         startTimer,
//         stopTimer,
//         getLastElapsed,
//         lastElapsed,
//       }}
//     >
//       {children}
//     </WizardContext.Provider>
//   );
// };

// export const useWizard = () => useContext(WizardContext);


// src/context/WizardContext.tsx
import { createContext, useContext, useRef, useState, useCallback, useEffect } from "react";
import { useLocation } from "react-router-dom";

type WizardContextType = {
  step: number;
  setStep: (n: number) => void;
  selectedApp: any;
  setSelectedApp: (app: any) => void;

  // Navigation timing
  startTimer: (target?: string) => void;
  stopTimer: (target?: string) => string | null;
  // Returns formatted elapsed time for a given target pathname (e.g., '/apps')
  getLastElapsed: (target?: string) => string | null;
  lastElapsed: string | null;
};

const WizardContext = createContext<WizardContextType>(null!);

export const WizardProvider = ({ children }: { children: React.ReactNode }) => {
  const [step, setStep] = useState(1);
  const [selectedApp, setSelectedApp] = useState<any>(null);

  // Timer state
  const timerStartRef = useRef<number | null>(null);
  const [lastElapsed, setLastElapsed] = useState<string | null>(null);

  const formatElapsed = (msTotal: number) => {
    const minutes = Math.floor(msTotal / 60000);
    const seconds = Math.floor((msTotal % 60000) / 1000);
    const centis = Math.floor((msTotal % 1000) / 10);

    const pad = (n: number, width = 2) => String(n).padStart(width, "0");

    return `${pad(minutes)}m : ${pad(seconds)}s : ${pad(centis)}ms`;
  };

  const startTimer = useCallback((target?: string) => {
    timerStartRef.current = Date.now();
    setLastElapsed(null);
    if (target) {
      try {
        sessionStorage.setItem("lastTimerTarget", target);
      } catch (e) {}
    }
  }, []);

  const stopTimer = useCallback((target?: string) => {
    const ts = timerStartRef.current;
    if (!ts) return null;
    const elapsed = Date.now() - ts;
    const formatted = formatElapsed(elapsed);
    setLastElapsed(formatted);
    timerStartRef.current = null;

    try {
      if (target) {
        sessionStorage.setItem("lastTimerTarget", target);
        sessionStorage.setItem(`lastElapsedMs_${target}`, String(elapsed));
      }
      sessionStorage.setItem("lastElapsedMs", String(elapsed));
    } catch (e) {}

    return formatted;
  }, []);

  const getLastElapsed = useCallback((target?: string) => {
    if (lastElapsed) return lastElapsed;
    const key = target ? `lastElapsedMs_${target}` : "lastElapsedMs";
    try {
      const raw = sessionStorage.getItem(key);
      if (!raw) return null;
      const ms = parseInt(raw, 10);
      if (isNaN(ms)) return null;
      return formatElapsed(ms);
    } catch (e) {
      return null;
    }
  }, [lastElapsed]);

  // Auto-start timer on navigation (covers back/forward and direct nav)
  const location = useLocation();
  useEffect(() => {
    // If there's an active timer, save its elapsed time before starting a new one
    if (timerStartRef.current) {
      const elapsed = Date.now() - timerStartRef.current;
      const formatted = formatElapsed(elapsed);
      try {
        sessionStorage.setItem("lastElapsedMs", String(elapsed));
      } catch (e) {}
      setLastElapsed(formatted);
    }
    // Now start timer for the new page
    timerStartRef.current = Date.now();
    try {
      sessionStorage.setItem("lastTimerTarget", location.pathname);
    } catch (e) {}
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname]);

  return (
    <WizardContext.Provider
      value={{
        step,
        setStep,
        selectedApp,
        setSelectedApp,
        startTimer,
        stopTimer,
        getLastElapsed,
        lastElapsed,
      }}
    >
      {children}
    </WizardContext.Provider>
  );
};

export const useWizard = () => useContext(WizardContext);
