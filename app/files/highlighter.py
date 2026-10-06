from __future__ import annotations

import re
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat

try:
    from pygments import lex
    from pygments.lexers import get_lexer_by_name, guess_lexer_for_filename, TextLexer
    from pygments.token import Token
    HAS_PYGMENTS = True
except Exception:  # pragma: no cover
    HAS_PYGMENTS = False


def _fmt(color: str, bold: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Weight.Bold)
    return f


# Map pygments token types → colors (GitHub dark-ish)
_PYG_COLORS = {
    Token.Keyword: "#ff7b72",
    Token.Keyword.Constant: "#79c0ff",
    Token.Keyword.Namespace: "#ff7b72",
    Token.Name.Builtin: "#7ee787",
    Token.Name.Function: "#d2a8ff",
    Token.Name.Class: "#f0883e",
    Token.Name.Decorator: "#d2a8ff",
    Token.String: "#a5d6ff",
    Token.String.Doc: "#8b949e",
    Token.Number: "#79c0ff",
    Token.Comment: "#8b949e",
    Token.Operator: "#ff7b72",
    Token.Name.Tag: "#7ee787",
    Token.Name.Attribute: "#79c0ff",
    Token.Literal.String.Symbol: "#a5d6ff",
}


def _color_for_token(ttype) -> str | None:
    while ttype is not None:
        if ttype in _PYG_COLORS:
            return _PYG_COLORS[ttype]
        ttype = ttype.parent
    return None


class CodeHighlighter(QSyntaxHighlighter):
    """Syntax highlighter — uses Pygments when installed, else lightweight rules."""

    def __init__(self, document, language: str = "text", path: str | None = None):
        super().__init__(document)
        self.language = language or "text"
        self.path = path or ""
        self._rules = self._rules_for(self.language)
        self._lexer = None
        if HAS_PYGMENTS:
            self._lexer = self._make_lexer(self.language, self.path)

    @classmethod
    def language_for_path(cls, path: str) -> str:
        p = (path or "").lower()
        name = p.rsplit("/", 1)[-1]
        if name in ("dockerfile", "containerfile") or name.startswith("dockerfile."):
            return "docker"
        if name == "makefile" or name.endswith(".mk"):
            return "make"
        if p.endswith((".py", ".pyw")):
            return "python"
        if p.endswith((".json",)):
            return "json"
        if p.endswith((".yml", ".yaml")):
            return "yaml"
        if p.endswith((".sh", ".bash", ".zsh")):
            return "bash"
        if p.endswith((".md", ".markdown")):
            return "markdown"
        if p.endswith((".ini", ".conf", ".cfg", ".env", ".toml")):
            return "ini"
        if p.endswith((".js", ".ts", ".jsx", ".tsx")):
            return "javascript"
        if p.endswith((".xml", ".html", ".htm", ".svg")):
            return "html"
        if p.endswith((".css", ".scss")):
            return "css"
        if p.endswith((".log", ".txt")):
            return "text"
        if p.endswith((".sql",)):
            return "sql"
        if p.endswith((".go",)):
            return "go"
        if p.endswith((".rs",)):
            return "rust"
        if p.endswith((".java",)):
            return "java"
        if p.endswith((".c", ".h", ".cpp", ".hpp", ".cc")):
            return "c"
        return "text"

    def _make_lexer(self, language: str, path: str):
        if not HAS_PYGMENTS:
            return None
        try:
            if path:
                return guess_lexer_for_filename(path, "")
        except Exception:
            pass
        try:
            return get_lexer_by_name(language)
        except Exception:
            return TextLexer()

    def _rules_for(self, language: str):
        comment = _fmt("#8b949e")
        keyword = _fmt("#ff7b72", True)
        string = _fmt("#a5d6ff")
        number = _fmt("#79c0ff")
        key = _fmt("#d2a8ff")
        header = _fmt("#ffa657", True)
        builtin = _fmt("#7ee787")
        patterns: list[tuple[str, QTextCharFormat]] = []
        if language in ("python",):
            patterns = [
                (r"#.*$", comment),
                (r"\b(def|class|return|if|elif|else|for|while|import|from|as|try|except|finally|with|yield|async|await|True|False|None|and|or|not|in|is|lambda|pass|break|continue)\b", keyword),
                (r"('([^'\\]|\\.)*'|\"([^\"\\]|\\.)*\")", string),
                (r"\b[0-9]+(\.[0-9]+)?\b", number),
            ]
        elif language in ("json",):
            patterns = [
                (r"\".*?\"(?=\s*:)", key),
                (r"('([^'\\]|\\.)*'|\"([^\"\\]|\\.)*\")", string),
                (r"\b(true|false|null)\b", keyword),
                (r"-?\b[0-9]+(\.[0-9]+)?\b", number),
            ]
        elif language in ("yaml", "docker"):
            patterns = [
                (r"#.*$", comment),
                (r"^\s*[\w\-\.]+\s*(?=:)", key),
                (r"('([^'\\]|\\.)*'|\"([^\"\\]|\\.)*\")", string),
                (r"\b(true|false|null|yes|no|FROM|RUN|CMD|COPY|ADD|ENV|EXPOSE|WORKDIR|ENTRYPOINT|VOLUME|USER|ARG|LABEL)\b", keyword),
                (r"-?\b[0-9]+(\.[0-9]+)?\b", number),
            ]
        elif language in ("bash", "shell", "sh"):
            patterns = [
                (r"#.*$", comment),
                (r"\b(if|then|else|fi|for|while|do|done|case|esac|function|return|export|source|alias)\b", keyword),
                (r"('([^'\\]|\\.)*'|\"([^\"\\]|\\.)*\")", string),
                (r"\$\{?\w+\}?", builtin),
            ]
        elif language == "markdown":
            patterns = [
                (r"^#{1,6}\s.*$", header),
                (r"`[^`]+`", string),
                (r"\*\*[^*]+\*\*", keyword),
            ]
        elif language in ("ini", "toml"):
            patterns = [
                (r"[;#].*$", comment),
                (r"^\s*\[[^\]]+\]", header),
                (r"^\s*[\w\.\-]+\s*(?==)", key),
                (r"('([^'\\]|\\.)*'|\"([^\"\\]|\\.)*\")", string),
            ]
        compiled = []
        for pat, fmt in patterns:
            compiled.append((re.compile(pat), fmt))
        return compiled

    def highlightBlock(self, text: str):
        if self._lexer is not None and HAS_PYGMENTS:
            try:
                pos = 0
                for ttype, value in lex(text, self._lexer):
                    color = _color_for_token(ttype)
                    if color and value:
                        self.setFormat(pos, len(value), _fmt(color))
                    pos += len(value)
                return
            except Exception:
                pass
        for pattern, fmt in self._rules:
            for m in pattern.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)
