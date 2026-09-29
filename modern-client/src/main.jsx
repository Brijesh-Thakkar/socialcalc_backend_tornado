import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { IonApp, IonButton, IonContent, IonHeader, IonTitle, IonToolbar, setupIonicReact } from "@ionic/react";
import {
  enableGridLines,
  enableRowColHeaders,
  enableTouchScroll,
  getMSCContent,
  initializeApp,
} from "socialcalc-ai";
import "@ionic/react/css/core.css";
import "@ionic/react/css/normalize.css";
import "@ionic/react/css/structure.css";
import "@ionic/react/css/typography.css";
import "./style.css";

setupIonicReact();

function App() {
  const [fname, setFname] = useState("default");
  const [status, setStatus] = useState("Loading saved sheet…");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let active = true;
    const name = new URLSearchParams(window.location.search).get("fname") || "default";
    setFname(name);
    fetch(`/api/v1/modern/sheet?fname=${encodeURIComponent(name)}`, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || `Could not load sheet (${response.status})`);
        return payload.data;
      })
      .then((data) => {
        if (!active) return;
        initializeApp(data);
        enableRowColHeaders();
        enableGridLines();
        enableTouchScroll();
        setReady(true);
        setStatus(`Loaded “${name}”`);
      })
      .catch((error) => active && setStatus(error.message));

    return () => { active = false; };
  }, []);

  async function save() {
    if (!ready) return;
    setStatus("Saving…");
    try {
      const data = getMSCContent();
      const response = await fetch("/save", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
        body: new URLSearchParams({ fname, data }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.data || result.error || `Save failed (${response.status})`);
      setStatus(`Saved “${fname}”`);
    } catch (error) {
      setStatus(error.message);
    }
  }

  async function archiveOn0G() {
    if (!ready) return;
    if (!window.confirm(`Archive “${fname}” on 0G?`)) return;
    setStatus("Archiving on 0G…");
    try {
      const response = await fetch("/api/v1/0g/archive", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fname, data: getMSCContent() }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || `Archive failed (${response.status})`);
      setStatus(`${result.mock ? "Mock archive" : "Archived"}: ${result.rootHash}`);
    } catch (error) {
      setStatus(error.message);
    }
  }

  return (
    <IonApp>
      <IonHeader>
        <IonToolbar>
          <IonTitle>Modern SocialCalc · {fname}</IonTitle>
          <IonButton slot="end" disabled={!ready} onClick={save}>Save</IonButton>
          <IonButton slot="end" disabled={!ready} onClick={archiveOn0G}>Archive on 0G</IonButton>
        </IonToolbar>
      </IonHeader>
      <IonContent>
        <div className="status" role="status">{status}</div>
        <main id="container">
          <div id="workbookControl" />
          <div id="msg" />
          <div id="tableeditor" />
        </main>
        <footer><a href="/save">Back to saved sheets</a></footer>
      </IonContent>
    </IonApp>
  );
}

createRoot(document.getElementById("root")).render(<App />);
