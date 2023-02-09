import {html, render, useEffect, useState, useCallback, useRef} from "./preact_htm.js";

import {
  useScripts,
  useScript,
  useStartScript,
  killRunningScript,
  deleteRunningScript,
  RunningScriptInfoProvider,
  useRunningWebsocketConnected,
  useRunningScripts,
  useRunningScript,
  useRunningScriptProgress,
  useRunningScriptStatus,
  useRunningScriptReturnCode,
  useRunningScriptEndTime,
  useRunningScriptOutput,
} from "./client.js";

import {ArgumentInput} from "./forms.js";




/** Hook returning window.location.hash. */
function useHash() {
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    const cb = () => setHash(window.location.hash);
    addEventListener("hashchange", cb);
    return () => removeEventListener("hashchange", cb);
  }, [])
  
  return hash;
}

/** Hook which produces a unique ID. */
let _nextId = 0
function useId() {
  return useState(() => `__useId_${_nextId++}`)[0];
}

/** Simple centred spinner animation. */
function Spinner() {
  return html`
    <div class="Spinner">
      <svg viewBox="0 0 50 50">
        <circle cx="25" cy="25" r="20" fill="none" stroke-width="5"></circle>
      </svg>
    </div>
  `;
}

/** Vertically and horizontally centre whatever is inside. */
function CenteredMessage({children}) {
  return html`
    <div class="CenteredMessage">
      <div class="inner">
        ${children}
      </div>
    </div>
  `;
}

/** Shows an error message centered on the screen. */
function Error({children}) {
  return html`
    <${CenteredMessage}>
    <div class="Error">
        <svg viewBox="0 0 512 512">
          <!--! Font Awesome Pro 6.2.1 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license (Commercial License) Copyright 2022 Fonticons, Inc. -->
          <path d="M256 32c14.2 0 27.3 7.5 34.5 19.8l216 368c7.3 12.4 7.3 27.7 .2 40.1S486.3 480 472 480H40c-14.3 0-27.6-7.7-34.7-20.1s-7-27.8 .2-40.1l216-368C228.7 39.5 241.8 32 256 32zm0 128c-13.3 0-24 10.7-24 24V296c0 13.3 10.7 24 24 24s24-10.7 24-24V184c0-13.3-10.7-24-24-24zm32 224c0-17.7-14.3-32-32-32s-32 14.3-32 32s14.3 32 32 32s32-14.3 32-32z"/>
        </svg>
        <p>${children}</p>
      </div>
    <//>
  `;
}

/**
 * Modal dialogue containing the provided children.
 *
 * Dismissed by 'escape' key or clicking on the background.
 */
function Modal({children, onDismiss=null}) {
  // Close modal on pressing escape
  useEffect(() => {
    const onKeyDown = evt => {
      if (evt.keyCode === 27) {
        if (onDismiss) {
          onDismiss();
        }
        evt.preventDefault();
        evt.stopPropagation();
      }
    };
    
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  });
  
  const bgRef = useRef();
  const onBgClick = useCallback(e => {
    // Prevent bubbled events being mistaken for clicking the BG
    if (e.target === bgRef.current) {
      if (onDismiss) {
        onDismiss();
      }
    }
  }, []);
  
  return html`
    <div
      class="Modal"
      ref=${bgRef}
      onClick=${onBgClick}
    >
      <div class="inner">
        ${children}
      </div>
    </div>
  `;
}

/** Hook returning the current visual viewport. */
function useVisualViewport() {
  function copyVisualViewport() {
    return {
      offsetLeft: window.visualViewport.offsetLeft,
      offsetTop: window.visualViewport.offsetTop,
      pageLeft: window.visualViewport.pageLeft,
      pageTop: window.visualViewport.pageTop,
      width: window.visualViewport.width,
      height: window.visualViewport.height,
      scale: window.visualViewport.scale,
    };
  }
  
  const [visualViewport, setVisualViewport] = useState(copyVisualViewport());
  
  useEffect(() => {
    const onResize = evt => {
      setVisualViewport(copyVisualViewport());
    };
    window.visualViewport.addEventListener('resize', onResize);
    return () => window.visualViewport.removeEventListener('resize', onResize);
  }, []);
  
  return visualViewport;
}

/** An Android-style floating action button. */
function FloatingActionButton({children, onClick}) {
  // On mobile browsers, the UI appearing and disappearing will lead to the
  // action button being placed below the UI some of the time. We avoid this by
  // manually tracking the visual viewport size and moving the button
  // accordingly.
  const {offsetTop, offsetLeft, width, height} = useVisualViewport();
  
  return html`
    <div
      class="FloatingActionButton"
      style=${{
        top: offsetTop,
        left: offsetLeft,
        width,
        height,
        maxWidth: "",
      }}
    >
      <div class="inner" tabindex="0" onClick=${onClick}>
        ${children}
      </div>
    </div>
  `;
}

/** A floating action button with a + symbol on it. */
function PlusFloatingActionButton({onClick}) {
  return html`
    <${FloatingActionButton} onClick=${onClick}>
      <svg viewBox="0 0 50 50" width="25px" height="25px">
        <path
          d="M25 0 L25 50 M0 25 L50 25"
          stroke-width="8"
          stroke="white"
        />
      </svg>
    <//>
  `;
}


/**
 * Return an approximate textual description of how long has ellapsed since the
 * given timestamp (in ms).
 */
function approxTimeSince(timestamp) {
  const seconds = Math.round((Date.now() - timestamp) / 1000);
  
  if (seconds < 10) {
    return "just now";
  } else if (seconds < 60) {
    return `about ${seconds - (seconds % 10)} seconds ago`;
  }
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) {
    return `about ${minutes} minute${minutes != 1 ? 's' : ''} ago`;
  }
  
  const hours = Math.round(minutes / 60);
  if (hours < 24) {
    return `about ${hours} hour${hours != 1 ? 's' : ''} ago`;
  }
  
  const days = Math.round(hours / 24);
  return `about ${days} day${days != 1 ? 's' : ''} ago`;
}


/** Hook which returns the current approxTimeSince a timestamp. */
function useApproxTimeSince(timestamp) {
  const [value, setValue] = useState(approxTimeSince(timestamp))
  
  useEffect(() => {
    const intervalId = setInterval(() => {
      setValue(approxTimeSince(timestamp));
    }, 1000);
    
    return () => clearInterval(intervalId);
  }, [timestamp]);
  
  return value
}


/**
 * Return an approximate textual description of how long a duration (given in
 * ms) was.
 */
function approxDuration(milliseconds) {
  if (milliseconds < 1000) {
    return `${milliseconds} ms`;
  }
  
  const seconds = Math.floor(milliseconds / 1000);
  if (seconds < 60) {
    return `${seconds} s`;
  }
  
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes} min`;
  }
  
  const hours = Math.floor(minutes / 60);
  return `${hours} hr`;
}

/**
 * Scroll the provided element into view iff it is not entirely on-screen.
 */
function scrollIntoViewIfNeeded(elem) {
  const container = elem.offsetParent;
  
  const visibleTop = container.scrollTop;
  const visibleBottom = visibleTop + container.offsetHeight;
  
  const offsetBottom = elem.offsetTop + elem.offsetHeight;
  
  if (elem.offsetTop < visibleTop || offsetBottom > visibleBottom) {
    elem.scrollIntoView({
      behaviour: "smooth",
      block: "nearest",
    });
  }
}

/**
 * Given a ref to a scrollable element, keep the element scrolled to the bottom
 * whenever anything in the sensitivityList array changes, unless the user has
 * manually scrolled up.
 */
function useKeepScrolledToBottom(ref, sensitivityList) {
  const lastAtBottomRef = useRef(true);
  
  if (ref.current) {
    const elem = ref.current;
    const atBottom = elem.scrollTop + elem.offsetHeight == elem.scrollHeight;
    lastAtBottomRef.current = atBottom;
  } else {
    lastAtBottomRef.current = true;
  }
  
  useEffect(() => {
    const elem = ref.current;
    if (elem && lastAtBottomRef.current) {
      // Scroll to bottom
      elem.scrollTop = elem.scrollHeight - elem.offsetHeight;
    }
  }, sensitivityList);
}

/** A simple 'X' button. */
function CloseButton({onClick}) {
  return html`
    <div class="CloseButton" tabindex="2" onClick=${onClick}>
      <svg viewBox="0 0 50 50" width="15px">
        <path
          d="M4 4 L46 46 M4 46 L 46 4"
          stroke="black"
          stroke-width="8"
        />
      </svg>
    </div>
  `;
}


/** The list of available scripts. */
function ScriptList() {
  const [scriptList, error] = useScripts();
  
  if (error !== null) {
    return html`<${Error}>${error}<//>`;
  }
  
  if (scriptList === null) {
    return html`<${Spinner}/>`;
  }
  
  scriptList.sort((a, b) => (a.name < b.name) ? -1 : (a.name > b.name) ?  1 : 0);
  
  return html`
    <ul class="ScriptList">
      ${scriptList.map(script => html`
        <li key=${script.script}>
          <a
            href="#/scripts/${encodeURI(script.script)}"
            title="${script.script}"
            tabindex="1"
          >
            ${script.name}
          </a>
        </li>
      `)}
    </ul>
  `;
}

/**
 * Form for specifying script arguments.
 *
 * @param onSubmit Called with the FormData gathered from the form.
 * @param onCancel Called when 'Cancel' is clicked.
 * @param initialArgs Array of initial values for all arguments (e.g. if
 *                    re-running script with same args).
 */
function RunScriptForm({
  script: scriptFilename,
  onSubmit,
  onCancel,
  initialArgs=null,
}) {
  const [script, error] = useScript(scriptFilename);
  
  if (error !== null) {
    return html`
      <${Error}>
        <p>${error}</p>
        <button onClick=${onCancel}>Close</button>
      <//>
    `;
  }
  
  if (script === null) {
    return html`<${Spinner}/>`;
  }
  
  let description = null;
  if (script.description) {
    description = script.description.split(/\n\n+/).map(line => html`<p>${line}</p>`);
  }
  
  // Focus first input when first displayed
  const firstInputRef = useRef();
  useEffect(() => {
    if (firstInputRef.current) {
      firstInputRef.current.focus();
    }
  }, [script.args]);
  
  const baseId = useId();
  
  const inputs = [];
  for (let i = 0; i < script.args.length; i++) {
    const arg = script.args[i];
    const initialValue = (initialArgs || [])[i];
    
    const name = `arg${inputs.length}`;
    const id = `${baseId}_${name}`;
    
    const extraClass = arg.description ? "" : "unnamed";
    
    inputs.push(html`
      <div class="argument" key=${name}>
        <label class="description ${extraClass}" for=${id}>
          ${arg.description || `(Argument ${i+1})`}
        </label>
        <div class="input">
          <${ArgumentInput}
            type=${arg.type}
            id=${id}
            name=${name}
            value=${initialValue}
            inputRef=${i == 0 ? firstInputRef : null}
          />
        </div>
      </div>
    `);
  }
  
  const onSubmitCb = useCallback(e => {
    if (onSubmit) {
      onSubmit(new FormData(e.target));
    }
    e.preventDefault();
    e.stopPropagation();
  }, [onSubmit]);
  
  return html`
    <div class="RunScriptForm">
      <h1>${script.name}</h1>
      ${description}
      <form onSubmit=${onSubmitCb}>
        <div class="inputs">
          ${inputs}
        </div>
        <div class="buttons">
          <button type="button" onClick=${onCancel}>Cancel</button>
          <input type="submit" value="Run script"/>
        </div>
      </form>
    </div>
  `;
}

/**
 * Start a script running, showing a progress indicator while data is
 * uploaded.
 *
 * @param formData The form data to submit (or null to do nothing and cancel
 *                 any previously started upload).
 * @param onFinish Called with the running script ID when the script has
 *                 started.
 * @param onFail Called if the upload fails for some reason (e.g. network).
 * @param onCancel Called if the cancel button is pressed. (Does not cancel the
 *                 submission on its own!)
 */
function RunScriptUpload({script, formData, onFinish, onFail, onCancel}) {
  const [response, error, progress] = useStartScript(script, formData);
  
  if (error) {
    return html`
      <${Error}>
        <div class="RunScriptUpload-error">
          <p>Couldn't start script: <code>${error}</code></p>
          <button onClick=${onFail}>OK</button>
        </div>
      <//>
    `;
  }
  
  // This is a bit naughty but it does work...
  useEffect(() => {
    if (response && onFinish) {
      onFinish(response)
    }
  }, [response, script, formData, onFinish]);
  
  let state;
  if (response) {
    state = "Script started!";
  } else if (progress <1.0) {
    state = "Uploading data..." ;
  } else {
    state =  "Starting script...";
  }
  
  return html`
    <div class="RunScriptUpload">
      <div class="inner">
        <h1>${state}</h1>
        <progress value="${progress}" max="1" />
        <button class="cancel" onClick=${onCancel}>Cancel</button>
      </div>
    </div>
  `;
}

/** Top level dialogue showing script argument form then upload progress. */
function RunScriptDialogue({script, initialArgs=null}) {
  const [formData, setFormData] = useState(null);
  
  // Form callbacks
  const onSubmit = useCallback(formData => {
    // Prevent double submission
    setFormData(oldFormData => oldFormData ? oldFormData : formData);
  }, []);
  const onCancel = useCallback(() => {
    history.back()
  }, []);
  
  // Upload callbacks
  const cancelUpload = useCallback(() => setFormData(null), []);
  const onFinish = useCallback(rs_id => {
    window.location.hash = `#/running/${encodeURI(rs_id)}`;
  }, []);
  
  let upload = null;
  if (formData) {
    upload = html`
      <div class="upload">
        <${RunScriptUpload}
          script=${script}
          formData=${formData}
          onFinish=${onFinish}
          onCancel=${cancelUpload}
          onFail=${cancelUpload}
        />
      </div>
    `;
  }
  
  // NB: We hide the form during upload (rather than removing it entirely) so
  // that if we cancel/fail we can re-show it without loss of state.
  const uploadingClass = formData ? "uploading" : "";
  
  return html`
    <div class="RunScriptDialogue ${uploadingClass}">
      <div class="form">
        <${RunScriptForm}
          script=${script}
          initialArgs=${initialArgs}
          onSubmit=${onSubmit}
          onCancel=${onCancel}
        />
      </div>
      ${upload}
    </div>
  `;
}

/**
 * Show (and control) the state of a running script.
 *
 * @param id The running script ID
 * @param script The script's filename.
 * @param name The script's friendly name
 * @param args The args used to call the script.
 * @param startTime The time the script was started (ISO timestamp)
 *
 * The following props will be used as initial values with live data streamed
 * in from the server to supersede it.
 *
 * @param endTime The time the script was ended (ISO timestamp) or null.
 * @param progress The current progress.
 * @param status The current status.
 * @param returnCode The return code or null.
 */
function RunningScriptListEntry({
  id,
  script,
  name,
  args,
  startTime,
  endTime: initialEndTime,
  progress: initialProgress,
  status: initialStatus,
  returnCode: initialReturnCode,
}) {
  // Pull in live status info
  const endTime = useRunningScriptEndTime(id, initialEndTime);
  const progress = useRunningScriptProgress(id, initialProgress);
  const status = useRunningScriptStatus(id, initialStatus);
  const returnCode = useRunningScriptReturnCode(id, initialReturnCode);
  
  // Determine current state
  let state;
  if (returnCode !== null) {
    if (returnCode === 0) {
      state = "Succeeded";
    } else if (returnCode > 0) {
      state = "Failed";
    } else {  // returnCode < 0
      state = "Killed";
    }
  } else {
    state = "Running";
  }
  
  // Format progress information
  let progressBar = null;
  if (returnCode === null) {
    let progressRatio = 0;
    if (progress[1] !== 0) {
      progressRatio = progress[0] / progress[1];
    }
    progressBar = html`
      <div
        class="progress-bar"
        style="--progress: ${progressRatio}"
      />
    `;
  }
  
  // Include the coarse progress in the status line when indicating something
  // other than a simple fraction.
  let statusLinePrefix = state;
  if (status) {
    // No need to state we're running if script is producing its own status.
    statusLinePrefix = "";
  }
  if (progress[1] != 0 && progress[1] != 1) {
    // If producing an interesting progress indication, show that
    statusLinePrefix = `${Math.floor(progress[0])}/${progress[1]}`;
  }
  
  // Format approximate timing information
  const timeSinceStarted = useApproxTimeSince(Date.parse(startTime));
  const timeSinceEnded = useApproxTimeSince(endTime ? Date.parse(endTime) : 0);
  let runtime;
  if (!endTime) {
    runtime = `Started ${timeSinceStarted}`;
  } else {
    const duration = approxDuration(Date.parse(endTime) - Date.parse(startTime));
    runtime = `${state} ${timeSinceEnded} (took ${duration})`;
  }
  
  // Toggle the display of output and extra script controls
  const [expanded, setExpanded] = useState(false);
  const toggleExpanded = useCallback(() => setExpanded(state => !state), []);
  
  // Fetch the real time script output (unless not expanded)
  const output = useRunningScriptOutput(expanded ? id : null);
  
  // Toggle filtering of '## ...' declarations in output
  const [hideDeclarations, setHideDeclarations] = useState(true);
  const toggleHideDeclarations = useCallback(() => {
    setHideDeclarations(state => !state);
  }, []);
  
  // Callback which brings up the script starting form with the same arguments
  // used for this script filled in.
  const onRunAgain = useCallback(() => {
    const encodedArgs = encodeURIComponent(JSON.stringify(args));
    window.location.hash = `#/scripts/${encodeURI(script)}?args=${encodedArgs}`;
  }, [script, args]);
  
  // Callback which kills a running script or deletes a finished one.
  const killOrDelete = returnCode === null ? "Kill" : "Delete";
  const onKillOrDelete = useCallback(() => {
    if (returnCode === null) {
      killRunningScript(id);
    } else {
      deleteRunningScript(id);
    }
  }, [id, returnCode]);
  
  // Scroll into view on first render if the window hash matches this ID since
  // the ID will have been set just moments before this entry finally appeared
  // in the UI.
  const hash = useHash();
  const outerRef = useRef();
  useEffect(() => {
    if (hash === `#/running/${encodeURI(id)}` && outerRef.current) {
      outerRef.current.scrollIntoView();
    }
  }, []);
  
  
  // Scroll into view when first expanded (since output might immediately run
  // off the end of the screen).
  useEffect(() => {
    if (expanded && outerRef.current) {
      scrollIntoViewIfNeeded(outerRef.current);
    }
  }, [
    expanded,
    // NB: Force re-evaluation when output changes to non-empty since the
    // output isn't loaded instantly the first attempt to scroll will fall
    // short.
    output != "",
  ]);
  
  // Keep output viewer scrolled to the bottom
  const outputRef = useRef();
  useKeepScrolledToBottom(outputRef, [output]);
  
  let details = null;
  if (expanded) {
    let filteredOutput = output;
    if (hideDeclarations) {
      const outputLines = output.split("\n");
      const filteredOutputLines = outputLines.filter(
        line => !line.match(/^\s*## ([a-zA-Z0-9_-]+)\s*:\s*(.*)$/)
      );
      filteredOutput = filteredOutputLines.join("\n");
    }
    
    details = html`
      <div class="details">
        <div class="buttons">
          <label class="hide-declarations">
            <input
              type="checkbox"
              checked=${hideDeclarations}
              onChange=${toggleHideDeclarations}
            />
            Hide progress
          </label>
          <button onClick=${onRunAgain} class="again">
            Run again
          </button>
          <button onClick=${onKillOrDelete} class="kill-or-delete">
            ${killOrDelete}
          </button>
        </div>
        <pre class="output" ref=${outputRef}>
          ${filteredOutput}
        </pre>
      </div>
    `;
  }
  
  return html`
    <div 
      class="RunningScriptListEntry ${state.toLowerCase()}"
      ref=${outerRef}
    >
      <div
        class="header"
        id="/running/${id}"
        tabindex="2"
        onClick=${toggleExpanded}
      >
        <div class="title-line">
          <h1 title=${script}>${name}</h1>
          <div
            class="runtime"
            title="${startTime} - ${endTime || "(ongoing)"}"
          >
            ${runtime}
          </div>
        </div>
        <div 
          class="status-line"
          title="${returnCode !== null ? `Return code: ${returnCode}` : ""}"
        >
          ${statusLinePrefix}
          ${(statusLinePrefix && status) ? ": " : ""}
          ${status}
        </div>
        ${progressBar}
      </div>
      ${details}
    </div>
  `;
};


/** Enumerates all running scripts. */
function RunningScriptList() {
  const runningScripts = useRunningScripts();
  
  if (runningScripts === null) {
    return html`<${Spinner}/>`;
  }
  
  if (runningScripts.length === 0) {
    return html`
      <div class="RunningScriptList empty">
        <${CenteredMessage}>
            No scripts running.
        <//>
      </div>
    `;
  }
  
  // Order by start time, with the most recently started script at the top.
  //
  // NB: We can't do anything clever like keeping running scripts at the top
  // here because useRunningScripts doesn't report changes in scripts'
  // liveness.
  runningScripts.sort((a, b) => {
    const aStartTime = Date.parse(a.start_time);
    const bStartTime = Date.parse(b.start_time);
    return bStartTime - aStartTime;
  });
  
  return html`
    <ul class="RunningScriptList">
      ${runningScripts.map(rs => html`
        <li key=${rs.id}>
          <${RunningScriptListEntry}
            id=${rs.id}
            script=${rs.script}
            name=${rs.name}
            args=${rs.args}
            startTime=${rs.start_time}
            endTime=${rs.end_time}
            progress=${rs.progress}
            status=${rs.status}
            returnCode=${rs.return_code}
          />
        </li>
      `)}
    </ul>
  `;
}


/**
 * The main application.
 *
 * The coarse grained mode of the application is determined to be one of the
 * following based on the current window.location.hash:
 *
 * * "script-list" mode (when at #/scripts/) -- showing a list of scripts.
 * * "script-form" mode (when at #/scripts/*) -- showing a form to
 *   collect arguments to run a script.
 * * "running-list" mode (when at #/running/*") -- showing the currently
 *   running list of scripts
 *
 * On narrow screens (e.g. phones) these modes largely correspond to which
 * 'view' is being shown to the user. On desktop screens, the script-list and
 * running-list modes are visually identical (both are shown at once), but the
 * script-form mode involves a modal dialogue.
 */
function App() {
  // Determine the broad mode of the application
  const hash = useHash();
  let mode;
  if (hash === "#/scripts/") {
    mode = "script-list";
  } else if (hash.startsWith("#/scripts/")) {
    mode = "script-form";
  } else {  // if (hash.startsWith("#/running/")) {
    mode = "running-list";
  }
  
  // Navigation callbacks
  const goBack = useCallback(() => history.back(), []);
  const showScriptList = useCallback(() => {
    window.location.hash = "#/scripts/";
  }, []);
  
  // Generate the modal dialogue with the script-form in it
  let scriptFormModal = null;
  const scriptHashMatch = hash.match(/^#\/scripts\/([^?]+)([?].*)?$/);
  if (scriptHashMatch) {
    // Extract the script filename from the URI
    const script = decodeURI(scriptHashMatch[1]);
    
    // Extract any initial argument values from the URI
    let initialArgs = null;
    if (scriptHashMatch[2] && scriptHashMatch[2].startsWith("?args=")) {
      try {
        initialArgs = JSON.parse(
          decodeURIComponent(
            scriptHashMatch[2].substring(6)
          )
        );
      } catch (e) {
        console.warn("Bad initial args:", e);
      }
    }
    
    scriptFormModal = html`
      <${Modal} onDismiss=${goBack}>
        <${RunScriptDialogue}
          script=${script}
          initialArgs=${initialArgs}
          onDismiss=${goBack}
        />
      <//>
    `;
  }
  
  // Show notification if connectivity lost
  let connectivityNotification = null;
  const connected = useRunningWebsocketConnected();
  if (!connected) {
    connectivityNotification = html`
      <div class="connectivity-notification">
        Connection to server lost.
      </div>
    `;
  }
  
  return html`
    <div class="App ${mode}">
      <div class="split">
        <div class="pane pane-left">
          <div class="header">
            <h1>Scriptie</h1>
            <${CloseButton} onClick=${goBack} />
          </div>
          <${ScriptList}/>
        </div>
        <div class="pane pane-right">
          <${RunningScriptList}/>
          <div class="action-button">
            <${PlusFloatingActionButton} onClick=${showScriptList} />
          </div>
          <div class="shade" onClick=${goBack} />
        </div>
      </div>
      ${scriptFormModal}
      ${connectivityNotification}
    </div>
  `;
}

render(
  html`
    <${RunningScriptInfoProvider}>
      <${App}/>
    <//>
  `,
  document.body
);
