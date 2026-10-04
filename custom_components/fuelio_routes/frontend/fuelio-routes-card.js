/* Fuelio Routes card: the trips of one day on a map, with a day and trip selector. */

const LEAFLET_URL = new URL("./leaflet/leaflet-src.esm.js", import.meta.url).href;
const LEAFLET_CSS_URL = new URL("./leaflet/leaflet.css", import.meta.url).href;

const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const TILE_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

const COLORS = ["#1e88e5", "#e53935", "#43a047", "#fb8c00", "#8e24aa", "#00acc1", "#6d4c41", "#d81b60"];
const DEFAULT_MAP_HEIGHT = 400;
const DEFAULT_LIST_HEIGHT = 260;
const DEFAULT_MIN_DISTANCE = 100;
const FEET_PER_METER = 3.28084;
const KM_PER_MILE = 1.609344;
const POLL_INTERVAL_MS = 10000;

const ICONS = {
  prev: "M15.41,16.58L10.83,12L15.41,7.41L14,6L8,12L14,18L15.41,16.58Z",
  next: "M8.59,16.58L13.17,12L8.59,7.41L10,6L16,12L10,18L8.59,16.58Z",
  first: "M18.41,7.41L17,6L11,12L17,18L18.41,16.59L13.83,12L18.41,7.41M12.41,7.41L11,6L5,12L11,18L12.41,16.59L7.83,12L12.41,7.41Z",
  last: "M5.59,7.41L7,6L13,12L7,18L5.59,16.59L10.17,12L5.59,7.41M11.59,7.41L13,6L19,12L13,18L11.59,16.59L16.17,12L11.59,7.41Z",
  refresh:
    "M17.65,6.35C16.2,4.9 14.21,4 12,4A8,8 0 0,0 4,12A8,8 0 0,0 12,20C15.73,20 18.84,17.45 19.73,14H17.65C16.83,16.33 14.61,18 12,18A6,6 0 0,1 6,12A6,6 0 0,1 12,6C13.66,6 15.14,6.69 16.22,7.78L13,11H20V4L17.65,6.35Z",
};

const STYLE = `
  :host { display: block; }
  ha-card { overflow: hidden; display: flex; flex-direction: column; height: 100%; }
  .title { padding: 16px 16px 0; font-size: var(--ha-card-header-font-size, 24px); line-height: 1.3; }
  .toolbar { display: flex; align-items: center; gap: 4px; padding: 8px; }
  button.icon {
    width: 40px; height: 40px; border: 0; border-radius: 50%; background: none; cursor: pointer;
    color: var(--primary-text-color); display: inline-flex; align-items: center; justify-content: center;
  }
  button.icon:hover:not(:disabled) { background: var(--secondary-background-color); }
  button.icon:disabled { opacity: 0.3; cursor: default; }
  button.icon svg { width: 24px; height: 24px; fill: currentColor; }
  button.icon.busy svg { animation: spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .date { position: relative; }
  button.date-button {
    font: inherit; font-size: 15px; padding: 7px 10px; border-radius: 8px; cursor: pointer; white-space: nowrap;
    color: var(--primary-text-color); background: var(--secondary-background-color); border: 1px solid var(--divider-color);
  }
  .calendar {
    position: absolute; top: calc(100% + 4px); left: 0; z-index: 5; padding: 8px; border-radius: 12px;
    background: var(--ha-card-background, var(--card-background-color, #fff)); border: 1px solid var(--divider-color);
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.3);
  }
  .calendar[hidden] { display: none; }
  .calendar .head { display: flex; align-items: center; }
  .calendar .head .month { flex: 1; text-align: center; font-weight: 500; white-space: nowrap; }
  .calendar .head button.icon { width: 32px; height: 32px; }
  .calendar .grid { display: grid; grid-template-columns: repeat(7, 34px); gap: 2px; margin-top: 4px; }
  .calendar .weekday { text-align: center; font-size: 11px; color: var(--secondary-text-color); padding: 2px 0; }
  .calendar button.day {
    height: 34px; border: 0; border-radius: 50%; background: none; font: inherit; font-size: 13px; cursor: pointer;
    color: var(--secondary-text-color); opacity: 0.6;
  }
  .calendar button.day.has {
    opacity: 1; font-weight: 600; color: var(--primary-text-color);
    background: color-mix(in srgb, var(--primary-color, #03a9f4) 22%, transparent);
  }
  .calendar button.day:hover { outline: 2px solid var(--primary-color, #03a9f4); }
  .calendar button.day.today { box-shadow: inset 0 0 0 1px var(--secondary-text-color); }
  .calendar button.day.selected { opacity: 1; background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); }
  .status { flex: 1; min-width: 0; font-size: 12px; color: var(--secondary-text-color); padding: 0 4px; text-align: right; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .map { width: 100%; z-index: 0; background: var(--secondary-background-color); }
  .map.dark .leaflet-tile-pane { filter: invert(1) hue-rotate(180deg) brightness(0.9) contrast(0.9); }
  .trips { display: flex; flex-direction: column; flex: 1 1 auto; min-height: 0; padding: 0 0 8px; overflow-y: auto; }
  .trip {
    flex: 0 0 auto; display: grid; grid-template-columns: 14px auto 1fr auto; align-items: center; column-gap: 10px;
    padding: 8px 16px; cursor: pointer; border: 0; background: none; font: inherit; text-align: left;
    color: var(--primary-text-color); border-left: 4px solid transparent;
  }
  .trip.all { position: sticky; top: 0; z-index: 1; background: var(--ha-card-background, var(--card-background-color, #fff)); }
  .trip:hover { background: var(--secondary-background-color); }
  .trip.selected { background: var(--secondary-background-color); border-left-color: var(--primary-color); }
  .trip.dimmed { opacity: 0.55; }
  .hidden-note { padding: 6px 16px 0; font-size: 12px; color: var(--secondary-text-color); }
  .dot { width: 12px; height: 12px; border-radius: 50%; }
  .time { font-variant-numeric: tabular-nums; white-space: nowrap; font-weight: 500; }
  .label { min-width: 0; }
  .label .name, .label .places { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .label .places { font-size: 12px; color: var(--secondary-text-color); }
  .stats { font-size: 12px; color: var(--secondary-text-color); white-space: nowrap; text-align: right; }
  .message { padding: 16px; color: var(--secondary-text-color); text-align: center; }
  .message.error { color: var(--error-color); }
  .leaflet-container { font: inherit; }
  .endpoint { border-radius: 50%; border: 2px solid #fff; box-shadow: 0 0 3px rgba(0, 0, 0, 0.6); }
`;

let leafletPromise;
function loadLeaflet() {
  leafletPromise = leafletPromise || import(LEAFLET_URL);
  return leafletPromise;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function iconButton(path, label) {
  const button = element("button", "icon");
  button.type = "button";
  button.title = label;
  button.setAttribute("aria-label", label);
  button.innerHTML = `<svg viewBox="0 0 24 24"><path d="${path}"></path></svg>`;
  return button;
}

class FuelioRoutesCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._days = [];
    this._date = null;
    this._trips = [];
    this._selected = null;
    this._cache = new Map();
    this._layers = new Map();
    this._built = false;
    this._started = false;
    this._request = 0;
    this._autoDate = true;
    this._shownDate = null;
    this._shownCount = -1;
    this._importing = false;
    this._pollTimer = null;
  }

  static getStubConfig() {
    return { map_height: DEFAULT_MAP_HEIGHT, list_height: DEFAULT_LIST_HEIGHT };
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "title", selector: { text: {} } },
        {
          name: "map_height",
          selector: { number: { min: 150, max: 1200, step: 10, mode: "box", unit_of_measurement: "px" } },
        },
        {
          name: "min_distance",
          selector: { number: { min: 0, max: 5000, step: 10, mode: "box", unit_of_measurement: "m" } },
        },
        {
          name: "list_height",
          selector: { number: { min: 80, max: 1200, step: 10, mode: "box", unit_of_measurement: "px" } },
        },
        { name: "entry_id", selector: { config_entry: { integration: "fuelio_routes" } } },
      ],
      computeLabel: (schema) =>
        ({
          title: "Title",
          map_height: "Map height",
          list_height: "Maximum height of the trip list",
          min_distance: "Hide trips shorter than (0 shows all)",
          entry_id: "Routes folder (all when empty)",
        })[schema.name],
    };
  }

  setConfig(config) {
    const height = config.map_height === undefined ? DEFAULT_MAP_HEIGHT : Number(config.map_height);
    if (!Number.isFinite(height) || height < 50) {
      throw new Error("map_height must be a number of pixels");
    }
    const listHeight = config.list_height === undefined ? DEFAULT_LIST_HEIGHT : Number(config.list_height);
    if (!Number.isFinite(listHeight) || listHeight < 50) {
      throw new Error("list_height must be a number of pixels");
    }
    const minDistance = config.min_distance === undefined ? DEFAULT_MIN_DISTANCE : Number(config.min_distance);
    if (!Number.isFinite(minDistance) || minDistance < 0) {
      throw new Error("min_distance must be a number of meters");
    }
    const previous = this._config;
    this._config = { ...config, map_height: height, list_height: listHeight, min_distance: minDistance };
    if (this._built) {
      this._applyConfig();
      const filterChanged =
        previous.entry_id !== config.entry_id || previous.min_distance !== this._config.min_distance;
      if (filterChanged && this._started) {
        this._cache.clear();
        this._loadDays(true);
      }
    }
  }

  set hass(hass) {
    const previous = this._hass;
    this._hass = hass;
    if (!this._built) return;
    if (previous && previous.themes?.darkMode !== hass.themes?.darkMode) this._setTiles();
    this._start();
  }

  connectedCallback() {
    this._build();
    if (this._started) {
      // The view was re-opened: pick up trips synchronized in the meantime.
      this._cache.clear();
      this._loadDays(false);
      if (this._map) requestAnimationFrame(() => this._map.invalidateSize());
    } else {
      this._start();
    }
  }

  disconnectedCallback() {
    if (this._resizeObserver) this._resizeObserver.disconnect();
    this._resizeObserver = null;
    clearTimeout(this._pollTimer);
    this._pollTimer = null;
    if (this._built) this._closeCalendar();
  }

  getCardSize() {
    return Math.ceil((this._config.map_height + this._config.list_height) / 50) + 1;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, min_rows: 5 };
  }

  _build() {
    if (!this._built) {
      const root = this.shadowRoot;
      const style = element("style");
      style.textContent = STYLE;
      const css = element("link");
      css.rel = "stylesheet";
      css.href = LEAFLET_CSS_URL;

      this._card = element("ha-card");
      this._titleEl = element("div", "title");
      const toolbar = element("div", "toolbar");
      this._prevButton = iconButton(ICONS.prev, "Previous day with trips");
      this._nextButton = iconButton(ICONS.next, "Next day with trips");
      this._refreshButton = iconButton(ICONS.refresh, "Fetch new routes from Google Drive");
      this._dateWrap = element("div", "date");
      this._dateButton = element("button", "date-button");
      this._dateButton.type = "button";
      this._dateButton.title = "Pick a day";
      this._calendarEl = element("div", "calendar");
      this._calendarEl.hidden = true;
      this._dateWrap.append(this._dateButton, this._calendarEl);
      this._statusEl = element("div", "status");
      toolbar.append(
        this._prevButton,
        this._dateWrap,
        this._nextButton,
        this._statusEl,
        this._refreshButton,
      );
      this._mapEl = element("div", "map");
      this._messageEl = element("div", "message");
      this._tripsEl = element("div", "trips");
      this._card.append(this._titleEl, toolbar, this._mapEl, this._messageEl, this._tripsEl);
      root.append(style, css, this._card);

      this._prevButton.addEventListener("click", () => this._step(-1));
      this._nextButton.addEventListener("click", () => this._step(1));
      this._refreshButton.addEventListener("click", () => this._refresh());
      this._dateButton.addEventListener("click", () =>
        this._calendarEl.hidden ? this._openCalendar() : this._closeCalendar(),
      );
      this._calendarEl.addEventListener("keydown", (event) => {
        if (event.key === "Escape") this._closeCalendar();
      });
      this._built = true;
      this._applyConfig();
    }
    if (!this._resizeObserver && window.ResizeObserver) {
      this._resizeObserver = new ResizeObserver(() => this._map && this._map.invalidateSize());
      this._resizeObserver.observe(this._mapEl);
    }
  }

  _applyConfig() {
    this._titleEl.textContent = this._config.title || "";
    this._titleEl.style.display = this._config.title ? "" : "none";
    this._mapEl.style.height = `${this._config.map_height}px`;
    // The list scrolls on its own so that the map stays in view while browsing trips.
    this._tripsEl.style.maxHeight = `${this._config.list_height}px`;
    if (this._map) {
      this._setTiles();
      this._map.invalidateSize();
    }
  }

  async _start() {
    if (this._started || !this._hass || !this._built || !this.isConnected) return;
    this._started = true;
    try {
      this._leaflet = await loadLeaflet();
      this._createMap();
    } catch (err) {
      this._showMessage(`The map could not be loaded: ${err.message || err}`, true);
      return;
    }
    await this._loadDays(true);
  }

  _createMap() {
    const L = this._leaflet;
    this._map = L.map(this._mapEl, { zoomControl: true, attributionControl: true });
    this._map.attributionControl.setPrefix(false);
    const { latitude, longitude } = this._hass.config || {};
    this._map.setView([latitude || 0, longitude || 0], latitude === undefined ? 2 : 11);
    this._setTiles();
  }

  _setTiles() {
    if (!this._map) return;
    const L = this._leaflet;
    const url = this._config.tile_url || TILE_URL;
    // The default tiles only exist in a light style, so darken them to match a dark theme.
    this._mapEl.classList.toggle("dark", !this._config.tile_url && Boolean(this._hass?.themes?.darkMode));
    if (this._tileLayer && this._tileUrl === url) return;
    if (this._tileLayer) this._tileLayer.remove();
    this._tileUrl = url;
    this._tileLayer = L.tileLayer(url, {
      attribution: this._config.tile_attribution || TILE_ATTRIBUTION,
      maxZoom: 19,
      // Home Assistant pages send no referrer by default, and OpenStreetMap's tile
      // servers reject requests without one.
      referrerPolicy: "strict-origin-when-cross-origin",
    }).addTo(this._map);
  }

  _ws(message) {
    if (this._config.entry_id) message.entry_id = this._config.entry_id;
    if (message.type !== "fuelio_routes/refresh" && this._config.min_distance) {
      message.min_distance = this._config.min_distance;
    }
    return this._hass.callWS(message);
  }

  async _loadDays(resetDate) {
    let result;
    try {
      result = await this._ws({ type: "fuelio_routes/days" });
    } catch (err) {
      this._days = [];
      this._showMessage(this._describeError(err), true);
      this._updateToolbar();
      return;
    }
    this._days = result.days.map((day) => day.date);
    const counts = new Map(result.days.map((day) => [day.date, day.trips]));
    this._counts = counts;
    if (this._built && !this._calendarEl.hidden) this._renderCalendar();
    this._updateSync(result.sync);
    if (resetDate || !this._date) {
      this._date = this._days[this._days.length - 1] || this._today();
    }
    // Redraw only when the shown day changed, so polling during an import leaves the map alone.
    if (this._date !== this._shownDate || (counts.get(this._date) || 0) !== this._shownCount) {
      this._cache.delete(this._date);
      await this._loadDay();
    } else {
      this._updateToolbar();
    }
  }

  _updateSync(sync) {
    clearTimeout(this._pollTimer);
    this._pollTimer = null;
    if (sync && sync.running) {
      this._importing = true;
      this._statusEl.textContent = sync.total
        ? `Importing ${sync.done} / ${sync.total}`
        : "Checking Google Drive…";
      this._schedulePoll(POLL_INTERVAL_MS);
    } else if (this._importing) {
      this._importing = false;
      this._statusEl.textContent = "";
    }
  }

  _schedulePoll(delay) {
    clearTimeout(this._pollTimer);
    if (!this.isConnected) return;
    // Until the user picks a day, keep following the newest one as trips arrive.
    this._pollTimer = setTimeout(() => this._loadDays(this._autoDate), delay);
  }

  _describeError(err) {
    if (err && err.code === "unknown_command") {
      return "The Fuelio Routes integration is not set up.";
    }
    return `Routes could not be loaded: ${(err && err.message) || err}`;
  }

  _today() {
    const timeZone = this._hass.config?.time_zone;
    try {
      return new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).format(
        new Date(),
      );
    } catch (_err) {
      return new Date().toISOString().slice(0, 10);
    }
  }

  _selectDate(date) {
    this._autoDate = false;
    if (date === this._date) return;
    this._date = date;
    this._selected = null;
    this._loadDay();
  }

  _step(direction) {
    const target = this._adjacentDay(direction);
    if (target) this._selectDate(target);
  }

  _adjacentDay(direction) {
    if (direction < 0) {
      for (let i = this._days.length - 1; i >= 0; i--) {
        if (this._days[i] < this._date) return this._days[i];
      }
      return null;
    }
    return this._days.find((day) => day > this._date) || null;
  }

  _updateToolbar() {
    this._dateButton.textContent = this._date ? this._formatDate(this._date) : "—";
    this._prevButton.disabled = !this._adjacentDay(-1);
    this._nextButton.disabled = !this._adjacentDay(1);
  }

  _locale() {
    return this._hass.locale?.language || this._hass.language || undefined;
  }

  _formatDate(date) {
    const [year, month, day] = date.split("-").map(Number);
    const options = { weekday: "short", year: "numeric", month: "short", day: "numeric", timeZone: "UTC" };
    try {
      return new Intl.DateTimeFormat(this._locale(), options).format(Date.UTC(year, month - 1, day));
    } catch (_err) {
      return date;
    }
  }

  _firstWeekday() {
    // 0 = Sunday ... 6 = Saturday
    const setting = this._hass.locale?.first_weekday;
    const fixed = { sunday: 0, monday: 1, tuesday: 2, wednesday: 3, thursday: 4, friday: 5, saturday: 6 }[setting];
    if (fixed !== undefined) return fixed;
    try {
      const locale = new Intl.Locale(this._locale());
      const info = locale.getWeekInfo ? locale.getWeekInfo() : locale.weekInfo;
      if (info) return info.firstDay % 7;
    } catch (_err) {
      // fall through
    }
    return 1;
  }

  _openCalendar() {
    this._calendarMonth = (this._date || this._today()).slice(0, 7);
    this._renderCalendar();
    this._calendarEl.hidden = false;
    this._outsideListener = (event) => {
      if (!event.composedPath().includes(this._dateWrap)) this._closeCalendar();
    };
    document.addEventListener("pointerdown", this._outsideListener, true);
  }

  _closeCalendar() {
    this._calendarEl.hidden = true;
    if (this._outsideListener) document.removeEventListener("pointerdown", this._outsideListener, true);
    this._outsideListener = null;
  }

  _shiftCalendar(months) {
    const [year, month] = this._calendarMonth.split("-").map(Number);
    const shifted = new Date(Date.UTC(year, month - 1 + months, 1));
    this._calendarMonth = `${shifted.getUTCFullYear()}-${String(shifted.getUTCMonth() + 1).padStart(2, "0")}`;
    this._renderCalendar();
  }

  _renderCalendar() {
    const [year, month] = this._calendarMonth.split("-").map(Number);
    const counts = this._counts || new Map();
    const head = element("div", "head");
    const navigation = [
      [ICONS.first, "Previous year", -12],
      [ICONS.prev, "Previous month", -1],
      null,
      [ICONS.next, "Next month", 1],
      [ICONS.last, "Next year", 12],
    ];
    for (const item of navigation) {
      if (item === null) {
        let label = this._calendarMonth;
        try {
          label = new Intl.DateTimeFormat(this._locale(), { month: "long", year: "numeric", timeZone: "UTC" }).format(
            Date.UTC(year, month - 1, 1),
          );
        } catch (_err) {
          // keep the ISO month
        }
        head.append(element("div", "month", label));
        continue;
      }
      const button = iconButton(item[0], item[1]);
      button.addEventListener("click", () => this._shiftCalendar(item[2]));
      head.append(button);
    }

    const grid = element("div", "grid");
    const first = this._firstWeekday();
    for (let i = 0; i < 7; i++) {
      // 2024-01-07 is a Sunday.
      const name = new Intl.DateTimeFormat(this._locale(), { weekday: "narrow", timeZone: "UTC" }).format(
        Date.UTC(2024, 0, 7 + first + i),
      );
      grid.append(element("div", "weekday", name));
    }
    const offset = (new Date(Date.UTC(year, month - 1, 1)).getUTCDay() - first + 7) % 7;
    for (let i = 0; i < offset; i++) grid.append(element("div"));
    const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate();
    const today = this._today();
    for (let day = 1; day <= daysInMonth; day++) {
      const date = `${this._calendarMonth}-${String(day).padStart(2, "0")}`;
      const trips = counts.get(date) || 0;
      const button = element("button", "day", String(day));
      button.type = "button";
      button.classList.toggle("has", trips > 0);
      button.classList.toggle("selected", date === this._date);
      button.classList.toggle("today", date === today);
      button.title = trips ? `${trips} trip${trips === 1 ? "" : "s"}` : "No trips";
      button.addEventListener("click", () => {
        this._closeCalendar();
        this._selectDate(date);
      });
      grid.append(button);
    }
    this._calendarEl.replaceChildren(head, grid);
  }

  async _loadDay() {
    const date = this._date;
    const request = ++this._request;
    this._updateToolbar();
    let day = this._cache.get(date);
    if (!day) {
      try {
        day = await this._ws({ type: "fuelio_routes/day", date });
      } catch (err) {
        if (request === this._request) this._showMessage(this._describeError(err), true);
        return;
      }
      this._cache.set(date, day);
    }
    if (request !== this._request) return;
    const trips = day.trips;
    this._hidden = day.hidden || 0;
    this._shownDate = date;
    this._shownCount = trips.length;
    this._trips = trips;
    if (!trips.some((trip) => trip.id === this._selected)) this._selected = null;
    if (trips.length) {
      this._showMessage("");
    } else if (this._hidden) {
      this._showMessage(`Only trips shorter than ${this._formatThreshold()} were recorded on this day (${this._hidden}).`);
    } else {
      this._showMessage("No trips were recorded on this day.");
    }
    this._renderTrips();
    this._renderMap(true);
  }

  _showMessage(text, isError = false) {
    this._messageEl.textContent = text;
    this._messageEl.style.display = text ? "" : "none";
    this._messageEl.classList.toggle("error", isError);
  }

  async _refresh() {
    if (this._refreshing) return;
    this._refreshing = true;
    this._refreshButton.classList.add("busy");
    this._refreshButton.disabled = true;
    this._statusEl.textContent = "Synchronizing…";
    // A manual synchronization can be a long import too: show its progress meanwhile.
    this._schedulePoll(POLL_INTERVAL_MS / 4);
    try {
      const result = await this._ws({ type: "fuelio_routes/refresh" });
      this._cache.clear();
      await this._loadDays(Boolean(result.new_trips));
      if (result.running) {
        // _loadDays already shows the progress of the import that is under way.
      } else if (!result.success) {
        this._statusEl.textContent = "Synchronization failed, see the log";
      } else if (result.new_trips) {
        this._statusEl.textContent = `${result.new_trips} new trip${result.new_trips === 1 ? "" : "s"}`;
      } else {
        this._statusEl.textContent = "No new trips";
      }
    } catch (err) {
      this._statusEl.textContent = this._describeError(err);
    } finally {
      this._refreshing = false;
      this._refreshButton.classList.remove("busy");
      this._refreshButton.disabled = false;
    }
  }

  _color(index) {
    return COLORS[index % COLORS.length];
  }

  _formatTime(timestamp) {
    const locale = this._hass.locale?.language || this._hass.language || undefined;
    const options = { hour: "2-digit", minute: "2-digit", timeZone: this._hass.config?.time_zone };
    const format = this._hass.locale?.time_format;
    if (format === "12") options.hour12 = true;
    if (format === "24") options.hour12 = false;
    try {
      return new Intl.DateTimeFormat(locale, options).format(new Date(timestamp));
    } catch (_err) {
      return new Date(timestamp).toLocaleTimeString();
    }
  }

  _imperial() {
    return this._hass.config?.unit_system?.length === "mi";
  }

  _formatDistance(meters) {
    const km = meters / 1000;
    return this._imperial() ? `${(km / KM_PER_MILE).toFixed(1)} mi` : `${km.toFixed(1)} km`;
  }

  _formatSpeed(metersPerSecond) {
    const kmh = metersPerSecond * 3.6;
    return this._imperial() ? `${Math.round(kmh / KM_PER_MILE)} mph` : `${Math.round(kmh)} km/h`;
  }

  _formatDuration(milliseconds) {
    const minutes = Math.max(1, Math.round(milliseconds / 60000));
    return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
  }

  _tripStats(trip) {
    const parts = [this._formatDistance(trip.distance)];
    if (trip.end) parts.push(this._formatDuration(trip.end - trip.start));
    if (trip.max_speed) parts.push(`max ${this._formatSpeed(trip.max_speed)}`);
    return parts.join(" · ");
  }

  _renderTrips() {
    this._tripsEl.replaceChildren();
    this._rows = new Map();
    if (this._trips.length > 1) {
      const all = element("button", "trip all");
      all.type = "button";
      const distance = this._trips.reduce((sum, trip) => sum + trip.distance, 0);
      const duration = this._trips.reduce((sum, trip) => sum + (trip.end ? trip.end - trip.start : 0), 0);
      const stats = [this._formatDistance(distance)];
      if (duration) stats.push(this._formatDuration(duration));
      all.append(
        element("span"),
        element("span", "time", "All trips"),
        element("span", "label", `${this._trips.length} trips`),
        element("span", "stats", stats.join(" · ")),
      );
      all.addEventListener("click", () => this._selectTrip(null));
      this._tripsEl.append(all);
      this._rows.set(null, all);
    }
    this._trips.forEach((trip, index) => {
      const row = element("button", "trip");
      row.type = "button";
      const dot = element("span", "dot");
      dot.style.background = this._color(index);
      const time = trip.end
        ? `${this._formatTime(trip.start)} – ${this._formatTime(trip.end)}`
        : this._formatTime(trip.start);
      const label = element("span", "label");
      label.append(element("div", "name", trip.name || "Trip"));
      if (trip.start_name || trip.end_name) {
        const places = [trip.start_name, trip.end_name].filter(Boolean).join(" → ");
        const placesEl = element("div", "places", places);
        placesEl.title = places;
        label.append(placesEl);
      }
      row.append(dot, element("span", "time", time), label, element("span", "stats", this._tripStats(trip)));
      row.addEventListener("click", () => this._selectTrip(this._selected === trip.id ? null : trip.id));
      row.addEventListener("mouseenter", () => this._hover(trip.id));
      row.addEventListener("mouseleave", () => this._hover(null));
      this._tripsEl.append(row);
      this._rows.set(trip.id, row);
    });
    if (this._trips.length && this._hidden) {
      const count = this._hidden;
      const note = `${count} trip${count === 1 ? "" : "s"} shorter than ${this._formatThreshold()} hidden`;
      this._tripsEl.append(element("div", "hidden-note", note));
    }
    this._updateRows();
  }

  _formatThreshold() {
    const meters = this._config.min_distance;
    return this._imperial() ? `${Math.round(meters * FEET_PER_METER)} ft` : `${meters} m`;
  }

  _updateRows() {
    for (const [id, row] of this._rows) {
      row.classList.toggle("selected", id === this._selected && (id !== null || this._trips.length > 1));
      row.classList.toggle("dimmed", this._selected !== null && id !== null && id !== this._selected);
    }
  }

  _selectTrip(id) {
    this._selected = id;
    this._updateRows();
    this._styleLayers();
    this._fit();
    this._revealRow(id);
  }

  _revealRow(id) {
    // Scroll only the list (never the page) to the trip that was picked on the map.
    const row = id === null ? null : this._rows.get(id);
    if (!row) return;
    const list = this._tripsEl;
    const header = this._rows.get(null);
    const top = row.offsetTop - list.offsetTop - (header ? header.offsetHeight : 0);
    const bottom = row.offsetTop - list.offsetTop + row.offsetHeight;
    if (top < list.scrollTop) list.scrollTop = top;
    else if (bottom > list.scrollTop + list.clientHeight) list.scrollTop = bottom - list.clientHeight;
  }

  _hover(id) {
    this._hovered = id;
    this._styleLayers();
  }

  _renderMap(fit) {
    if (!this._map) return;
    const L = this._leaflet;
    if (this._group) this._group.remove();
    this._group = L.layerGroup().addTo(this._map);
    this._layers = new Map();
    this._trips.forEach((trip, index) => {
      if (!trip.points.length) return;
      const color = this._color(index);
      const latLngs = trip.points.map((point) => [point[0], point[1]]);
      const line = L.polyline(latLngs, { color, lineJoin: "round" }).addTo(this._group);
      line.on("click", () => this._selectTrip(trip.id));
      line.on("mousemove", (event) => this._showPointTooltip(trip, line, event));
      line.on("mouseout", () => line.closeTooltip());
      line.bindTooltip("", { sticky: true, direction: "top", opacity: 0.9 });
      const start = this._endpoint(latLngs[0], "#2e7d32", `Start ${this._formatTime(trip.start)}`);
      const end = this._endpoint(
        latLngs[latLngs.length - 1],
        "#c62828",
        trip.end ? `End ${this._formatTime(trip.end)}` : "End",
      );
      this._layers.set(trip.id, { line, start, end });
    });
    this._styleLayers();
    if (fit) this._fit();
  }

  _endpoint(latLng, color, label) {
    const L = this._leaflet;
    const icon = L.divIcon({
      className: "",
      html: `<div class="endpoint" style="width:10px;height:10px;background:${color}"></div>`,
      iconSize: [14, 14],
      iconAnchor: [7, 7],
    });
    return L.marker(latLng, { icon, keyboard: false }).bindTooltip(label, { direction: "top" });
  }

  _showPointTooltip(trip, line, event) {
    let best = null;
    let bestDistance = Infinity;
    for (const point of trip.points) {
      const dLat = point[0] - event.latlng.lat;
      const dLon = (point[1] - event.latlng.lng) * Math.cos((event.latlng.lat * Math.PI) / 180);
      const distance = dLat * dLat + dLon * dLon;
      if (distance < bestDistance) {
        bestDistance = distance;
        best = point;
      }
    }
    if (!best) return;
    const parts = [];
    if (best[2]) parts.push(this._formatTime(best[2]));
    if (best[3] !== null && best[3] !== undefined) parts.push(this._formatSpeed(best[3]));
    if (!parts.length) parts.push(trip.name || "Trip");
    line.setTooltipContent(parts.join(" · "));
  }

  _styleLayers() {
    for (const [id, layer] of this._layers) {
      const selected = this._selected === id;
      const emphasized = selected || this._hovered === id;
      const dimmed = this._selected !== null && !selected;
      layer.line.setStyle({
        weight: emphasized ? 6 : 4,
        opacity: dimmed && !emphasized ? 0.3 : emphasized ? 1 : 0.85,
      });
      if (selected) layer.line.bringToFront();
      for (const marker of [layer.start, layer.end]) {
        if (!dimmed && !this._group.hasLayer(marker)) marker.addTo(this._group);
        if (dimmed && this._group.hasLayer(marker)) this._group.removeLayer(marker);
      }
    }
  }

  _fit() {
    if (!this._map) return;
    const L = this._leaflet;
    const bounds = L.latLngBounds([]);
    for (const [id, layer] of this._layers) {
      if (this._selected === null || this._selected === id) bounds.extend(layer.line.getBounds());
    }
    if (bounds.isValid()) {
      this._map.invalidateSize();
      this._map.fitBounds(bounds, { padding: [24, 24], maxZoom: 17 });
    }
  }
}

function register() {
  if (customElements.get("fuelio-routes-card")) return;
  customElements.define("fuelio-routes-card", FuelioRoutesCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "fuelio-routes-card",
    name: "Fuelio Routes",
    description: "Show the trips recorded by Fuelio on a map, one day at a time.",
    preview: false,
    documentationURL: "https://github.com/majorcs/home-assistant-fuelio-routes",
  });
}

// This module is loaded before the Home Assistant app. In browsers without scoped
// custom element registries the app then replaces window.customElements with a
// polyfill, which does not know elements defined earlier. So wait for the app's own
// root element to be registered: from then on the registry is the final one.
if (customElements.get("home-assistant")) {
  register();
} else {
  const timer = setInterval(() => {
    if (customElements.get("home-assistant")) {
      clearInterval(timer);
      register();
    }
  }, 50);
}
