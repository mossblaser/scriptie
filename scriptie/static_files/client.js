/**
 * This module contains functions and hooks for interacting with the scriptie
 * server.
 *
 * For some hooks to function, the containing component must be part of a
 * RunningScriptInfoProvider.
 */

import {
  html,
  createContext,
  useEffect,
  useState,
  useRef,
  useCallback,
  useContext,
} from "./preact_htm.js";

import {useFormSubmission} from "./forms.js";


/**
 * Promise which resolves to a JSON list of scripts.
 */
async function fetchScripts() {
  const resp = await fetch("scripts/")
  if (!resp.ok) {
    throw `Couldn't list scripts: ${await resp.text()}`;
  }
  return await resp.json();
}

/**
 * Promise which resolves to a JSON description of the named script.
 */
async function fetchScript(script) {
  const resp = await fetch(`scripts/${encodeURI(script)}`)
  if (!resp.ok) {
    throw `Couldn't get script metadata: ${await resp.text()}`;
  }
  return await resp.json();
}

/**
 * Promise which resolves to a JSON description of a currently running script.
 */
async function fetchRunningScript(rsId) {
  const resp = await fetch(`running/${rsId}`)
  if (!resp.ok) {
    throw `Couldn't get running script metadata: ${await resp.text()}`;
  }
  return await resp.json();
}

/** 
 * Hook which returns the value (or error) returned by the promise returned by
 * getPromise.
 *
 * Returns [value, error] where value is initialValue until the promise
 * succeeds and error is initialError until the promise fails.
 *
 * Value and error will be reset to their initial values whenever the
 * getPromise function changes.
 */
function usePromise(getPromise, {initialValue=null, initialError=null}={}) {
  const [value, setValue] = useState(initialValue);
  const [error, setError] = useState(initialError);
  
  useEffect(() => {
    const cancelled = {current: false};
    setValue(initialValue);
    setError(initialError);
    getPromise()
      .then(value => {if (!cancelled.current) setValue(value)})
      .catch(error => {if (!cancelled.current) setError(error)})
    
    return () => {cancelled.current = true};
  }, [getPromise]);
  
  return [value, error];
}


/**
 * Returns [scriptList, error] where scriptList is null whilst the list is
 * being loaded and error is null unless an error has occurred during loading.
 */
export function useScripts() {
  return usePromise(fetchScripts);
}

/**
 * Returns [scriptDescription, error] where scriptDescription is null whilst
 * the description is being loaded and error is null unless an error has
 * occurred during loading.
 */
export function useScript(script) {
  return usePromise(useCallback(() => fetchScript(script), [script]));
}

/**
 * Returns [runningScriptDescription, error] where runningScriptDescription is
 * null whilst the description is being loaded and error is null unless an
 * error has occurred during loading.
 */
export function useRunningScript(rsId) {
  return usePromise(useCallback(() => fetchRunningScript(rsId), [rsId]));
}


/**
 * Trigger the starting of a new script, reporting progress as any files are
 * uploaded.
 *
 * Takes as arguments the script filename to start and a FormData object with
 * the arguments to pass to it. If FormData is None, any ongoing submissions
 * will be cancelled and no script will be started.
 *
 * Returns a 3-list [submitResponse, submitError, submitProgress].
 *
 * submitResponse is null until the script has successfully started at which
 * point it will contain the newly started running script ID.
 *
 * submitError will be null unless an error occurs during submission.
 *
 * submitProgress is a float between 0.0 and 1.0 indicating data upload
 * progress. This may hit 1.0 before submitResponse becomes non-null. Prior to
 * any FormData being provided, will remain at 0.0.
 */
export function useStartScript(script, formData=null) {
  return useFormSubmission("post", `scripts/${encodeURI(script)}`, formData);
}

/**
 * Kill a running script.
 */
export async function killRunningScript(rsId) {
  const resp = await fetch(`running/${rsId}/kill`, {method: "POST"})
  if (!resp.ok) {
    throw `Couldn't delete script: ${await resp.text()}`;
  }
}

/**
 * Delete a running script.
 */
export async function deleteRunningScript(rsId) {
  const resp = await fetch(`running/${rsId}`, {method: "DELETE"})
  if (!resp.ok) {
    throw `Couldn't delete script: ${await resp.text()}`;
  }
}

/**
 * A client for the /running/ws API end point.
 *
 * The call() method is used to make calls to the server from which responses
 * are returned asynchronously via promises.
 *
 * The close() method is used to shut down the connection to the websocket.
 */
class RunningWebSocketClient {
  constructor() {
    // Next ID number
    this._nextId = 0;
    
    // Set to true when we're closing the connection intentionally.
    this._shutdown = false;
    
    // Mapping from ID to a {request, resolve, reject} object where request
    // contains the original request and resolve/reject are Promise-callbacks
    // which to be called with the response when it is eventually received.
    this._pending = {}
    
    this.connected = false;
    
    this._eventListeners = {
      "connect": [],
      "disconnect": [],
    };
    
    this._connect()
  }
  
  _connect() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    
    const pathParts = window.location.pathname.split("/");
    pathParts[pathParts.length - 1] = "running";
    pathParts.push("ws")
    const path = pathParts.join("/")
    
    this._ws = new WebSocket(`${protocol}//${window.location.host}${path}`);
    
    this._ws.onmessage = this._onMessage.bind(this);
    this._ws.onopen = this._onOpen.bind(this);
    this._ws.onclose = this._onClose.bind(this);
  }
  
  _onOpen(evt) {
    this.connected = true;
    this._sendEvent("connect");
    
    // (Re-)send any pending messages upon connection
    for (const {request} of Object.values(this._pending)) {
      this._ws.send(JSON.stringify(request));
    }
  }
  
  _onClose(evt) {
    // Reconnect to the websocket if it closes for whatever reason after 1s
    if (!this._shutdown && this.connected) {
      console.warn("RunningWebSocketClient disconnected, reconnecting in 1s...");
    }
    
    if (this.connected) {
      this.connected = false;
      this._sendEvent("disconnect");
    }
    
    this._ws = null;
    setTimeout(() => {
      if (!this._shutdown) {
        this._connect();
      }
    }, 1000);
  }
  
  _onMessage(evt) {
    if (this._shutdown) {
      return;  // Do nothing after shutdown
    }
    
    const response = JSON.parse(evt.data);
    const id = response.id;
    if (this._pending.hasOwnProperty(id)) {
      const {resolve, reject} = this._pending[id];
      delete this._pending[id];
      if (response.hasOwnProperty("value")) {
        resolve(response["value"])
      } else {
        reject(response["error"])
      }
    } else {
      console.warn(`Got response to unknown ID ${id}: ${JSON.stringify(response)}`);
    }
  }
  
  close() {
    this._shutdown = true;
    this._ws.close();
    
    for (const {reject} of Object.values(this._pending)) {
      reject("Connection shutting down.")
    }
  }
  
  /**
   * Listen for a websocket state change.
   *
   * Available events are 'connect' and 'disconnect'.
   */
  addEventListener(name, cb) {
    this._eventListeners[name].push(cb);
  }
  removeEventListener(name, cb) {
    const eventListeners = this._eventListeners[name];
    const i = eventListeners.find(cb);
    if (i >= 0) {
      eventListeners.splice(i, 1);
    }
  }
  _sendEvent(name) {
    for (const cb of this._eventListeners[name]) {
      cb();
    }
  }
  
  call(type, params={}) {
    const id = `req_${this._nextId++}`;
    const request = {id, type, ...params};
    
    return new Promise((resolve, reject) => {
      if (this._shutdown) {
        reject("Connection shutting down");
      } else {
        this._pending[id] = {request, resolve, reject};
        if (this._ws.readyState === WebSocket.OPEN) {
          this._ws.send(JSON.stringify(request));
        }
      }
    });
  }
}


const RunningWebSocketClientContext = createContext(null);

/**
 * This provider component gives all children access to a
 * RunningWebSocketClient which is used to return live status information about
 * running scripts from the server.
 *
 * Required by all of the useRunningScript* hooks.
 */
export function RunningScriptInfoProvider({children}) {
  // NB: Naughty but this is only created once and avoids us having to go
  // through a render cycle handling the case where this is Null...
  const runningWebSocketClient = useRef(null);
  if (runningWebSocketClient.current === null) {
    runningWebSocketClient.current = new RunningWebSocketClient();
  }
  // Cleanup only
  useEffect(() => {
    return () => {
      runningWebSocketClient.current.close();
      runningWebSocketClient.current = null;
    }
  }, []);
  
  return html`
    <${RunningWebSocketClientContext.Provider} value=${runningWebSocketClient.current}>
      ${children}
    <//>
  `;
}


/**
 * Returns the current 'connected' state of the running websocket client
 * provided by the surrounding RunningWebSocketClientContext.
 */
export function useRunningWebsocketConnected() {
  const runningWebSocketClient = useContext(RunningWebSocketClientContext);
  const [connected, setConnected] = useState(runningWebSocketClient.connected);
  
  useEffect(() => {
    const onConnect = () => setConnected(true);
    const onDisconnect = () => setConnected(false);
    
    runningWebSocketClient.addEventListener("connect", onConnect);
    runningWebSocketClient.addEventListener("disconnect", onDisconnect);
    
    return () => {
      runningWebSocketClient.removeEventListener("connect", onConnect);
      runningWebSocketClient.removeEventListener("disconnect", onDisconnect);
    };
  }, [runningWebSocketClient]);
  
  return connected;
}


/**
 * Returns the 'call' method of the running websocket client provided by a
 * surrounding RunningWebSocketClientContext.
 */
function useRunningWebsocketClient() {
  const runningWebSocketClient = useContext(RunningWebSocketClientContext);
  return runningWebSocketClient.call.bind(runningWebSocketClient);
}


/**
 * Automates the process of polling a value which changes as a script runs.
 *
 * The getNextValue function is called with the current value and must return a
 * promise which resolves to the next value (whenever a change occurs) or to
 * the same value (if the script has exited and no further updates are
 * possible). Changes are based on using JSON.stringify to compare subsequent
 * values (grr Javascript's lack of deep comparisons!).
 *
 * The initialValue should be the initial value to shown prior to a value being
 * fetched.
 *
 * The returned value will initially be the initialValue and will be replaced
 * with values fetched by getNextValue as they come in.
 */
function useChangingValue(getNextValue, initialValue) {
  const [value, setValue] = useState(initialValue);
  
  useEffect(() => {
    const run = {current: true};
    
    (async () => {
      let oldValue = initialValue;
      setValue(initialValue);
      while (run.current) {
        const newValue = await getNextValue(oldValue);
        if (run.current && JSON.stringify(newValue) !== JSON.stringify(oldValue)) {
          setValue(newValue);
          oldValue = newValue;
        } else {
          // Next value was same as previous value so we can stop checking now
          return;
        }
      }
    })();
    
    return () => {run.current = false};
  }, [getNextValue, initialValue]);
  
  return value
}


/**
 * Automates the process of waiting for a value to become non-null.
 *
 * The getValue function should return a promise which resolves to a non-null
 * value on script termination.
 *
 * The initialValue will be the initial value. If non-null, getValue is not
 * called and that value is returned. Otherwise, the null is returned until
 * getValue resolves and that value is returned.
 */
function useDefinedOnceValue(getValue, initialValue) {
  const rwsc = useRunningWebsocketClient();
  const [value, setValue] = useState(initialValue);
  
  useEffect(() => {
    const cancelled = {current: false};
    
    setValue(initialValue);
    
    (async () => {
      if (initialValue === null) {
        const newValue = await getValue();
        if (!cancelled.current) {
          setValue(newValue);
        }
      } else {
        setValue(initialValue);
      }
    })();
    
    return () => {cancelled.current = true};
  }, [getValue, initialValue]);
  
  return value;
}


/**
 * Returns runningScriptList or null whilst loading. Updates dynamically as
 * scripts are started, deleted or expire. Does not update when script states
 * change: this should be monitored separately.
 */
export function useRunningScripts() {
  const rwsc = useRunningWebsocketClient()
  const [runningScripts, setRunningScripts] = useState(null);
  
  useEffect(() => {
    const run = {current: true};
    
    (async () => {
      let oldRsIds = ["non-existant-id-to-force-instant-response"];
      while (run.current) {
        const runningScripts = await rwsc("wait_for_running_change", {old_rs_ids: oldRsIds});
        setRunningScripts(runningScripts);
        oldRsIds = runningScripts.map(({id}) => id);
      }
    })();
    
    return () => {run.current = false};
  }, []);
  
  return runningScripts;
}

/**
 * Follow the progress of a running (or completed) script.
 */
export function useRunningScriptProgress(id, initialProgress=[0.0, 0.0]) {
  const rwsc = useRunningWebsocketClient();
  return useChangingValue(
    useCallback(oldProgress => rwsc("get_progress", {rs_id: id, old_progress: oldProgress}), [id]),
    initialProgress,
  );
}

/**
 * Follow the status of a running (or completed) script.
 */
export function useRunningScriptStatus(id, initialStatus="") {
  const rwsc = useRunningWebsocketClient();
  return useChangingValue(
    useCallback(oldStatus => rwsc("get_status", {rs_id: id, old_status: oldStatus}), [id]),
    initialStatus,
  );
}

/**
 * Wait for the return-code of a script, returning null while the script is
 * still running.
 */
export function useRunningScriptReturnCode(id, initialReturnCode=null) {
  const rwsc = useRunningWebsocketClient();
  
  return useDefinedOnceValue(
    useCallback(() => rwsc("get_return_code", {rs_id: id}), [id]),
    initialReturnCode,
  );
}

/**
 * Wait for the end time of a script, returning null while the script is
 * still running.
 */
export function useRunningScriptEndTime(id, initialEndTime=null) {
  const rwsc = useRunningWebsocketClient();
  
  return useDefinedOnceValue(
    useCallback(() => rwsc("get_end_time", {rs_id: id}), [id]),
    initialEndTime,
  );
}

/**
 * Follow the output of a script. Returns the whole output known so far.
 */
export function useRunningScriptOutput(id) {
  const rwsc = useRunningWebsocketClient();
  
  const [output, setOutput] = useState("");
  function appendOutput(newOutput) {
    setOutput(previousOutput => previousOutput + newOutput);
  }
  
  useEffect(() => {
    const run = {current: true};
    
    (async () => {
      if (id) {
        let length = 0;
        setOutput("");
        while (run.current) {
          const content = await rwsc("get_output", {rs_id: id, after: length});
          if (run.current && content.length > 0) {
            appendOutput(content);
            length += content.length;
          } else {
            return;
          }
        }
      }
    })();
    
    return () => {run.current = false};
  }, [id]);
  
  return output;
}

