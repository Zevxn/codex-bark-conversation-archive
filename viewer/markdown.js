(function () {
  "use strict";

  const ALLOWED_TAGS = new Set([
    "A", "BLOCKQUOTE", "BR", "CODE", "DEL", "DETAILS", "EM", "H1", "H2", "H3",
    "H4", "H5", "H6", "HR", "IMG", "KBD", "LI", "MARK", "OL", "P", "PRE",
    "S", "STRONG", "SUB", "SUMMARY", "SUP", "TABLE", "TBODY", "TD", "TFOOT",
    "TH", "THEAD", "TR", "UL"
  ]);
  const DROP_CONTENT_TAGS = new Set([
    "APPLET", "AUDIO", "BASE", "BUTTON", "CANVAS", "EMBED", "FORM", "FRAME",
    "FRAMESET", "IFRAME", "INPUT", "LINK", "META", "NOSCRIPT", "OBJECT", "SCRIPT",
    "SELECT", "SOURCE", "STYLE", "TEMPLATE", "TEXTAREA", "VIDEO"
  ]);

  if (typeof marked !== "undefined") {
    marked.setOptions({
      breaks: true,
      gfm: true
    });
  }

  if (typeof mermaid !== "undefined") {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "default",
      fontFamily: "Inter, Segoe UI, PingFang SC, Microsoft YaHei, sans-serif"
    });
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "\"": "&quot;",
      "'": "&#39;"
    })[character]);
  }

  function protectMarkdown(source) {
    const codeMap = new Map();
    const mathMap = new Map();
    let serial = 0;
    let protectedSource = String(source || "");

    const nextToken = (kind) => `CODEXARCHIVE${kind}${serial++}TOKEN`;
    const storeCode = (value) => {
      const token = nextToken("CODE");
      codeMap.set(token, value);
      return token;
    };
    const storeMath = (value) => {
      const token = nextToken("MATH");
      mathMap.set(token, value);
      return token;
    };

    const fencedCode = /(^|\n)([ \t]*)([`~]{3,})([^\n]*\n[\s\S]*?)(?:\n\2\3[ \t]*)(?=\n|$)/g;
    protectedSource = protectedSource.replace(
      fencedCode,
      (match, prefix) => `${prefix}${storeCode(match.slice(prefix.length))}`
    );
    protectedSource = protectedSource.replace(/(`+)([^`\n]+?)\1/g, (match) => storeCode(match));

    protectedSource = protectedSource.replace(/\$\$[\s\S]+?\$\$/g, (match) => storeMath(match));
    protectedSource = protectedSource.replace(/\\\[[\s\S]+?\\\]/g, (match) => storeMath(match));
    protectedSource = protectedSource.replace(/\\\([^\n]+?\\\)/g, (match) => storeMath(match));
    protectedSource = protectedSource.replace(
      /(^|[^\\$])\$(?!\s)([^$\n]*?\S)\$/g,
      (match, prefix, expression) => `${prefix}${storeMath(`$${expression}$`)}`
    );

    for (const [token, code] of codeMap) {
      protectedSource = protectedSource.split(token).join(code);
    }

    return { protectedSource, mathMap };
  }

  function isSafeUrl(rawValue, allowImage) {
    const value = String(rawValue || "").trim();
    if (!value) return false;
    if (value.startsWith("#")) return !allowImage;
    try {
      const url = new URL(value, window.location.href);
      if (allowImage) {
        return url.origin === window.location.origin && url.protocol === window.location.protocol;
      }
      if (["http:", "https:"].includes(url.protocol)) return true;
      if (!allowImage && url.protocol === "mailto:") return true;
      return url.origin === window.location.origin && url.protocol === window.location.protocol;
    } catch {
      return false;
    }
  }

  function sanitizeHtml(html) {
    const template = document.createElement("template");
    template.innerHTML = String(html || "");

    const elements = Array.from(template.content.querySelectorAll("*"));
    for (const element of elements) {
      if (DROP_CONTENT_TAGS.has(element.tagName)) {
        element.remove();
        continue;
      }
      if (!ALLOWED_TAGS.has(element.tagName)) {
        element.replaceWith(...element.childNodes);
        continue;
      }

      for (const attribute of Array.from(element.attributes)) {
        const name = attribute.name.toLowerCase();
        const allowed =
          (element.tagName === "A" && ["href", "title"].includes(name)) ||
          (element.tagName === "IMG" && ["src", "alt", "title"].includes(name)) ||
          (element.tagName === "CODE" && name === "class");
        if (!allowed || name.startsWith("on") || name === "style") {
          element.removeAttribute(attribute.name);
        }
      }

      if (element.tagName === "A") {
        if (!isSafeUrl(element.getAttribute("href"), false)) {
          element.removeAttribute("href");
        } else {
          element.target = "_blank";
          element.rel = "noopener noreferrer";
        }
      }
      if (element.tagName === "IMG") {
        if (!isSafeUrl(element.getAttribute("src"), true)) {
          element.removeAttribute("src");
        } else {
          element.loading = "lazy";
          element.referrerPolicy = "no-referrer";
        }
      }
      if (element.tagName === "CODE") {
        const languageClass = Array.from(element.classList).find((name) => /^language-[\w+-]+$/i.test(name));
        element.removeAttribute("class");
        if (languageClass) element.classList.add(languageClass);
      }
    }

    return template.innerHTML;
  }

  function parseMarkdown(source) {
    if (typeof marked === "undefined") return escapeHtml(source).replace(/\n/g, "<br>");
    const { protectedSource, mathMap } = protectMarkdown(source);
    let html = marked.parse(protectedSource);
    for (const [token, math] of mathMap) {
      html = html.split(token).join(escapeHtml(math));
    }
    return sanitizeHtml(html);
  }

  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
  }

  function enhanceCodeBlocks(container) {
    for (const code of container.querySelectorAll("pre > code")) {
      if (code.closest(".code-block")) continue;
      if (typeof hljs !== "undefined") {
        try {
          hljs.highlightElement(code);
        } catch (error) {
          console.warn("代码高亮失败", error);
        }
      }

      const pre = code.parentElement;
      const wrapper = document.createElement("div");
      wrapper.className = "code-block";
      const toolbar = document.createElement("div");
      toolbar.className = "code-block-toolbar";
      const language = Array.from(code.classList)
        .find((name) => name.startsWith("language-"))
        ?.slice("language-".length) || "text";
      const label = document.createElement("span");
      label.textContent = language;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "copy-code-button";
      button.textContent = "复制";
      button.addEventListener("click", async () => {
        try {
          await copyText(code.textContent || "");
          button.textContent = "已复制";
          window.setTimeout(() => { button.textContent = "复制"; }, 1200);
        } catch {
          button.textContent = "复制失败";
        }
      });
      toolbar.append(label, button);
      pre.replaceWith(wrapper);
      wrapper.append(toolbar, pre);
    }
  }

  async function renderMermaidBlocks(container) {
    if (typeof mermaid === "undefined") return;
    const blocks = Array.from(container.querySelectorAll("pre > code.language-mermaid"));
    for (const code of blocks) {
      const pre = code.parentElement;
      const target = document.createElement("div");
      target.className = "mermaid-block";
      pre.replaceWith(target);
      try {
        const id = `archive-mermaid-${crypto.randomUUID?.() || Math.random().toString(36).slice(2)}`;
        const { svg } = await mermaid.render(id, code.textContent || "");
        target.innerHTML = svg;
      } catch (error) {
        target.classList.add("mermaid-error");
        target.textContent = `Mermaid 图表渲染失败\n${error?.message || String(error)}`;
      }
    }
  }

  async function renderInto(container, source) {
    container.innerHTML = parseMarkdown(source);

    if (typeof renderMathInElement !== "undefined") {
      try {
        renderMathInElement(container, {
          delimiters: [
            { left: "$$", right: "$$", display: true },
            { left: "\\[", right: "\\]", display: true },
            { left: "\\(", right: "\\)", display: false },
            { left: "$", right: "$", display: false }
          ],
          ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code"],
          throwOnError: false,
          trust: false,
          strict: "ignore"
        });
      } catch (error) {
        console.warn("公式渲染失败", error);
      }
    }

    await renderMermaidBlocks(container);
    enhanceCodeBlocks(container);
  }

  window.ArchiveMarkdown = {
    copyText,
    parseMarkdown,
    renderInto
  };
})();
