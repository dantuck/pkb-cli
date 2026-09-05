// ---------- markdown rendering ----------
// Small hand-rolled renderer (headers, bold/italic, inline code, fenced
// code blocks, links, lists, blockquotes, paragraphs) -- enough for the
// plain notes this tool stores, without pulling in a library and breaking
// the single-static-directory, no-build-step design.

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderInline(s) {
  s = escapeHtml(s);
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, "<em>$1</em>");
  // Only render as a link if the URL is a safe scheme -- blocks javascript:
  // and other script-executing URIs from a synced/pasted entry body.
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, text, url) =>
    /^(https?:|mailto:|\/|#)/i.test(url) ? `<a href="${url}" target="_blank" rel="noopener">${text}</a>` : m);
  return s;
}

function renderMarkdown(md) {
  const lines = String(md || "").split("\n");
  let html = "";
  let inCode = false;
  let listType = null;
  let inBlockquote = false;
  let paragraph = [];

  const flushParagraph = () => {
    if (paragraph.length) {
      html += `<p>${renderInline(paragraph.join(" "))}</p>`;
      paragraph = [];
    }
  };
  const closeList = () => {
    if (listType) { html += `</${listType}>`; listType = null; }
  };
  const closeBlockquote = () => {
    if (inBlockquote) { html += "</blockquote>"; inBlockquote = false; }
  };

  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      if (!inCode) { flushParagraph(); closeList(); closeBlockquote(); html += "<pre><code>"; }
      else { html += "</code></pre>"; }
      inCode = !inCode;
      continue;
    }
    if (inCode) { html += escapeHtml(line) + "\n"; continue; }

    const headerMatch = line.match(/^(#{1,6})\s+(.*)$/);
    if (headerMatch) {
      flushParagraph(); closeList(); closeBlockquote();
      const level = headerMatch[1].length;
      html += `<h${level}>${renderInline(headerMatch[2])}</h${level}>`;
      continue;
    }

    const ulMatch = line.match(/^\s*[-*]\s+(.*)$/);
    const olMatch = line.match(/^\s*\d+\.\s+(.*)$/);
    if (ulMatch || olMatch) {
      flushParagraph(); closeBlockquote();
      const wantType = ulMatch ? "ul" : "ol";
      if (listType !== wantType) { closeList(); html += `<${wantType}>`; listType = wantType; }
      html += `<li>${renderInline((ulMatch || olMatch)[1])}</li>`;
      continue;
    }
    closeList();

    const bqMatch = line.match(/^>\s?(.*)$/);
    if (bqMatch) {
      flushParagraph();
      if (!inBlockquote) { html += "<blockquote>"; inBlockquote = true; }
      html += `<p>${renderInline(bqMatch[1])}</p>`;
      continue;
    }
    closeBlockquote();

    if (!line.trim()) { flushParagraph(); continue; }
    paragraph.push(line.trim());
  }
  flushParagraph();
  closeList();
  closeBlockquote();
  if (inCode) html += "</code></pre>";
  return html;
}

// "3h ago" / "2d ago" for recent items, falling back to a plain date once
// it's far enough back that relative phrasing stops being useful.
function formatRelative(iso) {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 14) return `${days}d ago`;
  return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

// Generic JSON fetch wrapper -- every mutating call (tag/link edits, capture,
// inbox actions, todo actions, admin actions) is a fetch with an optional
// JSON body and a JSON (or empty) response, so they all go through this one
// helper instead of each hand-rolling the same method/headers/body/parse
// boilerplate.
async function apiFetch(url, method, body) {
  const opts = { method };
  if (body !== undefined) {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  let data = null;
  try { data = await res.json(); } catch {}
  return { ok: res.ok, data };
}

const CORE_TYPES = ["tutorial", "how-to", "reference", "explanation"];
const ALL_TYPES = ["tutorial", "how-to", "reference", "explanation", "journal", "inbox", "source"];
const THEME_KEY = "kb-theme";

document.addEventListener("alpine:init", () => {
  Alpine.data("kbApp", () => ({
    CORE_TYPES,
    ALL_TYPES,

    // ---------- status ----------
    statusText: "checking server...",
    statusClass: "",

    // ---------- theme ----------
    theme: "system",
    get themeLabel() {
      return this.theme === "light" ? "☀ light" : this.theme === "dark" ? "☾ dark" : "◐ system";
    },
    applyThemeAttr(mode) {
      if (mode === "light" || mode === "dark") document.documentElement.setAttribute("data-theme", mode);
      else document.documentElement.removeAttribute("data-theme");
    },
    toggleTheme() {
      this.theme = this.theme === "system" ? "light" : this.theme === "light" ? "dark" : "system";
      try { localStorage.setItem(THEME_KEY, this.theme); } catch {}
    },

    // ---------- capture ----------
    captureText: "",
    captureMsg: "",
    captureBusy: false,
    async capture() {
      const text = this.captureText.trim();
      if (!text) return;
      this.captureBusy = true;
      this.captureMsg = "adding...";
      try {
        const { ok, data } = await apiFetch("/api/capture", "POST", { text });
        if (!ok) throw new Error();
        this.captureText = "";
        this.captureMsg = `added -> ${data.path}`;
        // Reload rather than insert a synthetic card: today's capture lands
        // in the same journal entry as any earlier capture from today (see
        // journal_append in scripts/kb), so the feed needs the merged entry
        // the server now has, not a second one-off card standing in for it.
        if (!this.searchQuery.trim() && !this.activeTag) this.loadFeed(false);
      } catch {
        this.captureMsg = "failed to add -- try again";
      } finally {
        this.captureBusy = false;
      }
    },
    captureKeydown(e) {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) this.capture();
    },

    // ---------- feed / search ----------
    feedItems: [],
    feedCursor: null,
    feedEmptyMsg: "",
    activeTag: null,
    activeType: null,
    searchQuery: "",
    _searchDebounce: null,

    itemStamp(item) {
      return `${formatRelative(item.created)} · ${item.type}`;
    },

    async loadFeed(append) {
      const url = new URL("/api/feed", location.origin);
      url.searchParams.set("limit", "30");
      if (append && this.feedCursor) url.searchParams.set("before", this.feedCursor);
      if (this.activeTag) url.searchParams.set("tag", this.activeTag);
      if (this.activeType) url.searchParams.set("type", this.activeType);
      const res = await fetch(url);
      if (!res.ok) {
        if (!append) this.feedItems = [];
        this.feedEmptyMsg = "couldn't load feed";
        this.feedCursor = null;
        return;
      }
      const data = await res.json();
      this.feedItems = append ? this.feedItems.concat(data.items) : data.items;
      this.feedCursor = data.next;
      this.feedEmptyMsg = this.feedItems.length ? "" : "Nothing here yet.";
    },

    async runSearch(query) {
      const url = new URL("/api/search", location.origin);
      url.searchParams.set("q", query);
      url.searchParams.set("all", "1");
      if (this.activeType) url.searchParams.set("type", this.activeType);
      this.feedCursor = null;
      const res = await fetch(url);
      if (!res.ok) {
        this.feedItems = [];
        this.feedEmptyMsg = "search failed";
        return;
      }
      const data = await res.json();
      this.feedItems = data.items;
      this.feedEmptyMsg = data.items.length ? "" : "no results";
    },

    onSearchInput() {
      clearTimeout(this._searchDebounce);
      const query = this.searchQuery.trim();
      this._searchDebounce = setTimeout(() => {
        if (!query) {
          this.loadFeed(false);
        } else {
          this.activeTag = null;
          this.runSearch(query);
        }
      }, 250);
    },

    setTagFilter(tag) {
      this.activeTag = tag;
      this.searchQuery = "";
      this.feedCursor = null;
      this.loadFeed(false);
    },
    clearTagFilter() {
      this.activeTag = null;
      this.feedCursor = null;
      this.loadFeed(false);
    },

    // Type chips are a browsing aid -- a quick way to see everything of one
    // kind without knowing a tag or search term for it -- so picking one
    // clears any active search, same as clicking a tag does.
    setTypeFilter(type) {
      this.activeType = this.activeType === type ? null : type;
      this.searchQuery = "";
      this.feedCursor = null;
      this.loadFeed(false);
    },

    // ---------- inbox ----------
    inboxOpen: false,
    inboxItems: [],

    async loadInbox() {
      const { ok, data } = await apiFetch("/api/inbox", "GET");
      if (ok) this.inboxItems = data.items.map((it) => ({ ...it, promoteType: CORE_TYPES[0] }));
    },
    async resolveInboxItem(item, action) {
      const urls = {
        promote: [`/api/inbox/${encodeURIComponent(item.id)}/promote`, "POST", { type: item.promoteType }],
        redirect: [`/api/inbox/${encodeURIComponent(item.id)}/redirect`, "POST", undefined],
        discard: [`/api/inbox/${encodeURIComponent(item.id)}`, "DELETE", undefined],
      };
      const { ok } = await apiFetch(...urls[action]);
      if (ok) {
        this.inboxItems = this.inboxItems.filter((i) => i.id !== item.id);
        if (!this.activeTag && !this.searchQuery.trim()) this.loadFeed(false);
      }
    },

    // ---------- todos ----------
    todosOpen: false,
    todoItems: [],
    todoAdd: { title: "", priority: "2", type: "task" },

    async loadTodos() {
      const { ok, data } = await apiFetch("/api/todo", "GET");
      if (ok) this.todoItems = data.items.map((it) => ({ ...it, detailOpen: false, detailText: null, comment: "" }));
    },
    async todoAction(id, action, body) {
      const { ok, data } = await apiFetch(`/api/todo/${encodeURIComponent(id)}/${action}`, "POST", body);
      return ok ? data : null;
    },
    async toggleTodoDetail(item) {
      if (!item.detailOpen && item.detailText === null) {
        const r = await this.todoAction(item.id, "show");
        if (r) item.detailText = r.message;
      }
      item.detailOpen = !item.detailOpen;
    },
    async submitTodoComment(item) {
      const text = item.comment.trim();
      if (!text) return;
      item.comment = "";
      await this.todoAction(item.id, "comment", { text });
    },
    async closeTodo(item) {
      const r = await this.todoAction(item.id, "close");
      if (r) this.todoItems = this.todoItems.filter((i) => i.id !== item.id);
    },
    async addTodo() {
      const title = this.todoAdd.title.trim();
      if (!title) return;
      const { ok } = await apiFetch("/api/todo", "POST",
        { title, priority: this.todoAdd.priority, type: this.todoAdd.type });
      if (ok) {
        this.todoAdd.title = "";
        this.loadTodos();
      }
    },

    // ---------- entry modal ----------
    // Clicking a card opens the entry here: full content, unclamped, with an
    // Edit toggle that swaps the same panel into the tag/link/body editor.
    // `modalItem` points at the underlying feed-card object, so a save made
    // in the modal (title/tags) is reflected on the card automatically
    // through Alpine's reactivity -- no manual DOM syncing needed.
    modalOpen: false,
    modalEntryId: null,
    modalItem: null,
    modalStamp: "",
    modalLoading: false,
    modalLoadError: "",
    modalEditing: false,
    modalTitle: "",
    modalBody: "",
    modalTags: [],
    modalLinks: [],
    modalTagInput: "",
    modalLinkInput: "",
    modalLinkError: "",
    modalPreview: false,
    modalSaving: false,
    modalSaveMsg: "",
    modalSaveErr: false,

    // Typeahead over every tag/entry already in the repo, loaded once at boot
    // (see loadTagIndex/loadEntryIndex) -- cheap for a personal KB's scale,
    // and means the tag/link editors never block on a network round-trip
    // while you're typing.
    allTags: [],
    allEntries: [],
    modalTagHighlight: -1,
    modalLinkHighlight: -1,
    tagInputFocused: false,
    linkInputFocused: false,
    tagSuggestUp: false,
    linkSuggestUp: false,

    // The tag/link rows sit inside the modal's own scrolling container, which
    // clips anything positioned below its bottom edge -- so a dropdown near
    // the end of the form has nowhere visible to open downward into. Flip it
    // above the input instead whenever there isn't enough room below.
    positionSuggest(which) {
      const el = this.$refs[`${which}SuggestWrap`];
      if (!el) return;
      const spaceBelow = window.innerHeight - el.getBoundingClientRect().bottom;
      this[`${which}SuggestUp`] = spaceBelow < 220;
    },

    get modalTagSuggestions() {
      const q = this.modalTagInput.trim().toLowerCase();
      if (!q) return [];
      return this.allTags
        .filter((t) => t.toLowerCase().includes(q) && !this.modalTags.includes(t))
        .slice(0, 8);
    },
    get modalLinkSuggestions() {
      const q = this.modalLinkInput.trim().toLowerCase();
      if (!q) return [];
      return this.allEntries
        .filter((e) => e.id !== this.modalEntryId && !this.modalLinks.includes(e.id)
          && (e.id.toLowerCase().includes(q) || (e.title || "").toLowerCase().includes(q)))
        .slice(0, 8);
    },

    // Shared by the tag and link inputs: arrow keys move a highlighted
    // suggestion, Enter picks it (or falls back to adding whatever was
    // typed), Escape drops the suggestion list without bubbling up to the
    // modal's own Escape-closes handler.
    _suggestKeydown(e, suggestions, highlightProp, pick, fallback) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        this[highlightProp] = Math.min(this[highlightProp] + 1, suggestions.length - 1);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        this[highlightProp] = Math.max(this[highlightProp] - 1, -1);
      } else if (e.key === "Enter") {
        e.preventDefault();
        const chosen = suggestions[this[highlightProp]];
        chosen ? pick(chosen) : fallback();
      } else if (e.key === "Escape" && suggestions.length) {
        e.stopPropagation();
        this[highlightProp] = -1;
      }
    },
    tagInputKeydown(e) {
      this._suggestKeydown(e, this.modalTagSuggestions, "modalTagHighlight",
        (t) => this.selectTagSuggestion(t), () => this.addModalTag());
    },
    linkInputKeydown(e) {
      this._suggestKeydown(e, this.modalLinkSuggestions, "modalLinkHighlight",
        (entry) => this.selectLinkSuggestion(entry), () => this.addModalLink());
    },
    selectTagSuggestion(tag) {
      this.modalTagInput = tag;
      this.modalTagHighlight = -1;
      this.addModalTag();
    },
    selectLinkSuggestion(entry) {
      this.modalLinkInput = entry.id;
      this.modalLinkHighlight = -1;
      this.addModalLink();
    },
    async loadTagIndex() {
      const { ok, data } = await apiFetch("/api/tags", "GET");
      if (ok) this.allTags = data.tags;
    },
    async loadEntryIndex() {
      const { ok, data } = await apiFetch("/api/entries", "GET");
      if (ok) this.allEntries = data.items;
    },

    get modalViewHtml() {
      // Entries conventionally open with a "# Heading" line, and the
      // frontmatter title is already shown in the modal header -- so any
      // leading H1 is redundant here regardless of its exact wording.
      let shown = String(this.modalBody || "").replace(/^\n+/, "");
      const firstLine = shown.split("\n", 1)[0];
      if (/^#\s+\S/.test(firstLine)) shown = shown.slice(firstLine.length).replace(/^\n+/, "");
      return renderMarkdown(shown);
    },
    get modalPreviewHtml() {
      return renderMarkdown(this.modalBody);
    },

    async openEntryModal(item) {
      this.modalItem = item;
      this.modalEntryId = item.id;
      this.modalStamp = item.title || item.id;
      this.modalEditing = false;
      this.modalLoading = true;
      this.modalLoadError = "";
      this.modalTagInput = "";
      this.modalLinkInput = "";
      this.modalLinkError = "";
      this.modalSaveMsg = "";
      this.modalSaveErr = false;
      this.modalPreview = false;
      this.modalTagHighlight = -1;
      this.modalLinkHighlight = -1;
      this.modalOpen = true;
      try {
        const res = await fetch(`/api/entries/${encodeURIComponent(item.id)}`);
        if (!res.ok) throw new Error();
        const data = await res.json();
        this.modalTags = data.frontmatter.tags || [];
        this.modalLinks = data.frontmatter.links || [];
        this.modalTitle = data.frontmatter.title || "";
        this.modalBody = data.body || "";
      } catch {
        this.modalLoadError = "couldn't load";
      } finally {
        this.modalLoading = false;
      }
    },
    closeEntryModal() {
      this.modalOpen = false;
      this.modalItem = null;
      this.modalEntryId = null;
    },
    showModalEdit() {
      this.modalPreview = false;
      this.modalSaveMsg = "";
      this.modalEditing = true;
    },
    showModalView() {
      this.modalEditing = false;
    },
    async saveModalEntry() {
      this.modalSaving = true;
      this.modalSaveErr = false;
      this.modalSaveMsg = "saving…";
      const { ok, data } = await apiFetch(`/api/entries/${encodeURIComponent(this.modalEntryId)}`, "PATCH", {
        title: this.modalTitle, body: this.modalBody,
      });
      this.modalSaving = false;
      if (ok) {
        if (data.title != null) this.modalTitle = data.title;
        if (data.body != null) this.modalBody = data.body;
        if (this.modalItem) this.modalItem.title = this.modalTitle || this.modalEntryId;
        this.modalStamp = this.modalTitle || this.modalEntryId;
        this.showModalView();
      } else {
        this.modalSaveErr = true;
        this.modalSaveMsg = (data && data.error) || "save failed";
      }
    },
    async patchModalEntry(field, body) {
      const { data } = await apiFetch(`/api/entries/${encodeURIComponent(this.modalEntryId)}/${field}`, "PATCH", body);
      return data;
    },
    async addModalTag() {
      const val = this.modalTagInput.trim();
      if (!val) return;
      this.modalTagInput = "";
      this.modalTagHighlight = -1;
      const r = await this.patchModalEntry("tags", { add: [val] });
      if (r && r.tags) {
        this.modalTags = r.tags;
        if (this.modalItem) this.modalItem.tags = r.tags;
        if (!this.allTags.includes(val)) this.allTags = [...this.allTags, val].sort();
      }
    },
    async removeModalTag(tag) {
      const r = await this.patchModalEntry("tags", { rm: [tag] });
      if (r && r.tags) {
        this.modalTags = r.tags;
        if (this.modalItem) this.modalItem.tags = r.tags;
      }
    },
    async addModalLink() {
      const val = this.modalLinkInput.trim();
      if (!val) return;
      this.modalLinkError = "";
      this.modalLinkHighlight = -1;
      const r = await this.patchModalEntry("links", { add: [val] });
      if (r && r.links) { this.modalLinkInput = ""; this.modalLinks = r.links; }
      else if (r && r.error) { this.modalLinkError = r.error; }
    },
    async removeModalLink(link) {
      const r = await this.patchModalEntry("links", { rm: [link] });
      if (r && r.links) this.modalLinks = r.links;
    },

    // ---------- admin ----------
    adminOpen: false,
    adminMsg: "",
    adminOutput: "",
    adminSyncSource: "all",

    async adminRun(label, fn) {
      this.adminMsg = `${label}...`;
      this.adminOutput = "";
      try {
        const data = await fn();
        this.adminMsg = data.ok === false ? `${label}: issues found` : `${label}: done`;
        if (data.output) {
          this.adminOutput = data.output;
        } else if (data.note) {
          this.adminMsg += ` -- ${data.note}`;
        } else if (data.error) {
          this.adminMsg = `${label}: ${data.error}`;
        }
      } catch {
        this.adminMsg = `${label} failed`;
      }
    },
    adminReindex() {
      this.adminRun("reindex", () => apiFetch("/api/index", "POST", { full: false }).then((r) => r.data));
    },
    adminValidate() {
      this.adminRun("validate", () => apiFetch("/api/validate", "POST").then((r) => r.data));
    },
    adminDoctor() {
      this.adminRun("doctor", () => apiFetch("/api/doctor", "GET").then((r) => r.data));
    },
    adminSync() {
      this.adminRun("sync", () => apiFetch("/api/sync", "POST", { source: this.adminSyncSource }).then((r) => r.data))
        .then(() => { this.loadTagIndex(); this.loadEntryIndex(); });
    },

    // ---------- global keydown ----------
    onKeydown(e) {
      if (e.key !== "Escape") return;
      if (this.adminOpen) this.adminOpen = false;
      if (this.modalOpen) this.closeEntryModal();
    },

    // ---------- boot ----------
    init() {
      try { this.theme = localStorage.getItem(THEME_KEY) || "system"; } catch {}
      this.applyThemeAttr(this.theme);
      this.$watch("theme", (mode) => this.applyThemeAttr(mode));

      // Lock the page's own scroll behind the modal so dragging the feed
      // underneath doesn't fight with scrolling the modal's content.
      this.$watch("modalOpen", (open) => {
        document.body.style.overflow = open ? "hidden" : "";
      });

      fetch("/api/health")
        .then((r) => r.json())
        .then((data) => {
          this.statusText = "server up -- data repo: " + data.root;
          this.statusClass = "ok";
        })
        .catch(() => {
          this.statusText = "couldn't reach /api/health";
          this.statusClass = "err";
        });

      this.loadFeed(false);
      this.loadInbox();
      this.loadTodos();
      this.loadTagIndex();
      this.loadEntryIndex();
    },
  }));
});
