// The home manual (manual.html): the house's equipment, its labels and manuals, its maintenance, and Claude to read
// labels, suggest maintenance and answer questions. The API is housemon/manual.py's, through server.py. A module, so
// its names stay its own and don't meet app.js's.
export {};

/**
 * @typedef {{ name: string, value: string }} Spec
 * @typedef {{ id: number, equipment_id: number, kind: string, name: string, mime: string, size: number }} StoredFile
 * @typedef {{ id: number, equipment_id: number | null, title: string, every_months: number, last_done: string,
 *   notes: string, due: string, status: "overdue" | "soon" | "later" | "never" }} Task
 * @typedef {{ id: number, name: string, kind: string, room: string, brand: string, model: string, serial: string,
 *   installed: string, notes: string, specs: Spec[], files: StoredFile[], tasks: Task[] }} Equipment
 * @typedef {{ title: string, every_months: number }} StarterTask
 * @typedef {{ equipment: Equipment[], tasks: Task[], kinds: Record<string, string>,
 *   starter_tasks: Record<string, StarterTask[]>, rooms: string[], claude: { available: boolean, problem: string | null } }} Contents
 * @typedef {{ kind: string, name: string, brand: string, model: string, serial: string, manufactured: string,
 *   specs: Spec[], notes: string }} Label
 * @typedef {{ title: string, every_months: number, why: string }} Suggestion
 * @typedef {{ name: string, mime: string, data: string }} Upload
 */

/** @type {{ contents: Contents | null, adding: boolean, editing: number | null, asking: number | null,
 *   suggestions: Record<number, Suggestion[]>, answers: Record<string, string>, busy: Set<string>, error: string }} */
const state = { contents: null, adding: false, editing: null, asking: null, suggestions: {}, answers: {}, busy: new Set(), error: "" };

const root = /** @type {HTMLElement} */ (document.getElementById("manual"));
const SHOWN_LATER = 5; // tasks due later than soon, shown in Coming up before "and N more"
const PHOTO_TYPES = "image/jpeg,image/png,image/webp,image/gif";

/**
 * An element, its attributes (a function is an event listener, false leaves the attribute off) and children.
 * @param {string} tag
 * @param {Record<string, string | number | boolean | ((event: Event) => void)>} [attrs]
 * @param {...(Node | string | null | false | undefined)} children
 * @returns {HTMLElement}
 */
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (typeof value === "function") node.addEventListener(name, value);
    else if (value === true) node.setAttribute(name, "");
    else if (value !== false) node.setAttribute(name, String(value));
  }
  for (const child of children) if (child !== null && child !== false && child !== undefined) node.append(child);
  return node;
}

/** @param {string} path @param {object} [body] @returns {Promise<any>} */
async function api(path, body) {
  const response = await fetch(`api/manual${path}`, body
    ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : {});
  const json = response.headers.get("Content-Type")?.startsWith("application/json") ? await response.json() : null;
  if (!response.ok) {
    if (response.status === 404 && !json) throw new Error("The home manual is kept by the house monitor on the house's own computer; open it there.");
    throw new Error(json?.error || `The request failed (${response.status}).`);
  }
  return json;
}

/** @param {string} day YYYY-MM-DD @returns {string} */
function dateText(day) {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

/** @param {number} months @returns {string} */
function everyText(months) {
  if (months === 1) return "every month";
  if (months % 12 === 0) return months === 12 ? "every year" : `every ${months / 12} years`;
  return `every ${months} months`;
}

/** @param {string} installed YYYY, YYYY-MM or YYYY-MM-DD @returns {string} */
function installedText(installed) {
  const age = new Date().getFullYear() - Number(installed.slice(0, 4));
  return `installed ${installed}${age > 0 ? ` (${age} year${age === 1 ? "" : "s"} ago)` : ""}`;
}

/** @param {number} bytes */
const sizeText = (bytes) => (bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`);

/** @param {File} file @returns {Promise<Upload>} */
function upload(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({ name: file.name, mime: file.type, data: String(reader.result).split(",")[1] || "" });
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

/** @param {unknown} error */
const message = (error) => (error instanceof Error ? error.message : String(error));

/**
 * Runs an action, showing it busy by `key`, then loads the manual again; an error shows at the top.
 * @param {string} key
 * @param {() => Promise<unknown>} action
 */
async function run(key, action) {
  state.busy.add(key);
  state.error = "";
  render();
  try {
    await action();
    state.contents = await api("");
  } catch (error) {
    state.error = message(error);
  } finally {
    state.busy.delete(key);
    render();
  }
}

/** @param {string} key */
const busy = (key) => state.busy.has(key);

/**
 * A button that runs an action; while it runs it says `doing` and can't be pressed.
 * @param {string} label
 * @param {string} key
 * @param {() => Promise<unknown>} action
 * @param {{ doing?: string, kind?: string }} [options]
 */
function actionButton(label, key, action, options = {}) {
  return el("button", { type: "button", class: options.kind || "", disabled: busy(key), click: () => run(key, action) },
    busy(key) ? options.doing || `${label}…` : label);
}

/**
 * A delete button that asks once more before it deletes.
 * @param {string} label
 * @param {string} key
 * @param {() => Promise<unknown>} action
 */
function deleteButton(label, key, action) {
  const button = el("button", { type: "button", class: "danger" }, label);
  let armed = false;
  button.addEventListener("click", () => {
    if (armed) run(key, action);
    else {
      armed = true;
      button.textContent = "Sure? Delete";
    }
  });
  button.addEventListener("blur", () => {
    armed = false;
    button.textContent = label;
  });
  return button;
}

/** @param {string} label @param {HTMLElement} input */
const field = (label, input) => el("label", { class: "field" }, el("span", {}, label), input);

/** @param {Task} task @returns {[number, string]} the order Coming up lists it in, and its status in words */
function standing(task) {
  if (task.status === "overdue") return [0, `Overdue since ${dateText(task.due)}`];
  if (task.status === "never") return [1, "Not done yet"];
  return [task.status === "soon" ? 2 : 3, `Due ${dateText(task.due)}`];
}

/**
 * One task: its status, title and what it's for, Done today, and where it's managed, Delete.
 * @param {Task} task
 * @param {{ owner?: string, deletable?: boolean }} [options] what it's for, in Coming up
 */
function taskRow(task, options = {}) {
  const [, words] = standing(task);
  return el("li", { class: `task ${task.status}` },
    el("div", { class: "task-main" },
      el("span", { class: "task-title" }, task.title),
      el("span", { class: "task-meta" },
        task.status === "overdue" ? el("span", { class: "icon", "aria-hidden": "true" }, "⚠ ") : null,
        [words, options.owner, everyText(task.every_months), task.last_done && `last done ${dateText(task.last_done)}`].filter(Boolean).join(" · "))),
    el("span", { class: "task-actions" },
      actionButton("Done today", `done:${task.id}`, () => api("/task-done", { id: task.id }), { doing: "Saving…" }),
      options.deletable && deleteButton("Delete", `task-delete:${task.id}`, () => api("/task-delete", { id: task.id }))));
}

// Coming up: every task, the most pressing first

function comingUp() {
  const contents = /** @type {Contents} */ (state.contents);
  const owner = new Map(contents.equipment.map((e) => [e.id, e.name]));
  const tasks = [...contents.tasks, ...contents.equipment.flatMap((e) => e.tasks)]
    .sort((a, b) => standing(a)[0] - standing(b)[0] || a.due.localeCompare(b.due));
  const later = tasks.filter((t) => t.status === "later");
  const shown = [...tasks.filter((t) => t.status !== "later"), ...later.slice(0, SHOWN_LATER)];
  return el("section", { class: "manual-section" },
    el("h2", {}, "Coming up"),
    tasks.length === 0
      ? el("p", { class: "manual-note" }, "No maintenance yet. Add equipment with its usual maintenance, or a task for the house below.")
      : el("ul", { class: "task-list" }, ...shown.map((t) => taskRow(t, { owner: t.equipment_id === null ? "The house" : owner.get(t.equipment_id) }))),
    later.length > SHOWN_LATER && el("p", { class: "manual-note" }, `And ${later.length - SHOWN_LATER} more due later.`));
}

// Equipment

function equipmentSection() {
  const contents = /** @type {Contents} */ (state.contents);
  return el("section", { class: "manual-section" },
    el("div", { class: "section-head" },
      el("h2", {}, "Equipment"),
      !state.adding && el("button", { type: "button", class: "primary", click: () => { state.adding = true; state.editing = null; render(); } }, "Add equipment")),
    state.adding && equipmentForm(null),
    contents.equipment.length === 0
      ? !state.adding && el("p", { class: "manual-note" }, "Nothing yet. Add your furnace, water heater, appliances and the like, from a photo of each one's label.")
      : el("div", { class: "equipment-grid" }, ...contents.equipment.map((item) => (state.editing === item.id ? equipmentForm(item) : equipmentCard(item)))));
}

/** @param {Equipment} item */
function equipmentCard(item) {
  const contents = /** @type {Contents} */ (state.contents);
  const photos = item.files.filter((f) => f.mime !== "application/pdf");
  const documents = item.files.filter((f) => f.mime === "application/pdf");
  const details = [item.brand, item.model && `model ${item.model}`, item.serial && `serial ${item.serial}`].filter(Boolean).join(" · ");
  const claude = contents.claude.available;
  return el("article", { class: "equipment" },
    el("div", { class: "equipment-head" },
      el("h3", {}, item.name),
      el("span", { class: "task-meta" }, [contents.kinds[item.kind] || item.kind, item.room].filter(Boolean).join(" · "))),
    details && el("p", { class: "equipment-detail" }, details),
    item.installed && el("p", { class: "equipment-detail" }, installedText(item.installed)),
    item.specs.length > 0 && el("dl", { class: "specs" }, ...item.specs.flatMap((s) => [el("dt", {}, s.name), el("dd", {}, s.value)])),
    item.notes && el("p", { class: "equipment-notes" }, item.notes),
    photos.length > 0 && el("div", { class: "thumbs" }, ...photos.map((f) => thumb(f, item.name))),
    documents.length > 0 && el("ul", { class: "documents" }, ...documents.map((f) => el("li", {},
      el("a", { href: `api/manual/file?id=${f.id}`, target: "_blank", rel: "noopener" }, f.name),
      el("span", { class: "task-meta" }, `${f.kind} · ${sizeText(f.size)}`),
      deleteButton("Remove", `file:${f.id}`, () => api("/file-delete", { id: f.id }))))),
    el("div", { class: "equipment-actions" },
      fileButton("Add a label photo", `label:${item.id}`, "label", item.id, PHOTO_TYPES),
      fileButton("Add a manual (PDF)", `manual:${item.id}`, "manual", item.id, "application/pdf"),
      claude && actionButton("Suggest maintenance", `suggest:${item.id}`, async () => {
        state.suggestions[item.id] = (await api("/suggest-tasks", { id: item.id })).tasks;
      }, { doing: "Claude is thinking…" }),
      claude && el("button", { type: "button", "aria-expanded": state.asking === item.id ? "true" : "false",
        click: () => { state.asking = state.asking === item.id ? null : item.id; render(); } }, "Ask about it"),
      el("button", { type: "button", click: () => { state.editing = item.id; state.adding = false; render(); } }, "Edit"),
      deleteButton("Delete", `delete:${item.id}`, () => api("/equipment-delete", { id: item.id }))),
    state.asking === item.id && askBox(`item:${item.id}`, `Ask about the ${item.name.toLowerCase()}: its manual and label go with the question`, item.id),
    suggestionsBox(item),
    el("h4", {}, "Maintenance"),
    item.tasks.length > 0
      ? el("ul", { class: "task-list" }, ...item.tasks.map((t) => taskRow(t, { deletable: true })))
      : el("p", { class: "manual-note" }, "None yet."),
    taskForm(item.id));
}

/** @param {StoredFile} file @param {string} name */
function thumb(file, name) {
  return el("figure", { class: "thumb" },
    el("a", { href: `api/manual/file?id=${file.id}`, target: "_blank", rel: "noopener" },
      el("img", { src: `api/manual/file?id=${file.id}`, alt: `${file.kind === "label" ? "Label" : "Photo"} of the ${name.toLowerCase()}`, loading: "lazy" })),
    el("figcaption", {}, deleteButton("Remove", `file:${file.id}`, () => api("/file-delete", { id: file.id }))));
}

/**
 * A button that picks a file and stores it for a piece of equipment.
 * @param {string} label
 * @param {string} key
 * @param {string} kind
 * @param {number} id
 * @param {string} accept
 */
function fileButton(label, key, kind, id, accept) {
  const input = /** @type {HTMLInputElement} */ (el("input", { type: "file", accept, hidden: true }));
  input.addEventListener("change", () => {
    const file = input.files?.[0];
    if (file) run(key, async () => api("/file", { ...(await upload(file)), equipment_id: id, kind }));
  });
  return el("span", { class: "file-button" }, input,
    el("button", { type: "button", disabled: busy(key), click: () => input.click() }, busy(key) ? "Saving…" : label));
}

/** @param {Equipment} item Claude's suggested maintenance for it, each to add or leave */
function suggestionsBox(item) {
  const suggestions = state.suggestions[item.id];
  if (!suggestions) return null;
  const boxes = suggestions.map(() => /** @type {HTMLInputElement} */ (el("input", { type: "checkbox", checked: true })));
  return el("div", { class: "suggestions" },
    el("p", { class: "manual-note" }, suggestions.length ? "Claude suggests (untick any to leave out):" : "Claude has nothing to add."),
    ...suggestions.map((s, i) => el("label", { class: "check" }, boxes[i],
      el("span", {}, el("strong", {}, s.title), ` · ${everyText(s.every_months)}`, el("span", { class: "task-meta" }, ` ${s.why}`)))),
    el("div", { class: "form-actions" },
      suggestions.length > 0 && actionButton("Add the ticked tasks", `add-suggested:${item.id}`, async () => {
        for (const [i, s] of suggestions.entries()) {
          if (boxes[i].checked) await api("/task", { fields: { title: s.title, every_months: s.every_months, equipment_id: item.id, notes: s.why } });
        }
        delete state.suggestions[item.id];
      }, { kind: "primary", doing: "Adding…" }),
      el("button", { type: "button", click: () => { delete state.suggestions[item.id]; render(); } }, "Dismiss")));
}

/** @param {number | null} equipmentId a small form to add a task, for it or (null) the house as a whole */
function taskForm(equipmentId) {
  const title = /** @type {HTMLInputElement} */ (el("input", { type: "text", placeholder: "e.g. Replace the filter", required: true }));
  const every = /** @type {HTMLInputElement} */ (el("input", { type: "number", min: 1, max: 240, value: 12, required: true }));
  const last = /** @type {HTMLInputElement} */ (el("input", { type: "date" }));
  const key = `task:${equipmentId ?? "house"}`;
  const form = el("form", { class: "task-form" },
    field("New task", title), field("Every (months)", every), field("Last done", last),
    el("button", { type: "submit", disabled: busy(key) }, busy(key) ? "Adding…" : "Add task"));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    run(key, () => api("/task", { fields: { title: title.value, every_months: Number(every.value), equipment_id: equipmentId, last_done: last.value } }));
  });
  return form;
}

/** @param {Equipment | null} item the form to change it, or (null) to add one, which can start from its label's photo */
function equipmentForm(item) {
  const contents = /** @type {Contents} */ (state.contents);
  /** @param {string} value @param {string} [placeholder] */
  const text = (value, placeholder = "") => /** @type {HTMLInputElement} */ (el("input", { type: "text", value, placeholder }));
  const name = text(item?.name || "", "e.g. Furnace");
  const kind = /** @type {HTMLSelectElement} */ (el("select", {}, ...Object.entries(contents.kinds).map(([id, label]) =>
    el("option", { value: id, selected: (item?.kind || "furnace") === id }, label))));
  const room = text(item?.room || "", "where it is");
  room.setAttribute("list", "manual-rooms");
  const brand = text(item?.brand || "");
  const model = text(item?.model || "");
  const serial = text(item?.serial || "");
  const installed = text(item?.installed || "", "YYYY or YYYY-MM");
  const notes = /** @type {HTMLTextAreaElement} */ (el("textarea", { rows: 2 }, item?.notes || ""));
  /** @type {Spec[]} */
  let specs = item ? [...item.specs] : [];
  const specsList = el("dl", { class: "specs" });
  const showSpecs = () => specsList.replaceChildren(...specs.flatMap((s) => [el("dt", {}, s.name), el("dd", {}, s.value)]));
  showSpecs();
  const starter = /** @type {HTMLInputElement} */ (el("input", { type: "checkbox", checked: true }));
  const starterList = el("span", { class: "task-meta" });
  const showStarter = () => {
    const tasks = contents.starter_tasks[kind.value] || [];
    starterList.textContent = tasks.length ? ` (${tasks.map((t) => `${t.title.toLowerCase()} ${everyText(t.every_months)}`).join("; ")})` : " (none for this kind)";
  };
  kind.addEventListener("change", showStarter);
  showStarter();
  /** @type {Upload | null} */
  let labelPhoto = null;
  const status = el("p", { class: "manual-note", role: "status" });
  const photo = /** @type {HTMLInputElement} */ (el("input", { type: "file", accept: PHOTO_TYPES, hidden: true }));
  photo.addEventListener("change", async () => {
    const file = photo.files?.[0];
    if (!file) return;
    labelPhoto = await upload(file);
    if (!contents.claude.available) {
      status.textContent = `${file.name} will be saved with it. Fill in what the label says.`;
      return;
    }
    status.textContent = "Claude is reading the label…";
    try {
      /** @type {Label} */
      const label = await api("/read-label", labelPhoto);
      name.value ||= label.name;
      if (label.kind in contents.kinds) kind.value = label.kind;
      brand.value = label.brand || brand.value;
      model.value = label.model || model.value;
      serial.value = label.serial || serial.value;
      installed.value = label.manufactured || installed.value;
      if (label.notes) notes.value = [notes.value, label.notes].filter(Boolean).join("\n");
      if (label.specs.length) specs = label.specs;
      showSpecs();
      showStarter();
      status.textContent = "Read by Claude: check each field before saving. The photo is saved with it.";
    } catch (error) {
      status.textContent = message(error);
    }
  });
  const key = item ? `edit:${item.id}` : "add";
  const form = el("form", { class: "equipment-form" },
    el("h3", {}, item ? `Change the ${item.name.toLowerCase()}` : "Add equipment"),
    !item && el("div", { class: "form-actions" }, photo,
      el("button", { type: "button", click: () => photo.click() }, contents.claude.available ? "Start from a photo of its label" : "Add a photo of its label"),
      !contents.claude.available && el("span", { class: "task-meta" }, contents.claude.problem || "")),
    status,
    el("div", { class: "form-grid" },
      field("Name", name), field("Kind", kind), field("Where", room), field("Brand", brand),
      field("Model", model), field("Serial", serial), field("Installed or made", installed)),
    el("datalist", { id: "manual-rooms" }, ...contents.rooms.map((r) => el("option", { value: r }))),
    field("Notes", notes),
    specsList,
    !item && el("label", { class: "check" }, starter, el("span", {}, "Add the usual maintenance", starterList)),
    el("div", { class: "form-actions" },
      el("button", { type: "submit", class: "primary", disabled: busy(key) }, busy(key) ? "Saving…" : item ? "Save" : "Add"),
      el("button", { type: "button", click: () => { state.adding = false; state.editing = null; render(); } }, "Cancel")));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const fields = { name: name.value, kind: kind.value, room: room.value, brand: brand.value, model: model.value,
      serial: serial.value, installed: installed.value, notes: notes.value, specs };
    run(key, async () => {
      const saved = await api("/equipment", { id: item ? item.id : null, fields, starter: !item && starter.checked });
      if (labelPhoto) await api("/file", { ...labelPhoto, equipment_id: saved.id, kind: "label" });
      state.adding = false;
      state.editing = null;
    });
  });
  return form;
}

// Questions for Claude

/**
 * A question box: about one piece of equipment (its manual and label go with the question) or the whole house.
 * @param {string} key
 * @param {string} placeholder
 * @param {number | null} equipmentId
 */
function askBox(key, placeholder, equipmentId) {
  const question = /** @type {HTMLTextAreaElement} */ (el("textarea", { rows: 2, placeholder, "aria-label": placeholder }));
  const form = el("form", { class: "ask" }, question,
    el("div", { class: "form-actions" }, el("button", { type: "submit", class: "primary", disabled: busy(key) }, busy(key) ? "Claude is thinking…" : "Ask")),
    state.answers[key] && el("div", { class: "answer", role: "status" }, state.answers[key]));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const asked = question.value.trim();
    if (!asked) return;
    run(key, async () => {
      const { answer } = await api("/ask", { question: asked, equipment_id: equipmentId });
      state.answers[key] = `${asked}\n\n${answer}`;
    });
  });
  return form;
}

function houseSection() {
  const contents = /** @type {Contents} */ (state.contents);
  return el("section", { class: "manual-section" },
    el("h2", {}, "House tasks"),
    el("p", { class: "manual-note" }, "Maintenance for the house as a whole, like the gutters or the roof."),
    contents.tasks.length > 0 && el("ul", { class: "task-list" }, ...contents.tasks.map((t) => taskRow(t, { deletable: true }))),
    taskForm(null));
}

function askSection() {
  const contents = /** @type {Contents} */ (state.contents);
  return el("section", { class: "manual-section" },
    el("h2", {}, "Ask about your home"),
    contents.claude.available
      ? askBox("house", "e.g. What filter does the furnace take? What's due this winter?", null)
      : el("p", { class: "manual-note" }, contents.claude.problem || "Claude isn't set up."));
}

function render() {
  if (!state.contents) {
    root.replaceChildren(el("p", { class: state.error ? "manual-error" : "manual-note", role: "status" }, state.error || "Loading…"));
    return;
  }
  root.replaceChildren(
    state.error ? el("p", { class: "manual-error", role: "alert" }, el("span", { class: "icon", "aria-hidden": "true" }, "⚠ "), state.error) : "",
    comingUp(), equipmentSection(), houseSection(), askSection());
}

api("").then(
  (contents) => { state.contents = contents; render(); },
  (error) => { state.error = message(error); render(); });
