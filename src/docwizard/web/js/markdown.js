// Kleine, veilige Markdown-renderer. Alle invoer wordt eerst ge-escaped; alleen een vaste set
// tags wordt gegenereerd. Links alleen http(s)/mailto, altijd rel="noopener noreferrer".
// Ondersteunt: koppen, vet/cursief/doorhalen, inline code, codeblokken, (geneste) lijsten,
// genummerde lijsten, taaklijsten, citaten, horizontale lijnen, tabellen (GFM), links.

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

function safeUrl(url) {
  const u = url.trim().replace(/&amp;/g, "&");
  if (/^(https?:|mailto:)/i.test(u)) return escapeHtml(u);
  return null;
}

function inline(src) {
  // src is ruwe tekst; we escapen en zetten daarna markup om. Codespans eerst eruit halen.
  const codes = [];
  let s = src.replace(/`([^`\n]+)`/g, (_, c) => { codes.push(c); return `\u0000${codes.length - 1}\u0000`; });
  s = escapeHtml(s);
  // links [tekst](url)
  s = s.replace(/\[([^\]\n]+)\]\(([^)\s]+)(?:\s+&quot;[^&]*&quot;)?\)/g, (m, text, url) => {
    const u = safeUrl(url);
    return u ? `<a href="${u}" target="_blank" rel="noopener noreferrer">${text}</a>` : text;
  });
  // autolinks
  s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+[^\s<).,;:!?])/g, (m, pre, url) => {
    const u = safeUrl(url);
    return u ? `${pre}<a href="${u}" target="_blank" rel="noopener noreferrer">${url}</a>` : m;
  });
  s = s.replace(/\*\*([^*\n]+?)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/__([^_\n]+?)__/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*\w])\*([^*\n]+?)\*(?!\w)/g, "$1<em>$2</em>");
  s = s.replace(/(^|[^_\w])_([^_\n]+?)_(?!\w)/g, "$1<em>$2</em>");
  s = s.replace(/~~([^~\n]+?)~~/g, "<del>$1</del>");
  s = s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${escapeHtml(codes[+i])}</code>`);
  return s;
}

function splitRow(line) {
  let l = line.trim();
  if (l.startsWith("|")) l = l.slice(1);
  if (l.endsWith("|")) l = l.slice(0, -1);
  return l.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
}

const LIST_RE = /^(\s*)([-*+]|\d{1,3}[.)])\s+(.*)$/;

function renderList(lines, start) {
  // Bouwt een (geneste) lijst vanaf `start`; retourneert [html, volgende index].
  const first = lines[start].match(LIST_RE);
  const baseIndent = first[1].length;
  const ordered = /\d/.test(first[2]);
  const startNum = ordered ? parseInt(first[2], 10) : 1;
  let html = ordered ? `<ol${startNum !== 1 ? ` start="${startNum}"` : ""}>` : "<ul>";
  let i = start;
  while (i < lines.length) {
    const m = lines[i].match(LIST_RE);
    if (!m) {
      // vervolgregel van een item (ingesprongen) of einde
      if (lines[i].trim() && /^\s+/.test(lines[i]) && html.endsWith("</li>")) {
        html = html.slice(0, -5) + " " + inline(lines[i].trim()) + "</li>";
        i++; continue;
      }
      break;
    }
    const indent = m[1].length;
    if (indent < baseIndent) break;
    if (indent > baseIndent) {
      const [sub, next] = renderList(lines, i);
      html = html.endsWith("</li>") ? html.slice(0, -5) + sub + "</li>" : html + `<li>${sub}</li>`;
      i = next; continue;
    }
    if (/\d/.test(m[2]) !== ordered) break;
    let content = m[3];
    const task = content.match(/^\[([ xX])\]\s+(.*)$/);
    if (task) {
      content = `<input type="checkbox" disabled${task[1] !== " " ? " checked" : ""}> ${inline(task[2])}`;
      html += `<li class="task">${content}</li>`;
    } else {
      html += `<li>${inline(content)}</li>`;
    }
    i++;
  }
  html += ordered ? "</ol>" : "</ul>";
  return [html, i];
}

export function renderMarkdown(src) {
  const lines = String(src ?? "").replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  let i = 0;
  let para = [];
  const flush = () => { if (para.length) { out.push(`<p>${para.map(inline).join("<br>")}</p>`); para = []; } };

  while (i < lines.length) {
    const line = lines[i];
    // codeblok
    const fence = line.match(/^\s*(```|~~~)\s*([\w+-]*)\s*$/);
    if (fence) {
      flush();
      const buf = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith(fence[1])) { buf.push(lines[i]); i++; }
      i++;
      const lang = fence[2] ? ` data-lang="${escapeHtml(fence[2])}"` : "";
      out.push(`<pre${lang}><code>${escapeHtml(buf.join("\n"))}</code></pre>`);
      continue;
    }
    if (!line.trim()) { flush(); i++; continue; }
    const hd = line.match(/^(#{1,6})\s+(.*?)\s*#*\s*$/);
    if (hd) { flush(); const n = Math.min(hd[1].length + 1, 6); out.push(`<h${n}>${inline(hd[2])}</h${n}>`); i++; continue; }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { flush(); out.push("<hr>"); i++; continue; }
    // tabel
    if (line.includes("|") && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(lines[i + 1])) {
      flush();
      const head = splitRow(line);
      const aligns = splitRow(lines[i + 1]).map((c) => (c.startsWith(":") && c.endsWith(":") ? "center" : c.endsWith(":") ? "right" : ""));
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) { rows.push(splitRow(lines[i])); i++; }
      const al = (j) => (aligns[j] ? ` style="text-align:${aligns[j]}"` : "");
      out.push(`<div class="md-table"><table><thead><tr>${head.map((c, j) => `<th${al(j)}>${inline(c)}</th>`).join("")}</tr></thead>` +
        `<tbody>${rows.map((r) => `<tr>${head.map((_, j) => `<td${al(j)}>${inline(r[j] ?? "")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
      continue;
    }
    if (/^\s*>/.test(line)) {
      flush();
      const buf = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) { buf.push(lines[i].replace(/^\s*>\s?/, "")); i++; }
      out.push(`<blockquote>${renderMarkdown(buf.join("\n"))}</blockquote>`);
      continue;
    }
    if (LIST_RE.test(line) && (para.length === 0 || /^\s*([-*+]|\d+[.)])\s/.test(line))) {
      flush();
      const [html, next] = renderList(lines, i);
      out.push(html);
      i = next;
      continue;
    }
    para.push(line.trim());
    i++;
  }
  flush();
  return out.join("\n");
}
