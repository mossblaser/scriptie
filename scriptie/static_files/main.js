import {html, render, useEffect, useState, useCallback} from "./preact_htm.js";

import {
  useScripts,
  useScript,
  useStartScript,
  killRunningScript,
  deleteRunningScript,
  RunningScriptInfoProvider,
  useRunningScripts,
  useRunningScript,
  useRunningScriptProgress,
  useRunningScriptStatus,
  useRunningScriptReturnCode,
  useRunningScriptEndTime,
  useRunningScriptOutput,
} from "./client.js";

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



/** List of scripts available to start. */
function ScriptList() {
  const [scripts, scriptsFetchError] = useScripts();
  
  if (scriptsFetchError !== null) {
    return html`<span class="ScriptList error">${scriptsFetchError}</span>`;
  }
  
  if (scripts === null) {
    return html`<span class="ScriptList loading">Loading...</span>`;
  }
  
  return html`
    <p>Available scripts:</p>
    <ul class="ScriptList">
      ${
        scripts.map(script => html`
          <li key=${script.script}>
            <a
              href="#/scripts/${encodeURI(script.script)}"
              title="${script.description}"
            >
              ${script.name}
            </a>
          </li>
        `)
      }
    </ul>
    <p><a href="#/running/">Go to running scripts</a></p>
  `;
}

/**
 * An <input type="checkbox"> replacement which always includes a value to be
 * sent back to the server, whether true or false.
 *
 * When checked, sets name to 'value', when unchecked, sets name to 'offValue',
 * refaulting to "true" and "false" respectively.
 */
function Checkbox({initialState=false, value="true", offValue="false", name, ...props}={}) {
  // The hack used is to remove the checkbox's name when unchecked and
  // append a hidden input with that name and the offValue.
  const [state, setState] = useState(initialState);
  const onChange = useCallback(e => setState(e.target.checked), []);
  const checkbox = html`
    <input
      ...${props}
      type="checkbox"
      name="${state ? name : ""}"
      value="${value}"
      onChange=${onChange}
      checked="${state}"
      key="1"
    />
  `;
  
  if (state) {
    return checkbox;
  } else {
    return html`
      ${checkbox}
      <input type="hidden" value="${offValue}" name="${name}" />
    `;
  }
}


/** A <form> element with suitable inputs for the provided script args list. */
function ScriptFormBody({args, ...formProps}) {
  const baseId = useId();
  
  const inputs = args.map((arg, i) => {
    const id = `${baseId}_${i}`;
    const name = `arg${i}`;
    const nameIdProps = {name, id};
    
    // Split an argument with type information (e.g.
    // 'choice:one:two:three') into the type (e.g. 'choice') and
    // type-argument (e.g. 'one:two:three').
    const typeSplit = arg.type.indexOf(":");
    const type = typeSplit >= 0 ? arg.type.substring(0, typeSplit) : arg.type;
    const typeArg = typeSplit >= 0 ? arg.type.substring(typeSplit+1) : null;
    
    let input;
    if (type === "bool") {
      input = html`
        <${Checkbox} ...${nameIdProps} initialState=${typeArg === "true"} />
      `;
    } else if (type === "number" || type === "int" || type === "float") {
      input = html`
        <input
          type="number"
          ...${nameIdProps}
          placeholder="(number)"
          value="${typeArg}"
          step="${type === "int" ? "1" : "any"}"
        />
      `;
    } else if (type === "str") {
      input = html`<input type="text" ...${nameIdProps} value="${typeArg}" />`;
    } else if (type === "multi_line_str") {
      input = html`<textarea ...${nameIdProps}>${typeArg}</textarea>`;
    } else if (type === "password") {
      input = html`<input type="password" ...${nameIdProps} value="${typeArg}" />`;
    } else if (type === "file") {
      const filetypes = (typeArg || "").split(":");
      input = html`
        <input type="file" ...${nameIdProps} accept="${filetypes.join(",")}" />
      `;
    } else if (type === "choice") {
      const options = (typeArg || "").split(":");
      input = html`
        <select ...${nameIdProps}>
          ${options.map((option, i) => html`
            <option value=${option} key="${i}">${option}</option>
          `)}
        </select>
      `;
    } else {
      input = html`
        <input ...${nameIdProps} />
        <span class="unknown-type">(${arg.type})</span>
      `;
    }
    
    return html`
      <div>
        <label for="${id}">${arg.description}</label>
        ${input}
      </div>
    `;
  })
  
  return html`
    <form
      ...${formProps}
    >
      ${inputs}
      <input type="submit"/>
    </form>
  `;
}


/** A form for filling out to start a script running. */
function ScriptForm({script}) {
  const [scriptMetadata, scriptFetchError] = useScript(script);
  if (scriptFetchError !== null) {
    return html`<span class="ScriptForm error">${scriptFetchError}</span>`;
  }
  if (scriptMetadata === null) {
    return html`<span class="ScriptForm loading">Loading...</span>`;
  }
  
  const [formData, setFormData] = useState(null);
  const onSubmit = useCallback(e => {
    setFormData(new FormData(e.target));
    
    e.preventDefault();
    e.stopPropagation();
  }, []);
  const onCancel = useCallback(() => setFormData(null), []);
  
  const [submitResponse, submitError, submitProgress] = useStartScript(script, formData);
  
  let cancelButton = "";
  if (formData !== null && submitResponse === null && submitError === null) {
    cancelButton = html`<button onclick=${onCancel}>Cancel</button>`
  }
  
  const {name, description, args} = scriptMetadata;
  return html`
    <h1>${name}</h1>
    <p>${description}</p>
    <${ScriptFormBody} args=${args} onsubmit=${onSubmit} />
    <p>submitResponse = ${submitResponse}</p>
    <p>submitError = ${submitError}</p>
    <p>submitProgress = ${submitProgress}</p>
    ${cancelButton}
    <p><a href="#/">Go back to list of scripts</a></p>
    <p><a href="#/running/">Go to running scripts</a></p>
  `;
}


function RunningListEntry({id, script, name, start_time, end_time, progress, status, return_code}) {
  const liveProgress = useRunningScriptProgress(id, progress);
  const liveStatus = useRunningScriptStatus(id, status);
  const liveReturnCode = useRunningScriptReturnCode(id, return_code);
  const liveEndTime = useRunningScriptEndTime(id, end_time);;
  
  // XXX
  const liveOutput = useRunningScriptOutput(id);;
  
  const killCb = useCallback(() => killRunningScript(id), [id]);
  const deleteCb = useCallback(() => deleteRunningScript(id), [id]);
  
  return html`
    <h2><a href="#/running/${encodeURI(id)}">${name}</a></h2>
    <p>Running ${start_time} - ${liveEndTime} (exit status ${JSON.stringify(liveReturnCode)})</p>
    <p>Status: '${liveStatus}' (Progress: ${JSON.stringify(liveProgress)})</p>
    <button onClick=${killCb}>Kill</button>
    <button onClick=${deleteCb}>Delete</button>
    <pre>${liveOutput}</pre>
  `;
}


/** A list of currently running (or historically running) scripts. */
function RunningList({script}) {
  const running = useRunningScripts();
  
  if (running === null) {
    return html`<span class="RunningList loading">Loading...</span>`;
  }
  
  return html`
    Showing ${(running || []).length} running scripts:
    <ul class="RunningList">
      ${
        running.map(rs => html`
          <li key=${rs.script}>
            <${RunningListEntry} ...${rs} />
          </li>
        `).reverse()
      }
    </ul>
    <p><a href="#/">Go to script list</a></p>
  `;
}


function Main() {
  const hash = useHash();

  if (hash.match(/^(|#|#\/)$/)) {
    return html`<${ScriptList}/>`;
  }
  
  const scriptMatch = hash.match(/^#\/scripts\/(.+)$/)
  if (scriptMatch) {
    const script = decodeURI(scriptMatch[1]);
    return html`<${ScriptForm} script=${script} />`;
  }
  
  if (hash.match(/^#\/running\/?$/)) {
    return html`<${RunningList}/>`;
  }
  
  return html`Unhandled hash: ${hash}`;
}

render(
  html`
    <${RunningScriptInfoProvider}>
      <${Main}/>
    <//>
  `,
  document.body
);
