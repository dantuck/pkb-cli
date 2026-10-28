// Grows a textarea to fit its content, so editing an entry never means
// scrolling a small fixed-height box inside the page.
function autosizeTextarea(el) {
  if (!el) return;
  el.style.height = "auto";
  el.style.height = el.scrollHeight + 2 + "px";
}

// ---------- visual (in-place) editing ----------
// The rendered entry itself becomes contenteditable. On every edit the DOM is
// serialised back to markdown (htmlToMarkdown) -- the inverse of
// renderMarkdown for the syntax it supports. Because the renderer is lossy
// (e.g. it flattens nested lists), richEditable() first checks that an
// entry survives a render -> serialise round trip; entries that don't are
// edited as source instead, so visual editing can never silently rewrite them.
const RICH_BLOCK_TAGS = new Set(["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "blockquote", "pre"]);

function mdMark(inner, mark) {
  const m = inner.match(/^(\s*)([\s\S]*?)(\s*)$/);
  return m[2] ? `${m[1]}${mark}${m[2]}${mark}${m[3]}` : inner;
}

function htmlInline(node) {
  let s = "";
  for (const n of node.childNodes) {
    if (n.nodeType === 3) { s += n.textContent.replace(/ /g, " "); continue; }
    if (n.nodeType !== 1) continue;
    const t = n.tagName.toLowerCase();
    if (t === "br") { s += " "; continue; }
    if (t === "ul" || t === "ol") continue;  // nested lists are handled by the list serialiser
    const inner = htmlInline(n);
    if (t === "strong" || t === "b") s += mdMark(inner, "**");
    else if (t === "em" || t === "i") s += mdMark(inner, "*");
    else if (t === "code") s += mdMark(inner, "`");
    else if (t === "a") s += n.getAttribute("href") ? `[${inner}](${n.getAttribute("href")})` : inner;
    else if (t === "img") s += `![${n.getAttribute("alt") || ""}](${n.getAttribute("data-src") || n.getAttribute("src") || ""})`;
    else s += inner;
  }
  return s;
}

function htmlBlocks(node) {
  const out = [];
  let loose = "";  // inline content sitting directly in the container
  const flush = () => { const t = loose.trim(); if (t) out.push(t); loose = ""; };
  for (const n of node.childNodes) {
    if (n.nodeType === 3) { loose += n.textContent.replace(/ /g, " "); continue; }
    if (n.nodeType !== 1) continue;
    const t = n.tagName.toLowerCase();
    if (!RICH_BLOCK_TAGS.has(t)) { loose += htmlInline({ childNodes: [n] }); continue; }
    flush();
    if (/^h[1-6]$/.test(t)) {
      const text = htmlInline(n).trim();
      if (text) out.push("#".repeat(Number(t[1])) + " " + text);
    } else if (t === "ul" || t === "ol") {
      const items = [...n.children].filter((c) => c.tagName.toLowerCase() === "li");
      const lines = items.map((li, i) => `${t === "ul" ? "-" : i + 1 + "."} ${htmlInline(li).trim()}`).filter((l) => l.replace(/^(-|\d+\.)\s*/, ""));
      if (lines.length) out.push(lines.join("\n"));
    } else if (t === "blockquote") {
      const lines = htmlBlocks(n).flatMap((b) => b.split("\n")).map((l) => "> " + l);
      if (lines.length) out.push(lines.join("\n"));
    } else if (t === "pre") {
      out.push("```\n" + n.textContent.replace(/\n$/, "") + "\n```");
    } else if (t === "li") {
      const text = htmlInline(n).trim();
      if (text) out.push(text);
    } else {  // p, div: may contain further blocks (Chrome nests divs)
      out.push(...htmlBlocks(n));
    }
  }
  flush();
  return out;
}

function htmlToMarkdown(root) {
  return htmlBlocks(root).join("\n\n");
}

// Splits off the "# Title" first line the feed already shows as the row title,
// so the in-place editor shows just the body and the line is re-attached on save.
function splitLeadingH1(body) {
  const shown = String(body || "").replace(/^\n+/, "");
  const first = shown.split("\n", 1)[0];
  if (/^#\s+\S/.test(first)) return { h1: first, rest: shown.slice(first.length).replace(/^\n+/, "") };
  return { h1: "", rest: shown };
}

function richEditable(body) {
  const { rest } = splitLeadingH1(body);
  if (/^[ \t]+([-*]|\d+\.)\s/m.test(rest) || /\t/.test(rest)) return false;  // nested lists / tabs
  // The renderer joins consecutive plain lines into one paragraph (that is how
  // tables and hard-wrapped text would be flattened on save), so any entry
  // with two plain lines in a row is edited as source instead.
  let inFence = false;
  let prevKind = "";
  for (const line of rest.split("\n")) {
    if (line.trim().startsWith("```")) { inFence = !inFence; prevKind = ""; continue; }
    if (inFence) continue;
    const kind = !line.trim() ? "" : /^#{1,6}\s/.test(line) ? "h" : /^\s*([-*]|\d+\.)\s/.test(line) ? "li" : /^>/.test(line) ? "q" : "p";
    if (kind === "p" && prevKind) return false;
    prevKind = kind;
  }
  const el = document.createElement("div");
  el.innerHTML = renderMarkdown(rest);
  const squash = (s) => s.replace(/\s+/g, " ").trim();
  return squash(htmlToMarkdown(el)) === squash(rest);
}

// Which toolbar formats apply at the caret/selection -- drives the buttons'
// pressed state. Visual mode reads the DOM; source mode reads the markdown.
function richActiveFormats(el) {
  const sel = getSelection();
  if (!el || !sel.rangeCount || !el.contains(sel.anchorNode)) return {};
  const node = sel.anchorNode.nodeType === 1 ? sel.anchorNode : sel.anchorNode.parentElement;
  const has = (s) => { const x = node.closest(s); return !!x && el.contains(x); };
  return {
    // Headings report "bold" from their own styling, so only real <b> counts there.
    // At a bare caret, the surrounding markup says what the text *is*; the
    // command state says what the next typed character *will be* (e.g. after
    // clicking Bold inside a bold word) -- the latter is what the button shows.
    bold: has("h1,h2,h3,h4,h5,h6") ? has("b,strong") : document.queryCommandState("bold"),
    italic: document.queryCommandState("italic"),
    code: has("code"), link: has("a"),
    heading: has("h1,h2,h3,h4,h5,h6"), quote: has("blockquote"), codeblock: has("pre"),
    ul: has("ul"), ol: has("ol"),
  };
}

function sourceActiveFormats(ta) {
  if (!ta) return {};
  const { selectionStart: s, selectionEnd: e, value: v } = ta;
  const ls = v.lastIndexOf("\n", s - 1) + 1;
  let le = v.indexOf("\n", e);
  if (le === -1) le = v.length;
  const line = v.slice(ls, le);
  const lo = s - ls, hi = e - ls;
  // an inline span is "active" when the selection sits inside a match
  const inside = (re) => [...line.matchAll(re)].some((m) => lo >= m.index && hi <= m.index + m[0].length);
  const fences = (v.slice(0, ls).match(/^\s*```/gm) || []).length;
  return {
    bold: inside(/\*\*[^*]+\*\*/g), italic: inside(/(?<!\*)\*[^*]+\*(?!\*)/g),
    code: inside(/`[^`]+`/g), link: inside(/\[[^\]]*\]\([^)]*\)/g),
    heading: /^#{1,6}\s/.test(line), quote: /^>/.test(line), codeblock: fences % 2 === 1,
    ul: /^\s*[-*]\s/.test(line), ol: /^\s*\d+\.\s/.test(line),
  };
}

// execCommand-based formatting for the contenteditable surface.
function richApply(el, id) {
  if (!el) return;
  el.focus();
  const sel = getSelection();
  const within = (sel.anchorNode && (sel.anchorNode.nodeType === 1 ? sel.anchorNode : sel.anchorNode.parentElement)) || el;
  const closest = (sel_) => within.closest(sel_) && el.contains(within.closest(sel_)) ? within.closest(sel_) : null;
  document.execCommand("styleWithCSS", false, false);
  switch (id) {
    case "bold": return document.execCommand("bold");
    case "italic": return document.execCommand("italic");
    case "ul": return document.execCommand("insertUnorderedList");
    case "ol": return document.execCommand("insertOrderedList");
    case "heading": return document.execCommand("formatBlock", false, closest("h1,h2,h3,h4,h5,h6") ? "p" : "h2");
    case "quote": return closest("blockquote") ? document.execCommand("outdent") : document.execCommand("formatBlock", false, "blockquote");
    case "codeblock": return document.execCommand("formatBlock", false, closest("pre") ? "p" : "pre");
    case "code": {
      const code = closest("code");
      if (code) { code.replaceWith(...code.childNodes); el.dispatchEvent(new Event("input", { bubbles: true })); return; }
      if (sel.isCollapsed) return;
      return document.execCommand("insertHTML", false, `<code>${escapeHtml(sel.toString())}</code>`);
    }
    case "link": {
      if (closest("a")) return document.execCommand("unlink");
      const url = (window.prompt("Link URL") || "").trim();
      if (url && SAFE_URL_RE.test(url)) document.execCommand("createLink", false, url);
      return;
    }
  }
}

// "## " / "- " / "1. " / "> " typed at the start of an empty-so-far paragraph
// turns it into that block, like most visual markdown editors.
function richBlockShortcut(el, e) {
  const sel = getSelection();
  if (!sel.rangeCount || !sel.isCollapsed) return false;
  const node = sel.anchorNode.nodeType === 1 ? sel.anchorNode : sel.anchorNode.parentElement;
  const block = node.closest("p,div");
  if (!block || !el.contains(block) || block === el) return false;
  const r = sel.getRangeAt(0).cloneRange();
  r.setStart(block, 0);
  const typed = r.toString();
  const m = typed.match(/^(#{1,6}|[-*]|\d+\.|>)$/);
  if (!m) return false;
  e.preventDefault();
  // Backspace over the marker (rather than deleting the range) so the caret
  // stays valid inside the now-empty block for the command below.
  for (let i = 0; i < typed.length; i++) document.execCommand("delete");
  const mark = m[1];
  if (mark[0] === "#") document.execCommand("formatBlock", false, "h" + mark.length);
  else if (mark === ">") document.execCommand("formatBlock", false, "blockquote");
  else document.execCommand(/\d/.test(mark) ? "insertOrderedList" : "insertUnorderedList");
  return true;
}

// ---------- markdown editing helpers ----------
// Toolbar/shortcut actions on a plain <textarea>. Edits go through
// execCommand("insertText") where possible so the browser's native undo stack
// keeps working (a bare `ta.value = ...` would wipe it); the resulting input
// event is also what keeps x-model in sync. Only syntax the renderer below
// understands is offered.
const MD_TOOLS = [
  { id: "heading", label: "H", title: "Heading" },
  { id: "bold", label: "B", title: "Bold (⌘B)" },
  { id: "italic", label: "I", title: "Italic (⌘I)" },
  { id: "code", label: "</>", title: "Inline code (⌘E)" },
  { id: "codeblock", label: "{ }", title: "Code block" },
  { id: "quote", label: "❝", title: "Quote" },
  { id: "ul", label: "•", title: "Bulleted list" },
  { id: "ol", label: "1.", title: "Numbered list" },
  { id: "link", label: "Link", title: "Link (⌘K)" },
];
const MD_URL_RE = /^https?:\/\/\S+$/;

function mdReplace(ta, start, end, text, selStart, selEnd) {
  ta.focus();
  ta.setSelectionRange(start, end);
  const ok = text === ""
    ? (start === end || document.execCommand("delete"))
    : document.execCommand("insertText", false, text);
  if (!ok) {
    ta.setRangeText(text, start, end, "end");
    ta.dispatchEvent(new Event("input", { bubbles: true }));
  }
  ta.setSelectionRange(selStart, selEnd);
}

function mdWrap(ta, pre, suf, placeholder) {
  const { selectionStart: s, selectionEnd: e, value: v } = ta;
  const sel = v.slice(s, e);
  const wrapped = s >= pre.length && v.slice(s - pre.length, s) === pre && v.slice(e, e + suf.length) === suf;
  // `*` is also half of `**`: don't mistake bold for italic when toggling.
  const isBoldNotItalic = pre === "*" && v[s - 2] === "*" && v[e + 1] === "*";
  if (wrapped && !isBoldNotItalic) {
    mdReplace(ta, s - pre.length, e + suf.length, sel, s - pre.length, s - pre.length + sel.length);
    return;
  }
  const text = sel || placeholder;
  mdReplace(ta, s, e, pre + text + suf, s + pre.length, s + pre.length + text.length);
}

// Rewrites every line touched by the selection.
function mdLines(ta, fn) {
  const { selectionStart: s, selectionEnd: e, value: v } = ta;
  const ls = v.lastIndexOf("\n", s - 1) + 1;
  let le = e > s && v[e - 1] === "\n" ? e - 1 : v.indexOf("\n", e);
  if (le === -1) le = v.length;
  const out = fn(v.slice(ls, le).split("\n")).join("\n");
  mdReplace(ta, ls, le, out, ls, ls + out.length);
}

function mdPrefixLines(ta, prefix, stripRe) {
  mdLines(ta, (lines) => {
    const all = lines.every((l) => l.startsWith(prefix));
    return lines.map((l) => (all ? l.slice(prefix.length) : prefix + l.replace(stripRe, "")));
  });
}

function mdApply(ta, id) {
  if (!ta) return;
  const { selectionStart: s, selectionEnd: e, value: v } = ta;
  switch (id) {
    case "bold": return mdWrap(ta, "**", "**", "bold text");
    case "italic": return mdWrap(ta, "*", "*", "italic text");
    case "code": return mdWrap(ta, "`", "`", "code");
    case "heading": return mdPrefixLines(ta, "## ", /^#{1,6}\s+/);
    case "quote": return mdPrefixLines(ta, "> ", /^>\s?/);
    case "ul": return mdPrefixLines(ta, "- ", /^\s*(?:[-*]|\d+\.)\s+/);
    case "ol":
      return mdLines(ta, (lines) => {
        const all = lines.every((l) => /^\d+\.\s/.test(l));
        return lines.map((l, i) => (all ? l.replace(/^\d+\.\s/, "") : `${i + 1}. ${l.replace(/^\s*(?:[-*]|\d+\.)\s+/, "")}`));
      });
    case "codeblock": {
      const sel = v.slice(s, e);
      const lead = s > 0 && v[s - 1] !== "\n" ? "\n" : "";
      const text = `${lead}\`\`\`\n${sel}\n\`\`\``;
      const caret = s + lead.length + 4;
      return mdReplace(ta, s, e, text, caret, caret + sel.length);
    }
    case "link": {
      const sel = v.slice(s, e);
      if (MD_URL_RE.test(sel)) {
        return mdReplace(ta, s, e, `[link text](${sel})`, s + 1, s + 10);
      }
      const text = sel || "link text";
      const from = s + text.length + 3;
      return mdReplace(ta, s, e, `[${text}](url)`, from, from + 3);
    }
  }
}

// Enter on a list/quote line continues it (an empty item ends it); the usual
// Cmd/Ctrl shortcuts map onto the toolbar actions.
function mdKeydown(e) {
  const ta = e.target;
  const mod = e.metaKey || e.ctrlKey;
  if (mod && !e.shiftKey && !e.altKey) {
    const id = { b: "bold", i: "italic", e: "code", k: "link" }[e.key.toLowerCase()];
    if (id) { e.preventDefault(); mdApply(ta, id); }
    return;
  }
  if (e.key !== "Enter" || e.shiftKey || e.altKey || mod || e.isComposing) return;
  const { selectionStart: pos, selectionEnd: end, value: v } = ta;
  if (pos !== end) return;
  const ls = v.lastIndexOf("\n", pos - 1) + 1;
  let le = v.indexOf("\n", pos);
  if (le === -1) le = v.length;
  if (pos !== le) return;  // only continue from the end of a line
  const m = v.slice(ls, le).match(/^(\s*)([-*]|\d+\.|>)\s(.*)$/);
  if (!m) return;
  e.preventDefault();
  if (!m[3].trim()) {
    mdReplace(ta, ls, le, "", ls, ls);
    return;
  }
  const marker = /^\d+\.$/.test(m[2]) ? `${parseInt(m[2], 10) + 1}.` : m[2];
  const text = `\n${m[1]}${marker} `;
  mdReplace(ta, pos, pos, text, pos + text.length, pos + text.length);
}

// Pasting a URL over selected text makes it a link instead of replacing it.
function mdPaste(e) {
  const ta = e.target;
  const url = (e.clipboardData?.getData("text/plain") || "").trim();
  const { selectionStart: s, selectionEnd: en, value: v } = ta;
  if (s === en || !MD_URL_RE.test(url)) return;
  e.preventDefault();
  const text = `[${v.slice(s, en)}](${url})`;
  mdReplace(ta, s, en, text, s + text.length, s + text.length);
}

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

const SAFE_URL_RE = /^(https?:|mailto:|\/|#)/i;
const VIDEO_EXT_RE = /\.(mp4|webm|ogg|ogv|mov|m4v)(\?.*)?$/i;

// `mediaCounter`, when passed, gets `.count` incremented once per embedded
// image/video -- lets a caller (flushParagraph, for gallery-grid grouping)
// learn how much media a string contains without a second pass over the
// rendered HTML.
function renderInline(s, mediaCounter) {
  s = escapeHtml(s);
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, "<em>$1</em>");
  // Images (and, by extension, videos) first -- `![alt](src)` would otherwise
  // also match the plain-link pattern below (missing only its leading "!"),
  // rendering a broken link instead of the media. Same safe-scheme allowlist
  // as links, for the same reason: blocks javascript: and other
  // script-executing URIs from a synced/pasted entry body. A synced video
  // attachment (e.g. from usememos) uses this same `![]()` syntax -- see
  // attachments_markdown in sync_memos.py -- distinguished here purely by
  // file extension so it plays inline instead of rendering as a static image.
  s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (m, alt, url) => {
    if (!SAFE_URL_RE.test(url)) return m;
    if (mediaCounter) mediaCounter.count++;
    // No `controls` on the inline video -- a click anywhere on it needs to
    // open the lightbox (see handleMediaClick in app.js), and native
    // controls swallow clicks meant for that (a click on the play/seek bar
    // is retargeted to the <video> host through the UA's closed shadow
    // root, so it's indistinguishable from a click on the frame itself).
    // The lightbox's own <video> gets `controls` for real playback.
    return VIDEO_EXT_RE.test(url)
      ? `<span class="media-thumb media-video" data-src="${url}" data-type="video"><video src="${url}" preload="metadata" muted playsinline></video><span class="play-badge" aria-hidden="true">&#9654;</span></span>`
      : `<img class="media-thumb" data-type="image" src="${url}" alt="${alt}" loading="lazy">`;
  });
  // Only render as a link if the URL is a safe scheme -- blocks javascript:
  // and other script-executing URIs from a synced/pasted entry body.
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, text, url) =>
    SAFE_URL_RE.test(url) ? `<a href="${url}" target="_blank" rel="noopener">${text}</a>` : m);
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
      const mediaCounter = { count: 0 };
      const inner = renderInline(paragraph.join(" "), mediaCounter);
      // A paragraph made up of several attached images/videos (usememos
      // syncs multiple attachments as consecutive `![]()` lines within one
      // paragraph -- see attachments_markdown in sync_memos.py) reads better
      // as a gallery grid than stacked full-width, so it gets its own
      // wrapper instead of a plain <p>; see .media-grid in style.css.
      html += mediaCounter.count > 1 ? `<div class="media-grid">${inner}</div>` : `<p>${inner}</p>`;
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
const ADMIN_STATUS_POLL_MS = 120000;

// One source of truth for the new-entry form's blank state, used both for
// the initial Alpine state and to reset the form each time it reopens --
// so the two can't drift out of sync with each other.
function newEntryDefaults() {
  return {
    newEntryType: CORE_TYPES[0],
    newEntryTitle: "",
    newEntryBody: "",
    newEntryTags: [],
    newEntryLinks: [],
    newEntryTagInput: "",
    newEntryLinkInput: "",
    newEntryPreview: false,
    newEntrySaving: false,
    newEntrySaveMsg: "",
    newEntrySaveErr: false,
    newEntryTagHighlight: -1,
    newEntryLinkHighlight: -1,
    newEntryTagInputFocused: false,
    newEntryLinkInputFocused: false,
    newEntryTagSuggestUp: false,
    newEntryLinkSuggestUp: false,
  };
}

const CAPTURE_SCROLL_RANGE = 100; // px of scroll over which the capture box collapses

document.addEventListener("alpine:init", () => {
  Alpine.data("kbApp", () => ({
    CORE_TYPES,
    ALL_TYPES,
    autosizeTextarea,
    MD_TOOLS,
    mdActive: {},
    richApply,
    mdApply,
    mdKeydown,
    mdPaste,

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
    // The capture box lives in the pinned header and shrinks as the page
    // scrolls: scroll position drives a 0..1 CSS variable (--p) so the box
    // tracks the scroll one-to-one. Focusing it (captureOpen) pins it open.
    captureOpen: false,
    captureCompact: false,
    updateCaptureProgress() {
      // Wide layouts start collapsed to a one-line bar (see style.css); the
      // box then only expands while focused, not as a function of scroll.
      const wide = window.matchMedia("(min-width: 820px)").matches;
      const p = this.captureOpen ? 0 : wide ? 1 : Math.min(1, Math.max(0, window.scrollY / CAPTURE_SCROLL_RANGE));
      document.getElementById("site-header")?.style.setProperty("--p", p);
      this.captureCompact = p > 0.5;
    },
    captureFocusOut(e) {
      if (e.currentTarget.contains(e.relatedTarget)) return;
      if (!this.captureText.trim() && !this.captureBusy) this.captureOpen = false;
    },
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
        this.captureOpen = false;
        document.activeElement?.blur();
        // Reload rather than insert a synthetic card: today's capture lands
        // in the same journal entry as any earlier capture from today (see
        // journal_append in cli.py), so the feed needs the merged entry
        // the server now has, not a second one-off card standing in for it.
        if (!this.activeTag) this.loadFeed(false);
      } catch {
        this.captureMsg = "failed to add -- try again";
      } finally {
        this.captureBusy = false;
      }
    },
    captureKeydown(e) {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) this.capture();
    },

    // ---------- feed ----------
    feedItems: [],
    feedCursor: null,
    feedEmptyMsg: "",
    activeTag: null,
    activeType: null,

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
      if (!append && this.entryInline) {
        // An expanded row survives a refresh (sync events, new captures) as
        // long as its entry is still listed; otherwise its row is about to
        // be removed, so hand the panel back to <body> first.
        const again = data.items.find((i) => i.id === this.modalEntryId);
        if (again) this.modalItem = again; else this.closeEntryModal();
      }
      this.feedItems = append ? this.feedItems.concat(data.items) : data.items;
      this.feedCursor = data.next;
      this.feedEmptyMsg = this.feedItems.length ? "" : "Nothing here yet.";
    },

    setTagFilter(tag) {
      this.activeTag = tag;
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
    // clears any active tag filter, same as clicking a tag does.
    setTypeFilter(type) {
      this.activeType = this.activeType === type ? null : type;
      this.feedCursor = null;
      this.loadFeed(false);
    },

    // ---------- inbox ----------
    inboxDrawerOpen: false,
    dockTodos: false,
    inboxItems: [],
    // Distinguishes "haven't heard back yet" from "heard back, zero items" --
    // the pill badge needs both so it can stay invisible-but-space-reserving
    // before the first load, then switch to a visible empty-state dash once
    // it actually knows the count is zero rather than just not-yet-known.
    inboxLoaded: false,

    async loadInbox() {
      const { ok, data } = await apiFetch("/api/inbox", "GET");
      if (ok) this.inboxItems = data.items.map((it) => ({ ...it, promoteType: CORE_TYPES[0] }));
      this.inboxLoaded = true;
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
        if (!this.activeTag) this.loadFeed(false);
      }
    },

    // ---------- todos ----------
    // Priority is the one piece of data that should read at a glance --
    // collapse bd's 0-4 scale down to the three-tier high/mid/low language
    // the rest of the UI already has color tokens for (danger/accent/muted),
    // rather than inventing a fourth palette just for todos.
    todoDrawerOpen: false,
    todoItems: [],
    todoAdd: { title: "", priority: "2", type: "task", description: "", labels: "", due: "" },
    todoAddExpanded: false,
    todoLoaded: false,
    todoShowClosed: false,
    todoFilter: "",
    todoTypeFilter: "",
    todoLabelFilter: "",

    priorityLevel(p) {
      const n = Number(p);
      return n <= 1 ? "high" : n === 2 ? "mid" : "low";
    },

    async loadTodos() {
      const { ok, data } = await apiFetch(`/api/todo${this.todoShowClosed ? "?all=1" : ""}`, "GET");
      // bd's own JSON calls this field issue_type; every other bit of kb web
      // UI code (badges, the type select, filtering) reads item.type, so
      // normalize it once here instead of touching every read site.
      if (ok) this.todoItems = data.items.map((i) => ({ ...i, type: i.issue_type }));
      this.todoLoaded = true;
    },
    async toggleTodoShowClosed() {
      this.todoShowClosed = !this.todoShowClosed;
      await this.loadTodos();
    },
    todoAllLabels() {
      const set = new Set();
      for (const i of this.todoItems) for (const l of i.labels || []) set.add(l);
      return [...set].sort();
    },
    setTodoTypeFilter(t) { this.todoTypeFilter = this.todoTypeFilter === t ? "" : t; },
    setTodoLabelFilter(l) { this.todoLabelFilter = this.todoLabelFilter === l ? "" : l; },
    filteredTodos() {
      const q = this.todoFilter.trim().toLowerCase();
      const matches = this.todoItems.filter((i) => {
        if (this.todoTypeFilter && i.type !== this.todoTypeFilter) return false;
        if (this.todoLabelFilter && !(i.labels || []).includes(this.todoLabelFilter)) return false;
        if (!q) return true;
        return i.title.toLowerCase().includes(q) ||
          i.id.toLowerCase().includes(q) ||
          (i.labels || []).some((l) => l.toLowerCase().includes(q));
      });
      // bd already sorts by priority; within a priority, soonest due first and
      // undated last. Array.sort is stable, so ties keep bd's order.
      const key = (i) => [Number(i.priority), i.due_at ? new Date(i.due_at).getTime() : Infinity];
      return matches.map((i) => [key(i), i]).sort(([a], [b]) => a[0] - b[0] || (a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0)).map(([, i]) => i);
    },
    async todoAction(id, action, body) {
      const { ok, data } = await apiFetch(`/api/todo/${encodeURIComponent(id)}/${action}`, "POST", body);
      return ok ? data : null;
    },
    async closeTodo(item) {
      const r = await this.todoAction(item.id, "close");
      if (r) {
        if (this.todoShowClosed) item.status = "closed";
        else this.todoItems = this.todoItems.filter((i) => i.id !== item.id);
      }
    },
    // The row X closes a todo, so it needs a second click on the same button.
    confirmCloseTodo(item) {
      if (this.todoConfirmId !== item.id) {
        this.todoConfirmId = item.id;
        setTimeout(() => { if (this.todoConfirmId === item.id) this.todoConfirmId = ""; }, 4000);
        return;
      }
      this.todoConfirmId = "";
      this.closeTodo(item);
    },
    async reopenTodo(item) {
      const r = await this.todoAction(item.id, "reopen");
      if (r) item.status = "open";
    },
    async addTodo() {
      const title = this.todoAdd.title.trim();
      if (!title) return;
      const { ok } = await apiFetch("/api/todo", "POST", {
        title, priority: this.todoAdd.priority, type: this.todoAdd.type,
        description: this.todoAdd.description.trim() || undefined,
        labels: this.todoAdd.labels.trim() || undefined,
        due: this.todoAdd.due.trim() || undefined,
      });
      if (ok) {
        this.todoAdd.title = "";
        this.todoAdd.due = "";
        this.todoAdd.description = "";
        this.todoAdd.labels = "";
        this.todoAddExpanded = false;
        this.loadTodos();
      }
    },

    // ---------- todo detail ----------
    // Clicking a todo swaps the drawer's list for that todo's detail view
    // (back arrow returns to the list). The todoModal* names predate that.
    todoModalOpen: false,
    todoModalItem: null,
    todoModalDetail: "",
    todoModalLoading: false,
    todoModalComment: "",
    todoModalPriority: "2",
    todoModalType: "task",
    todoModalDescription: "",
    todoModalAssignee: "",
    todoModalLabelInput: "",
    todoModalUpdateMsg: "",
    todoModalUpdateErr: false,
    todoModalBlockedBy: [],
    todoModalBlocks: [],
    todoModalDepsLoading: false,
    todoModalDepInput: "",
    todoModalDepDirection: "blocked_by",
    todoModalDepError: "",
    todoModalDeleteConfirm: false,
    todoModalDue: "",

    // ---------- reminders ----------
    // A snooze is bd's "deferred" status with a future defer_until; the server also
    // moves the due date there, so a snoozed todo comes back already due. Labels
    // mirror format_due() in cli.py.
    isSnoozed(item) {
      return !!item && item.status === "deferred" && (!item.defer_until || new Date(item.defer_until) > new Date());
    },
    isOverdue(item) {
      return !!item && item.status !== "closed" && !this.isSnoozed(item) && !!item.due_at && new Date(item.due_at) <= new Date();
    },
    dueLabel(item) {
      if (!item || item.status === "closed") return "";
      const snoozed = this.isSnoozed(item);
      const when = snoozed ? item.defer_until : item.due_at;
      if (!when) return "";
      const seconds = Math.round((new Date(when) - new Date()) / 1000);
      const span = Math.abs(seconds);
      const [unit, size] = [["d", 86400], ["h", 3600], ["m", 60]].find(([, n]) => span >= n) || ["", 0];
      const amount = size ? `${Math.floor(span / size)}${unit}` : "<1m";
      if (snoozed) return `snoozed ${amount}`;
      return seconds > 0 ? `due in ${amount}` : `overdue ${amount}`;
    },
    // Reload the list, then re-point the open detail view at the refreshed item so
    // its due/snooze badges update without closing the view.
    async refreshTodoModalItem() {
      const id = this.todoModalItem && this.todoModalItem.id;
      await this.loadTodos();
      if (!id) return;
      const fresh = this.todoItems.find((i) => i.id === id);
      if (fresh) {
        this.todoModalItem = fresh;
        this.todoModalDue = "";
      }
    },
    async saveTodoDue() {
      if (!this.todoModalItem) return;
      const text = this.todoModalDue.trim();
      if (!text) return;
      const clear = text === "none" || text === "clear";
      this.todoModalUpdateErr = false;
      const { ok, data } = await this.todoPatch(this.todoModalItem.id, { due: clear ? "" : text });
      this.todoModalUpdateErr = !ok;
      this.todoModalUpdateMsg = ok ? (clear ? "reminder cleared" : "reminder set") : ((data && data.error) || "failed to save");
      if (ok) await this.refreshTodoModalItem();
    },
    async snoozeTodo(until) {
      if (!this.todoModalItem) return;
      const r = await this.todoAction(this.todoModalItem.id, "snooze", { until });
      if (r) await this.refreshTodoModalItem();
    },
    async unsnoozeTodo() {
      if (!this.todoModalItem) return;
      const r = await this.todoAction(this.todoModalItem.id, "unsnooze");
      if (r) await this.refreshTodoModalItem();
    },

    async openTodoModal(item) {
      this.todoModalItem = item;
      this.todoModalComment = "";
      this.todoModalDetail = "";
      this.todoModalPriority = String(item.priority);
      this.todoModalType = item.type;
      this.todoModalDescription = item.description || "";
      this.todoModalAssignee = item.owner || "";
      this.todoModalLabelInput = "";
      this.todoModalDue = "";
      this.todoModalUpdateMsg = "";
      this.todoModalUpdateErr = false;
      this.todoModalBlockedBy = [];
      this.todoModalBlocks = [];
      this.todoModalDepInput = "";
      this.todoModalDepDirection = "blocked_by";
      this.todoModalDepError = "";
      this.todoModalDeleteConfirm = false;
      this.todoModalLoading = true;
      this.todoModalOpen = true;
      const requestedId = item.id;
      const [r] = await Promise.all([
        this.todoAction(item.id, "show"),
        this.loadTodoDeps(item.id),
      ]);
      if (!this.todoModalItem || this.todoModalItem.id !== requestedId) return;
      this.todoModalDetail = r ? r.message : "couldn't load";
      this.todoModalLoading = false;
    },
    async loadTodoDeps(id) {
      this.todoModalDepsLoading = true;
      const { ok, data } = await apiFetch(`/api/todo/${encodeURIComponent(id)}/deps`, "GET");
      if (!this.todoModalItem || this.todoModalItem.id !== id) return;
      if (ok) {
        this.todoModalBlockedBy = data.blocked_by;
        this.todoModalBlocks = data.blocks;
      } else {
        this.todoModalDepError = (data && data.error) || "failed to load dependencies";
      }
      this.todoModalDepsLoading = false;
    },
    async addTodoDep() {
      const targetId = this.todoModalDepInput.trim();
      if (!targetId || !this.todoModalItem) return;
      this.todoModalDepError = "";
      const { ok, data } = await apiFetch(`/api/todo/${encodeURIComponent(this.todoModalItem.id)}/deps`, "POST",
        { target_id: targetId, direction: this.todoModalDepDirection });
      if (ok) {
        this.todoModalDepInput = "";
        await this.loadTodoDeps(this.todoModalItem.id);
      } else {
        this.todoModalDepError = (data && data.error) || "failed to add dependency";
      }
    },
    async removeTodoDep(targetId, direction) {
      if (!this.todoModalItem) return;
      const { ok, data } = await apiFetch(
        `/api/todo/${encodeURIComponent(this.todoModalItem.id)}/deps/${encodeURIComponent(targetId)}?direction=${direction}`,
        "DELETE");
      if (ok) await this.loadTodoDeps(this.todoModalItem.id);
      else this.todoModalDepError = (data && data.error) || "failed to remove dependency";
    },
    closeTodoModal() {
      this.todoModalOpen = false;
      this.todoModalItem = null;
    },
    async submitTodoModalComment() {
      const text = this.todoModalComment.trim();
      if (!text || !this.todoModalItem) return;
      this.todoModalComment = "";
      await this.todoAction(this.todoModalItem.id, "comment", { text });
    },
    async closeTodoFromModal() {
      if (!this.todoModalItem) return;
      await this.closeTodo(this.todoModalItem);
      this.closeTodoModal();
    },
    async reopenTodoFromModal() {
      if (!this.todoModalItem) return;
      await this.reopenTodo(this.todoModalItem);
    },
    async deleteTodoFromModal() {
      if (!this.todoModalItem) return;
      const item = this.todoModalItem;
      const { ok } = await apiFetch(`/api/todo/${encodeURIComponent(item.id)}`, "DELETE", { confirm: item.id });
      if (ok) {
        this.todoItems = this.todoItems.filter((i) => i.id !== item.id);
        this.closeTodoModal();
      }
    },
    // Priority/type live on the issue itself, not behind show/close/comment --
    // PATCHes straight to the item object backing both the modal and its row
    // in the drawer list, so the pri-dot/border color update the moment the
    // edit lands instead of needing a full todo list reload.
    async todoPatch(id, payload) {
      const { ok, data } = await apiFetch(`/api/todo/${encodeURIComponent(id)}`, "PATCH", payload);
      return { ok, data };
    },
    async updateTodoPriority() {
      if (!this.todoModalItem) return;
      this.todoModalUpdateMsg = "saving…";
      this.todoModalUpdateErr = false;
      const { ok, data } = await this.todoPatch(this.todoModalItem.id, { priority: this.todoModalPriority });
      if (ok) {
        this.todoModalItem.priority = Number(this.todoModalPriority);
        this.todoModalUpdateMsg = "saved";
      } else {
        this.todoModalUpdateErr = true;
        this.todoModalUpdateMsg = (data && data.error) || "failed to save";
      }
    },
    async updateTodoType() {
      if (!this.todoModalItem) return;
      this.todoModalUpdateMsg = "saving…";
      this.todoModalUpdateErr = false;
      const { ok, data } = await this.todoPatch(this.todoModalItem.id, { type: this.todoModalType });
      if (ok) {
        this.todoModalItem.type = this.todoModalType;
        this.todoModalUpdateMsg = "saved";
      } else {
        this.todoModalUpdateErr = true;
        this.todoModalUpdateMsg = (data && data.error) || "failed to save";
      }
    },
    async updateTodoDescription() {
      if (!this.todoModalItem) return;
      if (this.todoModalDescription === (this.todoModalItem.description || "")) return;
      this.todoModalUpdateMsg = "saving…";
      this.todoModalUpdateErr = false;
      const { ok, data } = await this.todoPatch(this.todoModalItem.id, { description: this.todoModalDescription });
      if (ok) {
        this.todoModalItem.description = this.todoModalDescription;
        this.todoModalUpdateMsg = "saved";
      } else {
        this.todoModalUpdateErr = true;
        this.todoModalUpdateMsg = (data && data.error) || "failed to save";
      }
    },
    async updateTodoAssignee() {
      if (!this.todoModalItem) return;
      if (this.todoModalAssignee === (this.todoModalItem.owner || "")) return;
      this.todoModalUpdateMsg = "saving…";
      this.todoModalUpdateErr = false;
      const { ok, data } = await this.todoPatch(this.todoModalItem.id, { assignee: this.todoModalAssignee });
      if (ok) {
        this.todoModalItem.owner = this.todoModalAssignee;
        this.todoModalUpdateMsg = "saved";
      } else {
        this.todoModalUpdateErr = true;
        this.todoModalUpdateMsg = (data && data.error) || "failed to save";
      }
    },
    async addTodoLabel() {
      const label = this.todoModalLabelInput.trim();
      if (!label || !this.todoModalItem) return;
      this.todoModalLabelInput = "";
      const { ok, data } = await this.todoPatch(this.todoModalItem.id, { add_label: label });
      if (ok) {
        this.todoModalItem.labels = [...(this.todoModalItem.labels || []), label];
      } else {
        this.todoModalUpdateErr = true;
        this.todoModalUpdateMsg = (data && data.error) || "failed to add label";
      }
    },
    async removeTodoLabel(label) {
      if (!this.todoModalItem) return;
      const { ok, data } = await this.todoPatch(this.todoModalItem.id, { remove_label: label });
      if (ok) {
        this.todoModalItem.labels = (this.todoModalItem.labels || []).filter((l) => l !== label);
      } else {
        this.todoModalUpdateErr = true;
        this.todoModalUpdateMsg = (data && data.error) || "failed to remove label";
      }
    },

    // ---------- entry modal ----------
    // Clicking a card opens the entry here: full content, unclamped, with an
    // Edit toggle that swaps the same panel into the tag/link/body editor.
    // `modalItem` points at the underlying feed-card object, so a save made
    // in the modal (title/tags) is reflected on the card automatically
    // through Alpine's reactivity -- no manual DOM syncing needed.
    modalOpen: false,
    // True when the entry panel is docked inside its feed row (wide screens)
    // instead of floating as an overlay -- see dockEntryPanel().
    entryInline: false,
    bootHashDone: false,
    modalEntryId: null,
    modalItem: null,
    modalStamp: "",
    modalLoading: false,
    modalLoadError: "",
    modalEditing: false,
    // {label, url} when the entry is a read-only mirror of an external
    // source (see _readonly_source_info in the kb script) -- null for any
    // normal, editable entry. Set from the GET response in openEntryModal.
    modalReadonly: null,
    modalTitle: "",
    modalBody: "",
    modalTags: [],
    modalLinks: [],
    modalTagInput: "",
    modalLinkInput: "",
    modalLinkError: "",
    // Editor layout: raw markdown only, rendered only, or both side by side
    // (the default -- you see the result as you type).
    modalMode: "split",
    // First "# Title" line of the body, set aside while the visual editor
    // (whose content is written by loadRich, never re-rendered per keystroke)
    // is open and re-attached on every edit.
    modalH1: "",
    modalSaving: false,
    modalSaveMsg: "",
    modalSaveErr: false,
    // Snapshot of title/body when editing starts: Cancel restores it, and the
    // difference from the live fields is what "unsaved changes" means.
    modalOrig: { title: "", body: "" },
    modalDiscardArmed: false,

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

    // Shared by every tag/link editor (the entry-view modal's and the
    // new-entry form's alike) so there's one filter implementation to get
    // right instead of one copy per editor.
    _tagSuggestionsFor(inputVal, currentTags) {
      const q = inputVal.trim().toLowerCase();
      if (!q) return [];
      return this.allTags
        .filter((t) => t.toLowerCase().includes(q) && !currentTags.includes(t))
        .slice(0, 8);
    },
    _linkSuggestionsFor(inputVal, currentLinks, excludeId) {
      const q = inputVal.trim().toLowerCase();
      if (!q) return [];
      return this.allEntries
        .filter((e) => e.id !== excludeId && !currentLinks.includes(e.id)
          && (e.id.toLowerCase().includes(q) || (e.title || "").toLowerCase().includes(q)))
        .slice(0, 8);
    },
    get modalTagSuggestions() {
      return this._tagSuggestionsFor(this.modalTagInput, this.modalTags);
    },
    get modalLinkSuggestions() {
      return this._linkSuggestionsFor(this.modalLinkInput, this.modalLinks, this.modalEntryId);
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

    get modalDirty() {
      return this.modalEditing
        && (this.modalTitle !== this.modalOrig.title || this.modalBody !== this.modalOrig.body);
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

    // On wide screens the same panel element is moved into the clicked feed
    // row, so the entry expands in place and the feed keeps its scroll
    // position; elsewhere (narrow screens, command palette) it stays an
    // overlay. All bindings are on the element itself, so moving it is safe.
    undockEntryPanel() {
      const panel = document.getElementById("entry-panel");
      if (panel && panel.parentElement !== document.body) document.body.appendChild(panel);
    },
    dockEntryPanel(row) {
      const panel = document.getElementById("entry-panel");
      if (panel && row) row.appendChild(panel);
    },
    async openEntryModal(item, row) {
      // Clicking the already-expanded row collapses it.
      if (row && this.modalOpen && this.modalEntryId === item.id) { this.requestCloseEntry(); return; }
      if (!this.guardUnsaved()) return;
      this.undockEntryPanel();
      this.entryInline = !!row && window.matchMedia("(min-width: 820px)").matches;
      if (this.entryInline) this.dockEntryPanel(row);
      try { history.replaceState(null, "", "#" + encodeURIComponent(item.id)); } catch {}
      this.modalItem = item;
      this.modalEntryId = item.id;
      this.modalStamp = item.title || item.id;
      this.modalEditing = false;
      this.modalReadonly = null;
      this.modalLoading = true;
      this.modalLoadError = "";
      this.modalTagInput = "";
      this.modalLinkInput = "";
      this.modalLinkError = "";
      this.modalSaveMsg = "";
      this.modalSaveErr = false;
      this.modalMode = "split";
      this.modalTagHighlight = -1;
      this.modalLinkHighlight = -1;
      this.modalOpen = true;
      try {
        const res = await fetch(`/api/entries/${encodeURIComponent(item.id)}`);
        if (!res.ok) throw new Error();
        const data = await res.json();
        if (this.modalEntryId !== item.id) return;  // another entry was opened meanwhile
        this.modalTags = data.frontmatter.tags || [];
        this.modalLinks = data.frontmatter.links || [];
        this.modalTitle = data.frontmatter.title || "";
        this.modalBody = data.body || "";
        this.modalReadonly = data.readonly || null;
      } catch {
        this.modalLoadError = "couldn't load";
      } finally {
        if (this.modalEntryId === item.id) this.modalLoading = false;
      }
      // Only now does the row swap its snippet for the full body, so scroll
      // after that swap rather than before the content has arrived.
      if (this.entryInline && this.modalEntryId === item.id) {
        this.$nextTick(() => row.scrollIntoView({ block: "nearest" }));
      }
    },
    // Warns instead of silently dropping edits when the user navigates away.
    guardUnsaved() {
      if (!this.modalDirty) return true;
      this.modalSaveErr = true;
      this.modalSaveMsg = "Unsaved changes -- save or cancel first";
      return false;
    },
    requestCloseEntry() {
      if (this.guardUnsaved()) this.closeEntryModal();
    },
    closeEntryModal() {
      this.modalOpen = false;
      this.modalEditing = false;
      this.undockEntryPanel();
      this.entryInline = false;
      try { history.replaceState(null, "", location.pathname + location.search); } catch {}
      this.modalItem = null;
      this.modalEntryId = null;
    },
    showModalEdit(opts = {}) {
      if (this.modalReadonly) return;  // belt-and-suspenders: the edit button is hidden for these
      this.modalSaveMsg = "";
      this.modalSaveErr = false;
      this.modalDiscardArmed = false;
      this.modalBody = this.modalBody.replace(/^\n+/, "");  // no empty first line in the editor
      this.modalOrig = { title: this.modalTitle, body: this.modalBody };
      // Edit the rendered entry in place when that is lossless; otherwise
      // fall back to markdown source with a live preview.
      if (richEditable(this.modalBody)) {
        this.loadRich();
        this.modalMode = "rich";
      } else {
        this.modalMode = "split";
        this.modalSaveMsg = "Edited as source -- this entry has formatting visual editing can't preserve";
      }
      this.modalEditing = true;
      this.$nextTick(() => this.$nextTick(() => {
        if (this.modalMode === "rich") this.focusRich(opts.offset);
        else { const ta = document.querySelector("#entry-panel .edit-body"); autosizeTextarea(ta); ta?.focus(); }
      }));
    },
    richEl() { return document.querySelector("#entry-panel .rich-edit"); },
    loadRich() {
      const { h1, rest } = splitLeadingH1(this.modalBody);
      this.modalH1 = h1;
      // Written straight into the element (not via x-html) so the content is
      // there synchronously -- focusRich() runs right after and needs it.
      const el = this.richEl();
      if (el) el.innerHTML = renderMarkdown(rest);
    },
    // `offset` is a character offset into the entry's text (see viewClick): the
    // view and the editor render the same content, so the same offset lands on
    // the same spot even though the page layout shifts when editing starts.
    focusRich(offset) {
      const el = this.richEl();
      if (!el) return;
      el.focus();
      const range = document.createRange();
      let placed = false;
      if (offset != null) {
        const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
        let left = offset;
        for (let n = walker.nextNode(); n; n = walker.nextNode()) {
          if (left <= n.length) { range.setStart(n, left); placed = true; break; }
          left -= n.length;
        }
      }
      if (placed) range.collapse(true);
      else { range.selectNodeContents(el); range.collapse(false); }
      const sel = getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    },
    // Visual -> markdown on every edit: modalBody is what Save sends and what
    // the dirty check compares, but the editing surface itself is not re-rendered.
    onRichInput() {
      const el = this.richEl();
      if (!el) return;
      const md = htmlToMarkdown(el);
      this.modalBody = (this.modalH1 ? this.modalH1 + "\n\n" : "") + md + (md ? "\n" : "");
      this.modalDiscardArmed = false;
    },
    // Enter / leaving a list leaves empty <p>/<div> blocks behind (sometimes
    // nested). They render as blank space and serialise to nothing, so drop them
    // -- only when focus leaves or on save, never while the caret may be in one.
    richTidy() {
      const el = this.richEl();
      if (!el) return;
      [...el.querySelectorAll("p,div")].reverse().forEach((b) => {
        if (!b.textContent.trim() && !b.querySelector("img,pre,ul,ol,li,blockquote")) b.remove();
      });
    },
    richKeydown(e) {
      const el = e.currentTarget;
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key === "Enter") { e.preventDefault(); this.saveModalEntry(); return; }
      if (mod && !e.shiftKey && !e.altKey) {
        const id = { b: "bold", i: "italic", e: "code", k: "link" }[e.key.toLowerCase()];
        if (id) { e.preventDefault(); richApply(el, id); this.onRichInput(); this.updateToolActive(); }
        return;
      }
      if (e.key === " " && !e.isComposing && richBlockShortcut(el, e)) this.onRichInput();
    },
    // Paste as plain text so foreign HTML (fonts, spans, styles) never enters
    // the entry; a URL pasted over a selection becomes a link.
    richPaste(e) {
      e.preventDefault();
      const text = (e.clipboardData?.getData("text/plain") || "").replace(/\r/g, "");
      const sel = getSelection();
      if (!sel.isCollapsed && MD_URL_RE.test(text.trim())) {
        document.execCommand("createLink", false, text.trim());
      } else {
        text.split("\n").forEach((line, i) => {
          if (i) document.execCommand("insertParagraph");
          if (line) document.execCommand("insertText", false, line);
        });
      }
      this.onRichInput();
    },
    // Clicking the rendered entry starts editing right where you clicked.
    viewClick(e) {
      if (this.modalReadonly || e.target.closest("a, .media-thumb, .media-video")) return;
      let offset = null;
      const hit = document.caretRangeFromPoint ? document.caretRangeFromPoint(e.clientX, e.clientY) : null;
      if (hit && e.currentTarget.contains(hit.startContainer)) {
        const before = document.createRange();
        before.selectNodeContents(e.currentTarget);
        before.setEnd(hit.startContainer, hit.startOffset);
        offset = before.toString().length;
      }
      this.showModalEdit({ offset });
    },
    setEditMode(mode) {
      if (mode === this.modalMode) return;
      if (mode === "rich") {
        if (!richEditable(this.modalBody)) {
          this.modalSaveErr = true;
          this.modalSaveMsg = "This entry has formatting the visual editor can't preserve -- stay in Source";
          return;
        }
        this.loadRich();
      }
      this.modalMode = mode;
      this.$nextTick(() => this.$nextTick(() => {
        if (mode === "rich") this.focusRich();
        else { const ta = document.querySelector("#entry-panel .edit-body"); autosizeTextarea(ta); ta?.focus(); }
      }));
    },
    // Recomputes which toolbar buttons show as pressed for the current selection.
    updateToolActive() {
      let next = {};
      if (this.modalEditing) {
        next = this.modalMode === "rich"
          ? richActiveFormats(this.richEl())
          : sourceActiveFormats(document.querySelector("#entry-panel textarea.edit-body"));
      }
      if (JSON.stringify(next) !== JSON.stringify(this.mdActive)) this.mdActive = next;
    },
    toolAction(id) {
      if (this.modalMode === "rich") { richApply(this.richEl(), id); this.onRichInput(); }
      else mdApply(document.querySelector("#entry-panel textarea.edit-body"), id);
      this.updateToolActive();
    },
    // Back to the read-only view, throwing away anything not saved.
    cancelModalEdit() {
      this.modalTitle = this.modalOrig.title;
      this.modalBody = this.modalOrig.body;
      this.modalEditing = false;
      this.modalMode = "split";
      this.modalDiscardArmed = false;
      this.modalSaveErr = false;
      this.modalSaveMsg = "";
    },
    // Esc while editing: cancel straight away if nothing changed, otherwise
    // ask for a second Esc so a stray keypress can't discard work.
    escapeEditing() {
      if (this.modalDirty && !this.modalDiscardArmed) {
        this.modalDiscardArmed = true;
        this.modalSaveErr = true;
        this.modalSaveMsg = "Unsaved changes -- press Esc again to discard";
        return;
      }
      this.cancelModalEdit();
    },
    async saveModalEntry() {
      if (this.modalSaving) return;
      if (this.modalMode === "rich") this.richTidy();
      this.modalSaving = true;
      this.modalSaveErr = false;
      this.modalDiscardArmed = false;
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
        this.modalOrig = { title: this.modalTitle, body: this.modalBody };
        this.modalEditing = false;
        this.modalSaveMsg = "Saved";
        setTimeout(() => { if (!this.modalEditing && this.modalSaveMsg === "Saved") this.modalSaveMsg = ""; }, 2500);
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

    // ---------- new entry ----------
    // A separate modal from the entry-view/edit one above -- it never has an
    // entry id to load against, just a blank form that POSTs to
    // /api/entries and then behaves like any other freshly-created card.
    newEntryOpen: false,
    ...newEntryDefaults(),

    openNewEntryModal() {
      Object.assign(this, newEntryDefaults());
      this.newEntryOpen = true;
      this.$nextTick(() => this.$refs.newEntryTitleInput && this.$refs.newEntryTitleInput.focus());
    },
    closeNewEntryModal() {
      this.newEntryOpen = false;
    },
    // Shared by both chip editors below -- trim, skip empty/duplicate, push,
    // clear the input. (Unlike the entry-view modal's addModalTag/addModalLink,
    // there's no entry yet to PATCH, so this only ever touches local state.)
    _addLocalChip(inputProp, arrayProp) {
      const val = this[inputProp].trim();
      this[inputProp] = "";
      if (val && !this[arrayProp].includes(val)) this[arrayProp] = [...this[arrayProp], val];
    },
    addNewEntryTag() {
      this._addLocalChip("newEntryTagInput", "newEntryTags");
    },
    addNewEntryLink() {
      this._addLocalChip("newEntryLinkInput", "newEntryLinks");
    },
    get newEntryTagSuggestions() {
      return this._tagSuggestionsFor(this.newEntryTagInput, this.newEntryTags);
    },
    get newEntryLinkSuggestions() {
      return this._linkSuggestionsFor(this.newEntryLinkInput, this.newEntryLinks, null);
    },
    newEntryTagInputKeydown(e) {
      this._suggestKeydown(e, this.newEntryTagSuggestions, "newEntryTagHighlight",
        (t) => this.selectNewEntryTagSuggestion(t), () => this.addNewEntryTag());
    },
    newEntryLinkInputKeydown(e) {
      this._suggestKeydown(e, this.newEntryLinkSuggestions, "newEntryLinkHighlight",
        (entry) => this.selectNewEntryLinkSuggestion(entry), () => this.addNewEntryLink());
    },
    selectNewEntryTagSuggestion(tag) {
      this.newEntryTagInput = tag;
      this.newEntryTagHighlight = -1;
      this.addNewEntryTag();
    },
    selectNewEntryLinkSuggestion(entry) {
      this.newEntryLinkInput = entry.id;
      this.newEntryLinkHighlight = -1;
      this.addNewEntryLink();
    },
    async createNewEntry() {
      const title = this.newEntryTitle.trim();
      if (!title) {
        this.newEntrySaveErr = true;
        this.newEntrySaveMsg = "title is required";
        return;
      }
      this.newEntrySaving = true;
      this.newEntrySaveErr = false;
      this.newEntrySaveMsg = "creating…";
      const { ok, data } = await apiFetch("/api/entries", "POST", {
        type: this.newEntryType, title, body: this.newEntryBody,
        tags: this.newEntryTags, links: this.newEntryLinks,
      });
      this.newEntrySaving = false;
      if (ok) {
        this.newEntryOpen = false;
        // The response already tells us everything loadEntryIndex()/
        // loadTagIndex() would otherwise re-fetch from scratch (a full
        // rescan of every entry in the repo) -- just fold the one new
        // row/tags into the already-loaded indexes instead.
        const newTags = this.newEntryTags.filter((t) => !this.allTags.includes(t));
        if (newTags.length) this.allTags = [...this.allTags, ...newTags].sort();
        if (!this.allEntries.some((e) => e.id === data.id)) {
          this.allEntries = [...this.allEntries, { id: data.id, title, type: this.newEntryType }];
        }
        // Only worth a feed reload if the new entry would actually show up
        // in the view the user is currently looking at.
        const matchesView = (!this.activeTag || this.newEntryTags.includes(this.activeTag))
          && (!this.activeType || this.activeType === this.newEntryType);
        if (matchesView) this.loadFeed(false);
      } else {
        this.newEntrySaveErr = true;
        this.newEntrySaveMsg = (data && data.error) || "create failed";
      }
    },

    // ---------- command palette ----------
    // Cmd/Ctrl+K opens a quick-jump search independent of the main feed's
    // search box -- it has its own query/results/highlight state so opening
    // it never clobbers whatever filter or search is already active in the
    // background feed.
    paletteOpen: false,
    paletteQuery: "",
    paletteResults: [],
    paletteHighlight: -1,
    paletteLoading: false,
    paletteMsg: "",
    _paletteDebounce: null,
    _paletteSeq: 0,

    openPalette() {
      this.paletteOpen = true;
      this.paletteQuery = "";
      this.paletteResults = [];
      this.paletteHighlight = -1;
      this.paletteMsg = "";
      this.$nextTick(() => this.$refs.paletteInput && this.$refs.paletteInput.focus());
    },
    closePalette() {
      this.paletteOpen = false;
    },
    onPaletteInput() {
      clearTimeout(this._paletteDebounce);
      const query = this.paletteQuery.trim();
      this.paletteHighlight = -1;
      if (!query) {
        this.paletteResults = [];
        this.paletteLoading = false;
        this.paletteMsg = "";
        return;
      }
      this.paletteLoading = true;
      this._paletteDebounce = setTimeout(() => this.runPaletteSearch(query), 200);
    },
    async runPaletteSearch(query) {
      const seq = ++this._paletteSeq;
      const url = new URL("/api/search", location.origin);
      url.searchParams.set("q", query);
      url.searchParams.set("all", "1");
      try {
        const res = await fetch(url);
        if (seq !== this._paletteSeq) return; // a newer keystroke's request already landed
        if (!res.ok) throw new Error();
        const data = await res.json();
        this.paletteResults = data.items.slice(0, 20);
        this.paletteMsg = this.paletteResults.length ? "" : "no results";
      } catch {
        if (seq !== this._paletteSeq) return;
        this.paletteResults = [];
        this.paletteMsg = "search failed";
      } finally {
        if (seq === this._paletteSeq) this.paletteLoading = false;
      }
    },
    openPaletteResult(item) {
      this.closePalette();
      this.openEntryModal(item);
    },
    paletteKeydown(e) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        this.paletteHighlight = Math.min(this.paletteHighlight + 1, this.paletteResults.length - 1);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        this.paletteHighlight = Math.max(this.paletteHighlight - 1, -1);
      } else if (e.key === "Enter") {
        e.preventDefault();
        const chosen = this.paletteResults[this.paletteHighlight] || this.paletteResults[0];
        if (chosen) this.openPaletteResult(chosen);
      }
      // Escape isn't handled here -- it's left to bubble up to the
      // window-level onKeydown handler, which already closes the palette
      // (and, in the same keypress, any other modal/drawer stacked under it).
    },

    // ---------- admin ----------
    adminOpen: false,
    adminTab: "overview",
    adminMsg: "",
    adminMsgErr: false,
    adminOutput: "",
    adminBusy: false,
    todoConfirmId: "", // todo whose row X was clicked once and awaits a second click
    adminConfirm: "", // key of the destructive/heavy action awaiting its second click
    adminSyncSource: "all",
    adminIndexFull: false,
    adminRollupMonth: "", // "" = last month, which the server resolves
    adminJournalMonths: [],
    adminSyncInterval: 60,
    adminEditor: "",
    adminConfig: { editor: "", saved: "", source: null },
    adminUpdate: null, // /api/admin/update result; null until checked
    adminUpdateChecking: false,
    adminServices: null, // /api/admin/services result; null until loaded
    adminReminders: null, // /api/admin/reminders result; null until loaded
    adminReminderInterval: 5,
    adminReminderTestMsg: "",
    adminReminderTestOk: true,
    // Populated by /api/admin/status -- the same read-only checks `kb doctor`
    // reports, polled on a timer so the Manage badge and drawer reflect what
    // needs attention without anyone clicking "Doctor" themselves.
    adminStatus: { ok: [], todo: [], needs_attention: false, version: "", root: "", sources: [], push: { is_git: false, upstream: null, ahead: null, behind: null } },
    adminStatusLoaded: false,

    async loadAdminStatus() {
      const { ok, data } = await apiFetch("/api/admin/status", "GET");
      if (ok) this.adminStatus = data;
      this.adminStatusLoaded = true;
    },
    async loadAdminServices() {
      const { ok, data } = await apiFetch("/api/admin/services", "GET");
      if (ok) this.adminServices = data;
    },
    async loadAdminReminders() {
      const { ok, data } = await apiFetch("/api/admin/reminders", "GET");
      if (ok) {
        this.adminReminders = data;
        if (data.interval_minutes) this.adminReminderInterval = data.interval_minutes;
        // the permission was granted since the last look: drop the stale failed test
        if (this.reminderNotifierAllowed()) this.adminReminderTestMsg = this.adminReminderTestOk ? this.adminReminderTestMsg : "";
      }
    },
    // The doctor line about reminders can't be fixed by "Fix with setup" -- it's a macOS
    // setting -- so the Overview offers a shortcut to the card that walks through it.
    adminTodoMentionsReminders() {
      return (this.adminStatus.todo || []).some((line) => /reminder/i.test(line));
    },
    reminderNeedsPermission() {
      const n = this.adminReminders && this.adminReminders.notifier;
      return !!(this.adminReminders && this.adminReminders.installed && n && n.app_built && !n.allowed);
    },
    reminderNotifierAllowed() {
      const n = this.adminReminders && this.adminReminders.notifier;
      return !n || !n.app_built || n.allowed;
    },
    reminderState() {
      const r = this.adminReminders;
      if (!r || !r.installed) return { label: "off", cls: "off" };
      if (r.detail) return { label: "needs attention", cls: "off" };
      if (this.reminderNeedsPermission()) return { label: "needs permission", cls: "off" };
      return { label: r.interval_minutes ? `every ${r.interval_minutes}m` : "on", cls: "ok" };
    },
    async adminReminderInstall() {
      const interval = Number(this.adminReminderInterval);
      const data = await this.adminRun("reminders install",
        () => apiFetch("/api/admin/reminders/install", "POST", { interval_minutes: interval }));
      if (data && data.ok && data.notifications_ok === false) {
        this.adminSay("reminders installed -- but macOS isn't allowing notifications yet, see the steps below", true);
      }
      this.adminReminderTestMsg = "";
      this.loadAdminReminders();
      this.loadAdminStatus();
    },
    async adminReminderUninstall() {
      if (this.adminNeedsConfirm("remind:uninstall")) return;
      await this.adminRun("reminders uninstall", () => apiFetch("/api/admin/reminders/uninstall", "POST"));
      this.adminReminderTestMsg = "";
      this.loadAdminReminders();
      this.loadAdminStatus();
    },
    async adminReminderTest() {
      if (this.adminBusy) return;
      this.adminBusy = true;
      try {
        const { ok, data } = await apiFetch("/api/admin/reminders/test", "POST");
        this.adminReminderTestOk = ok && !!data.ok;
        this.adminReminderTestMsg = ok ? data.message : ((data && data.error) || "test failed");
        if (ok && data.reminders) this.adminReminders = data.reminders;
        // the steps are already on screen: don't repeat them as a wall of red text
        if (!this.adminReminderTestOk && this.reminderNeedsPermission()) {
          this.adminReminderTestMsg = "Still blocked -- macOS hasn't been told to allow it yet. Follow the steps above.";
        }
      } finally {
        this.adminBusy = false;
      }
      this.loadAdminStatus();
    },
    async adminReminderOpenSettings() {
      const { ok, data } = await apiFetch("/api/admin/reminders/open-settings", "POST");
      if (!ok) this.adminSay((data && data.error) || "couldn't open System Settings", true);
    },
    async loadAdminJournalMonths() {
      const { ok, data } = await apiFetch("/api/admin/journal-months", "GET");
      if (ok) this.adminJournalMonths = data.months;
    },
    async loadAdminConfig() {
      const { ok, data } = await apiFetch("/api/admin/config", "GET");
      if (!ok) return;
      this.adminConfig = data;
      this.adminEditor = data.saved;
    },
    async checkAdminUpdate() {
      this.adminUpdateChecking = true;
      try {
        const { ok, data } = await apiFetch("/api/admin/update", "GET");
        this.adminUpdate = ok ? data : { error: (data && data.error) || "update check failed" };
      } catch {
        this.adminUpdate = { error: "update check failed" };
      }
      this.adminUpdateChecking = false;
    },
    // Called when the drawer opens, and when switching tabs: refreshes just what
    // the visible tab shows, so opening Manage doesn't fire every request at once.
    adminTabOpened() {
      if (this.adminTab === "overview" && !this.adminUpdate && !this.adminUpdateChecking) this.checkAdminUpdate();
      if (this.adminTab === "maintenance") this.loadAdminJournalMonths();
      if (this.adminTab === "services") { this.loadAdminServices(); this.loadAdminReminders(); }
      if (this.adminTab === "settings") this.loadAdminConfig();
    },
    adminSay(msg, err = false) {
      this.adminMsg = msg;
      this.adminMsgErr = err;
    },
    adminSetTab(tab) {
      this.adminTab = tab;
      this.adminConfirm = "";
      this.adminTabOpened();
    },
    // Heavy or hard-to-undo actions (update, setup, removing a service) need a
    // second click on the same button -- inline, since a browser confirm() dialog
    // would block the page and can't be styled to match.
    adminNeedsConfirm(key) {
      if (this.adminConfirm === key) { this.adminConfirm = ""; return false; }
      this.adminConfirm = key;
      setTimeout(() => { if (this.adminConfirm === key) this.adminConfirm = ""; }, 5000);
      return true;
    },
    // Runs one admin request. `fn` resolves to the endpoint's {ok, data}; a
    // non-2xx (409 busy, 400 bad input, 500) shows data.error rather than output.
    async adminRun(label, fn) {
      if (this.adminBusy) return null;
      this.adminBusy = true;
      this.adminSay(`${label}...`);
      this.adminOutput = "";
      let data = null;
      try {
        const res = await fn();
        data = res.data || {};
        if (!res.ok || data.error) {
          this.adminSay(`${label}: ${data.error || "failed"}`, true);
        } else {
          this.adminSay(`${label}: ${data.ok === false ? "issues found" : "done"}${data.note ? ` -- ${data.note}` : ""}`,
            data.ok === false);
        }
        if (data.output) this.adminOutput = data.output;
      } catch {
        this.adminSay(`${label} failed`, true);
      }
      this.adminBusy = false;
      this.loadAdminStatus();
      return data;
    },
    adminReindex() {
      this.adminRun(this.adminIndexFull ? "full reindex" : "reindex",
        () => apiFetch("/api/index", "POST", { full: this.adminIndexFull }));
    },
    adminValidate() {
      this.adminRun("validate", () => apiFetch("/api/validate", "POST"));
    },
    adminDoctor() {
      this.adminRun("doctor", () => apiFetch("/api/doctor", "GET"));
    },
    adminTriage() {
      this.adminRun("triage", () => apiFetch("/api/admin/triage", "GET"));
    },
    adminRollup() {
      this.adminRun("journal rollup", () => apiFetch("/api/admin/rollup", "POST", { month: this.adminRollupMonth }))
        .then(() => { this.loadTagIndex(); this.loadEntryIndex(); });
    },
    adminSync() {
      this.adminRun("sync", () => apiFetch("/api/sync", "POST", { source: this.adminSyncSource }))
        .then(() => { this.loadTagIndex(); this.loadEntryIndex(); });
    },
    adminPush() {
      this.adminRun("push", () => apiFetch("/api/push", "POST").then((r) => {
        const { ok, message } = r.data;
        return { ok: r.ok, data: ok ? { ok, note: `pushed -> ${message}` } : { ok, error: message } };
      }));
    },
    adminSetup() {
      if (this.adminNeedsConfirm("setup")) return;
      this.adminRun("setup", () => apiFetch("/api/admin/setup", "POST"));
    },
    async adminApplyUpdate() {
      if (this.adminNeedsConfirm("update")) return;
      const data = await this.adminRun("update", () => apiFetch("/api/admin/update", "POST"));
      if (data && data.ok && data.restart_scheduled) this.adminAwaitRestart("update");
      else if (data && data.ok) this.checkAdminUpdate();
    },
    // Poll /api/health until the (restarted) server answers again, then reload so
    // the page runs the new frontend. The restart is scheduled ~1s after the
    // response, so wait that out first -- otherwise the old process answers.
    async adminAwaitRestart(label) {
      this.adminBusy = true;
      this.adminSay(`${label}: restarting kb web...`);
      await new Promise((r) => setTimeout(r, 3000));
      for (let i = 0; i < 40; i++) {
        try {
          const res = await fetch("/api/health");
          if (res.ok) { location.reload(); return; }
        } catch {}
        await new Promise((r) => setTimeout(r, 1500));
      }
      this.adminBusy = false;
      this.adminSay(`${label}: kb web didn't come back -- check the service logs`, true);
    },
    async adminService(name, action) {
      if ((action === "uninstall" || (name === "web" && action === "refresh")) && this.adminNeedsConfirm(`${name}:${action}`)) return;
      const body = action === "install" ? { interval_minutes: Number(this.adminSyncInterval) } : {};
      const data = await this.adminRun(`${name} service ${action}`,
        () => apiFetch(`/api/admin/services/${name}/${action}`, "POST", body));
      if (data && data.ok && data.restart_scheduled) {
        if (name === "web" && action === "uninstall") {
          this.adminSay("web service removed -- this page stops working once kb web exits");
        } else {
          this.adminAwaitRestart(`${name} service ${action}`);
        }
      }
      this.loadAdminServices();
    },
    async adminSaveEditor() {
      const { ok, data } = await apiFetch("/api/admin/config", "PATCH", { editor: this.adminEditor });
      this.adminSay(ok ? (this.adminEditor.trim() ? "editor saved" : "editor preference cleared")
        : (data && data.error) || "couldn't save", !ok);
      if (ok) { this.adminConfig = data; this.adminEditor = data.saved; }
    },

    // ---------- lightbox ----------
    // Opened by clicking any `.media-thumb` (image or video) rendered inside
    // a `.md-view` block -- see handleMediaClick below and renderInline's
    // markup for the media it targets. `lightboxItems` is built fresh from
    // whichever `.md-view` container was clicked (there's no separate JSON
    // media array per post -- see renderMarkdown), so next/prev only ever
    // moves within that one post's media, in the order it was rendered.
    lightboxOpen: false,
    lightboxItems: [],
    lightboxIndex: 0,
    get lightboxItem() {
      return this.lightboxItems[this.lightboxIndex] || null;
    },
    openLightbox(items, index) {
      if (!items.length) return;
      this.lightboxItems = items;
      this.lightboxIndex = index;
      this.lightboxOpen = true;
    },
    closeLightbox() {
      this.lightboxOpen = false;
      this.lightboxItems = [];
    },
    lightboxStep(delta) {
      const n = this.lightboxItems.length;
      if (n < 2) return;
      this.lightboxIndex = (this.lightboxIndex + delta + n) % n;
    },
    // Bound once, window-wide (see `click.window.capture` on <body>), since
    // `.md-view` content is `x-html`-injected and can't carry its own
    // per-element Alpine directives. Capture phase so a click on a post's
    // media is claimed -- and, for the feed, kept from also opening the
    // entry modal -- before it bubbles to the feed <li>'s own click handler.
    handleMediaClick(e) {
      const el = e.target.closest(".media-thumb");
      if (!el) return;
      const container = el.closest(".md-view");
      if (!container) return;
      e.stopPropagation();
      const thumbs = Array.from(container.querySelectorAll(".media-thumb"));
      const items = thumbs.map((t) => ({ src: t.dataset.src || t.src, type: t.dataset.type }));
      this.openLightbox(items, thumbs.indexOf(el));
    },

    // ---------- global keydown ----------
    onKeydown(e) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        this.paletteOpen ? this.closePalette() : this.openPalette();
        return;
      }
      if (this.lightboxOpen && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
        e.preventDefault();
        this.lightboxStep(e.key === "ArrowLeft" ? -1 : 1);
        return;
      }
      if (e.key !== "Escape") return;
      if (this.lightboxOpen) { this.closeLightbox(); return; }
      if (this.modalOpen && this.modalEditing && !this.paletteOpen) { this.escapeEditing(); return; }
      if (this.paletteOpen) this.closePalette();
      if (this.adminOpen) this.adminOpen = false;
      if (this.inboxDrawerOpen) this.inboxDrawerOpen = false;
      if (this.todoModalOpen) { this.closeTodoModal(); return; }
      if (this.todoDrawerOpen) this.todoDrawerOpen = false;
      if (this.modalOpen) this.closeEntryModal();
      if (this.newEntryOpen) this.closeNewEntryModal();
    },

    // ---------- boot ----------
    init() {
      try { this.theme = localStorage.getItem(THEME_KEY) || "system"; } catch {}
      this.applyThemeAttr(this.theme);
      this.$watch("theme", (mode) => this.applyThemeAttr(mode));

      // Wide screens dock Todos as always-visible
      // columns instead of slide-over drawers. Breakpoints match style.css.
      const dockQueries = [
        ["dockTodos", "todoDrawerOpen", window.matchMedia("(min-width: 1100px)")],
      ];
      for (const [flag, drawer, mq] of dockQueries) {
        const sync = () => {
          this[flag] = mq.matches;
          if (mq.matches) this[drawer] = false;
        };
        sync();
        mq.addEventListener("change", sync);
      }

      // init() runs twice (see bootHashDone), so register this one only once.
      if (!window.__kbSelectionListener) {
        window.__kbSelectionListener = true;
        document.addEventListener("selectionchange", () => this.updateToolActive());
      }
      this.$watch("modalMode", () => this.$nextTick(() => this.updateToolActive()));
      this.$watch("modalEditing", () => this.$nextTick(() => this.updateToolActive()));

      window.addEventListener("scroll", () => this.updateCaptureProgress(), { passive: true });
      window.addEventListener("resize", () => this.updateCaptureProgress());
      // Scroll-driven changes must not be eased (they'd lag the scroll), but a
      // click/focus-driven open or close should glide -- so transitions are
      // only switched on briefly around that change.
      this.$watch("captureOpen", () => {
        const el = document.getElementById("site-header");
        el?.classList.add("snap");
        this.updateCaptureProgress();
        setTimeout(() => el?.classList.remove("snap"), 300);
      });
      this.$nextTick(() => this.updateCaptureProgress());

      // Lock the page's own scroll behind whichever modal or drawer is open
      // so dragging the feed underneath doesn't fight with scrolling the
      // overlay's own content -- a drawer should only ever scroll itself.
      // Every flag needs its own $watch (Alpine has no "watch any of these"
      // form), but they all share one body.
      const updateBodyScrollLock = () => {
        document.body.style.overflow = ((this.modalOpen && !this.entryInline) || this.newEntryOpen
          || this.adminOpen || this.inboxDrawerOpen || this.todoDrawerOpen || this.paletteOpen
          || this.lightboxOpen) ? "hidden" : "";
      };
      this.$watch("modalOpen", updateBodyScrollLock);
      this.$watch("newEntryOpen", updateBodyScrollLock);
      this.$watch("adminOpen", updateBodyScrollLock);
      this.$watch("adminOpen", (open) => {
        if (open) { this.loadAdminStatus(); this.adminTabOpened(); } else this.adminConfirm = "";
      });
      // Coming back from System Settings: re-check the notification permission so the
      // steps clear on their own once it's allowed (read-only; never prompts).
      document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible" && this.adminOpen && this.adminTab === "services") {
          this.loadAdminReminders();
          this.loadAdminStatus();
        }
      });
      this.$watch("inboxDrawerOpen", updateBodyScrollLock);
      this.$watch("todoDrawerOpen", updateBodyScrollLock);
      this.$watch("paletteOpen", updateBodyScrollLock);
      this.$watch("lightboxOpen", updateBodyScrollLock);

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

      const bootId = decodeURIComponent(location.hash.slice(1));
      if (bootId) {
        // init() runs twice (Alpine auto-calls it and x-init does too), so the
        // "already handled" flag lives on the component, not in this closure.
        this.$watch("feedItems", () => {
          if (this.bootHashDone) return;
          this.bootHashDone = true;
          this.$nextTick(() => {
            const row = document.querySelector(`#feed > li[data-id="${CSS.escape(bootId)}"]`);
            const item = this.feedItems.find((i) => i.id === bootId) || { id: bootId, title: bootId };
            this.openEntryModal(item, row);
          });
        });
      }

      this.refreshAll();

      // Manage health checks are read-only and cheap (file/sqlite checks, no
      // network) -- poll them the whole time the tab is open, not just while
      // the drawer is open, so the header badge can flag "needs attention"
      // before anyone thinks to look.
      this.loadAdminStatus();
      setInterval(() => this.loadAdminStatus(), ADMIN_STATUS_POLL_MS);

      this.connectEvents();
    },

    // Reloads every list the feed/drawers show -- shared by the initial
    // load and by connectEvents() so there's one place that knows what
    // "refresh everything" means.
    refreshAll() {
      if (!this.activeTag) this.loadFeed(false);
      this.loadInbox();
      this.loadTodos();
      this.loadTagIndex();
      this.loadEntryIndex();
    },

    // ---------- live updates ----------
    // Picks up data written by another process -- a `kb sync` run from the
    // CLI, sync-service's timer, or bd -- that this tab has no other way to
    // hear about. The server has no payload to send (see /api/events); on
    // each "changed" event we re-run refreshAll(), debounced so a burst of
    // writes (e.g. syncing several sources back to back) collapses into one
    // reload instead of one per event.
    connectEvents() {
      const es = new EventSource("/api/events");
      let debounceTimer = null;
      es.addEventListener("changed", () => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => this.refreshAll(), 500);
      });
      // EventSource retries on its own after a drop (server restart, laptop
      // sleep) -- nothing to do here but let it.
    },
  }));
});
