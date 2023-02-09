/**
 * Functions and components for working with Scriptie argument forms.
 */

import {
  html,
  useEffect,
  useState,
  useCallback,
} from "./preact_htm.js";


/**
 * Submit a form via XMLHttpRequest, reporting upload progress and the returned
 * text.
 *
 * Takes details of how and where to send the data along with the FormData
 * object to send as arguments. If the using component is detroyed, or if the
 * arguments or become null, any ongoing form submissions will be cancelled.
 *
 * Returns an array [result, error, progress] where:
 *
 * * result is null or the returned text when the form submission returned a
 *   2xx status.
 * * error is null or an error message when something goes wrong or a non-2xx
 *   response is received.
 * * progress is a float between 0.0 and 1.0 indicating form data upload
 *   progress. Note that It may become 1.0 before result is non-null!
 */
export function useFormSubmission(method, url, formData) {
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [progress, setProgress] = useState(0.0);
  
  useEffect(() => {
    // If no form provided, do nothing
    if (!formData) {
      return;
    }
    
    const req = new XMLHttpRequest();
    
    req.onload = () => {
      if (req.status >= 200 && req.status < 300) {
        setResult(req.response)
        setProgress(1.0);  // In case upload progress wasn't computable
      } else {
        setError(`HTTP ${req.status}: ${req.response}`);
      }
    }
    req.onerror = (e) => {
      setError(`Error during submission: ${e}`);
    }
    req.upload.onprogress = e => {
      if (e.lengthComputable) {
        setProgress(e.loaded / e.total);
      }
    }
    
    req.open(method, url);
    req.send(formData);
    
    return () => {
      req.abort();
      setResult(null);
      setError(null);
      setProgress(0.0);
    };
  }, [method, url, formData]);

  return [result, error, progress];
}


/**
 * An <input type="checkbox"> replacement which always includes a value to be
 * sent back to the server (e.g. it can send 'true' and 'false' rather than
 * 'true' and absent).
 *
 * When checked, sets name to 'value', when unchecked, sets name to 'offValue',
 * refaulting to "true" and "false" respectively.
 */
function ExplicitCheckbox({initialState=false, value="true", offValue="false", name, ...props}={}) {
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


/**
 * Creates a form input element of the appropriate kind for a particular
 * Scriptie argument type indicated by 'type'.
 */
export function ArgumentInput({
  type: fullType,
  value,
  inputRef: ref,
  ...props
}) {
  // Split an argument with type information (e.g.  'choice:one:two:three')
  // into the type (e.g. 'choice') and type-argument (e.g. 'one:two:three').
  const typeSplit = fullType.indexOf(":");
  const typeArg = typeSplit >= 0 ? fullType.substring(typeSplit+1) : null;
  const type = typeSplit >= 0 ? fullType.substring(0, typeSplit) : fullType;
  
  // Returns the provided value whenever the 'value' argument is undefined.
  // Otherwise, returns the value argument.
  function d(defaultValue) {
    return typeof value !== "undefined" ? value : defaultValue;
  }
  
  const [isUpload, setIsUpload] = useState(!value);
  const toggleIsUpload = useCallback(() => setIsUpload(state => !state), []);
  
  if (type === "bool") {
    return html`
      <${ExplicitCheckbox} ...${props} initialState=${d(typeArg) === "true"} />
    `;
  } else if (type === "number" || type === "int" || type === "float") {
    return html`
      <input
        type="number"
        ref=${ref}
        ...${props}
        placeholder="(${type === "int" ? "whole " : ""}number)"
        value="${d(typeArg)}"
        step="${type === "int" ? "1" : "any"}"
      />
    `;
  } else if (type === "str") {
    return html`<input type="text" ref=${ref} ...${props} value="${d(typeArg)}" />`;
  } else if (type === "multi_line_str") {
    return html`<textarea ref=${ref} ...${props}>${d(typeArg)}</textarea>`;
  } else if (type === "password") {
    return html`<input type="password" ref=${ref} ...${props} value="${d(typeArg)}" />`;
  } else if (type === "file") {
    const filetypes = (typeArg || "").split(":");
    let input;
    if (isUpload) {
      input = html`
        <input type="file" ref=${ref} ...${props} accept="${filetypes.join(",")}" />
      `;
    } else {
      input = html`
        <input
          type="text"
          ref=${ref}
          ...${props}
          placeholder="(remote filename)"
          value=${value}
        />
      `;
    }
    return html`
      <div class="file">
        ${input}
        <label>
          <input
            type="checkbox"
            checked=${isUpload}
            onChange=${toggleIsUpload}
          />
          Upload
        </label>
      </div>
    `;
  } else if (type === "choice") {
    const options = (typeArg || "").split(":");
    return html`
      <select ref=${ref} ...${props}>
        ${options.map((option, i) => html`
          <option
            value=${option}
            key="${i}"
            selected=${value === option}
          >
            ${option}
          </option>
        `)}
      </select>
    `;
  } else {
    return html`
      <input class="unknown-type" ref=${ref} ...${props} value=${value} />
      <div class="unknown-type-name">Unknown type: <code>${fullType}</code></div>
    `;
  }
}

