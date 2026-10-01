import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { IonApp, setupIonicReact } from "@ionic/react";
import {
  enableCellEditModal,
  enableGridLines,
  enableRowColHeaders,
  enableTouchScroll,
  getMSCContent,
  initializeApp,
  updateCellValueAndFormat,
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
  const [cellEdit, setCellEdit] = useState(null);
  const [cellValue, setCellValue] = useState("");
  const finishingCellEdit = useRef(false);

  useEffect(() => {
    const handleCellEdit = (event) => {
      const detail = event.detail;
      const cell = document.getElementById(`cell_${detail.coord}`);
      if (!cell) return;
      const rect = cell.getBoundingClientRect();
      finishingCellEdit.current = false;
      setCellValue(detail.text || "");
      setCellEdit({
        detail,
        style: {
          left: `${rect.left}px`,
          top: `${rect.top}px`,
          width: `${Math.max(rect.width, 60)}px`,
          height: `${Math.max(rect.height, 24)}px`,
        },
      });
    };
    window.addEventListener("socialcalc:cell-edit-request", handleCellEdit);
    return () => window.removeEventListener("socialcalc:cell-edit-request", handleCellEdit);
  }, []);

  useEffect(() => {
    if (!cellEdit) return;
    const input = document.querySelector(".sc-inline-cell-editor");
    input?.focus();
    input?.select();
  }, [cellEdit]);

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
        enableCellEditModal();
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

  function finishCellEdit(commit) {
    if (!cellEdit || finishingCellEdit.current) return;
    if (commit) {
      updateCellValueAndFormat(cellEdit.detail.coord, cellValue, {});
    }
    finishingCellEdit.current = true;
    cellEdit.detail.cleanup?.();
    setCellEdit(null);
  }

  return (
    <IonApp>
      <div className="modern-shell">
        <h3 className="modern-heading">
          <a href="/">Aspiring Investments</a>&nbsp;&nbsp;&nbsp;Modern editor: {fname}
        </h3>
        <div className="modern-actions">
          <input className="smaller" type="button" value="Save" disabled={!ready} onClick={save} />
          <input className="smaller" type="button" value="Archive on 0G" disabled={!ready} onClick={archiveOn0G} />
          <a href="/save">Back to saved sheets</a>
        </div>
        <div className="status" role="status">{status}</div>
        <main id="container">
          <div id="workbookControl" />
          <div id="msg" />
          <div id="tableeditor" />
        </main>
      </div>
      {cellEdit && (
        <input
          className="sc-inline-cell-editor"
          aria-label={`Edit cell ${cellEdit.detail.coord}`}
          style={cellEdit.style}
          value={cellValue}
          onChange={(event) => setCellValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              finishCellEdit(true);
            } else if (event.key === "Escape") {
              event.preventDefault();
              finishCellEdit(false);
            }
          }}
          onBlur={() => finishCellEdit(true)}
        />
      )}
    </IonApp>
  );
}

createRoot(document.getElementById("root")).render(<App />);
